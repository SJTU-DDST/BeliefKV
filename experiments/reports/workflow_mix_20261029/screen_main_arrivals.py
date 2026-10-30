"""Choose development arrival slots by declared gap geometry, never policy results."""

import argparse
import math
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


def screen(workflow_ids, *, rate, makespan, seeds=SEEDS, window_ratio=None):
    if (
        len(workflow_ids) <= 5
        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in (rate, makespan))
        or not seeds or len(seeds) > 64 or len(set(seeds)) != len(seeds)
        or any(type(seed) is not int or seed < 0 for seed in seeds)
        or window_ratio is not None and (
            len(window_ratio) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                   for value in window_ratio)
            or window_ratio[0] > window_ratio[1]
        )
    ):
        raise ValueError("arrival screening needs a bounded declared seed family and finite geometry")
    mean = 1. / rate
    expected_window = (len(workflow_ids) - 5) * mean
    trials = []
    for seed in seeds:
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
        if window_ratio is not None:
            if times[-1] < window_ratio[0] * expected_window:
                reasons.append("submission_window_too_bursty_for_expected_rate")
            if times[-1] > window_ratio[1] * expected_window:
                reasons.append("submission_window_too_sparse_for_expected_rate")
        trials.append({
            "seed": seed, "first_gap_s": gaps[0], "maximum_gap_s": max(gaps),
            "last_submission_s": times[-1], "gaps_s": gaps,
            "realized_mean_gap_s": times[-1] / len(gaps),
            "realized_to_expected_window_ratio": times[-1] / expected_window,
            "arrival_schedule_sha256": plan["arrival_schedule_sha256"],
            "eligible_by_slot_geometry": not reasons, "reasons": reasons,
        })
    selected = next((row["seed"] for row in trials if row["eligible_by_slot_geometry"]), None)
    return {
        "selected_seed": selected, "trials": trials,
        "selection_rule": "first_eligible_seed_in_declared_order",
        "seed_family": list(seeds),
        "constraints": {
            "first_gap_max_mean_intervals": 1.,
            "largest_gap_max_mean_intervals": 3.,
            "submission_window_max_control_makespans": 1.,
            "initial_workflows": 5, "permutation_seed": 21,
            "submission_window_expected_ratio_bounds": window_ratio,
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
    parser.add_argument("--seed-start", type=int, default=24)
    parser.add_argument("--seed-stop", type=int, default=31)
    parser.add_argument("--expected-window-min-ratio", type=float)
    parser.add_argument("--expected-window-max-ratio", type=float)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve all old arrival selections")
    population, review = causal.load(args.population), causal.load(args.control_review)
    makespan = review["metrics"]["makespan_s"]
    if (
        not 0 <= args.seed_start <= args.seed_stop <= args.seed_start + 63
        or (args.expected_window_min_ratio is None) != (args.expected_window_max_ratio is None)
    ):
        raise ValueError("declare at most64 seeds and both expected-window bounds together")
    result = screen(
        [row["workflow_id"] for row in population["workflows"]],
        rate=len(population["workflows"]) / makespan * .9, makespan=makespan,
        seeds=tuple(range(args.seed_start, args.seed_stop + 1)),
        window_ratio=(
            None if args.expected_window_min_ratio is None else
            (args.expected_window_min_ratio, args.expected_window_max_ratio)
        ),
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
    frozen_arrivals = args.output_dir / "arrivals.json"
    frozen = causal.load(frozen_arrivals)
    selected = next(row for row in result["trials"] if row["seed"] == result["selected_seed"])
    result["frozen_arrival_schedule_match_verified"] = (
        frozen["arrival_schedule_sha256"] == selected["arrival_schedule_sha256"]
    )
    result["frozen_arrival_schedule_sha256"] = frozen["arrival_schedule_sha256"]
    result["inputs_sha256"] = {
        str(path.resolve()): sha256_file(path)
        for path in (
            args.control, args.control_review, args.population, Path(__file__), frozen_arrivals,
        )
    }
    result["GPU_action"] = False
    write(args.output_dir / "selection.json", result)
    if not result["frozen_arrival_schedule_match_verified"]:
        raise ValueError("screened and frozen arrival schedules differ; preserve evidence, no GPU action")
    print({k: result[k] for k in ("selected_seed", "selection_rule")})


if __name__ == "__main__":
    main()
