from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import full_runtime, state


def join_wait(live, *, tokens=200, continuation=False):
    live.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
        observed(2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                 parent_invocation_id="parent", relation_type=RelationType.SPAWN),
        observed(3, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
    ))
    req = request("parent-first", "parent", tokens=tokens, workflow="wf")
    live.register_visible_request(req)
    live.on_batch_completed(NS(reqs=[req]))
    if continuation:
        following = request("parent-second", "parent", tokens=tokens, workflow="wf")
        live.register_visible_request(following)
        live.on_batch_completed(NS(reqs=[following]))
    live.on_events((observed(4, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
    adapter = live._agent_admission
    adapter.policy._pause(adapter.policy.programs["parent"], time.monotonic())
    return adapter, adapter.latest["parent"]


def test_actual_join_can_use_free_complete_budget_without_changing_a_peer(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, join_free_admission=True)
    try:
        adapter, key = join_wait(live)
        live.register_visible_request(request("peer", "peer", tokens=400))
        peer = deepcopy(adapter.policy.programs["peer"])
        before = adapter.policy.remaining_capacity()
        assert adapter.claim_restore(key, source="join_ticket")
        assert adapter.holds_restore(key)
        assert adapter.policy.programs["peer"] == peer
        assert adapter.policy.remaining_capacity() == before - 302
        assert adapter.counts["join_free_capacity_admitted"] == 1
        assert adapter.counts["restore_new_join_eligibility"] == 1
        assert adapter.claim_restore(key, source="join_ticket")
        assert adapter.policy.remaining_capacity() == before - 302
        assert adapter.counts["join_free_capacity_admitted"] == 1
        validate_reference_admission(adapter.status(), join_free_admission=True)
        with pytest.raises(ValueError):
            validate_reference_admission(adapter.status())
    finally:
        live.close()


def test_join_admission_never_uses_decayed_acting_capacity_or_pauses_a_peer(monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    live = full_runtime(monkeypatch, tmp_path, capacity=700, join_free_admission=True)
    try:
        adapter, key = join_wait(live, continuation=True)
        req = request("peer", "peer", tokens=400)
        live.register_visible_request(req)
        adapter.tick(now=clock[0])
        live.on_batch_completed(NS(reqs=[req]))
        clock[0] += 20.
        assert adapter.policy.remaining_capacity_with_decay(now=clock[0]) >= 302
        assert adapter.policy.remaining_capacity() == 198
        before = state(adapter)
        assert not adapter.claim_restore(key, source="join_ticket")
        assert state(adapter) == before
        assert adapter.counts["join_free_capacity_insufficient"] == 1
    finally:
        live.close()


@pytest.mark.parametrize("scenario", (
    "disabled", "tool_source", "ordinary_source", "ready_parent", "released_join",
    "pending_model", "marked", "stale_epoch", "stale_generation", "other_context",
))
def test_free_budget_alone_cannot_authorize_unproven_parent_warming(monkeypatch, tmp_path, scenario):
    live = full_runtime(monkeypatch, tmp_path, join_free_admission=scenario != "disabled")
    try:
        adapter, key = join_wait(live)
        source = "join_ticket"
        if scenario == "tool_source":
            source = "tool_wait"
        elif scenario == "ordinary_source":
            source = "execution_handoff"
        elif scenario == "ready_parent":
            live.on_events((observed(5, Kind.JOIN_TIMEOUT, join_id="join"),))
        elif scenario == "released_join":
            live.graph.joins["join"].released_waiter_ids.add("parent")
        elif scenario == "pending_model":
            adapter.policy.arrive("parent", 220, now=time.monotonic())
        elif scenario == "marked":
            adapter.policy.programs["parent"].marked_for_pause = True
        elif scenario == "stale_epoch":
            key = replace(key, context_epoch=key.context_epoch + 1)
        elif scenario == "stale_generation":
            key = replace(key, session_generation=7)
        elif scenario == "other_context":
            key = replace(key, context_id="other")
        before = state(adapter)
        assert not adapter.claim_restore(key, source=source)
        assert state(adapter) == before
    finally:
        live.close()


@pytest.mark.parametrize("mode", ("scheduler_only", "kv_only", "native_matched"))
def test_candidate_free_join_admission_is_removed_from_controls(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_JOIN_FREE_ADMISSION": "1",
    }, mode)
    assert settings["AGENT_JOIN_FREE_ADMISSION"] == "0"
    assert settings["BELIEFKV_AGENT_JOIN_FREE_ADMISSION"] == "0"


def test_paper_decay_control_does_not_inherit_candidate_join_admission():
    settings = paper_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_JOIN_FREE_ADMISSION": "1",
    }, "thunderagent_acting_decay")
    assert settings["BELIEFKV_AGENT_JOIN_FREE_ADMISSION"] == "0"
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"


def test_candidate_constructor_and_explicit_runtime_declaration(monkeypatch, tmp_path):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_JOIN_FREE_ADMISSION": "1",
    }, "full")
    proof = comparison.preflight_runtime(settings, str(tmp_path))
    assert proof["verified_configuration"]["agent_join_free_admission"] is True
    with pytest.raises(ValueError, match="free-capacity JOIN"):
        arm_environment({"AGENT_JOIN_FREE_ADMISSION": "1"}, "full")


def test_candidate_changes_only_the_explicit_join_admission_switch():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_reference_thunder_20261027.json").read_bytes())
    candidate = json.loads((root / "configs/migration/predictive_reference_join_free_20261027.json").read_bytes())
    environment = dict(candidate["common_environment"])
    for name in ("AGENT_JOIN_FREE_ADMISSION", "BELIEFKV_AGENT_JOIN_FREE_ADMISSION"):
        assert environment.pop(name) == "1"
    assert environment == base["common_environment"]
    assert candidate["tool_timing_overlay"] == base["tool_timing_overlay"]
    assert candidate["control_reuse"] == base["control_reuse"]


@pytest.mark.parametrize("full_native", (512,), indirect=True)
@pytest.mark.parametrize("physical_room", (True, False))
def test_logical_join_grant_keeps_real_native_allocation_ack_and_host_ownership(
    full_native, monkeypatch, tmp_path, physical_room,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_JOIN_FREE_ADMISSION": "1",
        "TOOL_RESTORE_MODE": "speculative", "EXECUTION_FRONTIER_RESTORE": "0",
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "native-free-join.sock"))
    live.attach_native_cache(cache)
    try:
        adapter, _ = join_wait(live, tokens=8)
        node, generation = insert_session(cache, list(range(8)), "parent-session")
        previous = adapter.latest["parent"]
        key = replace(previous, session_id="parent-session", session_generation=generation)
        adapter.latest["parent"] = key
        free = allocator.available_size()
        assert cache.prepare_beliefkv_spawn_full(
            session_id=key.session_id, session_generation=key.session_generation,
        )["issued"]
        assert len(writes) == 1
        cache.writing_check(finish_count=1)
        cache.beliefkv_cold_full_owner_validator = lambda _owners: True
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=free + 8, mamba_slots=0,
        ) == {0: 8}
        if not physical_room:
            held = allocator.alloc(allocator.available_size() - 4)
            assert held is not None
        before = allocator.available_size()
        assert adapter.claim_restore(key, source="join_ticket")
        assert allocator.available_size() == before
        outcomes = cache.prefetch_gpu_session_nodes(
            session_id=key.session_id, session_generation=key.session_generation,
            leaf_node_id=node.id, leaf_creation_time=float(node.creation_time),
            nodes=((node.id, float(node.creation_time), False, "free-join-restore"),),
            beliefkv_before_enqueue=lambda _operation: adapter.holds_restore(key),
        )
        assert sum(outcome.issued for outcome in outcomes) == int(physical_room)
        assert len(loads) == int(physical_room)
        assert allocator.available_size() == before - 8 * int(physical_room)
        assert node.component_data[0].host_value is not None
        if physical_room:
            assert node.load_back_pending_id is not None
            cache.loading_check(finish_count=1)
            assert node.load_back_pending_id is None
            assert node.component_data[0].value is not None
        assert node.component_data[0].host_lock_ref == 0
    finally:
        live.close()
