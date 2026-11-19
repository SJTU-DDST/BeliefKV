"""Native allocator and ACK checks for the demand restore capacity guard."""

from collections import Counter
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from beliefkv.runtime.demand_restore_capacity import ensure_device_capacity_preserving_live_kv
from beliefkv.runtime.subagent_materialization import SubagentMaterialization
from tests.test_full_only_native_lifecycle import full_native


def bind_host_slots(cache, writes, capacity, monkeypatch):
    """Bind the native CPU Host allocator before the fixture's first backup."""
    from sglang.srt.mem_cache.pool_host.mha import MHATokenToKVPoolHost

    group = cache.host_pool_group
    pool = object.__new__(MHATokenToKVPoolHost)
    pool.size = capacity
    pool.size_per_token = group.size_per_token
    pool.page_size = cache.page_size
    pool.device = "cpu"
    pool.lock = RLock()
    pool.clear()
    monkeypatch.setattr(group.anchor_entry, "host_pool", pool)
    monkeypatch.setattr(group, "available_size", pool.available_size)
    monkeypatch.setattr(
        group, "free", lambda slots, *, pool: group.entry_map[pool].host_pool.free(slots),
        raising=False,
    )
    monkeypatch.setattr(cache.components[0], "_full_kv_pool_host", pool)
    controller = cache.cache_controller
    original_write = controller.write

    def write(indices, *, beliefkv_defer_writing=False, **kwargs):
        slots = pool.alloc(len(indices))
        if slots is None:
            return None
        assert original_write(indices, beliefkv_defer_writing=True, **kwargs) is not None
        writes[-1].host_indices = slots
        if not beliefkv_defer_writing:
            controller.start_writing()
        return slots

    monkeypatch.setattr(controller, "write", write)
    return pool


@pytest.fixture
def constrained_cache(full_native, monkeypatch, request):
    cache, allocator, writes, _, commits = full_native
    pool = bind_host_slots(cache, writes, getattr(request, "param", 8), monkeypatch)
    monkeypatch.setenv("SGLANG_HICACHE_SKIP_HOST_DUPLICATE_RECLAIM", "0")
    return cache, allocator, pool, writes, commits


def _backup(cache, node, *, demote=False):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV

    assert cache._execute_and_commit_kv_backup(
        BackupKV([node.id]), write_back=True, no_host_reclaim=True,
    ) == len(node.component_data[0].value)
    cache.writing_check(write_back=True)
    if demote:
        cache._demote(node.id, {0: 0})
    return node.component_data[0].host_value


def test_available_capacity_uses_absolute_device_slots(constrained_cache, monkeypatch):
    cache, allocator, _, _, _ = constrained_cache
    evict = Mock(side_effect=AssertionError("capacity is already available"))
    monkeypatch.setattr(cache, "evict_for_alloc", evict)
    assert ensure_device_capacity_preserving_live_kv(cache, allocator.available_size())
    assert not ensure_device_capacity_preserving_live_kv(cache, allocator.size + 1)
    evict.assert_not_called()


@pytest.mark.parametrize("use_mirror", (False, True))
def test_native_writeback_preserves_host_neighbor(constrained_cache, use_mirror):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, writes, commits = constrained_cache
    neighbor, _ = insert_session(cache, range(1, 5), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    if use_mirror:
        mirror, _ = insert_session(cache, range(10, 14), "mirror")
        mirror_slots = _backup(cache, mirror)
        mirror_hold = cache.inc_lock_ref(mirror.id).to_dec_params()
        assert pool.available_size() == 0
    victim, _ = insert_session(cache, range(20, 24), "victim")
    before = allocator.available_size()
    try:
        assert ensure_device_capacity_preserving_live_kv(cache, before + 4)
        assert allocator.available_size() == before + 4
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert victim.component_data[0].value is None
        assert victim.component_data[0].host_value.tolist() == writes[-1].host_indices.tolist()
        assert pool.slot_used[neighbor_slots].all()
        assert commits[-1].direction == "d2h" and commits[-1].status == "completed"
        assert commits[-1].node_ids == (victim.id,)
        if use_mirror:
            assert mirror.component_data[0].value is not None
            assert mirror.component_data[0].host_value is None
            assert writes[-1].host_indices.tolist() == mirror_slots.tolist()
    finally:
        if use_mirror:
            cache.dec_lock_ref(mirror.id, mirror_hold)
    cache.sanity_check()


@pytest.mark.parametrize("case", ("stale_index", "pinned_mirror", "d3_cold_device"))
def test_no_room_preserves_sole_host_copy(constrained_cache, monkeypatch, case):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, writes, _ = constrained_cache
    pinned_mirror = case != "stale_index"
    neighbor, _ = insert_session(cache, range(1, 5 if pinned_mirror else 9), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    if pinned_mirror:
        mirror, _ = insert_session(cache, range(10, 14), "pinned-mirror")
        _backup(cache, mirror)
        device_hold = cache.inc_lock_ref(mirror.id).to_dec_params()
        host_hold = cache.inc_host_lock_ref(mirror.id).to_dec_params()
    victim, _ = insert_session(cache, range(20, 24), "victim")
    victim_slots = victim.component_data[0].value
    before, writes_before = allocator.available_size(), len(writes)
    assert pool.available_size() == 0
    evict = Mock(wraps=cache.evict_for_alloc)
    monkeypatch.setattr(cache, "evict_for_alloc", evict)
    tree, full = cache.tree_core, cache.components[0]
    host_hook = Mock(wraps=full.drive_host_eviction)
    monkeypatch.setattr(full, "drive_host_eviction", host_hook)
    materialization = None
    guarded_host_hook = host_hook
    if case == "d3_cold_device":
        cache.release_radix_session("victim")
        discard = Mock(wraps=tree.discard_terminated_leaf)
        monkeypatch.setattr(tree, "discard_terminated_leaf", discard)
        runtime = SimpleNamespace(counts=Counter(), _native_chunk_input_reserve=4)
        materialization = SubagentMaterialization(runtime, include_root_entries=True)
        materialization.attach(cache)
        guarded_host_hook = full.drive_host_eviction
    try:
        assert not ensure_device_capacity_preserving_live_kv(cache, before + 4)
        assert evict.call_count == int(pinned_mirror)
        assert len(writes) == writes_before and allocator.available_size() == before
        assert victim.component_data[0].value is victim_slots
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert tree.node_by_id(victim.id) is victim and victim in tree.evictable_device_leaves
        assert tree.node_by_id(neighbor.id) is neighbor and neighbor in tree.evictable_host_leaves
        assert pool.slot_used[neighbor_slots].all()
        assert full.drive_host_eviction is guarded_host_hook
        host_hook.assert_not_called()
        if materialization is not None:
            discard.assert_called_once_with(victim.id, victim.creation_time)
    finally:
        if materialization is not None:
            materialization.close()
        if pinned_mirror:
            cache.dec_lock_ref(mirror.id, device_hold)
            cache.dec_host_lock_ref(mirror.id, host_hold)
    cache.sanity_check()


@pytest.mark.parametrize("constrained_cache", (12,), indirect=True)
@pytest.mark.parametrize("raise_error", (False, True))
def test_backup_failure_preserves_kv_and_restores_hooks(
    constrained_cache, monkeypatch, raise_error,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, _, commits = constrained_cache
    neighbor, _ = insert_session(cache, range(1, 5), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    first, _ = insert_session(cache, range(10, 14), "first")
    second, _ = insert_session(cache, range(20, 24), "second")
    before = allocator.available_size()
    controller = cache.cache_controller
    original_write = controller.write
    failed_writes = []
    drop_hook = Mock(wraps=cache._drop_subtree_no_host)
    monkeypatch.setattr(cache, "_drop_subtree_no_host", drop_hook)

    def write(indices, *, node_id, **kwargs):
        if node_id == first.id:
            return original_write(indices, node_id=node_id, **kwargs)
        failed_writes.append(node_id)
        if raise_error:
            raise RuntimeError("injected pre-enqueue write failure")
        return None

    monkeypatch.setattr(controller, "write", write)
    if raise_error:
        with pytest.raises(RuntimeError, match="injected pre-enqueue write failure"):
            ensure_device_capacity_preserving_live_kv(cache, before + 8)
    else:
        assert not ensure_device_capacity_preserving_live_kv(cache, before + 8)

    assert failed_writes == [second.id]
    assert first.component_data[0].value is None
    assert first.component_data[0].host_value is not None
    assert commits[-1].node_ids == (first.id,)
    assert second.component_data[0].value is not None
    assert second.component_data[0].host_value is None
    assert neighbor.component_data[0].host_value is neighbor_slots
    assert pool.slot_used[neighbor_slots].all() and pool.available_size() == 4
    assert allocator.available_size() == before + 4
    assert cache._drop_subtree_no_host is drop_hook
    drop_hook.assert_not_called()
    assert "_release_all_component_layers" not in vars(cache.tree_core)
    assert "drive_host_eviction" not in vars(cache.components[0])
    assert not cache.components[0].is_evict_device_ongoing
    assert not cache.ongoing_write_through and not controller.ack_write_queue
    cache.sanity_check()
