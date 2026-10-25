from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.concur_feedback import ConcurFeedback


def feedback():
    cache = NS(
        tree_components=(0,),
        token_to_kv_pool_allocator=NS(size_full=1000, available_size=lambda: 100),
        evictable_size=lambda: 200,
    )
    return ConcurFeedback(cache)


def request(identity="one", **changes):
    values = dict(
        rid=identity, origin_input_ids=[1] * 100,
        cached_tokens_device=10, cached_tokens_host=80,
        _cache_breakdown_computed=True, retracted_stain=False,
    )
    return NS(**{**values, **changes})


def test_native_usage_matches_non_evictable_usage_and_records_total_allocation():
    state = feedback()
    sample = state.sample(0)
    assert sample.gpu_usage == .7
    assert sample.allocated_gpu_fraction == .9
    assert sample.gpu_hit_rate is None


def test_host_hits_cannot_mask_gpu_only_misses():
    state = feedback()
    state.observe_request(request())
    state.observe_request(request("two", cached_tokens_device=30, cached_tokens_host=60))
    sample = state.sample(0)
    assert sample.gpu_hit_rate == .2
    assert sample.prompt_tokens == 200 and sample.gpu_hit_tokens == 40
    assert sample.observed_requests == 2


def test_chunked_prefill_and_decode_do_not_count_the_same_prefix_repeatedly():
    state = feedback()
    req = request()
    for _ in range(10):
        state.observe_request(req)
    sample = state.sample(0)
    assert sample.observed_requests == 1 and sample.prompt_tokens == 100
    assert state.sample(1).gpu_hit_rate is None
    state.observe_request(req)
    assert state.sample(2).gpu_hit_rate is None


@pytest.mark.parametrize("change", (
    {"_cache_breakdown_computed": False}, {"retracted_stain": True},
    {"cached_tokens_device": -1}, {"cached_tokens_device": 101},
    {"cached_tokens_device": None}, {"cached_tokens_device": True},
))
def test_unknown_or_stale_prefix_evidence_does_not_become_a_zero_hit(change):
    state = feedback()
    state.observe_request(request(**change))
    sample = state.sample(0)
    assert sample.observed_requests == 0 and sample.gpu_hit_rate is None


def test_inconsistent_native_counters_preserve_unconsumed_prefix_observations():
    state = feedback()
    state.observe_request(request())
    state.cache.evictable_size = lambda: 1000
    assert state.sample(0) is None
    state.cache.evictable_size = lambda: 200
    assert state.sample(1).observed_requests == 1


def test_hybrid_cache_geometry_is_not_silently_mapped_to_full_only():
    with pytest.raises(ValueError, match="FULL-only"):
        ConcurFeedback(NS(
            tree_components=(0, 2),
            token_to_kv_pool_allocator=NS(size_full=1000),
        ))
