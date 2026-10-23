"""FULL pressure must not wait for a copy of independently resident state."""

from array import array
from unittest.mock import Mock

import pytest

from tests.test_full_backup_checkpoint_durability import (
    inherited_checkpoint,
    prepared_checkpoint,
)
from tests.test_waiting_full_tail_backup import native_cache


@pytest.mark.parametrize("scope", ("unowned", "inherited"))
def test_full_only_capacity_parks_checkpoint_until_state_pressure(native_cache, scope):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    if scope == "unowned":
        cache, allocator, request_pool, leaf = prepared_checkpoint(
            native_cache, incremental=True,
        )
        query = [10, 11, 12, 13, 99]
        cache.beliefkv_join_pressure_candidates = ()
        cache.beliefkv_join_pressure_validator = lambda *_: False
    else:
        cache, allocator, leaf, _ = inherited_checkpoint(native_cache)
        request_pool = cache.req_to_token_pool
        query = [1, 2, 3, 4, 5, 99]
        cache.beliefkv_join_pressure_candidates_by_component = {
            0: ((leaf.id, leaf.creation_time),),
        }
        cache.beliefkv_join_pressure_validator = lambda node_id, created: (
            node_id, created,
        ) == (leaf.id, leaf.creation_time)

    full, state = leaf.component_data[0], leaf.component_data[2]
    assert state.session_ref == 0 and state.host_value is None
    full_host, state_device = full.host_value, state.value
    full_free = allocator.available_size()
    state_free = request_pool.mamba_allocator.available_size()
    cache._execute_and_commit_kv_backup = Mock(
        side_effect=AssertionError("FULL-only pressure needs no state DMA"),
    )
    cache.writing_check = Mock(
        side_effect=AssertionError("FULL-only pressure needs no state ACK"),
    )

    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=full_free + 4, mamba_slots=0,
    ) == {0: 4}
    assert allocator.available_size() == full_free + 4
    assert request_pool.mamba_allocator.available_size() == state_free
    assert full.value is None and full.host_value is full_host
    assert state.value is state_device and state.host_value is None
    cache._execute_and_commit_kv_backup.assert_not_called()
    cache.writing_check.assert_not_called()
    assert cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", query)),
    )).host_hit_length == 4

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [leaf.id] and write_back and no_host_reclaim
        device, components = cache.tree_core.build_backup_spec(leaf.id)
        assert device is cache.tree_core._empty_match_result.device_indices
        assert device.numel() == 0 and set(components) == {2}
        cache.ongoing_write_through[leaf.id] = True
        return 0

    def acknowledge(*, write_back):
        assert write_back
        state.host_value = state.value.clone()
        cache.ongoing_write_through.clear()

    cache._execute_and_commit_kv_backup.side_effect = enqueue
    cache.writing_check.side_effect = acknowledge
    # Later accesses can make the retained state the next real LRU victim.
    lru = cache.tree_core.lru_lists[2]
    for node in cache.tree_core._node_arena.values():
        if node is not leaf and node.component_data[2].value is not None:
            lru.reset_node_mru(node)
    assert lru.get_lru_no_lock() is leaf
    cache._preserve_mamba_reentry_candidates(1)
    cache._execute_and_commit_kv_backup.assert_called_once()
    assert state.host_value is not None
    assert full.host_value is full_host and full.value is None
    result = cache.tree_core.demote_backed_mamba_state(leaf.id)
    cache._free_values(result.device_frees, result.host_frees)
    assert request_pool.mamba_allocator.available_size() == state_free + 1
    assert state.value is None
    assert cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", query)),
    )).host_hit_length == 4


def test_finite_path_cap_saves_checkpoint_before_releasing_full(native_cache):
    from sglang.srt.mem_cache.unified_cache.components import ComponentType

    cache, allocator, _, leaf = prepared_checkpoint(native_cache, incremental=True)
    cache.components[ComponentType.MAMBA].mamba_max_states_per_path = 2
    full, state = leaf.component_data[0], leaf.component_data[2]
    available = allocator.available_size()
    cache.beliefkv_join_pressure_candidates = ()
    cache.beliefkv_join_pressure_validator = lambda *_: False
    locks = {}

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [leaf.id] and not write_back and not no_host_reclaim
        device, components = cache.tree_core.build_backup_spec(leaf.id)
        assert device.numel() == 0 and set(components) == {2}
        state.host_value = state.value.clone()
        locks[leaf.id] = cache.inc_lock_ref(leaf.id).to_dec_params()
        cache.tree_core.mark_write_through_pending([leaf.id], leaf.id)
        cache.ongoing_write_through[leaf.id] = True
        return 0

    cache._execute_and_commit_kv_backup = Mock(side_effect=enqueue)
    cache.writing_check = Mock(side_effect=AssertionError("capacity copies stay asynchronous"))
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 4, mamba_slots=0,
    ) == {}
    assert allocator.available_size() == available
    assert full.value is not None and state.value is not None
    assert state.host_value is not None
    cache.writing_check.assert_not_called()
    cache.tree_core.finish_write_through([leaf.id], leaf.id)
    cache.ongoing_write_through.clear()
    cache.dec_lock_ref(leaf.id, locks[leaf.id])
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 4, mamba_slots=0,
    ) == {0: 4, 2: 1}
    cache._execute_and_commit_kv_backup.assert_called_once()
