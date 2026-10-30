"""Choose development arrival slots by declared gap geometry, never policy results."""

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


SEEDS = tuple(range(24, 32))


def screen(workflow_ids, *, rate, makespan):
    mean = 1. / rate
    trials = []
    for seed in SEEDS:
        plan = permuted_poisson_arrival_plan(
            workflow_ids, rate_per_second=rate, seed=seed,
            permutation_seed=21, initial_workflows=5,
        )
        times = [row["offset_seconds"] for row in plan["arrival_schedule"]]
        gaps = [right - left for left, right in zip([0.] + times[5:-1], times[5:])]
        reasons = []
        if gaps[0] > mean:
            reasons.append("first_gap_exceeds_one_mean")
        if max(gaps) > 3 * mean:
            reasons.append("maximum_gap_exceeds_three_means")
        if times[-1] > makespan:
            reasons.append("submission_window_exceeds_complete_control_makespan")
        trials.append({
            "seed": seed, "first_gap_s": gaps[0], "maximum_gap_s": max(gaps),
            "last_submission_s": times[-1], "gaps_s": gaps,
            "arrival_schedule_sha256": plan["arrival_schedule_sha256"],
            "eligible_by_slot_geometry": not reasons, "reasons": reasons,
        })
    selected = next((row["seed"] for row in trials if row["eligible_by_slot_geometry"]), None)
    return {
        "selected_seed": selected, "trials": trials,
        "selection_rule": "first_eligible_seed_in_fixed_24_to_31_sequence",
        "constraints": {
            "first_gap_max_mean_intervals": 1.,
            "largest_gap_max_mean_intervals": 3.,
            "submission_window_max_control_makespans": 1.,
            "initial_workflows": 5, "permutation_seed": 21,
        },
        "scope": {
            "development_schedule_conditioned_on_slot_geometry": True,
            "unconditional_Poisson_sample_claimed": False,
            "individual_gaps_clipped_or_rescaled": False,
            "GPU_policy_results_used_for_seed_selection": False,
            "HBM_pressure_or_competition_proven": False,
            "new_population_requires_its_matching_capacity_calibration": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--control-review", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve all old arrival selections")
    population, review = causal.load(args.population), causal.load(args.control_review)
    makespan = review["metrics"]["makespan_s"]
    result = screen(
        [row["workflow_id"] for row in population["workflows"]],
        rate=.9 * len(population["workflows"]) / makespan, makespan=makespan,
    )
    if result["selected_seed"] is None:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        write(args.output_dir / "selection.json", result)
        raise ValueError("no predeclared seed satisfies the slot geometry; no GPU action")
    command = [
        sys.executable, str(ROOT / "experiments/reports/workflow_mix_20261029/prepare_main_arrivals.py"),
        "--control", str(args.control), "--control-review", str(args.control_review),
        "--population", str(args.population), "--output-dir", str(args.output_dir),
        "--arrival-seed", str(result["selected_seed"]),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    result["inputs_sha256"] = {
        str(path.resolve()): sha256_file(path)
        for path in (args.control, args.control_review, args.population, Path(__file__))
    }
    result["GPU_action"] = False
    write(args.output_dir / "selection.json", result)
    print({k: result[k] for k in ("selected_seed", "selection_rule")})


if __name__ == "__main__":
    main()
