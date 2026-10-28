"""Actual offload may replace redundant mirrors; optional backup cannot."""

from dataclasses import replace
import time
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.host_demand_capacity import HostDemandCapacity
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_idle_host_mirror_recycle import mirror_runtime
from tests.test_tool_policy_adaptation import demand_runtime


def test_real_native_host_demand_reclaims_a_pinned_duplicate_without_freeing_device(mirror_runtime):
    runtime, cache, allocator, cold, hot, writes, slots = mirror_runtime
    manager = HostDemandCapacity(runtime, 128)
    before = allocator.available_size()
    device, cold_device = hot.component_data[0].value, cold.component_data[0].value
    assert manager.ensure(4, protected=(cold.id,))
    assert slots["free"] == 8 and allocator.available_size() == before
    assert hot.component_data[0].value is device
    assert cold.component_data[0].value is cold_device
    assert hot.component_data[0].host_value is None
    assert hot.id in cache._beliefkv_unbacked_full
    assert not writes


@pytest.mark.parametrize("reason", ("host_only", "host_lock", "d2h", "h2d", "unpinned", "partial", "protected"))
def test_actual_host_reclaim_preserves_unique_and_inflight_copies(mirror_runtime, reason):
    runtime, cache, allocator, cold, hot, writes, slots = mirror_runtime
    data = hot.component_data[0]
    if reason == "host_only":
        data.value = None
    elif reason == "host_lock":
        data.host_lock_ref = 1
    elif reason == "d2h":
        hot.write_through_pending_id = 99
    elif reason == "h2d":
        hot.load_back_pending_id = 99
    elif reason == "unpinned":
        data.lock_ref = 0
    elif reason == "partial":
        data.host_value = data.host_value[:4]
    original = data.host_value
    manager = HostDemandCapacity(runtime, 128)
    assert not manager.ensure(4, protected=(hot.id,) if reason == "protected" else ())
    assert data.host_value is original and slots["free"] == 0
    assert allocator.available_size() == 0 and not writes


def test_optional_backup_preserves_reserve_without_reclaiming_mirrors(mirror_runtime):
    runtime, cache, _, cold, hot, writes, slots = mirror_runtime
    manager = HostDemandCapacity(runtime, 4 * 64)
    assert manager.optional_batch(8 * 64) == 0
    assert hot.component_data[0].host_value is not None
    slots["free"] = 10
    assert manager.optional_batch(8 * 64) == 6 * 64
    assert not writes
    assert manager.ensure(8, protected=(cold.id,))
    assert manager.reserve() == 8 * 64
    assert manager.optional_batch(8 * 64) == 2 * 64
    manager.demand_expires_ms = 0
    assert manager.reserve() == 4 * 64


def test_idle_dispatch_cannot_consume_demand_reserve_to_recycle_a_mirror(mirror_runtime):
    runtime, _, _, _, hot, writes, slots = mirror_runtime
    runtime._host_demand_capacity = HostDemandCapacity(runtime, 8 * 64)
    slots["free"] = 8
    runtime.dispatch_idle_prepare()
    assert not writes
    assert slots["free"] == 8
    assert hot.component_data[0].host_value is not None
    assert runtime.counts["idle_prepare_host_demand_reserve_skips"] == 1


def test_spawn_backup_batch_cannot_consume_demand_reserve(mirror_runtime):
    runtime, cache, _, _, _, _, slots = mirror_runtime
    runtime._host_demand_capacity = HostDemandCapacity(runtime, 4 * 64)
    slots["free"] = 10
    key = runtime.context_sessions["ctx-tool"]
    runtime._spawn_full_prepares["ctx-tool"] = key
    cache.prepare_beliefkv_spawn_full = Mock(return_value={
        "issued": (), "complete": False, "reason": "copy_blocked",
    })
    runtime.dispatch_spawn_prepare()
    assert cache.prepare_beliefkv_spawn_full.call_args.kwargs["max_batch_bytes"] == 6 * 64


@pytest.mark.parametrize("mode", ("scheduler_only", "native_matched", "thunderagent_acting_decay"))
def test_baselines_do_not_inherit_host_management_or_relaxed_threshold(mode):
    common = {"BELIEFKV_HOST_DEMAND_RESERVE_BYTES": "2147483648", "BELIEFKV_TOOL_OFFLOAD_MIN_SURVIVAL": ".75"}
    settings = paper_environment(common, mode) if mode.startswith("thunder") else arm_environment(common, mode)
    assert settings["BELIEFKV_HOST_DEMAND_RESERVE_BYTES"] == "0"
    assert settings["BELIEFKV_TOOL_OFFLOAD_MIN_SURVIVAL"] == ".9"


@pytest.mark.parametrize("value", ("0", "1", "nan", "inf", "-.1", "bogus"))
def test_invalid_survival_threshold_is_rejected(monkeypatch, value):
    monkeypatch.setenv("BELIEFKV_TOOL_OFFLOAD_MIN_SURVIVAL", value)
    with pytest.raises(ValueError):
        NativeAdmissionRuntime()


@pytest.mark.parametrize("survival,allowed", ((.9, False), (.75, True)))
def test_same_real_capacity_demand_obeys_declared_survival(demand_runtime, survival, allowed):
    runtime, controller, _, allocator, donor, _, _ = demand_runtime
    runtime.tool_offload_min_survival = survival
    hint = runtime.tool_wait_hints["ctx-tool"]
    runtime.tool_wait_hints["ctx-tool"] = replace(
        hint, release_cdf=((100., .2), (100_000., 1.)),
        issued_monotonic_ms=time.monotonic() * 1000.,
        residual_support_detail="online_offline_prior_command",
    )
    controller.dispatch()
    assert (donor.component_data[0].value is None) is allowed, (
        dict(runtime.counts), controller.action_detail,
    )
    assert allocator.available_size() == (36 if allowed else 4)
    if allowed:
        assert controller.action_detail["minimum_remaining_survival"] == .75


def test_rejected_cost_never_reclaims_host_mirrors(demand_runtime):
    runtime, controller, _, _, donor, request, _ = demand_runtime
    runtime._host_demand_capacity = Mock()
    controller._worth_offloading = Mock(return_value=False)
    donor.component_data[0].host_value = None
    step, _ = runtime._refreshed_shadow_backup_selection(context_id="ctx-tool")
    assert not controller._prepare_allowed(step, Mock(host_full_free_tokens=0))
    runtime._host_demand_capacity.ensure.assert_not_called()
