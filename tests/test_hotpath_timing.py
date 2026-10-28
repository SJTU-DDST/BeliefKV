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


def test_nested_thread_cpu_is_distinct_from_wall_and_excludes_children():
    with (
        patch("beliefkv.runtime.hotpath_timing.time.perf_counter_ns",
              side_effect=[0, 10, 20, 50, 110, 200]),
        patch("beliefkv.runtime.hotpath_timing.time.thread_time_ns",
              side_effect=[100, 110, 120, 140]),
    ):
        timing = HotpathTiming()
        with timing.measure("outer"):
            with timing.measure("inner"):
                pass
        snapshot = timing.snapshot()
    outer, inner = snapshot["phases"]["outer"], snapshot["phases"]["inner"]
    assert outer["total_ms"] == 100 / 1_000_000
    assert outer["self_ms"] == 70 / 1_000_000
    assert outer["thread_cpu_total_ms"] == 40 / 1_000_000
    assert outer["thread_cpu_self_ms"] == 30 / 1_000_000
    assert inner["thread_cpu_total_ms"] == inner["thread_cpu_self_ms"] == 10 / 1_000_000
    assert sum(row["thread_cpu_self_ms"] for row in snapshot["phases"].values()) == pytest.approx(
        outer["thread_cpu_total_ms"],
    )


def test_sparse_roots_preserve_complete_nested_samples_and_skip_clock_reads():
    timing = HotpathTiming(sample_every=4)
    with (
        patch("beliefkv.runtime.hotpath_timing.time.perf_counter_ns", return_value=100) as wall,
        patch("beliefkv.runtime.hotpath_timing.time.thread_time_ns", return_value=10) as cpu,
    ):
        for _ in range(9):
            with timing.measure("outer"):
                with timing.measure("inner"):
                    pass
        assert wall.call_count == cpu.call_count == 12
    phases = timing.snapshot()["phases"]
    assert phases["outer"]["observed_calls"] == phases["outer"]["root_calls"] == 9
    assert phases["inner"]["observed_calls"] == 9
    assert phases["inner"]["root_calls"] == 0
    assert phases["outer"]["count"] == phases["inner"]["count"] == 3
    assert timing._stack == []


def test_nested_calls_do_not_advance_a_phases_root_sampling_stride():
    timing = HotpathTiming(sample_every=2)
    with timing.measure("outer"):
        with timing.measure("inner"):
            pass
    for _ in range(3):
        with timing.measure("inner"):
            pass
    phase = timing.snapshot()["phases"]["inner"]
    assert phase["observed_calls"] == 4
    assert phase["root_calls"] == 3
    assert phase["count"] == 3


def test_unsampled_exception_restores_stack_and_next_root_can_be_measured():
    timing = HotpathTiming(sample_every=2)
    with timing.measure("root"):
        pass
    with pytest.raises(ValueError, match="original"):
        with timing.measure("root"):
            with timing.measure("nested"):
                raise ValueError("original")
    assert timing._stack == []
    with timing.measure("root"):
        pass
    assert timing.snapshot()["phases"]["root"]["count"] == 2
    assert "nested" not in timing.snapshot()["phases"]


@pytest.mark.parametrize("value", (0, -1, True, 1.5, None))
def test_invalid_sample_interval_is_rejected(value):
    with pytest.raises(ValueError, match="positive integer"):
        HotpathTiming(sample_every=value)


def test_disabled_profile_does_not_read_either_clock_in_scopes():
    timing = HotpathTiming(enabled=False, sample_every=64)
    with (
        patch("beliefkv.runtime.hotpath_timing.time.perf_counter_ns") as wall,
        patch("beliefkv.runtime.hotpath_timing.time.thread_time_ns") as cpu,
    ):
        with timing.measure("disabled"):
            pass
        wall.assert_not_called()
        cpu.assert_not_called()
    assert timing.snapshot()["phases"] == {}


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("has_writer", (False, True))
def test_native_runtime_uses_sparse_timing_only_with_explicit_opt_in_and_writer(
    monkeypatch, tmp_path, enabled, has_writer,
):
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    monkeypatch.setenv("BELIEFKV_PROFILE_SHARED_PATH", str(int(enabled)))
    monkeypatch.delenv("BELIEFKV_ADMISSION_OPPORTUNITY_DIR", raising=False)
    runtime = NativeAdmissionRuntime(
        opportunity_dir=str(tmp_path / "opportunities") if has_writer else None,
    )
    try:
        assert runtime._hotpath_timing.enabled is (enabled and has_writer)
        assert runtime._hotpath_timing.sample_every == 64
        assert runtime._hotpath_timing.snapshot()["cpu_clock"] == "thread_time_ns"
    finally:
        runtime.close()
