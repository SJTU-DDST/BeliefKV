"""Sampled serving supply and logical competition; not a physical counterfactual."""

import math

from beliefkv.experiments.workflow_cohort import validate_cohort
from beliefkv.experiments.workflow_metrics import distribution


def inspect_supply(arrivals, cohort, events, states, requests, totals, *, bin_seconds=300.):
    validate_cohort(cohort, arrivals)
    if not math.isfinite(bin_seconds) or bin_seconds <= 0:
        raise ValueError("supply bins must be positive and finite")
    ids = {row["workflow_id"] for row in arrivals["arrival_schedule"]}
    if ids != totals.keys() or any(value <= 0 for value in totals.values()):
        raise ValueError("intrinsic work labels must cover the frozen population")
    events = sorted(events, key=lambda row: row["ts_ms"])
    states = sorted(states, key=lambda row: row["monotonic_ms"])
    if not events or not states:
        raise ValueError("supply audit requires observed replay events and agent snapshots")
    if any(
        not math.isfinite(row["monotonic_ms"])
        or row.get("physical_disabled") is not False
        for row in states
    ) or any(a["monotonic_ms"] >= b["monotonic_ms"] for a, b in zip(states, states[1:])):
        raise ValueError("require increasing healthy native state snapshots")
    starts, ends, offsets = {}, {}, {
        row["workflow_id"]: row["offset_seconds"] for row in arrivals["arrival_schedule"]
    }
    for event in events:
        workflow = event["workflow_id"]
        if workflow not in ids or not math.isfinite(event["ts_ms"]):
            raise ValueError("foreign workflow or invalid replay clock")
        if event["kind"] in ("workflow_start", "workflow_end"):
            mapping = starts if event["kind"] == "workflow_start" else ends
            if workflow in mapping:
                raise ValueError("duplicate workflow boundary")
            mapping[workflow] = event["ts_ms"]
    origins = [when - offsets[workflow] * 1000. for workflow, when in starts.items()]
    if not origins or max(origins) - min(origins) > 1e-5:
        raise ValueError("observed workflow starts differ from the frozen arrival table")
    origin = origins[0]
    remaining = dict(totals)
    pending, tools, joins, completed = {}, set(), {}, set()
    observed_starts, observed_ends, cursor = set(), set(), 0
    bins, snapshots = {}, []
    window_start, window_end = cohort["throughput_window_offset_seconds"]
    for state in states:
        when = state["monotonic_ms"]
        while cursor < len(events) and events[cursor]["ts_ms"] <= when:
            event = events[cursor]
            cursor += 1
            kind, workflow, invocation = (
                event["kind"], event["workflow_id"], event.get("invocation_id"),
            )
            attrs = event.get("attributes") or {}
            if kind == "workflow_start":
                observed_starts.add(workflow)
            elif kind == "workflow_end":
                observed_ends.add(workflow)
            elif kind in ("llm_submit", "llm_result"):
                request = attrs.get("request_id")
                item = requests.get(request)
                if item is None or (item["workflow_id"], item["invocation_id"]) != (workflow, invocation):
                    raise ValueError("model request changed its source owner")
                if kind == "llm_submit":
                    if invocation in pending or request in completed:
                        raise ValueError("source serial invocation has overlapping requests")
                    pending[invocation] = request
                else:
                    if pending.pop(invocation, None) != request or request in completed:
                        raise ValueError("source completion has no matching pending request")
                    completed.add(request)
                    remaining[workflow] -= item["input_increment"] + 100 * item["output_tokens"]
                    if remaining[workflow] < 0:
                        raise ValueError("observed work exceeds its conserved source total")
            elif kind in ("tool_start", "tool_end"):
                key = invocation, attrs.get("tool_run_id") or attrs.get("tool_call_id")
                if kind == "tool_start":
                    tools.add(key)
                else:
                    tools.discard(key)
            elif kind == "join_wait":
                joins[workflow, event.get("join_id")] = invocation
            elif kind in ("join_satisfied", "join_timeout"):
                joins.pop((workflow, event.get("join_id")), None)
        offset = (when - origin) / 1000.
        if not window_start <= offset < window_end:
            continue
        a = state.get("agent_admission") or {}
        if any(type(a.get(name)) is not list for name in (
            "active_invocation_ids", "paused_pending_invocation_ids", "marked_for_pause_invocation_ids",
        )):
            raise ValueError("native snapshot omits exact sampled agent eligibility")
        capacity, active_tokens = a.get("capacity_tokens"), a.get("logical_active_tokens")
        if any(type(value) is not int or value < 0 for value in (capacity, active_tokens)):
            raise ValueError("native snapshot omits logical capacity")
        free = max(0, capacity - active_tokens)
        blocked = {key[0] for key in tools} | set(joins.values())
        active, paused, marked = (
            set(a[name]) for name in (
                "active_invocation_ids", "paused_pending_invocation_ids", "marked_for_pause_invocation_ids",
            )
        )
        ready = {
            invocation: request for invocation, request in pending.items()
            if invocation not in blocked
        }
        candidates = [{
            "invocation_id": invocation, "request_id": request,
            "workflow_id": requests[request]["workflow_id"],
            "minimum_logical_tokens": requests[request]["prompt_tokens"] + 100,
            "remaining_completion_boundary_work_units": remaining[requests[request]["workflow_id"]],
        } for invocation, request in ready.items() if invocation in paused - marked]
        fitting = [row for row in candidates if row["minimum_logical_tokens"] <= free]
        fitting_workflows = {row["workflow_id"] for row in fitting}
        set_competition = (
            len(fitting_workflows) > 1
            and sum(row["minimum_logical_tokens"] for row in fitting) > free
        )
        work_values = [
            remaining[workflow] for workflow in fitting_workflows if remaining[workflow] > 0
        ]
        pair = []
        for index, left in enumerate(fitting):
            for right in fitting[index + 1:]:
                sizes = (
                    left["remaining_completion_boundary_work_units"],
                    right["remaining_completion_boundary_work_units"],
                )
                if (
                    left["workflow_id"] != right["workflow_id"] and min(sizes) > 0
                    and left["minimum_logical_tokens"] + right["minimum_logical_tokens"] > free
                ):
                    pair.append({
                        "request_ids": [left["request_id"], right["request_id"]],
                        "remaining_work_ratio": max(sizes) / min(sizes),
                    })
        row = {
            "offset_s": offset, "arrived": len(observed_starts),
            "completed": len(observed_ends), "inflight": len(observed_starts - observed_ends),
            "submitted_pending_agents": len(pending), "application_ready_agents": len(ready),
            "active_ready_agents": len(ready.keys() & active),
            "ready_paused_agents": len(candidates), "logical_free_tokens": free,
            "logical_active_fraction": active_tokens / capacity if capacity else None,
            "fitting_paused_agents_lower_bound": len(fitting),
            "cross_workflow_competing_pairs_lower_bound": len(pair),
            "cross_workflow_set_competition_lower_bound": set_competition,
            "maximum_set_competing_remaining_work_ratio": (
                max(work_values) / min(work_values) if set_competition and work_values else None
            ),
            "maximum_competing_remaining_work_ratio": max(
                (item["remaining_work_ratio"] for item in pair), default=None,
            ),
            "candidates": candidates, "competing_pairs": pair,
            "latest_control_counts": a.get("counts"),
        }
        snapshots.append(row)
        index = int((offset - window_start) // bin_seconds)
        bucket = bins.setdefault(index, [])
        bucket.append(row)
    summaries = []
    for index, rows in sorted(bins.items()):
        summaries.append({
            "start_s": window_start + index * bin_seconds,
            "end_s": min(window_end, window_start + (index + 1) * bin_seconds),
            "samples": len(rows), "first_inflight": rows[0]["inflight"],
            "last_inflight": rows[-1]["inflight"],
            "inflight": distribution(row["inflight"] for row in rows),
            "application_ready_agents": distribution(row["application_ready_agents"] for row in rows),
            "ready_paused_agents": distribution(row["ready_paused_agents"] for row in rows),
            "samples_with_free_budget_and_no_ready_paused": sum(
                row["logical_free_tokens"] > 100 and not row["ready_paused_agents"] for row in rows
            ),
            "samples_with_cross_workflow_pair_competition": sum(
                bool(row["competing_pairs"]) for row in rows
            ),
            "samples_with_cross_workflow_set_competition": sum(
                row["cross_workflow_set_competition_lower_bound"] for row in rows
            ),
            "samples_with_competing_remaining_work_ratio_ge_2": sum(
                (row["maximum_competing_remaining_work_ratio"] or 0) >= 2 for row in rows
            ),
        })
    measured = cohort["measured_workflow_ids"]
    submission_end = max(offsets.values())
    last_measured = max(
        ((ends[workflow] - origin) / 1000. for workflow in measured if workflow in ends),
        default=None,
    )
    return {
        "observed_arrivals": len(starts), "observed_completions": len(ends),
        "measured_completed": len(set(measured) & ends.keys()),
        "measured_population": len(measured), "all_workflows_completed": ids == ends.keys(),
        "observed_through_offset_s": (states[-1]["monotonic_ms"] - origin) / 1000.,
        "last_scheduled_arrival_offset_s": submission_end,
        "latest_observed_measured_completion_offset_s": last_measured,
        "all_measured_completed": set(measured) <= ends.keys(),
        "background_covers_all_measured_completions": (
            submission_end >= last_measured if set(measured) <= ends.keys() else None
        ),
        "bins": summaries,
        "competition_snapshots": [
            row for row in snapshots if row["cross_workflow_set_competition_lower_bound"]
        ],
        "sample_counts": {
            "common_window": len(snapshots),
            "with_ready_paused": sum(bool(row["ready_paused_agents"]) for row in snapshots),
            "with_cross_workflow_pair_competition": sum(bool(row["competing_pairs"]) for row in snapshots),
            "with_cross_workflow_set_competition": sum(
                row["cross_workflow_set_competition_lower_bound"] for row in snapshots
            ),
            "with_competing_remaining_work_ratio_ge_2": sum(
                (row["maximum_competing_remaining_work_ratio"] or 0) >= 2 for row in snapshots
            ),
        },
        "scope": {
            "sampled_state_not_exact_control_decisions": True,
            "budget": "undiscounted_snapshot; not stale periodic decay budget",
            "remaining_work": "whole_workflow_source_total_minus_completed_requests; partial_output_not_subtracted",
            "logical_fit": "prompt_plus_100_lower_bound; active_partial_output_growth_not_reconstructed",
            "physical_counterfactual_verified": False,
            "true_native_readiness_and_free_extent_checks_required": True,
            "client_submitted_work_may_precede_native_ingress": True,
            "independently_sealed_prefixes_not_atomic_snapshot": True,
            "steady_state_established": False, "performance_accepted": False,
        },
    }
