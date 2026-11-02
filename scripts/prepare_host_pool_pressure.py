"""Prepare a declared larger Host pool and initial workflow population."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments import host_numa
from beliefkv.experiments.replay_arrivals import (
    permuted_poisson_arrival_plan, substitute_background_arrivals, validate_arrival_plan,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import declare_cohort
from scripts.qualify_interleaved_host_pool import configuration
from scripts.train_workflow_work import write


def pressure_protocol(arrivals, *, initial=20, measured=18):
    identities = [row["workflow_id"] for row in arrivals["arrival_schedule"]]
    validate_arrival_plan(arrivals, identities)
    if type(initial) is not int or type(measured) is not int or (
        initial <= 0 or measured <= 0 or initial + measured >= len(identities)
    ):
        raise ValueError("declare initial warmup, fixed measured members and remaining background")
    original = arrivals.get("original_plan", arrivals)
    if original["mode"] != "permuted_warm_start_poisson":
        raise ValueError("retain the independent permutation and seeded Poisson draw prefix")
    following = permuted_poisson_arrival_plan(
        [row["workflow_id"] for row in original["arrival_schedule"]],
        rate_per_second=arrivals["rate_per_second"], seed=arrivals["seed"],
        permutation_seed=original["permutation_seed"], initial_workflows=initial,
    )
    if arrivals["mode"] == "background_substituted_poisson":
        following = substitute_background_arrivals(
            following, arrivals["replacements"],
            preserved_prefix_workflows=max(initial, arrivals["preserved_prefix_workflows"]),
        )
    if [row["workflow_id"] for row in following["arrival_schedule"]] != identities:
        raise ValueError("pressure preparation changed an original workflow or its order")
    slots = following["arrival_schedule"]
    cohort = declare_cohort(
        following, warmup=initial, measured=measured,
        throughput_start_s=slots[initial]["offset_seconds"],
        throughput_end_s=slots[initial + measured - 1]["offset_seconds"],
        stop_after_measured=True,
    )
    return following, cohort


def pressure_configuration(previous, arrival_path, cohort_path, *, size_gb=400):
    if previous["order"] not in (["full"], ["thunderagent_acting_decay"]):
        raise ValueError("prepare unsorted Full or the retained acting-decay reference")
    result = deepcopy(previous)
    common = result["common_environment"]
    if any(common.get(key, "0") != "0" for key in (
        "TOOL_PREFETCH", "BELIEFKV_ENABLE_TOOL_PREFETCH", "LAYER_HANDOFF", "BELIEFKV_LAYER_HANDOFF",
    )):
        raise ValueError("retain disabled tool prefetch and layer handoff")
    common.update(configuration(size_gb, 1, "0,1"))
    common["HOST_NUMA_REGISTRATION_POLICY"] = "strict"
    common["BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE"] = ""
    if any(common.get(key, "") for key in (
        "WORKFLOW_COMPUTE_SERVICE_CONFIG", "BELIEFKV_WORKFLOW_COMPUTE_SERVICE_CONFIG",
    )):
        raise ValueError("keep compute-service changes separate from the capacity experiment")
    host_numa.configuration(common)
    result.update(
        status="larger_Host_pressure_requires_bound_CPU_and_actual_GPU_qualification",
        performance_accepted=False,
        workflow_arrivals={"path": str(arrival_path.resolve()), "sha256": sha256_file(arrival_path)},
        private_analysis_cohort={
            "path": str(cohort_path.resolve()), "sha256": sha256_file(cohort_path),
            "delivered_to_scheduler": False,
        },
        host_capacity_experiment={
            "declared_decimal_Host_GB": size_gb,
            "actual_Host_bytes_required": host_numa.expected_full_bytes(common),
            "unchanged_HBM_and_running_decode_limits": True,
            "explicit_balanced_interleave_nodes": [0, 1],
            "model_qualification_pending": True,
            "old_200GB_results_are_not_matched_controls": True,
        },
    )
    result.setdefault("gpu_execution", {}).update(registered=False, requires_fresh_position_and_sampling=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-base", type=Path, required=True)
    parser.add_argument("--thunder-base", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial", type=int, default=20)
    parser.add_argument("--measured", type=int, default=18)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve every prepared capacity/arrival experiment")
    bases = {
        arm: json.loads(path.read_bytes())
        for arm, path in (("full", args.full_base), ("thunderagent_acting_decay", args.thunder_base))
    }
    for name in ("source_registration", "workflow_arrivals", "tool_timing_overlay"):
        if bases["full"].get(name) != bases["thunderagent_acting_decay"].get(name):
            raise ValueError("pressure arms must retain the same complete original source: " + name)
    arrival_path = Path(bases["full"]["workflow_arrivals"]["path"])
    if sha256_file(arrival_path) != bases["full"]["workflow_arrivals"]["sha256"]:
        raise ValueError("the previous arrival declaration changed")
    old = json.loads(arrival_path.read_bytes())
    arrivals, cohort = pressure_protocol(old, initial=args.initial, measured=args.measured)
    args.output_dir.mkdir(parents=True)
    new_arrival, new_cohort = args.output_dir / "arrivals.json", args.output_dir / "analysis_cohort.json"
    write(new_arrival, arrivals)
    write(new_cohort, cohort)
    for arm, previous in bases.items():
        write(args.output_dir / f"{arm}.json", pressure_configuration(previous, new_arrival, new_cohort))
    write(args.output_dir / "preparation.json", {
        "kind": "same_original_workflows_larger_Host_initial_population_diagnostic",
        "workflows": len(arrivals["arrival_schedule"]), "initial": args.initial,
        "measured": args.measured, "background": len(cohort["background_workflow_ids"]),
        "previous_initial": old["initial_workflows"],
        "mean_interval_s": 1. / arrivals["rate_per_second"],
        "same_workflow_population_order_tokens_tools_dependencies": True,
        "same_original_arrival_seed": arrivals["seed"] == old["seed"],
        "cohort_redeclared_by_arrival_index_before_execution": True,
        "previous_fixed18_members_not_claimed_unchanged": True,
        "complete_measured_JCT_and_background_stop_retained": True,
        "actual_ready_supply_background_coverage_and_stability_unverified": True,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.full_base, args.thunder_base, arrival_path, Path(__file__),
        )},
        "artifacts_sha256": {str(path.resolve()): sha256_file(path) for path in (
            new_arrival, new_cohort, args.output_dir / "full.json",
            args.output_dir / "thunderagent_acting_decay.json",
        )},
        "GPU_action": False, "GPU_followup_registered": False, "performance_accepted": False,
    })
    print(json.dumps({
        "initial": args.initial, "measured": args.measured,
        "mean_interval_s": 1. / arrivals["rate_per_second"],
        "common_window": cohort["throughput_window_offset_seconds"],
        "last_arrival_s": arrivals["arrival_schedule"][-1]["offset_seconds"],
        "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
