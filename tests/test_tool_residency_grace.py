from array import array
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.tool_residency_grace import ToolResidencyGrace
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import join_fixture, observed, request
from tests.test_tool_continuity import tool


def test_candidate_changes_only_the_declared_short_tool_grace():
    root = Path(__file__).resolve().parents[1] / "configs/migration"
    source = json.loads((root / "qwen3_coder_18project_root_phase16_20261024.json").read_text())
    candidate = json.loads((
        root / "qwen3_coder_18project_root_phase16_tool_grace_20261024.json"
    ).read_text())
    before, after = source["common_environment"], candidate["common_environment"]
    assert after == {**before, "TOOL_CONTINUITY_GRACE_MS": "100"}
    assert candidate["performance_accepted"] is False
    assert candidate["order"] == ["full"]


@pytest.fixture
def pinned_round(full_native, monkeypatch):
    from test_beliefkv_terminal_capacity import insert_session

    clock = [12.]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    runtime, (first, peer) = join_fixture(
        monkeypatch, mode="full", scheduling="workflow",
        tool_continuity=True, tool_continuity_grace_ms=100,
    )
    runtime._join_priority_pending.clear()
    cache, allocator, writes, loads, _ = full_native
    runtime.attach_native_cache(cache)
    first.origin_input_ids = array("q", (10, 11, 12, 13, 14))
    first.output_ids = [99]
    first.finished = lambda: True
    peer.finished = lambda: False
    runtime._tool_open_token_ids = frozenset((99,))
    leaf, generation = insert_session(cache, [10, 11, 12, 13], first.session_id)
    assert generation == first.session_generation
    assert cache.prepare_beliefkv_spawn_full(
        session_id=first.session_id, session_generation=generation,
    )["issued"]
    cache.writing_check(finish_count=1)
    cache.tree_core.set_hicache_enabled()
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    key = runtime.visible[first.rid]
    runtime.on_batch_completed(NS(reqs=[first, peer]))
    assert runtime._tool_residency_grace.key == key
    assert leaf.component_data[0].lock_ref == 1
    try:
        yield runtime, first, peer, leaf, clock, cache, allocator, writes, loads
    finally:
        runtime.close()
    assert leaf.component_data[0].lock_ref == 0
    assert len(writes) == 1 and not loads


@pytest.mark.parametrize("hold_ms", (True, False, 0, -1, 1001, float("nan"), float("inf"), "100"))
def test_invalid_grace_is_rejected(hold_ms):
    with pytest.raises(ValueError):
        ToolResidencyGrace(hold_ms)


def test_native_residency_survives_gap_and_hands_off_to_successor_lock(pinned_round):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    runtime, first, peer, leaf, clock, cache, allocator, _, _ = pinned_round
    host_copy = leaf.component_data[0].host_value
    free = allocator.available_size()
    assert cache.evict(EvictParams(num_tokens=4)).num_tokens_evicted == 0
    assert allocator.available_size() == free
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
    successor.origin_input_ids = first.origin_input_ids
    runtime.register_visible_request(successor)
    runtime._refresh_tool_residency_grace()
    assert runtime._tool_residency_grace.key is not None
    runtime._residency_ordering_hints[runtime.visible[successor.rid]] = (13000., {})
    # Native admission pins the checkpoint before reporting success to runtime.
    native = cache.inc_lock_ref(leaf.id)
    runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
    assert runtime._tool_residency_grace.key is None
    assert not runtime._residency_ordering_hints
    assert leaf.component_data[0].lock_ref == 1
    assert cache.evict(EvictParams(num_tokens=4)).num_tokens_evicted == 0
    cache.dec_lock_ref(leaf.id, native.to_dec_params())
    assert leaf.component_data[0].host_value is host_copy
    assert cache.evict(EvictParams(num_tokens=4)).num_tokens_evicted == 4
    assert allocator.available_size() == free + 4


@pytest.mark.parametrize("reason", (
    "expiry", "ready_join", "join_eos", "critical_parent", "disabled",
    "generation", "epoch", "return", "workflow_end", "abort", "retraction",
    "cache_changed", "idle_gpu", "allocation_pressure", "decode_reserve", "mirror_failure",
))
def test_grace_is_revoked_before_it_can_obstruct_progress(pinned_round, monkeypatch, reason):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    runtime, first, peer, leaf, clock, cache, allocator, _, _ = pinned_round
    key = runtime._tool_residency_grace.key
    if reason == "expiry":
        clock[0] = 12.101
    elif reason == "ready_join":
        runtime._join_priority_pending["parent"] = "join"
    elif reason == "join_eos":
        runtime._eos_prefetch_keys["child"] = key
    elif reason == "critical_parent":
        runtime._set_critical_parent_demand(key, 4, 0, 13000.)
    elif reason == "disabled":
        runtime.physical_disabled = True
    elif reason == "generation":
        runtime.context_sessions[key.context_id] = replace(key, session_generation=2)
    elif reason == "epoch":
        runtime.context_sessions[key.context_id] = replace(key, context_epoch=2)
    elif reason == "return":
        runtime.on_events((observed(
            100, Kind.RETURN, invocation_id=key.invocation_id,
        ),))
    elif reason == "workflow_end":
        runtime.on_events((observed(100, Kind.WORKFLOW_END),))
    elif reason == "abort":
        runtime.on_abort_request(NS(rid=first.rid))
    elif reason == "retraction":
        runtime._tool_continuity.retracted(key)
    elif reason == "cache_changed":
        runtime.attach_native_cache(cache)
    elif reason == "idle_gpu":
        runtime.plan_native_prefill([], running_batch=NS(reqs=[]), adder=None)
    elif reason == "allocation_pressure":
        assert runtime.on_prefill_candidate_result(
            peer, admitted=False, result="NO_TOKEN",
        ) is True
    elif reason == "decode_reserve":
        allocator.page_size = 1
        monkeypatch.setattr(allocator, "available_size", lambda: 0)
    elif reason == "mirror_failure":
        monkeypatch.setattr(
            runtime.graph, "apply_batch",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad mirror")),
        )
        with pytest.raises(ValueError, match="bad mirror"):
            runtime.on_events((observed(100, Kind.TOOL_START, invocation_id="normal"),))
    runtime._refresh_tool_residency_grace()
    assert runtime._tool_residency_grace.key is None
    assert leaf.component_data[0].lock_ref == 0
    assert cache.evict(EvictParams(num_tokens=4)).num_tokens_evicted == 4


def test_native_prefix_split_preserves_the_paired_grace_lock(pinned_round):
    from test_beliefkv_terminal_capacity import insert_session

    runtime, _, _, leaf, _, cache, _, _, _ = pinned_round
    insert_session(cache, [10, 11, 50, 51], "other-session")
    assert len(leaf.component_data[0].value) == 2
    assert leaf.component_data[0].lock_ref == 1
    assert leaf.parent.component_data[0].lock_ref == 1
    runtime._release_tool_residency_grace("split_verified")
    assert leaf.component_data[0].lock_ref == 0
    assert leaf.parent.component_data[0].lock_ref == 0


def test_unrelated_ordinary_admission_does_not_drop_the_grace(pinned_round):
    runtime, _, peer, leaf, _, _, _, _, _ = pinned_round
    runtime.on_prefill_candidate_result(peer, admitted=True, result="CONTINUE")
    assert runtime._tool_residency_grace.key is not None
    assert leaf.component_data[0].lock_ref == 1


@pytest.mark.parametrize("mode,continuity,expected", (
    ("full", True, 100.), ("full", False, 0.),
    ("scheduler_only", True, 0.), ("join_priority_only", True, 0.),
    ("kv_only", True, 0.),
))
def test_effective_grace_is_limited_to_custom_kv_with_continuity(
    monkeypatch, mode, continuity, expected,
):
    common = {"TOOL_CONTINUITY": str(int(continuity)), "TOOL_CONTINUITY_GRACE_MS": "100"}
    settings = arm_environment(common, mode)
    assert float(settings["BELIEFKV_TOOL_CONTINUITY_GRACE_MS"]) == expected
    runtime, _ = join_fixture(
        monkeypatch, mode=mode, scheduling="workflow",
        tool_continuity=continuity, tool_continuity_grace_ms=100,
    )
    try:
        assert runtime.tool_continuity_grace_ms == expected
        assert (runtime._tool_residency_grace is not None) == bool(expected)
    finally:
        runtime.close()


@pytest.mark.parametrize("configuration", ("-1", "1001", "nan", "inf", "bad"))
def test_invalid_grace_environment_is_rejected(monkeypatch, configuration):
    with pytest.raises(ValueError):
        join_fixture(
            monkeypatch, mode="full", scheduling="workflow",
            tool_continuity=True, tool_continuity_grace_ms=configuration,
        )
    with pytest.raises(ValueError):
        arm_environment({"TOOL_CONTINUITY_GRACE_MS": configuration}, "full")


def test_grace_does_not_acquire_a_second_native_lock(pinned_round):
    runtime, _, _, leaf, _, cache, _, _, _ = pinned_round
    assert not runtime._tool_residency_grace.retain(
        cache, runtime._tool_residency_grace.key, now=12.,
    )
    assert leaf.component_data[0].lock_ref == 1


@pytest.mark.parametrize("fault", ("generation", "already_locked", "write_pending", "load_pending"))
def test_grace_rejects_unsettled_or_unproven_native_state(full_native, fault):
    from test_beliefkv_terminal_capacity import insert_session

    cache, _, _, _, _ = full_native
    node, generation = insert_session(cache, [10, 11, 12, 13], "session")
    key = PrefillCandidateKey("r", "wf", "i", "c", 0, 0, "session", generation)
    prior_lock = None
    if fault == "generation":
        key = replace(key, session_generation=generation + 1)
    elif fault == "already_locked":
        prior_lock = cache.inc_lock_ref(node.id)
    elif fault == "write_pending":
        node.write_through_pending_id = 7
    elif fault == "load_pending":
        node.load_back_pending_id = 7
    grace = ToolResidencyGrace(100)
    try:
        assert not grace.retain(cache, key, now=12.)
        assert grace.key is None
    finally:
        if prior_lock is not None:
            cache.dec_lock_ref(node.id, prior_lock.to_dec_params())
