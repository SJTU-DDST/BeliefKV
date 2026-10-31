"""Prepare reviewed oracle admission, including explicit user diagnostic exceptions."""

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
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal
from scripts.run_causal_gpu_replay import source_plan
from scripts.review_sustained_workload import validate_minimum_work_ratio
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


DIAGNOSTIC_KIND = "user_requested_oracle_coalescing_diagnostic_v1"
DIAGNOSTIC_CHECKS = frozenset((
    "background_arrivals_cover_measured_tails",
    "ready_paused_supply_in_at_least_quarter_of_window_samples",
    "repeated_actual_periodic_competition",
))


def diagnostic_waivers(request):
    if request is None:
        return set()
    waived = request.get("waived_workload_checks")
    if (
        request.get("kind") != DIAGNOSTIC_KIND or request.get("requested_by") != "user"
        or request.get("preserve_original_workload_rejection") is not True
        or request.get("formal_workload_accepted") is not False
        or request.get("coalescing_window_ms") not in (1000, 2000)
        or type(request.get("coalescing_window_ms")) is not int
        or not isinstance(waived, list) or len(set(waived)) != len(waived)
        or not set(waived) <= DIAGNOSTIC_CHECKS
    ):
        raise ValueError("invalid explicit user oracle diagnostic exception")
    return set(waived)


def load_diagnostic_request(path, control_path, decision_path):
    request = causal.load(path)
    diagnostic_waivers(request)
    if any(
        request.get(name + "_sha256") != sha256_file(target)
        or request.get(name) != str(target.resolve())
        for name, target in (
            ("control_registration", control_path), ("workload_decision", decision_path),
        )
    ):
        raise ValueError("oracle diagnostic request belongs to another control or decision")
    return request


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


def validate_workload(
    cohort, supply, periodic, population, *, minimum_model_work_p80_p20=3., completion=None,
    diagnostic=None,
):
    waived = diagnostic_waivers(diagnostic)
    minimum = validate_minimum_work_ratio(minimum_model_work_p80_p20)
    cohort_stop = cohort.get("cohort", {}).get("schema_version") == 2
    complete = (
        completion is not None
        and completion.get("protocol") == "all_fixed_measured_workflows_completed"
        and completion.get("cohort") == cohort["cohort"]
        and completion.get("actual_owned_exit_verified") is True
        and cohort.get("all_measured_completed") is True
        and cohort.get("measured_service_output_matches_complete_receipts") is True
        and type(cohort.get("measured_exact_receipt_requests")) is int
        and cohort["measured_exact_receipt_requests"] > 0
        and cohort.get("measured_exact_receipt_requests") == completion.get("measured_requests")
        if cohort_stop else (
            cohort.get("all_workflows_completed") is True
            and cohort.get("service_output_matches_complete_receipts") is True
            and supply.get("all_workflows_completed") is True
        )
    )
    if (
        not complete
        or cohort.get("normal_tails_removed") is not False
        or cohort.get("background_submission_coverage", {}).get("covers_all_measured_completions") is not True
        and "background_arrivals_cover_measured_tails" not in waived
        or cohort.get("measured_complete_workflow_JCT_ms", {}).get("count") != 18
        or supply.get("all_measured_completed") is not True
        or periodic.get("counts", {}).get("cross_workflow_logical_set_competition_controls", 0) < 10
        and "repeated_actual_periodic_competition" not in waived
        or population["population"]["model_work_units"]["p80_p20"] < minimum
    ):
        raise ValueError("complete cohort, background, heterogeneity or repeated decision competition is unqualified")
    return {
        "fixed_measured_workflows": 18, "minimum_model_work_p80_p20": minimum,
        "minimum_actual_periodic_competition_controls": 10,
        "full_candidate_and_physical_coverage_proven": False,
        "JOIN_shape_limitations_retained": True,
        "oracle_diagnostic_accepted_not_formal_baseline_or_steady_state_claim": True,
        "complete_work_scope": "fixed_measured_workflows" if cohort_stop else "whole_population",
        **({
            "explicit_user_diagnostic": True, "formal_workload_accepted": False,
            "waived_workload_checks": sorted(waived),
            "original_workload_rejection_preserved": True,
        } if diagnostic is not None else {}),
    }


def validate_decision(decision, required_inputs, *, diagnostic=None):
    waived = diagnostic_waivers(diagnostic)
    failed = decision.get("failed_checks")
    exceptional = (
        diagnostic is not None and isinstance(failed, list) and set(failed) == waived
        and set(failed) == {
            name for name, passed in decision.get("checks", {}).items() if passed is False
        }
        and all(
            passed is True for name, passed in decision.get("checks", {}).items()
            if name not in waived
        )
    )
    if (
        decision.get("accepted_for_oracle_diagnostic") is not True
        or decision.get("failed_checks") != []
        or not decision.get("checks")
        or any(value is not True for value in decision["checks"].values())
    ) and not exceptional:
        raise ValueError("sustained workload has not passed the complete phase-one decision")
    pins = decision["inputs_sha256"]
    for path in required_inputs:
        if pins.get(str(path.resolve())) != sha256_file(path):
            raise ValueError("workload decision belongs to different complete reviews")
    for name, digest in pins.items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("workload decision input changed: " + name)


def oracle_artifact(
    source, overlay, coefficient, costs, calibration, *, combination=False, coalescing_window_ms=0,
):
    if type(coalescing_window_ms) is not int or coalescing_window_ms not in (0, 1000, 2000) or (
        combination and coalescing_window_ms
    ):
        raise ValueError("timed windows apply to the separately declared global oracle")
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
        **({"free_dispatch_coalescing_window_ms": coalescing_window_ms}
           if coalescing_window_ms else {}),
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
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--coalescing-window-ms", type=int, choices=(0, 1000, 2000), default=0)
    parser.add_argument("--diagnostic-request", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise ValueError("preserve prior oracle preparations")
    plan, review, cohort, supply, periodic = (
        causal.load(path) for path in (
            args.control, args.control_review, args.cohort_review, args.supply_review, args.periodic_review,
        )
    )
    if cohort.get("cohort", {}).get("schema_version") == 2 and args.cohort is None:
        raise ValueError("measured completion requires its frozen cohort definition")
    completion = verify_execution(args.control, args.cohort)
    review_matches_execution(review, completion)
    source = Path(plan["source_arm"])
    population_path = source / "composite_source.json"
    population = causal.load(population_path)
    decision = causal.load(args.workload_review)
    diagnostic = (
        load_diagnostic_request(args.diagnostic_request, args.control, args.workload_review)
        if args.diagnostic_request is not None else None
    )
    if diagnostic is not None and diagnostic["coalescing_window_ms"] != args.coalescing_window_ms:
        raise ValueError("requested and prepared oracle coalescing windows differ")
    validate_decision(decision, (
        args.control_review, args.cohort_review, args.supply_review, args.periodic_review, population_path,
    ), diagnostic=diagnostic)
    acceptance = validate_workload(
        cohort, supply, periodic, population,
        minimum_model_work_p80_p20=decision.get("minimum_model_work_p80_p20", 3.),
        completion=completion, diagnostic=diagnostic,
    )
    base = Path(plan["experiment_configuration"])
    configuration = causal.load(base)
    common = configuration["common_environment"]
    if (
        plan["order"] != ["full"] or configuration["order"] != ["full"]
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
    artifact = oracle_artifact(
        source, overlay, coefficient, costs, calibration, combination=combination,
        coalescing_window_ms=args.coalescing_window_ms,
    )
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
            "control_completion_protocol": completion["protocol"],
            **({
                "diagnostic_request": {
                    "path": str(args.diagnostic_request.resolve()),
                    "sha256": sha256_file(args.diagnostic_request),
                },
                "formal_workload_accepted": False,
                "original_workload_rejection_preserved": True,
            } if diagnostic is not None else {}),
            **({
                "cohort_definition": str(args.cohort.resolve()),
                "cohort_definition_sha256": sha256_file(args.cohort),
                "control_completion_certificate": completion["certificate"],
                "control_completion_certificate_sha256": completion["certificate_sha256"],
            } if completion["protocol"] == "all_fixed_measured_workflows_completed" else {}),
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
        first_registration_continuation_sibling_dispatch=(
            "coalesced_timed_admission_epochs" if args.coalescing_window_ms
            else "coalesced_existing_native_boundary"
        ),
        free_dispatch_coalescing_window_ms=args.coalescing_window_ms,
        coalescing_and_oracle_ranking_are_joint_change=bool(args.coalescing_window_ms),
        join_eos_recovery_bypasses_ordinary_coalescing_window=True,
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
            *((args.cohort, Path(completion["certificate"])) if args.cohort is not None
              and completion["protocol"] == "all_fixed_measured_workflows_completed" else ()),
            *((args.diagnostic_request,) if args.diagnostic_request is not None else ()),
        )},
        "configuration": str((args.output_dir / "full.json").resolve()),
        "configuration_sha256": sha256_file(args.output_dir / "full.json"),
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    })
    print({"workflows": len(artifact["workflows"]), "requests": len(artifact["requests"]),
           "configuration": str(args.output_dir / "full.json")})


if __name__ == "__main__":
    main()
