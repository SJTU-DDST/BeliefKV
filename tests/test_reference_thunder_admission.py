from copy import deepcopy
from dataclasses import asdict, replace
import random
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter
from beliefkv.runtime.reference_thunder_admission import (
    ReferenceThunderAdmission, validate_reference_admission,
)
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request


def full_runtime(monkeypatch, tmp_path, *, capacity=1000):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "TOOL_RESTORE_MODE": "speculative",
        "EXECUTION_FRONTIER_RESTORE": "0",
        "TOOL_CONTINUITY": "1",
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "reference.sock"))
    live.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=capacity),
    ))
    return live


def paper_runtime():
    return NS(
        internal_comparison_mode="scheduler_only", _custom_kv_management=False,
        enable_resident_first=False, enable_join_parent_priority=False,
        enable_join_last_child_priority=False, enable_final_stage_priority=False,
        _tool_continuity=None, _model_worker=None, _semantic_worker=None,
        _online_tool_timing=None, visible={},
    )


def state(adapter):
    return {
        "programs": {name: asdict(item) for name, item in adapter.policy.programs.items()},
        "acting_since": dict(adapter.policy.acting_since),
        "remaining": adapter.policy.remaining_capacity(),
        "next_tick": adapter.next_tick,
        "last_forced": set(adapter.last_forced),
    }


@pytest.mark.parametrize("seed", (21, 43, 79, 107))
def test_actual_full_lifecycle_matches_reference_decay_without_logical_loans(monkeypatch, tmp_path, seed):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    live = full_runtime(monkeypatch, tmp_path, capacity=2000)
    candidate = live._agent_admission
    paper = ThunderAgentDecayAdapter(paper_runtime())
    paper.attach(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=2000)))
    rng = random.Random(seed)
    serial = 0
    try:
        for _ in range(240):
            clock[0] += rng.choice((.05, .2, 1., 5., 30.))
            pending = [
                rid for rid, (key, _arrived) in candidate.requests.items()
                if candidate.policy.programs[key.invocation_id].active
            ]
            available = [
                name for name in ("a", "b", "c", "d", "e", "f")
                if name not in candidate.policy.programs
                or not candidate.policy.programs[name].pending
            ]
            if pending and (not available or rng.random() < .5):
                rid = rng.choice(pending)
                req = candidate._live_requests[rid]
                req.output_ids.extend([3] * rng.randrange(1, 100))
                batch = NS(reqs=[req])
                live.on_batch_completed(batch)
                paper.completed(batch, (rid,))
            elif available:
                serial += 1
                invocation = rng.choice(available)
                req = request(str(serial), invocation, tokens=rng.randrange(100, 850))
                live.register_visible_request(req)
                key = live.visible[req.rid]
                paper.runtime.visible[req.rid] = key
                paper.registered(req, key)
            for adapter in (candidate, paper):
                adapter.tick(now=clock[0])
            assert state(candidate) == state(paper)
            before = state(candidate)
            for key in tuple(candidate.latest.values()):
                candidate.claim_restore(key, source="join_ticket")
            assert state(candidate) == before
        validate_reference_admission(candidate.status())
        assert type(candidate.policy) is ThunderActingDecayWorkingSet
        assert not hasattr(candidate.policy, "loan_borrowers")
    finally:
        live.close()
        paper.close()


def test_native_prefill_filters_paused_agents_with_full_migration_enabled(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, capacity=500)
    try:
        first, second = request("one", "a", tokens=300), request("two", "b", tokens=300)
        live.register_visible_request(first)
        live.register_visible_request(second)
        live._agent_admission.next_tick = float("inf")
        plan = live.plan_native_prefill([second, first], running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            [second, first], plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert selected.candidates == (first,)
        assert live._custom_kv_management and live.enable_prepare_host
        assert live.enable_tool_prefetch and live.enable_join_parent_priority
        assert live._tool_continuity is not None and live._paper_baseline is None
        assert not live.enable_frontier_restore and not live.enable_execution_handoff
    finally:
        live.close()


def test_join_warming_keeps_existing_eligibility_and_rejects_paused_or_stale_owner(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context"),
            observed(2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                     parent_invocation_id="a", relation_type=RelationType.SPAWN),
            observed(3, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
        ))
        req = request("one", "a", tokens=800, workflow="wf")
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        live.on_events((observed(4, Kind.JOIN_WAIT, invocation_id="a", join_id="join"),))
        adapter = live._agent_admission
        key = adapter.latest["a"]
        before = state(adapter)
        assert adapter.claim_restore(key, source="join_ticket")
        assert state(adapter) == before
        assert not adapter.claim_restore(replace(key, session_generation=7), source="join_ticket")
        adapter.policy.programs["a"].active = False
        before = state(adapter)
        assert not adapter.claim_restore(key, source="join_ticket")
        assert not adapter.claim_restore(key, source="execution_handoff")
        assert state(adapter) == before
    finally:
        live.close()


def test_tool_warming_requires_real_tool_wait_and_existing_active_owner(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context"),
        ))
        req = request("one", "a", workflow="wf")
        live.register_visible_request(req)
        live.on_batch_completed(NS(reqs=[req]))
        adapter = live._agent_admission
        key = adapter.latest["a"]
        assert not adapter.claim_restore(key, source="tool_wait")
        live.on_events((observed(
            2, Kind.TOOL_START, invocation_id="a", attributes={"tool_call_id": "tool"},
        ),))
        before = state(adapter)
        assert adapter.claim_restore(key, source="tool_wait")
        assert not adapter.claim_restore(replace(key, context_epoch=1), source="tool_wait")
        assert state(adapter) == before
        live.on_events((observed(
            3, Kind.TOOL_END, invocation_id="a", attributes={"tool_call_id": "tool"},
        ),))
        assert not adapter.claim_restore(key, source="tool_wait")
    finally:
        live.close()


def test_terminal_events_clear_reference_programs_and_restore_identity(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context"),
        ))
        live.register_visible_request(request("one", "a", workflow="wf"))
        adapter = live._agent_admission
        key = adapter.latest["a"]
        live.on_events((observed(2, Kind.WORKFLOW_END),))
        assert not adapter.policy.programs and not adapter.requests and not adapter.latest
        assert not adapter.claim_restore(key, source="join_ticket")
    finally:
        live.close()


@pytest.mark.parametrize("field,value", (
    ("logical_borrowing_enabled", True), ("restore_changes_eligibility", True),
    ("blocked_lending", "backed_demand_recall"), ("restore_reservation_ms", 1500),
    ("acting_token_decay", False), ("decay_half_life_s", True),
    ("undiscounted_capacity_enforced", False), ("physical_capacity_credit", True),
))
def test_effective_reference_state_rejects_changed_policy(monkeypatch, tmp_path, field, value):
    live = full_runtime(monkeypatch, tmp_path)
    try:
        status = deepcopy(live._agent_admission.status())
        status[field] = value
        with pytest.raises(ValueError, match="reference ThunderAgent"):
            validate_reference_admission(status)
    finally:
        live.close()


@pytest.mark.parametrize("setting", (
    {"EXECUTION_FRONTIER_RESTORE": "1"},
    {"SESSIONLESS_PREFIX_MIGRATION": "1"},
    {"AGENT_BLOCKED_LENDING": "backed_demand"},
    {"AGENT_PRESSURE_PAUSE_POLICY": "projected_release"},
    {"AGENT_JOIN_DEPENDENCY_ADMISSION": "1"},
))
def test_reference_candidate_rejects_retained_extensions(setting):
    with pytest.raises(ValueError):
        arm_environment({"AGENT_ADMISSION": "thunderagent_reference", **setting}, "full")


@pytest.mark.parametrize("mode", ("scheduler_only", "kv_only", "native_matched"))
def test_reference_candidate_does_not_leak_into_controls(mode):
    settings = arm_environment({"AGENT_ADMISSION": "thunderagent_reference"}, mode)
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"


def test_production_preflight_constructs_the_reference_full_candidate(monkeypatch, tmp_path):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "TOOL_RESTORE_MODE": "speculative",
    }, "full")
    result = comparison.preflight_runtime(settings, str(tmp_path))
    assert result["runtime_configuration_verified"] is True
    assert result["verified_configuration"]["enable_agent_working_set"] is True
    assert result["verified_configuration"]["enable_tool_prefetch"] is True


def test_scheduling_and_native_lifecycle_methods_are_the_reference_implementations():
    for name in ("attach", "tick", "completed", "aborted", "_refresh_streaming_progress"):
        assert getattr(ReferenceThunderAdmission, name) is getattr(ThunderAgentDecayAdapter, name)


def test_native_requeue_refreshes_the_restore_identity_without_new_program_arrival(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path)
    try:
        req = request("one", "a")
        live.register_visible_request(req)
        old = live.visible["one"]
        req.cache_request_handle.attempt_id = 1
        live.on_requests_requeued((req,), is_retracted=True)
        adapter = live._agent_admission
        assert adapter.latest["a"].attempt_id == 1
        assert adapter.allows(live.visible["one"])
        assert not adapter.allows(old)
        assert adapter.policy.programs["a"].steps == 1
        assert adapter.counts["registered"] == 1
    finally:
        live.close()
