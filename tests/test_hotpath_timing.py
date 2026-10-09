from unittest.mock import patch

import pytest

from beliefkv.runtime.hotpath_timing import BUCKET_UPPER_NS, HotpathTiming, timed_runtime


def test_nested_scopes_do_not_double_count_and_use_bounded_histograms():
    with patch("beliefkv.runtime.hotpath_timing.time.perf_counter_ns",
               side_effect=[0, 10, 20, 50, 110, 200]):
        timing = HotpathTiming()
        with timing.measure("outer"):
            with timing.measure("inner"):
                pass
        snapshot = timing.snapshot()
    phases = snapshot["phases"]
    assert phases["outer"]["total_ms"] == 100 / 1_000_000
    assert phases["outer"]["self_ms"] == 70 / 1_000_000
    assert phases["inner"]["total_ms"] == phases["inner"]["self_ms"] == 30 / 1_000_000
    assert sum(row["self_ms"] for row in phases.values()) == pytest.approx(phases["outer"]["total_ms"])
    assert all(len(row["histogram"]) == len(BUCKET_UPPER_NS) + 1 for row in phases.values())
    assert all(sum(row["histogram"]) == row["count"] for row in phases.values())


def test_decorator_accounts_exceptions_and_disabled_profile_preserves_behavior():
    class Runtime:
        def __init__(self, enabled):
            self._hotpath_timing = HotpathTiming(enabled=enabled)

        @timed_runtime("failure")
        def call(self, message):
            raise ValueError(message)

    for enabled in (True, False):
        runtime = Runtime(enabled)
        with pytest.raises(ValueError, match="original"):
            runtime.call("original")
        phases = runtime._hotpath_timing.snapshot()["phases"]
        assert ("failure" in phases) is enabled
        assert runtime._hotpath_timing._stack == []


def test_overflow_bucket_remains_finite():
    with patch("beliefkv.runtime.hotpath_timing.time.perf_counter_ns",
               side_effect=[0, 0, 2_000_000_000, 2_000_000_000]):
        timing = HotpathTiming()
        with timing.measure("long"):
            pass
        snapshot = timing.snapshot()
    assert snapshot["phases"]["long"]["histogram"][-1] == 1
    assert snapshot["bucket_upper_ms"][-1] is None
