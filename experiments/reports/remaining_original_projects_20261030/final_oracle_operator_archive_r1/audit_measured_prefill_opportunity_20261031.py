"""Reconstruct fixed-cohort critical paths before considering chunk changes."""

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "measured_prefill_opportunity_complete_controls_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_causal_jct_paths import account, first_launches, request_cohorts
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def inspect(registration, cohort_path):
    completion = verify_execution(registration, cohort_path)
    plan = causal.load(registration)
    measured = set(causal.load(cohort_path)["measured_workflow_ids"])
    arm = Path(plan["run_root"]) / "full"
    graph, _expected, _evidence = source_plan(Path(plan["source_arm"]))
    nodes = tuple(node for node in graph.nodes if node.workflow_id in measured)
    node_ids = {node.node_id for node in nodes}
    if any(not set(node.dependencies) <= node_ids for node in nodes):
        raise ValueError("measurement restriction would remove a real dependency")
    request_ids = {node.request_id for node in nodes if node.request_id}
    graph = replace(graph, nodes=nodes, request_usage={
        key: value for key, value in graph.request_usage.items() if key in request_ids
    })
    reader = SealedRecords()
    events = [
        row for row in reader(arm / "replay/runtime_events.replay.jsonl")
        if row.get("workflow_id") in measured
    ]
    requests = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
        if row["native_receipt"]["identity"][0] in measured
    }
    status_path = arm / "server/native_telemetry_status.json"
    status = causal.load(status_path)
    count = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    all_spans, prefill_spans, own, first_decode = [], [], defaultdict(list), {}
    prefill, decoded_widths = [], []
    clocks = set()

    def samples():
        for row in reader(arm / "server/runtime_audit.jsonl"):
            if row.get("event") == "gpu_service_sample":
                begin, end = row["service_start_monotonic_ms"], row["complete_monotonic_ms"]
                if not math.isclose(end - begin, row["service_elapsed_ms"], abs_tol=.01, rel_tol=1e-9):
                    raise ValueError("worker interval differs from its recorded elapsed time")
                clocks.add(row["monotonic_clock_domain"])
                all_spans.append((begin, end))
                members = row["request_samples"]
                if row["phase"] == "prefill":
                    prefill_spans.append((begin, end))
                    prefill.append({
                        "worker_ms": row["service_elapsed_ms"],
                        "input_tokens": sum(member["token_delta"] for member in members),
                        "context_before_sum": sum(member["sequence_tokens_before"] for member in members),
                        "batch_size": row["batch_size"],
                    })
                elif row["phase"] == "decode":
                    decoded_widths.append((row["batch_size"], row["service_elapsed_ms"]))
                for member in members:
                    rid = member["request_id"]
                    if rid not in requests:
                        continue
                    own[rid].append((begin, end))
                    if member["phase"] == "decode" and member["token_delta"] > 0:
                        first_decode.setdefault(rid, row["launch_monotonic_ms"])
            yield row

    launches = first_launches(samples(), requests, expected_records=count)
    if len(clocks) != 1 or request_ids != requests.keys():
        raise ValueError("request population or native monotonic clock is incomplete")
    certificate = causal.load(Path(completion["certificate"]))
    paths = account(
        graph, events, requests.values(), launches,
        expected_jct_ms=certificate["measured_JCT_ms"], cohorts=request_cohorts(events),
    )
    worker, fills = EnvelopeIndex(all_spans), EnvelopeIndex(prefill_spans)
    intervals = {}
    for rid, request in requests.items():
        begin, end = launches[rid], request["native_complete_monotonic_ms"]
        submitted = request["submitted_monotonic_ms"]
        covered = EnvelopeIndex(own[rid]).overlap_ms(begin, end)
        decode_begin = first_decode.get(rid)
        intervals[rid] = {
            "submit_to_first_launch_ms": begin - submitted,
            "first_launch_to_complete_ms": end - begin,
            "submit_wait_covered_by_recorded_worker_ms": worker.overlap_ms(submitted, begin),
            "submit_wait_without_recorded_worker_ms": begin - submitted - worker.overlap_ms(submitted, begin),
            "own_worker_union_ms": covered,
            "after_launch_outside_own_worker_ms": end - begin - covered,
            "prefill_worker_overlap_after_first_decode_ms": (
                fills.overlap_ms(decode_begin, end) if decode_begin is not None else None
            ),
            "first_decode_observed": decode_begin is not None,
        }
    workflows = {}
    for workflow, path in paths["workflows"].items():
        members = [intervals[rid] for rid in path["critical_path_requests"]]
        workflows[workflow] = {
            "JCT_ms": path["jct_ms"], "critical_path_requests": len(members),
            **{key: sum(row[key] for row in members if row[key] is not None)
               for key in next(iter(intervals.values())) if key.endswith("_ms")},
        }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed source changed while being audited")
    return {
        "registration": str(registration), "measured_requests": len(requests),
        "measured_workflows": len(workflows), "critical_path": paths,
        "workflow_equal_weight_mean_s": {
            key.removesuffix("_ms") + "_s": statistics.mean(row[key] for row in workflows.values()) / 1000.
            for key in next(iter(workflows.values())) if key.endswith("_ms")
        },
        "workflows": workflows,
        "whole_execution_prefill_worker_ms": distribution(row["worker_ms"] for row in prefill),
        "whole_execution_prefill_input_tokens": distribution(row["input_tokens"] for row in prefill),
        "prefill_over_100ms_batches": sum(row["worker_ms"] > 100. for row in prefill),
        "decode_worker_weighted_width": sum(width * ms for width, ms in decoded_widths) / sum(
            ms for _width, ms in decoded_widths),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            registration, cohort_path, status_path, Path(completion["certificate"]), Path(__file__),
        )},
        "scope": [
            "All fixed measured tails and exact complete measured native work are verified.",
            "Worker intervals include host submission and waits, not pure GPU kernel time.",
            "Prefill overlap after first decode is exposure to possible interleaving, not removable work or JCT.",
            "Other workflows need that prefill; shortening or reordering it can change throughput and later dependencies.",
            "No sampled service interval is extrapolated; all recorded worker samples are counted.",
            "This descriptive audit alone does not authorize a GPU chunk sweep.",
        ],
        "GPU_action": False,
    }


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve reports and use nice19 CPU-only analysis")
    definitions = {
        "best_original56": (
            OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
            REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json",
        ),
        "latest_original69": (
            OPS / "workflow_original69_initial12_mean70_predictive_control_20261031_r1.registration.json",
            REPORTS / "original69_initial12_mean70_prepare_r1/analysis_cohort.json",
        ),
    }
    results = {}
    for label, pair in definitions.items():
        result = inspect(*pair)
        write(HERE / (label + ".json"), result)
        results[label] = result
        print(json.dumps({"label": label, **result["workflow_equal_weight_mean_s"]}), flush=True)
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": results,
        "GPU_action": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
