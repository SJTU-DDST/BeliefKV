"""Repair recorded progress on fixed execution populations, then fit once on CPU."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "experiments/reports/workflow_work_training_20261029"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import FEATURES, SCHEMA
from experiments.reports.workflow_work_training_20261029.profile_text_pilot import augment
from scripts.train_workflow_work import NATIVE_PROGRESS, export_capture, write


def replace_observations(original, exported, evidence, rejections):
    previous = {row["workflow"]: row for row in original["rows"]}
    rows = [row for row in exported if row["workflow"] in previous]
    current = {row["workflow"]: row for row in rows}
    if current.keys() != previous.keys():
        raise ValueError("progress repair changed the fixed execution population")
    for workflow, before in previous.items():
        after = current[workflow]
        if any(after[name] != before[name] for name in (
            "total_work", "project", "task_template", "source_workflow_sha256",
        )):
            raise ValueError("progress repair changed task identity or final logical work")
    return {
        "schema": SCHEMA, "rows": rows, "sources_sha256": evidence,
        "prefill_token_weight": original["prefill_token_weight"],
        "work_unit": original["work_unit"],
        "rejected_non_natural_workflows": rejections,
        "progress_observation_contract": NATIVE_PROGRESS,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=HERE / "matched6_finish_r1")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("progress repair must explicitly disable CUDA")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    inputs = {
        str(path.resolve()): sha256_file(path) for path in (
            Path(__file__), ROOT / "scripts/train_workflow_work.py",
            HERE / "profile_text_pilot.py", HERE / "qualify_input_contract.py",
        )
    }
    data = {}
    for name in ("training", "calibration", "evaluation"):
        path = args.base_dir / (name + ".json")
        inputs[str(path.resolve())] = sha256_file(path)
        data[name] = json.loads(path.read_bytes())
    paths = sorted({row["source_capture"] for value in data.values() for row in value["rows"]})
    state = {
        "status": "exporting_fixed_execution_populations", "inputs_sha256": inputs,
        "captures": paths,
        "population_sizes": {
            name: len({row["workflow"] for row in value["rows"]}) for name, value in data.items()
        },
        "fixed_parameters": {
            "hidden_width": 16, "vocabulary_size": 512,
            "dropout": .25, "weight_decay": .1, "epochs": 300, "seed": 21,
        },
        "data_repair_only": True, "GPU_experiment_started": False,
        "further_GPU_action_registered": False,
    }
    write(args.output_dir / "plan.json", state)
    try:
        exported, evidence, rejections = [], {}, []
        for i, path in enumerate(paths):
            rows, bound, rejected = export_capture(
                Path(path), prefill_weight=.01, native_progress=True,
            )
            write(args.output_dir / f"source_{i}.json", {
                "capture": path, "rows": rows, "sources_sha256": bound,
                "rejected_non_natural_workflows": rejected,
                "progress_observation_contract": NATIVE_PROGRESS,
            })
            exported.extend(rows)
            evidence.update(bound)
            rejections.extend(rejected)
            print(json.dumps({"capture": path, "snapshots": len(rows)}), flush=True)
        for name, original in data.items():
            dataset = augment(replace_observations(original, exported, evidence, rejections))
            write(args.output_dir / (name + ".json"), dataset)
            state[name] = {
                "snapshots": len(dataset["rows"]),
                "native_progress_observations": sum(
                    row["event"] == "native_decode_progress" for row in dataset["rows"]
                ),
                "nonzero_running_snapshots": sum(
                    row["features"][FEATURES.index("running")] > 0 for row in dataset["rows"]
                ),
                "events": dict(Counter(row["event"] for row in dataset["rows"])),
                "final_work_and_execution_population_unchanged": True,
            }
        for name, script, arguments in (
            ("fit", "scripts/train_workflow_work.py", [
                "fit", "--dataset", str(args.output_dir / "training.json"),
                "--calibration-dataset", str(args.output_dir / "calibration.json"),
                "--evaluation-dataset", str(args.output_dir / "evaluation.json"),
                "--split-mode", "workflow", "--hidden-width", "16",
                "--vocabulary-size", "512", "--dropout", ".25", "--weight-decay", ".1",
                "--output-dir", str(args.output_dir / "fit"),
            ]),
            ("input_contract", str(HERE / "qualify_input_contract.py"), [
                "--prototype-dir", str(args.output_dir),
                "--output-dir", str(args.output_dir / "input_binding"),
            ]),
        ):
            if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
                raise ValueError("progress repair source or fixed dataset changed")
            command = [sys.executable, str(ROOT / script), *arguments]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            write(args.output_dir / (name + ".entry.json"), {
                "command": command, "returncode": completed.returncode,
                "stdout": completed.stdout, "stderr": completed.stderr,
            })
            if completed.returncode:
                raise ValueError(name + " failed; preserve its diagnostic entry")
        state.update(
            status="complete_cpu_data_repair_requires_model_and_ranking_review",
            evaluation=json.loads((args.output_dir / "fit/evaluation.json").read_bytes()),
        )
    except BaseException as error:
        state.update(status="failed_cpu_repair_no_GPU_action", error=repr(error))
        raise
    finally:
        write(args.output_dir / "state.json", state)


if __name__ == "__main__":
    main()
