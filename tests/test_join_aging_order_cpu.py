"""Preserve aged FIFO, ties and the exact ten-second threshold in simple Full."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_join_priority_kv_cpu import fixture, pinned_planner


@pytest.mark.parametrize("mode", ("full", "join_priority_only"))
@pytest.mark.parametrize("ages,expected", (
    ((0., 9., 8., 7.), (0, 1, 2, 3)),
    ((0., 10., 9.999, 10.001), (3, 1, 0, 2)),
    ((11., 13., 13., 12.), (1, 2, 3, 0)),
    ((None, 10., None, 10.), (1, 3, 0, 2)),
    ((0., -1., 10., 9.), (2, 0, 1, 3)),
    ((10., 10., 10., 10.), (0, 1, 2, 3)),
))
def test_simple_order_preserves_native_ties_missing_arrivals_and_threshold(mode, ages, expected):
    runtime, queue = fixture(mode, "join_priority", 4)
    now = 1000.
    runtime._visible_since = {
        request.rid: now - age for request, age in zip(queue, ages) if age is not None
    }
    try:
        with patch("time.monotonic", lambda: now):
            inspection = runtime.inspect_native_prefill_queue(queue)
            plan = runtime.plan_native_prefill(
                queue, running_batch=NS(reqs=[]), adder=NS(),
                _queue_inspection=inspection,
            )
        assert [key.request_id for key in plan.prioritized] == [queue[index].rid for index in expected]
        assert not runtime.physical_disabled
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_aging_benchmark_executes_the_pinned_planner_body(monkeypatch):
    baseline = pinned_planner("8edcb01")
    runtime, queue = fixture("full", "join_priority", 4, runtime_class=baseline)
    try:
        monkeypatch.setattr(NativeAdmissionRuntime, "plan_native_prefill", Mock(
            side_effect=AssertionError("baseline must not reuse the current planner"),
        ))
        plan = runtime.plan_native_prefill(queue, running_batch=NS(reqs=[]), adder=NS())
        assert len(plan.prioritized) == 4
    finally:
        runtime.close()
