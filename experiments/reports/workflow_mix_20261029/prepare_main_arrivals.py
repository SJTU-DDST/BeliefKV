"""Freeze independent sustained arrival slots using the complete burst control."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import calibrated_arrivals, population_fingerprint
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--control-review", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve the original arrival calibration")
    if complete_work(args.control) is None:
        raise ValueError("calibrate only after complete burst work, drain and actual exit")
    plan, review, population = (
        causal.load(path) for path in (args.control, args.control_review, args.population)
    )
    rows = population["workflows"]
    if (
        Path(review["registration"]).resolve() != args.control.resolve()
        or review["registration_sha256"] != sha256_file(args.control)
        or review["complete_work_and_actual_exit_verified"] is not True
        or review["match_contract"]["workflow_arrivals"] != plan["workflow_arrivals"]
        or review["match_contract"]["token_source_sha256"] != plan["token_source_sha256"]
        or population["population_sha256"] != population_fingerprint(rows)
        or any(
            Path(row["source_capture"]).resolve() != Path(plan["source_arm"]).resolve()
            or row["token_source_sha256"] != plan["token_source_sha256"]
            for row in rows
        )
    ):
        raise ValueError("arrival calibration control and measured population differ")
    contract = {
        "workflow_ids": plan["workflow_ids"], "completed_workflows": len(plan["workflow_ids"]),
        "makespan_seconds": review["metrics"]["makespan_s"], "all_work_complete": True,
        "source_population_sha256": population["population_sha256"],
        "registration_sha256": sha256_file(args.control), "mode": plan["workflow_arrivals"]["mode"],
    }
    calibrated = calibrated_arrivals(
        rows, completed_control=contract, permutation_seed=21, arrival_seed=22,
        initial_workflows=5, offered_load=.9,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    arrivals = args.output_dir / "arrivals.json"
    write(arrivals, calibrated["arrivals"])
    write(args.output_dir / "calibration.json", {
        **calibrated,
        "arrival_path": str(arrivals.resolve()), "arrival_sha256": sha256_file(arrivals),
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (args.control, args.control_review, args.population, Path(__file__))
        },
        "matched_sustained_controls_still_required": True,
        "measured_steady_state_utilization": False,
        "sustained_capacity_competition_verified": False,
        "GPU_action": False, "performance_accepted": False,
    })
    print({
        "initial_workflows": 5, "permutation_seed": 21, "arrival_seed": 22,
        "rate_per_second": calibrated["arrivals"]["rate_per_second"], "GPU_action": False,
    })


if __name__ == "__main__":
    main()
