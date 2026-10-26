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
            "completed_monotonic_ms": None,
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
        self.gaps[workflow].observe(now - self.anchor[workflow])
        self.anchor[workflow] = now
        self.first_output.setdefault(workflow, now)
        if row["first_output_monotonic_ms"] is None:
            row["first_output_monotonic_ms"] = now
            row["first_chunk_tokens"] = tokens
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
            workflows[workflow] = {
                "jct_ms": self.ended[workflow] - start if workflow in self.ended else None,
                "ttft_ms": self.first_output[workflow] - start if workflow in self.first_output else None,
                "request_count": len(requests),
                "mean_request_interactivity_tps": fmean(rates) if rates else None,
                "ready_output_service_gaps_ms": self.gaps[workflow].summary(),
            }
        return {
            "definitions": {
                "workflow_ttft": "first nonempty streaming output minus WORKFLOW_START",
                "request_interactivity": "(output_tokens - 1) / (last_output_s - first_output_s)",
                "e2e_interactivity": "output_tokens / (http_completion_s - visible_submit_s)",
                "e2e_normalized_interactivity": (
                    "1 / percentile(e2e_seconds_per_output_token, p); "
                    "p90 names the slow tail, not the fast p90 of token rates"
                ),
                "ready_service_gap": (
                    "client-observed gaps to output while at least one descendant "
                    "has submitted incomplete model work; includes admission and KV waits"
                ),
                "service_gap_resolution": "streaming chunks; coalesced tokens are disclosed",
                "workflow_weight": "one root per workflow aggregate, independent of fanout",
                "normal_tails_removed": False,
            },
            "all_workflows_completed": self.started.keys() == self.ended.keys() and bool(self.started),
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
            "coverage": {
                "requests": len(request_rows),
                "completed_requests": sum(row["completed_monotonic_ms"] is not None for row in request_rows),
                "requests_with_output_timing": sum(row["first_output_monotonic_ms"] is not None for row in request_rows),
                "undefined_interactivity_requests": sum(row["interactivity_tps"] is None for row in request_rows),
                "coalesced_output_chunks": sum(row["coalesced_output_chunks"] for row in request_rows),
                "e2e_normalized_interactivity_requests": e2e_ratios["count"],
            },
        }
