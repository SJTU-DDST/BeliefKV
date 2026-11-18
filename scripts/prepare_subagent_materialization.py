"""Prepare matched initial KV materialization controls without launching a GPU."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.host_numa import expected_full_bytes
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from beliefkv.runtime.subagent_materialization import PRESSURE_POLICIES
from scripts.prepare_arrival_rate_followup import followup_configuration, followup_protocol
from scripts.train_workflow_work import write


def configurations(previous, *, host_gb=None, pressure_policy="pressure_v1"):
    if previous["order"] != ["full"]:
        raise ValueError("prepare from the declared Full configuration")
    before = previous["common_environment"]
    if before.get("BELIEFKV_SUBAGENT_MATERIALIZATION", "off") != "off":
        raise ValueError("the base must preserve the original materialization behavior")
    if host_gb is not None and (type(host_gb) is not int or not 1 <= host_gb <= 1024):
        raise ValueError("Host capacity must be an explicit integer number of decimal GB")
    if pressure_policy not in PRESSURE_POLICIES:
        raise ValueError("select a supported materialization pressure policy")
    result = {}
    for label, policy in (("control", "off"), ("pressure", pressure_policy)):
        config = deepcopy(previous)
        common = config["common_environment"]
        if host_gb is not None:
            common["HICACHE_SIZE_GB"] = str(host_gb)
        common["BELIEFKV_SUBAGENT_MATERIALIZATION"] = policy
        arm_environment(common, "full")
        if host_gb is not None:
            config["inherited_host_capacity_experiment"] = config.pop("host_capacity_experiment", None)
            config["host_capacity_experiment"] = {
                "declared_decimal_Host_GB": host_gb,
                "actual_Host_bytes_required": expected_full_bytes(common),
                "unchanged_HBM_and_running_decode_limits": True,
                "inherit_existing_NUMA_policy": True,
                "old_results_are_not_matched_controls": True,
                "model_qualification_pending": True,
            }
        config.update(
            status="CPU_prepared_child_materialization_not_GPU_registered",
            performance_accepted=False,
        )
        config.setdefault("comparison_contract", {}).update(
            subagent_materialization_policy=policy,
            child_task_prompt_tools_and_workflow_arrival_unchanged=True,
            first_child_prefill_delay_included_in_complete_workflow_JCT=True,
            held_first_calls_distinguished_from_KV_restore_candidates=True,
            matched_control_changes_only_materialization_policy=True,
            historical_GPU_results_are_not_matched_controls=host_gb is not None,
            zero_recomputation_guaranteed=False,
        )
        config["materialization_experiment"] = {
            "policy": policy, "Host_GB": int(common["HICACHE_SIZE_GB"]),
            "Host_changed_from_base": common["HICACHE_SIZE_GB"] != before["HICACHE_SIZE_GB"],
            "inherit_NUMA_placement_without_new_balance_requirement": True,
            "requires_matched_control_and_fresh_resource_qualification": True,
            "requires_original_JCT_and_actual_useful_prefix_loss_audit": True,
        }
        config.setdefault("gpu_execution", {}).update(
            registered=False, fresh_source_bound_position_check_required=True,
        )
        result[label] = config
    left, right = (deepcopy(result[label]) for label in ("control", "pressure"))
    for config in (left, right):
        config["common_environment"].pop("BELIEFKV_SUBAGENT_MATERIALIZATION")
        config["comparison_contract"].pop("subagent_materialization_policy")
        config["materialization_experiment"].pop("policy")
    if left != right:
        raise ValueError("the two controls changed more than child materialization")
    return result


def baseline_configurations(control):
    result = {}
    for label in ("thunderagent_acting_decay", "native_matched"):
        config = deepcopy(control)
        config["order"] = [label]
        common = config["common_environment"]
        effective = (
            paper_environment(common, label)
            if label == "thunderagent_acting_decay" else arm_environment(common, label)
        )
        if effective["BELIEFKV_SUBAGENT_MATERIALIZATION"] != "off":
            raise ValueError("child materialization cannot leak into the baselines")
        config["comparison_contract"] = {
            "complete_original_workflows": True,
            "same_fixed_tools_arrivals_and_resources_for_all_arms": True,
            "measured_cohort_not_delivered_to_scheduler": True,
            "first_child_prefill_delay_included_in_complete_workflow_JCT": True,
            "subagent_materialization_policy": "off",
            "future_information_used": False,
            "baseline_policy": label,
            "native_replay_baseline": label == "native_matched",
            "historical_GPU_results_are_not_matched_controls": True,
        }
        config["native_reference"] = {
            "matched_native_capture_exists": False,
            "fresh_matched_causal_native_replay_required": True,
            "original_capture_has_different_Host_and_arrivals": True,
        }
        result[label] = config
    return result


def prepare(path, output, *, host_gb=None, mean_interval_s=None,
            include_baselines=False, pressure_policy="pressure_v1"):
    if output.exists():
        raise FileExistsError("preserve earlier preparations")
    previous = json.loads(path.read_bytes())
    inputs = [path, Path(__file__), ROOT / "beliefkv/runtime/subagent_materialization.py"]
    for field in ("workflow_arrivals", "private_analysis_cohort"):
        binding = previous[field]
        target = Path(binding["path"])
        if sha256_file(target) != binding["sha256"]:
            raise ValueError("source timing or fixed measurement membership changed")
        inputs.append(target)
    arrivals = json.loads(inputs[-2].read_bytes())
    cohort = json.loads(inputs[-1].read_bytes())
    validate_cohort(cohort, arrivals)
    previous_mean = 1. / arrivals["rate_per_second"]
    if mean_interval_s is not None:
        following, analysis = followup_protocol(
            arrivals, cohort, mean_interval_seconds=mean_interval_s,
        )
        write(output / "arrivals.json", following)
        write(output / "analysis_cohort.json", analysis)
        previous = followup_configuration(
            previous, output / "arrivals.json", output / "analysis_cohort.json",
        )
        previous["arrival_intensity_experiment"] = {
            "previous_mean_interval_s": previous_mean,
            "mean_interval_s": mean_interval_s,
            "initial_population_order_seed_draws_and_fixed_measured_members_preserved": True,
            "new_schedule_is_identical_in_all_matched_arms": True,
            "historical_arrival_results_are_not_matched_controls": True,
            "background_tail_coverage_and_ready_supply_require_execution": True,
        }
        previous["comparison_contract"]["workload_change"] = (
            "same_original133_and_fixed18_retimed_seeded_Poisson"
        )
        arrivals, cohort = following, analysis
    configurations_by_arm = configurations(previous, host_gb=host_gb, pressure_policy=pressure_policy)
    if include_baselines:
        configurations_by_arm.update(baseline_configurations(configurations_by_arm["control"]))
    acceptance = {
        "primary_reference": "control",
        "materialization_gain_requires": [
            "fewer_executed_previously_served_prefix_tokens",
            "common_wall_window_decode_output_tps_at_least_control",
            "common_wall_window_total_output_tps_at_least_control",
            "complete_fixed18_mean_workflow_JCT_no_worse_than_control",
        ],
        "reported_supply_groups": [
            "ready_paused_with_previously_materialized_state",
            "unmaterialized_first_requests_permitted",
            "unmaterialized_first_requests_held",
            "tool_paused", "join_paused", "active_ready",
        ],
        "held_first_requests_are_not_KV_restore_backlog": True,
        "GPU_utilization_or_Host_occupancy_alone_is_not_acceptance": True,
        "lower_batch_width_or_added_idle_with_held_children_requires_diagnosis": True,
        "background_submission_coverage_and_arrival_backlog_drift_must_be_reported": True,
        "no_finite_workload_steady_state_or_zero_recomputation_guarantee": True,
    }
    for label, config in configurations_by_arm.items():
        config["materialization_acceptance"] = acceptance
        write(output / (label + ".json"), config)
    report = {
        "scope": "matched_initial_KV_materialization_CPU_preparation",
        "pressure_policy": pressure_policy,
        "inputs_sha256": {str(item.resolve()): sha256_file(item) for item in inputs},
        "artifacts": {
            label: {"path": str((output / (label + ".json")).resolve()),
                    "sha256": sha256_file(output / (label + ".json"))}
            for label in configurations_by_arm
        },
        "workflows": len(arrivals["arrival_schedule"]),
        "initial_workflows": arrivals["initial_workflows"],
        "mean_interarrival_s": 1. / arrivals["rate_per_second"],
        "previous_mean_interarrival_s": previous_mean,
        "arrival_intensity_changed": mean_interval_s is not None and mean_interval_s != previous_mean,
        "measured_workflow_ids": cohort["measured_workflow_ids"],
        "Host_GB": int(configurations_by_arm["control"]["common_environment"]["HICACHE_SIZE_GB"]),
        "original_arrivals_preserved": mean_interval_s is None or mean_interval_s == previous_mean,
        "fixed_measurement_members_order_and_seeded_draws_preserved": True,
        "matched_arms_have_identical_arrivals": True,
        "included_baselines": [
            label for label in configurations_by_arm if label not in ("control", "pressure")
        ],
        "acceptance": acceptance,
        "GPU_action": False, "GPU_followup_registered": False, "performance_accepted": False,
    }
    write(output / "preparation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--host-gb", type=int)
    parser.add_argument("--mean-interval-s", type=float)
    parser.add_argument("--include-baselines", action="store_true")
    parser.add_argument("--pressure-policy", choices=PRESSURE_POLICIES, default="pressure_v1")
    args = parser.parse_args()
    report = prepare(
        args.config, args.output_dir, host_gb=args.host_gb,
        mean_interval_s=args.mean_interval_s,
        include_baselines=args.include_baselines, pressure_policy=args.pressure_policy,
    )
    print(json.dumps({key: report[key] for key in (
        "workflows", "initial_workflows", "mean_interarrival_s", "Host_GB", "GPU_action",
    )}))


if __name__ == "__main__":
    main()
