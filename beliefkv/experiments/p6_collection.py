from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from beliefkv.experiments.deepagents_swebench import load_workload_bundle


ALLOWED_SPLITS = frozenset({"train", "calibration", "test_id"})
ALLOWED_FANOUT_PROFILES = frozenset(
    {
        "natural",
        "parallel_analysis_2to3",
        "native_subagent_2to3",
        "native_dynamic_1to4",
    }
)
ALLOWED_COLLECTION_POLICIES = frozenset(
    {"frozen_p5_observed", "frozen_native_reactive_v0520"}
)


@dataclass(frozen=True)
class P6CollectionBatch:
    plan_path: Path
    plan_id: str
    batch_id: str
    split: str
    workload_manifest: Path
    workflow_count: int
    concurrency: int
    workflow_arrival_interval_ms: float
    workflow_arrival_batch_size: int
    workflow_arrival_batch_interval_ms: float
    saturated_root_backlog: bool
    preflight_command: str | None
    subagent_fanout_profile: str
    semantic_gate_stop_after_first_join: bool
    runtime_policy: str = "frozen_p5_observed"


def load_collection_batch(
    plan_path: Path,
    batch_id: str,
    *,
    allow_calibration: bool = False,
    allow_test: bool = False,
) -> P6CollectionBatch:
    plan_path = plan_path.expanduser().resolve()
    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("collection plan must be a JSON object")
    if not bool(raw.get("frozen")):
        raise ValueError("collection plan must be frozen")
    if bool(raw.get("predictor_enabled")) or bool(
        raw.get("predictive_actions_enabled")
    ):
        raise ValueError("training evidence must disable predictive policy")
    policy = raw.get("runtime_policy")
    if policy not in ALLOWED_COLLECTION_POLICIES:
        raise ValueError("unsupported frozen collection policy")
    if policy == "frozen_native_reactive_v0520":
        source_plan = raw.get("source_plan")
        if not source_plan or not raw.get("source_plan_sha256"):
            raise ValueError("native reactive plan requires a frozen source plan")
        source_path = Path(str(source_plan)).resolve()
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != raw[
            "source_plan_sha256"
        ]:
            raise ValueError("native reactive source plan changed after freeze")
        if any(
            item.get("split") != "train"
            for item in raw.get("batches", ())
            if isinstance(item, dict)
        ):
            raise ValueError("native reactive plan must contain train batches only")

    matches = [
        item
        for item in raw.get("batches", [])
        if isinstance(item, dict) and item.get("batch_id") == batch_id
    ]
    if len(matches) != 1:
        raise ValueError(f"batch ID must resolve exactly once: {batch_id}")
    batch = matches[0]
    split = str(batch.get("split"))
    if split not in ALLOWED_SPLITS:
        raise ValueError(f"unsupported collection split: {split}")
    if split == "calibration" and not allow_calibration:
        raise PermissionError("calibration collection requires --allow-calibration")
    if split == "test_id" and not allow_test:
        raise PermissionError("sealed test collection requires --allow-test")
    if bool(batch.get("predictive_actions")):
        raise ValueError("batch enables predictive actions")
    if batch.get("policy") != policy:
        raise ValueError("batch policy differs from frozen collection policy")

    arrival_interval_ms = _nonnegative_float(
        batch.get("workflow_arrival_interval_ms", 0.0),
        "workflow_arrival_interval_ms",
    )
    arrival_batch_size = _nonnegative_int(
        batch.get("workflow_arrival_batch_size", 0),
        "workflow_arrival_batch_size",
    )
    arrival_batch_interval_ms = _nonnegative_float(
        batch.get("workflow_arrival_batch_interval_ms", 0.0),
        "workflow_arrival_batch_interval_ms",
    )
    saturated_raw = batch.get("saturated_root_backlog", False)
    if not isinstance(saturated_raw, bool):
        raise ValueError("saturated_root_backlog must be a boolean")
    if saturated_raw and (arrival_batch_size or arrival_interval_ms):
        raise ValueError(
            "saturated_root_backlog cannot be combined with an arrival schedule"
        )
    if arrival_batch_size == 0 and arrival_batch_interval_ms != 0.0:
        raise ValueError(
            "workflow_arrival_batch_interval_ms requires a non-zero batch size"
        )

    manifest_path = Path(str(batch["workload_manifest"])).expanduser().resolve()
    expected_digest = str(batch.get("workload_manifest_sha256", ""))
    if not expected_digest:
        raise ValueError("batch is missing workload_manifest_sha256")
    if _sha256(manifest_path) != expected_digest:
        raise ValueError(f"workload manifest digest mismatch: {manifest_path}")
    bundle = load_workload_bundle(manifest_path)
    workflow_count = int(batch["workflow_count"])
    if len(bundle.workloads) != workflow_count:
        raise ValueError("batch workflow count differs from workload manifest")
    projects = set(batch.get("projects", []))
    if any(item.repo not in projects for item in bundle.workloads):
        raise ValueError("workload project is absent from batch project set")
    declared_images = set(batch.get("docker_images", []))
    if any(item.docker_image not in declared_images for item in bundle.workloads):
        raise ValueError("workload image is absent from batch image set")

    preflight = batch.get("preflight_command")
    semantic_gate_raw = batch.get("semantic_gate_stop_after_first_join", False)
    if not isinstance(semantic_gate_raw, bool):
        raise ValueError("semantic_gate_stop_after_first_join must be a boolean")
    fanout_profile = str(batch.get("subagent_fanout_profile") or "natural")
    if fanout_profile not in ALLOWED_FANOUT_PROFILES:
        raise ValueError(f"unsupported subagent fanout profile: {fanout_profile}")
    return P6CollectionBatch(
        plan_path=plan_path,
        plan_id=str(raw["plan_id"]),
        batch_id=batch_id,
        split=split,
        workload_manifest=manifest_path,
        workflow_count=workflow_count,
        concurrency=int(batch["concurrency"]),
        workflow_arrival_interval_ms=arrival_interval_ms,
        workflow_arrival_batch_size=arrival_batch_size,
        workflow_arrival_batch_interval_ms=arrival_batch_interval_ms,
        saturated_root_backlog=saturated_raw,
        preflight_command=str(preflight) if preflight is not None else None,
        subagent_fanout_profile=fanout_profile,
        semantic_gate_stop_after_first_join=semantic_gate_raw,
        runtime_policy=policy,
    )


def _nonnegative_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite non-negative number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return parsed


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
