from array import array
import math

import pytest

from beliefkv.runtime.kvflow_cache import KVFlowCachePriorities
from tests.test_full_only_native_lifecycle import full_native
from tests.test_paper_prefix_transfers import OWNER, insert, native, settle_backup


@pytest.fixture
def priorities(native):
    actor, *_ = native
    policy = KVFlowCachePriorities(actor)
    yield policy, native
    policy.close()


def test_native_heap_evicts_dynamic_suffix_before_farther_fixed_prefix(priorities):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    policy, (actor, cache, allocator, _, _, _, _) = priorities
    fixed_a = insert(cache, [1, 2])
    suffix_a = insert(cache, [1, 2, 3, 4])
    fixed_b = insert(cache, [5, 6])
    snap_a = actor.capture(fixed_a.id, owner=OWNER)
    snap_b = actor.capture(fixed_b.id, owner=OWNER)
    policy.set_anchor("w:a", snap_a, owner=OWNER)
    policy.set_anchor("w:b", snap_b, owner=OWNER)
    policy.set_steps({"w:a": 1, "w:b": 5})
    settle_backup(actor, cache, actor.capture(suffix_a.id, owner=OWNER))
    settle_backup(actor, cache, snap_b)
    before = allocator.available_size()
    result = cache.evict(EvictParams(num_tokens=2))
    assert result.num_tokens_evicted == 2 and allocator.available_size() == before + 2
    assert suffix_a.component_data[0].value is None
    assert fixed_a.component_data[0].value is not None
    assert fixed_b.component_data[0].value is not None
    cache.evict(EvictParams(num_tokens=2))
    assert fixed_b.component_data[0].value is None
    assert fixed_a.component_data[0].value is not None


def test_split_prefix_inherits_anchored_agent_and_dynamic_suffix_remains_infinite(priorities):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    policy, (actor, cache, _, _, _, _, _) = priorities
    leaf = insert(cache, [1, 2, 3, 4])
    policy.set_anchor("w:a", actor.capture(leaf.id, owner=OWNER), owner=OWNER)
    policy.set_steps({"w:a": 2})
    match = cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [1, 2]), "workflow", cache_salt="workflow"),
    ))
    prefix = cache.tree_core.node_by_id(match.last_device_node)
    assert policy._eviction_key(prefix)[0] == -2
    assert policy._eviction_key(leaf)[0] == -2
    assert len(policy._paths["w:a"]) == 2
    suffix = insert(cache, [1, 2, 3, 4, 5, 6])
    assert policy._eviction_key(suffix)[0] == -math.inf
    assert policy.counts["split_prefix_memberships"] == 1


def test_shared_fixed_prefix_uses_nearest_agent_and_changes_only_affected_nodes(priorities):
    policy, (actor, cache, _, _, _, _, _) = priorities
    shared = insert(cache, [1, 2])
    a = insert(cache, [1, 2, 3])
    b = insert(cache, [1, 2, 4])
    policy.set_anchor("w:a", actor.capture(a.id, owner=OWNER), owner=OWNER)
    policy.set_anchor("w:b", actor.capture(b.id, owner=OWNER), owner=OWNER)
    policy.set_steps({"w:a": 5, "w:b": 1})
    assert policy._eviction_key(shared)[0] == -1
    before = policy.counts["step_update_nodes"]
    policy.set_steps({"w:a": 2, "w:b": 1})
    assert policy._eviction_key(shared)[0] == -1
    assert policy.counts["step_update_nodes"] - before == 2
    policy.forget("w:b")
    assert policy._eviction_key(shared)[0] == -2
    assert policy._eviction_key(b)[0] == -math.inf


def test_native_running_locks_still_prevent_eviction_despite_high_paper_priority(priorities):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    policy, (actor, cache, _, _, _, _, _) = priorities
    leaf = insert(cache, [1, 2])
    snap = actor.capture(leaf.id, owner=OWNER)
    policy.set_anchor("w:a", snap, owner=OWNER)
    policy.set_steps({"w:a": 100})
    settle_backup(actor, cache, snap)
    lock = cache.inc_lock_ref(leaf.id).to_dec_params()
    assert cache.evict(EvictParams(num_tokens=2)).num_tokens_evicted == 0
    assert leaf.component_data[0].value is not None
    cache.dec_lock_ref(leaf.id, lock)


def test_same_unqualified_agent_name_cannot_merge_distinct_workflows(priorities):
    policy, (actor, cache, _, _, _, _, _) = priorities
    a = insert(cache, [1, 2], "one")
    b = insert(cache, [1, 2], "two")
    one, two = ("one", "context", 0), ("two", "context", 0)
    policy.set_anchor("agent", actor.capture(a.id, owner=one), owner=one)
    with pytest.raises(ValueError, match="cannot cross workflows"):
        policy.set_anchor("agent", actor.capture(b.id, owner=two), owner=two)


def test_close_restores_original_native_policy_without_touching_cache_values(native):
    actor, cache, _, _, _, _, _ = native
    before = cache.components[0].session_ref_eviction_strategy
    observer = cache.tree_core.beliefkv_full_resident_observer
    leaf = insert(cache, [1, 2])
    value = leaf.component_data[0].value
    policy = KVFlowCachePriorities(actor)
    policy.close()
    assert cache.components[0].session_ref_eviction_strategy is before
    assert cache.tree_core.beliefkv_full_resident_observer is observer
    assert leaf.component_data[0].value is value


def test_host_retention_and_native_eviction_observers_both_survive_a_split(priorities):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    policy, (actor, cache, _, _, _, _, _) = priorities
    actor.clock = lambda: 10.
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=OWNER)
    policy.set_anchor("w:a", snapshot, owner=OWNER)
    policy.set_steps({"w:a": 2})
    settle_backup(actor, cache, snapshot)
    lease = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [1, 2]), "workflow", cache_salt="workflow"),
    ))
    path = actor.resolve(snapshot, owner=OWNER)
    assert len(path) == 2
    assert all(node.component_data[0].host_lock_ref == 1 for node in path)
    assert all(policy._eviction_key(node)[0] == -2 for node in path)
    actor.release_host(lease)
    assert cache.tree_core.beliefkv_full_resident_observer is policy._observer_callback


def test_speculative_native_heap_reclaim_never_waits_for_unrelated_d2h(priorities):
    policy, (actor, cache, allocator, _, _, _, _) = priorities
    settled = insert(cache, [1, 2])
    settled_snapshot = actor.capture(settled.id, owner=OWNER)
    settle_backup(actor, cache, settled_snapshot)
    pending = insert(cache, [9, 8])
    pending_snapshot = actor.capture(pending.id, owner=OWNER)
    assert actor.backup(pending_snapshot, owner=OWNER)
    finish = cache.cache_controller.ack_write_queue[0].finish_event
    before = allocator.available_size()
    freed, checked = policy.reclaim_for_prefetch(2, max_nodes=4)
    assert freed == 2 and 1 <= checked <= 4
    assert allocator.available_size() == before + 2
    assert settled.component_data[0].value is None
    assert pending.component_data[0].value is not None
    assert pending.id in cache.ongoing_write_through
    finish.synchronize.assert_not_called()


def test_unbacked_native_heap_victim_is_only_demoted_after_external_native_ack(priorities):
    policy, (actor, cache, allocator, writes, _, _, _) = priorities
    leaf = insert(cache, [1, 2, 3, 4])
    before = allocator.available_size()
    assert policy.reclaim_for_prefetch(4, max_nodes=1) == (0, 1)
    assert allocator.available_size() == before and len(writes) == 1
    assert leaf.write_through_pending_id is not None
    cache.cache_controller.ack_write_queue[0].finish_event.synchronize.assert_not_called()
    cache.writing_check(finish_count=1)
    assert policy.reclaim_for_prefetch(4, max_nodes=1) == (4, 1)
    assert allocator.available_size() == before + 4
    assert leaf.component_data[0].value is None


def test_prefetch_reclaim_honors_native_locks_and_the_node_budget(priorities):
    policy, (actor, cache, allocator, writes, _, _, _) = priorities
    locked = insert(cache, [1, 2])
    lock = cache.inc_lock_ref(locked.id).to_dec_params()
    insert(cache, [3, 4])
    insert(cache, [5, 6])
    before = allocator.available_size()
    assert policy.reclaim_for_prefetch(4, max_nodes=1) == (0, 1)
    assert len(writes) == 1 and allocator.available_size() == before
    assert locked.component_data[0].value is not None
    cache.dec_lock_ref(locked.id, lock)


def test_native_demand_allocation_retries_after_speculative_backup_unlocks_a_leaf(priorities):
    from sglang.srt.mem_cache.allocation import alloc_token_slots

    policy, (actor, cache, allocator, _, _, _, _) = priorities
    parent = insert(cache, [1, 2])
    settle_backup(actor, cache, actor.capture(parent.id, owner=OWNER))
    leaf = insert(cache, [1, 2, 3, 4])
    assert policy.reclaim_for_prefetch(2, max_nodes=1) == (0, 1)
    assert leaf.write_through_pending_id is not None
    assert cache.full_evictable_size() == 0
    pressure = allocator.alloc(allocator.available_size() - 2)
    assert allocator.available_size() == 2
    allocated = alloc_token_slots(cache, 4)
    assert len(allocated) == 4
    assert leaf.write_through_pending_id is None
    assert leaf.component_data[0].value is None
    assert parent.component_data[0].value is not None
    assert actor.counts["allocation_after_backup_retry"] == 1
    allocator.free(allocated)
    allocator.free(pressure)
