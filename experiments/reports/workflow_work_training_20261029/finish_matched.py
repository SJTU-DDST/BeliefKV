"""Fit a fixed current-profile model after the matched six-task native capture."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
MAIN = Path("/home/longhao/experiment/BeliefKV")
HERE = MAIN / "experiments/reports/workflow_work_training_20261029"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.workflow_work_training_20261029.finish_training import (
    audit_finished_source, source_ready,
)
from experiments.reports.workflow_work_training_20261029.profile_text_pilot import augment
from scripts import run_causal_source_capture as capture
from scripts.train_workflow_work import export_capture, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or git_source_state(ROOT)["dirty"]:
        raise ValueError("matched training requires CPU-only clean frozen sources")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    base_paths = {
        "training": HERE / "current_calibration_r1/train28.json",
        "calibration": HERE / "current_calibration_r1/calibration22.json",
        "evaluation": HERE / "datasets/evaluation18.json",
    }
    inputs = {
        str(path.resolve()): sha256_file(path)
        for path in (args.registration, *base_paths.values(), Path(__file__),
                     ROOT / "scripts/train_workflow_work.py")
    }
    source = capture.load(args.registration)
    if source["environment"]["ROOT_COUNT"] != "6":
        raise ValueError("this declared expansion requires the six-task matched source")
    state_path = args.output_dir / "state.json"
    state = {
        "status": "waiting_complete_matched6_source",
        "watcher": capture.reservation.process(os.getpid()),
        "application": git_source_state(ROOT), "inputs_sha256": inputs,
        "fixed_model_parameters": {
            "hidden_width": 16, "vocabulary_size": 512, "dropout": .25, "weight_decay": .1,
            "epochs": 300, "seed": 21, "selected_before_this_collection": True,
        },
        "GPU_experiment_started": False, "performance_accepted": False,
    }

    def pulse():
        if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
            raise ValueError("matched training inputs changed")
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        capture.reservation.write_json(state_path, state)

    def run(name, script, arguments):
        pulse()
        command = [sys.executable, str(ROOT / script), *arguments]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        write(args.output_dir / (name + ".entry.json"), {
            "command": command, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        })
        if completed.returncode:
            raise ValueError(name + " failed; preserve its entry")

    try:
        while True:
            pulse()
            observed = capture.load(args.registration.with_suffix(".state.json"))
            if source_ready(source, observed):
                break
            time.sleep(15.)
        state["source_completion_sha256"] = audit_finished_source(source, observed)
        state["status"] = "exporting_matched_training_data"
        pulse()
        rows, bound, rejected = export_capture(
            Path(source["run_root"]) / "native_capture", prefill_weight=.01,
        )
        if rejected or len({row["workflow"] for row in rows}) != 6:
            raise ValueError("matched training requires all six natural workflows")
        data = {name: capture.load(path) for name, path in base_paths.items()}
        combined = deepcopy(data["training"])
        combined["rows"].extend(rows)
        combined["sources_sha256"].update(bound)
        if len({row["workflow"] for row in combined["rows"]}) != 34:
            raise ValueError("matched training must retain the existing28 plus6 new workflows")
        for name, dataset in {**data, "training": combined}.items():
            write(args.output_dir / (name + ".json"), augment(dataset))
        state["status"] = "fitting_fixed_profile_model"
        run("fit", "scripts/train_workflow_work.py", [
            "fit", "--dataset", str(args.output_dir / "training.json"),
            "--calibration-dataset", str(args.output_dir / "calibration.json"),
            "--evaluation-dataset", str(args.output_dir / "evaluation.json"),
            "--split-mode", "workflow", "--hidden-width", "16", "--vocabulary-size", "512",
            "--dropout", ".25", "--weight-decay", ".1",
            "--output-dir", str(args.output_dir / "fit"),
        ])
        state["status"] = "verifying_runtime_input_contract"
        run("input_contract", "experiments/reports/workflow_work_training_20261029/qualify_input_contract.py", [
            "--prototype-dir", str(args.output_dir),
            "--output-dir", str(args.output_dir / "input_binding"),
        ])
        state["status"] = "auditing_observed_paused_competition"
        rank_path = args.output_dir / "paused_rank.json"
        run("paused_rank", "experiments/reports/workflow_work_training_20261029/audit_paused_rank.py", [
            "--arm", str(MAIN / "experiments/raw/decay_tick2s_no_handoff_20261030_r1/full"),
            "--source", str(MAIN / "experiments/raw/locobench_parallel_retry_source_20261026_v230/native_capture"),
            "--dataset", str(args.output_dir / "evaluation.json"),
            "--model", str(args.output_dir / "input_binding/model.json"),
            "--output", str(rank_path),
        ])
        rank = capture.load(rank_path)
        state.update({
            "status": "matched_model_complete_requires_review",
            "evaluation": capture.load(args.output_dir / "fit/evaluation.json"),
            "paused_pair_metrics": rank["pair_metrics"],
            "paused_continuous_metrics": rank["continuous_pair_metrics"],
            "further_GPU_action_registered": False,
        })
    except BaseException as error:
        state.update(status="stopped_no_GPU_action", error=repr(error))
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        capture.reservation.write_json(state_path, state)


if __name__ == "__main__":
    main()
