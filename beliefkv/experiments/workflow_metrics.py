"""Streaming response and root-balanced workflow measurements for causal replay."""

from bisect import bisect_left
from collections import defaultdict
import math
from statistics import fmean

from beliefkv.core.events import RuntimeEventKind as Kind


GAP_BUCKETS_MS = (
    1., 2., 5., 10., 20., 50., 100., 200., 500., 1000., 2000., 5000.,
    10_000., 30_000., 60_000., 300_000.,
)


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p75": None, "p90": None, "p95": None, "p99": None, "max": None}

    def percentile(fraction):
        position = (len(values) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return {
        "count": len(values), "mean": fmean(values),
        "p50": percentile(.5), "p75": percentile(.75), "p90": percentile(.9), "p95": percentile(.95),
        "p99": percentile(.99), "max": values[-1],
    }


class _ReadyGaps:
    def __init__(self):
        self.count = 0
        self.total = 0.
        self.maximum = 0.
        self.buckets = [0] * (len(GAP_BUCKETS_MS) + 1)

    def observe(self, duration):
        self.count += 1
        self.total += duration
        self.maximum = max(self.maximum, duration)
        self.buckets[bisect_left(GAP_BUCKETS_MS, duration)] += 1

    def summary(self):
        return {
            "count": self.count, "mean": self.total / self.count if self.count else None,
            "max": self.maximum if self.count else None,
            "bucket_upper_bounds_ms": [*GAP_BUCKETS_MS, None],
            "bucket_counts": self.buckets,
        }


class StreamingWorkflowMetrics:
    """Maintain bounded service-gap statistics, without a per-token output log.

    Pending work begins at visible LLM_SUBMIT, before any local admission, token
    loading or backend wait. All descendants share one workflow account. When
    no model request remains pending, the next service gap starts at reentry.
    """

    def __init__(self):
        self.started = {}
        self.ended = {}
        self.requests = {}
        self.ready = defaultdict(set)
        self.anchor = {}
        self.gaps = defaultdict(_ReadyGaps)
        self.post_first_gaps = defaultdict(_ReadyGaps)
        self.first_output = {}

    def event(self, event):
        workflow, now = event.workflow_id, event.ts_ms
        if event.kind is Kind.WORKFLOW_START:
            if workflow in self.started:
                raise ValueError("workflow metrics saw duplicate arrival")
            self.started[workflow] = now
        elif event.kind is Kind.LLM_SUBMIT:
            self.submit(event.attributes["request_id"], workflow, now)
        elif event.kind is Kind.WORKFLOW_END:
            if workflow not in self.started or self.ready[workflow] or workflow in self.ended:
                raise ValueError("workflow metrics saw an incomplete or duplicate end")
            self.ended[workflow] = now

    def submit(self, request, workflow, now):
        if workflow not in self.started or workflow in self.ended or request in self.requests:
            raise ValueError("streaming request lacks a unique live workflow")
        self.requests[request] = {
            "workflow_id": workflow, "submitted_monotonic_ms": now,
            "output_tokens": 0, "output_chunks": 0, "coalesced_output_chunks": 0,
            "first_chunk_tokens": None,
            "first_output_monotonic_ms": None, "last_output_monotonic_ms": None,
            "completed_monotonic_ms": None, "max_output_gap_ms": None,
        }
        if not self.ready[workflow]:
            self.anchor[workflow] = now
        self.ready[workflow].add(request)

    def progress(self, request, tokens, now):
        row = self.requests[request]
        previous = row["output_tokens"]
        if (
            type(tokens) is not int or tokens < previous or row["completed_monotonic_ms"] is not None
            or not math.isfinite(now) or now < row["submitted_monotonic_ms"]
            or row["last_output_monotonic_ms"] is not None and now < row["last_output_monotonic_ms"]
        ):
            raise ValueError("streaming progress changed token count or clock")
        if tokens == previous:
            return
        workflow = row["workflow_id"]
        gap = now - self.anchor[workflow]
        self.gaps[workflow].observe(gap)
        if workflow in self.first_output:
            self.post_first_gaps[workflow].observe(gap)
        self.anchor[workflow] = now
        self.first_output.setdefault(workflow, now)
        if row["first_output_monotonic_ms"] is None:
            row["first_output_monotonic_ms"] = now
            row["first_chunk_tokens"] = tokens
        elif row["last_output_monotonic_ms"] is not None:
            row["max_output_gap_ms"] = max(
                row["max_output_gap_ms"] or 0., now - row["last_output_monotonic_ms"],
            )
        row["last_output_monotonic_ms"] = now
        row["output_tokens"] = tokens
        row["output_chunks"] += 1
        row["coalesced_output_chunks"] += tokens - previous > 1

    def complete(self, request, tokens, now):
        row = self.requests[request]
        if (
            row["completed_monotonic_ms"] is not None or tokens != row["output_tokens"]
            or now < (row["last_output_monotonic_ms"] or row["submitted_monotonic_ms"])
        ):
            raise ValueError("streaming completion changed observed request work")
        row["completed_monotonic_ms"] = now
        self.ready[row["workflow_id"]].remove(request)

    def request_summary(self, request):
        row = dict(self.requests[request])
        first, last = row["first_output_monotonic_ms"], row["last_output_monotonic_ms"]
        row["ttft_ms"] = first - row["submitted_monotonic_ms"] if first is not None else None
        row["interactivity_tps"] = (
            1000. * (row["output_tokens"] - 1) / (last - first)
            if row["output_tokens"] > 1 and first is not None and last > first else None
        )
        row["chunk_corrected_interactivity_tps"] = (
            1000. * (row["output_tokens"] - row["first_chunk_tokens"]) / (last - first)
            if row["interactivity_tps"] is not None else None
        )
        duration = (
            row["completed_monotonic_ms"] - row["submitted_monotonic_ms"]
            if row["completed_monotonic_ms"] is not None else None
        )
        row["e2e_interactivity_tps"] = (
            1000. * row["output_tokens"] / duration
            if row["output_tokens"] > 0 and duration is not None and duration > 0 else None
        )
        return row

    def summary(self):
        request_rows = [self.request_summary(request) for request in self.requests]
        e2e_ratios = distribution(
            1. / row["e2e_interactivity_tps"] for row in request_rows
            if row["e2e_interactivity_tps"] is not None and row["ttft_ms"] is not None
            and row["ttft_ms"] > 0
        )
        workflows = {}
        for workflow, start in sorted(self.started.items()):
            requests = [row for row in request_rows if row["workflow_id"] == workflow]
            rates = [row["interactivity_tps"] for row in requests if row["interactivity_tps"] is not None]
            waits = [row["ttft_ms"] for row in requests if row["ttft_ms"] is not None]
            gaps = [row["max_output_gap_ms"] for row in requests if row["max_output_gap_ms"] is not None]
            workflows[workflow] = {
                "jct_ms": self.ended[workflow] - start if workflow in self.ended else None,
                "ttft_ms": self.first_output[workflow] - start if workflow in self.first_output else None,
                "request_count": len(requests),
                "completed_request_count": sum(row["completed_monotonic_ms"] is not None for row in requests),
                "requests_with_output_timing": len(waits),
                "output_tokens": sum(row["output_tokens"] for row in requests),
                "max_request_ttft_ms": max(waits) if waits else None,
                "max_request_output_gap_ms": max(gaps) if gaps else None,
                "mean_request_interactivity_tps": fmean(rates) if rates else None,
                "ready_output_service_gaps_ms": self.gaps[workflow].summary(),
                "post_first_ready_output_service_gaps_ms": self.post_first_gaps[workflow].summary(),
            }
        complete = self.started.keys() == self.ended.keys() and bool(self.started)
        makespan = max(self.ended.values()) - min(self.started.values()) if complete else None
        return {
            "definitions": {
                "workflow_ttft": (
                    "first nonempty streaming output from any descendant minus WORKFLOW_START; "
                    "internal model service, not necessarily a user-visible root response"
                ),
                "request_interactivity": "(output_tokens - 1) / (last_output_s - first_output_s)",
                "e2e_interactivity": "output_tokens / (http_completion_s - visible_submit_s)",
                "e2e_normalized_interactivity": (
                    "1 / percentile(e2e_seconds_per_output_token, p); "
                    "p90 names the slow tail, not the fast p90 of token rates"
                ),
                "ready_service_gap": (
                    "client-observed gaps to output while at least one descendant "
                    "has submitted incomplete model work; includes initial ready wait, admission and KV waits"
                ),
                "post_first_ready_service_gap": (
                    "ready output gaps after the workflow's first output; "
                    "excludes initial TTFT and resets at reentry after all-branch external blocking"
                ),
                "service_gap_resolution": "streaming chunks; coalesced tokens are disclosed",
                "per_agent_starvation": (
                    "workflow aggregate output can hide an individual stalled agent; "
                    "also report maximum request TTFT and request output gap"
                ),
                "workflow_weight": "one root per workflow aggregate, independent of fanout",
                "normal_tails_removed": False,
            },
            "all_workflows_completed": complete,
            "workflow_event_makespan_ms": makespan,
            "output_tokens": sum(row["output_tokens"] for row in request_rows),
            "workflows": workflows,
            "workflow_jct_ms": distribution(
                row["jct_ms"] for row in workflows.values() if row["jct_ms"] is not None
            ),
            "workflow_ttft_ms": distribution(
                row["ttft_ms"] for row in workflows.values() if row["ttft_ms"] is not None
            ),
            "request_interactivity_tps": distribution(
                row["interactivity_tps"] for row in request_rows if row["interactivity_tps"] is not None
            ),
            "request_e2e_interactivity_tps": distribution(
                row["e2e_interactivity_tps"] for row in request_rows
                if row["e2e_interactivity_tps"] is not None
            ),
            "e2e_normalized_interactivity_p90_tps": (
                1. / e2e_ratios["p90"] if e2e_ratios["count"] else None
            ),
            "e2e_normalized_interactivity_p75_tps": (
                1. / e2e_ratios["p75"] if e2e_ratios["count"] else None
            ),
            "equal_workflow_mean_interactivity_tps": distribution(
                row["mean_request_interactivity_tps"] for row in workflows.values()
                if row["mean_request_interactivity_tps"] is not None
            ),
            "workflow_max_ready_output_gap_ms": distribution(
                row["ready_output_service_gaps_ms"]["max"] for row in workflows.values()
                if row["ready_output_service_gaps_ms"]["max"] is not None
            ),
            "workflow_max_post_first_ready_output_gap_ms": distribution(
                row["post_first_ready_output_service_gaps_ms"]["max"] for row in workflows.values()
                if row["post_first_ready_output_service_gaps_ms"]["max"] is not None
            ),
            "coverage": {
                "workflows": len(workflows),
                "completed_workflows": len(self.ended),
                "workflows_with_output_timing": len(self.first_output),
                "requests": len(request_rows),
                "completed_requests": sum(row["completed_monotonic_ms"] is not None for row in request_rows),
                "requests_with_output_timing": sum(row["first_output_monotonic_ms"] is not None for row in request_rows),
                "undefined_interactivity_requests": sum(row["interactivity_tps"] is None for row in request_rows),
                "coalesced_output_chunks": sum(row["coalesced_output_chunks"] for row in request_rows),
                "e2e_normalized_interactivity_requests": e2e_ratios["count"],
            },
        }


def evaluate_workflow_service_slo(summary, *, first_output_budget_ms, ready_gap_budget_ms):
    """Evaluate common offline budgets without changing scheduling or removing tails."""
    for budget in (first_output_budget_ms, ready_gap_budget_ms):
        if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
            raise ValueError("workflow service budgets must be finite and positive")
    workflows = summary["workflows"]
    rows = {}
    for identity, row in workflows.items():
        timing_complete = (
            row["jct_ms"] is not None and row["request_count"] > 0
            and row["completed_request_count"] == row["request_count"]
            and row["requests_with_output_timing"] == row["request_count"]
        )
        first = row["ttft_ms"]
        gaps = row["post_first_ready_output_service_gaps_ms"]
        first_met = first is not None and first <= first_output_budget_ms
        gap_met = (
            gaps["count"] == 0 or gaps["max"] is not None and gaps["max"] <= ready_gap_budget_ms
        )
        rows[identity] = {
            "complete_output_timing": timing_complete,
            "first_output_budget_met": first_met,
            "post_first_ready_gap_budget_met": gap_met,
            "attained": timing_complete and first_met and gap_met,
        }
    attained = sum(row["attained"] for row in rows.values())
    makespan = summary.get("workflow_event_makespan_ms")
    complete = summary["all_workflows_completed"] and makespan is not None and makespan > 0
    qualifying_tokens = sum(
        workflows[identity]["output_tokens"] for identity, row in rows.items() if row["attained"]
    )
    return {
        "first_output_budget_ms": first_output_budget_ms,
        "post_first_ready_gap_budget_ms": ready_gap_budget_ms,
        "workflow_count": len(rows), "attained_workflow_count": attained,
        "equal_workflow_attainment": attained / len(rows) if rows else None,
        "qualifying_workflows_per_second": 1000. * attained / makespan if complete else None,
        "qualifying_output_tokens_per_second": (
            1000. * qualifying_tokens / makespan if complete else None
        ),
        "all_workflows_completed": summary["all_workflows_completed"],
        "workflows": rows,
        "normal_tails_removed": False,
        "policy_modified": False,
        "scope": "offline common-budget service attainment; does not replace full-work JCT or throughput",
    }
