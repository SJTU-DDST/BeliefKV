#!/usr/bin/env python3
"""Fit an OFFLINE/UNCALIBRATED FrontierBelief model on native-reactive train evidence."""

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

from beliefkv.predictor.structured_frontier import (  # noqa: E402
    FrontierBeliefModel,
    load_decision_rows,
    runtime_environment_digest,
    validate_training_corpus_diversity,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_preflight(root: Path) -> tuple[dict[str, Any], str]:
    path = root / "dataset_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"dataset manifest is not an object: {path}")
    source = manifest.get("source") or {}
    contract = source.get("collection_contract") or {}
    environment = source.get("runtime_environment_contract") or {}
    split = manifest.get("split_contract") or {}
    evidence = source.get("native_request_evidence") or {}
    status = evidence.get("status") or {}
    requests = (manifest.get("tables") or {}).get("request_calls") or {}
    count = evidence.get("complete_request_count")
    if (
        manifest.get("dataset_kind") != "beliefkv_p6_training_evidence"
        or manifest.get("evaluation_role") != "frozen_split_local_training_evidence"
        or manifest.get("formal_local_training_eligible") is not True
        or contract.get("plan_id") not in {
            "qwen35-native-reactive-v0520-v1",
            "qwen35-native-reactive-v0520-v2",
            "qwen35-native-reactive-v0520-v3",
            "qwen35-native-reactive-v0520-v4-128root",
            "qwen35-native-reactive-v0520-v5-overlapped-128root",
        }
        or contract.get("split") != "train"
        or contract.get("runtime_policy") != "frozen_native_reactive_v0520"
        or contract.get("raw_trace_eligible") is not True
        or contract.get("model_revision_stable") is not True
        or contract.get("runtime_source_stable") is not True
        or contract.get("predictor_enabled") is not False
        or contract.get("predictive_actions_enabled") is not False
        or environment.get("runtime_kind") != "native_reactive_v0520"
        or split.get("source") != "explicit frozen split manifest"
        or split.get("development_only") is not False
        or not split.get("manifest_digest")
        or evidence.get("schema_version") != 1
        or evidence.get("telemetry_complete") is not True
        or type(count) is not int or count <= 0
        or type(evidence.get("missing_or_incomplete_request_count")) is not int
        or evidence["missing_or_incomplete_request_count"] < 0
        or type(requests.get("row_count")) is not int
        or requests["row_count"] != (
            count + evidence["missing_or_incomplete_request_count"]
        )
        or status.get("schema_version") != 1
        or status.get("source") != "native_sglang_v0520"
        or status.get("writer_error") is not None
        or any(status.get(key) != 0 for key in (
            "pending_request_count", "pending_batch_count",
            "failed_records", "dropped_records",
        ))
        or not isinstance(status.get("record_counts"), dict)
        or (manifest.get("integrity") or {}).get("passes") is not True
    ):
        raise ValueError(f"input is not verified native_reactive_v0520 train: {root}")

    # The loader filters non-train rows; reject mixed exports before it can do so.
    for name in ("frontier_decision_points", "request_calls"):
        table = (manifest.get("tables") or {}).get(name) or {}
        filename = table.get("path")
        if (
            not isinstance(filename, str)
            or filename != f"{name}.jsonl"
            or table.get("sha256") != _digest(root / filename)
        ):
            raise ValueError(f"native input table is not verified: {root / name}")
        for line in (root / filename).read_text(encoding="utf-8").splitlines():
            if json.loads(line).get("split") not in ("train", None):
                raise ValueError(f"non-train {name} row in native input: {root}")
    return manifest, _digest(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, action="append", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--development-only", action="store_true")
    parser.add_argument("--minimum-projects", type=int)
    parser.add_argument("--minimum-tasks", type=int)
    parser.add_argument("--minimum-workflows", type=int)
    args = parser.parse_args(argv)

    overrides = (args.minimum_projects, args.minimum_tasks, args.minimum_workflows)
    if not args.development_only and any(value is not None for value in overrides):
        parser.error("diversity overrides require --development-only")
    minimums = tuple(
        default if value is None else value
        for value, default in zip(overrides, (5, 40, 40))
    )
    if any(value <= 0 for value in minimums):
        parser.error("diversity minimums must be positive")
    if not args.model_version.strip():
        parser.error("--model-version must be nonempty")
    roots = [path.resolve() for path in args.dataset_dir]
    if len(set(roots)) != len(roots):
        raise ValueError("duplicate dataset directory")
    if args.output.resolve() in {
        root / filename
        for root in roots
        for filename in ("dataset_manifest.json", "frontier_decision_points.jsonl",
                         "request_calls.jsonl")
    }:
        raise ValueError("output would overwrite training evidence")

    checked = [_manifest_preflight(root) for root in roots]
    rows, manifests = load_decision_rows(
        roots, allowed_splits=("train",), allow_formal_local=True
    )
    if not rows or any(row.get("split") != "train" for row in rows):
        raise ValueError("fit requires eligible train decision rows")
    if manifests != [manifest for manifest, _ in checked]:
        raise ValueError("dataset manifest count changed during loading")
    diversity = validate_training_corpus_diversity(
        rows,
        minimum_projects=minimums[0],
        minimum_tasks=minimums[1],
        minimum_workflows=minimums[2],
    )
    model = FrontierBeliefModel(model_version=args.model_version)
    summary = model.fit(rows)
    if summary["action_target_count"] != 0 or summary["operational_timing"]["sample_count"] != 0:
        raise ValueError("native fit unexpectedly trained an action head")
    model.save(args.output, metadata={
        "fit_split": "train",
        "runtime_policy": "frozen_native_reactive_v0520",
        "development_only": args.development_only,
        "offline": True,
        "online_eligible": False,
        "predictive_action_eligible": False,
        "calibration_status": "uncalibrated",
        "test_id_status": "sealed_not_evaluated",
        "test_ood_status": "sealed_not_evaluated",
        "action_target_count": 0,
        "pcie_service_head": "not_fitted_no_verified_evidence",
        "dataset_dirs": [str(root) for root in roots],
        "dataset_manifest_file_sha256s": [digest for _, digest in checked],
        "runtime_environment_contract_digests": [
            runtime_environment_digest(manifest["source"]["runtime_environment_contract"])
            for manifest, _ in checked
        ],
        "formal_diversity_gate": diversity,
    })
    print(json.dumps({"output": str(args.output), "summary": summary,
                      "diversity": diversity}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
