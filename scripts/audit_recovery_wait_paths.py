"""Associate measured critical-path waiting with native recovery and JOIN handoffs."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_agent_admission_wait import partition
from scripts.audit_h2d_agent_origins import union_duration
from scripts.audit_join_enqueue_handoff import analyze as enqueue_handoffs
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_staged_native_restore import inspect as staged_inspect, transfer_key
from scripts.train_workflow_work import write


def pending_phases(begin, end, episodes, ready):
    staged = [
        row for row in episodes
        if begin <= row["staged_monotonic_ms"] <= end
    ]
    if not staged:
        return {"staging_before_first_service": False}
    intervals, completion_times, unknown = [], [], 0
    for row in staged:
        key = row["native_submit_ts_ms"], tuple(row["native_node_ids"]), row["native_h2d_bytes"]
        done = ready.get(key)
        if done is None:
            unknown += 1
            continue
        if done["monotonic_clock_domain"] != row["monotonic_clock_domain"]:
            raise ValueError("staging/ACK observation clocks differ")
        start, finish = row["staged_monotonic_ms"], done["monotonic_ms"]
        if finish < start:
            raise ValueError("ACK observation precedes its staging")
        intervals.append((start, min(end, finish)))
        completion_times.append(finish)
    first = min(row["staged_monotonic_ms"] for row in staged)
    last = max(completion_times) if completion_times and not unknown else None
    return {
        "staging_before_first_service": True, "staging_episodes": len(staged),
        "submit_to_first_staging_ms": first - begin,
        "first_staging_to_service_ms": end - first,
        "staging_to_observed_ACK_union_ms": union_duration(intervals),
        "staging_episodes_without_ready_observation": unknown,
        "all_staged_ACKs_observed_before_service": last is not None and last <= end,
        "last_observed_staged_ACK_to_service_ms": (
            end - last if last is not None and last <= end else None
        ),
    }


def summarize(rows, workflows):
    sums = Counter()
    for row in rows:
        sums["wait_s"] += row["submit_to_first_launch_s"]
        for field in ("active_ms", "inactive_ms", "unknown_ms"):
            sums[field] += row["eligibility"][field]
        for field in (
            "submit_to_first_staging_ms", "first_staging_to_service_ms",
            "staging_to_observed_ACK_union_ms", "last_observed_staged_ACK_to_service_ms",
        ):
            value = row["staged_phases"].get(field)
            if value is not None:
                sums[field] += value
    return {
        "critical_path_requests": len(rows),
        "wait_sum_s": sums["wait_s"], "wait_mean_per_workflow_s": sums["wait_s"] / workflows,
        "eligibility_mean_per_workflow_s": {
            field.removesuffix("_ms"): sums[field] / 1000 / workflows
            for field in ("active_ms", "inactive_ms", "unknown_ms")
        },
        "staged_phase_mean_per_workflow_s": {
            field.removesuffix("_ms"): sums[field] / 1000 / workflows for field in (
                "submit_to_first_staging_ms", "first_staging_to_service_ms",
                "staging_to_observed_ACK_union_ms", "last_observed_staged_ACK_to_service_ms",
            )
        },
        "requests_with_staging_before_service": sum(
            row["staged_phases"]["staging_before_first_service"] for row in rows
        ),
        "requests_with_all_staged_ACKs_observed_before_service": sum(
            row["staged_phases"].get("all_staged_ACKs_observed_before_service", False)
            for row in rows
        ),
    }


def audit(registration, paths_report, origins_report):
    reg = json.loads(registration.read_bytes())
    paths = json.loads(paths_report.read_bytes())["result"]
    origins = json.loads(origins_report.read_bytes())
    arm = Path(reg["run_root"]) / "full"
    if (
        Path(paths["arm"]).resolve() != arm.resolve()
        or Path(origins["registration"]).resolve() != registration.resolve()
    ):
        raise ValueError("recovery reports belong to different experiments")
    reader = SealedRecords()
    states, journals, ready = [], [], {}
    relevant = {
        "native_demand_restore_staged", "native_demand_restore_ready",
        "confirmed_join_client_bound", "confirmed_join_ticket_closed",
        "prefetch_native_issued", "wait_prefetch_plan",
        "agent_join_physical_plan_result", "agent_join_blocked_capacity_admitted",
        "agent_join_free_capacity_admitted",
    }
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "admission_runtime_state":
            states.append({"ts_ms": row["monotonic_ms"], "agent_admission": row["agent_admission"]})
        elif row.get("event") in relevant:
            journals.append(row)
            if row["event"] == "native_demand_restore_ready":
                key = transfer_key({
                    "submit_ts_ms": row["native_submit_ts_ms"],
                    "node_ids": row["native_node_ids"], "actual_bytes": row["native_bytes"],
                })
                if key in ready:
                    raise ValueError("duplicate staged ACK observation")
                ready[key] = row
    transfers = list(reader(arm / "server/transfer_telemetry.jsonl"))
    stage = staged_inspect(
        journals, transfers, bytes_per_token=origins["bytes_per_full_token"],
    )
    by_request = defaultdict(list)
    for row in stage["episodes"]:
        for member in row["requests"]:
            by_request[member["request_id"]].append(row)
    receipts = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
        if row["request_id"] in paths["requests"]
    }
    if receipts.keys() != paths["requests"].keys():
        raise ValueError("measured receipts changed")
    direct = {row["request_id"] for row in origins["direct_host_hit_records"]}
    rows, groups = [], defaultdict(list)
    for workflow in paths["workflows"].values():
        for request in workflow["critical_path_requests"]:
            detail, receipt = paths["requests"][request], receipts[request]
            begin, end = (
                receipt["submitted_monotonic_ms"],
                receipt["submitted_monotonic_ms"] + 1000 * detail["submit_to_first_launch_s"],
            )
            phases = pending_phases(begin, end, by_request[request], ready)
            restoration = (
                "staged_and_direct" if phases["staging_before_first_service"] and request in direct else
                "staged" if phases["staging_before_first_service"] else
                "direct_counter_only" if request in direct else "no_initial_demand_evidence"
            )
            row = {
                "request_id": request, **detail,
                "demand_association": restoration,
                "staged_phases": phases,
                "eligibility": partition(begin, end, detail["invocation_id"], states),
            }
            rows.append(row)
            groups[detail["cohort"]].append(row)
    workflows = len(paths["workflows"])
    if not math.isclose(
        sum(row["submit_to_first_launch_s"] for row in rows) / workflows,
        paths["mean_components_s"]["request_submit_to_first_launch"], abs_tol=1e-6,
    ):
        raise ValueError("recovery request waits do not reconstruct the measured critical paths")
    acks = list(reader(arm / "server/physical_action_ack.jsonl"))
    attempts = [row for row in journals if row["event"] == "agent_join_physical_plan_result"]
    grants = [row for row in journals if row["event"] == "agent_join_blocked_capacity_admitted"]
    closed = [row for row in journals if row["event"] == "confirmed_join_ticket_closed"]
    outcomes = Counter((row["dispatch_stage"], row["outcome"]) for row in attempts)
    donors = Counter()
    for row in grants:
        donors["budget_grants"] += 1
        donors["donor_associations"] += len(row.get("donors", ()))
    for path, sealed in reader.prefixes.items():
        previous = origins["input_jsonl_prefixes"].get(path)
        if previous is not None and sealed != previous:
            raise ValueError("H2D attribution input changed")
        previous = paths["input_jsonl_prefixes"].get(path)
        if previous is not None and sealed != previous:
            raise ValueError("critical-path input changed")
    return {
        "observed_at_local": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "mean_jct_s": paths["mean_jct_s"], "measured_workflows": workflows,
        "critical_path_waits_by_cohort": {
            name: summarize(members, workflows) for name, members in groups.items()
        },
        "tool_waits_by_demand_evidence": {
            name: summarize(
                [row for row in groups["post_tool"] if row["demand_association"] == name], workflows,
            ) for name in (
                "staged", "staged_and_direct", "direct_counter_only", "no_initial_demand_evidence",
            )
        },
        "requests": rows,
        "JOIN_physical_attempts": {
            stage: {outcome: count for (kind, outcome), count in outcomes.items() if kind == stage}
            for stage in sorted({kind for kind, _outcome in outcomes})
        },
        "JOIN_budget_grant_counts": dict(donors),
        "JOIN_ticket_closures": {
            "by_reason": dict(Counter(row["reason"] for row in closed)),
            "without_issued_nodes_by_reason": dict(Counter(
                row["reason"] for row in closed if not row["issued_nodes"]
            )),
        },
        "JOIN_enqueue_handoffs": enqueue_handoffs(journals, acks),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (registration, paths_report, origins_report, Path(__file__))
        },
        "scope": [
            "Fixed18 complete last-finishing dependency paths; background supplies real contention.",
            "Request waiting includes all admission, transport, native selection and recovery.",
            "Demand labels associate requests with restoration; they do not isolate DMA stalls.",
            "Staging-to-ready intervals include copy overlap, polling and scheduling; not pure DMA.",
            "ACK-to-service intervals concern staged copies only; direct restoration may still occur.",
            "State samples describe logical membership with at most1500ms age, not physical residency.",
            "JOIN attempt and closure counts are repeated controller observations, not unique opportunities.",
            "No counterfactual schedule or removable-JCT estimate is asserted.",
        ],
        "GPU_action": False, "tool_prefetch_implemented": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--paths", type=Path, required=True)
    parser.add_argument("--origins", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve earlier evidence and hide CUDA")
    result = audit(args.registration, args.paths, args.origins)
    write(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key in (
        "mean_jct_s", "critical_path_waits_by_cohort", "tool_waits_by_demand_evidence",
        "JOIN_physical_attempts", "JOIN_ticket_closures",
    )}, indent=2))


if __name__ == "__main__":
    main()
