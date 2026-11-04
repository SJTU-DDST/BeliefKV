"""Bound recorded prefill exposure after observed tool and JOIN dependencies."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_compute_frontier_projection import dependency_model, propagate
from scripts.audit_join_reentry_service import join_reentries
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def critical_requests(model, workflow):
    by_index = {position: request for request, position in model["requests"].items()}
    current, requests = model["ends"][workflow], set()
    while True:
        if current in by_index:
            requests.add(by_index[current])
        deps = model["dependencies"][current]
        if not deps:
            return requests
        current = max(deps, key=model["reference"].__getitem__)


def inspect(registration, cohort_path):
    complete = verify_execution(registration, cohort_path)
    config = json.loads(registration.read_bytes())
    cohort = json.loads(cohort_path.read_bytes())
    measured = set(cohort["measured_workflow_ids"])
    arm, reader = Path(config["run_root"]) / "full", SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    requests = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
    }
    graph, _expected, _evidence = source_plan(Path(config["source_arm"]))
    model = dependency_model(graph, events, requests)
    baseline = propagate(model)
    if any(abs(when - value) > 1e-5 for when, value in zip(baseline, model["reference"])):
        raise ValueError("baseline DAG projection changed observed completion")
    certificate = json.loads(Path(complete["certificate"]).read_bytes())
    for workflow in measured:
        value = baseline[model["ends"][workflow]] - baseline[model["starts"][workflow]]
        if not math.isclose(value, certificate["measured_JCT_ms"][workflow], abs_tol=1e-5, rel_tol=0.):
            raise ValueError("complete measured JCT differs from the DAG")
    join_rows, _anchor = join_reentries(events)
    tool_rows, _anchor, _diagnostics = tool_reentries(events)
    multi_joins = {
        row["join_id"] for row in events if row["kind"] == "join_create"
        and row["attributes"].get("mode") == "all" and len(row["member_invocation_ids"]) >= 2
    }
    cohorts = {
        "post_join_all": {row["request_id"] for row in join_rows if "request_id" in row},
        "post_join_multiple_children": {
            row["request_id"] for row in join_rows
            if "request_id" in row and row["join_id"] in multi_joins
        },
        "post_tool": {row["request_id"] for row in tool_rows if "request_id" in row},
    }
    critical = {workflow: critical_requests(model, workflow) for workflow in measured}
    critical["all_measured_requests"] = {
        rid for rid, row in requests.items() if row["native_receipt"]["identity"][0] in measured
    }
    batches, request_batches, extended = {}, defaultdict(set), defaultdict(int)
    worker_count = 0
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        worker_count += 1
        if row["sample_id"] != f"native-service-{worker_count:09d}":
            raise ValueError("missing worker sample")
        if row["phase"] != "prefill":
            continue
        elapsed = row["service_elapsed_ms"]
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid prefill worker duration")
        batches[row["sample_id"]] = elapsed
        for member in row["request_samples"]:
            rid = member["request_id"]
            request_batches[rid].add(row["sample_id"])
            if (
                member["phase"] == "prefill"
                and member.get("token_delta_semantics") == "prefill_extend_input_len"
            ):
                extended[rid] += member["token_delta"]
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if (
        worker_count != status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
        or status.get("writer_error") is not None or status["dropped_records"] or status["failed_records"]
    ):
        raise ValueError("incomplete native worker record")
    result = {}
    for name, targets in cohorts.items():
        measured_targets = targets & critical["all_measured_requests"]
        per_workflow = {}
        for workflow in measured:
            selected = targets & critical[workflow]
            shared_batches = {
                batch for rid in selected for batch in request_batches.get(rid, ())
            }
            per_workflow[workflow] = {
                "critical_path_requests": len(selected),
                "whole_prefill_worker_exposure_ms": sum(batches[batch] for batch in shared_batches),
                "observed_prefill_extend_tokens": sum(extended[rid] for rid in selected),
            }
        mean = statistics.mean(row["whole_prefill_worker_exposure_ms"] for row in per_workflow.values()) / 1000.
        result[name] = {
            "all_observed_reentries": len(targets), "complete_measured_reentries": len(measured_targets),
            "critical_measured_reentries": sum(row["critical_path_requests"] for row in per_workflow.values()),
            "workflow_equal_weight_prefill_exposure_s": mean,
            "exposure_fraction_of_observed_mean_JCT": mean / (
                statistics.mean(certificate["measured_JCT_ms"].values()) / 1000.
            ),
            "complete_measured_request_extend_tokens": distribution(extended[rid] for rid in measured_targets),
            "by_workflow": per_workflow,
        }
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "registration": str(registration),
        "complete_measured_workflows": len(measured), "complete_measured_requests": complete["measured_requests"],
        "worker_samples": worker_count, "cohorts": result,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            registration, cohort_path, status_path, Path(__file__),
            ROOT / "scripts/audit_compute_frontier_projection.py",
            ROOT / "scripts/audit_join_reentry_service.py",
            ROOT / "scripts/audit_tool_reentry_service.py",
        )},
        "scope": [
            "The original complete measured JCT is reproduced before counting exposure.",
            "Whole shared prefill batches are deduplicated within each workflow's observed last-completing path.",
            "Tool and JOIN populations can overlap; their exposure must not be added.",
            "Exposure includes necessary work and possible old-prefix recomputation, not just new tool-result input.",
            "No proof is supplied that future input bytes were stable or available before the dependency completed.",
            "This is recorded exposure, not removable JCT, an online permission or a global performance bound.",
            "Earlier work would consume GPU and KV capacity and may delay other workflows; those costs remain unmodeled.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only execution")
    result = inspect(args.registration, args.cohort)
    write(args.output, result)
    print(json.dumps({
        name: {key: value for key, value in row.items() if key != "by_workflow"}
        for name, row in result["cohorts"].items()
    }), flush=True)


if __name__ == "__main__":
    main()
