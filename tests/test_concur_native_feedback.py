import pytest

from beliefkv.runtime.concur_feedback import ConcurFeedback
from tests.test_full_only_native_lifecycle import full_native


def test_native_gpu_usage_excludes_reclaimable_real_tree_pages(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, _, _ = full_native
    feedback = ConcurFeedback(cache)
    node, _ = insert_session(cache, [1, 2, 3, 4], "session")
    sample = feedback.sample(0)
    assert sample.gpu_usage == 0.
    assert sample.allocated_gpu_fraction == pytest.approx(4 / allocator.size_full)
    receipt = cache.inc_lock_ref(node.id)
    try:
        sample = feedback.sample(1)
        assert sample.gpu_usage == pytest.approx(4 / allocator.size_full)
    finally:
        cache.dec_lock_ref(node.id, receipt.to_dec_params())
    assert feedback.sample(2).gpu_usage == 0.


def test_native_shared_prefix_is_counted_once_in_congestion_usage(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, _, _ = full_native
    first, _ = insert_session(cache, [1, 2, 3, 4], "first")
    second, _ = insert_session(cache, [1, 2, 5, 6], "second")
    receipts = [cache.inc_lock_ref(node.id) for node in (first, second)]
    try:
        feedback = ConcurFeedback(cache)
        sample = feedback.sample(0)
        assert sample.gpu_usage == pytest.approx(6 / allocator.size_full)
        assert sample.allocated_gpu_fraction == pytest.approx(6 / allocator.size_full)
    finally:
        for node, receipt in zip((first, second), receipts):
            cache.dec_lock_ref(node.id, receipt.to_dec_params())
