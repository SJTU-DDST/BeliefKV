"""Declare the user-accepted 56-project arrival diagnostic without collecting work."""

import argparse
import json
from pathlib import Path
import random
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay import evaluate_causal_plan
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import load_overlay
from scripts import run_causal_comparison as causal
from scripts.prepare_composite_comparison import BASE, configuration, measurement_protocol
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-arm", "source-registration", "tool-timing", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve every preparation and seed selection")
    population_path = args.source_arm / "composite_source.json"
    population = json.loads(population_path.read_bytes())
    profiles = {row["workflow_id"]: dict(row) for row in population["selected_workflows"]}
    if len(profiles) != 56 or population["population"]["projects"] != 56:
        raise ValueError("this preparation keeps all 56 original projects")
    graph, _, evidence = source_plan(args.source_arm)
    changed, _ = load_overlay(graph, args.tool_timing)
    external = evaluate_causal_plan(
        changed, dict.fromkeys(changed.request_usage, 0.),
    )["workflow_elapsed_ms"]
    for identity, profile in profiles.items():
        profile["fixed_external_path_s"] = external[identity] / 1000.
    fields = ("model_work_units", "initial_prompt_tokens", "fixed_external_path_s")

    def average(ids):
        return {name: mean(profiles[identity][name] for identity in ids) for name in fields}

    overall, trials = average(profiles), []
    for permutation_seed in range(47, 100):
        ids = sorted(profiles)
        random.Random(permutation_seed).shuffle(ids)
        groups = [average(ids[:12]), average(ids[12:30]), average(ids[30:])]
        checks = {
            "group_model_means_within_15_percent": all(
                .85 <= row[fields[0]] / overall[fields[0]] <= 1.15 for row in groups
            ),
            "group_context_means_within_20_percent": all(
                .8 <= row[fields[1]] / overall[fields[1]] <= 1.2 for row in groups
            ),
            "group_fixed_external_path_means_within_20_percent": all(
                .8 <= row[fields[2]] / overall[fields[2]] <= 1.2 for row in groups
            ),
            "initial_context_mean_no_larger_than_population": (
                average(ids[:8])[fields[1]] <= overall[fields[1]]
            ),
        }
        trials.append({"seed": permutation_seed, "groups": groups, "checks": checks})
        if all(checks.values()):
            break
    else:
        raise ValueError("no statically balanced permutation; preserve the original pool")
    arrivals, cohort = measurement_protocol(
        profiles, rate_per_second=1. / 90., arrival_seed=35,
        permutation_seed=permutation_seed, initial_workflows=8, warmup=12, measured=18,
    )
    offsets = [row["offset_seconds"] for row in arrivals["arrival_schedule"]]
    max_gap = max(right - left for left, right in zip(offsets, offsets[1:]))
    if not (
        4320 <= offsets[-1] <= 4752 and max_gap <= 360
        and offsets[-1] - offsets[29] >= 26 * 90
    ):
        raise ValueError("declared seed35 does not satisfy its static coverage conditions")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    arrival_path, cohort_path = (
        args.output_dir / "arrivals.json", args.output_dir / "analysis_cohort.json",
    )
    write(arrival_path, arrivals)
    write(cohort_path, cohort)
    timing = json.loads(args.tool_timing.read_bytes())
    prior = Path(timing["offline_prior"]["path"])
    if sha256_file(prior) != timing["offline_prior"]["sha256"]:
        raise ValueError("offline timing prior changed")
    config = configuration(
        json.loads(BASE.read_bytes()), workflow_count=56,
        workload_manifest=args.source_arm / "workload_manifest.json",
        source_registration=args.source_registration, arrivals=arrival_path,
        timing=args.tool_timing, prior=prior, dispatch_interval_ms=500,
    )
    config["workload_acceptance"] = {
        "minimum_model_work_p80_p20": 2.9,
        "reason": "user_explicitly_accepts_complete_original56_P80_P20_2.931418",
        "historical_default_3_and_other_acceptance_checks_preserved": True,
    }
    config_path = args.output_dir / "full.json"
    write(config_path, config)
    preflights = {}
    for mode in ("full", "scheduler_only", "native_matched"):
        configured = arm_environment(config["common_environment"], mode)
        for name in causal.SERVER_FIELDS:
            if name in configured:
                configured["BELIEFKV_" + name] = configured[name]
        preflights[mode] = causal.preflight_runtime(
            configured, config["common_environment"]["MODEL_PATH"],
        )
    write(args.output_dir / "preparation.json", {
        "workflows": 56, "initial_workflows": 8, "warmup": 12, "measured": 18, "background": 26,
        "mean_interval_seconds": 90., "arrival_rate_per_second": 1. / 90.,
        "arrival_seed": 35, "permutation_seed": permutation_seed,
        "permutation_trials_from_seed47": trials, "intrinsic_population_means": overall,
        "arrival_seed35_checks": {
            "expected_span_s": 4320., "last_arrival_s": offsets[-1],
            "maximum_gap_s": max_gap, "first_measured_s": offsets[12],
            "last_measured_s": offsets[29], "background_span_after_last_measured_s": (
                offsets[-1] - offsets[29]
            ),
        },
        "earlier_readonly_seed35_to54_exploration_not_used_to_pick_policy_winners": True,
        "selected_realization_is_conditioned_diagnostic_not_unconditioned_Poisson_evidence": True,
        "selection_uses_JCT_or_policy_winners": False,
        "model_work_p80_p20": population["population"]["model_work_units"]["p80_p20"],
        "minimum_model_work_p80_p20": 2.9,
        "source_evidence": evidence, "configuration": str(config_path.resolve()),
        "configuration_sha256": sha256_file(config_path),
        "constructor_preflights": preflights,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (population_path, BASE, args.tool_timing, prior, Path(__file__))
        },
        "all_original_tasks_tokens_and_tails_retained": True,
        "pressure_supply_and_background_coverage_require_complete_GPU_review": True,
        "GPU_action": False, "oracle_registered": False, "performance_accepted": False,
    })
    print(json.dumps({
        "workflows": 56, "arrival_seed": 35, "permutation_seed": permutation_seed,
        "cohort_counts": [12, 18, 26], "last_arrival_s": offsets[-1],
        "first_measured_s": offsets[12], "last_measured_s": offsets[29],
        "minimum_model_work_p80_p20": 2.9,
    }))


if __name__ == "__main__":
    main()
