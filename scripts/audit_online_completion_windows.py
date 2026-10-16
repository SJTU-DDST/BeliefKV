#!/usr/bin/env python3
"""Compare all due workflows at fixed, fully observed collection-time horizons."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.arrival_schedule import arrival_schedule_digest
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.compare_native_policy_runs import native_reference_metadata


def arrival_states(schedule, events):
    expected, previous = {}, -math.inf
    for row in schedule:
        instance, when = row["instance_id"], row["offset_seconds"]
        if (
            not instance or instance in expected
            or not finite_number(when) or when < 0 or when < previous
        ):
            raise ValueError("invalid or duplicate scheduled arrival")
        expected[instance] = {"scheduled_seconds": when}
        previous = when
    observed = []
    names = {
        "workflow_submitted": "submitted_seconds",
        "workflow_started": "started_seconds",
        "workflow_finished": "finished_seconds",
    }
    for event in events:
        if event.get("event") not in names:
            continue
        instance = event.get("instance_id")
        if instance not in expected:
            raise ValueError(f"arrival event outside the planned cohort: {instance!r}")
        when = event.get("actual_offset_seconds")
        if not finite_number(when) or when < 0:
            raise ValueError("invalid actual arrival/finish offset")
        state, field = expected[instance], names[event["event"]]
        if field in state:
            raise ValueError(f"duplicate arrival lifecycle event: {instance} {field}")
        state[field] = when
        observed.append(when)
        if field != "finished_seconds":
            planned = event.get("scheduled_offset_seconds")
            if planned != state["scheduled_seconds"]:
                raise ValueError(f"scheduled arrival differs from manifest: {instance}")
    for instance, state in expected.items():
        if "finished_seconds" in state and "started_seconds" not in state:
            raise ValueError(f"finish has no observed start: {instance}")
        if "started_seconds" in state and "submitted_seconds" not in state:
            raise ValueError(f"start has no observed submission: {instance}")
        ordering = [
            state[field] for field in (
                "scheduled_seconds", "submitted_seconds", "started_seconds", "finished_seconds",
            ) if field in state
        ]
        if any(right + 1e-6 < left for left, right in zip(ordering, ordering[1:])):
            raise ValueError(f"unordered arrival lifecycle: {instance}")
    return expected, max(observed, default=None)


def snapshot(states, results, horizon, *, observed_through):
    if not finite_number(horizon) or horizon <= 0:
        raise ValueError("horizon must be finite and positive")
    if observed_through is None or observed_through < horizon:
        raise ValueError("arrival/finish log has not covered the requested horizon")
    counts, outcomes, eligibility, quality = Counter(), Counter(), Counter(), Counter()
    totals = Counter()
    rows, jitters, dispatch_waits = [], [], []
    for instance, state in states.items():
        scheduled = state["scheduled_seconds"]
        if scheduled > horizon:
            continue
        counts["due_workflows"] += 1
        submitted, started, finish = (
            state.get(name) for name in ("submitted_seconds", "started_seconds", "finished_seconds")
        )
        submitted_by_horizon = submitted is not None and submitted <= horizon
        started_by_horizon = started is not None and started <= horizon
        finished = finish is not None and finish <= horizon
        counts["submitted"] += submitted_by_horizon
        counts["started"] += started_by_horizon
        counts["runner_finished"] += finished
        counts["due_submission_unobserved"] += not submitted_by_horizon
        counts["runner_unfinished"] += not finished
        result = results.get(instance) if finished else None
        outcome = result.get("outcome") if result else None
        completed = outcome == "completed"
        eligible = completed and result.get("native_agent_jct_eligible") is True
        counts["recorded_completed"] += completed
        counts["explicitly_jct_eligible_completed"] += eligible
        if finished:
            outcomes[outcome or "result_missing"] += 1
            eligibility[str(result.get("native_agent_jct_eligible")) if result else "unknown"] += 1
            quality[str(result.get("task_correctness_valid")) if result else "unknown"] += 1
        age = horizon - scheduled
        elapsed = finish - scheduled if finished else age
        durations = {
            "runner_finish_capped_seconds": elapsed,
            "recorded_completed_capped_seconds": elapsed if completed else age,
            "eligible_completed_capped_seconds": elapsed if eligible else age,
        }
        totals.update(durations)
        if submitted_by_horizon:
            jitters.append(submitted - scheduled)
        if started_by_horizon:
            dispatch_waits.append(started - submitted)
        rows.append({
            "instance_id": instance,
            "scheduled_arrival_seconds": scheduled,
            "actual_submitted_seconds": submitted if submitted_by_horizon else None,
            "actual_started_seconds": started if started_by_horizon else None,
            "observed_runner_finish_seconds": finish if finished else None,
            "recorded_outcome_by_horizon": outcome,
            "explicit_native_jct_eligibility": (
                result.get("native_agent_jct_eligible") if result else None
            ),
            "task_correctness_valid": result.get("task_correctness_valid") if result else None,
            **durations,
        })
    size = counts["due_workflows"]
    return {
        "horizon_seconds": horizon,
        "counts": dict(counts),
        "finished_outcomes": dict(outcomes),
        "finished_native_jct_eligibility": dict(eligibility),
        "finished_task_correctness_valid": dict(quality),
        "capped_sojourn_sum_seconds": dict(totals),
        "capped_sojourn_mean_seconds": {
            name: totals[name] / size if size else None
            for name in (
                "runner_finish_capped_seconds", "recorded_completed_capped_seconds",
                "eligible_completed_capped_seconds",
            )
        },
        "recorded_completions_per_collection_hour": counts["recorded_completed"] * 3600 / horizon,
        "submission_jitter_seconds": distribution(jitters),
        "submission_to_worker_start_seconds": distribution(dispatch_waits),
        "rows": rows,
    }


def read_json(path, bindings):
    data = path.read_bytes()
    bindings[str(path)] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return json.loads(data)


def audit_arm(arm, horizons):
    clients = [path for path in arm.glob("client_*") if path.is_dir()]
    if len(clients) != 1:
        raise ValueError(f"expected one client in {arm}")
    client = clients[0]
    bindings = {}
    manifest = read_json(client / "manifest.json", bindings)
    schedule = manifest["arrival_schedule"]
    digest = arrival_schedule_digest(schedule)
    if (
        manifest.get("arrival_schedule_sha256") is not None
        and manifest["arrival_schedule_sha256"] != digest
    ):
        raise ValueError("manifest arrival schedule digest differs")
    sealed = SealedRecords()
    arrival_path = client / "workflow_arrivals.jsonl"
    if not arrival_path.is_file():
        raise FileNotFoundError(arrival_path)
    states, covered = arrival_states(schedule, sealed(arrival_path))
    results = {}
    missing = []
    for instance, state in states.items():
        if state.get("finished_seconds", math.inf) > max(horizons):
            continue
        path = client / "workflows" / instance / "result.json"
        if not path.is_file():
            missing.append(instance)
            continue
        result = read_json(path, bindings)
        if result.get("instance_id") != instance:
            raise ValueError(f"result identity differs: {instance}")
        results[instance] = result
    return {
        "arm": str(arm), "arrival_schedule_sha256": digest,
        "planned_workflows": len(schedule),
        "arrival_or_finish_observed_through_seconds": covered,
        "missing_finished_results_in_requested_horizons": missing,
        "source_json_bindings": bindings, "input_jsonl_prefixes": sealed.prefixes,
        "snapshots": [snapshot(states, results, horizon, observed_through=covered) for horizon in horizons],
    }


def comparisons(arms):
    result = []
    for left, right in combinations(arms, 2):
        before, after = arms[left], arms[right]
        if before["arrival_schedule_sha256"] != after["arrival_schedule_sha256"]:
            raise ValueError(f"arrival schedules differ: {left} vs {right}")
        for a, b in zip(before["snapshots"], after["snapshots"], strict=True):
            if a["horizon_seconds"] != b["horizon_seconds"]:
                raise ValueError("comparison horizons differ")
            aa, bb = {row["instance_id"] for row in a["rows"]}, {row["instance_id"] for row in b["rows"]}
            if aa != bb:
                raise ValueError("due task cohorts differ")
            result.append({
                "left": left, "right": right, "horizon_seconds": a["horizon_seconds"],
                "due_workflows": len(aa),
                "left_minus_right_recorded_completions": (
                    a["counts"].get("recorded_completed", 0) - b["counts"].get("recorded_completed", 0)
                ),
                "left_minus_right_capped_mean_seconds": {
                    key: (
                        a["capped_sojourn_mean_seconds"][key] - b["capped_sojourn_mean_seconds"][key]
                        if aa else None
                    ) for key in a["capped_sojourn_mean_seconds"]
                },
            })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_DIRECTORY")
    parser.add_argument("--horizon-seconds", type=float, action="append", required=True)
    parser.add_argument("--native-reference-name", default="native")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; use a fresh report ID")
    named = [value.split("=", 1) for value in args.arm]
    if any(len(value) != 2 for value in named) or len({value[0] for value in named}) != len(named):
        parser.error("--arm requires distinct NAME=ARM_DIRECTORY entries")
    paths = {name: Path(path).resolve() for name, path in named}
    horizons = sorted(set(args.horizon_seconds))
    arms = {name: audit_arm(path, horizons) for name, path in paths.items()}
    reference = paths.get(args.native_reference_name)
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "arms": arms, "comparisons": comparisons(arms),
        "native_reference_metadata": {
            name: native_reference_metadata(path.parent, reference.parent)
            for name, path in paths.items() if reference is not None and name != args.native_reference_name
        },
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py",
                ROOT / "scripts/compare_native_policy_runs.py",
                ROOT / "beliefkv/experiments/arrival_schedule.py",
            )
        },
        "semantics": [
            "The fixed cohort contains every planned workflow due by the horizon, including missing submissions, unfinished runs, failures and unknown results.",
            "Offsets are the runner's recorded collection-relative monotonic values; no server/client wall-clock conversion is used.",
            "Endpoint is workflow_finished after run_one returns, including setup and artifact collection; it differs from result.duration_seconds.",
            "Runner-finish capped sojourn is min(runner_finish, horizon) minus planned arrival; incomplete/error endpoints are explicitly retained.",
            "Recorded-completed capped sojourn uses a completed endpoint only; failed, incomplete, unknown and unfinished workflows accrue age until the horizon.",
            "Eligible-completed additionally requires explicit native_agent_jct_eligible=true. No global eligibility filter removes tasks from the denominator.",
            "Means divide capped sums by all due tasks. Late arrivals have less follow-up; these are finite-horizon sojourn areas, not Kaplan-Meier RMST or eventual JCT means.",
            "Completed outcome, JCT eligibility and task correctness are different. Ungraded outcomes cannot establish quality-matched completion or useful throughput.",
            "Arrival tables match; realized model/tool work can differ. Historical references and single development runs do not establish causal speedup.",
            "JSONL uses complete stat-bounded prefixes. Results are read only for runner-finished tasks within the requested horizons; missing metadata stays unknown.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {
            name: [{
                key: snapshot[key] for key in (
                    "horizon_seconds", "counts", "capped_sojourn_mean_seconds",
                    "finished_outcomes", "finished_native_jct_eligibility",
                )
            } for snapshot in arm["snapshots"]]
            for name, arm in arms.items()
        },
        "comparisons": report["comparisons"],
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
