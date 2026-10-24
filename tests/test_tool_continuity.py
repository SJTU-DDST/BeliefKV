from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment, validate_runtime
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey as Key
from beliefkv.runtime.tool_continuity import ToolContinuationPolicy
from tests.test_internal_comparison import join_fixture, observed, request


def key(round=0, *, invocation="normal", workflow="wf"):
    return Key(
        f"{invocation}-{round}", workflow, invocation, f"ctx-{invocation}",
        round, 0, f"session-{invocation}", 1,
    )


def tool(kind, *, ts=100., run="tool", invocation="normal", workflow="wf", **kwargs):
    return RuntimeEvent(
        f"{kind.value}-{run}-{ts}", ts, kind, workflow,
        invocation_id=invocation, context_id=f"ctx-{invocation}",
        attributes={"tool_run_id": run}, **kwargs,
    )


def completed_policy(*, max_rounds=4):
    policy = ToolContinuationPolicy(max_rounds=max_rounds)
    policy.admitted(key(), arrival=1., promoted=False)
    policy.completed(key(), now=10.)
    return policy


def short_round(policy, *, invocation="normal", workflow="wf", ts=100.):
    policy.observe(tool(Kind.TOOL_START, ts=ts, invocation=invocation, workflow=workflow))
    policy.observe(tool(Kind.TOOL_END, ts=ts + 20., invocation=invocation, workflow=workflow))


def test_short_tools_inherit_segment_arrival_and_spend_earned_credit():
    policy = completed_policy()
    short_round(policy)
    assert policy.inherited_arrival(key(1), arrival=10.1) == 1.
    policy.admitted(key(1), arrival=10.1, promoted=True)
    assert policy.segments[("wf", "normal")].rounds == 2
    assert policy.segments[("wf", "normal")].arrival == 1.
    assert policy.credits == {"wf": 2}
    policy.completed(key(1), now=11.)
    short_round(policy, ts=200.)
    policy.credits["wf"] = 0
    assert policy.inherited_arrival(key(2), arrival=11.1) is None
    policy.admitted(key(2), arrival=11.1, promoted=False)
    assert policy.segments[("wf", "normal")].arrival == 11.1
    assert policy.credits == {"wf": 3}


def test_parallel_tools_require_whole_round_completion_and_duration():
    policy = completed_policy()
    policy.observe(tool(Kind.TOOL_START, ts=100., run="a"))
    policy.observe(tool(Kind.TOOL_START, ts=150., run="b"))
    policy.observe(tool(Kind.TOOL_END, ts=200., run="b"))
    assert policy.inherited_arrival(key(1), arrival=10.1) is None
    policy.observe(tool(Kind.TOOL_END, ts=300., run="a"))
    assert policy.inherited_arrival(key(1), arrival=10.1) == 1.
    policy.observe(tool(Kind.TOOL_END, ts=300., run="a"))
    assert policy.counts["short_tool_rounds"] == 1


@pytest.mark.parametrize("fault", (
    "long_tool", "long_client_gap", "negative_gap", "missing_start", "negative_tool",
    "changed_session", "changed_generation", "changed_context", "skipped_epoch",
    "same_request", "compaction", "cancel", "spawn", "join", "workflow_end",
    "terminal_result", "internal_summary", "unknown_submit", "missing_run",
))
def test_unproven_or_expired_continuity_falls_back_without_privilege(fault):
    policy = completed_policy()
    successor, arrival = key(1), 10.1
    if fault == "missing_start":
        policy.observe(tool(Kind.TOOL_END))
    elif fault == "negative_tool":
        policy.observe(tool(Kind.TOOL_START, ts=100.))
        policy.observe(tool(Kind.TOOL_END, ts=99.))
    elif fault == "missing_run":
        policy.observe(replace(tool(Kind.TOOL_START), attributes={}))
    else:
        policy.observe(tool(Kind.TOOL_START, ts=100.))
        policy.observe(tool(Kind.TOOL_END, ts=1200. if fault == "long_tool" else 120.))
    if fault == "long_client_gap":
        arrival = 11.001
    elif fault == "negative_gap":
        arrival = 9.9
    elif fault == "changed_session":
        successor = replace(successor, session_id="new-session")
    elif fault == "changed_generation":
        successor = replace(successor, session_generation=2)
    elif fault == "changed_context":
        successor = replace(successor, context_id="new-context")
    elif fault == "skipped_epoch":
        successor = replace(successor, context_epoch=2)
    elif fault == "same_request":
        successor = key()
    elif fault in ("compaction", "cancel", "spawn", "join", "workflow_end", "terminal_result"):
        kind = {
            "compaction": Kind.CONTEXT_COMPACT, "cancel": Kind.INVOCATION_CANCEL,
            "spawn": Kind.SPAWN, "join": Kind.JOIN_WAIT, "workflow_end": Kind.WORKFLOW_END,
            "terminal_result": Kind.LLM_RESULT,
        }[fault]
        policy.observe(replace(tool(kind), attributes={"terminal": fault == "terminal_result"}))
    elif fault in ("internal_summary", "unknown_submit"):
        policy.observe(replace(
            tool(Kind.LLM_SUBMIT), context_epoch=3 if fault == "unknown_submit" else 1,
            attributes={"runtime_internal": fault == "internal_summary", "request_id": "other"},
        ))
    assert policy.inherited_arrival(successor, arrival=arrival) is None


def test_native_completion_is_required_and_completion_before_events_can_arrive_late():
    policy = ToolContinuationPolicy()
    policy.admitted(key(), arrival=1., promoted=False)
    short_round(policy)
    assert not policy.ready
    policy.admitted(key(), arrival=1., promoted=False)
    policy.completed(key(), now=10.)
    policy.observe(replace(
        tool(Kind.LLM_SUBMIT), context_epoch=1, attributes={"request_id": key(1).request_id},
    ))
    assert policy.inherited_arrival(key(1), arrival=10.1) is None


def test_segment_budget_yields_even_when_siblings_earn_credit():
    policy = completed_policy()
    for index in range(1, 5):
        sibling = key(0, invocation=f"sibling{index}")
        policy.admitted(sibling, arrival=2., promoted=False)
    for round in range(1, 4):
        short_round(policy, ts=round * 100.)
        policy.admitted(key(round), arrival=10. + round * .1, promoted=True)
        policy.completed(key(round), now=10. + round * .1)
    assert ("wf", "normal") not in policy.segments
    assert policy.counts["segment_budget_yields"] == 1
    assert policy.inherited_arrival(key(4), arrival=10.5) is None


def test_one_ordinary_turn_earns_three_bounded_short_successors_without_siblings():
    policy = completed_policy()
    for round in range(1, 4):
        short_round(policy, ts=round * 100.)
        arrival = 10. + round * .1
        assert policy.inherited_arrival(key(round), arrival=arrival) == 1.
        policy.admitted(key(round), arrival=arrival, promoted=True)
        policy.completed(key(round), now=arrival)
    assert policy.credits["wf"] == 0
    assert policy.counts["priority_admissions"] == 3
    assert not policy.ready


def test_siblings_share_credit_but_cannot_inherit_each_others_age():
    policy = completed_policy()
    short_round(policy)
    assert policy.inherited_arrival(key(1, invocation="sibling"), arrival=10.1) is None
    other = key(0, invocation="sibling")
    policy.admitted(other, arrival=3., promoted=False)
    policy.completed(other, now=10.)
    short_round(policy, invocation="sibling")
    policy.credits["wf"] = 1
    policy.admitted(key(1), arrival=10.1, promoted=True)
    assert policy.inherited_arrival(key(1, invocation="sibling"), arrival=10.1) is None


def test_chunks_and_retractions_never_mint_new_request_credit():
    policy = ToolContinuationPolicy()
    for _ in range(5):
        policy.admitted(key(), arrival=1., promoted=False)
    assert policy.credits["wf"] == 3
    policy.retracted(key())
    policy.admitted(key(), arrival=1., promoted=False)
    assert policy.credits["wf"] == 3
    policy.completed(key(), now=10.)
    short_round(policy)
    assert policy.inherited_arrival(key(1), arrival=10.1) is None


def test_completed_token_budget_survives_rounds_and_duplicate_completion():
    policy = ToolContinuationPolicy(max_output_tokens=64)
    policy.admitted(key(), arrival=1., promoted=False)
    policy.completed(key(), now=10., output_tokens=32)
    policy.completed(key(), now=10., output_tokens=32)
    assert policy.segments[("wf", "normal")].output_tokens == 32
    short_round(policy)
    policy.admitted(key(1), arrival=10.1, promoted=True)
    policy.completed(key(1), now=11., output_tokens=32)
    short_round(policy, ts=200.)
    assert policy.inherited_arrival(key(2), arrival=11.1) is None
    assert policy.counts["completed_output_tokens"] == 64
    assert policy.counts["segment_token_budget_yields"] == 1


def test_a_long_ordinary_request_does_not_receive_short_tool_priority():
    policy = ToolContinuationPolicy(max_output_tokens=64)
    policy.admitted(key(), arrival=1., promoted=False)
    policy.completed(key(), now=10., output_tokens=1000)
    short_round(policy)
    assert not policy.segments and not policy.ready
    assert policy.inherited_arrival(key(1), arrival=10.1) is None


@pytest.mark.parametrize("configuration", (
    {"short_gap_seconds": 0}, {"short_gap_seconds": float("nan")},
    {"short_gap_seconds": True}, {"max_rounds": 1}, {"max_rounds": 2.5},
    {"max_output_tokens": 0}, {"max_output_tokens": True}, {"max_output_tokens": 2.5},
))
def test_invalid_bounds_are_rejected(configuration):
    with pytest.raises(ValueError):
        ToolContinuationPolicy(**configuration)


def runtime_round(monkeypatch, *, mode="full", continuity=True, scheduling="join_priority"):
    monkeypatch.setenv("BELIEFKV_ENABLE_TOOL_CONTINUITY", str(int(continuity)))
    runtime, requests = join_fixture(
        monkeypatch, mode=mode, scheduling=scheduling, tool_continuity=continuity,
    )
    runtime._join_priority_pending.clear()
    first, normal = requests
    runtime._visible_since[first.rid] = 10.
    runtime._visible_since[normal.rid] = 11.
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    first.finished = lambda: True
    first.output_ids = [1]
    runtime.on_batch_completed(NS(reqs=[first]))
    runtime.on_events((
        tool(Kind.TOOL_START, ts=100.),
        tool(Kind.TOOL_END, ts=120.),
        observed(
            130, Kind.LLM_SUBMIT, invocation_id="normal", context_id="ctx-normal",
            context_epoch=1, attributes={"request_id": "successor"},
        ),
    ))
    successor = request("normal")
    successor.rid = "successor"
    successor.beliefkv_metadata["context_epoch"] = 1
    runtime.register_visible_request(successor)
    names = ["_read_request_reentry", "_read_request_residency_hint"]
    if scheduling == "join_priority":
        names.append("_prefill_causal_ranks")
    for name in names:
        monkeypatch.setattr(runtime, name, Mock(side_effect=AssertionError("no extra KV planning")))
    return runtime, normal, successor


def workflow_round(monkeypatch, *, continuity=True):
    runtime, normal, successor = runtime_round(
        monkeypatch, continuity=continuity, scheduling="workflow",
    )
    monkeypatch.setattr(runtime, "_prefill_causal_ranks", lambda tagged, _keys: {
        index: (2, 0, index) for index, _req in tagged
    })
    hot = {
        "device_checkpoint_tokens": 100, "missing_full_tokens": 0,
        "missing_mamba_slots": 0,
    }
    reader = Mock(return_value=hot)
    monkeypatch.setattr(runtime, "_read_request_residency_hint", reader)
    return runtime, normal, successor, reader


@pytest.mark.parametrize("continuity", (False, True))
def test_workflow_continuation_reuses_residency_and_never_plans_or_restores(monkeypatch, continuity):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, normal, successor, reader = workflow_round(monkeypatch, continuity=continuity)
    try:
        plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        assert plan.prioritized[0].request_id == (successor.rid if continuity else normal.rid)
        assert reader.call_count == 2
        runtime._read_request_reentry.assert_not_called()
        assert not runtime.physical_ledger.pending_count
        if continuity:
            runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
            assert runtime._tool_continuity.counts["priority_admissions"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("observation", (
    None,
    {"device_checkpoint_tokens": 0, "missing_full_tokens": 0, "missing_mamba_slots": 0},
    {"device_checkpoint_tokens": 100, "missing_full_tokens": 100, "missing_mamba_slots": 0},
    {"device_checkpoint_tokens": 100, "missing_full_tokens": 0, "missing_mamba_slots": 1},
))
def test_workflow_continuation_never_promotes_unknown_or_host_only_state(monkeypatch, observation):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, normal, successor, reader = workflow_round(monkeypatch)
    hot = reader.return_value
    reader.side_effect = lambda req, **_: hot if req is normal else observation
    try:
        plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        assert plan.prioritized[0].request_id == normal.rid
        assert runtime._tool_continuity_promoted is None
        assert reader.call_count == 2
        runtime._read_request_reentry.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("constraint", ("join", "causal_class", "older", "budget", "priority_head"))
def test_workflow_continuation_preserves_critical_order_and_fairness(monkeypatch, constraint):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, normal, successor, reader = workflow_round(monkeypatch)
    try:
        if constraint == "join":
            runtime._join_priority_pending["parent"] = "join"
        elif constraint == "causal_class":
            monkeypatch.setattr(runtime, "_prefill_causal_ranks", lambda tagged, _keys: {
                index: (1 if req is normal else 2, 0, index) for index, req in tagged
            })
        elif constraint == "older":
            runtime._visible_since[normal.rid] = 9.
        elif constraint == "budget":
            runtime._tool_continuity.credits["wf"] = 0
        if constraint == "priority_head":
            ordered = [(0, normal), (1, successor)]
            runtime._order_tool_continuation(
                ordered, {i: runtime.visible[req.rid] for i, req in ordered},
                now=12., protected_head=True,
                causal_ranks={i: (2, 0, i) for i, _ in ordered},
                residency_observations={i: reader.return_value for i, _ in ordered},
            )
            assert ordered[0][1] is normal
        else:
            plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
            assert plan.prioritized[0].request_id == normal.rid
        assert runtime._tool_continuity_promoted is None
        runtime._read_request_reentry.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("scheduling", ("workflow", "join_priority"))
@pytest.mark.parametrize("join_credit", (0, 1, 2, 3, 4))
def test_continuation_never_crosses_ready_join_parent_during_boost_cooldown(
    monkeypatch, scheduling, join_credit,
):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    if scheduling == "workflow":
        runtime, parent, successor, reader = workflow_round(monkeypatch)
    else:
        runtime, parent, successor = runtime_round(monkeypatch)
        reader = None
    try:
        runtime._join_priority_pending["parent"] = "join"
        runtime._join_priority_normal_admissions = join_credit
        plan = runtime.plan_native_prefill(
            [parent, successor], running_batch=None, adder=None,
        )
        assert [key.request_id for key in plan.prioritized] == [parent.rid, successor.rid]
        assert runtime._tool_continuity_promoted is None
        runtime._read_request_reentry.assert_not_called()
        if reader is not None:
            assert reader.call_count == (0 if join_credit == 4 else 2)
    finally:
        runtime.close()


@pytest.mark.parametrize("ready_parent", (False, True))
def test_continuation_can_order_after_join_without_promoting_join_itself(monkeypatch, ready_parent):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, parent, successor, reader = workflow_round(monkeypatch)
    try:
        runtime._join_priority_pending["parent"] = "join"
        runtime._join_priority_normal_admissions = 0
        if not ready_parent:
            runtime.graph.joins["join"].satisfied = False
        ordinary = request("ordinary")
        runtime.on_events((
            observed(140, Kind.INVOCATION_CREATE, invocation_id="ordinary", context_id="ctx-ordinary"),
        ))
        runtime.register_visible_request(ordinary)
        runtime._visible_since[ordinary.rid] = 11.
        plan = runtime.plan_native_prefill(
            [parent, ordinary, successor], running_batch=None, adder=None,
        )
        expected = (
            [parent.rid, successor.rid, ordinary.rid]
            if ready_parent else [successor.rid, parent.rid, ordinary.rid]
        )
        assert [key.request_id for key in plan.prioritized] == expected
        assert runtime._join_priority_promoted is None
        assert runtime._tool_continuity_promoted == successor.rid
        assert reader.call_count == 3
        runtime._read_request_reentry.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("join_position", (1, 2))
def test_continuation_does_not_add_boost_ahead_of_a_later_ready_join(monkeypatch, join_position):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, parent, successor, reader = workflow_round(monkeypatch)
    try:
        runtime._join_priority_pending["parent"] = "join"
        runtime._join_priority_normal_admissions = 0
        ordinary = request("ordinary")
        runtime.on_events((
            observed(140, Kind.INVOCATION_CREATE, invocation_id="ordinary", context_id="ctx-ordinary"),
        ))
        runtime.register_visible_request(ordinary)
        runtime._visible_since[ordinary.rid] = 11.
        queue = [ordinary, successor]
        queue.insert(join_position, parent)
        plan = runtime.plan_native_prefill(queue, running_batch=None, adder=None)
        assert [key.request_id for key in plan.prioritized] == [req.rid for req in queue]
        assert runtime._tool_continuity_promoted is None
        assert runtime._join_priority_promoted is None
        assert reader.call_count == 3
        runtime._read_request_reentry.assert_not_called()
    finally:
        runtime.close()


def test_runtime_charges_completed_output_once_without_decode_tracking(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, normal, successor, reader = workflow_round(monkeypatch)
    try:
        runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        successor.output_ids = [1] * 1023
        successor.finished = lambda: False
        runtime.on_batch_completed(NS(reqs=[successor]))
        assert runtime._tool_continuity.counts["completed_output_tokens"] == 1
        successor.finished = lambda: True
        runtime.on_batch_completed(NS(reqs=[successor]))
        runtime.on_batch_completed(NS(reqs=[successor]))
        assert runtime._tool_continuity.counts["completed_output_tokens"] == 1024
        assert runtime._tool_continuity.counts["segment_token_budget_yields"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only"))
def test_actual_short_tool_successor_reuses_age_with_all_scheduling_modes(monkeypatch, mode):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, normal, successor = runtime_round(monkeypatch, mode=mode)
    try:
        plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        assert [candidate.request_id for candidate in plan.prioritized] == [successor.rid, normal.rid]
        assert runtime._visible_since[successor.rid] == 12.
        runtime.on_prefill_candidate_result(successor, admitted=False, result="NO_TOKEN")
        assert runtime._tool_continuity.counts["priority_admissions"] == 0
        plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        assert runtime._tool_continuity.counts["priority_admissions"] == 1
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("constraint", ("disabled", "aged", "older", "join", "active_tool", "stale_session"))
def test_continuation_preserves_join_fairness_identity_and_native_fallback(monkeypatch, constraint):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, normal, successor = runtime_round(monkeypatch, continuity=constraint != "disabled")
    try:
        if constraint == "aged":
            runtime._visible_since[normal.rid] = 1.
        elif constraint == "older":
            runtime._visible_since[normal.rid] = 9.
        elif constraint == "join":
            runtime._join_priority_pending["parent"] = "join"
        elif constraint == "active_tool":
            runtime.graph.invocations["normal"].active_tool_calls["active"] = ("execute", 100.)
        elif constraint == "stale_session":
            successor.session_generation = 2
        plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        assert plan.prioritized[0].request_id == normal.rid
        assert runtime._tool_continuity_promoted is None
    finally:
        runtime.close()


def test_aged_queue_cannot_disable_all_short_tool_continuity(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 22.)
    runtime, normal, successor = runtime_round(monkeypatch)
    try:
        runtime._tool_continuity_normal_admissions = 3
        plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        assert plan.prioritized[0].request_id == normal.rid
        runtime._tool_continuity_normal_admissions = 4
        plan = runtime.plan_native_prefill([normal, successor], running_batch=None, adder=None)
        assert plan.prioritized[0].request_id == successor.rid
        assert runtime.counts["tool_continuity_priority_aged_bypass_ordered"] == 1
        runtime.on_prefill_candidate_result(successor, admitted=False, result="NO_TOKEN")
        assert runtime._tool_continuity_normal_admissions == 4
        runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        assert runtime._tool_continuity_normal_admissions == 0
        for _ in range(5):
            runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        assert runtime._tool_continuity_normal_admissions == 0
        runtime.on_prefill_candidate_result(normal, admitted=True, result="CONTINUE")
        assert runtime._tool_continuity_normal_admissions == 1
    finally:
        runtime.close()


def test_continuity_keeps_its_aged_fairness_budget_independent_of_join(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 22.)
    runtime, normal, successor = runtime_round(monkeypatch)
    try:
        runtime._visible_since[normal.rid] = 21.
        runtime._join_priority_pending["parent"] = "join"
        runtime._join_priority_normal_admissions = 4
        runtime._tool_continuity_normal_admissions = 0
        plan = runtime.plan_native_prefill([successor, normal], running_batch=None, adder=None)
        assert plan.prioritized[0].request_id == normal.rid
        runtime.on_prefill_candidate_result(normal, admitted=True, result="CONTINUE")
        assert runtime.counts["join_parent_priority_admitted"] == 1
        assert runtime._tool_continuity_normal_admissions == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only", "kv_only", "native_matched"))
def test_continuity_control_is_explicit_and_native_kv_modes_remain_independent(mode):
    env = arm_environment({"PREFILL_SCHEDULING_POLICY": "join_priority"}, mode)
    assert env["TOOL_CONTINUITY"] == env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    env = arm_environment({
        "PREFILL_SCHEDULING_POLICY": "join_priority", "TOOL_CONTINUITY": "1",
    }, mode)
    expected = str(int(mode in ("full", "scheduler_only", "join_priority_only")))
    assert env["TOOL_CONTINUITY"] == env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == expected
    env = arm_environment({"TOOL_CONTINUITY": "0"}, mode)
    assert env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only", "kv_only", "native_matched"))
def test_workflow_continuity_control_keeps_native_and_kv_modes_independent(mode):
    env = arm_environment({
        "PREFILL_SCHEDULING_POLICY": "workflow", "TOOL_CONTINUITY": "1",
    }, mode)
    expected = str(int(mode in ("full", "scheduler_only", "join_priority_only")))
    assert env["TOOL_CONTINUITY"] == env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == expected


def test_default_runtime_does_not_track_or_promote_short_tool_successors(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    monkeypatch.delenv("BELIEFKV_ENABLE_TOOL_CONTINUITY", raising=False)
    runtime, requests = join_fixture(
        monkeypatch, mode="full", scheduling="join_priority",
    )
    try:
        assert runtime._tool_continuity is None
        runtime._join_priority_pending.clear()
        runtime.on_events((
            tool(Kind.TOOL_START, ts=100.), tool(Kind.TOOL_END, ts=120.),
        ))
        plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        assert [key.request_id for key in plan.prioritized] == [req.rid for req in requests]
        assert runtime._tool_continuity_promoted is None
    finally:
        runtime.close()


def test_runtime_telemetry_exposes_effective_continuity_and_bounds(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, normal, successor = runtime_round(monkeypatch, mode="join_priority_only")
    runtime._opportunity_writer = Mock()
    try:
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        assert state["tool_continuity"] is True
        assert state["tool_continuity_policy"]["max_segment_requests"] == 4
        validate_runtime(
            "join_priority_only", state, tool_continuity=True, scheduling_policy="join_priority",
        )
        state["tool_continuity"] = False
        with pytest.raises(ValueError, match="tool_continuity"):
            validate_runtime("join_priority_only", state, tool_continuity=True)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("ready", (False, True))
def test_cpu_benchmark_models_a_real_successor_after_native_completion(ready):
    from scripts.benchmark_tool_continuity_cpu import exercise, fixture

    runtime, queue = fixture(8, enabled=True, ready=ready)
    try:
        order = exercise(runtime, queue)
        assert order[0] == queue[-1 if ready else 0].rid
        assert len(order) == len(queue)
    finally:
        runtime.close()
