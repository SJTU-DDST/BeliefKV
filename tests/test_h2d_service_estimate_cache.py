from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_service import NativeServiceSample, estimate_native_service
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def commit(runtime, *, size=100, delay=20., direction="h2d", status="completed", full=5):
    return runtime.on_native_transfer_commit(NS(
        direction=direction, status=status, child_commits=(),
        actual_bytes=size, submit_to_ack_ms=delay,
        num_tokens_by_pool=(("kv", full),),
    ))


def test_exact_size_and_shape_reuse_only_statistics():
    runtime = NativeAdmissionRuntime()
    try:
        for delay in (10., 20., 30.):
            commit(runtime, delay=delay)
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.estimate_native_service",
            wraps=estimate_native_service,
        ) as estimate:
            first = runtime._estimate_h2d_service(100, shape="full")
            assert runtime._estimate_h2d_service(100, shape="full") is first
            assert estimate.call_count == 1
            assert runtime._estimate_h2d_service(101, shape="full") != first
            assert estimate.call_count == 2
            runtime._estimate_h2d_service(100, shape="mamba")
            assert estimate.call_count == 4
        assert runtime.counts["h2d_service_estimate_reused"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("direction", ("h2d", "d2h"))
def test_new_receipt_invalidates_only_changed_direction_estimates(direction):
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime._estimate_h2d_service(100, shape="full") is None
        for delay in (10., 20., 30.):
            commit(runtime, delay=delay)
        expected = estimate_native_service(runtime._native_service_samples, 100, shape="full")
        assert runtime._estimate_h2d_service(100, shape="full") == expected
        runtime._estimate_h2d_service(1_000_000, shape="hybrid")
        assert len(runtime._h2d_service_estimates) == 2
        cached = dict(runtime._h2d_service_estimates)
        commit(runtime, direction=direction, delay=40.)
        if direction == "h2d":
            assert runtime._h2d_service_estimates == {}
        else:
            assert runtime._h2d_service_estimates == cached
        expected = estimate_native_service(runtime._native_service_samples, 100, shape="full")
        assert runtime._estimate_h2d_service(100, shape="full") == expected
    finally:
        runtime.close()


def test_full_history_rotation_invalidates_even_when_length_stays_constant():
    runtime = NativeAdmissionRuntime()
    try:
        for _ in range(256):
            commit(runtime, delay=10.)
        assert runtime._estimate_h2d_service(100, shape="full").submit_to_ack_p50_ms == 10.
        for _ in range(256):
            commit(runtime, delay=50.)
        assert len(runtime._native_service_samples) == 256
        assert runtime._estimate_h2d_service(100, shape="full").submit_to_ack_p50_ms == 50.
    finally:
        runtime.close()


def test_legacy_fallback_and_foreign_shape_semantics_are_preserved():
    runtime = NativeAdmissionRuntime()
    try:
        runtime._native_service_samples.extend(
            NativeServiceSample(100, delay, shape="mamba")
            for delay in (100., 200., 300.)
        )
        runtime._h2d_samples.extend((100, delay) for delay in (10., 20., 30.))
        expected = estimate_native_service(runtime._h2d_samples, 100)
        assert runtime._estimate_h2d_service(100, shape="full") == expected
        assert runtime._estimate_h2d_service(100, shape="full") == expected
        commit(runtime, delay=40.)
        expected = estimate_native_service(runtime._h2d_samples, 100)
        assert runtime._estimate_h2d_service(100, shape="full") == expected
    finally:
        runtime.close()


def test_query_storage_is_bounded_without_rejecting_new_queries():
    runtime = NativeAdmissionRuntime()
    try:
        for size in range(1, 520):
            assert runtime._estimate_h2d_service(size, shape="full") is None
            assert len(runtime._h2d_service_estimates) <= 256
        assert runtime.counts["h2d_service_estimate_computed"] == 519
    finally:
        runtime.close()
