from __future__ import annotations

import csv
import json
from pathlib import Path

from beliefkv.metrics.execution_timeline import (
    load_execution_timeline,
    render_execution_timeline,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_execution_timeline_aligns_service_and_transfer_overlap(tmp_path: Path) -> None:
    run = tmp_path / "run"
    server = run / "server"
    workloads = run / "workloads"
    server.mkdir(parents=True)
    workloads.mkdir(parents=True)
    _write_jsonl(
        server / "runtime_audit.jsonl",
        [
            {"event": "runtime_initialized", "ts_ms": 1000.0},
            {
                "event": "resource_snapshot",
                "ts_ms": 2000.0,
                "hbm_used_bytes": 80,
                "hbm_capacity_bytes": 100,
                "host_used_bytes": 10,
                "host_capacity_bytes": 100,
                "running_request_count": 2,
                "waiting_request_count": 3,
            },
            {"event": "predictive_semantic_intent_committed", "ts_ms": 2500.0},
        ],
    )
    _write_jsonl(
        server / "transfer_telemetry.jsonl",
        [
            {
                "event": "transfer_telemetry",
                "submit_ts_ms": 2200.0,
                "complete_ts_ms": 2800.0,
                "actual_bytes": 1024,
                "direction": "d2h",
                "extent_count": 2,
                "command_kind": "prepare_host",
                "command_id": "command-1",
                "predictive_intent_id": "intent-1",
                "context_id": "context-1",
                "context_epoch": 3,
                "status": "completed",
            },
            {
                "event": "transfer_telemetry",
                "submit_ts_ms": 2300.0,
                "complete_ts_ms": 2400.0,
                "actual_bytes": 2048,
                "direction": "h2d",
                "extent_count": 1,
                "command_kind": "native_demand_load",
                "command_id": "native-command-1",
                "status": "completed",
            },
            {
                "event": "transfer_telemetry",
                "submit_ts_ms": 2600.0,
                "complete_ts_ms": 2700.0,
                "actual_bytes": 4096,
                "direction": "h2d",
                "extent_count": 2,
                "command_kind": "prefetch_context",
                "command_id": "predictive-command-1",
                "predictive_intent_id": "intent-2",
                "context_id": "context-2",
                "context_epoch": 1,
                "status": "rejected",
            },
        ],
    )
    _write_jsonl(
        server / "runtime_events.sglang.jsonl",
        [
            {"kind": "tool_start", "ts_ms": 2100.0},
            {"kind": "join_wait", "ts_ms": 2300.0},
            {"kind": "tool_end", "ts_ms": 2700.0},
            {"kind": "join_satisfied", "ts_ms": 2900.0},
        ],
    )
    (server / "server.log").write_text(
        "[Gloo] Rank 0 is connected to 0 peer ranks.\n"
        "[2026-09-16 05:00:00] INFO: Application startup complete.\n"
        "[2026-09-16 05:00:01] Decode batch. #running-req: 2, #token: 100, "
        "token usage: 0.80, cuda graph: True, gen throughput (token/s): 80, "
        "#queue-req: 3,\n"
        "[2026-09-16 05:00:02] Prefill batch. #new-seq: 1, #new-token: 128, "
        "#cached-token: 256, token usage: 0.82, #running-req: 2, #queue-req: 2,\n"
    )
    with (workloads / "gpu_samples.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "timestamp",
                "memory_used_mib",
                "memory_free_mib",
                "gpu_utilization_percent",
                "memory_utilization_percent",
                "power_watts",
            ]
        )
        writer.writerow(["2026/09/16 05:00:01.000", 1, 1, 80, 10, 100])
        writer.writerow(["2026/09/16 05:00:02.000", 1, 1, 0, 10, 100])

    timeline = load_execution_timeline(run, arm="predictive")
    assert timeline.summary["transfer_count"] == 3
    assert timeline.summary["transfer_duration_ms"] == 800.0
    assert timeline.summary["completed_transfer_duration_ms"] == 700.0
    assert timeline.summary["predictive_transfer_count"] == 2
    assert timeline.summary["predictive_transfer_bytes"] == 5120
    assert timeline.summary["transfers_by_direction"]["h2d"]["count"] == 2
    assert timeline.summary["predictive_transfers_by_direction"]["d2h"] == {
        "count": 1,
        "bytes": 1024,
        "duration_ms": 600.0,
        "gpu_busy_overlap_ms": 600.0,
        "status_counts": {"completed": 1},
    }
    assert timeline.summary["predictive_transfers_by_direction"]["h2d"] == {
        "count": 1,
        "bytes": 4096,
        "duration_ms": 100.0,
        "gpu_busy_overlap_ms": 0.0,
        "status_counts": {"rejected": 1},
    }
    assert timeline.summary["predictive_commit_count"] == 1
    assert timeline.summary["peak_hbm_ratio"] == 0.8
    assert timeline.summary["mean_running"] == 2.0
    assert timeline.summary["mean_waiting"] == 2.5
    transfer = timeline.transfers[0]
    assert transfer["source"] == "predictive"
    assert transfer["predictive_intent_id"] == "intent-1"
    assert transfer["context_id"] == "context-1"
    assert transfer["context_epoch"] == 3
    assert transfer["gpu_busy_overlap_ms"] == 600.0
    assert transfer["potentially_hidden_fraction"] == 1.0
    assert timeline.decode_windows[0]["cuda_graph"] is True

    html, data = render_execution_timeline(timeline, tmp_path / "timeline.html")
    assert html.exists()
    assert data.exists()
    content = html.read_text()
    assert "GPU service" in content
    assert "DMA while GPU busy" in content
    assert "Predictive D2H" in content
    assert "Predictive H2D" in content
    assert "Native D2H" in content
    assert "Native H2D" in content
    assert "Non-completed transfer" in content
    assert "1 completed" in content
    assert "1 rejected" in content
    assert "Hidden-overlap fractions count completed transfers only" in content

    truncated = load_execution_timeline(
        run,
        arm="predictive",
        end_offset_ms=1500.0,
    )
    assert truncated.duration_ms == 1500.0
    assert all(float(item["t_ms"]) <= 1500.0 for item in truncated.resources)
    assert all(float(item["t_ms"]) <= 1500.0 for item in truncated.gpu_samples)
    assert all(float(item["end_ms"]) <= 1500.0 for item in truncated.transfers)
    assert truncated.summary["transfer_count"] == 2
    assert truncated.summary["predictive_transfer_count"] == 1
