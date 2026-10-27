from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import EventConfidence, RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime, _ExecutionHandoffTicket
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request


def runtime(
    monkeypatch, tmp_path, *, capacity=500, strategy="working_set",
    coadmit_join_children=False, blocked_lending="idle", workflow_entry_min_ready=0,
    tool_loan_return=False,
    join_dependency_admission=False, blocked_budget_proof="predicted_pressure",
):
    settings = arm_environment({
        "AGENT_ADMISSION": strategy, "TOOL_RESTORE_MODE": "execution",
        "EXECUTION_FRONTIER_RESTORE": str(int(strategy != "thunderagent_retained")),
        "TOOL_CONTINUITY": "1",
        "AGENT_COADMIT_JOIN_CHILDREN": str(int(coadmit_join_children)),
        "AGENT_BLOCKED_LENDING": blocked_lending,
        "AGENT_WORKFLOW_ENTRY_MIN_READY": str(workflow_entry_min_ready),
        "AGENT_TOOL_LOAN_RETURN": str(int(tool_loan_return)),
        "AGENT_JOIN_DEPENDENCY_ADMISSION": str(int(join_dependency_admission)),
        "AGENT_BLOCKED_BUDGET_PROOF": blocked_budget_proof,
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "runtime.sock"))
    live.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=capacity),
    ))
    return live


@pytest.mark.parametrize("mode", ("all", "any"))
@pytest.mark.parametrize("confidence", (EventConfidence.OBSERVED_EXACT, EventConfidence.INFERRED))
def test_retained_sibling_coadmission_requires_an_observed_blocking_all_join(
    monkeypatch, tmp_path, mode, confidence,
):
    live = runtime(
        monkeypatch, tmp_path, capacity=1000, strategy="thunderagent_retained",
        coadmit_join_children=True,
    )
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="p"),
            observed(2, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context",
                     parent_invocation_id="parent", relation_type=RelationType.SPAWN),
            observed(3, Kind.INVOCATION_CREATE, invocation_id="b", context_id="b-context",
                     parent_invocation_id="parent", relation_type=RelationType.SPAWN),
            observed(4, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("a", "b"),
                     attributes={"mode": mode}, confidence=confidence),
            observed(5, Kind.JOIN_WAIT, invocation_id="parent", join_id="join",
                     confidence=confidence),
        ))
        live.register_visible_request(request("a-one", "a", tokens=300, workflow="wf"))
        live.register_visible_request(request("large", "other", tokens=800, workflow="other"))
        live.register_visible_request(request("b-one", "b", tokens=300, workflow="wf"))
        policy = live._agent_admission.policy
        assert policy.allowed("a")
        observed_all = mode == "all" and confidence is EventConfidence.OBSERVED_EXACT
        assert policy.allowed("b") is observed_all
        assert policy.counts["sibling_coadmitted"] == int(observed_all)
        live.on_events((observed(6, Kind.JOIN_TIMEOUT, join_id="join", confidence=confidence),))
        assert not policy.observed_sibling_joins
        assert not policy.observed_agent_joins
    finally:
        live.close()


def test_real_prefill_filters_paused_agents_and_preserves_full_migration_policies(monkeypatch, tmp_path, strategy="working_set"):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        first, second = request("one", "a", tokens=300), request("two", "b", tokens=300)
        live.register_visible_request(first)
        live.register_visible_request(second)
        plan = live.plan_native_prefill([second, first], running_batch=NS(reqs=[]), adder=None)
        selection = select_native_prefill_candidates(
            [second, first], plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert selection.candidates == (first,)
        assert live._custom_kv_management and live.enable_prepare_host
        assert live.enable_join_parent_priority and live.enable_resident_first
        assert live._tool_continuity is not None and live._paper_baseline is None
        assert live.enable_frontier_restore is (strategy != "thunderagent_retained")
        assert not live.enable_tool_prefetch
    finally:
        live.close()


def test_two_short_tool_successors_share_one_agent_admission(monkeypatch, tmp_path, strategy="working_set"):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        first = request("one", "a", tokens=300)
        live.register_visible_request(first)
        live.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[first]))
        live.register_visible_request(request("other", "b", tokens=300))
        successor = request("two", "a", tokens=310)
        live.register_visible_request(successor)
        agents = live._agent_admission
        assert agents.allows(live.visible["two"])
        assert not agents.allows(live.visible["other"])
        assert agents.policy.agents["a"].steps == 2
        live.on_batch_completed(NS(reqs=[successor]))
        live.on_batch_completed(NS(reqs=[successor]))
        assert agents.counts["completed"] == 2
    finally:
        live.close()


def test_agent_status_declares_ownership_age_separately_from_queue_priority(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        status = live._agent_admission.status()
        assert status["eligibility_starvation_seconds"] == 1800.
        assert status["eligibility_release"] == (
            "observed_join_long_tool_terminal_growth_or_bounded_starvation"
        )
        assert live.enable_join_parent_priority and live.enable_resident_first
        assert live._tool_continuity is not None
    finally:
        live.close()


def test_real_join_releases_parent_and_restore_budget_is_bound_to_its_reentry(monkeypatch, tmp_path, strategy="working_set"):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
            observed(2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                     parent_invocation_id="parent", relation_type=RelationType.SPAWN),
            observed(3, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
        ))
        parent = request("parent-one", "parent", tokens=300, workflow="wf")
        live.register_visible_request(parent)
        live.on_prefill_candidate_result(parent, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[parent]))
        live.on_events((observed(4, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        child = request("child-one", "child", tokens=300, workflow="wf")
        live.register_visible_request(child)
        agents = live._agent_admission
        assert agents.allows(live.visible[child.rid])
        live.on_prefill_candidate_result(child, admitted=True, result="CONTINUE")
        assert not agents.claim_restore(agents.latest["parent"], source="join_ticket")
        live.on_batch_completed(NS(reqs=[child]))
        live.on_events((observed(5, Kind.RETURN, invocation_id="child"),))
        assert agents.claim_restore(agents.latest["parent"], source="join_ticket")
        successor = request("parent-two", "parent", tokens=310, workflow="wf")
        live.register_visible_request(successor)
        assert agents.allows(live.visible[successor.rid])
        live.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        assert agents.policy.restore_owner is None
        assert "parent" not in live._join_priority_pending
    finally:
        live.close()


@pytest.mark.parametrize("strategy", ("working_set", "thunderagent", "thunderagent_retained"))
def test_inferred_join_does_not_release_parent_eligibility_or_grant_return_priority(
    monkeypatch, tmp_path, strategy,
):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
            observed(2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                     parent_invocation_id="parent", relation_type=RelationType.SPAWN),
        ))
        parent = request("parent-one", "parent", tokens=300, workflow="wf")
        live.register_visible_request(parent)
        live.on_prefill_candidate_result(parent, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[parent]))
        live.on_events((
            observed(3, Kind.JOIN_CREATE, join_id="inferred", member_invocation_ids=("child",),
                     confidence=EventConfidence.INFERRED,
                     attributes={"source": "agentx_timestamp_inferred_join"}),
            observed(4, Kind.JOIN_WAIT, invocation_id="parent", join_id="inferred",
                     confidence=EventConfidence.INFERRED),
        ))
        agents = live._agent_admission
        assert agents.policy.agents["parent"].active
        assert "inferred" not in live.graph.joins
        assert live.graph.invocations["parent"].state.value == "ready"
        live.on_events((
            observed(5, Kind.RETURN, invocation_id="child"),
            observed(6, Kind.JOIN_SATISFIED, join_id="inferred",
                     confidence=EventConfidence.INFERRED),
        ))
        assert agents.policy.agents["parent"].active
        assert not live._join_priority_pending
        assert not live._confirmed_join_tickets
        assert live.counts["unobserved_join_event_ignored"] == 3
    finally:
        live.close()


def test_speculative_restore_does_not_restore_paused_agent_or_wrong_generation(monkeypatch, tmp_path, strategy="working_set"):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        live.register_visible_request(request("a", "one", tokens=300))
        live.register_visible_request(request("b", "two", tokens=300))
        agents = live._agent_admission
        key = live.visible["b"]
        assert not agents.claim_restore(key, source="execution_handoff")
        assert not agents.claim_restore(replace(key, session_id="wrong"), source="join_ticket")
        assert agents.counts[
            "ordinary_restore_disabled" if strategy == "thunderagent_retained"
            else "restore_outside_active_set"
        ] == 1
    finally:
        live.close()


def test_native_retraction_updates_attempt_without_creating_another_agent(monkeypatch, tmp_path, strategy="working_set"):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        req = request("a", "one", tokens=300)
        live.register_visible_request(req)
        old = live.visible["a"]
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        req.cache_request_handle.attempt_id = 1
        live.on_requests_requeued([req], is_retracted=True)
        agents = live._agent_admission
        assert not agents.allows(old) and agents.allows(live.visible["a"])
        assert len(agents.policy.agents) == 1
        assert not agents.policy.agents["one"].in_flight
    finally:
        live.close()


def test_existing_frontier_ticket_loses_authority_when_agent_is_paused(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        req = request("a", "one", tokens=300)
        live.register_visible_request(req)
        key = live.visible["a"]
        live._executable_waiting_key = Mock(return_value=key)
        ticket = _ExecutionHandoffTicket(key, req, time.monotonic() + 1.5)
        assert live._live_execution_handoff(ticket)
        live._agent_admission.policy.block("one", "join", now=time.monotonic())
        assert not live._live_execution_handoff(ticket)
        live._native_cache.prefetch_gpu_session_nodes = Mock()
        assert live._issue_handoff_prefetch_steps(ticket, (NS(key=key),)) == ()
        live._native_cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        live.close()


def test_low_frequency_maintenance_reuses_existing_long_tool_decision(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        req = request("a", "one", tokens=300)
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        agents = live._agent_admission
        agents.tool_waits["one"] = {"_legacy": 10.}
        live._long_tool_wait = Mock(return_value=True)
        agents.tick(now=10.1)
        assert not agents.policy.agents["one"].active
        agents.tick(now=10.2)
        assert agents.counts["control_ticks"] == 1
        live._long_tool_wait.assert_called_once()
    finally:
        live.close()


def test_abort_all_releases_idle_agent_budget_as_well_as_pending_requests(monkeypatch, tmp_path, strategy="working_set"):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        req = request("a", "one", tokens=300)
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        live.on_abort_request(NS(rid="", abort_all=True))
        agents = live._agent_admission
        assert not agents.policy.agents and agents.policy.used_tokens == 0
        assert not agents.latest
    finally:
        live.close()


def test_impossible_parent_reservation_does_not_start_capacity_reclamation(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        req = request("running", "one", tokens=300)
        live.register_visible_request(req)
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        live.register_visible_request(request("parent", "parent", tokens=300))
        live._set_critical_parent_demand(
            live.visible["parent"], 300, 0, time.monotonic() + 1.,
        )
        assert live._critical_parent_demand is None
        assert live.counts["agent_restore_deferred_before_reclaim"] == 1
        assert live._agent_admission.policy.used_tokens == 400
    finally:
        live.close()


def test_elapsed_tool_wait_can_yield_without_a_duration_model_or_future_end(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        req = request("a", "one", tokens=300)
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        agents = live._agent_admission
        agents.tool_waits["one"] = {"_legacy": 10.}
        live._long_tool_wait = Mock(return_value=False)
        agents.tick(now=10.1)
        assert agents.policy.agents["one"].active
        agents.tick(now=11.2)
        assert not agents.policy.agents["one"].active
        assert agents.policy.agents["one"].blocked == "elapsed_tool"
    finally:
        live.close()


def test_final_observer_requires_declared_agent_identity_and_complete_work(monkeypatch, tmp_path, strategy="working_set"):
    from scripts import run_causal_comparison as comparison

    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda *_a, **_k: None)
    actual = {"agent_admission": {
        "policy": (
            "thunderagent_retained_with_native_migration"
            if strategy == "thunderagent_retained" else
            "thunderagent_with_native_migration" if strategy == "thunderagent"
            else "agent_working_set_with_native_migration"
        ),
        "identity": "invocation_id", "future_information_used": False,
        "native_capacity_authority_preserved": True,
        "counts": {"registered": 2, "completed": 2},
    }}
    if strategy == "thunderagent_retained":
        actual["agent_admission"].update({
            "blocked_agents_retain_eligibility": True, "control_interval_ms": 5000.,
            "maintenance_interval_ms": 1000, "restore_reservation_ms": 1500,
            "restore_reservation_scope": "join_eos_only",
            "ordinary_restore_reservation_ms": 0,
        })
    monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda _path: actual)
    plan = {"reference_environment": {"AGENT_ADMISSION": strategy},
            "source_plan": {"requests": 2}}
    comparison.observed_configuration(plan, "full", tmp_path, final=True)
    actual["agent_admission"]["counts"]["completed"] = 1
    with pytest.raises(ValueError, match="request population"):
        comparison.observed_configuration(plan, "full", tmp_path, final=True)
    plan["reference_environment"]["AGENT_ADMISSION"] = "off"
    with pytest.raises(ValueError, match="undeclared"):
        comparison.observed_configuration(plan, "full", tmp_path, final=True)


def test_join_checkpoint_reservation_survives_next_epoch_registration(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        req = request("first", "parent", tokens=300)
        req.session_id, req.session_generation = "same-session", 1
        live.register_visible_request(req)
        original = live.visible[req.rid]
        live.on_batch_completed(NS(reqs=[req]))
        agents = live._agent_admission
        assert agents.claim_restore(original, source="join_ticket")
        successor = request("next", "parent", tokens=310)
        successor.session_id, successor.session_generation = "same-session", 1
        successor.beliefkv_metadata["context_epoch"] = 1
        live.register_visible_request(successor)
        assert agents.holds_restore(original)
        assert agents.claim_restore(original, source="join_ticket")
        assert not agents.holds_restore(replace(original, session_generation=2))
        live.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        assert not agents.holds_restore(original)
    finally:
        live.close()


def test_parallel_short_tool_completion_keeps_the_outstanding_long_wait(monkeypatch, tmp_path, strategy="working_set"):
    live = runtime(monkeypatch, tmp_path, strategy=strategy)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="one", context_id="one-context"),
        ))
        req = request("first", "one", tokens=100, workflow="wf")
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        live.on_events((
            observed(2, Kind.TOOL_START, invocation_id="one", attributes={"tool_run_id": "long"}),
            observed(3, Kind.TOOL_START, invocation_id="one", attributes={"tool_run_id": "short"}),
            observed(4, Kind.TOOL_END, invocation_id="one", attributes={"tool_run_id": "short"}),
        ))
        agents = live._agent_admission
        assert set(agents.tool_waits["one"]) == {"long"}
        live._long_tool_wait = Mock(return_value=False)
        agents.tick(now=agents.tool_waits["one"]["long"] + 1.1)
        assert not agents.policy.agents["one"].active
    finally:
        live.close()


def test_batched_tool_replacement_does_not_inherit_the_finished_tool_start(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="one", context_id="one-context"),
        ))
        req = request("first", "one", tokens=100, workflow="wf")
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        live.on_events((observed(
            2, Kind.TOOL_START, invocation_id="one", attributes={"tool_run_id": "old"},
        ),))
        agents = live._agent_admission
        agents.tool_waits["one"]["old"] = 0.
        live.on_events((
            observed(3, Kind.TOOL_END, invocation_id="one", attributes={"tool_run_id": "old"}),
            observed(4, Kind.TOOL_START, invocation_id="one", attributes={"tool_run_id": "new"}),
        ))
        assert set(agents.tool_waits["one"]) == {"new"}
        assert agents.tool_waits["one"]["new"] > 0.
    finally:
        live.close()


@pytest.mark.parametrize("mode", ("scheduler_only", "join_priority_only", "kv_only", "native_matched"))
def test_other_controls_never_inherit_the_new_agent_candidate(mode, strategy="working_set"):
    settings = arm_environment({
        "AGENT_ADMISSION": strategy, "TOOL_RESTORE_MODE": "execution",
    }, mode)
    assert settings["BELIEFKV_AGENT_ADMISSION"] == settings["AGENT_ADMISSION"] == "off"


@pytest.mark.parametrize("case", (
    test_real_prefill_filters_paused_agents_and_preserves_full_migration_policies,
    test_two_short_tool_successors_share_one_agent_admission,
    test_real_join_releases_parent_and_restore_budget_is_bound_to_its_reentry,
    test_speculative_restore_does_not_restore_paused_agent_or_wrong_generation,
    test_native_retraction_updates_attempt_without_creating_another_agent,
    test_abort_all_releases_idle_agent_budget_as_well_as_pending_requests,
    test_final_observer_requires_declared_agent_identity_and_complete_work,
    test_parallel_short_tool_completion_keeps_the_outstanding_long_wait,
), ids=lambda case: case.__name__)
def test_thunderagent_migration_runtime_lifecycle(case, monkeypatch, tmp_path):
    case(monkeypatch, tmp_path, strategy="thunderagent")


@pytest.mark.parametrize("mode", ("scheduler_only", "join_priority_only", "kv_only", "native_matched"))
def test_other_controls_never_inherit_thunderagent_migration(mode):
    test_other_controls_never_inherit_the_new_agent_candidate(mode, strategy="thunderagent")


@pytest.mark.parametrize("case", (
    test_real_prefill_filters_paused_agents_and_preserves_full_migration_policies,
    test_two_short_tool_successors_share_one_agent_admission,
    test_real_join_releases_parent_and_restore_budget_is_bound_to_its_reentry,
    test_speculative_restore_does_not_restore_paused_agent_or_wrong_generation,
    test_native_retraction_updates_attempt_without_creating_another_agent,
    test_abort_all_releases_idle_agent_budget_as_well_as_pending_requests,
    test_final_observer_requires_declared_agent_identity_and_complete_work,
), ids=lambda case: case.__name__)
def test_retained_thunderagent_migration_runtime_lifecycle(case, monkeypatch, tmp_path):
    case(monkeypatch, tmp_path, strategy="thunderagent_retained")


@pytest.mark.parametrize("mode", ("scheduler_only", "join_priority_only", "kv_only", "native_matched"))
def test_other_controls_never_inherit_retained_thunderagent_migration(mode):
    test_other_controls_never_inherit_the_new_agent_candidate(mode, strategy="thunderagent_retained")


@pytest.mark.parametrize("source", ("execution_handoff", "tool_wait"))
def test_retained_ordinary_recovery_cannot_create_an_extra_agent_reservation(
    monkeypatch, tmp_path, source,
):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        req = request("ordinary", "one", tokens=300)
        live.register_visible_request(req)
        agents = live._agent_admission
        key = live.visible[req.rid]
        assert agents.allows(key)
        reserve = Mock(wraps=agents.policy.reserve_restore)
        agents.policy.reserve_restore = reserve
        assert not agents.claim_restore(key, source=source)
        reserve.assert_not_called()
        assert agents.policy.restore_owner is None
        assert agents.status()["ordinary_restore_reservation_ms"] == 0
        assert agents.status()["restore_reservation_scope"] == "join_eos_only"
        assert agents.claim_restore(key, source="join_ticket")
        reserve.assert_called_once()
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        assert agents.policy.restore_owner is None
        assert agents.policy.agents["one"].in_flight
    finally:
        live.close()


def test_retained_post_forward_keeps_join_and_backup_without_frontier_planning(
    monkeypatch, tmp_path,
):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        req = request("observed-eos", "child", tokens=100)
        live.register_visible_request(req)
        live._eos_prefetch_keys[req.rid] = live.visible[req.rid]
        for name in (
            "dispatch_join_prefetch", "dispatch_frontier_restore", "dispatch_tool_prefetch",
            "dispatch_join_prepare", "dispatch_tool_prepare", "dispatch_idle_prepare",
        ):
            monkeypatch.setattr(live, name, Mock())
        live.dispatch_after_forward([request("next", "one", tokens=300)])
        live.dispatch_join_prefetch.assert_called_once()
        live.dispatch_frontier_restore.assert_not_called()
        live.dispatch_tool_prefetch.assert_not_called()
        live.dispatch_join_prepare.assert_called_once()
        live.dispatch_tool_prepare.assert_called_once()
        live.dispatch_idle_prepare.assert_called_once()
    finally:
        live.close()


def test_retained_configuration_rejects_ordinary_frontier_restore():
    with pytest.raises(ValueError, match="native ordinary demand recovery"):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_retained", "TOOL_RESTORE_MODE": "execution",
            "EXECUTION_FRONTIER_RESTORE": "1",
        }, "full")


@pytest.mark.parametrize("setting", (
    "BELIEFKV_ENABLE_FRONTIER_RESTORE", "BELIEFKV_ENABLE_EXECUTION_HANDOFF",
))
def test_retained_runtime_rejects_direct_ordinary_restore_override(monkeypatch, setting):
    monkeypatch.setenv("BELIEFKV_AGENT_ADMISSION", "thunderagent_retained")
    monkeypatch.setenv("BELIEFKV_TOOL_RESTORE_MODE", "execution")
    monkeypatch.setenv(setting, "1")
    with pytest.raises(ValueError, match="native ordinary demand recovery"):
        NativeAdmissionRuntime()


def test_retained_tool_warming_keeps_original_eligibility_without_new_restore_reservation(
    monkeypatch, tmp_path,
):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="one", context_id="one-context"),
        ))
        req = request("first", "one", tokens=100, workflow="wf")
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        live.on_events((observed(
            2, Kind.TOOL_START, invocation_id="one", attributes={"tool_run_id": "tool"},
        ),))
        agents = live._agent_admission
        key = agents.latest["one"]
        reserve = Mock(wraps=agents.policy.reserve_restore)
        agents.policy.reserve_restore = reserve
        assert agents.claim_restore(key, source="tool_wait")
        assert agents.holds_restore(key, source="tool_wait")
        assert not agents.holds_restore(replace(key, context_epoch=key.context_epoch + 1),
                                        source="tool_wait")
        assert agents.policy.restore_owner is None
        reserve.assert_not_called()
        agents.policy.agents["one"].active = False
        assert not agents.claim_restore(key, source="tool_wait")
        agents.policy.agents["one"].active = True
        live.on_events((observed(
            3, Kind.TOOL_END, invocation_id="one", attributes={"tool_run_id": "tool"},
        ),))
        assert not agents.holds_restore(key, source="tool_wait")
    finally:
        live.close()


def test_retained_full_can_enable_conditional_tools_without_enabling_ordinary_frontier(
    monkeypatch, tmp_path,
):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained", "TOOL_RESTORE_MODE": "speculative",
        "TOOL_PREFETCH_PRIORITY_MS": "1500", "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "conditional.sock"))
    try:
        assert live.enable_tool_prefetch and live.tool_prefetch_priority_ms == 1500
        assert live.coadmit_join_children
        assert not live.enable_frontier_restore and not live.enable_execution_handoff
    finally:
        live.close()


@pytest.mark.parametrize("change", (None, "siblings", "tool_eligibility", "priority_window"))
def test_conditional_tool_candidate_observer_checks_its_effective_settings(
    monkeypatch, tmp_path, change,
):
    from scripts import run_causal_comparison as comparison

    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda *_a, **_k: None)
    agents = {
        "policy": "thunderagent_retained_with_native_migration",
        "identity": "invocation_id", "future_information_used": False,
        "native_capacity_authority_preserved": True,
        "counts": {"registered": 2, "completed": 2},
        "blocked_agents_retain_eligibility": True, "control_interval_ms": 5000.,
        "maintenance_interval_ms": 1000, "restore_reservation_ms": 1500,
        "restore_reservation_scope": "join_eos_only", "ordinary_restore_reservation_ms": 0,
        "coadmit_join_children": True,
        "tool_warming_eligibility": "existing_active_tool_wait_only_no_new_reservation",
    }
    actual = {"agent_admission": agents, "tool_prefetch_priority_ms": 1500.}
    if change == "siblings":
        agents["coadmit_join_children"] = False
    elif change == "tool_eligibility":
        agents["tool_warming_eligibility"] = "selected_agent_restore_reservation"
    elif change == "priority_window":
        actual["tool_prefetch_priority_ms"] = 0.
    monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda _path: actual)
    plan = {
        "reference_environment": {
            "AGENT_ADMISSION": "thunderagent_retained", "AGENT_COADMIT_JOIN_CHILDREN": "1",
            "TOOL_PREFETCH": "1", "TOOL_RESTORE_MODE": "speculative",
        },
        "source_plan": {"requests": 2}, "tool_prefetch_priority_ms": 1500.,
    }
    if change is None:
        comparison.observed_configuration(plan, "full", tmp_path, final=True)
    else:
        with pytest.raises(ValueError):
            comparison.observed_configuration(plan, "full", tmp_path, final=True)


@pytest.mark.parametrize("mode", ("scheduler_only", "join_priority_only", "kv_only", "native_matched"))
def test_sibling_admission_is_full_only(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained", "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, mode)
    assert settings["BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN"] == "0"
