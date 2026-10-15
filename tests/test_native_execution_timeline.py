import csv
from datetime import datetime
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import pytest

from beliefkv.metrics.execution_timeline import load_execution_timeline, render_execution_timeline


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _native_run(
    tmp_path: Path, *, anchor: int = 1_700_000_000_000, capture_timezone: str = "UTC",
) -> Path:
    run = tmp_path / "native"
    server = run / "server"
    client = run / "client_2"
    server.mkdir(parents=True)
    client.mkdir()
    (server / "native_capacity_census.json").write_text(json.dumps({"capacity": {
        "device_full_tokens": 100, "device_full_bytes": 1000,
        "device_mamba_slots": 10, "device_mamba_bytes": 2000,
        "device_total_bytes": 3000,
    }}))
    offset = anchor - 1000
    _jsonl(server / "runtime_audit.jsonl", [
        {"event": "gpu_service_sample", "phase": phase, "batch_size": 2,
         "service_start_ts_ms": anchor + start, "complete_ts_ms": anchor + end,
         "complete_monotonic_ms": anchor + end - offset}
        for phase, start, end in (("prefill", 100, 300), ("decode", 300, 500))
    ])
    with (client / "gpu_samples.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "gpu_utilization_percent", "memory_utilization_percent"])
        for t in (0, 100, 500):
            writer.writerow([
                datetime.fromtimestamp(
                    (anchor + t) / 1000, ZoneInfo(capture_timezone),
                ).strftime("%Y/%m/%d %H:%M:%S.%f"),
                90, 10,
            ])
    _jsonl(client / "sglang_metrics.jsonl", [
        {"monotonic_ts_ms": 1100, "num_running_reqs": 2, "num_queue_reqs": 4,
         "num_used_tokens": 40},
    ])
    _jsonl(server / "host_pool_telemetry.jsonl", [
        {"event": "host_pool_usage", "ts_ms": anchor + 200,
         "pools": {"full": {"used_fraction": .8}, "mamba": {"used_fraction": .3}}},
    ])
    _jsonl(client / "workflows/task/runtime_events.deepagents.jsonl", [
        {"kind": "tool_start", "ts_ms": 1050},
        {"kind": "join_wait", "ts_ms": 1080},
        {"kind": "tool_end", "ts_ms": 1400},
        {"kind": "join_satisfied", "ts_ms": 1450},
    ])
    (client / "summary.json").write_text(json.dumps({
        "workflow_count": 2, "duration_seconds": .5,
        "workflows": [{"outcome": "completed"}, {"outcome": "incomplete"}],
    }))
    _jsonl(server / "physical_action_ack.jsonl", [
        {"event": "beliefkv_physical_action_ack", "command_id": command,
         "action": action, "context_id": "context", "context_epoch": 0}
        for command, action in (("prepare", "PREPARE_HOST"), ("prefetch", "PREFETCH_GPU"))
    ])
    _jsonl(server / "transfer_telemetry.jsonl", [
        {"event": "transfer_telemetry", "command_id": "batch-h2d", "command_kind": "native_hicache_ack",
         "submit_ts_ms": anchor + 200, "complete_ts_ms": anchor + 350,
         "actual_bytes": 230, "num_tokens_by_pool": {"kv": 3, "mamba": 1},
         "direction": "h2d", "status": "completed",
         "tagged_child_commits": [{"command_id": "prefetch", "num_bytes": 210,
                                   "num_tokens_by_pool": {"kv": 1, "mamba": 1}}]},
        {"event": "transfer_telemetry", "command_id": "batch-d2h", "command_kind": "native_hicache_ack",
         "submit_ts_ms": anchor + 200, "complete_ts_ms": anchor + 250,
         "actual_bytes": 20, "num_tokens_by_pool": {"kv": 2},
         "direction": "d2h", "status": "completed",
         "tagged_child_commits": [{"command_id": "prepare", "num_bytes": 20,
                                   "num_tokens_by_pool": {"kv": 2}}]},
    ])
    return run


def test_native_timeline_aligns_clocks_and_preserves_mixed_transfer_bytes(tmp_path: Path) -> None:
    run = _native_run(tmp_path)
    timeline = load_execution_timeline(run, arm="native")
    assert timeline.service_observations[0]["start_ms"] == pytest.approx(100)
    assert timeline.decode_windows[0]["start_ms"] == pytest.approx(300)
    assert timeline.queue_samples[0]["t_ms"] == pytest.approx(100)
    assert timeline.external_waits[0]["t_ms"] == pytest.approx(50)
    assert timeline.external_waits[-1]["active_joins"] == 0
    assert sum(row["bytes"] for row in timeline.transfers) == 250
    assert timeline.summary["predictive_transfer_count"] == 2
    assert timeline.summary["predictive_transfer_bytes"] == 230
    assert timeline.summary["prepare_ack_count"] == 1
    assert timeline.summary["peak_hbm_ratio"] is None
    assert timeline.summary["decode_inferred_window_count"] == 0
    assert timeline.summary["noncompleted_workflows"] == 1
    resources = timeline.resources[0]
    assert resources["full_active_ratio"] == .4
    assert resources["host_mamba_ratio"] == .3
    assert "mamba_occupancy_ratio" not in resources
    html, _ = render_execution_timeline(timeline, tmp_path / "timeline.html")
    assert "Unobserved" in html.read_text()
    assert "not CUDA kernel time" in html.read_text()


def test_native_timeline_reads_separate_device_occupancy_and_truncates(tmp_path: Path) -> None:
    run = _native_run(tmp_path)
    _jsonl(run / "opportunities/admission_opportunities.jsonl", [
        {"event": "session_h2d_opportunity", "ts_ms": 1_700_000_000_250,
         "headroom_observable": True, "device_full_free_tokens": 20,
         "device_mamba_free_slots": 5},
    ])
    timeline = load_execution_timeline(run, arm="predictive", end_offset_ms=320)
    assert timeline.resources[0]["full_occupancy_ratio"] == .8
    assert timeline.resources[0]["mamba_occupancy_ratio"] == .5
    assert timeline.summary["peak_hbm_ratio"] == pytest.approx(.6)
    assert all(row["end_ms"] <= 320 for row in timeline.service_observations)
    assert all(row["end_ms"] <= 320 for row in timeline.transfers)


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires process timezone support")
@pytest.mark.parametrize("capture_timezone,processing_timezone,anchor_text", [
    ("America/Los_Angeles", "America/Los_Angeles", "2026-10-14T20:00:00+00:00"),
    ("Asia/Shanghai", "America/Los_Angeles", "2026-10-14T20:00:00+00:00"),
    ("America/Los_Angeles", "UTC", "2026-10-14T20:00:00+00:00"),
    ("UTC", "Asia/Shanghai", "2026-10-14T20:00:00+00:00"),
    ("America/Los_Angeles", "America/Los_Angeles", "2026-01-14T20:00:00+00:00"),
])
def test_native_timeline_keeps_worker_and_transfer_intervals_across_capture_timezones(
    tmp_path, monkeypatch, capture_timezone, processing_timezone, anchor_text,
):
    anchor = int(datetime.fromisoformat(anchor_text).timestamp() * 1000)
    run = _native_run(tmp_path, anchor=anchor, capture_timezone=capture_timezone)
    try:
        with monkeypatch.context() as environment:
            environment.setenv("TZ", processing_timezone)
            time.tzset()
            timeline = load_execution_timeline(run, arm="native")
    finally:
        time.tzset()

    clock = timeline.summary["clock_alignment"]
    assert clock["anchor_unix_ms"] == anchor
    assert clock["gpu_timezone"] == capture_timezone
    assert clock["gpu_minus_first_client_ms"] == pytest.approx(-100)
    assert clock["native_client_clock_validated"] is True
    assert datetime.fromisoformat(timeline.start_anchor).timestamp() * 1000 == anchor
    assert timeline.service_observations[0]["start_ms"] == pytest.approx(100)
    assert timeline.service_observations[0]["end_ms"] == pytest.approx(300)
    assert timeline.decode_windows[0]["start_ms"] == pytest.approx(300)
    assert timeline.decode_windows[0]["end_ms"] == pytest.approx(500)
    assert timeline.queue_samples[0]["t_ms"] == pytest.approx(100)
    assert min(row["start_ms"] for row in timeline.transfers) == pytest.approx(200)
    assert max(row["end_ms"] for row in timeline.transfers) == pytest.approx(350)
    assert timeline.duration_ms == pytest.approx(500)


def test_native_timeline_rejects_wrong_explicit_capture_timezone(tmp_path):
    run = _native_run(tmp_path, capture_timezone="Asia/Shanghai")
    with pytest.raises(ValueError, match="native/client clock differ"):
        load_execution_timeline(run, arm="native", gpu_timezone="UTC")


def test_native_timeline_requires_explicit_zone_without_client_clock_reference(tmp_path):
    run = _native_run(tmp_path, capture_timezone="Asia/Shanghai")
    (run / "client_2/sglang_metrics.jsonl").unlink()
    with pytest.raises(ValueError, match="explicit zone or a client clock reference"):
        load_execution_timeline(run, arm="native")
    timeline = load_execution_timeline(run, arm="native", gpu_timezone="Asia/Shanghai")
    assert timeline.service_observations[0]["start_ms"] == pytest.approx(100)
    clock = timeline.summary["clock_alignment"]
    assert clock["gpu_timezone"] == "Asia/Shanghai"
    assert clock["native_client_clock_validated"] is False
    assert clock["anchor_source"] == "first_gpu_sample_with_explicit_capture_timezone"


def test_native_timeline_discards_pre_monitor_service_and_clips_crossing_service(tmp_path):
    run = _native_run(tmp_path)
    anchor = 1_700_000_000_000
    audit = run / "server/runtime_audit.jsonl"
    existing = [json.loads(line) for line in audit.read_text().splitlines()]
    _jsonl(audit, [
        {
            "event": "gpu_service_sample", "phase": "decode", "batch_size": 1,
            "service_start_ts_ms": anchor + begin, "complete_ts_ms": anchor + end,
            "complete_monotonic_ms": 1000 + end,
        }
        for begin, end in [(-200, -100), (-50, 50)]
    ] + existing)
    timeline = load_execution_timeline(run, arm="native")
    assert timeline.service_observations[0]["start_ms"] == 0
    assert timeline.service_observations[0]["end_ms"] == 50
    assert all(row["end_ms"] > row["start_ms"] for row in timeline.service_observations)
    assert sum(row["observation_count"] for row in timeline.service_observations) == 3
    assert sum(row["end_ms"] - row["start_ms"] for row in timeline.service_observations) == 450
