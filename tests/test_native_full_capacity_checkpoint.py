"""Native FULL allocation should retain independent state until it needs space."""

from array import array
from unittest.mock import Mock

import pytest

from tests.test_full_backup_checkpoint_durability import prepared_checkpoint
from tests.test_waiting_full_tail_backup import native_cache


@pytest.mark.parametrize("allocation", (False, True))
def test_native_full_shortfall_defers_state_copy_until_actual_state_pressure(
    native_cache, allocation,
):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams, MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    cache, allocator, request_pool, leaf = prepared_checkpoint(
        native_cache, incremental=True,
    )
    full, state = leaf.component_data[0], leaf.component_data[2]
    full_host, state_device = full.host_value, state.value
    full_free = allocator.available_size()
    state_free = request_pool.mamba_allocator.available_size()
    cache._execute_and_commit_kv_backup = Mock(
        side_effect=AssertionError("FULL allocation must not wait for state DMA"),
    )
    cache.writing_check = Mock()
    evict = cache.evict_for_alloc if allocation else cache.evict
    result = evict(EvictParams(num_tokens=4))

    assert result.num_tokens_evicted == 4 and result.mamba_num_evicted == 0
    assert allocator.available_size() == full_free + 4
    assert request_pool.mamba_allocator.available_size() == state_free
    assert full.value is None and full.host_value is full_host
    assert state.value is state_device and state.host_value is None
    cache._execute_and_commit_kv_backup.assert_not_called()
    assert cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [10, 11, 12, 13, 99])),
    )).host_hit_length == 4

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [leaf.id] and write_back and no_host_reclaim
        device, components = cache.tree_core.build_backup_spec(leaf.id)
        assert device.numel() == 0 and set(components) == {2}
        cache.ongoing_write_through[leaf.id] = True
        return 0

    def acknowledge(*, write_back):
        assert write_back
        state.host_value = state_device.clone()
        cache.ongoing_write_through.clear()

    cache._execute_and_commit_kv_backup.side_effect = enqueue
    cache.writing_check.side_effect = acknowledge
    result = evict(EvictParams(mamba_num=1))
    assert result.num_tokens_evicted == 0 and result.mamba_num_evicted == 1
    assert request_pool.mamba_allocator.available_size() == state_free + 1
    assert state.value is None and state.host_value is not None
    assert full.value is None and full.host_value is full_host
    cache._execute_and_commit_kv_backup.assert_called_once()
    assert cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [10, 11, 12, 13, 99])),
    )).host_hit_length == 4


@pytest.mark.parametrize("guard", ("state_pressure", "path_cap", "incremental_off"))
def test_native_full_state_retention_preserves_existing_policy_boundaries(
    native_cache, guard,
):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams
    from sglang.srt.mem_cache.unified_cache.components import ComponentType

    cache, allocator, request_pool, leaf = prepared_checkpoint(
        native_cache, incremental=guard != "incremental_off",
    )
    if guard == "path_cap":
        cache.components[ComponentType.MAMBA].mamba_max_states_per_path = 2
    full, state = leaf.component_data[0], leaf.component_data[2]
    full_free = allocator.available_size()
    state_free = request_pool.mamba_allocator.available_size()

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [leaf.id] and write_back and not no_host_reclaim
        device, components = cache.tree_core.build_backup_spec(leaf.id)
        assert device.numel() == 0 and set(components) == {2}
        cache.ongoing_write_through[leaf.id] = True
        return 0

    def acknowledge(*, write_back):
        assert write_back
        if leaf.id in cache.ongoing_write_through:
            state.host_value = state.value.clone()
            cache.ongoing_write_through.clear()

    cache._execute_and_commit_kv_backup = Mock(side_effect=enqueue)
    cache.writing_check = Mock(side_effect=acknowledge)
    result = cache.evict_for_alloc(EvictParams(
        num_tokens=4, mamba_num=1 if guard == "state_pressure" else 0,
    ))
    assert result.num_tokens_evicted == 4 and result.mamba_num_evicted == 1
    assert allocator.available_size() == full_free + 4
    assert request_pool.mamba_allocator.available_size() == state_free + 1
    assert full.value is None and state.value is None
    if guard == "incremental_off":
        cache._execute_and_commit_kv_backup.assert_not_called()
        assert state.host_value is None
    else:
        cache._execute_and_commit_kv_backup.assert_called_once()
        assert state.host_value is not None


def test_native_full_walk_can_reclaim_multiple_backed_leaves(native_cache):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, request_pool, first = prepared_checkpoint(
        native_cache, incremental=True,
    )
    second, _ = insert_session(cache, [20, 21, 22, 23], "second")
    cache.release_radix_session("second")
    second.component_data[0].host_value = second.component_data[0].value.clone()
    cache.tree_core._update_duplicate_tracking(second)
    states = [leaf.component_data[2].value for leaf in (first, second)]
    full_free = allocator.available_size()
    state_free = request_pool.mamba_allocator.available_size()
    cache._execute_and_commit_kv_backup = Mock(
        side_effect=AssertionError("backed FULL leaves need no state transfer"),
    )
    cache.writing_check = Mock()

    result = cache.evict_for_alloc(EvictParams(num_tokens=8))
    assert result.num_tokens_evicted == 8 and result.mamba_num_evicted == 0
    assert allocator.available_size() == full_free + 8
    assert request_pool.mamba_allocator.available_size() == state_free
    for leaf, state in zip((first, second), states):
        assert leaf.component_data[0].value is None
        assert leaf.component_data[2].value is state
        assert leaf.component_data[2].host_value is None
    cache._execute_and_commit_kv_backup.assert_not_called()


def test_full_walk_funding_a_state_target_keeps_native_cascade(native_cache):
    from sglang.srt.mem_cache.unified_cache.components import ComponentType

    cache, _, request_pool, leaf = prepared_checkpoint(
        native_cache, incremental=True,
    )
    state = leaf.component_data[2]
    state_free = request_pool.mamba_allocator.available_size()
    donor = Mock()
    donor.full_tokens_before_mamba_recheck.return_value = 1
    cache.token_to_kv_pool_allocator.mamba_full_cache_donor = Mock(
        return_value=donor,
    )

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [leaf.id] and write_back and not no_host_reclaim
        cache.ongoing_write_through[leaf.id] = True
        return 0

    def acknowledge(*, write_back):
        assert write_back
        state.host_value = state.value.clone()
        cache.ongoing_write_through.clear()

    cache._execute_and_commit_kv_backup = Mock(side_effect=enqueue)
    cache.writing_check = Mock(side_effect=acknowledge)
    tracker = {ct: 0 for ct in cache.tree_components}
    cache._evict_components(
        {ct: 4 if ct == ComponentType.FULL else 0 for ct in cache.tree_components},
        tracker,
        {ComponentType.FULL: (ComponentType.MAMBA, state_free + 1)},
    )
    assert tracker[ComponentType.FULL] == 4 and tracker[ComponentType.MAMBA] == 1
    assert request_pool.mamba_allocator.available_size() == state_free + 1
    assert state.value is None and state.host_value is not None
    cache._execute_and_commit_kv_backup.assert_called_once()
