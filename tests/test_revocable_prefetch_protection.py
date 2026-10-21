"""JOIN ordering and revocable post-ACK locks, including native CPU reclaim."""

from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import PhysicalActionCompleted, PrefetchLoadStep
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime, _PrefetchServiceLease
from beliefkv.experiments.internal_comparison import arm_environment, validate_runtime
from tests.test_internal_comparison import join_fixture
from tests.test_critical_remaining_capacity import capacity_fixture
from tests.test_tool_predictive_transfers import (
    locked_runtime, req, select, submit_restored_request,
)
from tests.test_waiting_full_tail_backup import native_cache, waiting_tail


@pytest.mark.parametrize("order", ("parent_first", "parent_last"))
@pytest.mark.parametrize("aged", (False, True))
@pytest.mark.parametrize("restore_quota", (0, 3, 4))
def test_full_and_join_only_choose_the_same_join_turn(
    monkeypatch, order, aged, restore_quota,
):
    outcomes = []
    for mode in ("full", "join_priority_only"):
        runtime, requests = join_fixture(monkeypatch, mode=mode)
        normal, parent = requests
        runtime._prefetch_priority_normal_admissions = restore_quota
        if aged:
            runtime._visible_since[normal.rid] = time.monotonic() - 11.
        if mode == "full":
            key = runtime.visible[normal.rid]
            runtime._prefetch_service_leases["restore"] = _PrefetchServiceLease(
                replace(key, request_id="previous"), "restore", 1, 1., "tool_wait",
                None, time.monotonic(), time.monotonic() + 10., (),
                demand_ready=True,
            )
        requests = [parent, normal] if order == "parent_first" else [normal, parent]
        try:
            with patch.object(
                runtime, "_prefill_causal_ranks", side_effect=AssertionError("JOIN fast path"),
            ), patch.object(
                runtime, "_read_request_reentry", side_effect=AssertionError("no restore scan"),
            ):
                plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
            outcomes.append((
                [key.invocation_id for key in plan.prioritized],
                runtime._join_priority_promoted,
            ))
            assert outcomes[-1][0][0] == "parent"
        finally:
            runtime.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("source", ("tool_wait", "execution_handoff", "join_ticket", "advisory"))
@pytest.mark.parametrize("barrier", ("causal_class", "aged_head"))
def test_restore_status_never_crosses_a_class_or_aged_barrier(source, barrier):
    runtime, _, _, action, _, _ = locked_runtime()
    restored = submit_restored_request(runtime)
    other = req("ordinary")
    runtime.register_visible_request(other)
    runtime.enable_resident_first = False
    lease = runtime._prefetch_service_leases[action.command_id]
    if source == "advisory":
        runtime._prefetch_admission_hints[restored.rid] = runtime.visible[restored.rid]
        runtime._release_prefetch_service_lease(action.command_id, "fixture")
    else:
        runtime._prefetch_service_leases[action.command_id] = replace(lease, source=source)
    if barrier == "aged_head":
        runtime._visible_since[other.rid] = time.monotonic() - 11.
    try:
        with patch.object(runtime, "_causal_rank", side_effect=lambda key, index, ranks: (
            int(barrier == "causal_class" and key.request_id == restored.rid), 0, index,
        )), patch.object(
            runtime, "_read_request_reentry", side_effect=AssertionError("no cross-class scan"),
        ):
            assert select(runtime, [other, restored]).candidates[0] is other
        assert runtime._prefetch_priority_promoted is None
    finally:
        runtime.close()


def test_pressure_revokes_only_device_protection_and_keeps_first_service_tracking():
    runtime, _, _, action, receipt, _ = locked_runtime()
    tree = runtime._native_cache.tree_core
    host_receipt = NS(node_id=1)
    tree.dec_host_lock_ref = Mock()
    lease = runtime._prefetch_service_leases[action.command_id]
    runtime._prefetch_service_leases[action.command_id] = replace(
        lease, host_lock_params=host_receipt,
    )
    try:
        runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
        runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
        tracked = runtime._prefetch_service_leases[action.command_id]
        assert tracked.lock_params is None and tracked.protected_bytes == 0
        assert tracked.host_lock_params is host_receipt
        assert tracked.acknowledged_at == lease.acknowledged_at
        assert tracked.pool_bytes == lease.pool_bytes
        tree.dec_lock_ref.assert_called_once_with(1, receipt)
        tree.dec_host_lock_ref.assert_not_called()
        restored = submit_restored_request(runtime)
        runtime.on_batch_completed(NS(reqs=[restored]))
        assert not runtime._prefetch_service_leases
        tree.dec_lock_ref.assert_called_once_with(1, receipt)
        tree.dec_host_lock_ref.assert_called_once_with(1, host_receipt)
        assert runtime.counts["prefetch_residency_released:first_gpu_service"] == 1
        assert runtime.counts["prefetch_protection_revoked:allocation_pressure"] == 1
    finally:
        runtime.close()


def test_uncertain_pressure_unlock_retains_its_receipt_and_disables_physical_actions():
    runtime, _, node, action, receipt, _ = locked_runtime()
    tree = runtime._native_cache.tree_core
    tree.dec_lock_ref.side_effect = RuntimeError("uncertain native unlock")
    original = runtime._prefetch_service_leases[action.command_id]
    try:
        for _ in range(2):
            runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
        assert runtime.physical_disabled
        assert runtime._prefetch_service_leases[action.command_id] is original
        assert node.component_data[0].value is not None
        tree.dec_lock_ref.assert_called_once_with(1, receipt)
        assert not runtime.counts["prefetch_protection_revoked:allocation_pressure"]
    finally:
        runtime.close()


def test_disabled_post_ack_protection_skips_closure_locks_but_tracks_service(monkeypatch):
    monkeypatch.setenv("BELIEFKV_PREFETCH_RESIDENCY_PROTECTION", "0")
    with patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        side_effect=AssertionError("disabled protection must not inspect closure"),
    ):
        runtime, hint, _, action, _, _ = locked_runtime()
        cache = runtime._native_cache
        cache.retain_beliefkv_prefetch_host = Mock(side_effect=AssertionError("no extra Host lock"))
        other = PhysicalActionCompleted(
            "next-extent", "PREFETCH_GPU", hint.key.context_id, hint.key.context_epoch,
            (1,), (("kv", 20),), 20,
        )
        runtime._prefetch_steps[other.command_id] = (
            PrefetchLoadStep(hint.key, 1, 2, 1, 2), "tool_wait", 2.,
        )
        runtime._register_prefetch_service_lease(other)
    try:
        assert not runtime.enable_prefetch_residency_protection
        assert len(runtime._prefetch_service_leases) == 2
        assert all(lease.lock_params is None and not lease.protected_bytes
                   for lease in runtime._prefetch_service_leases.values())
        cache.tree_core.inc_lock_ref.assert_not_called()
        cache.retain_beliefkv_prefetch_host.assert_not_called()
        restored = submit_restored_request(runtime)
        runtime.on_batch_completed(NS(reqs=[restored]))
        assert not runtime._prefetch_service_leases
        assert runtime.counts["prefetch_residency_released:first_gpu_service"] == 2
        cache.tree_core.dec_lock_ref.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("free", ((0, 0), (5000, 10), (300, 2)))
def test_critical_reclaim_revokes_donor_only_after_confirmed_capacity_shortage(free):
    with capacity_fixture(free=free) as state:
        runtime, cache = state.runtime, state.cache
        donor_key = replace(state.key, context_id="donor", invocation_id="other")
        donor = _PrefetchServiceLease(
            donor_key, "donor", 3, 3., "tool_wait", None, 1000., 1001., (),
            lock_params=NS(node_id=3), protected_bytes=100,
        )
        target = replace(
            donor, command_id="target", key=replace(state.key, request_id="old"),
            node_id=1, lock_params=NS(node_id=1),
        )
        runtime._prefetch_service_leases.update(donor=donor, target=target)
        calls = []
        cache.tree_core = NS(dec_lock_ref=Mock(side_effect=lambda *_: calls.append("unlock")))
        cache.reclaim_beliefkv_handoff_capacity.side_effect = lambda **_: calls.append("reclaim") or {}
        runtime._reclaim_critical_parent_headroom()
        assert runtime._prefetch_service_leases["target"] is target
        if free == (0, 0):
            assert calls == ["unlock", "reclaim"]
            assert runtime._prefetch_service_leases["donor"].lock_params is None
        else:
            assert calls == []
            assert runtime._prefetch_service_leases["donor"] is donor


def test_revoking_our_lock_cannot_remove_an_independent_native_lock(waiting_tail):
    runtime, hint, cache, allocator, _, checkpoint, leaf = waiting_tail
    leaf.component_data[0].host_value = leaf.component_data[0].value.clone()
    cache.tree_core._update_duplicate_tracking(leaf)
    own = cache.tree_core.inc_lock_ref(leaf.id).to_dec_params()
    native = cache.tree_core.inc_lock_ref(leaf.id).to_dec_params()
    now = time.monotonic()
    runtime._prefetch_service_leases["own"] = _PrefetchServiceLease(
        hint.key, "own", leaf.id, leaf.creation_time, "tool_wait",
        hint.invocation_revision_ts_ms, now, now + 10., (("kv", 100),),
        lock_params=own, protected_bytes=100,
    )
    runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
    assert runtime._prefetch_service_leases["own"].lock_params is None
    assert leaf.component_data[0].lock_ref > 0
    before = allocator.available_size()
    runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id)
    runtime._publish_parent_pressure_candidates()
    cache.reclaim_beliefkv_handoff_capacity(full_tokens=before + 34, mamba_slots=0)
    assert allocator.available_size() == before
    assert leaf.component_data[0].value is not None
    assert checkpoint.component_data[0].value is not None
    cache.tree_core.dec_lock_ref(leaf.id, native)
    runtime._publish_parent_pressure_candidates()
    result = cache.reclaim_beliefkv_handoff_capacity(full_tokens=before + 34, mamba_slots=0)
    assert result.get(0, 0) == 34
    assert allocator.available_size() == before + 34
    assert not runtime.physical_disabled


@pytest.mark.parametrize("setting", ("0", "1"))
@pytest.mark.parametrize("mode", ("full", "kv_only", "scheduler_only", "join_priority_only"))
def test_post_ack_ablation_preserves_other_controls_and_records_effective_setting(
    monkeypatch, mode, setting,
):
    baseline = arm_environment({}, mode)
    environment = arm_environment({"PREFETCH_RESIDENCY_PROTECTION": setting}, mode)
    changed = {key for key in baseline if baseline[key] != environment[key]}
    assert changed <= {"PREFETCH_RESIDENCY_PROTECTION", "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION"}
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "0")
    runtime = NativeAdmissionRuntime()
    rows = []
    runtime._opportunity_writer = NS(record=rows.append)
    try:
        runtime._record_runtime_state()
        assert rows[-1]["prefetch_residency_protection"] is (
            setting == "1" and mode in ("full", "kv_only")
        )
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_internal_runtime_validation_rejects_an_unintended_protection_setting():
    from tests.test_internal_comparison import configure

    with pytest.MonkeyPatch.context() as monkeypatch:
        configure(monkeypatch, "scheduler_only")
        runtime = NativeAdmissionRuntime()
        runtime._opportunity_writer = Mock()
        try:
            runtime._record_runtime_state()
            state = runtime._opportunity_writer.record.call_args.args[0]
            validate_runtime("scheduler_only", state, residency_protection=False)
            state["prefetch_residency_protection"] = True
            with pytest.raises(ValueError, match="prefetch_residency_protection"):
                validate_runtime("scheduler_only", state, residency_protection=False)
        finally:
            runtime._opportunity_writer = None
            runtime.close()


def test_invalid_post_ack_protection_setting_is_rejected(monkeypatch):
    monkeypatch.setenv("BELIEFKV_PREFETCH_RESIDENCY_PROTECTION", "sometimes")
    with pytest.raises(ValueError, match="prefetch residency protection"):
        NativeAdmissionRuntime()
