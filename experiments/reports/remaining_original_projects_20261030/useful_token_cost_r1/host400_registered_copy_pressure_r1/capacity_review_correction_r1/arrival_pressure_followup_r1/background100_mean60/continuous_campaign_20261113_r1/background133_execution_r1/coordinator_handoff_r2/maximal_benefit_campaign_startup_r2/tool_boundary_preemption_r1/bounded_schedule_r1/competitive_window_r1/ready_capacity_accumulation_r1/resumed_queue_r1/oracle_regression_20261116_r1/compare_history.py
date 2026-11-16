"""Compare actual old/new registrations and fixed-cohort reports on CPU."""

import json
import math
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = Path("/home/longhao/experiment/ops")


def load(path):
    return json.loads(Path(path).read_bytes())


def serving(stem):
    registration = OPS / (stem + ".registration.json")
    plan = load(registration)
    config = load(plan["experiment_configuration"])
    artifact = load(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    cohort_registration = load(OPS / (stem + ".cohort.registration.json"))
    cohort = load(Path(cohort_registration["analysis_output"]) / "cohort.json")
    fields = (
        "kind", "admission_order", "remaining_work_ranking_enabled",
        "shortest_work_first_admission", "strict_remaining_work_admission",
        "cost_aware_maximal_admission", "maximal_admission_objective",
        "ready_wait_priority_ms", "ready_capacity_accumulation_ms",
        "free_dispatch_coalescing_window_ms", "model_seconds_per_work_token",
    )
    return plan, config, artifact, cohort, {
        "registration": str(registration), "commit": plan["code_commit"],
        "execution_checkout": plan["execution_checkout"],
        "workflows": len(plan["workflow_ids"]),
        "artifact_fields": {name: artifact.get(name) for name in fields},
        "combination_admission": config["common_environment"]["WORKFLOW_COMBINATION_ADMISSION"],
        "pressure_policy": config["common_environment"]["AGENT_PRESSURE_PAUSE_POLICY"],
        "future_tool_durations_used": plan.get("oracle_information", {}).get("future_tool_durations_used"),
        "mean_complete_JCT_s": cohort["measured_complete_workflow_JCT_ms"]["mean"] / 1000.,
        "common_window_output_tps": cohort["common_window_output_tokens_per_second"],
    }


def main():
    output = HERE / "history_comparison_r1.json"
    if output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve existing reports and keep CUDA hidden")
    old_stems = (
        "workflow_original83_host400_mean70_full_20261106_r2",
        "workflow_original83_host400_mean70_oracle_20261106_r2",
        "workflow_original83_host400_mean70_repeat_full_20261107_r1",
        "workflow_original83_host400_mean70_repeat_oracle_20261107_r1",
    )
    new_stems = (
        "workflow_original133_host400_mean60_full_20261113_r1",
        "workflow_original133_host400_mean60_ready_capacity_oracle_20261115_r1",
    )
    data = [serving(name) for name in (*old_stems, *new_stems)]
    result = {"registrations": {name: row[4] for name, row in zip((*old_stems, *new_stems), data)}}
    result["complete_mean_JCT_reduction_percent"] = {
        name: 100. * (1. - data[after][4]["mean_complete_JCT_s"] / data[before][4]["mean_complete_JCT_s"])
        for name, before, after in (("old83_first", 0, 1), ("old83_repeat", 2, 3), ("latest133", 4, 5))
    }
    old, new = data[1], data[-1]
    old_cohort, new_cohort = old[3]["cohort"], new[3]["cohort"]
    if old_cohort["measured_workflow_ids"] != new_cohort["measured_workflow_ids"]:
        raise ValueError("fixed measured workflow membership changed")
    members = set(old_cohort["measured_workflow_ids"])
    before_requests = {
        key: value for key, value in old[2]["requests"].items() if value["workflow_id"] in members
    }
    after_requests = {
        key: value for key, value in new[2]["requests"].items() if value["workflow_id"] in members
    }
    if before_requests != after_requests:
        raise ValueError("fixed measured request identities or model work changed")
    old_arrivals, new_arrivals = old[0]["workflow_arrivals"], new[0]["workflow_arrivals"]
    earlier, later = old_arrivals["arrival_schedule"], new_arrivals["arrival_schedule"]
    same_prefix = [row["workflow_id"] for row in earlier] == [
        row["workflow_id"] for row in later[:len(earlier)]
    ]
    ratio = 60. / 70.
    offset_errors = [
        abs(before["offset_seconds"] * ratio - after["offset_seconds"])
        for before, after in zip(earlier, later) if before["workflow_id"] == after["workflow_id"]
    ]
    result["workload_comparison"] = {
        "same_fixed_18_measured_members": True,
        "same_fixed_measured_request_work": True,
        "measured_requests": len(before_requests),
        "measured_output_tokens": sum(row["output_tokens"] for row in before_requests.values()),
        "initial_workflows_old_and_new": [
            sum(row["offset_seconds"] == 0 for row in schedule) for schedule in (earlier, later)
        ],
        "old_arrival_population": len(earlier), "new_arrival_population": len(later),
        "old_arrival_identity_prefix_preserved": same_prefix,
        "arrival_offset_scale": ratio,
        "maximum_matching_identity_arrival_scaling_error_s": max(offset_errors),
        "all_matching_arrivals_exactly_retimed_within_1e_9s": all(error < 1e-9 for error in offset_errors),
        "mean_interval_change_is_not_the_only_change": True,
    }
    strict = load(HERE / "latest_strict_comparison_r1/report.json")
    result["latest_same_state_strict_diagnostic"] = {
        "counts": strict["counts"], "variants": strict["variants"],
        "not_a_JCT_counterfactual_or_GPU_gain": True,
    }
    result["GPU_action"] = False
    result["performance_accepted"] = False
    result["minimum_required_measured_average_JCT_reduction"] = .02
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "complete_mean_JCT_reduction_percent": result["complete_mean_JCT_reduction_percent"],
        "workload_comparison": result["workload_comparison"],
    }))


if __name__ == "__main__":
    main()
