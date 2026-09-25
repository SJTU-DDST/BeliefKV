#!/usr/bin/env python3
"""Promote a replay-verified Qwen3.5/v0.5.20 demand model for admission only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.structured_frontier import (  # noqa: E402
    FrontierBeliefModel,
    evaluate_frontier_model,
    runtime_environment_digest,
)
from beliefkv.runtime.sglang_v0520_prediction import (  # noqa: E402
    validate_admission_artifact,
)

MODEL_NAME = "Qwen3.5-35B-A3B"
MODEL_FILES = (
    "config.json",
    "model.safetensors.index.json",
    "tokenizer.json",
    "tokenizer_config.json",
)
SGLANG_COMMIT = "94602c9c2b7cbdb8efd5c52802dac6a1c180089e"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(type(value) is dict, f"expected JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _count(value: Any, name: str, minimum: int) -> None:
    _require(type(value) is int and value >= minimum, f"invalid {name}")


def _number(value: Any, name: str, *, low: float, high: float) -> float:
    _require(
        type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
        f"invalid {name}",
    )
    return float(value)


def _projects(value: Any, name: str, minimum: int) -> set[str]:
    _require(
        type(value) is list
        and len(value) >= minimum
        and all(type(item) is str and item and item != "unknown" for item in value)
        and len(set(value)) == len(value),
        f"invalid {name}",
    )
    return set(value)


def _source_contract(metadata: dict[str, Any], model_path: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    sources = metadata.get("semantic_source_runtime_environment_contracts")
    _require(type(sources) is list and len(sources) == 1, "expected one semantic source contract")
    contract = sources[0]
    _require(type(contract) is dict, "invalid semantic source contract")
    identity = contract.get("server_identity") or {}
    _require(
        identity.get("served_model_name") == MODEL_NAME
        and Path(identity.get("model_path") or "/").resolve() == model_path
        and identity.get("sglang_version") == "0.5.20"
        and identity.get("weight_dtype") == "bfloat16"
        and identity.get("resolved_kv_dtype") == "bfloat16"
        and contract.get("sglang_commit") == SGLANG_COMMIT,
        "model/runtime identity mismatch",
    )
    patch = contract.get("sglang_patch_sha256")
    profile = (contract.get("runtime_profile") or {}).get("sha256")
    _require(
        patch == _sha(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch")
        and type(profile) is str and len(profile) == 64
        and bool((contract.get("hardware") or {}).get("uuid")),
        "incomplete runtime provenance",
    )
    hashes = contract.get("model_revision_sha256")
    _require(
        type(hashes) is dict and set(hashes) == set(MODEL_FILES),
        "incomplete model revision hashes",
    )
    for name in MODEL_FILES:
        expected = hashes[name]
        _require(
            type(expected) is str and len(expected) == 64
            and all(c in "0123456789abcdef" for c in expected)
            and _sha(model_path / name) == expected,
            f"model revision mismatch: {name}",
        )
        if name in inventory["files"]:
            _require(
                inventory["files"][name].get("sha256") == expected,
                f"model inventory mismatch: {name}",
            )
    _require(
        (inventory["files"].get("config.json") or {}).get("sha256") == hashes["config.json"]
        and (inventory["files"].get("model.safetensors.index.json") or {}).get("sha256")
        == hashes["model.safetensors.index.json"],
        "model inventory is not pinned",
    )
    config = _object(model_path / "config.json")
    text_config = config.get("text_config") or {}
    _require(
        config.get("model_type") == "qwen3_5_moe"
        and "Qwen3_5MoeForConditionalGeneration" in config.get("architectures", ())
        and text_config.get("model_type") == "qwen3_5_moe_text"
        and text_config.get("num_hidden_layers") == 40
        and text_config.get("full_attention_interval") == 4,
        "model config is not Qwen3.5-35B-A3B",
    )
    return contract


def _dataset(
    directory: Path, split: str, contract: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _object(directory / "dataset_manifest.json")
    source = manifest.get("source") or {}
    collection = source.get("collection_contract") or {}
    frozen = manifest.get("split_contract") or {}
    _require(
        manifest.get("dataset_kind") == "beliefkv_p6_training_evidence"
        and manifest.get("formal_local_training_eligible") is True
        and manifest.get("evaluation_role") in (
            "frozen_split_training_evidence",
            "frozen_split_local_training_evidence",
        )
        and frozen.get("source") == "explicit frozen split manifest"
        and frozen.get("development_only") is False
        and bool(frozen.get("manifest_digest"))
        and set((frozen.get("counts_on_request_calls") or {})) == {split}
        and collection.get("split") == split
        and bool(collection.get("plan_id"))
        and collection.get("runtime_source_stable") is True
        and collection.get("predictor_enabled") is False
        and collection.get("predictive_actions_enabled") is False
        and bool(source.get("workload_manifest_sha256"))
        and source.get("runtime_environment_contract") == contract,
        f"invalid frozen {split} dataset: {directory}",
    )
    rows: list[dict[str, Any]] = []
    if split != "train":
        with (directory / "frontier_decision_points.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    row = json.loads(line)
                    _require(
                        row.get("split") == split
                        and row.get("training_eligible") is not False
                        and type(row.get("decision_id")) is str
                        and bool(row["decision_id"])
                        and int(row.get("schema_version") or 0) >= 2,
                        f"invalid {split} decision row: {directory}",
                    )
                    rows.append(row)
        _require(bool(rows), f"no {split} decision rows: {directory}")
    return manifest, rows


def _evidence_metrics(
    model: FrontierBeliefModel,
    directories: list[Path],
    split: str,
    contract: dict[str, Any],
    report_path: Path,
    min_workflows: int,
    min_projects: int,
    criteria: dict[str, Any],
) -> tuple[str, list[str]]:
    rows: list[dict[str, Any]] = []
    projects: set[str] = set()
    decisions: set[str] = set()
    workflows: set[str] = set()
    for directory in directories:
        _, shard = _dataset(directory, split, contract)
        for row in shard:
            _require(row["decision_id"] not in decisions, "duplicate held-out decision")
            decisions.add(row["decision_id"])
            _require(type(row.get("workflow_id")) is str and bool(row["workflow_id"]), "missing workflow")
            workflows.add(row["workflow_id"])
            project = row.get("project")
            _require(type(project) is str and project not in ("", "unknown"), "missing project")
            projects.add(project)
        rows.extend(shard)
    _count(len(workflows), f"{split} workflows", min_workflows)
    _count(len(projects), f"{split} projects", min_projects)
    actual = evaluate_frontier_model(model, rows)
    actual["evaluation_projects"] = sorted(projects)
    reported = _object(report_path)
    _require(_digest(actual) == _digest(reported), f"{split} metrics do not match replay")
    _require(actual.get("splits") == [split], f"wrong {split} evaluation split")
    demand = (actual.get("scalar") or {}).get("next_output_tokens") or {}
    availability = (actual.get("target_availability") or {}).get("next_output_demand") or {}
    _number(demand.get("episode_weight"), "demand weight", low=1, high=math.inf)
    _count(demand.get("interval_local_episode_count"), "demand interval episodes", 1)
    _number(
        demand.get("episode_weighted_mae"), "demand MAE",
        low=0, high=criteria["max_next_output_mae"],
    )
    _number(
        demand.get("workflow_macro_local_episode_interval_coverage"),
        "demand interval coverage", low=criteria["min_interval_coverage"], high=1,
    )
    _number(
        availability.get("available_rate"), "demand availability",
        low=criteria["min_available_rate"], high=1,
    )
    _number(availability.get("episode_weight"), "available demand weight", low=1, high=math.inf)
    return _sha(report_path), sorted(projects)


def promote(
    artifact: Path,
    calibration_report: Path,
    evaluation_report: Path,
    evaluation_dirs: list[Path],
    criteria_path: Path,
    inventory_path: Path,
    model_path: Path,
    output: Path,
) -> str:
    _require(artifact.resolve() != output.resolve(), "output must not overwrite input artifact")
    raw = _object(artifact)
    metadata = raw.get("metadata")
    _require(type(metadata) is dict, "missing artifact metadata")
    _require(
        type(raw.get("schema_version")) is int and raw["schema_version"] in (4, 5, 6, 7)
        and raw.get("model_kind") == "pooled_action_conditional_particle_frontier"
        and metadata.get("fit_split") == "train"
        and metadata.get("calibration_split") == "calibration"
        and metadata.get("calibration_status") == "calibrated"
        and metadata.get("development_only") is False
        and metadata.get("online_eligible") is False
        and metadata.get("beliefkv_worktree_clean") is True
        and metadata.get("test_id_status") == "sealed_not_evaluated",
        "artifact is not a sealed formal calibrated fit",
    )
    fit = _projects(metadata.get("fit_projects"), "fit projects", 5)
    cal = _projects(metadata.get("calibration_projects"), "calibration projects", 2)
    _require(fit.isdisjoint(cal), "fit and calibration projects overlap")
    diversity = metadata.get("formal_diversity_gate") or {}
    _require(
        set(diversity.get("projects") or ()) == fit
        and diversity.get("project_count") == len(fit),
        "invalid fit diversity",
    )
    _count(diversity.get("task_count"), "fit tasks", 40)
    _count(diversity.get("workflow_count"), "fit workflows", 40)
    _count(metadata.get("fit_task_count"), "fit task count", 40)
    training = raw.get("training_summary") or {}
    calibration = raw.get("calibration_summary") or {}
    _require(
        set((training.get("split_counts") or {})) == {"train"}
        and calibration.get("split") == "calibration"
        and calibration.get("training_counts_refit") is False,
        "fit/calibration split mismatch",
    )
    _count((training.get("observation_counts") or {}).get("next_output_demand"), "train demand samples", 1)
    _count((calibration.get("observation_counts") or {}).get("next_output_tokens"), "calibration demand samples", 1)
    coverage = _number(calibration.get("target_coverage"), "calibration target", low=0.01, high=0.99)
    _require(raw.get("calibration_coverage") == coverage, "calibration target mismatch")

    criteria = _object(criteria_path)
    _require(
        set(criteria) == {"max_next_output_mae", "min_interval_coverage", "min_available_rate"},
        "invalid admission criteria",
    )
    _number(criteria["max_next_output_mae"], "maximum demand MAE", low=0.001, high=math.inf)
    _number(criteria["min_interval_coverage"], "minimum interval coverage", low=max(0.8, coverage - 0.1), high=1)
    _number(criteria["min_available_rate"], "minimum demand availability", low=0.9, high=1)

    inventory = _object(inventory_path)
    model_path = model_path.resolve()
    _require(
        inventory.get("schema_version") == 1
        and Path(inventory.get("model_path") or "/").resolve() == model_path
        and type(inventory.get("files")) is dict,
        "invalid pinned model inventory",
    )
    contract = _source_contract(metadata, model_path, inventory)
    profile_path = Path(metadata.get("deployment_runtime_profile_path") or "")
    _require(
        profile_path.is_file()
        and _sha(profile_path) == contract["runtime_profile"]["sha256"]
        and _object(profile_path) == metadata.get("deployment_runtime_profile"),
        "deployment profile missing or changed",
    )
    contract_digest = runtime_environment_digest(contract)
    _require(
        metadata.get("runtime_environment_contract_digests") == [contract_digest]
        and metadata.get("runtime_environment_contracts") == [contract],
        "fit source provenance mismatch",
    )
    train_dirs = metadata.get("dataset_dirs")
    cal_dirs = metadata.get("calibration_dataset_dirs")
    _require(
        type(train_dirs) is list and bool(train_dirs)
        and type(cal_dirs) is list and bool(cal_dirs)
        and bool(evaluation_dirs),
        "missing frozen dataset directories",
    )
    _require(
        len({str(Path(p).resolve()) for p in (*train_dirs, *cal_dirs, *evaluation_dirs)})
        == len(train_dirs) + len(cal_dirs) + len(evaluation_dirs),
        "held-out datasets overlap",
    )
    train_hashes = metadata.get("dataset_manifest_file_sha256s")
    _require(type(train_hashes) is list and len(train_hashes) == len(train_dirs), "missing train manifest hashes")
    train_coverage = _object(Path(train_dirs[0]) / "coverage_report.json")
    _require(
        _sha(Path(train_dirs[0]) / "coverage_report.json") == metadata.get("coverage_report_sha256")
        and train_coverage.get("coverage_gate_passed") is True
        and train_coverage.get("dataset_manifest_sha256") in train_hashes,
        "train coverage did not pass",
    )
    train_decisions: set[str] = set()
    train_workflows: set[str] = set()
    train_projects: set[str] = set()
    train_tasks: set[tuple[str, str, str]] = set()
    train_demand = 0
    for directory, expected in zip(train_dirs, train_hashes):
        root = Path(directory).resolve()
        _dataset(root, "train", contract)
        _require(_sha(root / "dataset_manifest.json") == expected, "train manifest changed")
        with (root / "frontier_decision_points.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("split") != "train" or row.get("training_eligible") is False:
                    continue
                decision = row.get("decision_id")
                workflow = row.get("workflow_id")
                project = row.get("project")
                _require(
                    type(decision) is str and bool(decision) and decision not in train_decisions
                    and type(workflow) is str and bool(workflow)
                    and type(project) is str and project in fit
                    and int(row.get("schema_version") or 0) >= 2,
                    "invalid train decision row",
                )
                train_decisions.add(decision)
                train_workflows.add(workflow)
                train_projects.add(project)
                train_tasks.add((
                    project, str(row.get("instance_id") or "unknown"),
                    str(row.get("base_commit") or "unknown"),
                ))
                train_demand += sum(
                    label.get("next_output_tokens") is not None
                    and (label.get("target_training_eligible") or {}).get(
                        "next_output_demand", not label.get("censored", False)
                    )
                    for label in row.get("labels", ())
                )
    _require(
        train_projects == fit
        and len(train_decisions) == training.get("decision_point_count")
        and len(train_workflows) == diversity.get("workflow_count")
        and len(train_tasks) == metadata.get("fit_task_count")
        and train_demand == training["observation_counts"]["next_output_demand"],
        "train evidence does not match fitted counts",
    )
    cal_manifests = []
    for directory in cal_dirs:
        manifest, _ = _dataset(Path(directory).resolve(), "calibration", contract)
        cal_manifests.append(_digest(manifest))
    _require(
        metadata.get("calibration_dataset_manifest_digests") == cal_manifests,
        "calibration manifests changed",
    )
    audit_path = Path(metadata.get("calibration_coverage_report") or "")
    _require(
        audit_path.is_file()
        and _sha(audit_path) == metadata.get("calibration_coverage_report_sha256"),
        "calibration coverage audit missing or changed",
    )
    audit = _object(audit_path)
    _require(
        audit.get("split") == "calibration"
        and audit.get("coverage_gate_passed") is True
        and not audit.get("calibration_blockers")
        and set((audit.get("source") or {}).get("dataset_dirs") or ())
        == {str(Path(p).resolve()) for p in cal_dirs}
        and (audit.get("source") or {}).get("runtime_environment_digest") == contract_digest,
        "calibration coverage audit did not pass",
    )
    _require(
        sorted((audit.get("source") or {}).get("dataset_manifest_sha256s") or ())
        == sorted(_sha(Path(p) / "dataset_manifest.json") for p in cal_dirs),
        "calibration coverage audit did not pass",
    )
    model = FrontierBeliefModel.from_dict(raw)
    cal_sha, replay_cal = _evidence_metrics(
        model, [Path(p).resolve() for p in cal_dirs], "calibration", contract,
        calibration_report, 16, 2, criteria,
    )
    _require(set(replay_cal) == cal and fit.isdisjoint(replay_cal), "calibration projects mismatch")
    _require(
        _object(calibration_report).get("decision_point_count")
        == calibration.get("decision_point_count"),
        "calibration summary does not match replay",
    )
    eval_sha, replay_eval = _evidence_metrics(
        model, [p.resolve() for p in evaluation_dirs], "test_id", contract,
        evaluation_report, 16, 2, criteria,
    )
    _require(fit.isdisjoint(replay_eval) and cal.isdisjoint(replay_eval), "evaluation projects overlap")
    metadata = dict(metadata)
    metadata.update({
        "online_eligible": True,
        "predictive_action_eligible": False,
        "test_id_status": "evaluated_for_admission_only",
        "admission_eligibility_evidence": {
            "scope": "qwen35_v0520_next_output_admission_only",
            "model_inventory_sha256": _sha(inventory_path),
            "criteria_sha256": _sha(criteria_path),
            "calibration_report_sha256": cal_sha,
            "evaluation_report_sha256": eval_sha,
            "evaluation_dataset_manifest_sha256s": [
                _sha(p / "dataset_manifest.json") for p in evaluation_dirs
            ],
            "evaluation_projects": replay_eval,
        },
    })
    raw["metadata"] = metadata
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(raw, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        digest = _sha(temporary)
        validate_admission_artifact(str(temporary), expected_sha256=digest, model_path=str(model_path))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--calibration-report", type=Path, required=True)
    parser.add_argument("--evaluation-report", type=Path, required=True)
    parser.add_argument("--evaluation-dataset-dir", type=Path, action="append", required=True)
    parser.add_argument("--criteria", type=Path, required=True)
    parser.add_argument("--model-inventory", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    digest = promote(
        args.artifact, args.calibration_report, args.evaluation_report,
        args.evaluation_dataset_dir, args.criteria, args.model_inventory,
        args.model_path, args.output,
    )
    print(json.dumps({"output": str(args.output.resolve()), "sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
