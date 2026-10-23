"""A FULL-only backup must not silently weaken native checkpoint retention."""

from array import array
from unittest.mock import Mock

import pytest

from tests.test_waiting_full_tail_backup import native_cache


def prepared_checkpoint(native_cache, *, incremental):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, request_pool = native_cache
    leaf, _ = insert_session(cache, [10, 11, 12, 13], "completed")
    cache.release_radix_session("completed")
    full, state = leaf.component_data[0], leaf.component_data[2]
    assert full.session_ref == state.session_ref == 0
    full.host_value = full.value.clone()
    cache.tree_core._update_duplicate_tracking(leaf)
    cache.tree_core.set_hicache_enabled()
    cache.is_write_back = True
    cache.host_memory_mode = "cache"
    cache.cache_controller.write_policy = "write_back"
    cache.ongoing_write_through = {}
    cache._beliefkv_incremental_full_enabled = incremental
    return cache, allocator, request_pool, leaf


@pytest.mark.parametrize("incremental", (False, True))
def test_actual_leaf_reclaim_preserves_a_checkpoint_skipped_by_incremental_full_backup(
    native_cache, incremental,
):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    cache, allocator, request_pool, leaf = prepared_checkpoint(
        native_cache, incremental=incremental,
    )
    full, state = leaf.component_data[0], leaf.component_data[2]
    full_host = full.host_value
    available = allocator.available_size()
    state_available = request_pool.mamba_allocator.available_size()
    order = []

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [leaf.id] and write_back and not no_host_reclaim
        device, components = cache.tree_core.build_backup_spec(leaf.id)
        assert device.numel() == 0 and set(components) == {2}
        assert full.host_value is full_host
        cache.ongoing_write_through[leaf.id] = True
        order.append("state_enqueued")
        return 0

    def acknowledge(*, write_back):
        assert write_back
        state.host_value = state.value.clone()
        cache.ongoing_write_through.clear()
        order.append("state_acknowledged")

    cache._execute_and_commit_kv_backup = Mock(side_effect=enqueue)
    cache.writing_check = Mock(side_effect=acknowledge)
    tracker = {}
    assert cache._evict_device_leaf(leaf.id, tracker) is None
    assert tracker == {0: 4, 2: 1}
    assert allocator.available_size() == available + 4
    assert request_pool.mamba_allocator.available_size() == state_available + 1
    assert full.value is None and full.host_value is full_host
    assert state.value is None
    match = cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [10, 11, 12, 13, 99])),
    ))
    if incremental:
        assert order == ["state_enqueued", "state_acknowledged"]
        assert state.host_value is not None
        assert match.host_hit_length == 4
    else:
        assert order == []
        assert state.host_value is None
        assert match.host_hit_length == 0
        cache._execute_and_commit_kv_backup.assert_not_called()


def test_critical_full_reclaim_waits_for_incremental_state_ack(native_cache):
    cache, allocator, request_pool, leaf = prepared_checkpoint(
        native_cache, incremental=True,
    )
    full, state = leaf.component_data[0], leaf.component_data[2]
    available = allocator.available_size()
    state_available = request_pool.mamba_allocator.available_size()
    locks = {}
    cache.beliefkv_join_pressure_candidates = ()
    cache.beliefkv_join_pressure_validator = lambda *_: False

    def enqueue(action, *, write_back, no_host_reclaim):
        assert not write_back and not no_host_reclaim
        assert action.node_ids == [leaf.id]
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
    assert state.value is not None and full.value is not None
    assert state.host_value is not None
    cache.writing_check.assert_not_called()
    cache.tree_core.finish_write_through([leaf.id], leaf.id)
    cache.ongoing_write_through.clear()
    cache.dec_lock_ref(leaf.id, locks[leaf.id])
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 4, mamba_slots=0,
    ) == {0: 4, 2: 1}
    assert allocator.available_size() == available + 4
    assert request_pool.mamba_allocator.available_size() == state_available + 1
    assert full.value is None and state.value is None and state.host_value is not None
    cache._execute_and_commit_kv_backup.assert_called_once()


@pytest.mark.parametrize("handoff", (False, True))
def test_host_allocation_failure_keeps_native_capacity_fallback(native_cache, handoff):
    cache, allocator, _, leaf = prepared_checkpoint(native_cache, incremental=True)
    available = allocator.available_size()
    cache.beliefkv_join_pressure_candidates = ()
    cache.beliefkv_join_pressure_validator = lambda *_: False
    cache._execute_and_commit_kv_backup = Mock(return_value=0)
    cache.writing_check = Mock()
    if handoff:
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=available + 4, mamba_slots=0,
        ) == {0: 4, 2: 1}
    else:
        tracker = {}
        cache._evict_device_leaf(leaf.id, tracker)
        assert tracker == {0: 4, 2: 1}
    assert allocator.available_size() == available + 4
    cache.writing_check.assert_not_called()
    assert cache.beliefkv_reentry_backup_counts == {
        "attempted": 1, "unreferenced_attempted": 1, "acked": 0, "declined": 1,
    }


@pytest.mark.parametrize("guard", (
    "backed", "state_locked", "pending_write", "pending_load",
    "not_write_back", "no_session_cache", "no_controller", "buffer_mode",
))
def test_incremental_state_preservation_keeps_existing_guards(native_cache, guard):
    cache, allocator, _, leaf = prepared_checkpoint(native_cache, incremental=True)
    state = leaf.component_data[2]
    if guard == "backed":
        state.host_value = state.value.clone()
    elif guard == "state_locked":
        state.lock_ref = 1
    elif guard == "pending_write":
        leaf.write_through_pending_id = 99
    elif guard == "pending_load":
        leaf.load_back_pending_id = 99
    elif guard == "not_write_back":
        cache.is_write_back = False
    elif guard == "no_session_cache":
        cache.enable_session_radix_cache = False
    elif guard == "no_controller":
        cache.cache_controller = None
    else:
        cache.host_memory_mode = "buffer"
    available = allocator.available_size()
    value = state.value
    cache._execute_and_commit_kv_backup = Mock()
    cache._preserve_mamba_reentry_node(
        leaf, no_host_reclaim=False, preserve_unreferenced=True,
    )
    cache._execute_and_commit_kv_backup.assert_not_called()
    assert allocator.available_size() == available and state.value is value


def inherited_checkpoint(native_cache):
    from types import SimpleNamespace as NS
    import torch
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _ = native_cache
    older, generation = insert_session(cache, [1, 2, 3, 4], "waiting")
    current, _ = insert_session(cache, list(range(1, 9)), "waiting")
    values = torch.cat((current.component_data[0].value, allocator.alloc(2)))
    tail = cache.tree_core.node_by_id(cache.insert(InsertParams(
        key=RadixKey(array("q", range(1, 11))), value=values,
        prev_prefix_len=8, mamba_value=cache.req_to_token_pool.mamba_allocator.alloc(1),
    )).last_device_node)
    cache.session_refs.register_session_ref(NS(
        session_id="waiting", session_generation=generation, session=None,
        last_node=tail.id, origin_input_ids=array("q", range(1, 10)),
        output_ids=array("q", [10]), extra_key=None, beliefkv_metadata={},
    ))
    assert older.component_data[0].session_ref == 1
    assert older.component_data[0].session_ids is None
    assert older.component_data[2].session_ref == 0
    assert current.component_data[2].session_ref == 1
    assert tail.component_data[0].session_ids == {"waiting"}
    for node in (older, current, tail):
        full = node.component_data[0]
        full.host_value = full.value.clone()
        cache.tree_core._update_duplicate_tracking(node)
    cache.tree_core.set_hicache_enabled()
    cache.is_write_back = True
    cache.host_memory_mode = "cache"
    cache.cache_controller.write_policy = "write_back"
    cache.ongoing_write_through = {}
    cache._beliefkv_incremental_full_enabled = True
    for node in (tail, current):
        result = cache.tree_core.demote_backed_full_preserving_mamba(node.id)
        cache._free_values(result.device_frees, result.host_frees)
    return cache, allocator, older, current


def test_inherited_prefix_checkpoint_is_saved_before_critical_full_reclaim(native_cache):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    cache, allocator, older, current = inherited_checkpoint(native_cache)
    available = allocator.available_size()
    state = older.component_data[2]
    current_state = current.component_data[2].value
    cache.beliefkv_join_pressure_candidates_by_component = {
        0: ((older.id, older.creation_time),),
    }
    cache.beliefkv_join_pressure_validator = lambda node_id, created: (
        node_id, created,
    ) == (older.id, older.creation_time)
    locks = {}

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [older.id] and not write_back and not no_host_reclaim
        device, components = cache.tree_core.build_backup_spec(older.id)
        assert device.numel() == 0 and set(components) == {2}
        state.host_value = state.value.clone()
        locks[older.id] = cache.inc_lock_ref(older.id).to_dec_params()
        cache.tree_core.mark_write_through_pending([older.id], older.id)
        cache.ongoing_write_through[older.id] = True
        return 0

    cache._execute_and_commit_kv_backup = Mock(side_effect=enqueue)
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 4, mamba_slots=0,
    ) == {}
    assert allocator.available_size() == available
    assert state.value is not None and state.host_value is not None
    cache.tree_core.finish_write_through([older.id], older.id)
    cache.ongoing_write_through.clear()
    cache.dec_lock_ref(older.id, locks[older.id])
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 4, mamba_slots=0,
    ) == {0: 4, 2: 1}
    assert current.component_data[2].value is current_state
    assert cache.match_prefix(MatchPrefixParams(
        key=RadixKey(array("q", [1, 2, 3, 4, 5, 99])),
    )).host_hit_length == 4
    cache._execute_and_commit_kv_backup.assert_called_once()


def test_state_pressure_completes_the_backup_of_a_host_only_full_prefix(native_cache):
    cache, allocator, older, _ = inherited_checkpoint(native_cache)
    result = cache.tree_core.demote_backed_full_preserving_mamba(older.id)
    cache._free_values(result.device_frees, result.host_frees)
    assert cache.tree_core.lru_lists[2].get_lru_no_lock() is older
    full = older.component_data[0]
    state = older.component_data[2]
    full_host = full.host_value
    available = allocator.available_size()

    def enqueue(action, *, write_back, no_host_reclaim):
        assert action.node_ids == [older.id] and write_back and no_host_reclaim
        assert full.value is None and full.host_value is full_host
        device, components = cache.tree_core.build_backup_spec(older.id)
        assert device is cache.tree_core._empty_match_result.device_indices
        assert device.numel() == 0 and set(components) == {2}
        cache.ongoing_write_through[older.id] = True
        return 0

    def acknowledge(*, write_back):
        assert write_back
        state.host_value = state.value.clone()
        cache.ongoing_write_through.clear()

    cache._execute_and_commit_kv_backup = Mock(side_effect=enqueue)
    cache.writing_check = Mock(side_effect=acknowledge)
    cache._preserve_mamba_reentry_candidates(1)
    assert state.host_value is not None
    assert allocator.available_size() == available
    assert full.value is None and full.host_value is full_host
    cache._execute_and_commit_kv_backup.assert_called_once()
