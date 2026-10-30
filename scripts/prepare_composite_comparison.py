"""Freeze sustained arrivals and private cohort analysis for a composed trace."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.composite_workload import load_composed_source
from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import load_overlay
from beliefkv.experiments.workflow_cohort import declare_cohort
from scripts.train_workflow_work import write


BASE = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"


def measurement_protocol(
    ids, *, rate_per_second, arrival_seed, permutation_seed, initial_workflows, warmup, measured,
):
    if not 1 <= initial_workflows <= warmup:
        raise ValueError("initial fill must be included in the declared warmup cohort")
    arrivals = permuted_poisson_arrival_plan(
        ids, rate_per_second=rate_per_second, seed=arrival_seed,
        permutation_seed=permutation_seed, initial_workflows=initial_workflows,
    )
    cohort = declare_cohort(
        arrivals, warmup=warmup, measured=measured,
        throughput_start_s=arrivals["arrival_schedule"][warmup]["offset_seconds"],
        throughput_end_s=arrivals["arrival_schedule"][-1]["offset_seconds"],
    )
    return arrivals, cohort


def configuration(
    base, *, workflow_count, workload_manifest, source_registration, arrivals, timing, prior,
    dispatch_interval_ms=0,
):
    if type(dispatch_interval_ms) is not int or dispatch_interval_ms not in (0, 500):
        raise ValueError("declare unchanged dispatch or matched 500ms bounded dispatch")
    result = deepcopy(base)
    common = result["common_environment"]
    common.update(
        ROOT_COUNT=str(workflow_count), MAX_RUNNING_REQUESTS="24",
        WORKLOAD_MANIFEST=str(workload_manifest.resolve().relative_to(ROOT)),
        WORKFLOW_ARRIVAL_SCHEDULE="", ARRIVAL_BATCH_SIZE="0", ARRIVAL_BATCH_INTERVAL_MS="0",
        TOOL_PREFETCH="0", BELIEFKV_ENABLE_TOOL_PREFETCH="0",
        LAYER_HANDOFF="0", BELIEFKV_LAYER_HANDOFF="0", ENABLE_MIXED_CHUNK="0",
        WORKFLOW_SIZE_RESUME="0", BELIEFKV_WORKFLOW_SIZE_RESUME="0",
        WORKFLOW_WORK_SOURCE="learned", BELIEFKV_WORKFLOW_WORK_SOURCE="learned",
        WORKFLOW_WORK_MODEL="", BELIEFKV_WORKFLOW_WORK_MODEL="",
        WORKFLOW_WORK_MODEL_SHA256="", BELIEFKV_WORKFLOW_WORK_MODEL_SHA256="",
        AGENT_FREE_DISPATCH_INTERVAL_MS=str(dispatch_interval_ms),
        BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS=str(dispatch_interval_ms),
        BELIEFKV_TOOL_TIMING_PRIOR=str(prior.resolve()),
        BELIEFKV_TOOL_TIMING_PRIOR_SHA256=sha256_file(prior),
    )
    common.pop("CLIENT_ROOT_CONCURRENCY", None)
    for name in ("candidate_provenance", "arrival_experiment", "serving_profile_experiment"):
        result.pop(name, None)
    result.update(
        status="composed_original_workload_sustained_Full_calibration_pending",
        source_registration=str(source_registration.resolve()), order=["full"],
        stream_response_metrics=True,
        tool_timing_overlay={
            "path": str(timing.resolve()), "sha256": sha256_file(timing),
            "timing_domain": json.loads(timing.read_bytes())["timing_domain"],
            "synthetic_tool_timing": True,
        },
        workflow_arrivals={"path": str(arrivals.resolve()), "sha256": sha256_file(arrivals)},
        native_reference={"repeat_full_native_replay": False, "matched_native_capture_exists": False},
        gpu_execution={"registered": False, "requires_fresh_position_and_sampling": True},
        performance_accepted=False,
    )
    result["comparison_contract"].update(
        workflow_work_ranking=False, future_information_used=False,
        complete_original_workflows=True, independently_compiled_capture_clock_domains=True,
        same_fixed_tools_arrivals_and_resources_for_all_arms=True,
        source_native_JCT_is_not_a_matched_overlay_control=True,
        measured_cohort_not_delivered_to_scheduler=True,
        tool_prefetch_disabled_by_user=True, layer_handoff=False,
        free_dispatch_interval_ms=dispatch_interval_ms,
        free_dispatch_cadence_must_match_future_oracle=True,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-arm", type=Path, required=True)
    parser.add_argument("--source-registration", type=Path, required=True)
    parser.add_argument("--tool-timing", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rate-per-second", type=float, default=.02)
    parser.add_argument("--initial-workflows", type=int, default=6)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--measured", type=int, default=18)
    parser.add_argument("--arrival-seed", type=int, default=31)
    parser.add_argument("--permutation-seed", type=int, default=31)
    parser.add_argument("--dispatch-interval-ms", type=int, choices=(0, 500), default=0)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve all arrival/cohort/configuration declarations")
    graph, _, evidence = load_composed_source(args.source_arm)
    overlay = json.loads(args.tool_timing.read_bytes())
    load_overlay(graph, args.tool_timing)
    ids = sorted({node.workflow_id for node in graph.nodes})
    arrivals, cohort = measurement_protocol(
        ids, rate_per_second=args.rate_per_second, arrival_seed=args.arrival_seed,
        permutation_seed=args.permutation_seed, initial_workflows=args.initial_workflows,
        warmup=args.warmup, measured=args.measured,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    arrival_path, cohort_path = args.output_dir / "arrivals.json", args.output_dir / "analysis_cohort.json"
    write(arrival_path, arrivals)
    write(cohort_path, cohort)
    prior = Path(overlay["offline_prior"]["path"])
    if sha256_file(prior) != overlay["offline_prior"]["sha256"]:
        raise ValueError("offline timing prior changed")
    config = configuration(
        json.loads(BASE.read_bytes()), workflow_count=len(ids),
        workload_manifest=args.source_arm / "workload_manifest.json",
        source_registration=args.source_registration, arrivals=arrival_path, timing=args.tool_timing,
        prior=prior,
        dispatch_interval_ms=args.dispatch_interval_ms,
    )
    config_path = args.output_dir / "full.json"
    write(config_path, config)
    write(args.output_dir / "preparation.json", {
        "source_evidence": evidence, "workflows": len(ids),
        "initial_workflows": args.initial_workflows, "warmup": args.warmup, "measured": args.measured,
        "background": len(ids) - args.warmup - args.measured,
        "arrival_rate_per_second": args.rate_per_second,
        "mean_arrival_interval_seconds": 1. / args.rate_per_second,
        "arrival_seed": args.arrival_seed, "permutation_seed": args.permutation_seed,
        "free_dispatch_interval_ms": args.dispatch_interval_ms,
        "common_submission_end_seconds": arrivals["arrival_schedule"][-1]["offset_seconds"],
        "inputs_sha256": {str(path.resolve()): sha256_file(path)
                          for path in (BASE, args.tool_timing, prior, Path(__file__))},
        "configuration": str(config_path.resolve()), "configuration_sha256": sha256_file(config_path),
        "private_analysis_cohort": str(cohort_path.resolve()),
        "private_analysis_cohort_sha256": sha256_file(cohort_path),
        "pressure_and_background_coverage_require_actual_calibration": True,
        "sorting_oracle_is_separate_from_this_retained_Full_control": True,
        "formal_comparison_ready": False, "GPU_action": False, "performance_accepted": False,
    })
    print({
        "workflows": len(ids), "measured": args.measured,
        "background": len(cohort["background_workflow_ids"]), "mean_interval_s": 1. / args.rate_per_second,
        "submission_end_s": arrivals["arrival_schedule"][-1]["offset_seconds"],
    })


if __name__ == "__main__":
    main()
