"""Compare closed fixed-window worker costs while complete measured tails finish."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments import native_execution
from beliefkv.experiments.source_provenance import sha256_file
from scripts import stop_after_measured_cohort as stop
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_useful_token_cost import analyze
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def matching_conditions(reference, candidate):
    if reference["order"] != candidate["order"] or len(reference["order"]) != 1:
        raise ValueError("compare the same policy before and after the common engine change")
    for key in TRAFFIC_FIELDS:
        if key == "source_server_parameters":
            continue
        if reference.get(key) != candidate.get(key):
            raise ValueError("common execution comparison changed traffic: " + key)
    before = reference.get("source_server_parameters") or {}
    after = candidate.get("source_server_parameters") or {}
    additions = {"attention_backend", "tp_size", "max_total_tokens", "cuda_graph_config"}
    if set(before) - set(after) or (set(after) - set(before)) - additions or any(
        before[key] != after[key] for key in before if key != "cuda_graph_config"
    ):
        raise ValueError("common execution comparison changed source server parameters")
    excluded = {native_execution.CONFIG_KEY, native_execution.HASH_KEY}
    common = [
        {key: value for key, value in plan["reference_environment"].items() if key not in excluded}
        for plan in (reference, candidate)
    ]
    if common[0] != common[1]:
        raise ValueError("common execution comparison changed the agent policy")
    if reference.get("native_execution_configuration") is not None or not candidate.get(
        "native_execution_configuration"
    ):
        raise ValueError("require the original engine and an explicit common execution candidate")


def matching_actual_engines(reference_info, candidate_info, reference_capacity, candidate_capacity):
    fields = (
        "model_path", "dtype", "kv_cache_dtype", "attention_backend", "tp_size", "page_size",
        "max_total_tokens", "max_running_requests", "context_length", "chunked_prefill_size",
        "enable_mixed_chunk",
    )
    if any(reference_info.get(key) != candidate_info.get(key) for key in fields):
        raise ValueError("actual model, precision, backend or serving geometry changed")
    if reference_capacity != candidate_capacity:
        raise ValueError("actual Device or Host capacity changed")
    if reference_info["cuda_graph_config"]["prefill"] != candidate_info["cuda_graph_config"]["prefill"]:
        raise ValueError("common decode configuration changed actual prefill capture")


def reduction(reference, candidate):
    if reference is None or candidate is None or reference <= 0:
        return None
    return 100. * (1. - candidate / reference)


def inspect(reference_path, candidate_path, cohort_path, reference_cost_path):
    reference, candidate, cohort, reference_cost = (
        json.loads(path.read_bytes())
        for path in (reference_path, candidate_path, cohort_path, reference_cost_path)
    )
    matching_conditions(reference, candidate)
    verify_execution(reference_path, cohort_path)
    for path in (reference_path, cohort_path):
        if reference_cost["inputs_sha256"].get(str(path.resolve())) != sha256_file(path):
            raise ValueError("reference cost report belongs to different immutable inputs")
    if reference_cost.get("complete_measured_execution_verified") is not True:
        raise ValueError("require the verified complete reference accounting")
    arm, reader = stop.replay_arm(candidate), SealedRecords()
    stop.causal.comparison.verify_sources(candidate)
    execution = native_execution.verify_completed_configuration(candidate, arm)
    info_path = arm / "server/server_info.json"
    info = json.loads(info_path.read_bytes())
    reference_info_path = stop.replay_arm(reference) / "server/server_info.json"
    reference_info = json.loads(reference_info_path.read_bytes())
    if reference_cost["inputs_sha256"].get(str(reference_info_path.resolve())) != sha256_file(reference_info_path):
        raise ValueError("reference cost report's actual engine evidence changed")
    capacity_paths = [
        stop.replay_arm(plan) / "server/native_capacity_census.json" for plan in (reference, candidate)
    ]
    capacities = [json.loads(path.read_bytes())["capacity"] for path in capacity_paths]
    matching_actual_engines(reference_info, info, *capacities)
    offsets = {
        row["workflow_id"]: row["offset_seconds"]
        for row in candidate["workflow_arrivals"]["arrival_schedule"]
    }
    starts, ends = {}, {}
    stop.boundaries(
        reader(arm / "replay/runtime_events.replay.jsonl"), starts, ends, set(offsets),
    )
    origins = [when - offsets[key] * 1000. for key, when in starts.items()]
    if not origins or max(origins) - min(origins) > 1e-5:
        raise ValueError("candidate arrival origin changed")
    window = tuple(origins[0] + 1000. * value for value in cohort["throughput_window_offset_seconds"])
    seen_end = -float("inf")

    def worker_rows():
        nonlocal seen_end
        for row in reader(arm / "server/runtime_audit.jsonl"):
            if row.get("event") == "gpu_service_sample":
                seen_end = max(seen_end, row["complete_monotonic_ms"])
            yield row

    # The cost accumulator verifies every observed output ordinal. Partial
    # measured/background requests remain in the live worker prefix, so this
    # diagnostic deliberately makes no complete-request reconciliation claim.
    observed = analyze(
        worker_rows(), buckets=info["cuda_graph_config"]["decode"]["bs"],
        receipts={}, measured=set(), window=window,
    )
    if seen_end < window[1] + 10_000.:
        raise ValueError("wait until the worker journal passes the complete common window")
    if list(cohort["throughput_window_offset_seconds"]) != reference_cost["common_window_offset_seconds"]:
        raise ValueError("reference accounting used a different fixed wall-clock window")
    comparisons = {}
    for name, before, after in (
        ("total", reference_cost["scopes"]["common_window"], observed["scopes"]["common_window"]),
        ("decode", reference_cost["phase_costs"]["window:decode"], observed["phase_costs"]["window:decode"]),
        ("prefill", reference_cost["phase_costs"]["window:prefill"], observed["phase_costs"]["window:prefill"]),
    ):
        comparisons[name] = {
            "reference": before, "candidate": after,
            "worker_ms_per_output_token_reduction_percent": reduction(
                before["worker_ms_per_output_token"], after["worker_ms_per_output_token"],
            ),
            "worker_ms_per_executed_extend_token_reduction_percent": reduction(
                before["worker_ms_per_executed_extend_token"], after["worker_ms_per_executed_extend_token"],
            ),
        }
    paths = (
        reference_path, candidate_path, cohort_path, reference_cost_path, info_path,
        reference_info_path, *capacity_paths, Path(__file__),
        ROOT / "scripts/audit_useful_token_cost.py", ROOT / "scripts/verify_measured_execution.py",
        *map(Path, execution["inputs_sha256"]),
    )
    changed_sources = {
        name: {"reference": reference["source_sha256"][name], "candidate": candidate["source_sha256"][name]}
        for name in reference["source_sha256"].keys() & candidate["source_sha256"].keys()
        if reference["source_sha256"][name] != candidate["source_sha256"][name]
    }
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "kind": "closed_fixed_common_window_worker_cost_diagnostic",
        "method": arm.name, "common_window_offset_seconds": cohort["throughput_window_offset_seconds"],
        "actual_Device_and_Host_capacity_equal": capacities[0],
        "actual_non_graph_serving_geometry_equal": True,
        "source_server_metadata_additions": sorted(
            set(candidate["source_server_parameters"]) - set(reference["source_server_parameters"])
        ),
        "application_source_comparison": {
            "reference_commit": reference["code_commit"], "candidate_commit": candidate["code_commit"],
            "changed_common_sources": dict(sorted(changed_sources.items())),
            "changed_runtime_or_policy_paths": sorted(
                name for name in changed_sources
                if name.startswith(("beliefkv/runtime/", "beliefkv/policy/"))
            ),
            "same_agent_policy_configuration": True,
            "isolated_engine_only_attribution_verified": False,
        },
        "comparisons": comparisons,
        "candidate_common_window_decode_by_width": observed["common_window_decode_by_width"],
        "candidate_common_window_worker_coverage_ms": observed["common_window_worker_coverage_ms"],
        "candidate_latest_worker_end_after_window_ms": seen_end - window[1],
        "candidate_measured_completed_at_event_prefix": len(set(cohort["measured_workflow_ids"]) & ends.keys()),
        "candidate_complete_measured_execution_verified": False,
        "candidate_live_output_ordinals_checked": True,
        "candidate_complete_receipt_reconciliation_performed": False,
        "candidate_native_execution": execution,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in paths},
        "semantics": [
            "The same fixed wall-clock window contains all observed output, including background.",
            "Worker costs are assigned by forward completion; interval coverage is clipped exactly.",
            "Different batch/context compositions can change per-token costs; this is not kernel-level causal attribution.",
            "Matching policy configuration does not prove unchanged implementation; changed common sources are disclosed separately.",
            "Live output ordinals are checked but the complete measured/native receipt audit remains pending.",
            "The window is complete; the measured cohort, complete JCT, censoring and owned shutdown are not yet certified.",
            "The saved candidate engine evidence verifies configuration, not completion.",
        ],
        "GPU_action": False, "performance_accepted": False, "JCT_gain": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "candidate", "cohort", "reference-cost", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve existing diagnostics and use nice19 hidden-CUDA analysis")
    result = inspect(args.reference, args.candidate, args.cohort, args.reference_cost)
    write(args.output, result)
    print(json.dumps({
        "method": result["method"], "candidate_measured_completed": result["candidate_measured_completed_at_event_prefix"],
        "cost_reductions_percent": {
            name: row[
                "worker_ms_per_executed_extend_token_reduction_percent" if name == "prefill"
                else "worker_ms_per_output_token_reduction_percent"
            ]
            for name, row in result["comparisons"].items()
        }, "candidate_JCT_gain": None,
    }))


if __name__ == "__main__":
    main()
