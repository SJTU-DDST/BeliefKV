"""CPU-only test of explicit, already-known execution instructions as input."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "experiments/reports/workflow_work_training_20261029"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def controls(capture):
    plan_path = capture.parent / "capture_plan.json"
    manifest_path, = capture.glob("client_*/manifest.json")
    env = json.loads(plan_path.read_bytes())["environment"]
    contract = json.loads(manifest_path.read_bytes())["delegation_contract"]
    fields = {
        "profile": contract["profile"],
        "minimum_parallel_join": contract.get("minimum_completed_root_join_members", "not_required"),
        "child_soft_target": env.get("CHILD_WORK_BUDGET_TARGET_CALLS", "not_configured"),
        "root_phase_target": env.get("ROOT_WORK_BUDGET_TARGET_CALLS", "not_configured"),
        "maximum_spawn_depth": contract["maximum_spawn_depth"],
    }
    prefix = "Declared execution instructions: " + "; ".join(
        f"{name}={value}" for name, value in sorted(fields.items())
    ) + "\n\n"
    return prefix, fields, {
        str(path): sha256_file(path) for path in (plan_path, manifest_path)
    }


def augment(dataset):
    result = deepcopy(dataset)
    profiles, sources = {}, {}
    for row in result["rows"]:
        path = row["source_capture"]
        if path not in profiles:
            prefix, fields, bound = controls(Path(path))
            profiles[path] = {"text_prefix": prefix, "known_execution_controls": fields}
            sources.update(bound)
        row["text"] = profiles[path]["text_prefix"] + row["text"]
    result["sources_sha256"].update(sources)
    result["model_input_contract"] = {
        "kind": "task_plus_declared_execution_instructions",
        "controls_available_before_first_request": True, "profiles": profiles,
        "future_trace_or_total_work_in_input": False,
        "CPU_pilot_only_runtime_prefix_not_yet_wired": True,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    paths = {
        "training": HERE / "current_calibration_r1/train28.json",
        "calibration": HERE / "current_calibration_r1/calibration22.json",
        "evaluation": HERE / "datasets/evaluation18.json",
    }
    inputs = {str(path): sha256_file(path) for path in (
        *paths.values(), Path(__file__), ROOT / "scripts/train_workflow_work.py",
    )}
    for name, path in paths.items():
        write(args.output_dir / (name + ".json"), augment(json.loads(path.read_bytes())))
    command = [
        sys.executable, str(ROOT / "scripts/train_workflow_work.py"), "fit",
        "--dataset", str(args.output_dir / "training.json"),
        "--calibration-dataset", str(args.output_dir / "calibration.json"),
        "--evaluation-dataset", str(args.output_dir / "evaluation.json"),
        "--split-mode", "workflow", "--hidden-width", "16", "--vocabulary-size", "512",
        "--dropout", ".25", "--weight-decay", ".1",
        "--output-dir", str(args.output_dir / "fit"),
    ]
    write(args.output_dir / "plan.json", {
        "input_sha256": inputs, "command": command,
        "architecture_selected_before_this_feature_test": "compact16",
        "GPU_experiment_started": False, "runtime_input_contract_qualified": False,
    })
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    write(args.output_dir / "entry.json", {
        "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr,
    })
    if completed.returncode:
        raise RuntimeError("profile input pilot failed; retain this attempt")
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("profile pilot inputs changed")
    report = json.loads((args.output_dir / "fit/evaluation.json").read_bytes())
    print(json.dumps({key: report[key] for key in ("calibration", "test")}, indent=2))


if __name__ == "__main__":
    main()
