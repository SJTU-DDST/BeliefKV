import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts.analyze_busy_context_control import (
    WriterTimingWindow,
    control_profile,
    decode_geometry,
    periodic_decode_graph_log,
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
@pytest.mark.parametrize("graph_replayed", [None, False, True])
def test_native_writer_coverage_can_follow_the_last_gpu_row(tmp_path, collect_writer, graph_replayed):
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
    if graph_replayed is not None:
        rows[-1]["cuda_graph_replayed"] = graph_replayed
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
    label = "unknown" if graph_replayed is None else str(graph_replayed).lower()
    assert result["cuda_graph_cohorts"] == {
        f"1/0/{label}": {"batches": 1, "mean_ms": 10., "median_ms": 10.},
    }
    writer = result["background_writer_timing"]
    assert writer["available"] == collect_writer
    if collect_writer:
        assert writer["sampled_cpu_ms"] == 1.
    else:
        assert writer["reason"] == "writer_timing_not_collected"


def test_prefill_cohorts_require_native_geometry_for_every_batch_row(tmp_path):
    directory = tmp_path / "server"
    directory.mkdir()
    samples = [
        (150., 1, [{"sequence_tokens_before": 12000, "token_delta": 0}]),
        (155., 2, [{
            "sequence_tokens_before": 4096, "token_delta": 4096,
            "prefill_geometry_source": "native_batch",
        }]),
        (160., 2, [
            {"sequence_tokens_before": 0, "token_delta": 1000,
             "prefill_geometry_source": "native_batch"},
            {"sequence_tokens_before": 4096, "token_delta": 500,
             "prefill_geometry_source": "native_request_range"},
        ]),
        (165., 1, [{
            "sequence_tokens_before": None, "token_delta": None,
            "prefill_geometry_source": "unavailable",
        }]),
        (170., 1, [{
            "sequence_tokens_before": 8000, "token_delta": 0,
            "prefill_geometry_source": "native_batch",
        }]),
    ]
    rows = [{
        "event": "gpu_service_sample", "complete_ts_ms": timestamp,
        "phase": "prefill", "batch_size": size, "service_elapsed_ms": 5.,
        "request_samples": requests,
    } for timestamp, size, requests in samples]
    rows.extend([
        {"event": "gpu_service_sample", "complete_ts_ms": 175.,
         "phase": "decode", "batch_size": 1, "service_elapsed_ms": 10.,
         "request_samples": [{"sequence_tokens_before": 1000}]},
        {"event": "gpu_service_sample", "complete_ts_ms": 210.},
    ])
    (directory / "runtime_audit.jsonl").write_bytes(b"".join(encoded(row) for row in rows))

    prefill = decode_geometry(tmp_path, 100., 200.)["prefill_geometry"]
    assert prefill["batches"] == 5
    assert prefill["worker_interval_ms"] == 25.
    assert prefill["known_work_batches"] == 2
    assert prefill["unknown_work_batches"] == 3
    assert prefill["known_extend_tokens"] == 1500
    assert prefill["request_geometry_sources"] == {
        "native_batch": 3, "native_request_range": 1,
        "unavailable": 1, "unverified_legacy": 1,
    }
    assert set(prefill["cohorts"]) == {"2/1024/2048", "1/0/6144"}


@pytest.mark.parametrize("configuration", [
    b"ServerArgs(decode_log_interval=40)",
    b"server_args={'decode_log_interval': 40, 'cuda_graph_config': {'decode': {'bs': [40, 48]}}}",
])
def test_periodic_graph_log_keeps_boundary_and_unknown_samples_separate(tmp_path, configuration):
    lines = [
        b"[2026-10-11 16:42:00] " + configuration + b"\n",
        b"[2026-10-11 16:42:01] Decode batch, #running-req: 47, cuda graph: False\n",
        b"[2026-10-11 16:42:02] Decode batch [80], #running-req: 47, cuda graph: True\n",
        b"[2026-10-11 16:42:03] Decode batch. #running-req: 47, cuda graph: False\n",
        b"[2026-10-11 16:42:04] Decode batch, #running-req: 46, #queue-req: 1\n",
        b"[2026-10-11 16:42:05] Decode batch, #running-req: 47, cuda graph: False\n",
        b"[2026-10-11 16:42:06] Prefill batch, #running-req: 48\n",
        b"[2026-10-11 16:42:07] Decode batch, #running-req: 47, cuda graph: False\n",
    ]
    (tmp_path / "server.log").write_bytes(b"".join(lines))
    timezone = ZoneInfo("Asia/Shanghai")
    origin = datetime(2026, 10, 11, 16, 42, tzinfo=timezone).timestamp() * 1000.
    result = periodic_decode_graph_log(tmp_path, origin + 1500., origin + 5500., timezone)
    assert result["samples"] == 3
    assert result["batch_sizes"] == {
        "46": {"true": 0, "false": 0, "unknown": 1},
        "47": {"true": 1, "false": 1, "unknown": 0},
    }
    assert result["boundary_samples_excluded"] == {"begin": 1, "end": 1}
    assert result["window_fully_observed"]
    assert result["decode_log_interval_observed"] == 40
    prefix = b"".join(lines[:-1])
    assert result["read_prefix_bytes"] == len(prefix)
    assert result["read_prefix_sha256"] == hashlib.sha256(prefix).hexdigest()


def test_missing_graph_logs_differ_from_observed_zero_samples(tmp_path):
    timezone = ZoneInfo("UTC")
    origin = datetime(2026, 10, 11, tzinfo=timezone).timestamp() * 1000.
    missing = periodic_decode_graph_log(tmp_path, origin, origin + 1000., timezone)
    assert not missing["available"]
    assert missing["reason"] == "server_log_not_collected"
    (tmp_path / "server.log").write_bytes(
        b"[2026-10-11 00:00:01] Prefill batch, #running-req: 1\n"
    )
    result = periodic_decode_graph_log(tmp_path, origin, origin + 1000., timezone)
    assert result["available"] and result["window_fully_observed"]
    assert result["samples"] == 0 and result["batch_sizes"] == {}
    assert result["decode_log_interval_observed"] is None


def test_incomplete_graph_log_does_not_claim_full_window_coverage(tmp_path):
    timezone = ZoneInfo("UTC")
    origin = datetime(2026, 10, 11, tzinfo=timezone).timestamp() * 1000.
    prefix = b"[2026-10-11 00:00:00] Decode batch, #running-req: 48, cuda graph: True\n"
    partial = b"[2026-10-11 00:00:02] Decode batch, #running-req: 48, cuda graph: False"
    (tmp_path / "server.log").write_bytes(prefix + partial)
    result = periodic_decode_graph_log(tmp_path, origin, origin + 2000., timezone)
    assert not result["window_fully_observed"]
    assert result["batch_sizes"] == {"48": {"true": 1, "false": 0, "unknown": 0}}
    assert result["read_prefix_sha256"] == hashlib.sha256(prefix).hexdigest()
