from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_p6_high_pressure_ab import compare, render_html, summarize_arm


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _arm(tmp_path: Path, name: str, *, successful: int) -> Path:
    run_dir = tmp_path / name
    workloads = run_dir / "workloads"
    server = run_dir / "server"
    workloads.mkdir(parents=True)
    server.mkdir(parents=True)
    (workloads / "summary.json").write_text(
        json.dumps(
            {
                "duration_seconds": 3600.0,
                "workflow_count": 40,
                "completed_workflows": 20,
                "successful_workflows": successful,
                "system_jct_eligible_workflows": 18,
                "llm_request_count": 120,
                "tool_call_count": 240,
            }
        ),
        encoding="utf-8",
    )
    (workloads / "gpu_samples.csv").write_text(
        "timestamp,gpu_utilization_percent\n"
        "2026/09/16 00:00:00.000,25\n"
        "2026/09/16 00:00:01.000,75\n",
        encoding="utf-8",
    )
    _write_jsonl(
        server / "runtime_audit.jsonl",
        [
            {
                "event": "resource_snapshot",
                "ts_ms": 0.0,
                "hbm_used_bytes": 90,
                "hbm_capacity_bytes": 100,
                "running_request_count": 30,
                "waiting_request_count": 10,
            },
            {
                "event": "resource_snapshot",
                "ts_ms": 1000.0,
                "hbm_used_bytes": 95,
                "hbm_capacity_bytes": 100,
                "running_request_count": 32,
                "waiting_request_count": 8,
            },
            {
                "event": "gpu_service_observer_summary",
                "performance_aggregates": {
                    "prefill": {"tokens": 1000},
                    "decode": {"tokens": 2000},
                },
            },
        ],
    )
    _write_jsonl(
        server / "transfer_telemetry.jsonl",
        [
            {
                "event": "transfer_telemetry",
                "status": "completed",
                "direction": "d2h",
                "actual_bytes": 4096,
                "submit_ts_ms": 250.0,
            }
        ],
    )
    (server / "latest_runtime_summary.json").write_text(
        json.dumps({"correctness_gates": {"clean": True}}), encoding="utf-8"
    )
    return run_dir


def test_summarizes_throughput_and_external_transfer_telemetry(tmp_path: Path) -> None:
    baseline = summarize_arm(_arm(tmp_path, "baseline", successful=10), "baseline")
    predictive = summarize_arm(_arm(tmp_path, "predictive", successful=12), "predictive")
