#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PLAN = (
    REPOSITORY_ROOT
    / "configs/p6/h200_bf16_formal_train_v1/collection_plan.json"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / "configs/p6/oracle_v2_native_subagent_v1"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _batch(
    *,
    batch_id: str,
    manifest_path: Path,
    workloads: list[dict[str, Any]],
    semantic_gate: bool,
) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "split": "train",
        "workflow_count": len(workloads),
        "instance_ids": [item["instance_id"] for item in workloads],
        "projects": sorted({item["repo"] for item in workloads}),
        "workload_manifest": str(manifest_path),
        # The collection loader requires content binding for frozen input.
        "workload_manifest_sha256": _digest(manifest_path),
        "docker_images": sorted({item["docker_image"] for item in workloads}),
        "concurrency": len(workloads),
        "subagent_fanout_profile": "native_subagent_2to3",
        "workflow_arrival_interval_ms": 0,
        "workflow_arrival_batch_size": 0,
        "workflow_arrival_batch_interval_ms": 0,
        "saturated_root_backlog": True,
        "predictive_actions": False,
        "policy": "frozen_p5_observed",
        "semantic_gate_stop_after_first_join": semantic_gate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze native parent-continuation workloads for GPU Oracle runs."
    )
    parser.add_argument("--source-plan", type=Path, default=DEFAULT_SOURCE_PLAN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source_plan_path = args.source_plan.expanduser().resolve()
    source_plan = _read(source_plan_path)
    workloads_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for batch in source_plan.get("batches", ()):
        manifest = _read(Path(str(batch["workload_manifest"])).expanduser().resolve())
        for item in manifest.get("workloads", ()):
            instance_id = str(item["instance_id"])
            if instance_id in workloads_by_id:
                continue
            clean = dict(item)
            clean.pop("oracle_kv_pressure", None)
            workloads_by_id[instance_id] = clean
            ordered_ids.append(instance_id)
    if len(ordered_ids) < 64:
        raise ValueError(f"source plan contains only {len(ordered_ids)} unique workflows")
    formal = [workloads_by_id[item] for item in ordered_ids[:64]]

    gate: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    for item in formal:
        if item["repo"] in seen_projects:
            continue
        gate.append(item)
        seen_projects.add(item["repo"])
        if len(gate) == 4:
            break
    if len(gate) < 4:
        gate = formal[:4]

    output_dir = args.output_dir.expanduser().resolve()
    manifest_dir = output_dir / "workload_manifests"
    formal_path = manifest_dir / "oracle-v2-native-subagent-64-r0.json"
    gate_path = manifest_dir / "oracle-v2-native-subagent-semantic-gate-4-r0.json"
    common = {
        "schema_version": 2,
        "dataset": source_plan["dataset"],
        "dataset_revision": source_plan["dataset_revision"],
        "split_manifest": source_plan["split_manifest"],
        "split": "train",
        "rollout_index": 0,
        "subagent_fanout_profile": "native_subagent_2to3",
        "selection_policy": (
            "first 64 preregistered train workflows from the frozen source pool; "
            "selected before native-subagent execution with no outcome replacement"
        ),
    }
    _write(
        formal_path,
        {
            **common,
            "evidence_role": "native_subagent_gpu_characterization_and_oracle_replay",
            "workloads": formal,
        },
    )
    _write(
        gate_path,
        {
            **common,
            "selection_policy": (
                "first four project-distinct workflows from the frozen formal set"
            ),
            "evidence_role": "native_parent_continuation_semantic_gate_only",
            "workloads": gate,
        },
    )
    plan = {
        "schema_version": 3,
        "plan_id": "oracle-v2-native-subagent-gpu-v1",
        "frozen": True,
        "evidence_role": "gpu_first_native_parent_continuation",
        "dataset": source_plan["dataset"],
        "dataset_revision": source_plan["dataset_revision"],
        "split_manifest": source_plan["split_manifest"],
        "source_plan": str(source_plan_path),
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "arrival_contract": {
            "root_count": 64,
            "client_inflight": 64,
            "server_max_running_requests": 32,
            "submission": "all_roots_eager",
            "event_driven_release": False,
            "outcome_replacement": False,
        },
        "resource_contract": {
            "kv_pool_tokens": 850000,
            "host_pool_gib": 96,
            "context_window_tokens": 262144,
        },
        "conversation_contract": {
            "parent_context": "same physical conversation before and after JOIN",
            "child_context_mode": "fresh",
            "native_task_calls": [2, 3],
            "child_reports": "native task ToolMessages appended to parent conversation",
            "external_planner": False,
            "external_child_orchestration": False,
            "replacement_supervisor": False,
            "context_pack": False,
        },
        "failure_contract": {
            "guard_or_timeout": "remain in denominator; retain censor-safe local intervals",
            "replacement": "none",
            "semantic_gate": "controlled stop is diagnostic and never JCT/training eligible",
        },
        "batches": [
            _batch(
                batch_id="oracle-v2-native-semantic-gate-4-r0",
                manifest_path=gate_path,
                workloads=gate,
                semantic_gate=True,
            ),
            _batch(
                batch_id="oracle-v2-native-trace-64-r0",
                manifest_path=formal_path,
                workloads=formal,
                semantic_gate=False,
            ),
        ],
    }
    _write(output_dir / "collection_plan.json", plan)
    print(output_dir / "collection_plan.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
