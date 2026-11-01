"""Seal live fixed-cohort progress and actual periodic eligibility separately."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
OUTPUT = REPORTS / "final82_live_control_r1/measured_tail.json"
REGISTRATION = OPS / "workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json"
COHORT = REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json"
COORDINATOR = OPS / "final_oracle_native_20261031_r1.state.json"
OBSERVER = OPS / "workflow_final82_initial12_mean70_predictive_control_measured_completion_20261031_r1.registration.state.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def prefix_digest(path, count):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while count:
            block = stream.read(min(count, 1 << 20))
            if not block:
                raise ValueError("live evidence prefix became shorter")
            digest.update(block)
            count -= len(block)
    return digest.hexdigest()


def main():
    if OUTPUT.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve live evidence and use nice19 hidden-CUDA settings")
    plan = causal.load(REGISTRATION)
    arm = Path(plan["run_root"]) / "full"
    initial = {
        "serving": causal.load(REGISTRATION.with_suffix(".state.json")),
        "observer": causal.load(OBSERVER), "coordinator": causal.load(COORDINATOR),
    }
    identities = {
        **{name: initial["serving"][name] for name in ("watcher", "driver", "server")},
        "observer": initial["observer"]["process"],
        "coordinator": initial["coordinator"]["process"],
    }
    alive = {name: causal.reservation.alive(row) for name, row in identities.items()}
    if initial["serving"]["status"] != "running_full" or not all(alive.values()):
        raise ValueError("live-tail audit requires the actual declared Full owners")
    reader, measured = SealedRecords(), set(causal.load(COHORT)["measured_workflow_ids"])
    expected = causal.load(Path(plan["source_arm"]) / "expected.json")
    required = Counter(row["llm_submit"][0][0] for row in expected.values())
    ended, pending, tools, joins = set(), {}, set(), {}
    for row in reader(arm / "replay/runtime_events.replay.jsonl"):
        kind, invocation, attrs = row["kind"], row.get("invocation_id"), row.get("attributes") or {}
        if kind == "workflow_end":
            ended.add(row["workflow_id"])
        elif kind == "llm_submit":
            if invocation in pending:
                raise ValueError("one serial invocation submitted overlapping requests")
            pending[invocation] = row
        elif kind == "llm_result":
            previous = pending.pop(invocation, None)
            if previous is None or previous["attributes"]["request_id"] != attrs["request_id"]:
                raise ValueError("LLM result has no matching submitted request")
        elif kind in ("tool_start", "tool_end"):
            key = invocation, attrs.get("tool_run_id") or attrs.get("tool_call_id")
            if kind == "tool_start":
                tools.add(key)
            else:
                tools.discard(key)
        elif kind == "join_wait":
            joins[row["workflow_id"], row.get("join_id")] = invocation
        elif kind in ("join_satisfied", "join_timeout"):
            joins.pop((row["workflow_id"], row.get("join_id")), None)
    completed, outputs, last, receipt_ids = Counter(), Counter(), {}, set()
    for row in reader(arm / "replay/native_requests.jsonl"):
        if row["request_id"] in receipt_ids or row.get("exact_tokens_verified") is not True:
            raise ValueError("duplicate or inexact live request receipt")
        receipt_ids.add(row["request_id"])
        workflow = row["native_receipt"]["identity"][0]
        completed[workflow] += 1
        outputs[workflow] += row["output_tokens"]
        last[workflow] = max(last.get(workflow, 0.), row["native_complete_monotonic_ms"])
    recent, latest_state, latest_periodic = [], None, None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "admission_runtime_state":
            latest_state = row
        elif row.get("event") == "agent_periodic_admission_decision":
            latest_periodic = row
            recent.append(row)
            cutoff = row["monotonic_s"] - 120.
            while recent and recent[0]["monotonic_s"] < cutoff:
                recent.pop(0)
    if latest_state is None or latest_periodic is None:
        raise ValueError("live native policy observations are missing")
    remaining = measured - ended
    now = time.monotonic() * 1000.
    summary = []
    for workflow in sorted(remaining):
        controls, rows = 0, []
        counts = Counter()
        for record in recent:
            matched = [
                item for item in record["programs"]
                if (item.get("key") or {}).get("root_workflow_id") == workflow
            ]
            controls += bool(matched)
            for item in matched:
                counts["observed_agent_rows"] += 1
                ready = item["application_ready"] and item["pending_before"]
                counts["application_ready_agent_rows"] += ready
                counts["application_ready_paused_rows"] += ready and not item["active_before"]
                counts["application_ready_paused_individually_fitting_rows"] += bool(
                    ready and not item["active_before"]
                    and item["complete_budget_tokens"] <= record["resume_capacity_tokens"]
                )
                counts["resumed_rows"] += item["invocation_id"] in record["resumed_program_ids"]
                counts["active_after_pressure_rows"] += item["active_after_pressure"]
            if record is latest_periodic:
                rows = matched
        requests = [
            {
                "invocation_id": invocation, "request_id": row["attributes"]["request_id"],
                "submitted_event_monotonic_ms": row["ts_ms"],
                "complete_receipt_seen_in_independent_prefix": row["attributes"]["request_id"] in receipt_ids,
            } for invocation, row in pending.items() if row["workflow_id"] == workflow
        ]
        summary.append({
            "workflow_id": workflow, "completed_requests": completed[workflow],
            "required_requests": required[workflow], "completed_output_tokens": outputs[workflow],
            "seconds_since_last_complete_request": (
                (now - last[workflow]) / 1000. if workflow in last else None),
            "pending_model_events": requests,
            "blocked_join_parent_invocations": [
                invocation for (owner, _join), invocation in joins.items() if owner == workflow
            ],
            "last_120s_periodic_counts_not_time_weighted": {
                "controls_with_workflow_observed": controls, **counts,
            },
            "last_periodic_program_rows": rows,
        })
    for filename, prefix in reader.prefixes.items():
        if prefix_digest(Path(filename), prefix["complete_prefix_bytes"]) != prefix["complete_prefix_sha256"]:
            raise ValueError("live evidence prefix changed after being sealed")
    output = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(REGISTRATION), "measured_completed_in_event_prefix": len(measured & ended),
        "remaining": summary, "registered_process_identities": identities,
        "actual_processes_alive_at_entry": alive,
        "actual_processes_alive_at_exit": {
            name: causal.reservation.alive(row) for name, row in identities.items()
        },
        "last_periodic_monotonic_s": latest_periodic["monotonic_s"],
        "last_periodic_resume_capacity_tokens": latest_periodic["resume_capacity_tokens"],
        "latest_runtime_snapshot_ts_ms": latest_state["ts_ms"],
        "latest_native_headroom_observation": latest_state.get("prefetch_residency_headroom"),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (REGISTRATION, COHORT, Path(__file__))},
        "scope": [
            "Live independently sealed prefixes, not an atomic global snapshot or complete comparison.",
            "A long interval without completion does not prove deadlock or continuous readiness.",
            "Periodic logical fit does not prove physical HBM fit or immediate service.",
            "Counts over the latest120 seconds are repeated observations, not time-weighted waiting.",
            "No workload, timing, policy, KV allocation or running process is changed.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    write(OUTPUT, output)
    print(json.dumps({
        "measured_completed": output["measured_completed_in_event_prefix"],
        "remaining": [{
            key: row[key] for key in (
                "workflow_id", "completed_requests", "required_requests",
                "seconds_since_last_complete_request", "last_120s_periodic_counts_not_time_weighted",
            )
        } for row in summary],
        "actual_processes_alive_at_exit": output["actual_processes_alive_at_exit"],
    }), flush=True)


if __name__ == "__main__":
    main()
