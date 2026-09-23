#!/usr/bin/env python3
"""Freeze two disjoint 64-root train shards as one overlapped collection."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.p6_collection import load_collection_batch


def _digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_new(path: Path, value: Any) -> bytes:
    content = (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
    return content


def freeze_overlapped_plan(
    source_plan_path: Path,
    output_plan_path: Path,
    workload_output_path: Path,
    *,
    second_wave_delay_s: float = 60.0,
) -> dict[str, Any]:
    source_plan_path = source_plan_path.expanduser().resolve()
    output_plan_path = output_plan_path.expanduser().resolve()
    workload_output_path = workload_output_path.expanduser().resolve()
    source_bytes = source_plan_path.read_bytes()
    source_plan = json.loads(source_bytes)
    if (
        source_plan.get("frozen") is not True
        or source_plan.get("runtime_policy") != "frozen_native_reactive_v0520"
        or source_plan.get("predictor_enabled") is not False
        or source_plan.get("predictive_actions_enabled") is not False
    ):
        raise ValueError("source plan must be frozen native-reactive train evidence")
    train_batches = [
        item
        for item in source_plan.get("batches", ())
        if isinstance(item, dict) and item.get("split") == "train"
    ]
    if len(train_batches) != 2:
        raise ValueError("source plan must contain exactly two train batches")

    workloads: list[dict[str, Any]] = []
    source_manifests: dict[str, str] = {}
    batch_ids: list[str] = []
    for batch in train_batches:
        load_collection_batch(source_plan_path, str(batch["batch_id"]))
        manifest_path = Path(batch["workload_manifest"]).expanduser().resolve()
        manifest_bytes = manifest_path.read_bytes()
        digest = _digest_bytes(manifest_bytes)
        if digest != batch.get("workload_manifest_sha256"):
            raise ValueError(f"source workload manifest changed: {manifest_path}")
        manifest = json.loads(manifest_bytes)
        if manifest.get("split") != "train":
            raise ValueError(f"non-train workload manifest: {manifest_path}")
        batch_workloads = manifest.get("workloads")
        if not isinstance(batch_workloads, list) or len(batch_workloads) != 64:
            raise ValueError(f"each source batch must contain 64 workflows: {manifest_path}")
        if int(batch.get("concurrency", 0)) != 64:
            raise ValueError("source batches must be frozen at 64-root concurrency")
        if batch.get("subagent_fanout_profile") != "native_dynamic_1to4":
            raise ValueError("source batches must use native_dynamic_1to4")
        if batch.get("predictive_actions") is not False:
            raise ValueError("training workload must not enable predictive actions")
        source_manifests[str(manifest_path)] = digest
        batch_ids.append(str(batch["batch_id"]))
        workloads.extend(batch_workloads)

    instance_ids = [str(item["instance_id"]) for item in workloads]
    if len(instance_ids) != 128 or len(set(instance_ids)) != 128:
        raise ValueError("source batches must contain 128 distinct workflows")
    if second_wave_delay_s <= 0:
        raise ValueError("second-wave delay must be positive")

    first_manifest = json.loads(
        Path(train_batches[0]["workload_manifest"]).read_text(encoding="utf-8")
    )
    combined_manifest = {
        **first_manifest,
        "selection_policy": (
            "concatenate the two frozen disjoint 64-root train batches; "
            "submit roots 0-63 at t=0 and roots 64-127 at the frozen second-wave delay"
        ),
        "source_batch_ids": batch_ids,
        "source_workload_manifest_sha256": source_manifests,
        "workload_count": 128,
        "arrival_schedule": {
            "mode": "two_waves",
            "wave_sizes": [64, 64],
            "second_wave_delay_s": second_wave_delay_s,
            "server_instances": 1,
        },
        "workloads": workloads,
    }
    manifest_bytes = _write_new(workload_output_path, combined_manifest)

    source_batch = train_batches[0]
    batch = {
        **source_batch,
        "batch_id": "qwen35-native-reactive-overlapped-128root-train-r0",
        "workflow_count": 128,
        "concurrency": 128,
        "instance_ids": instance_ids,
        "projects": sorted({str(item["repo"]) for item in workloads}),
        "docker_images": sorted(
            {str(item["docker_image"]) for item in workloads}
        ),
        "source_repositories": sorted(
            {
                (str(item["repo"]), str(item["source_repo"]))
                for item in workloads
            }
        ),
        "versions": sorted(
            {
                f"{item['repo']}:{item['version']}"
                for item in workloads
                if item.get("version") is not None
            }
        ),
        "workload_manifest": str(workload_output_path),
        "workload_manifest_sha256": _digest_bytes(manifest_bytes),
        "workflow_arrival_interval_ms": 0.0,
        "workflow_arrival_batch_size": 64,
        "workflow_arrival_batch_interval_ms": second_wave_delay_s * 1000.0,
        "saturated_root_backlog": False,
        "predictive_actions": False,
        "semantic_gate_stop_after_first_join": False,
    }
    batch["source_repositories"] = [
        {"project": project, "path": path}
        for project, path in batch["source_repositories"]
    ]
    plan = {
        **source_plan,
        "plan_id": "qwen35-native-reactive-v0520-v5-overlapped-128root",
        "source_plan": str(source_plan_path),
        "source_plan_sha256": _digest_bytes(source_bytes),
        "arrival_contract": {
            "root_count": 128,
            "client_inflight": 128,
            "server_max_running_requests": 48,
            "server_instances": 1,
            "waves": [
                {"wave": 1, "root_count": 64, "offset_seconds": 0.0},
                {
                    "wave": 2,
                    "root_count": 64,
                    "offset_seconds": second_wave_delay_s,
                },
            ],
        },
        "batches": [batch],
        "batch_count": 1,
        "unique_task_count": 128,
        "repository_count": len(batch["projects"]),
        "workflow_count": 128,
        "unique_task_counts_by_split": {"train": 128},
        "workflow_counts_by_split": {"train": 128},
    }
    _write_new(output_plan_path, plan)
    load_collection_batch(output_plan_path, batch["batch_id"])
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--output-plan", type=Path, required=True)
    parser.add_argument("--workload-output", type=Path, required=True)
    parser.add_argument("--second-wave-delay-s", type=float, default=60.0)
    args = parser.parse_args()
    plan = freeze_overlapped_plan(
        args.source_plan,
        args.output_plan,
        args.workload_output,
        second_wave_delay_s=args.second_wave_delay_s,
    )
    batch = plan["batches"][0]
    print(
        json.dumps(
            {
                "plan_id": plan["plan_id"],
                "batch_id": batch["batch_id"],
                "workflow_count": batch["workflow_count"],
                "concurrency": batch["concurrency"],
                "wave_sizes": [64, 64],
                "second_wave_delay_s": (
                    batch["workflow_arrival_batch_interval_ms"] / 1000.0
                ),
                "server_instances": 1,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
