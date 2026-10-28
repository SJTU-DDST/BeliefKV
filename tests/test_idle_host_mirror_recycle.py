"""Cold backup may replace a pinned Device owner's redundant Host mirror."""

from collections import OrderedDict
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_full_only_native_lifecycle import full_native
from tests.test_sglang_v0520_runtime import req
from tests.test_tool_predictive_transfers import event, tool_runtime


@pytest.fixture
def mirror_runtime(full_native, monkeypatch, request):
    from sglang.srt.mem_cache import unified_radix_cache as native
    from test_beliefkv_terminal_capacity import insert_session

    enabled = getattr(request, "param", True)
    monkeypatch.setenv("BELIEFKV_IDLE_HOST_MIRROR_RECYCLE", str(int(enabled)))
    monkeypatch.setattr(native, "BELIEFKV_IDLE_FULL_PREPARE_MAX_BYTES", 4 * 64)
    cache, allocator, writes, _, _ = full_native
    cold, generation = insert_session(cache, [1, 2, 3, 4], "s")
    hot, _ = insert_session(cache, list(range(100, 108)), "hot")
    full = hot.component_data[0]
    full.host_value = full.value.clone()
    cache.tree_core._update_duplicate_tracking(hot)
    hot_lock = cache.inc_lock_ref(hot.id).to_dec_params()
    runtime, hint = tool_runtime()
    hint = replace(
        hint, key=replace(hint.key, session_generation=generation),
        wait_p10_ms=5000., wait_p50_ms=10_000., wait_p90_ms=20_000.,
    )
    runtime.context_sessions["ctx-tool"] = hint.key
    runtime.tool_wait_hints["ctx-tool"] = hint
    runtime._context_session_changed("ctx-tool")
    runtime._native_running_batch = NS(reqs=[req("hot")])
    allocator.alloc(allocator.available_size())
    host_slots = {"free": 0}
    cache.host_pool_group.available_size = lambda: host_slots["free"]
    cache.host_pool_group.anchor_entry.host_pool.available_size = lambda: host_slots["free"]
    cache.components[0]._full_kv_pool_host = cache.host_pool_group.anchor_entry.host_pool

    def free_host(indices, *, pool):
        assert str(getattr(pool, "value", pool)) == "kv"
        host_slots["free"] += len(indices)

    cache.host_pool_group.free = Mock(side_effect=free_host)
    native_write = cache.cache_controller.write

    def write(indices, **kwargs):
        assert len(indices) <= host_slots["free"]
        result = native_write(indices, **kwargs)
        if result is not None:
            host_slots["free"] -= len(result)
        return result

    cache.cache_controller.write = write
    runtime.attach_native_cache(cache)
    try:
        yield runtime, cache, allocator, cold, hot, writes, host_slots
    finally:
        runtime.close()
        full.lock_ref = 1
        cache.dec_lock_ref(hot.id, hot_lock)


@pytest.mark.parametrize("mirror_runtime", (False, True), indirect=True)
def test_full_host_budget_can_back_cold_state_without_releasing_device_pages(mirror_runtime):
    runtime, cache, allocator, cold, hot, writes, host_slots = mirror_runtime
    enabled = runtime.idle_host_mirror_recycle
    before_cold, before_hot = cold.component_data[0].value, hot.component_data[0].value
    before_free = allocator.available_size()
    runtime.dispatch_idle_prepare()
    assert allocator.available_size() == before_free == 0
    assert cold.component_data[0].value is before_cold
    assert hot.component_data[0].value is before_hot
    assert hot.component_data[0].lock_ref > 0
    if enabled:
        assert len(writes) == 1 and writes[0].node_id == cold.id
        assert hot.component_data[0].host_value is None
        assert cold.component_data[0].host_value is not None
        assert cold.write_through_pending_id is not None
        assert host_slots["free"] == 4
        assert runtime.counts["idle_host_mirror_recycle_host_tokens"] == 8
        assert hot.id not in cache.tree_core.full_host_duplicates
        assert hot.id in cache._beliefkv_unbacked_full
        cache.writing_check(finish_count=1)
        assert cold.write_through_pending_id is None
    else:
        assert not writes and host_slots["free"] == 0
        assert hot.component_data[0].host_value is not None
        assert cold.component_data[0].host_value is None
        assert cache.beliefkv_idle_full_prepare_stats["host_budget_pre_scan_skips"] == 1


def test_enabled_candidate_does_not_silently_leave_the_old_full_host_failure(mirror_runtime):
    runtime, _cache, allocator, cold, _hot, writes, _host_slots = mirror_runtime
    runtime.dispatch_idle_prepare()
    assert len(writes) == 1
    assert cold.component_data[0].host_value is not None
    assert allocator.available_size() == 0


@pytest.mark.parametrize("reason", (
    "mirror_host_lock", "mirror_d2h", "mirror_h2d", "mirror_unpinned",
    "mirror_host_only", "mirror_partial_host", "mirror_ongoing_write",
    "cold_device_lock", "cold_host_lock", "cold_d2h", "cold_h2d",
    "cold_foreign_owner", "cold_generation", "cold_ready", "prediction_short",
    "physical_disabled", "no_pressure", "critical_demand", "buffer_mode",
    "write_queue", "load_queue", "ack_write_queue", "ack_load_queue",
))
def test_recycling_preserves_native_protection_and_actual_blocking(mirror_runtime, monkeypatch, reason):
    runtime, cache, allocator, cold, hot, writes, host_slots = mirror_runtime
    cache.evict_host = Mock(side_effect=AssertionError("no Host-only eviction fallback"))
    if reason == "mirror_host_lock":
        hot.component_data[0].host_lock_ref = 1
    elif reason == "mirror_d2h":
        hot.write_through_pending_id = hot.id
    elif reason == "mirror_h2d":
        hot.load_back_pending_id = hot.id
    elif reason == "mirror_unpinned":
        hot.component_data[0].lock_ref = 0
    elif reason == "mirror_host_only":
        hot.component_data[0].value = None
    elif reason == "mirror_partial_host":
        hot.component_data[0].host_value = hot.component_data[0].host_value[:4]
    elif reason == "mirror_ongoing_write":
        cache.ongoing_write_through[hot.id] = True
    elif reason == "cold_device_lock":
        cold.component_data[0].lock_ref = 1
    elif reason == "cold_host_lock":
        cold.component_data[0].host_lock_ref = 1
    elif reason == "cold_d2h":
        cold.write_through_pending_id = cold.id
    elif reason == "cold_h2d":
        cold.load_back_pending_id = cold.id
    elif reason == "cold_foreign_owner":
        cold.component_data[0].session_ids = {"other"}
    elif reason == "cold_generation":
        cache.session_refs._session_generations["s"] += 1
    elif reason == "cold_ready":
        runtime.graph.invocations["tool"].state = InvocationState.READY
    elif reason == "prediction_short":
        runtime.tool_wait_hints["ctx-tool"] = replace(
            runtime.tool_wait_hints["ctx-tool"],
            wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
        )
    elif reason == "physical_disabled":
        runtime.physical_disabled = True
    elif reason == "no_pressure":
        monkeypatch.setattr(runtime, "_prepare_pressure", lambda *_: (False, False))
    elif reason == "critical_demand":
        monkeypatch.setattr(runtime, "_live_critical_parent_demand", lambda: object())
    elif reason == "buffer_mode":
        cache.host_memory_mode = "buffer_only"
    else:
        getattr(cache.cache_controller, reason).append(object())
    original_host = hot.component_data[0].host_value
    runtime.dispatch_idle_prepare()
    assert hot.component_data[0].host_value is original_host
    assert allocator.available_size() == host_slots["free"] == 0
    assert not writes
    cache.host_pool_group.free.assert_not_called()


def test_existing_host_space_uses_the_original_native_prepare(mirror_runtime):
    runtime, cache, _allocator, _cold, hot, writes, host_slots = mirror_runtime
    host_slots["free"] = 8
    runtime.dispatch_idle_prepare()
    assert len(writes) == 1 and host_slots["free"] == 4
    assert hot.component_data[0].host_value is not None
    assert not runtime.counts["idle_host_mirror_recycle_checks"]
    cache.host_pool_group.free.assert_not_called()


def test_tool_return_between_probe_and_release_keeps_all_mirrors(mirror_runtime, monkeypatch):
    runtime, cache, allocator, _cold, hot, writes, host_slots = mirror_runtime
    original = cache._beliefkv_causal_full_donor
    calls = []

    def check(node, **kwargs):
        calls.append(node.id)
        if len(calls) == 2:
            runtime.on_events((event(
                3, RuntimeEventKind.TOOL_END, invocation_id="tool",
                attributes={"tool_run_id": "long"},
            ),))
        return original(node, **kwargs)

    monkeypatch.setattr(cache, "_beliefkv_causal_full_donor", check)
    runtime.dispatch_idle_prepare()
    assert len(calls) == 2
    assert hot.component_data[0].host_value is not None
    assert allocator.available_size() == host_slots["free"] == 0
    assert not writes


def test_prefetched_device_owner_keeps_its_host_mirror(mirror_runtime):
    runtime, cache, allocator, _cold, hot, writes, host_slots = mirror_runtime
    runtime._prefetch_service_leases["protected"] = NS(node_id=hot.id)
    try:
        runtime.dispatch_idle_prepare()
        assert hot.component_data[0].host_value is not None
        assert allocator.available_size() == host_slots["free"] == 0
        assert not writes
    finally:
        runtime._prefetch_service_leases.pop("protected")


def test_insufficient_total_mirrors_drop_no_partial_set(mirror_runtime, monkeypatch):
    from sglang.srt.mem_cache import unified_radix_cache as native

    runtime, cache, allocator, _cold, hot, writes, host_slots = mirror_runtime
    monkeypatch.setattr(native, "BELIEFKV_IDLE_FULL_PREPARE_MAX_BYTES", 6 * 64)
    runtime.dispatch_idle_prepare()
    assert hot.component_data[0].host_value is not None
    assert allocator.available_size() == host_slots["free"] == 0
    assert runtime.counts["idle_host_mirror_recycle_insufficient_mirrors"] == 1
    assert not writes
    cache.host_pool_group.free.assert_not_called()


def test_new_dma_dependency_at_revalidation_keeps_host_capacity(mirror_runtime, monkeypatch):
    runtime, cache, allocator, _cold, hot, writes, host_slots = mirror_runtime
    controller = runtime._idle_host_mirror_recycler
    original = controller._live_mirror
    calls = []

    def check(cache, node, created):
        calls.append(node.id)
        if len(calls) == 2:
            hot.load_back_pending_id = hot.id
        return original(cache, node, created)

    monkeypatch.setattr(controller, "_live_mirror", check)
    runtime.dispatch_idle_prepare()
    assert calls == [hot.id, hot.id]
    assert hot.component_data[0].host_value is not None
    assert allocator.available_size() == host_slots["free"] == 0
    assert not writes


def test_failed_host_release_cannot_create_a_backup_capacity_credit(mirror_runtime):
    runtime, cache, allocator, cold, hot, writes, host_slots = mirror_runtime
    cache.host_pool_group.free.side_effect = None
    runtime.dispatch_idle_prepare()
    assert hot.component_data[0].host_value is None
    assert cold.component_data[0].host_value is None
    assert allocator.available_size() == host_slots["free"] == 0
    assert runtime.counts["idle_host_mirror_recycle_unmet_budget"] == 1
    assert not writes


def test_same_blocked_attempt_keeps_one_second_recycle_backoff(mirror_runtime):
    runtime, cache, _allocator, _cold, hot, writes, _host_slots = mirror_runtime
    hot.component_data[0].host_lock_ref = 1
    runtime.dispatch_idle_prepare()
    assert runtime.counts["idle_host_mirror_recycle_checks"] == 1
    hot.component_data[0].host_lock_ref = 0
    runtime._idle_prepare_next_ms = 0.
    runtime.dispatch_idle_prepare()
    assert runtime.counts["idle_host_mirror_recycle_checks"] == 1
    assert not writes
    runtime._idle_prepare_next_ms = runtime._idle_host_mirror_recycler.next_ms = 0.
    runtime.dispatch_idle_prepare()
    assert runtime.counts["idle_host_mirror_recycle_checks"] == 2
    assert len(writes) == 1


def test_dirty_probe_is_bounded_and_rotates_past_a_rejected_window(mirror_runtime):
    runtime, cache, _allocator, cold, _hot, writes, _host_slots = mirror_runtime
    stale = [(1000 + i, NS(
        id=1000 + i, creation_time=0.,
        component_data={0: NS(value=[1], host_value=None)},
    )) for i in range(32)]
    cache._beliefkv_unbacked_full = OrderedDict(
        [*stale, (cold.id, cold)],
    )
    runtime.dispatch_idle_prepare()
    assert runtime.counts["idle_host_mirror_recycle_cold_checks"] == 32
    assert not writes
    runtime._idle_prepare_next_ms = runtime._idle_host_mirror_recycler.next_ms = 0.
    runtime.dispatch_idle_prepare()
    assert runtime.counts["idle_host_mirror_recycle_cold_checks"] == 33
    assert len(writes) == 1


@pytest.mark.parametrize("mode", MODES)
def test_internal_controls_clear_host_mirror_recycling(mode):
    settings = arm_environment({"IDLE_HOST_MIRROR_RECYCLE": "1"}, mode)
    assert settings["BELIEFKV_IDLE_HOST_MIRROR_RECYCLE"] == str(int(mode == "full"))


@pytest.mark.parametrize("method", (
    "thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph",
))
def test_paper_controls_clear_host_mirror_recycling(method):
    assert paper_environment(
        {"IDLE_HOST_MIRROR_RECYCLE": "1"}, method,
    )["BELIEFKV_IDLE_HOST_MIRROR_RECYCLE"] == "0"


def test_declared_candidate_changes_only_the_two_new_common_flag_forms():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_tool_offload_before_admission_20261027.json").read_bytes())
    candidate = json.loads((root / "configs/migration/predictive_idle_host_mirror_recycle_20261027.json").read_bytes())
    assert {
        key: value for key, value in candidate["common_environment"].items()
        if key not in ("IDLE_HOST_MIRROR_RECYCLE", "BELIEFKV_IDLE_HOST_MIRROR_RECYCLE")
    } == base["common_environment"]
    for key in ("source_registration", "tool_timing_overlay", "control_reuse", "native_reference"):
        assert candidate[key] == base[key]


@pytest.mark.parametrize("settings", (
    {"BELIEFKV_IDLE_HOST_MIRROR_RECYCLE": "yes"},
    {"BELIEFKV_IDLE_HOST_MIRROR_RECYCLE": "1", "BELIEFKV_IDLE_PREPARE_POLICY": "all"},
    {"BELIEFKV_IDLE_HOST_MIRROR_RECYCLE": "1", "BELIEFKV_ENABLE_BACKGROUND_PREPARE": "0"},
    {"BELIEFKV_IDLE_HOST_MIRROR_RECYCLE": "1", "BELIEFKV_INTERNAL_COMPARISON_MODE": "kv_only"},
))
def test_runtime_rejects_unsupported_recycling_profiles(monkeypatch, settings):
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="Host mirror recycling"):
        NativeAdmissionRuntime()


@pytest.mark.parametrize("method,observed", (
    ("full", False), ("full", None), ("thunderagent_acting_decay", True),
))
def test_final_observer_requires_actual_recycling_isolation(monkeypatch, tmp_path, method, observed):
    plan = {
        "reference_environment": {"IDLE_HOST_MIRROR_RECYCLE": "1"},
        "source_plan": {"requests": 0},
    }
    snapshot = {"paper_baseline": {"counts": {"registered": 0, "completed": 0}}}
    if observed is not None:
        snapshot["idle_host_mirror_recycle"] = observed
    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda *_a, **_kw: None)
    monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda *_a: snapshot)
    with pytest.raises(ValueError, match="effective idle Host mirror recycling"):
        comparison.observed_configuration(plan, method, tmp_path, final=True)
