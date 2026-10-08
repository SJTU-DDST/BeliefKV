import csv
import json
from pathlib import Path

import pytest

from beliefkv.metrics.execution_timeline import load_execution_timeline, render_execution_timeline


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _native_run(tmp_path: Path) -> Path:
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
    anchor = 1_700_000_000_000
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
        from datetime import datetime

        for t in (0, 100, 500):
            writer.writerow([
                datetime.fromtimestamp((anchor + t) / 1000).strftime("%Y/%m/%d %H:%M:%S.%f"),
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
