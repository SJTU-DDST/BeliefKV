"""Seal live CALL releases, including the generic native causal-owner route."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-foreground-call-a73fdcb7")
OPS = Path("/home/longhao/experiment/ops")
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from audit_final82_live_tail_20261031 import prefix_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve live observations and use hidden-CUDA nice19")
    registration = OPS / "workflow_original56_foreground_call_20261031_r1.registration.json"
    plan = causal.load(registration)
    state = causal.load(registration.with_suffix(".state.json"))
    handles = {name: state[name] for name in ("watcher", "server", "driver")}
    if state["status"] != "running_full" or not all(causal.reservation.alive(row) for row in handles.values()):
        raise ValueError("require the actual running Full candidate")
    arm, reader, last, pending, cases = Path(plan["run_root"]) / "full", SealedRecords(), {}, {}, []
    last_event_ms = 0.
    for row in reader(arm / "replay/runtime_events.replay.jsonl"):
        kind, owner = row["kind"], row.get("invocation_id")
        last_event_ms = max(last_event_ms, row["ts_ms"])
        if kind == "llm_result":
            last[owner] = row["attributes"]["request_id"]
        elif kind == "call":
            case = {
                "caller_id": owner, "child_id": row["target_invocation_id"],
                "prior_request_id": last[owner], "start_ms": row["ts_ms"],
            }
            pending[case["child_id"]] = case
            cases.append(case)
        elif kind == "return" and owner in pending:
            pending.pop(owner)["end_ms"] = row["ts_ms"]
    wanted = {case["prior_request_id"] for case in cases}
    receipts = {
        row["request_id"]: row["native_receipt"]
        for row in reader(arm / "replay/native_requests.jsonl") if row["request_id"] in wanted
    }
    owners = {}
    for case in cases:
        receipt = receipts[case["prior_request_id"]]
        case["native_owner"] = receipt["session_id"], receipt["session_generation"]
        owners.setdefault(case["native_owner"], []).append(case)
    direct, generic, offset = [], [], None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        event = row.get("event")
        if event == "admission_runtime_state":
            offset = row["ts_ms"] - row["monotonic_ms"]
        if offset is None:
            continue
        when = row["ts_ms"] - offset if "ts_ms" in row else None
        if event == "parent_pressure_demoted" and row.get("source") == "child_wait":
            direct.append(row)
        elif event in (
            "critical_parent_headroom", "wait_prefetch_capacity_reclaimed",
            "execution_handoff_capacity_reclaimed",
        ):
            for victim in row["victims"]:
                if victim["scope"] != "causal_waiting_full":
                    continue
                for owner in victim.get("full_session_owners", ()):
                    for case in owners.get(tuple(owner), ()):
                        if case["start_ms"] + 50 < when < case.get("end_ms", last_event_ms) - 50:
                            generic.append({
                                "CALL_child_id": case["child_id"],
                                "event": event, "release_monotonic_ms": when, "victim": victim,
                                "beneficiary_context_id": row.get("context_id"),
                                "beneficiary_workflow_id": row.get("workflow_id"),
                            })
    for filename, prefix in reader.prefixes.items():
        if prefix_digest(Path(filename), prefix["complete_prefix_bytes"]) != prefix["complete_prefix_sha256"]:
            raise ValueError("sealed live observations changed")
    write(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration), "CALLs_observed": len(cases),
        "closed_CALLs": len(cases) - len(pending),
        "direct_child_wait_release_events": direct, "generic_native_CALL_releases": generic,
        "generic_native_CALL_freed_tokens": sum(row["victim"]["freed_units"].get(
            "0", row["victim"]["freed_units"].get(0, 0)) for row in generic),
        "input_jsonl_prefixes": reader.prefixes,
        "actual_handles_alive_at_exit": {name: causal.reservation.alive(row) for name, row in handles.items()},
        "inputs_sha256": {str(path): sha256_file(path) for path in (registration, Path(__file__))},
        "scope": [
            "Partial live evidence; generic owners are matched to actual caller receipt sessions.",
            "A direct counter of zero does not prove no native causal-owner release.",
            "Positive release does not prove a counterfactual JCT gain or complete physical residency.",
            "Open CALL windows are bounded by the independently observed last event.",
        ],
        "GPU_action": False, "complete_measured_JCT_available": False,
        "performance_accepted": False,
    })
    print({
        "CALLs": len(cases), "direct_releases": len(direct),
        "generic_native_releases": len(generic),
        "generic_freed_tokens": sum(row["victim"]["freed_units"].get(
            "0", row["victim"]["freed_units"].get(0, 0)) for row in generic),
    }, flush=True)


if __name__ == "__main__":
    main()
