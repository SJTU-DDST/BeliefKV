"""Verify saved fold models while preserving reports containing legacy canonical JSON hashes."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.train_workflow_work import write


def verify(directory):
    summary_path = directory / "evaluation.json"
    summary = json.loads(summary_path.read_bytes())
    pins = {str(summary_path.resolve()): sha256_file(summary_path)}
    models = []
    for report in summary["folds"]:
        path = directory / f"fold_{report['fold']}" / "model.json"
        digest = sha256_file(path)
        raw = json.loads(path.read_bytes())
        canonical = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        if report["model_sha256"] not in (digest, canonical):
            raise ValueError("a saved workflow model differs from its original recorded forecast")
        WorkflowWorkModel.load(path, digest)
        pins[str(path.resolve())] = digest
        models.append({
            "fold": report["fold"], "path": str(path.resolve()),
            "model_file_sha256": digest, "canonical_json_sha256": canonical,
            "original_report_sha256": report["model_sha256"],
            "original_report_hashes_file_bytes": report["model_sha256"] == digest,
            "source_bound_model_load_passed": True,
        })
    return {
        "models": models, "inputs_sha256": pins,
        "old_models_and_reports_rewritten": False, "predictions_or_fit_changed": False,
        "correction": "use_model_file_sha256_for_load_or_registration",
        "GPU_action": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous artifact identity verification")
    result = {str(path.resolve()): verify(path) for path in args.directory}
    write(args.output, {
        "studies": result, "verifier_sha256": sha256_file(Path(__file__)),
        "GPU_action": False, "performance_gain_claimed": False,
    })
    print(json.dumps({"studies": len(result), "verified_models": sum(
        len(value["models"]) for value in result.values()
    )}), flush=True)


if __name__ == "__main__":
    main()
