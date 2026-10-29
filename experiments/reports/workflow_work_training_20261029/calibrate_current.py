"""Pilot regularization with complete current-profile workflows in calibration."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "experiments/reports/workflow_work_training_20261029"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import capture_split, write

VARIANTS = (
    ("original", 64, 4096, 0., .03),
    ("compact16", 16, 512, .25, .1),
    ("compact32", 32, 1024, .15, .1),
)


def prepare(training, calibration, evaluation, current_capture):
    rows = training["rows"]
    current = [row for row in rows if row["source_capture"] == str(current_capture.resolve())]
    workflows = sorted(
        {row["workflow"] for row in current},
        key=lambda value: hashlib.sha256(("21:" + value).encode()).digest(),
    )
    if len(workflows) != 8:
        raise ValueError("current-profile pilot requires all eight complete workflows")
    # Decide membership before fitting any model, without labels or duration.
    held_out = set(workflows[::2])
    train = [row for row in rows if row["workflow"] not in held_out]
    cal = [*calibration["rows"], *(row for row in current if row["workflow"] in held_out)]
    contract = capture_split(train, cal, evaluation["rows"], require_capture_disjoint=False)
    train_data, cal_data = deepcopy(training), deepcopy(calibration)
    train_data["rows"], cal_data["rows"] = train, cal
    cal_data["sources_sha256"].update(training["sources_sha256"])
    return train_data, cal_data, {
        "training_workflows": len({row["workflow"] for row in train}),
        "calibration_workflows": len({row["workflow"] for row in cal}),
        "current_training_workflows": sorted(set(workflows) - held_out),
        "current_calibration_workflows": sorted(held_out),
        "membership_uses_labels": False, "membership_seed": 21, "split_contract": contract,
        "adaptive_development_after_prior_capture_holdout_failure": True,
        "evaluation_labels_used_for_fitting_or_selection": False,
    }


def select(reports):
    eligible = [
        (name, value) for name, value in reports.items() if value["decision_eligible"]
    ]
    return min(eligible, key=lambda item: (
        -item[1]["calibration"]["macro_f1"],
        item[1]["calibration"]["long_to_short_rate"], item[0],
    ))[0] if eligible else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    paths = {
        "training": HERE / "finish_r1/train32.json",
        "calibration": HERE / "datasets/calibration18.json",
        "evaluation": HERE / "datasets/evaluation18.json",
    }
    source_hashes = {str(path): sha256_file(path) for path in (
        *paths.values(), Path(__file__), ROOT / "scripts/train_workflow_work.py",
    )}
    data = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    current = ROOT / "experiments/raw/workflow_work_train8_source_20261029_r1/native_capture"
    training, calibration, split = prepare(**data, current_capture=current)
    train_path, cal_path = args.output_dir / "train28.json", args.output_dir / "calibration22.json"
    write(train_path, training)
    write(cal_path, calibration)
    write(args.output_dir / "plan.json", {
        **split, "variants": VARIANTS, "input_sha256": source_hashes,
        "selection": "calibration_eligible_then_macro_f1_then_long_to_short",
        "GPU_experiment_started": False,
    })
    reports = {}
    for name, width, vocabulary, dropout, decay in VARIANTS:
        command = [
            sys.executable, str(ROOT / "scripts/train_workflow_work.py"), "fit",
            "--dataset", str(train_path), "--calibration-dataset", str(cal_path),
            "--evaluation-dataset", str(paths["evaluation"]), "--split-mode", "workflow",
            "--hidden-width", str(width), "--vocabulary-size", str(vocabulary),
            "--dropout", str(dropout), "--weight-decay", str(decay),
            "--output-dir", str(args.output_dir / name),
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        write(args.output_dir / (name + ".entry.json"), {
            "command": command, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        })
        if completed.returncode:
            raise RuntimeError("CPU pilot failed; retain its evidence")
        reports[name] = json.loads((args.output_dir / name / "evaluation.json").read_bytes())
    if any(sha256_file(Path(path)) != digest for path, digest in source_hashes.items()):
        raise ValueError("pilot inputs changed during training")
    result = {
        "selected": select(reports), "selection_uses_evaluation_labels": False,
        "training_workflows": split["training_workflows"],
        "calibration_workflows": split["calibration_workflows"],
        "reports": {
            name: {key: report[key] for key in (
                "decision_eligible", "decision_rejection_reasons", "calibration", "test",
            )} for name, report in reports.items()
        },
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    write(args.output_dir / "summary.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
