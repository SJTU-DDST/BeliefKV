from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.paper_prefix_transfers import NativePrefixTransfers
from tests.test_full_only_native_lifecycle import full_native


OWNER = ("workflow", "agent_context", 0)


@pytest.fixture
def native(full_native):
    cache, allocator, writes, loads, commits = full_native
    safe = NS(value=True)
    actor = NativePrefixTransfers(cache, can_mutate=lambda: safe.value)

    def acknowledged(commit):
        commits.append(commit)
        actor.transfer_completed(commit)

    cache.on_hicache_transfer_commit = acknowledged
    return actor, cache, allocator, writes, loads, commits, safe


def insert(cache, tokens, namespace="workflow"):
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    indices = cache.token_to_kv_pool_allocator.alloc(len(tokens))
    assert indices is not None
    result = cache.insert(InsertParams(
        key=RadixKey(array("q", tokens), namespace, cache_salt=namespace),
        value=indices,
    ))
    return cache.tree_core.node_by_id(result.last_device_node)


def settle_backup(actor, cache, snapshot):
    assert actor.backup(snapshot, owner=OWNER)
    cache.writing_check(finish_count=1)


def test_ready_host_guard_protects_existing_prefix_during_incremental_backup(native):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    actor, cache, _, writes, *_ = native
    first = insert(cache, [1, 2, 3, 4])
    prefix = actor.capture(first.id, owner=OWNER)
    settle_backup(actor, cache, prefix)
    tail = insert(cache, [1, 2, 3, 4, 5, 6, 7, 8])
    snapshot = actor.capture(tail.id, owner=OWNER)
    guard = actor.retain_host_ready(snapshot, owner=OWNER, deadline_s=actor.clock() + 60.)
    assert guard is not None and not actor._host_leases[guard].complete
    assert not actor.retain_host(snapshot, owner=OWNER, deadline_s=actor.clock() + 60.)
    assert actor.backup(snapshot, owner=OWNER, allow_host_reclaim=True)
    assert cache.evict_host(100) == 0
    cache.match_prefix(MatchPrefixParams(key=RadixKey(
        array("q", [1, 2]), "workflow", cache_salt="workflow",
    )))
    assert len(actor._host_leases[guard].pins) == 2
    cache.writing_check(finish_count=1)
    full = actor.retain_host(snapshot, owner=OWNER, deadline_s=actor.clock() + 60.)
    assert full is not None and actor._host_leases[full].complete
    actor.release_host(guard)
    assert cache.evict_host(100) == 0
    assert [len(item.device_indices) for item in writes] == [4, 4]
    actor.release_host(full)


def test_opt_in_backup_reclaims_native_host_lru_before_enqueuing_the_whole_increment(native):
    actor, cache, _, writes, *_ = native
    group, free = cache.host_pool_group, [16]
    group.anchor_entry.host_pool.size = 16
    group.available_size = lambda: free[0]
    cache.components[0]._full_kv_pool_host = group.anchor_entry.host_pool

    def release(indices, *, pool):
        assert pool == "kv"
        free[0] += len(indices)

    group.free = release
    write = cache.cache_controller.write

    def allocate(indices, **kwargs):
        assert free[0] >= len(indices)
        result = write(indices, **kwargs)
        if result is not None:
            free[0] -= len(indices)
        return result

    cache.cache_controller.write = allocate
    first = insert(cache, [0, 1, 2, 3])
    first_snapshot = actor.capture(first.id, owner=OWNER)
    settle_backup(actor, cache, first_snapshot)
    unrelated = insert(cache, list(range(100, 108)))
    unrelated_snapshot = actor.capture(unrelated.id, owner=OWNER)
    settle_backup(actor, cache, unrelated_snapshot)
    assert free[0] == 4
    old_host = first.component_data[0].host_value
    grown = insert(cache, list(range(12)))
    snapshot = actor.capture(grown.id, owner=OWNER)
    guard = actor.retain_host_ready(snapshot, owner=OWNER, deadline_s=actor.clock() + 60.)
    assert guard is not None
    issued = actor.backup(snapshot, owner=OWNER, allow_host_reclaim=True)
    assert sum(item[2] for item in issued) == 8
    assert unrelated.component_data[0].host_value is None
    assert first.component_data[0].host_value is old_host
    assert free[0] == 4
    assert cache.evict_host(100) == 0
    cache.writing_check(finish_count=1)
    full = actor.retain_host(snapshot, owner=OWNER, deadline_s=actor.clock() + 60.)
    assert full is not None
    actor.release_host(guard)
    assert [len(op.device_indices) for op in writes] == [4, 8, 8]
    actor.release_host(full)


def test_sessionless_native_d2h_ack_demote_h2d_ack_and_unique_capacity(native):
    actor, cache, allocator, writes, loads, commits, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    assert snapshot.prefix_tokens == 4 and not cache.session_refs._session_generations
    before = allocator.available_size()
    assert actor.backup(snapshot, owner=OWNER)
    assert actor.status(snapshot, owner=OWNER) == "offloading"
    assert allocator.available_size() == before
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 0
    assert len(writes) == 1 and not commits
    cache.writing_check(finish_count=1)
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4
    assert allocator.available_size() == before + 4
    assert actor.status(snapshot, owner=OWNER) == "cpu_backup"
    cache.load_back_threshold = 1
    assert actor.restore(snapshot, owner=OWNER, available_tokens=4)
    assert allocator.available_size() == before
    assert actor.status(snapshot, owner=OWNER) == "loading"
    assert len(loads) == 1 and len(commits) == 1
    assert not actor.restore(snapshot, owner=OWNER, available_tokens=4)
    cache.loading_check(finish_count=1)
    assert actor.status(snapshot, owner=OWNER) == "gpu"
    assert leaf.component_data[0].host_lock_ref == leaf.component_data[0].lock_ref == 0
    assert [(item.direction, dict(item.num_tokens_by_pool)) for item in commits] == [
        ("d2h", {"kv": 4}), ("h2d", {"kv": 4}),
    ]


def test_backup_is_incremental_and_a_native_split_during_dma_preserves_snapshot(native):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    actor, cache, _, writes, _, _, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    assert actor.backup(snapshot, owner=OWNER)
    cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [1, 2]), "workflow", cache_salt="workflow"),
    ))
    assert actor.resolve(snapshot, owner=OWNER) is not None
    assert len(actor.resolve(snapshot, owner=OWNER)) == 2
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 0
    cache.writing_check(finish_count=1)
    assert not actor.backup(snapshot, owner=OWNER)
    assert sum(len(item.device_indices) for item in writes) == 4
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4


def test_live_peer_lock_keeps_shared_pages_and_release_allows_later_demotion(native):
    actor, cache, _, _, _, _, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    receipt = cache.inc_lock_ref(leaf.id).to_dec_params()
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 0
    cache.dec_lock_ref(leaf.id, receipt)
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4


def test_changed_owner_epoch_namespace_or_leaf_generation_cannot_move_cache(native):
    actor, cache, _, writes, loads, _, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    assert actor.resolve(snapshot, owner=("workflow", "agent_context", 1)) is None
    assert not actor.backup(snapshot, owner=("other_workflow", "agent_context", 0))
    leaf.creation_time += 1
    assert actor.resolve(snapshot, owner=OWNER) is None
    assert not actor.restore(snapshot, owner=OWNER, available_tokens=4)
    assert not writes and not loads


def test_original_namespace_cannot_resolve_after_node_key_changes(native):
    actor, cache, _, _, _, _, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    leaf.key.extra_key = "other"
    assert actor.resolve(snapshot, owner=OWNER) is None


def test_restoring_never_reclaims_another_request_to_fit_the_target(native):
    actor, cache, allocator, _, loads, _, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4
    before = allocator.available_size()
    assert not actor.restore(snapshot, owner=OWNER, available_tokens=3)
    assert allocator.available_size() == before and not loads


@pytest.mark.parametrize("busy_slot", range(3))
def test_busy_native_overlap_slot_defers_restore_without_allocating_or_claiming(native, busy_slot):
    actor, cache, allocator, _, loads, _, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4
    event = cache.cache_controller.layer_done_counter.events[busy_slot].finish_event
    event.query = lambda: False
    free, pending = allocator.available_size(), actor.receipts.pending_count
    assert not actor.restore(snapshot, owner=OWNER, available_tokens=4)
    assert allocator.available_size() == free and not loads
    assert not cache.cache_controller.load_queue
    assert actor.receipts.pending_count == pending
    assert actor.counts["restore_native_producer_backpressure"] == 1
    event.query = lambda: True
    assert actor.restore(snapshot, owner=OWNER, available_tokens=4)
    cache.loading_check(finish_count=1)
    assert actor.status(snapshot, owner=OWNER) == "gpu"


def test_one_maintenance_batch_submits_multiple_owners_to_one_native_producer(native):
    actor, cache, allocator, _, loads, _, _ = native
    targets = []
    for ordinal in range(4):
        owner = ("workflow", f"agent-{ordinal}", 0)
        leaf = insert(cache, range(ordinal * 4, ordinal * 4 + 4))
        snapshot = actor.capture(leaf.id, owner=owner)
        assert actor.backup(snapshot, owner=owner)
        cache.writing_check(finish_count=1)
        assert actor.demote(snapshot, owner=owner, max_tokens=4) == 4
        targets.append((owner, snapshot))
    start = cache.cache_controller.start_loading
    free = allocator.available_size()
    with actor.loading_batch():
        for owner, snapshot in targets:
            assert actor.restore(snapshot, owner=owner, available_tokens=4)
            assert not start.called
        assert len(cache.cache_controller.load_queue) == 4
    assert start.call_count == 1
    assert len(loads) == 4 and allocator.available_size() == free - 16
    assert len(cache.cache_controller.ack_load_queue) == 1
    cache.loading_check(finish_count=1)
    assert not actor.receipts.pending_count
    assert all(actor.status(snapshot, owner=owner) == "gpu" for owner, snapshot in targets)


def test_failed_later_target_still_flushes_prior_restore_in_the_same_batch(native):
    actor, cache, _, _, loads, _, _ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4
    with pytest.raises(ValueError, match="later target"):
        with actor.loading_batch():
            assert actor.restore(snapshot, owner=OWNER, available_tokens=4)
            raise ValueError("later target")
    assert len(loads) == 1
    assert cache.cache_controller.start_loading.call_count == 1
    cache.loading_check(finish_count=1)
    assert not actor.receipts.pending_count
    assert actor.status(snapshot, owner=OWNER) == "gpu"


def test_second_enqueue_failure_still_starts_and_acks_first_native_h2d(native):
    actor, cache, _, _, loads, commits, _ = native
    insert(cache, [1, 2])
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    assert len(actor.resolve(snapshot, owner=OWNER)) == 2
    settle_backup(actor, cache, snapshot)
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4
    cache.load_back_threshold = 1
    load = cache.cache_controller.load
    calls = []

    def decline_second(*args, **kwargs):
        calls.append(kwargs["node_id"])
        return load(*args, **kwargs) if len(calls) == 1 else None

    cache.cache_controller.load = decline_second
    assert len(actor.restore(snapshot, owner=OWNER, available_tokens=4)) == 1
    assert len(loads) == 1 and len(cache.cache_controller.ack_load_queue) == 1
    cache.loading_check(finish_count=1)
    assert commits[-1].direction == "h2d"
    assert dict(commits[-1].num_tokens_by_pool) == {"kv": 2}


def test_native_authority_is_required_without_adding_a_gpu_sync(native):
    actor, cache, _, writes, loads, _, safe = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    safe.value = False
    for operation in (
        lambda: actor.backup(snapshot, owner=OWNER),
        lambda: actor.demote(snapshot, owner=OWNER, max_tokens=4),
        lambda: actor.restore(snapshot, owner=OWNER, available_tokens=4),
    ):
        with pytest.raises(ValueError, match="native scheduler cache authority"):
            operation()
    assert not writes and not loads


def test_context_growth_backs_only_new_suffix_after_prior_copy_is_settled(native):
    actor, cache, _, writes, _, _, _ = native
    first = insert(cache, [1, 2, 3, 4])
    settle_backup(actor, cache, actor.capture(first.id, owner=OWNER))
    second = insert(cache, [1, 2, 3, 4, 5, 6, 7, 8])
    issued = actor.backup(actor.capture(second.id, owner=OWNER), owner=OWNER)
    assert len(issued) == 1 and issued[0][2] == 4
    assert [len(item.device_indices) for item in writes] == [4, 4]


def test_host_retention_protects_shared_ancestors_but_allows_device_capacity_release(native):
    actor, cache, _, _, _, _, _ = native
    actor.clock = lambda: 10.
    freed = []
    cache.host_pool_group.free = lambda indices, **_kwargs: freed.append(tuple(indices.tolist()))
    cache.components[0]._full_kv_pool_host = cache.host_pool_group.anchor_entry.host_pool
    ancestor = insert(cache, [1, 2])
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    lease = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    assert lease is not None
    assert all(node.component_data[0].host_lock_ref == 1 for node in (ancestor, leaf))
    assert cache.evict_host(4) == 0 and not freed
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4
    assert cache.evict_host(4) == 0
    assert actor.release_host(lease)
    assert cache.evict_host(4) == 4
    assert sum(map(len, freed)) == 4


def test_manual_host_ownership_survives_timed_heap_compaction_and_expiry(native):
    actor, cache, *_ = native
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    actor.clock, actor.max_host_leases = lambda: 10., 4
    manual = actor.retain_host_until_release(snapshot, owner=OWNER)
    assert manual is not None
    for _ in range(20):
        timed = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
        assert timed is not None and actor.release_host(timed)
    timed = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    actor.clock = lambda: 10**12
    assert actor.expire_host_leases() == 1
    assert timed not in actor._host_leases and manual in actor._host_leases
    assert leaf.component_data[0].host_lock_ref == 1
    assert actor.release_host(manual)
    assert leaf.component_data[0].host_lock_ref == 0


def test_host_pin_inherits_a_split_prefix_and_expiry_releases_every_source_extent(native):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    actor, cache, _, _, _, _, _ = native
    now = NS(value=10.)
    actor.clock = lambda: now.value
    cache.host_pool_group.free = lambda *_args, **_kwargs: None
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    lease = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [1, 2]), "workflow", cache_salt="workflow"),
    ))
    path = actor.resolve(snapshot, owner=OWNER)
    assert len(path) == len(actor._host_leases[lease].pins) == 2
    assert all(node.component_data[0].host_lock_ref == 1 for node in path)
    assert cache.evict_host(4) == 0
    now.value = 19.
    assert actor.expire_host_leases() == 0
    now.value = 20.
    assert actor.expire_host_leases() == 1
    assert all(node.component_data[0].host_lock_ref == 0 for node in path)
    assert cache.evict_host(4) == 4


def test_host_leases_share_copies_with_independent_reference_counts_and_bounded_tombstones(native):
    actor, cache, _, _, _, _, _ = native
    actor.clock = lambda: 10.
    actor.max_host_leases = 2
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    first = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    second = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    assert leaf.component_data[0].host_lock_ref == 2
    assert actor.retain_host(snapshot, owner=OWNER, deadline_s=20.) is None
    actor.release_host(first)
    assert leaf.component_data[0].host_lock_ref == 1
    actor.release_host(second)
    for _ in range(20):
        lease = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
        actor.release_host(lease)
    assert len(actor._host_deadlines) <= actor.max_host_leases
    assert cache.tree_core.beliefkv_full_resident_observer is None


def test_host_lease_requires_real_settled_copy_and_close_does_not_remove_native_dma_pin(native):
    actor, cache, _, _, _, _, _ = native
    actor.clock = lambda: 10.
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    assert actor.retain_host(snapshot, owner=OWNER, deadline_s=20.) is None
    assert actor.backup(snapshot, owner=OWNER)
    assert actor.retain_host(snapshot, owner=OWNER, deadline_s=20.) is None
    cache.writing_check(finish_count=1)
    lease = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    assert lease is not None
    assert actor.demote(snapshot, owner=OWNER, max_tokens=4) == 4
    cache.load_back_threshold = 1
    assert actor.restore(snapshot, owner=OWNER, available_tokens=4)
    assert leaf.component_data[0].host_lock_ref == 2
    actor.close()
    assert leaf.component_data[0].host_lock_ref == 1
    cache.loading_check(finish_count=1)
    assert leaf.component_data[0].host_lock_ref == 0


def test_close_restores_native_allocation_entry_without_touching_cached_pages(native):
    actor, cache, *_ = native
    original = actor._previous_allocation
    leaf = insert(cache, [1, 2])
    values = leaf.component_data[0].value
    assert cache.evict_for_alloc is actor._allocation_callback
    actor.close()
    assert cache.evict_for_alloc is original
    assert leaf.component_data[0].value is values
