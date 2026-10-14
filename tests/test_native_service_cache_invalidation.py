"""Transfer-cache invalidation follows the evidence actually changed by ACKs."""

from collections import deque
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.native_transfer_service import (
    NativeServiceSample,
    estimate_native_service,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def receipt(direction, *, size=20480, delay=30., shape="full"):
    return NS(
        status="completed", direction=direction, node_ids=(), child_commits=(),
        actual_bytes=size, submit_to_ack_ms=delay, enqueue_to_submit_ms=0.,
        num_tokens_by_pool=(
            ("kv", int(shape in ("full", "hybrid"))),
            ("mamba", int(shape in ("mamba", "hybrid"))),
        ),
    )


@pytest.mark.parametrize("direction", ("h2d", "d2h"))
@pytest.mark.parametrize("supported", (False, True))
@pytest.mark.parametrize("maxlen", (None, 8))
def test_opposite_receipt_preserves_supported_and_unsupported_estimates(
    direction, supported, maxlen,
):
    runtime = NativeAdmissionRuntime()
    opposite = "d2h" if direction == "h2d" else "h2d"
    runtime._native_service_samples = deque(
        (NativeServiceSample(20480, delay, direction, "full")
         for delay in ((10., 20., 30.) if supported else (10., 20.))),
        maxlen=maxlen,
    )
    estimate = getattr(runtime, f"_estimate_{direction}_service")
    try:
        before = estimate(20480, shape="full")
        assert (before is not None) is supported
        computations = runtime.counts[f"{direction}_service_estimate_computed"]
        runtime.on_native_transfer_commit(receipt(opposite))
        assert estimate(20480, shape="full") is before
        assert runtime.counts[f"{direction}_service_estimate_computed"] == computations
        assert runtime.counts[f"{direction}_service_cache_retained_after_{opposite}"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("direction", ("h2d", "d2h"))
def test_same_direction_receipt_invalidates_negative_support_cache(direction):
    runtime = NativeAdmissionRuntime()
    runtime._native_service_samples.extend(
        NativeServiceSample(20480, delay, direction, "full")
        for delay in (10., 20.)
    )
    estimate = getattr(runtime, f"_estimate_{direction}_service")
    try:
        assert estimate(20480, shape="full") is None
        runtime.on_native_transfer_commit(receipt(direction))
        actual = estimate(20480, shape="full")
        assert actual is not None and actual.sample_count == 3
        assert actual.submit_to_ack_p90_ms == 28.
        assert runtime.counts[f"{direction}_service_estimate_computed"] == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("incoming", ("h2d", "d2h"))
def test_shared_fifo_eviction_invalidates_both_changed_directions(incoming):
    runtime = NativeAdmissionRuntime()
    opposite = "d2h" if incoming == "h2d" else "h2d"
    runtime._native_service_samples = deque(
        [NativeServiceSample(20480, 10., opposite, "full")]
        + [NativeServiceSample(20480, delay, incoming, "full")
           for delay in (10., 20., 30.)]
        + [NativeServiceSample(20480, delay, opposite, "full")
           for delay in (20., 30.)],
        maxlen=6,
    )
    methods = {
        direction: getattr(runtime, f"_estimate_{direction}_service")
        for direction in ("h2d", "d2h")
    }
    try:
        before = {
            direction: method(20480, shape="full")
            for direction, method in methods.items()
        }
        assert all(value is not None for value in before.values())
        runtime.on_native_transfer_commit(receipt(incoming, delay=50.))
        for direction, method in methods.items():
            actual = method(20480, shape="full")
            expected = estimate_native_service(
                runtime._native_service_samples, 20480, direction=direction, shape="full",
            )
            assert actual == expected
            assert runtime.counts[f"{direction}_service_estimate_computed"] == 2
        assert methods[opposite](20480, shape="full") is None
    finally:
        runtime.close()


def test_retained_full_cache_still_expires_supplement_without_new_h2d(monkeypatch):
    runtime = NativeAdmissionRuntime()
    clock = [1000.]
    monkeypatch.setattr(
        "beliefkv.runtime.sglang_v0520_runtime.time.monotonic", lambda: clock[0],
    )
    try:
        for delay in (10., 20., 30.):
            runtime.on_native_transfer_commit(receipt("h2d", delay=delay))
        for _ in range(256):
            runtime.on_native_transfer_commit(receipt("d2h", shape="hybrid"))
        runtime._h2d_samples.clear()
        first = runtime._estimate_h2d_service(20480, shape="full")
        assert first is not None and first.sample_count == 3
        clock[0] += 301.
        runtime.on_native_transfer_commit(receipt("d2h", shape="hybrid"))
        assert runtime._estimate_h2d_service(20480, shape="full") is None
        assert runtime.counts["h2d_full_service_samples_expired"] == 3
    finally:
        runtime.close()


@pytest.mark.parametrize("maxlen", (7, 32, 256))
def test_interleaved_receipts_match_uncached_estimates_at_every_query(maxlen):
    runtime = NativeAdmissionRuntime()
    runtime._native_service_samples = deque(maxlen=maxlen)
    shapes = ("full", "mamba", "hybrid", "unknown")
    sizes = (20480, 40960, 81920)
    try:
        for index in range(120):
            direction = "h2d" if index % 11 in (0, 1, 2) else "d2h"
            runtime.on_native_transfer_commit(receipt(
                direction, size=sizes[index % len(sizes)],
                delay=float(index % 29 + 1), shape=shapes[index % len(shapes)],
            ))
            for query_direction in ("h2d", "d2h"):
                method = getattr(runtime, f"_estimate_{query_direction}_service")
                for shape in shapes:
                    size = sizes[(index + 1) % len(sizes)]
                    expected = estimate_native_service(
                        runtime._native_service_samples, size,
                        direction=query_direction, shape=shape,
                    )
                    if query_direction == "h2d":
                        expected = expected or estimate_native_service(
                            runtime._h2d_samples, size,
                        )
                        if expected is None and shape == "full":
                            expected = estimate_native_service(
                                (sample for _, sample in runtime._h2d_full_service_samples),
                                size, shape="full",
                            )
                    assert method(size, shape=shape) == expected
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()
