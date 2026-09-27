#!/usr/bin/env python3
"""Export native-agent patches for independent SWE-bench correctness grading."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


_INSTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def export_predictions(
    client_dir: Path, workload_manifest: Path, output_dir: Path,
) -> dict:
    summary_path = client_dir / "summary.json"
    client_path = client_dir / "manifest.json"
    summary = _object(summary_path)
    client = _object(client_path)
    workload = _object(workload_manifest)
    run_id = summary.get("run_id")
    model = client.get("config", {}).get("model")
    ids = client.get("instance_ids")
    rows = summary.get("workflows")
    tasks = workload.get("workloads")
    if (
        not isinstance(run_id, str) or not run_id
        or client.get("run_id") != run_id
        or not isinstance(model, str) or not model
        or not isinstance(ids, list) or not ids
        or not isinstance(rows, list) or len(rows) != len(ids)
        or summary.get("workflow_count") != len(ids)
        or not isinstance(tasks, list)
        or client.get("workload_manifest_sha256") != _sha256(workload_manifest)
        or client.get("dataset") != workload.get("dataset")
        or client.get("dataset_revision") != workload.get("dataset_revision")
        or len(set(ids)) != len(ids)
        or any(not isinstance(item, str) or not _INSTANCE_ID.fullmatch(item) for item in ids)
    ):
        raise ValueError("native run and frozen workload identities do not match")
    frozen = {task.get("instance_id"): task for task in tasks if isinstance(task, dict)}
    if len(frozen) != len(tasks) or any(instance_id not in frozen for instance_id in ids):
        raise ValueError("native run contains an unfrozen or duplicate task")

    predictions: dict[str, dict[str, str]] = {}
    patches: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid workflow result")
        instance_id = row.get("instance_id")
        if instance_id not in ids or instance_id in predictions:
            raise ValueError("missing or duplicate workflow result")
        task = frozen[instance_id]
        if (
            row.get("base_commit") != task.get("base_commit")
            or row.get("repo") != task.get("repo")
            or row.get("docker_image") != task.get("docker_image")
            or row.get("outcome") != "completed"
        ):
            raise ValueError(f"workflow identity or outcome changed: {instance_id}")
        patch_path = client_dir / "workflows" / instance_id / "model.patch"
        if not patch_path.is_file() or not patch_path.resolve().is_relative_to(client_dir.resolve()):
            raise ValueError(f"missing or external patch: {instance_id}")
        patch = patch_path.read_text(encoding="utf-8")
        if len(patch.removesuffix("\n")) != row.get("patch_chars"):
            raise ValueError(f"patch changed since workflow summary: {instance_id}")
        predictions[instance_id] = {
            "instance_id": instance_id,
            "model_name_or_path": f"beliefkv-{model}-{run_id}",
            "model_patch": patch,
        }
        patches[instance_id] = _sha256(patch_path)

    if set(predictions) != set(ids):
        raise ValueError("incomplete native workflow results")
    if output_dir.exists():
        raise FileExistsError(f"evaluation inputs already exist: {output_dir}")
    output_dir.mkdir(parents=True)
    predictions_path = output_dir / "preds.json"
    predictions_path.write_text(
        json.dumps(predictions, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "instance_ids": ids,
        "dataset": client["dataset"],
        "dataset_revision": client["dataset_revision"],
        "source_summary_sha256": _sha256(summary_path),
        "source_client_manifest_sha256": _sha256(client_path),
        "source_workload_manifest_sha256": _sha256(workload_manifest),
        "patch_sha256_by_instance": patches,
        "predictions_sha256": _sha256(predictions_path),
        "official_correctness_evaluated": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-dir", type=Path, required=True)
    parser.add_argument("--workload-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = export_predictions(
        args.client_dir, args.workload_manifest, args.output_dir
    )
    print(f"Exported {len(result['instance_ids'])} ungraded predictions to {args.output_dir}")


if __name__ == "__main__":
    main()
