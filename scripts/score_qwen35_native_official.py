#!/usr/bin/env python3
"""Join an official SWE-bench report to an immutable native-agent run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def score_run(client_dir: Path, evaluation_dir: Path, grader_run_id: str) -> dict:
    if not grader_run_id or "/" in grader_run_id or grader_run_id in (".", ".."):
        raise ValueError("invalid SWE-bench grading run ID")
    source_summary = client_dir / "summary.json"
    source_manifest = client_dir / "manifest.json"
    export_manifest = _load(evaluation_dir / "manifest.json")
    summary = _load(source_summary)
    client = _load(source_manifest)
    predictions_path = evaluation_dir / "preds.json"
    predictions = _load(predictions_path)
    ids = export_manifest["instance_ids"]
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) != len(set(ids))
        or not all(isinstance(item, str) and item for item in ids)
        or set(predictions) != set(ids)
        or export_manifest["run_id"] != summary.get("run_id")
        or client.get("run_id") != summary.get("run_id")
        or client.get("instance_ids") != ids
        or summary.get("workflow_count") != len(ids)
        or export_manifest["source_summary_sha256"] != _sha256(source_summary)
        or export_manifest["source_client_manifest_sha256"] != _sha256(source_manifest)
        or export_manifest["predictions_sha256"] != _sha256(predictions_path)
    ):
        raise ValueError("export does not match the native run")
    model_names = {row["model_name_or_path"] for row in predictions.values()}
    if len(model_names) != 1 or any(
        row.get("instance_id") != instance_id
        for instance_id, row in predictions.items()
    ):
        raise ValueError("invalid exported predictions")
    model_name = model_names.pop()
    if "/" in model_name or model_name in (".", ".."):
        raise ValueError("invalid SWE-bench model name")
    for instance_id in ids:
        patch_path = client_dir / "workflows" / instance_id / "model.patch"
        if (
            export_manifest["patch_sha256_by_instance"][instance_id]
            != _sha256(patch_path)
            or predictions[instance_id]["model_patch"]
            != patch_path.read_text(encoding="utf-8")
        ):
            raise ValueError(f"exported patch changed: {instance_id}")

    report_path = evaluation_dir / f"{model_name}.{grader_run_id}.json"
    report = _load(report_path)
    groups = {
        category: set(report.get(f"{category}_ids") or ())
        for category in ("resolved", "unresolved", "empty_patch", "incomplete", "error")
    }
    if (
        report.get("total_instances") != len(ids)
        or set(report.get("submitted_ids") or ()) != set(ids)
        or set.union(*groups.values()) != set(ids)
        or sum(map(len, groups.values())) != len(ids)
        or set(report.get("completed_ids") or ())
        != groups["resolved"] | groups["unresolved"]
    ):
        raise ValueError("official report is not a complete partition of this run")

    for instance_id in groups["resolved"] | groups["unresolved"]:
        instance_report = _load(
            evaluation_dir / "logs" / "run_evaluation" / grader_run_id
            / model_name / instance_id / "report.json"
        )
        outcome = instance_report.get(instance_id)
        if not isinstance(outcome, dict) or outcome.get("resolved") is not (
            instance_id in groups["resolved"]
        ):
            raise ValueError(f"per-instance grading contradicts report: {instance_id}")

    rows = summary.get("workflows")
    if (
        not isinstance(rows, list)
        or len(rows) != len(ids)
        or {row["instance_id"] for row in rows} != set(ids)
    ):
        raise ValueError("native workflow results do not match graded tasks")
    duration = summary.get("duration_seconds")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("native run has no measured duration")
    by_id = {row["instance_id"]: row for row in rows}
    complete = not groups["error"] and not groups["incomplete"]
    useful = [
        instance_id for instance_id in ids
        if instance_id in groups["resolved"]
        and by_id[instance_id].get("outcome") == "completed"
        and by_id[instance_id].get("native_agent_jct_eligible") is True
        and isinstance(by_id[instance_id].get("duration_seconds"), (int, float))
        and math.isfinite(by_id[instance_id]["duration_seconds"])
        and by_id[instance_id]["duration_seconds"] > 0
    ]
    jcts = [float(by_id[instance_id]["duration_seconds"]) for instance_id in useful]
    measurement_complete = complete and all(
        row.get("outcome") == "completed"
        and row.get("native_agent_jct_eligible") is True
        and isinstance(row.get("duration_seconds"), (int, float))
        and math.isfinite(row["duration_seconds"])
        and row["duration_seconds"] > 0
        for row in rows
    )
    return {
        "schema_version": 1,
        "native_run_id": summary["run_id"],
        "grader_run_id": grader_run_id,
        "official_correctness_evaluated": True,
        "complete_official_grading": complete,
        "measurement_complete": measurement_complete,
        "instance_ids": ids,
        "resolved_ids": sorted(groups["resolved"]),
        "correctly_completed_ids": useful,
        "unresolved_ids": sorted(groups["unresolved"]),
        "empty_patch_ids": sorted(groups["empty_patch"]),
        "incomplete_ids": sorted(groups["incomplete"]),
        "error_ids": sorted(groups["error"]),
        "duration_seconds": float(duration),
        "correctly_completed_workflows_per_hour": (
            len(useful) * 3600 / duration if measurement_complete else None
        ),
        "correctly_completed_jct_p50_seconds": median(jcts) if jcts else None,
        "correctly_completed_jct_p95_seconds": _percentile(jcts, 0.95) if jcts else None,
        "source_summary_sha256": _sha256(source_summary),
        "predictions_sha256": _sha256(predictions_path),
        "official_report_sha256": _sha256(report_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-dir", type=Path, required=True)
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--grader-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = score_run(args.client_dir, args.evaluation_dir, args.grader_run_id)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
