"""Native allocation must reuse cold pages made evictable by a pending D2H ACK."""

import pytest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from tests.test_full_only_native_lifecycle import full_native


def native_write_start(cache):
    deferred_write = cache.cache_controller.write

    def write(device_indices, **kwargs):
        deferred = kwargs.get("beliefkv_defer_writing", False)
        result = deferred_write(device_indices, **{**kwargs, "beliefkv_defer_writing": True})
        if not deferred:
            cache.cache_controller.start_writing()
        return result

    # Native write-back starts immediately; PREPARE explicitly starts its batch.
    cache.cache_controller.write = write


@pytest.mark.parametrize("ack_before_allocation", (False, True))
def test_native_allocation_retries_cold_pages_after_prepare_ack(
    full_native, ack_before_allocation,
):
    from sglang.srt.mem_cache.allocation import alloc_token_slots
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    native_write_start(cache)
    cache.tree_core.set_hicache_enabled()
    assert cache.enable_beliefkv_incremental_prepare()
    cold, generation = insert_session(cache, list(range(1, 33)), "cold")
    active, _ = insert_session(cache, list(range(100, 128)), "active")
    active_lock = cache.inc_lock_ref(active.id)
    assert allocator.available_size() == 4
    assert cache.prepare_beliefkv_spawn_full(
        session_id="cold", session_generation=generation,
    )["issued"]
    if ack_before_allocation:
        cache.writing_check(finish_count=1)
    try:
        allocated = alloc_token_slots(cache, 8)
        assert allocated is not None and len(allocated) == 8
        assert cold.component_data[0].value is None
        assert cold.component_data[0].host_value is not None
        assert not cache.ongoing_write_through
        assert active.component_data[0].value is not None
        assert active.component_data[0].lock_ref == 1
        assert len(writes) == 1 and not loads
        allocator.free(allocated)
    finally:
        cache.dec_lock_ref(active.id, active_lock.to_dec_params())


@pytest.mark.parametrize("other_victim_tokens", (1, 4))
def test_eviction_of_another_branch_can_complete_prepare_inside_the_first_walk(
    full_native, other_victim_tokens,
):
    from sglang.srt.mem_cache.allocation import alloc_token_slots
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    native_write_start(cache)
    cache.tree_core.set_hicache_enabled()
    assert cache.enable_beliefkv_incremental_prepare()
    cold, generation = insert_session(cache, list(range(1, 33)), "cold")
    other, _ = insert_session(cache, list(range(200, 200 + other_victim_tokens)), "other")
    active, _ = insert_session(cache, list(range(100, 128 - other_victim_tokens)), "active")
    active_lock = cache.inc_lock_ref(active.id)
    assert allocator.available_size() == 4
    assert cache.prepare_beliefkv_spawn_full(
        session_id="cold", session_generation=generation,
    )["issued"]
    try:
        allocated = alloc_token_slots(cache, 8)
        assert len(allocated) == 8
        assert other.component_data[0].value is None
        assert (cold.component_data[0].value is None) == (other_victim_tokens < 4)
        assert cold.component_data[0].host_value is not None
        assert active.component_data[0].lock_ref == 1
        assert len(writes) == 2 and not loads
        allocator.free(allocated)
    finally:
        cache.dec_lock_ref(active.id, active_lock.to_dec_params())


@pytest.mark.parametrize("custom,pending,write_policy,passes", (
    (False, True, "write_back", 1),
    (False, False, "write_back", 1),
    (True, True, "write_back", 2),
    (True, False, "write_back", 2),
    (True, True, "write_through", 1),
    ("unproven", True, "write_back", 1),
))
def test_prepare_ack_retry_is_gated_and_bounded(
    custom, pending, write_policy, passes, monkeypatch,
):
    import os
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(os.environ["SGLANG_SOURCE_CHECKOUT"]) / "python"))
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams
    from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
    from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache

    cache = NS(
        disable=False, tree_components=(ComponentType.FULL,),
        _beliefkv_incremental_full_enabled=custom,
        ongoing_write_through={1: object()} if pending else {},
        cache_controller=NS(write_policy=write_policy),
        _evict_request_by_type=lambda params: {ComponentType.FULL: params.num_tokens},
        _evict_components=Mock(), update_eviction_metrics=Mock(),
    )
    cache.writing_check = Mock(side_effect=lambda **kwargs: cache.ongoing_write_through.clear())
    result = UnifiedRadixCache._evict(cache, EvictParams(num_tokens=8))
    assert cache._evict_components.call_count == passes
    assert cache.writing_check.call_count == int(write_policy == "write_back")
    assert result.num_tokens_evicted == 0
