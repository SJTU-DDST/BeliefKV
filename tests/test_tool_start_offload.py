"""Exercise early D2 release against the native FULL CPU tree and allocator."""

from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.tool_start_offload import ToolStartOffload
from tests.test_full_only_native_lifecycle import full_native
from tests.test_prepare_ack_allocation import native_write_start
from tests.test_reference_tool_replacement import pair
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event


def _configured():
    return {
        "AGENT_ADMISSION": "thunderagent_reference",
        "PREDICTED_TOOL_OFFLOAD": "1",
        "BELIEFKV_TOOL_OFFLOAD_POLICY": "demand_cost",
        "TOOL_OFFLOAD_BEFORE_ADMISSION": "1",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
        "AGENT_RESIDENCY_PAUSE": "1",
        "BELIEFKV_TOOL_START_OFFLOAD": "1",
        "MODEL_CACHE_LAYOUT": "full_only",
    }


@pytest.mark.parametrize("mode", MODES)
def test_early_release_is_qualified_only_in_full(mode):
    assert arm_environment(_configured(), mode)["BELIEFKV_TOOL_START_OFFLOAD"] == (
        "1" if mode == "full" else "0"
    )


def test_reference_residency_pause_is_required(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="tool-start offload"):
        arm_environment({**_configured(), "AGENT_RESIDENCY_PAUSE": "0"}, "full")
    for name, value in arm_environment(_configured(), "full").items():
        monkeypatch.setenv(name, value)
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "early.sock"))
    try:
        assert isinstance(runtime._tool_start_offload, ToolStartOffload)
        assert runtime._tool_start_offload.demand_offload is runtime._predicted_tool_offload
    finally:
        runtime.close()


def _early(parts):
    runtime, admission, demand, adder = pair(parts)
    runtime._tool_start_offload = early = ToolStartOffload(runtime, demand)
    demand.replacement.next_ms = demand.next_ms = float("inf")
    runtime.tool_offload_min_survival = .75
    hint = runtime.tool_wait_hints["ctx-tool"]
    early.note_hint(hint)
    return runtime, admission, early, adder, hint


def _safe_point(runtime, adder, *, waiting=(), running=(object(),)):
    batch = NS(reqs=running)
    runtime._observe_native_admission_capacity(running_batch=batch, adder=adder)
    runtime.dispatch_after_forward(waiting, running_batch=batch)


def _return(runtime):
    runtime.on_events((event(
        10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
    ),))


def _bind_backup_receipts(runtime, cache, donor):
    native_write_start(cache)
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    cache.cache_controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    cache.cache_controller._transfer_num_bytes = lambda op: 64 * len(op.device_indices)
    cache.host_pool_group.alloc = Mock(side_effect=AssertionError("stand-in owns Host indices"))
    cache.host_pool_group.free = Mock()
    cache.on_hicache_transfer_commit = runtime.on_native_transfer_commit


def test_backed_tool_start_releases_native_pages_before_a_consumer_is_waiting(demand_runtime):
    runtime, admission, early, adder, hint = _early(demand_runtime)
    _, _, _, allocator, donor, request, _ = demand_runtime
    admission._dispatch_pending.clear()
    _safe_point(runtime, adder)
    assert allocator.available_size() == 36
    assert donor.component_data[0].value is None
    assert donor.component_data[0].host_value is not None
    assert not admission.policy.programs["tool"].active
    assert runtime.counts["tool_start_offload_releases"] == 1
    assert admission.counts["tool_event_replacement_grants"] == 0
    assert not admission.allows(runtime.visible[request.rid])

    early.note_hint(replace(hint, expires_monotonic_ms=hint.expires_monotonic_ms + 100))
    _safe_point(runtime, adder)
    assert runtime.counts["tool_start_offload_probes"] == 1


def test_released_capacity_is_visible_to_a_ready_request_at_the_same_boundary(demand_runtime):
    runtime, admission, _, adder, _ = _early(demand_runtime)
    _, _, _, allocator, _, request, _ = demand_runtime
    running = NS(reqs=[])
    assert not admission.allows(runtime.visible[request.rid])
    plan = runtime.plan_native_prefill([request], running_batch=running, adder=adder)
    assert allocator.available_size() == 36
    assert admission.allows(runtime.visible[request.rid])
    assert select_native_prefill_candidates(
        [request], plan=plan, current_semantic_revision=runtime.semantic_revision,
    ).candidates == (request,)


def test_native_completion_rearms_a_tool_start_observed_before_native_unpin(demand_runtime):
    runtime, admission, early, adder, hint = _early(demand_runtime)
    item = admission.policy.programs["tool"]
    item.pending = True
    _safe_point(runtime, adder)
    assert item.active and not early.pending
    item.pending = False
    early.completed((hint.key.request_id,))
    _safe_point(runtime, adder)
    assert not item.active
    assert runtime.counts["tool_start_offload_native_completion_rearms"] == 1


@pytest.mark.parametrize("scenario", ("returned", "short", "ample", "shared", "locked", "no_demand"))
def test_early_release_requires_a_live_affordable_private_extent(demand_runtime, scenario):
    runtime, admission, _early_controller, adder, hint = _early(demand_runtime)
    _, _, _, allocator, donor, _, _ = demand_runtime
    if scenario == "returned":
        _return(runtime)
    elif scenario == "short":
        runtime.tool_wait_hints["ctx-tool"] = replace(
            hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
        )
    elif scenario == "ample":
        adder.rem_input_tokens = adder.rem_chunk_tokens = 1
        adder.prefill_max_requests = 1
    elif scenario == "shared":
        donor.component_data[0].session_ids.add("another-session")
    elif scenario == "locked":
        donor.component_data[0].lock_ref += 1
    _safe_point(runtime, adder, running=() if scenario == "no_demand" else (object(),))
    assert allocator.available_size() == 4
    assert admission.policy.programs["tool"].active
    assert not runtime.counts["tool_start_offload_releases"]


@pytest.mark.parametrize("return_during_backup", (False, True))
@pytest.mark.parametrize("split", (False, True))
def test_early_backup_releases_only_after_ack_and_same_tool_wait(
    demand_runtime, return_during_backup, split,
):
    runtime, admission, early, adder, _ = _early(demand_runtime)
    _, _, cache, allocator, donor, _, _ = demand_runtime
    _bind_backup_receipts(runtime, cache, donor)
    parent = None
    if split:
        parent, action = cache.tree_core._split_node(donor.key, donor, 30)
        assert action is None

    _safe_point(runtime, adder)
    assert early.backup is not None, str(dict(runtime.counts))
    assert all(command.startswith("beliefkv-tool-start-") for command in early.backup[1])
    assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1 + split
    _safe_point(runtime, adder)
    assert admission.policy.programs["tool"].active
    assert allocator.available_size() == 4
    if return_during_backup:
        _return(runtime)
    cache.writing_check(finish_count=1)
    _safe_point(runtime, adder)
    assert not runtime.physical_ledger.pending_count
    assert early.backup is None
    assert donor.component_data[0].host_value is not None
    if parent is not None:
        assert parent.component_data[0].host_value is not None
        assert (parent.component_data[0].value is not None) == return_during_backup
    assert allocator.available_size() == (4 if return_during_backup else 36)
    assert admission.policy.programs["tool"].active == return_during_backup


def test_optional_action_does_not_demote_only_a_thin_tail_on_shared_prefix(demand_runtime):
    runtime, admission, _, adder, _ = _early(demand_runtime)
    _, _, cache, allocator, donor, _, _ = demand_runtime
    parent, action = cache.tree_core._split_node(donor.key, donor, 30)
    assert action is None
    parent.component_data[0].session_ref += 1
    parent.component_data[0].session_ids = {"s", "peer"}
    _safe_point(runtime, adder)
    assert allocator.available_size() == 4
    assert runtime.counts["tool_start_offload_no_useful_private_tail"] == 1
    assert admission.policy.programs["tool"].active


def test_actual_small_round_trip_can_fit_even_if_old_one_second_filter_refuses(demand_runtime):
    runtime, admission, _, adder, hint = _early(demand_runtime)
    _, _, cache, allocator, donor, _, _ = demand_runtime
    _bind_backup_receipts(runtime, cache, donor)
    runtime.tool_wait_hints["ctx-tool"] = replace(
        hint, issued_monotonic_ms=time.monotonic() * 1000., release_cdf=((200., 0.), (700., 1.)),
    )
    assert not runtime._predicted_long_tool_wait(hint.key)
    _safe_point(runtime, adder)
    assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1
    cache.writing_check(finish_count=1)
    _safe_point(runtime, adder)
    assert not admission.policy.programs["tool"].active
    assert allocator.available_size() == 36


def test_optional_backup_respects_host_demand_reserve(demand_runtime):
    runtime, admission, _, adder, _ = _early(demand_runtime)
    _, _, cache, allocator, donor, _, _ = demand_runtime
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    runtime._host_demand_capacity = NS(optional_batch=Mock(return_value=0))
    _safe_point(runtime, adder)
    assert runtime.counts["tool_start_offload_no_useful_private_tail"] == 1
    assert not runtime.physical_ledger.pending_count
    assert allocator.available_size() == 4
    assert admission.policy.programs["tool"].active


def test_late_native_boundary_does_not_use_a_shortened_or_expired_forecast(demand_runtime):
    runtime, admission, _, adder, hint = _early(demand_runtime)
    runtime.tool_wait_hints["ctx-tool"] = replace(
        hint, expires_monotonic_ms=time.monotonic() * 1000. - 1,
    )
    _safe_point(runtime, adder)
    assert admission.policy.programs["tool"].active
    assert runtime.counts["tool_start_offload_stale_or_protected"] == 1


@pytest.mark.parametrize("scenario", ("fits", "undersized", "not_backed", "returned"))
def test_masked_pressure_offer_releases_only_at_a_coverable_native_shortfall(
    demand_runtime, scenario,
):
    runtime, admission, early, adder, _ = _early(demand_runtime)
    _, _, cache, allocator, donor, _, _ = demand_runtime
    cache._beliefkv_tool_start_settled_shortfall = None
    admission.policy.capacity_tokens = 128
    _safe_point(runtime, adder)
    callback = cache._beliefkv_tool_start_settled_shortfall
    assert callback is not None and allocator.available_size() == 4
    assert runtime.counts["tool_start_offload_settled_alloc_offers"] == 1
    assert admission.policy.programs["tool"].active
    if scenario == "not_backed":
        donor.component_data[0].host_value = None
        cache.tree_core._update_duplicate_tracking(donor)
    elif scenario == "returned":
        _return(runtime)
    callback(33 if scenario == "undersized" else 12)
    assert early.allocation_offer is None
    assert cache._beliefkv_tool_start_settled_shortfall is None
    assert allocator.available_size() == (36 if scenario == "fits" else 4)
    assert admission.policy.programs["tool"].active == (scenario != "fits")
    assert runtime.counts["tool_start_offload_settled_alloc_releases"] == int(scenario == "fits")
    assert not runtime.physical_ledger.pending_count
    callback(12)
    assert runtime.counts["tool_start_offload_settled_alloc_probes"] == 1


def test_forecast_preserves_native_allocation_without_an_unbacked_victim_write(demand_runtime):
    from sglang.srt.mem_cache.allocation import alloc_token_slots

    runtime, admission, early, adder, _ = _early(demand_runtime)
    _, _, cache, allocator, donor, _, writes = demand_runtime
    if not hasattr(cache, "_beliefkv_tool_start_settled_shortfall"):
        pytest.skip("requires the D2 native allocator patch")
    native_write_start(cache)
    cache.tree_core.set_hicache_enabled()
    admission.policy.capacity_tokens = 128
    victim = next(
        node for node in cache.tree_core.evictable_device_leaves
        if node.component_data[0].session_ids == {"hot"}
    )
    cache.release_radix_session("hot")
    _safe_point(runtime, adder)
    assert early.allocation_offer is not None
    assert allocator.available_size() == 4 and not writes

    allocated = alloc_token_slots(cache, 8)

    assert len(allocated) == 8
    assert victim.component_data[0].value is not None
    assert victim.component_data[0].host_value is None
    assert donor.component_data[0].value is None
    assert donor.component_data[0].host_value is not None
    assert not writes and not runtime.physical_ledger.pending_count
    assert runtime.counts["tool_start_offload_settled_alloc_releases"] == 1
    assert early.allocation_offer is None
    allocator.free(allocated)
