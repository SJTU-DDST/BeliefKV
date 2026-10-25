from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from tests.test_frontier_cold_reclaim import cold_frontier_fixture


@pytest.mark.parametrize("quota", (0, 4))
@pytest.mark.parametrize("priority_credit", (0, 1, 3, 4))
def test_selected_preload_can_run_during_priority_cooldown_without_spending_priority(
    quota, priority_credit,
):
    runtime, key, request, cache, _, actions, _, _, _ = cold_frontier_fixture(free_tokens=513)
    runtime.frontier_restore_ordinary_quota = quota
    runtime._prefetch_priority_normal_admissions = priority_credit
    try:
        before = runtime.plan_native_prefill([request], running_batch=NS(reqs=[]), adder=None)
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        expected = priority_credit >= quota
        assert bool(actions) is expected
        assert bool(runtime.physical_ledger.pending_count) is expected
        assert runtime._prefetch_priority_normal_admissions == priority_credit
        assert runtime.counts["native_admitted"] == 0
        assert runtime._execution_handoff is None
        if expected:
            assert runtime._frontier_restore_consumer.key == key
            assert before.prioritized[0] == key
            assert runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU") > 0
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
    finally:
        runtime.close()


def test_removing_preload_cooldown_keeps_one_consumer_and_no_duplicate_dma():
    runtime, key, request, _, _, actions, _, _, _ = cold_frontier_fixture(free_tokens=513)
    runtime.frontier_restore_ordinary_quota = 0
    runtime._prefetch_priority_normal_admissions = 0
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        before = len(actions), runtime.physical_ledger.pending_count
        assert before[0] > 0
        runtime._wait_prefetch_opportunity = Mock(side_effect=AssertionError("duplicate planning"))
        for _ in range(128):
            runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        assert (len(actions), runtime.physical_ledger.pending_count) == before
        assert runtime._frontier_restore_consumer.key == key
        assert runtime._prefetch_priority_normal_admissions == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("guard", ("join", "eos", "no_slot", "no_capacity"))
def test_preload_without_cooldown_keeps_critical_and_native_capacity_guards(guard):
    runtime, key, request, cache, _, actions, _, _, _ = cold_frontier_fixture(
        free_tokens=1, reclaimed_tokens=0,
    )
    runtime.frontier_restore_ordinary_quota = 0
    runtime._prefetch_priority_normal_admissions = 0
    if guard == "join":
        runtime._confirmed_join_tickets["join"] = NS(key=key)
    elif guard == "eos":
        runtime._eos_prefetch_keys["child"] = key
    elif guard == "no_slot":
        runtime._native_prefill_slots = 0
        cache.req_to_token_pool.available_size = lambda: 0
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        assert not actions and not runtime.physical_ledger.pending_count
        assert runtime._frontier_restore_consumer is None
        assert runtime._prefetch_priority_normal_admissions == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("mode,expected", (
    ("full", "0"), ("kv_only", "0"), ("scheduler_only", "4"),
    ("join_priority_only", "4"), ("native_matched", "4"),
))
def test_preload_quota_reaches_only_arms_with_real_restore(mode, expected):
    env = arm_environment({
        "EXECUTION_FRONTIER_RESTORE": "1", "FRONTIER_RESTORE_ORDINARY_QUOTA": "0",
    }, mode)
    assert env["BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA"] == expected


@pytest.mark.parametrize("quota", ("", "-1", "1", "5", "nan"))
def test_unsupported_preload_quota_is_rejected_before_runtime_start(monkeypatch, quota):
    with pytest.raises(ValueError):
        arm_environment({"FRONTIER_RESTORE_ORDINARY_QUOTA": quota}, "full")
    # Configure the ordinary fixture first; constructor validation is exercised
    # directly after overriding its qualified environment below.
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
    from tests.test_internal_comparison import configure

    configure(monkeypatch, "full")
    monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "0")
    monkeypatch.setenv("BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA", quota)
    with pytest.raises(ValueError):
        NativeAdmissionRuntime()
