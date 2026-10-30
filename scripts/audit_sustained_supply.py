"""Inspect a sustained workload's supply without changing its serving process."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.sustained_supply import inspect_supply
from beliefkv.experiments.workflow_source_work import reconstruct_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def bound_arrivals(registration):
    path = Path(registration["workflow_arrivals_path"])
    arrivals = json.loads(path.read_bytes())
    if arrivals != registration["workflow_arrivals"]:
        raise ValueError("serving arrivals changed")
    return path, arrivals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier supply observations")
    registration = json.loads(args.registration.read_bytes())
    arrivals_path, arrivals = bound_arrivals(registration)
    source = Path(registration["source_arm"])
    plan, expected, evidence = source_plan(source)
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        requests, totals = reconstruct_work(plan, corpus)
    arm = Path(registration["run_root"]) / "full"
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    result = inspect_supply(
        arrivals, json.loads(args.cohort.read_bytes()),
        events, states, requests, totals,
    )
    physical = list(reader(arm / "server/host_pool_telemetry.jsonl"))
    result["physical_pool_samples"] = {
        "count": len(physical), "first": physical[0] if physical else None,
        "latest": physical[-1] if physical else None,
        "semantics": "allocator occupancy includes evictable and pending state; not unique useful KV",
    }
    telemetry = arm / "server/native_telemetry_status.json"
    status = json.loads(telemetry.read_bytes())
    result["latest_native_counters_not_atomic_with_prefixes"] = {
        key: status.get(key) for key in (
            "context_prefix_reuse_evidence", "host_block_eviction_attribution", "host_pool_evidence",
        )
    }
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration.resolve()), source_evidence=evidence,
        input_jsonl_prefixes=reader.prefixes, GPU_action=False,
        serving_process_changed=False, formal_workload_qualified=False,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.cohort, arrivals_path, Path(__file__),
            ROOT / "beliefkv/experiments/sustained_supply.py",
            ROOT / "beliefkv/experiments/workflow_source_work.py",
        )},
    )
    write(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "observed_arrivals", "observed_completions", "measured_completed", "sample_counts",
    )}))


if __name__ == "__main__":
    main()
