"""Smaller FULL copies can retain larger-copy costs without size extrapolation."""

from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_service import (
    NativeServiceSample, estimate_larger_full_h2d, estimate_native_service,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def sample(size=100_000_000, delay=40., **kwargs):
    return NativeServiceSample(size, delay, shape="full", **kwargs)


def test_small_full_estimate_keeps_measured_polling_and_queue_cost():
    samples = [
        sample(delay=delay, enqueue_to_submit_ms=queue)
        for delay, queue in ((30., 2.), (40., 4.), (50., 8.))
    ]
    assert estimate_native_service(samples, 1_000_000, shape="full") is None
    estimate = estimate_larger_full_h2d(samples, 1_000_000)
    assert estimate.submit_to_ack_p50_ms == 40.
    assert estimate.submit_to_ack_p90_ms == 48.
    assert estimate.enqueue_to_submit_p90_ms == 7.2
    assert estimate.sample_count == 3
    assert estimate.support == "measured_larger_full_payload_without_downscaling"


@pytest.mark.parametrize("excluded", (
    "too_few", "larger_target", "d2h", "hybrid", "mamba", "unknown",
    "zero", "negative", "boolean",
))
def test_only_supported_larger_pure_full_h2d_can_supply_the_estimate(excluded):
    samples, size = [sample()] * 3, 1_000_000
    if excluded == "too_few":
        samples.pop()
    elif excluded == "larger_target":
        size = 100_000_001
    elif excluded == "d2h":
        samples = [sample(direction="d2h")] * 3
    elif excluded in ("hybrid", "mamba", "unknown"):
        samples = [NativeServiceSample(100_000_000, 40., shape=excluded)] * 3
    else:
        size = {"zero": 0, "negative": -1, "boolean": True}[excluded]
    assert estimate_larger_full_h2d(samples, size) is None


def test_nearest_larger_samples_are_bounded_and_foreign_samples_are_ignored():
    samples = [
        sample(size=(index + 1) * 100_000_000, delay=float(index + 1))
        for index in range(32)
    ]
    samples.extend([NativeServiceSample(1_000_000, 999., shape="hybrid")] * 100)
    estimate = estimate_larger_full_h2d(reversed(samples), 1_000_000)
    assert estimate.sample_count == 16
    assert estimate.submit_to_ack_p50_ms == 8.5
    assert estimate.submit_to_ack_p90_ms == 14.5


def test_runtime_fallback_is_cached_and_expires_without_more_receipts():
    runtime = NativeAdmissionRuntime()
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic") as clock:
            clock.return_value = 100.
            for delay in (30., 40., 50.):
                runtime.on_native_transfer_commit(NS(
                    direction="h2d", status="completed", child_commits=(),
                    actual_bytes=100_000_000, submit_to_ack_ms=delay,
                    num_tokens_by_pool=(("kv", 100), ("mamba", 0)),
                ))
            # The primary local estimator retains its original size support.
            assert estimate_native_service(
                runtime._native_service_samples, 1_000_000, shape="full",
            ) is None
            estimate = runtime._estimate_h2d_service(1_000_000, shape="full")
            assert estimate.submit_to_ack_p90_ms == 48.
            assert runtime._estimate_h2d_service(1_000_000, shape="full") is estimate
            assert runtime.counts["h2d_larger_full_payload_supported"] == 1
            assert runtime._estimate_h2d_service(1_000_000, shape="mamba") is None
            assert runtime._estimate_d2h_service(1_000_000, shape="full") is None
            clock.return_value = 400.001
            assert runtime._estimate_h2d_service(1_000_000, shape="full") is None
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()
