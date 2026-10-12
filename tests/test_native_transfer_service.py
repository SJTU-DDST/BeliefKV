import pytest

from beliefkv.runtime.native_transfer_service import (
    NativeServiceSample, estimate_native_service, pool_shape,
)


def test_small_ack_polling_cost_is_not_multiplied_by_large_transfer_bytes():
    samples = [(532_480, 100.), (70_000_000, 45.), (72_000_000, 50.), (68_000_000, 55.)]
    result = estimate_native_service(samples, 80_000_000)
    assert result.sample_count == 3
    assert result.submit_to_ack_p90_ms < 60.
    assert result.enqueue_to_submit_p90_ms is None


def test_mamba_full_and_hybrid_samples_are_conditioned_separately():
    samples = [
        NativeServiceSample(80_000_000, delay, shape=shape)
        for shape, delay in (("hybrid", 50.), ("hybrid", 55.), ("hybrid", 60.),
                             ("full", 500.), ("full", 600.), ("full", 700.))
    ]
    assert estimate_native_service(samples, 80_000_000, shape="hybrid").submit_to_ack_p90_ms == 59.
    assert estimate_native_service(samples, 80_000_000, shape="mamba") is None
    assert pool_shape(1, 1) == "hybrid"
    assert pool_shape(0, 1) == "mamba"


def test_unknown_transfer_size_requires_evidence_instead_of_linear_extrapolation():
    assert estimate_native_service([(10, 100.)] * 3, 80_000_000) is None
    with pytest.raises(ValueError):
        NativeServiceSample(100, float("nan"))


@pytest.mark.parametrize("shape", ("full", "mamba", "hybrid"))
def test_foreign_shapes_do_not_displace_legacy_fallback_or_exact_support(shape):
    samples = [
        NativeServiceSample(80_000_000, 1., shape=foreign)
        for foreign in ("full", "mamba", "hybrid") if foreign != shape
        for _ in range(16)
    ] + [(80_000_000, delay) for delay in (40., 50., 60.)]
    fallback = estimate_native_service(samples, 80_000_000, shape=shape)
    assert fallback.sample_count == 3
    assert fallback.submit_to_ack_p90_ms == 58.
    assert fallback.support == "nearby_size_with_legacy_shape"
    samples.extend(
        NativeServiceSample(80_000_000, delay, shape=shape)
        for delay in (20., 25., 30.)
    )
    matched = estimate_native_service(samples, 80_000_000, shape=shape)
    assert matched.sample_count == 3
    assert matched.submit_to_ack_p90_ms == 29.
    assert matched.support == "matched_pool_shape_and_size"
