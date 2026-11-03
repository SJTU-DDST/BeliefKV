"""After natural source completion, export labels and train CPU-only size candidates."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.workflow_work_training_20261029.finish_training import source_ready
from scripts import run_causal_source_capture as capture
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("capture-registration", "dataset", "cohort", "previous-training-data",
                 "baseline-model", "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    source = git_source_state(ROOT)
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or source["dirty"]:
        raise ValueError("CPU postprocessing requires hidden CUDA and clean frozen code")
    paths = (
        args.capture_registration, args.dataset, args.cohort, args.previous_training_data,
        args.baseline_model, args.baseline_evaluation,
        Path(__file__), ROOT / "scripts/export_native_workflow_training.py",
        ROOT / "scripts/train_workflow_task_diversity.py",
        ROOT / "scripts/extend_workflow_prediction_training.py",
        ROOT / "scripts/train_workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_work_training.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    plan = json.loads(args.capture_registration.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    status_path = args.output_dir / "state.json"
    state = {
        "status": "waiting_complete_native_training_source", "code": source,
        "pid": os.getpid(), "inputs_sha256": pins, "GPU_action": False,
        "automatic_GPU_followup": False, "performance_accepted": False,
    }

    def pulse():
        if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
            raise ValueError("a frozen CPU training input changed")
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        capture.reservation.write_json(status_path, state)

    def command(label, arguments):
        pulse()
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / label), *arguments],
            cwd=ROOT, capture_output=True, text=True,
        )
        write(args.output_dir / (label + ".result.json"), {
            "arguments": arguments, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        })
        if completed.returncode:
            raise ValueError(label + " failed; its original output is preserved")

    try:
        while True:
            pulse()
            observed = json.loads(args.capture_registration.with_suffix(".state.json").read_bytes())
            if source_ready(plan, observed):
                break
            time.sleep(15.)
        state["status"] = "exporting_complete_original_training_tasks"
        exported = args.output_dir / "additional_training.json"
        command("export_native_workflow_training.py", [
            "--registration", str(args.capture_registration), "--output", str(exported),
        ])
        state["additional_training_sha256"] = sha256_file(exported)
        state["status"] = "training_and_calibrating_task_diversity_components"
        fitted = args.output_dir / "training"
        command("train_workflow_task_diversity.py", [
            "--dataset", str(args.dataset), "--cohort", str(args.cohort),
            "--previous-training-data", str(args.previous_training_data),
            "--additional-training-data", str(exported),
            "--baseline-model", str(args.baseline_model),
            "--baseline-evaluation", str(args.baseline_evaluation),
            "--output-dir", str(fitted),
        ])
        evaluation = json.loads((fitted / "evaluation.json").read_bytes())
        state.update(
            status="training_complete_requires_review", selected=evaluation["selected"],
            expanded_training_executions=evaluation["expanded_training_executions"],
            training_projects=evaluation["training_projects"],
            evaluation=str((fitted / "evaluation.json").resolve()),
            evaluation_sha256=sha256_file(fitted / "evaluation.json"),
            predictor_deployed=False, JCT_gain_proven=False,
        )
        pulse()
    except BaseException as error:
        state.update(status="stopped_no_GPU_action", error=repr(error))
        raise
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        state["checked_at"] = state["finished_at"]
        capture.reservation.write_json(status_path, state)


if __name__ == "__main__":
    main()
