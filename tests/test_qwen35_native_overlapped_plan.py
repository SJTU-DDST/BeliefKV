from __future__ import annotations

import json
from pathlib import Path

from scripts.freeze_qwen35_native_overlapped_train_plan import (
    freeze_overlapped_plan,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_freezes_two_64_root_shards_as_one_overlapped_collection(
    tmp_path: Path,
) -> None:
    source_plan = (
        REPOSITORY_ROOT
        / "configs/migration/qwen35_native_reactive_128root_train_plan_2026-09-23.json"
    )
    output_plan = tmp_path / "overlapped-plan.json"
    workload_manifest = tmp_path / "overlapped-workloads.json"

    plan = freeze_overlapped_plan(
        source_plan,
        output_plan,
        workload_manifest,
        second_wave_delay_s=60.0,
    )
    batch = plan["batches"][0]
    manifest = json.loads(workload_manifest.read_text(encoding="utf-8"))

    assert plan["workflow_count"] == 128
    assert plan["arrival_contract"]["server_instances"] == 1
    assert plan["arrival_contract"]["waves"] == [
        {"wave": 1, "root_count": 64, "offset_seconds": 0.0},
        {"wave": 2, "root_count": 64, "offset_seconds": 60.0},
    ]
    assert batch["workflow_count"] == 128
    assert batch["concurrency"] == 128
    assert batch["saturated_root_backlog"] is False
    assert batch["workflow_arrival_batch_size"] == 64
    assert batch["workflow_arrival_batch_interval_ms"] == 60_000
    assert len(set(batch["instance_ids"])) == 128
    assert manifest["arrival_schedule"]["server_instances"] == 1
    assert len(manifest["workloads"]) == 128
