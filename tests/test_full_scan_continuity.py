"""Interleaved state pressure must not starve a bounded FULL capacity scan."""

from unittest.mock import Mock

import pytest

from tests.test_waiting_full_tail_backup import native_cache


def scan_fixture(native_cache):
    from test_beliefkv_backed_full_scan import backed_victim

    cache, allocator, request_pool = native_cache
    victim, nodes, leaves = backed_victim(cache)
    cache.beliefkv_cold_full_owner_validator = Mock(return_value=False)
    return cache, allocator, request_pool, victim, nodes, leaves


def full_retry(cache, target):
    return cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=target, mamba_slots=0, max_nodes=4,
        collect_diagnostics=True, collect_cold_full_candidates=True,
    )


def state_only_retry(cache, allocator, request_pool):
    return cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=allocator.available_size(),
        mamba_slots=request_pool.mamba_allocator.available_size() + 1,
        max_nodes=4, collect_diagnostics=True,
    )


@pytest.mark.parametrize("native_cache", (32,), indirect=True)
def test_interleaved_state_reclaims_preserve_progress_to_a_backed_full_victim(native_cache):
    cache, allocator, request_pool, victim, nodes, leaves = scan_fixture(native_cache)
    available = allocator.available_size()
    host = victim.component_data[0].host_value
    originals = [node.component_data[0].value for node in nodes[:-1]]
    scanned = []
    for attempt in range(4):
        result = full_retry(cache, available + 2)
        diagnosis = cache.beliefkv_handoff_last_capacity
        scanned.append(diagnosis["native_full_scanned"])
        assert diagnosis["native_full_scanned"] <= 4
        assert diagnosis["transaction_attempts"] <= 4
        if attempt < 3:
            assert not result
            assert diagnosis["native_full_scan_truncated"]
            assert allocator.available_size() == available
            state_only_retry(cache, allocator, request_pool)
            assert allocator.available_size() == available
        else:
            assert result.get(0) == 2
    assert scanned == [4, 4, 4, 1]
    assert allocator.available_size() == available + 2
    assert victim.component_data[0].value is None
    assert victim.component_data[0].host_value is host
    assert all(node.component_data[0].value is original
               for node, original in zip(nodes[:-1], originals))
    assert leaves.iterations == 1
    cache._execute_and_commit_kv_backup.assert_not_called()


@pytest.mark.parametrize("native_cache", (32,), indirect=True)
@pytest.mark.parametrize("change", ("host_lost", "device_lock", "session", "d2h", "h2d"))
def test_resumed_scan_still_checks_the_current_victim(native_cache, change):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, request_pool, victim, _, leaves = scan_fixture(native_cache)
    available = allocator.available_size()
    assert not full_retry(cache, available + 2)
    state_only_retry(cache, allocator, request_pool)
    full = victim.component_data[0]
    original = full.value
    lock = None
    if change == "host_lost":
        full.host_value = None
        cache.tree_core._update_duplicate_tracking(victim)
    elif change == "device_lock":
        lock = cache.inc_lock_ref(victim.id).to_dec_params()
    elif change == "session":
        insert_session(cache, [112, 1012], "new-live-owner")
    elif change == "d2h":
        victim.write_through_pending_id = victim.id
    else:
        victim.load_back_pending_id = victim.id
    try:
        for _ in range(3):
            assert not full_retry(cache, available + 2)
            diagnosis = cache.beliefkv_handoff_last_capacity
            assert diagnosis["native_full_scanned"] <= 4
            assert diagnosis["transaction_attempts"] <= 4
            assert allocator.available_size() == available
            assert full.value is original
        assert leaves.iterations == 1
        cache._execute_and_commit_kv_backup.assert_not_called()
    finally:
        if lock is not None:
            cache.dec_lock_ref(victim.id, lock)
