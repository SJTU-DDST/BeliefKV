import hashlib
import json

import pytest

from scripts.analyze_busy_context_control import (
    WriterTimingWindow,
    control_profile,
    decode_geometry,
)


def timing(timestamp, *, records=65, samples=2, cpu=3., wall=30., categories=None):
    return {
        "schema_version": 1, "sample_every": 64, "snapshot_ts_ms": timestamp,
        "cpu_clock": "thread_time_ns", "wall_clock": "perf_counter_ns",
        "categories": categories if categories is not None else {
            "prefix": {
                "records_seen": records, "samples": samples,
                "sampled_cpu_ms": cpu, "sampled_wall_ms": wall,
                "max_sample_cpu_ms": 999., "max_sample_wall_ms": 9999.,
            },
        },
    }


def encoded(row):
    return (json.dumps(row) + "\n").encode()


def test_writer_samples_are_differenced_without_extrapolation_or_cumulative_maxima():
    window = WriterTimingWindow(100., 200.)
    before, after = timing(90.), timing(190., records=129, samples=3, cpu=4., wall=50.)
    window.observe(before, encoded(before))
    window.observe(after, encoded(after))
    window.observe(timing(210., cpu=100.), b"outside\n")
    result = window.summary()
    assert result["available"]
    assert result["sampled_cpu_ms"] == 1.
    assert result["sampled_wall_ms"] == 20.
    assert result["categories"]["prefix"] == {
        "records_seen": 64, "samples": 1, "sampled_cpu_ms": 1.,
        "sampled_wall_ms": 20., "mean_sample_cpu_ms": 1., "mean_sample_wall_ms": 20.,
    }
    assert result["raw_boundary_line_sha256"] == [
        hashlib.sha256(encoded(row)).hexdigest() for row in (before, after)
    ]
    assert result["begin_snapshot_lag_ms"] == result["end_snapshot_lag_ms"] == 10.


def test_missing_or_incomplete_writer_evidence_is_not_reported_as_zero_cost():
    window = WriterTimingWindow(100., 200.)
    assert window.summary() == {
        "available": False, "reason": "writer_timing_not_collected",
    }
    window.observe(timing(150.), b"middle\n")
    window.observe(timing(210.), b"after\n")
    assert window.summary()["reason"] == "writer_window_not_fully_observed"


def test_new_categories_and_records_without_new_samples_remain_distinct():
    window = WriterTimingWindow(100., 200.)
    window.observe(timing(90.), b"before\n")
    categories = timing(190., records=66)["categories"]
    categories["new"] = {
        "records_seen": 1, "samples": 1, "sampled_cpu_ms": 2., "sampled_wall_ms": 4.,
    }
    window.observe(timing(190., categories=categories), b"middle\n")
    window.observe(timing(210.), b"after\n")
    result = window.summary()["categories"]
    assert result["prefix"]["records_seen"] == 1
    assert result["prefix"]["samples"] == 0
    assert result["prefix"]["mean_sample_cpu_ms"] is None
    assert result["new"]["sampled_cpu_ms"] == 2.


@pytest.mark.parametrize("change", ["counter_reset", "sample_every"])
def test_counter_reset_or_sampling_change_does_not_produce_a_window_cost(change):
    window = WriterTimingWindow(100., 200.)
    window.observe(timing(90.), b"before\n")
    after = timing(190.)
    if change == "counter_reset":
        after["categories"]["prefix"]["samples"] = 1
    else:
        after["sample_every"] = 32
    window.observe(after, b"middle\n")
    window.observe(timing(210.), b"after\n")
    with pytest.raises(ValueError, match="within the window"):
        window.summary()


def test_opportunity_writer_window_uses_write_clock_instead_of_producer_clock(tmp_path):
    directory = tmp_path / "opportunities"
    directory.mkdir()
    path = directory / "admission_opportunities.jsonl"
    rows = []
    for timestamp, writer_timestamp, samples, cpu in (
        (90., 90., 1, 1.), (120., 150., 2, 3.),
        (180., 250., 3, 100.), (210., 260., 4, 200.),
    ):
        rows.append({
            "event": "admission_runtime_state", "ts_ms": timestamp,
            "shared_path_timing": {
                "phases": {"control": {"count": timestamp, "self_ms": timestamp, "total_ms": timestamp}},
            },
            "opportunity_writer_timing": timing(writer_timestamp, samples=samples, cpu=cpu),
        })
    path.write_bytes(b"".join(encoded(row) for row in rows))
    result = control_profile(tmp_path, 100., 200.)
    assert result["boundary_state_ts_ms"] == [90., 180.]
    writer = result["background_writer_timing"]
    assert writer["boundary_snapshot_ts_ms"] == [90., 150.]
    assert writer["sampled_cpu_ms"] == 2.
    assert writer["end_snapshot_lag_ms"] == 50.


@pytest.mark.parametrize("collect_writer", [False, True])
def test_native_writer_coverage_can_follow_the_last_gpu_row(tmp_path, collect_writer):
    directory = tmp_path / "server"
    directory.mkdir()
    rows = []
    if collect_writer:
        rows.append({"event": "background_writer_timing", "timing": timing(90.)})
    rows.append({
        "event": "gpu_service_sample", "complete_ts_ms": 150.,
        "phase": "decode", "batch_size": 1, "service_elapsed_ms": 10.,
        "request_samples": [{"sequence_tokens_before": 1000}],
    })
    if collect_writer:
        rows.append({
            "event": "background_writer_timing",
            "timing": timing(190., samples=3, cpu=4., wall=50.),
        })
    rows.append({"event": "gpu_service_sample", "complete_ts_ms": 210.})
    if collect_writer:
        rows.append({"event": "background_writer_timing", "timing": timing(220.)})
    (directory / "runtime_audit.jsonl").write_bytes(b"".join(encoded(row) for row in rows))
    result = decode_geometry(tmp_path, 100., 200.)
    assert result["decode_batches"] == 1
    writer = result["background_writer_timing"]
    assert writer["available"] == collect_writer
    if collect_writer:
        assert writer["sampled_cpu_ms"] == 1.
    else:
        assert writer["reason"] == "writer_timing_not_collected"
