#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    load_evaluation_rows,
    runtime_environment_digest,
)
from beliefkv.predictor.action_targets import load_action_target_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate a fitted FrontierBeliefModel on held-out projects."
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, action="append", required=True)
    parser.add_argument("--action-target", type=Path, action="append")
    parser.add_argument("--action-target-report", type=Path)
    parser.add_argument("--target-coverage", type=float, default=0.9)
    parser.add_argument(
        "--native-heads-only",
        action="store_true",
        help="Calibrate native predictive heads without claiming action calibration.",
    )
    parser.add_argument(
        "--model-version",
        help="Version for the calibrated artifact; defaults to the fitted model version.",
    )
    parser.add_argument(
        "--coverage-report",
        type=Path,
        help=(
            "required for formal calibration; must be a passing frozen "
            "calibration coverage audit"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--development-on-train",
        action="store_true",
        help=(
            "MVP-only mode: calibrate on train/development rows and mark the "
            "artifact development_only. Never use this for formal evidence."
        ),
    )
    args = parser.parse_args(argv)
    if args.output.resolve() == args.model.resolve():
        raise SystemExit("calibration output must not overwrite the fitted model")
    if args.native_heads_only and (
        args.development_on_train or args.action_target or args.action_target_report
        or args.coverage_report
    ):
        raise SystemExit(
            "native heads-only calibration cannot accept development or action evidence"
        )

    raw_model = json.loads(args.model.read_text(encoding="utf-8"))
    model = FrontierBeliefModel.from_dict(raw_model)
    parent_model_version = model.model_version
    if args.model_version:
        model.model_version = args.model_version
    action_targets = load_action_target_rows(args.action_target or ())
    if args.development_on_train:
        rows: list[dict[str, object]] = []
        seen: set[str] = set()
        for directory in args.dataset_dir:
            for path in sorted(
                glob.glob(
                    f"{directory}/p6-0*/gpu0/dataset/"
                    "frontier_decision_points.jsonl"
                )
            ):
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        row = json.loads(line)
                        if row.get("split") not in {"train", "development"}:
                            continue
                        if row.get("training_eligible") is False:
                            continue
                        decision_id = str(row.get("decision_id") or "")
                        if not decision_id or decision_id in seen:
                            continue
                        seen.add(decision_id)
                        rows.append(row)
        if not rows:
            raise SystemExit("no train/development decision points were found")
        manifests: list[dict[str, object]] = []
        summary = model.calibrate(
            rows,
            target_coverage=args.target_coverage,
            allow_development=True,
            action_targets=action_targets,
        )
    else:
        if not args.native_heads_only and (
            not action_targets or args.action_target_report is None
        ):
            raise SystemExit(
                "formal predictor calibration requires action targets and report"
            )
        if not args.native_heads_only and args.coverage_report is None:
            raise SystemExit(
                "formal calibration requires --coverage-report"
            )
        if args.coverage_report is not None:
            coverage = json.loads(
                args.coverage_report.read_text(encoding="utf-8")
            )
            if (
                coverage.get("split") != "calibration"
                or coverage.get("coverage_gate_passed") is not True
                or coverage.get("calibration_blockers")
            ):
                raise SystemExit("calibration coverage report did not pass")
            reported_dirs = {
                str(Path(item).resolve())
                for item in (coverage.get("source") or {}).get(
                    "dataset_dirs", ()
                )
            }
            requested_dirs = {str(item.resolve()) for item in args.dataset_dir}
            if reported_dirs != requested_dirs:
                raise SystemExit(
                    "calibration coverage report does not bind the requested datasets"
                )
        rows, manifests = load_evaluation_rows(
            args.dataset_dir,
            split="calibration",
            allow_formal_local=True,
        )
        if not rows:
            raise SystemExit("no calibration decision points were found")
        fit_projects = set(raw_model.get("metadata", {}).get("fit_projects", ()))
        if not fit_projects:
            raise SystemExit(
                "fitted model has no fit_projects provenance; formal calibration "
                "cannot prove project disjointness"
            )
        calibration_projects = sorted(
            {str(row.get("project") or "unknown") for row in rows}
        )
        overlap = sorted(fit_projects.intersection(calibration_projects))
        if overlap:
            raise SystemExit(
                f"calibration projects overlap model-fitting projects: {overlap}"
            )
        fit_environments = set(
            raw_model.get("metadata", {}).get(
                "runtime_environment_contract_digests", ()
            )
        )
        calibration_environments = {
            runtime_environment_digest(
                (manifest.get("source") or {}).get(
                    "runtime_environment_contract"
                ) or {}
            )
            for manifest in manifests
        }
        if not fit_environments or calibration_environments != fit_environments:
            raise SystemExit(
                "calibration runtime environment differs from the fitted model"
            )
        if args.coverage_report is not None and (coverage.get("source") or {}).get(
            "runtime_environment_digest"
        ) not in fit_environments:
            raise SystemExit(
                "calibration coverage environment differs from the fitted model"
            )
        if args.native_heads_only:
            if len(calibration_projects) < 2 or any(
                (manifest.get("source") or {}).get("collection_contract", {}).get(
                    "plan_id"
                ) != "qwen35-native-reactive-v0520-v1-calibration-66root"
                or (manifest.get("training_readiness") or {}).get(
                    "join_reentry_eligible_count", 0
                ) < 1
                or (manifest.get("training_readiness") or {}).get(
                    "remaining_decode_demand_eligible_request_count", 0
                ) < 1
                or (manifest.get("training_readiness") or {}).get(
                    "pcie_service_eligible_count", 0
                ) < 1
                for manifest in manifests
            ):
                raise SystemExit(
                    "native calibration lacks disjoint projects or required measured labels"
                )
        summary = model.calibrate(
            rows,
            target_coverage=args.target_coverage,
            action_targets=action_targets,
        )
        if args.native_heads_only and (
            summary["observation_counts"].get("remaining_to_return_ms", 0) < 8
            or summary["observation_counts"].get("next_output_tokens", 0) < 8
        ):
            raise SystemExit("native calibration has insufficient held-out head labels")
    calibration_projects = sorted(
        {str(row.get("project") or "unknown") for row in rows}
    )
    metadata = dict(raw_model.get("metadata", {}))
    metadata.update(
        {
            "calibration_split": (
                "development_train" if args.development_on_train else "calibration"
            ),
            "calibration_dataset_dirs": [
                str(item.resolve()) for item in args.dataset_dir
            ],
            "calibration_projects": calibration_projects,
            "calibration_status": (
                "calibrated_native_heads_only"
                if args.native_heads_only else "calibrated"
            ),
            "action_calibration_status": (
                "unavailable_no_action_targets"
                if args.native_heads_only else "calibrated"
            ),
            "parent_model_version": parent_model_version,
            "online_eligible": False,
            "predictive_action_eligible": False,
            "calibration_action_target_count": len(action_targets),
            "calibration_action_target_paths": [
                str(path.resolve()) for path in (args.action_target or ())
            ],
            "calibration_action_target_report": (
                str(args.action_target_report.resolve())
                if args.action_target_report is not None
                else None
            ),
        }
    )
    if args.coverage_report is not None:
        metadata.update(
            {
                "calibration_coverage_report": str(
                    args.coverage_report.resolve()
                ),
                "calibration_coverage_report_sha256": hashlib.sha256(
                    args.coverage_report.read_bytes()
                ).hexdigest(),
                "calibration_coverage_warnings": coverage.get(
                    "coverage_warnings", ()
                ),
                "exact_incremental_action_boundary_available": (
                    (
                        coverage.get("action_boundaries") or {}
                    ).get("exact_incremental_count", 0)
                    > 0
                ),
                "test_id_status": "sealed_not_evaluated",
            }
        )
    if manifests:
        metadata["calibration_dataset_manifest_digests"] = [
            hashlib.sha256(
                json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for item in manifests
        ]
    if args.development_on_train:
        metadata["development_only"] = True
        metadata["calibration_source"] = "train_development_mvp"
    model.save(args.output, metadata=metadata)
    print(
        json.dumps(
            {"output": str(args.output), "summary": summary},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
