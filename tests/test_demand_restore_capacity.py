"""Native allocator and ACK checks for the demand restore capacity guard."""

from collections import Counter
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, call

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


@pytest.mark.parametrize(
    ("ready", "released_on_ack"),
    ((True, 0), (False, 0), (True, 2)),
    ids=("completed", "in-flight", "completed-recheck-shortfall"),
)
def test_single_pending_write_ack(constrained_cache, monkeypatch, ready, released_on_ack):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, writes, commits = constrained_cache
    neighbor, _ = insert_session(cache, range(1, 5), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    donor, _ = insert_session(cache, range(10, 14), "donor")
    data = donor.component_data[0]
    device_slots = data.value
    assert cache._execute_and_commit_kv_backup(
        BackupKV([donor.id]), write_back=False, no_host_reclaim=True,
    ) == 4
    donor_slots = data.host_value
    controller = cache.cache_controller
    ack, = controller.ack_write_queue
    pending = cache.ongoing_write_through[donor.id]
    donor_lock = data.lock_ref
    assert ack.node_ids == [donor.id] and donor.write_through_pending_id == donor.id
    assert donor_lock > 0 and cache.full_evictable_size() == 0
    assert pool.available_size() == 0
    assert neighbor.component_data[0].session_ref > 0 and data.session_ref > 0

    if released_on_ack:
        # Return independently held slots during ACK publication to exercise the reread.
        held_slots = allocator.alloc(released_on_ack)
        assert held_slots is not None

        def commit_and_release_slots(commit):
            commits.append(commit)
            allocator.free(held_slots)

        monkeypatch.setattr(cache, "on_hicache_transfer_commit", commit_and_release_slots)

    query = Mock(return_value=ready)
    monkeypatch.setattr(ack.finish_event, "query", query, raising=False)
    finish = Mock(wraps=cache.writing_check)
    evict = Mock(wraps=cache.evict_for_alloc)
    monkeypatch.setattr(cache, "writing_check", finish)
    monkeypatch.setattr(cache, "evict_for_alloc", evict)
    before = allocator.available_size()
    target = before + 4 + released_on_ack
    writes_before, commits_before = len(writes), len(commits)

    assert ensure_device_capacity_preserving_live_kv(cache, target) is ready

    query.assert_called_once_with()
    if ready:
        assert finish.call_args_list[:1] == [call(finish_count=1)]
        ack.finish_event.synchronize.assert_called_once_with()
        evict.assert_called_once()
        assert evict.call_args.args[0].num_tokens == 4
        assert not controller.ack_write_queue and not cache.ongoing_write_through
        assert donor.write_through_pending_id is None and data.lock_ref == 0
        assert len(commits) == commits_before + 1
        assert commits[-1].direction == "d2h" and commits[-1].status == "completed"
        assert commits[-1].node_ids == (donor.id,)
        assert allocator.available_size() == target and data.value is None
    else:
        finish.assert_not_called()
        ack.finish_event.synchronize.assert_not_called()
        evict.assert_not_called()
        assert controller.ack_write_queue == [ack]
        assert cache.ongoing_write_through[donor.id] is pending
        assert donor.write_through_pending_id == donor.id and data.lock_ref == donor_lock
        assert len(commits) == commits_before
        assert allocator.available_size() == before and data.value is device_slots
    assert len(writes) == writes_before and not controller.write_queue
    assert data.host_value is donor_slots and pool.slot_used[donor_slots].all()
    assert neighbor.component_data[0].host_value is neighbor_slots
    assert neighbor.component_data[0].value is None and pool.slot_used[neighbor_slots].all()
    assert pool.available_size() == 0
    cache.sanity_check()


@pytest.mark.parametrize("constrained_cache", (12,), indirect=True)
@pytest.mark.parametrize("batched", (False, True), ids=("two-acks", "multi-node-ack"))
def test_multiple_pending_write_nodes_are_left_pending(constrained_cache, monkeypatch, batched):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, writes, commits = constrained_cache
    neighbor, _ = insert_session(cache, range(1, 5), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    donors = (
        insert_session(cache, range(10, 14), "first")[0],
        insert_session(cache, range(20, 24), "second")[0],
    )
    controller = cache.cache_controller
    for donor in donors:
        assert cache._execute_and_commit_kv_backup(
            BackupKV([donor.id]), write_back=False, no_host_reclaim=True,
            beliefkv_defer_writing=batched,
        ) == 4
    if batched:
        controller.start_writing()
    acks = tuple(controller.ack_write_queue)
    assert [len(ack.node_ids) for ack in acks] == ([2] if batched else [1, 1])
    for ack in acks:
        monkeypatch.setattr(ack.finish_event, "query", Mock(return_value=True), raising=False)
    finish = Mock(wraps=cache.writing_check)
    monkeypatch.setattr(cache, "writing_check", finish)
    pending = dict(cache.ongoing_write_through)
    before = allocator.available_size()
    assert pool.available_size() == 0

    assert not ensure_device_capacity_preserving_live_kv(cache, before + 4)

    finish.assert_not_called()
    for ack in acks:
        ack.finish_event.query.assert_not_called()
        ack.finish_event.synchronize.assert_not_called()
    assert tuple(controller.ack_write_queue) == acks and not controller.write_queue
    assert cache.ongoing_write_through == pending
    assert allocator.available_size() == before and pool.available_size() == 0
    assert neighbor.component_data[0].host_value is neighbor_slots
    assert pool.slot_used[neighbor_slots].all() and len(writes) == 3 and len(commits) == 1
    cache.sanity_check()


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


@pytest.mark.parametrize("pinned_mirror", (False, True))
def test_no_room_preserves_sole_host_copy(constrained_cache, monkeypatch, pinned_mirror):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, writes, _ = constrained_cache
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
    try:
        assert not ensure_device_capacity_preserving_live_kv(cache, before + 4)
        assert evict.call_count == int(pinned_mirror)
        assert len(writes) == writes_before and allocator.available_size() == before
        assert victim.component_data[0].value is victim_slots
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert tree.node_by_id(victim.id) is victim and victim in tree.evictable_device_leaves
        assert tree.node_by_id(neighbor.id) is neighbor and neighbor in tree.evictable_host_leaves
        assert pool.slot_used[neighbor_slots].all()
        assert full.drive_host_eviction is host_hook
        host_hook.assert_not_called()
    finally:
        if pinned_mirror:
            cache.dec_lock_ref(mirror.id, device_hold)
            cache.dec_host_lock_ref(mirror.id, host_hold)
    cache.sanity_check()


@pytest.mark.parametrize("pinned_mirror", (False, True))
@pytest.mark.parametrize("blocker", (
    None, "session_ref", "device_lock", "host_lock_ref",
    "write_through_pending_id", "load_back_pending_id",
))
def test_guarded_d3_cold_device_leaf(constrained_cache, monkeypatch, pinned_mirror, blocker):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, writes, commits = constrained_cache
    neighbor, _ = insert_session(cache, range(1, 5 if pinned_mirror else 9), "neighbor")
    neighbor_slots = _backup(cache, neighbor, demote=True)
    if pinned_mirror:
        mirror, _ = insert_session(cache, range(10, 14), "pinned-mirror")
        mirror_slots = _backup(cache, mirror)
        mirror_device_hold = cache.inc_lock_ref(mirror.id).to_dec_params()
        mirror_host_hold = cache.inc_host_lock_ref(mirror.id).to_dec_params()
    leaf, _ = insert_session(cache, range(20, 24), "candidate")
    data = leaf.component_data[0]
    device_slots = data.value
    if blocker != "session_ref":
        cache.release_radix_session("candidate")
    leaf_hold = cache.inc_lock_ref(leaf.id).to_dec_params() if blocker == "device_lock" else None
    before, writes_before, commits_before = allocator.available_size(), len(writes), len(commits)
    assert pool.available_size() == 0
    runtime = SimpleNamespace(counts=Counter(), _native_chunk_input_reserve=4)
    gate = SubagentMaterialization(runtime, include_root_entries=True)
    tree = cache.tree_core
    discard = Mock(wraps=tree.discard_terminated_leaf)
    monkeypatch.setattr(tree, "discard_terminated_leaf", discard)
    evict = Mock(wraps=cache.evict_for_alloc)
    monkeypatch.setattr(cache, "evict_for_alloc", evict)
    gate.attach(cache)
    attached_evict = cache._evict_device_leaf
    try:
        with monkeypatch.context() as state:
            if blocker == "host_lock_ref":
                state.setattr(data, blocker, 1)
            if blocker in ("write_through_pending_id", "load_back_pending_id"):
                state.setattr(leaf, blocker, leaf.id)
            expected = blocker is None
            assert ensure_device_capacity_preserving_live_kv(cache, before + 4) is expected

        assert cache._evict_device_leaf is attached_evict
        assert "_release_all_component_layers" not in vars(tree)
        assert allocator.available_size() == before + 4 * expected
        assert len(writes) == writes_before and len(commits) == commits_before
        assert runtime.counts["subagent_materialization_cold_device_discarded_nodes"] == expected
        assert runtime.counts["subagent_materialization_cold_device_discarded_tokens"] == 4 * expected
        assert neighbor.component_data[0].host_value is neighbor_slots
        assert pool.slot_used[neighbor_slots].all() and pool.available_size() == 0
        if pinned_mirror:
            assert mirror.component_data[0].host_value is mirror_slots
            assert pool.slot_used[mirror_slots].all()
        if expected:
            evict.assert_called_once()
            discard.assert_called_once_with(leaf.id, leaf.creation_time)
            with pytest.raises(KeyError):
                tree.node_by_id(leaf.id)
        else:
            assert evict.call_count == int(pinned_mirror and blocker != "device_lock")
            assert tree.node_by_id(leaf.id) is leaf
            assert data.value is device_slots and data.host_value is None
    finally:
        gate.close()
        if leaf_hold is not None:
            cache.dec_lock_ref(leaf.id, leaf_hold)
        if pinned_mirror:
            cache.dec_host_lock_ref(mirror.id, mirror_host_hold)
            cache.dec_lock_ref(mirror.id, mirror_device_hold)
    cache.sanity_check()


def test_d3_failed_backup_preserves_live_host_descendant(constrained_cache, monkeypatch):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool, writes, commits = constrained_cache
    parent, _ = insert_session(cache, range(1, 5), "parent")
    child, _ = insert_session(cache, range(1, 9), "child")
    child_slots = _backup(cache, child, demote=True)
    cache.release_radix_session("parent")
    tree = cache.tree_core
    assert child.parent is parent and parent in tree.evictable_device_leaves
    assert pool.available_size() == 4 and child.component_data[0].session_ref > 0
    before, parent_slots = allocator.available_size(), parent.component_data[0].value
    reject_write = Mock(return_value=None)
    monkeypatch.setattr(cache.cache_controller, "write", reject_write)
    fallback = Mock(wraps=tree.drop_subtree_no_host)
    monkeypatch.setattr(tree, "drop_subtree_no_host", fallback)
    runtime = SimpleNamespace(counts=Counter(), _native_chunk_input_reserve=4)
    gate = SubagentMaterialization(runtime, include_root_entries=True)
    gate.attach(cache)
    try:
        assert not ensure_device_capacity_preserving_live_kv(cache, before + 4)
        reject_write.assert_called()
        fallback.assert_not_called()
        assert tree.drop_subtree_no_host is fallback
        assert allocator.available_size() == before
        assert tree.node_by_id(parent.id) is parent and tree.node_by_id(child.id) is child
        assert parent.component_data[0].value is parent_slots
        assert child.component_data[0].host_value is child_slots and pool.slot_used[child_slots].all()
        assert len(writes) == len(commits) == 1
        assert not runtime.counts["subagent_materialization_cold_device_discarded_nodes"]
    finally:
        gate.close()
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
