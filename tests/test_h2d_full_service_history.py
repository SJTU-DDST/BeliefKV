from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_service import estimate_native_service
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def receipt(runtime, *, delay=20., size=100, direction="h2d", full=1, mamba=0,
            status="completed"):
    runtime.on_native_transfer_commit(NS(
        direction=direction, status=status, child_commits=(), actual_bytes=size,
        submit_to_ack_ms=delay, num_tokens_by_pool=(("kv", full), ("mamba", mamba)),
    ))


def rotate_mixed_histories(runtime):
    for _ in range(256):
        receipt(runtime, size=10_000, full=1, mamba=1)


def test_fresh_full_only_receipts_survive_other_shapes_without_changing_primary():
    runtime = NativeAdmissionRuntime()
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=100.):
            for delay in (10., 20., 30.):
                receipt(runtime, delay=delay)
            primary = estimate_native_service(runtime._native_service_samples, 100, shape="full")
            assert runtime._estimate_h2d_service(100, shape="full") == primary
            assert not runtime.counts["h2d_full_service_fallback_computed"]
            rotate_mixed_histories(runtime)
            assert estimate_native_service(runtime._native_service_samples, 100, shape="full") is None
            assert estimate_native_service(runtime._h2d_samples, 100) is None
            assert runtime._estimate_h2d_service(100, shape="full") == primary
            assert runtime.counts["h2d_full_service_fallback_supported"] == 1
            assert runtime._estimate_h2d_service(100, shape="full") == primary
            assert runtime.counts["h2d_full_service_fallback_computed"] == 1
            assert runtime._estimate_h2d_service(100, shape="mamba") is None
            assert runtime._estimate_h2d_service(100, shape="hybrid") is None
            assert runtime._estimate_d2h_service(100, shape="full") is None
    finally:
        runtime.close()


def test_fallback_expires_without_a_new_ack_and_new_receipts_restore_support():
    runtime = NativeAdmissionRuntime()
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic") as clock:
            for now, delay in ((100., 10.), (101., 20.), (102., 30.)):
                clock.return_value = now
                receipt(runtime, delay=delay)
            rotate_mixed_histories(runtime)
            clock.return_value = 400.
            assert runtime._estimate_h2d_service(100, shape="full") is not None
            clock.return_value = 400.001
            assert runtime._estimate_h2d_service(100, shape="full") is None
            assert len(runtime._h2d_full_service_samples) == 2
            receipt(runtime, delay=40.)
            assert runtime._estimate_h2d_service(100, shape="full").submit_to_ack_p50_ms == 30.
    finally:
        runtime.close()


def test_primary_and_legacy_estimates_keep_precedence_over_supplement():
    runtime = NativeAdmissionRuntime()
    try:
        for delay in (100., 200., 300.):
            receipt(runtime, delay=delay)
        rotate_mixed_histories(runtime)
        runtime._h2d_samples.clear()
        runtime._h2d_samples.extend((100, delay) for delay in (10., 20., 30.))
        expected = estimate_native_service(runtime._h2d_samples, 100)
        assert runtime._estimate_h2d_service(100, shape="full") == expected
        assert runtime.counts["h2d_full_service_fallback_computed"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("delays", ((10.,), (10., 20.)))
def test_minimum_support_and_size_range_are_preserved(delays):
    runtime = NativeAdmissionRuntime()
    try:
        for delay in delays:
            receipt(runtime, delay=delay)
        rotate_mixed_histories(runtime)
        assert runtime._estimate_h2d_service(100, shape="full") is None
        assert runtime._estimate_h2d_service(10_000_000, shape="full") is None
    finally:
        runtime.close()


def test_supplement_is_bounded_and_ignores_invalid_and_foreign_receipts():
    runtime = NativeAdmissionRuntime()
    try:
        for delay in range(1, 65):
            receipt(runtime, delay=float(delay))
        assert len(runtime._h2d_full_service_samples) == 32
        assert [sample.submit_to_ack_ms for _, sample in runtime._h2d_full_service_samples] == [
            float(delay) for delay in range(33, 65)
        ]
        before = tuple(runtime._h2d_full_service_samples)
        for kwargs in (
            {"status": "failed"}, {"delay": float("nan")}, {"size": 0},
            {"delay": 60_001.}, {"direction": "d2h"},
            {"mamba": 1}, {"full": 0, "mamba": 1}, {"full": 0},
        ):
            receipt(runtime, **kwargs)
        assert tuple(runtime._h2d_full_service_samples) == before
    finally:
        runtime.close()
