"""Prepare oracle set admission only after a sustained workload's complete review."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import load_overlay
from beliefkv.experiments.workflow_source_work import reconstruct_work
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.runtime.workflow_work_oracle import (
    KIND, WorkflowWorkOracleArtifact, critical_path_graphs,
)
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def calibrate_costs(batches, transfers):
    """Freeze worker-interval proxies; no future policy's timing is consulted."""
    decode, prefill, h2d = [], [], []
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        duration = batch.get("service_elapsed_ms")
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("cost calibration requires positive native worker intervals")
        members = batch["request_samples"]
        if batch["phase"] == "decode":
            width = batch["decode_batch_size"]
            if not 1 <= width <= 24 or len(members) != width:
                raise ValueError("decode cost needs complete non-mixed batch geometry")
            contexts = [row["sequence_tokens_before"] for row in members]
            if any(type(value) is not int or value <= 0 for value in contexts):
                raise ValueError("decode cost lacks observed context lengths")
            decode.append((width, sum(contexts) / width, duration))
        elif batch["phase"] == "prefill":
            tokens = sum(row["token_delta"] for row in members)
            if tokens >= 256:
                prefill.append(duration / tokens)
    for row in transfers:
        tokens = row.get("num_tokens_by_pool", {}).get("kv", 0)
        elapsed = row.get("transfer_stream_elapsed_ms")
        if (
            row.get("direction") == "h2d" and row.get("status") == "completed"
            and type(tokens) is int and tokens >= 1024
            and type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed > 0
        ):
            h2d.append(elapsed / tokens)
    if not decode or not prefill or not h2d:
        raise ValueError("complete control lacks decode, substantial prefill or H2D cost support")
    context = median(value for _width, value, _duration in decode)
    by_width = defaultdict(list)
    for width, observed_context, duration in decode:
        by_width[width].append(duration / max(1., observed_context / context))
    observed = {width: median(values) for width, values in by_width.items()}
    widths, table = sorted(observed), []
    for width in range(1, 25):
        index = bisect_left(widths, width)
        if width in observed:
            value = observed[width]
        elif index == 0:
            value = observed[widths[0]]
        elif index == len(widths):
            value = observed[widths[-1]] * width / widths[-1]
        else:
            left, right = widths[index - 1:index + 1]
            value = observed[left] + (observed[right] - observed[left]) * (width - left) / (right - left)
        table.append(max(table[-1] if table else 0., value))
    costs = {
        "prefill_ms_per_token": median(prefill), "h2d_ms_per_token": median(h2d),
        "decode_ms_by_width": table, "reference_context_tokens": context,
        "decode_steps": 16, "epsilon_ms": .1, "relative_tie": .01,
    }
    CombinationCosts(**costs)
    return costs, {
        "decode_batches": len(decode), "prefill_batches_ge_256_tokens": len(prefill),
        "h2d_copies_ge_1024_tokens": len(h2d),
        "observed_decode_widths": widths,
        "decode_width_sample_counts": {width: len(values) for width, values in by_width.items()},
        "curve_rule": "context_normalized_medians; interpolate; linear_upper_extrapolation; monotone_upper_envelope",
        "limitations": "Worker intervals can include stalls; context scaling and additive copy/compute costs are heuristic, not exact GPU/JCT.",
    }


def validate_workload(cohort, supply, periodic, population):
    if (
        cohort.get("all_workflows_completed") is not True
        or cohort.get("normal_tails_removed") is not False
        or cohort.get("service_output_matches_complete_receipts") is not True
        or cohort.get("background_submission_coverage", {}).get("covers_all_measured_completions") is not True
        or cohort.get("measured_complete_workflow_JCT_ms", {}).get("count") != 18
        or supply.get("all_measured_completed") is not True
        or supply.get("all_workflows_completed") is not True
        or periodic.get("counts", {}).get("cross_workflow_logical_set_competition_controls", 0) < 10
        or population["population"]["model_work_units"]["p80_p20"] < 3.
    ):
        raise ValueError("complete cohort, background, heterogeneity or repeated decision competition is unqualified")
    return {
        "fixed_measured_workflows": 18, "minimum_model_work_p80_p20": 3.,
        "minimum_actual_periodic_competition_controls": 10,
        "full_candidate_and_physical_coverage_proven": False,
        "JOIN_shape_limitations_retained": True,
        "oracle_diagnostic_accepted_not_formal_baseline_or_steady_state_claim": True,
    }


def validate_decision(decision, required_inputs):
    if (
        decision.get("accepted_for_oracle_diagnostic") is not True
        or decision.get("failed_checks") != []
        or not decision.get("checks")
        or any(value is not True for value in decision["checks"].values())
    ):
        raise ValueError("sustained workload has not passed the complete phase-one decision")
    pins = decision["inputs_sha256"]
    for path in required_inputs:
        if pins.get(str(path.resolve())) != sha256_file(path):
            raise ValueError("workload decision belongs to different complete reviews")
    for name, digest in pins.items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("workload decision input changed: " + name)


def oracle_artifact(source, overlay, coefficient, costs, calibration, *, combination=False):
    graph, expected, evidence = source_plan(source)
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        requests, totals = reconstruct_work(graph, corpus)
    sizes = sorted(value / 100. for value in totals.values())
    boundaries = [len(sizes) // 3, 2 * len(sizes) // 3]
    cuts = [(sizes[index - 1] + sizes[index]) / 2. for index in boundaries]
    members = Counter(row["workflow_id"] for row in requests.values())
    transformed, _timing = load_overlay(graph, overlay)
    result = {
        "kind": KIND, "future_information_used": True, "deployable": False,
        "admission_order": "within_reference_category" if combination else "global_ready_remaining_path",
        "prefill_token_weight": .01, "work_thresholds": cuts,
        "model_seconds_per_work_token": coefficient,
        "workflow_graphs": critical_path_graphs(transformed),
        "requests": requests,
        "workflows": {
            workflow: {"total_work_units": total, "requests": members[workflow]}
            for workflow, total in sorted(totals.items())
        },
        "source_binding": {
            "source_arm": str(source.resolve()), "source_evidence": evidence,
            "token_path": str((source / "server/request_tokens.jsonl").resolve()),
            "token_sha256": sha256_file(source / "server/request_tokens.jsonl"),
            "tool_overlay_path": str(overlay.resolve()), "tool_overlay_sha256": sha256_file(overlay),
        },
        "admission_cost_profile": {
            "kind": "r5_frozen_batch_cost_proxy", "costs": costs,
            "identity": hashlib.sha256(json.dumps(costs, sort_keys=True).encode()).hexdigest(),
            "oracle_reference_path_s": 1.,
            "calibration": calibration,
        },
        "scope": "Exact future model/tool DAG and observed progress rank legal ready-paused sets only; no readiness, JOIN or native capacity authority from future data.",
        "threshold_scope": "intrinsic_complete_work_tertiles_for_diagnostics_only",
    }
    WorkflowWorkOracleArtifact(result).validate_source(source, graph, evidence)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--control-review", type=Path, required=True)
    parser.add_argument("--cohort-review", type=Path, required=True)
    parser.add_argument("--supply-review", type=Path, required=True)
    parser.add_argument("--periodic-review", type=Path, required=True)
    parser.add_argument("--workload-review", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--selection", choices=("global_remaining_path", "join_combination"),
                        default="global_remaining_path")
    args = parser.parse_args()
    if args.output_dir.exists() or complete_work(args.control) is None:
        raise ValueError("preserve prior preparations and wait for complete control/drain/owned exit")
    plan, review, cohort, supply, periodic = (
        causal.load(path) for path in (
            args.control, args.control_review, args.cohort_review, args.supply_review, args.periodic_review,
        )
    )
    source = Path(plan["source_arm"])
    population_path = source / "composite_source.json"
    population = causal.load(population_path)
    validate_decision(causal.load(args.workload_review), (
        args.control_review, args.cohort_review, args.supply_review, args.periodic_review, population_path,
    ))
    acceptance = validate_workload(cohort, supply, periodic, population)
    base = Path(plan["experiment_configuration"])
    configuration = causal.load(base)
    common = configuration["common_environment"]
    if (
        plan["order"] != ["full"] or configuration["order"] != ["full"]
        or review["complete_work_and_actual_exit_verified"] is not True
        or review["registration_sha256"] != sha256_file(args.control)
        or common["WORKFLOW_SIZE_RESUME"] != "0" or common["TOOL_PREFETCH"] != "0"
        or common["AGENT_FREE_DISPATCH_INTERVAL_MS"] != "500"
        or common["AGENT_CONTROL_INTERVAL_MS"] != "2000"
    ):
        raise ValueError("oracle must use its complete matched unsorted Full control")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    costs, calibration = calibrate_costs(
        reader(arm / "server/runtime_audit.jsonl"), reader(arm / "server/transfer_telemetry.jsonl"),
    )
    overlay = Path(configuration["tool_timing_overlay"]["path"])
    coefficient = review["model_cost_calibration"]["model_seconds_per_work_token"]
    combination = args.selection == "join_combination"
    artifact = oracle_artifact(source, overlay, coefficient, costs, calibration, combination=combination)
    artifact["model_cost_calibration"] = review["model_cost_calibration"]
    artifact_path = args.output_dir.resolve() / "oracle.json"
    write(artifact_path, artifact)
    candidate = deepcopy(configuration)
    changed = {
        "WORKFLOW_SIZE_RESUME": "1", "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_COMBINATION_ADMISSION": str(int(combination)),
        "WORKFLOW_WORK_MODEL": str(artifact_path), "WORKFLOW_WORK_MODEL_SHA256": sha256_file(artifact_path),
    }
    for name, value in changed.items():
        candidate["common_environment"][name] = value
        candidate["common_environment"]["BELIEFKV_" + name] = value
    candidate.update(
        status=f"sustained_oracle_{args.selection}_pending_CPU_GPU_qualification",
        candidate_provenance={
            "base": str(base), "base_sha256": sha256_file(base),
            "control_registration": str(args.control.resolve()),
            "control_registration_sha256": sha256_file(args.control),
            "control_review": str(args.control_review.resolve()),
            "control_review_sha256": sha256_file(args.control_review),
            "workload_review": str(args.workload_review.resolve()),
            "workload_review_sha256": sha256_file(args.workload_review),
            "changed_policy": (
                "bounded_oracle_JOIN_set_selection_at_existing_admission_boundaries"
                if combination else "global_ready_remaining_model_tool_path_before_legacy_category"
            ),
            "first_registration_coalescing_is_part_of_candidate": True,
            "future_information_used": True, "deployable": False,
        },
        workflow_work_qualification={
            "kind": KIND, "artifact": str(artifact_path), "artifact_sha256": sha256_file(artifact_path),
            "future_information_used": True, "deployable": False,
            "learned_calibration_requirement_applies": False,
        },
        performance_accepted=False,
    )
    candidate["comparison_contract"].update(
        workflow_work_ranking=True, future_information_used=True, deployable=False,
        oracle_is_not_an_optimal_JCT_bound=True,
        minimum_promising_measured_mean_JCT_reduction=.05,
        common_window_output_throughput_noninferiority_fraction=.98,
        workflow_resume_key=(
            ["protected_JOIN_reference", "feasible_R5_set_score", "stable_reference_tie"]
            if combination else
            ["protected_ready_JOIN", "remaining_model_tool_path", "legacy_category_tie",
             "context_tokens", "queue_sequence"]
        ),
        first_registration_continuation_sibling_dispatch="coalesced_existing_native_boundary",
    )
    preflights = {}
    for mode in ("full", "scheduler_only", "native_matched"):
        environment = arm_environment(candidate["common_environment"], mode)
        for name in causal.SERVER_FIELDS:
            if name in environment:
                environment["BELIEFKV_" + name] = environment[name]
        preflights[mode] = causal.preflight_runtime(environment, common["MODEL_PATH"])
    write(args.output_dir / "full.json", candidate)
    write(args.output_dir / "preparation.json", {
        "workload_acceptance": acceptance, "cost_calibration": calibration,
        "selection": args.selection, "cost_profile_used_by_selection": combination,
        "constructor_preflights": preflights, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.control, args.control_review, args.cohort_review, args.supply_review,
            args.periodic_review, args.workload_review, base, overlay, Path(__file__),
        )},
        "configuration": str((args.output_dir / "full.json").resolve()),
        "configuration_sha256": sha256_file(args.output_dir / "full.json"),
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    })
    print({"workflows": len(artifact["workflows"]), "requests": len(artifact["requests"]),
           "configuration": str(args.output_dir / "full.json")})


if __name__ == "__main__":
    main()
