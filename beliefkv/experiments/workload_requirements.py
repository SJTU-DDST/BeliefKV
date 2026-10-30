"""Mechanism-first workload screening, with serving evidence kept separate."""

from collections import Counter, defaultdict
import math

from beliefkv.experiments.workload_mix import summary


DEFAULT_DIAGNOSTICS = {
    "model_work_p80_p20": 3.,
    "multi_child_join_fraction": .5,
    "child_calls_median_max": 12.,
    "child_calls_p90_max": 20.,
    "root_parallel_rounds_median_min": 2.,
}


def assess(inventory, shape, *, diagnostics=None, allow_multiple_capture_protocols=False):
    """Keep normal runs and negative evidence; do not accept policy wins as inputs."""
    diagnostics = DEFAULT_DIAGNOSTICS if diagnostics is None else diagnostics
    if (
        type(allow_multiple_capture_protocols) is not bool
        or diagnostics.keys() != DEFAULT_DIAGNOSTICS.keys()
        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in diagnostics.values())
    ):
        raise ValueError("declare source-shape diagnostics without policy outcome fields")
    workflows = inventory["workflows"]
    ids = {row["workflow_id"] for row in workflows}
    if (
        not workflows or len(ids) != len(workflows)
        or len({row["project"] for row in workflows}) != len(workflows)
        or (not allow_multiple_capture_protocols
            and len({row["protocol_sha256"] for row in workflows}) != 1)
        or shape["source"] != inventory["capture"]
        or any(row["workflow_id"] not in ids for row in (*shape["children"], *shape["joins"]))
    ):
        raise ValueError("workload audit requires one complete project-disjoint execution protocol")
    children, joins = defaultdict(list), defaultdict(list)
    for row in shape["children"]:
        children[row["workflow_id"]].append(row)
    for row in shape["joins"]:
        joins[row["workflow_id"]].append(row)
    details = []
    for row in workflows:
        workflow = row["workflow_id"]
        branch = row.get("agent_work_breakdown")
        if branch is None:
            raise ValueError("refresh inventory to attribute actual intrinsic root/child work")
        total = sum(item["model_work_units"] for item in branch["agents"])
        if not math.isclose(total, row["model_work_units"], rel_tol=1e-10):
            raise ValueError("per-agent intrinsic work is not conserved")
        child_rows = children[workflow]
        if (
            len(joins[workflow]) != row["joins"]
            or sum(item["model_calls"] for item in child_rows) !=
            sum(item["model_calls"] for item in branch["agents"] if item["kind"] == "child")
            or sum(item["output_tokens"] for item in child_rows) !=
            sum(item["generated_tokens"] for item in branch["agents"] if item["kind"] == "child")
        ):
            raise ValueError("shape and inventory child work differ")
        details.append({
            "workflow_id": workflow, "project": row["project"],
            "source_eligible": row["eligible"], "model_work_units": row["model_work_units"],
            "root_parallel_rounds": row["completed_parallel_root_joins"],
            "root_calls": sum(item["model_calls"] for item in branch["agents"] if item["kind"] == "root"),
            "child_model_work_fraction": branch["child_model_work_fraction"],
            "largest_child_model_work_fraction": branch["largest_child_model_work_fraction"],
            "child_model_calls": [item["model_calls"] for item in child_rows],
            "initial_prompt_tokens": row["initial_prompt_tokens"],
            "child_output_fraction": (
                sum(item["output_tokens"] for item in child_rows) / row["generated_tokens"]
            ),
        })
    child_calls = summary([value for row in details for value in row["child_model_calls"]])
    work = summary([row["model_work_units"] for row in details])
    parallel_rounds = summary([row["root_parallel_rounds"] for row in details])
    multi_fraction = (
        sum(row["members"] >= 2 for row in shape["joins"]) / len(shape["joins"])
        if shape["joins"] else 0.
    )
    shortfalls = []
    checks = (
        (work["p80_p20"] is not None and work["p80_p20"] >= diagnostics["model_work_p80_p20"],
         "insufficient_intrinsic_work_diversity"),
        (multi_fraction >= diagnostics["multi_child_join_fraction"], "mostly_single_child_joins"),
        (bool(child_calls["count"]) and child_calls["p50"] <= diagnostics["child_calls_median_max"],
         "child_calls_median_too_large"),
        (bool(child_calls["count"]) and child_calls["p90"] <= diagnostics["child_calls_p90_max"],
         "child_calls_p90_too_large"),
        (parallel_rounds["p50"] >= diagnostics["root_parallel_rounds_median_min"],
         "insufficient_multi_round_parallel_joins"),
    )
    shortfalls.extend(reason for accepted, reason in checks if not accepted)
    if any(not row["source_eligible"] for row in details):
        shortfalls.append("source_contains_ineligible_natural_returns")
    return {
        "workflows": details,
        "capture_protocols": sorted({row["protocol_sha256"] for row in workflows}),
        "heterogeneous_capture_protocols_explicitly_permitted": allow_multiple_capture_protocols,
        "summary": {
            "workflows": len(details), "projects": len(details),
            "model_work": work, "child_model_calls": child_calls,
            "multi_child_join_fraction": multi_fraction,
            "root_parallel_rounds": parallel_rounds,
            "child_model_work_fraction": summary([row["child_model_work_fraction"] for row in details]),
            "largest_child_model_work_fraction": summary([
                row["largest_child_model_work_fraction"] for row in details
            ]),
            "root_calls": summary([row["root_calls"] for row in details]),
            "join_fanout_counts": dict(Counter(row["members"] for row in shape["joins"])),
        },
        "diagnostic_targets": dict(diagnostics), "shape_shortfalls": shortfalls,
        "model_work_diversity": {
            "metric": "p80_p20", "minimum": diagnostics["model_work_p80_p20"],
            "actual": work["p80_p20"],
            "meets_threshold_3": work["p80_p20"] is not None and work["p80_p20"] >= 3.,
            "meets_threshold_4": work["p80_p20"] is not None and work["p80_p20"] >= 4.,
            "p90_p10_is_reference_only": True,
        },
        "shape_status": "needs_new_candidates" if shortfalls else "source_shape_candidate",
        "serving_evidence_status": "pending_matching_arrival_and_capacity_calibration",
        "serving_evidence_required": [
            "predeclared_arrival_cohort_complete_JCT_and_background_coverage",
            "ready_paused_cross_workflow_individual_fit_but_not_joint_fit",
            "physical_HBM_headroom_or_safe_cold_pages_and_native_growth_reserve",
            "actual_missing_previously_served_prefixes_and_exposed_recovery_wait",
            "live_unique_HBM_Host_working_set_and_useful_prefix_recomputation",
            "tool_wait_union_with_other_ready_model_work_and_refill_delay",
            "same_task_tool_timing_capacity_and_information_for_all_arms",
        ],
        "scope": {
            "diagnostics_are_not_model_call_limits": True,
            "natural_returns_or_tails_removed": False,
            "native_recomputation_or_predictive_win_used_to_select": False,
            "source_shape_does_not_prove_physical_pressure": True,
            "child_sum_and_largest_child_are_reported_separately": True,
        },
        "formal_comparison_ready": False, "GPU_action": False,
    }
