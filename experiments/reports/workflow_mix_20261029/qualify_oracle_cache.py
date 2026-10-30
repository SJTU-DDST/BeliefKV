"""Compare cached and literal paths on complete, already finished oracle evidence."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import summary
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle, scan_remaining_paths
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import _native_progress_records, native_decode_progress, write
from scripts.run_causal_gpu_replay import source_plan


def query_groups(records):
    groups = []
    for row in records:
        kind = row.get("event")
        if kind == "workflow_size_resume_selection":
            workflows = list(dict.fromkeys(
                item["key"]["root_workflow_id"] for item in row["candidates"]
            ))
        elif kind == "workflow_size_free_dispatch_order":
            workflows = list(dict.fromkeys(
                item["key"]["root_workflow_id"] for item in row["ordered"]
            ))
        else:
            continue
        if workflows:
            groups.append({
                "now": row["monotonic_s"], "workflows": workflows, "source": kind,
            })
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-progress", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve earlier cache evidence")
    plan = causal.load(args.registration)
    work = complete_work(args.registration)
    if work is None:
        raise ValueError("use complete old work with fresh drain and actual owned exit")
    common = causal.load(args.configuration)["common_environment"]
    oracle = WorkflowWorkOracle(common["WORKFLOW_WORK_MODEL"], common["WORKFLOW_WORK_MODEL_SHA256"])
    if {rid: tuple(values[:2]) for rid, values in work.items()} != {
        rid: (item["prompt_tokens"], item["output_tokens"])
        for rid, item in oracle.artifact.requests.items()
    }:
        raise ValueError("cache qualification is bound to another token population")
    source = Path(plan["source_arm"])
    graph, _expected, binding = source_plan(source)
    oracle.artifact.validate_source(source, graph, binding)
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    events = [RuntimeEvent.from_dict(row) for row in reader(arm / "replay/runtime_events.replay.jsonl")]
    progress_path = arm / "server/runtime_audit.jsonl"
    partial = native_decode_progress(
        events, _native_progress_records(progress_path),
        {rid: item["output_tokens"] for rid, item in oracle.artifact.requests.items()},
    ) if args.native_progress else []
    groups = query_groups(reader(arm / "opportunities/admission_opportunities.jsonl"))
    timeline = [(event.ts_ms / 1000., 0, index, event) for index, event in enumerate(events)]
    timeline.extend((row["ts_ms"] / 1000., 1, index, row) for index, row in enumerate(partial))
    timeline.extend((row["now"], 2, index, row) for index, row in enumerate(groups))
    timeline.sort(key=lambda row: row[:3])
    clock = [timeline[0][0]]
    counts, cached_us, literal_us = Counter(), [], []
    maximum_difference = 0.
    all_inputs = {
        str(path): sha256_file(path) for path in (
            args.registration, args.configuration, progress_path,
            Path(common["WORKFLOW_WORK_MODEL"]), Path(__file__),
            ROOT / "beliefkv/runtime/workflow_work_oracle.py",
        )
    }
    with patch("beliefkv.runtime.workflow_work_oracle.time.monotonic", side_effect=lambda: clock[0]):
        for now, kind, _index, observation in timeline:
            clock[0] = now
            if kind == 0:
                event = observation
                if event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
                    rid = event.attributes["request_id"]
                    request = oracle.artifact.requests[rid]
                    call = (rid, event.invocation_id, event.context_id, range(request["prompt_tokens"]))
                    if event.kind is Kind.LLM_SUBMIT:
                        oracle.submit(event.workflow_id, "started", *call)
                    else:
                        oracle.submit(event.workflow_id, "completed", *call, range(request["output_tokens"]))
                oracle.submit(event.workflow_id, "event", event)
                counts["observed_events"] += 1
            elif kind == 1:
                oracle.submit(
                    observation["workflow_id"], "generated",
                    observation["request_id"], observation["output_tokens"],
                )
                counts["observed_partial_outputs"] += 1
            else:
                scores, reference_scores = {}, {}
                for workflow in observation["workflows"]:
                    began = time.thread_time()
                    value = oracle.prediction(workflow)
                    cached_us.append((time.thread_time() - began) * 1e6)
                    if value is None:
                        raise ValueError("recorded query references an already closed workflow")
                    began = time.thread_time()
                    reference = scan_remaining_paths(
                        oracle.artifact.graphs[workflow], oracle.artifact.requests,
                        oracle._done_at, oracle._active,
                        oracle.artifact.model_seconds_per_work_token, now,
                    )
                    literal_us.append((time.thread_time() - began) * 1e6)
                    values = (
                        value.remaining_path_s, value.model_only_path_s,
                        value.tool_and_fixed_delay_path_s,
                    )
                    delta = max(abs(left - right) for left, right in zip(values, reference))
                    maximum_difference = max(maximum_difference, delta)
                    if any(
                        not math.isfinite(left) or not math.isclose(left, right, abs_tol=1e-7, rel_tol=1e-12)
                        for left, right in zip(values, reference)
                    ):
                        raise ValueError("cached remaining paths differ from the literal oracle")
                    scores[workflow], reference_scores[workflow] = values[0], reference[0]
                    counts["queries"] += 1
                # Tiny floating point differences are allowed for seconds, but
                # report even a tie-order change within the same candidate group.
                key = lambda item: (item[1], item[0])
                counts["priority_order_changes"] += (
                    sorted(scores.items(), key=key) != sorted(reference_scores.items(), key=key)
                    and [item[0] for item in sorted(scores.items(), key=key)]
                    != [item[0] for item in sorted(reference_scores.items(), key=key)]
                )
                counts["query_groups"] += 1
    status = oracle.status()
    if (
        status["error"] or any(status["remaining_work_units"].values())
        or status["counts"]["completed_requests"] != len(work)
        or status["counts"]["closed_workflows"] != len(oracle.artifact.workflows)
        or status["counts"].get("unmodeled_core_event_observations", 0)
        or all_inputs != {name: sha256_file(Path(name)) for name in all_inputs}
    ):
        raise ValueError("cache qualification lacks complete stable observed work")
    write(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "maximum_path_difference_s": maximum_difference, "cached_thread_cpu_us": summary(cached_us),
        "literal_thread_cpu_us": summary(literal_us), "cached_total_cpu_s": sum(cached_us) / 1e6,
        "literal_total_cpu_s": sum(literal_us) / 1e6, "oracle": status,
        "inputs_sha256": all_inputs, "input_jsonl_prefixes": reader.prefixes,
        "scope": {
            "same_complete_tokens_and_observed_events": True,
            "partial_output_quantum": 128 if args.native_progress else None,
            "native_partial_progress_included": args.native_progress,
            "observed_query_groups": "recorded_periodic_candidates_and_changed_dispatch_candidates",
            "all_online_queries_reconstructed": False,
            "completion_progress_time": "client_result_boundary_not_exact_native_callback",
            "priority_order_changes_are_local_not_JCT_counterfactual": True,
            "future_information_used": True, "deployable": False,
            "GPU_action": False, "performance_accepted": False,
        },
    })
    print({
        "queries": counts["queries"], "priority_order_changes": counts["priority_order_changes"],
        "maximum_difference_s": maximum_difference, "cached_cpu_s": sum(cached_us) / 1e6,
        "literal_cpu_s": sum(literal_us) / 1e6, "cache": status["counts"],
    })


if __name__ == "__main__":
    main()
