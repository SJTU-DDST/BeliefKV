"""Runtime idle guards for trace replay; never alter request work or barriers."""

from collections import Counter
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ReplayTimingPolicy:
    workflow_idle_cap_ms: float | None = None
    system_idle_cap_ms: float | None = None

    def __post_init__(self):
        for value in (self.workflow_idle_cap_ms, self.system_idle_cap_ms):
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            ):
                raise ValueError("replay idle caps must be finite positive milliseconds")


AGENTX_TIMING = ReplayTimingPolicy(300_000., 10_000.)


class ReplayIdleClock:
    """Shift only installed external timers while actual requests are idle.

    The workflow guard covers the root and every descendant, including requests
    queued at the backend. Global shifts preserve all pending relative spacing.
    Callers retain the dependency graph and keep control timers outside this map.
    """

    def __init__(self, policy, workflow_by_node, origin_ms):
        self.policy = policy
        self.workflow_by_node = workflow_by_node
        self.active = Counter()
        self.system_idle_since = origin_ms
        self.workflow_idle_since = dict.fromkeys(set(workflow_by_node.values()), origin_ms)
        self.shifts = []
        self.timer_idle_waits = []

    def observe_timer_idle_wait(self, start_ms, end_ms):
        """Record a sleep made with no request or control coroutine running."""
        if not math.isfinite(start_ms) or not math.isfinite(end_ms) or end_ms < start_ms:
            raise ValueError("invalid replay external-idle interval")
        self.timer_idle_waits.append({
            "start_monotonic_ms": start_ms, "end_monotonic_ms": end_ms,
            "duration_ms": end_ms - start_ms,
        })

    def request_started(self, workflow):
        self.active[workflow] += 1
        self.workflow_idle_since[workflow] = None
        self.system_idle_since = None

    def request_finished(self, workflow, now_ms):
        if self.active[workflow] <= 0:
            raise ValueError("replay completion has no outstanding request")
        self.active[workflow] -= 1
        if not self.active[workflow]:
            self.workflow_idle_since[workflow] = now_ms
        if not any(self.active.values()):
            self.system_idle_since = now_ms

    def _shift(self, deadlines, keys, amount, kind, now_ms, workflow=None):
        if amount <= 0:
            return
        for key in keys:
            deadlines[key] -= amount
        self.shifts.append({
            "kind": kind, "observed_monotonic_ms": now_ms,
            "workflow_id": workflow, "skipped_ms": amount,
            "pending_timers": len(keys),
        })

    def adjust(self, deadlines, busy_workflows, now_ms):
        if not deadlines:
            return
        groups = {}
        for key in deadlines:
            groups.setdefault(self.workflow_by_node[key], []).append(key)
        cap = self.policy.workflow_idle_cap_ms
        if cap is not None:
            for workflow, keys in groups.items():
                idle = self.workflow_idle_since[workflow]
                if (
                    idle is None or workflow in busy_workflows
                    or now_ms - idle < cap
                ):
                    continue
                self._shift(
                    deadlines, keys, min(deadlines[key] for key in keys) - now_ms,
                    "workflow_idle", now_ms, workflow,
                )
        cap = self.policy.system_idle_cap_ms
        idle = self.system_idle_since
        if cap is not None and idle is not None and not busy_workflows:
            budget = max(0., cap - (now_ms - idle))
            self._shift(
                deadlines, tuple(deadlines), min(deadlines.values()) - now_ms - budget,
                "system_idle", now_ms,
            )

    def next_wakeup_ms(self, deadlines, busy_workflows, now_ms):
        wake = min(deadlines.values())
        cap = self.policy.workflow_idle_cap_ms
        if cap is not None:
            for workflow in {self.workflow_by_node[key] for key in deadlines}:
                idle = self.workflow_idle_since[workflow]
                if (
                    idle is not None and workflow not in busy_workflows
                    and idle + cap > now_ms
                ):
                    wake = min(wake, idle + cap)
        return max(0., wake - now_ms)

    def summary(self):
        return {
            "workflow_idle_cap_ms": self.policy.workflow_idle_cap_ms,
            "system_idle_cap_ms": self.policy.system_idle_cap_ms,
            "timing_transformed": bool(self.shifts),
            "system_skipped_ms": sum(
                row["skipped_ms"] for row in self.shifts if row["kind"] == "system_idle"
            ),
            "workflow_skipped_ms": {
                workflow: sum(
                    row["skipped_ms"] for row in self.shifts
                    if row["workflow_id"] == workflow
                )
                for workflow in sorted(set(self.workflow_by_node.values()))
            },
            "shifts": self.shifts,
            "no_inflight_external_timer_wait_ms": sum(
                row["duration_ms"] for row in self.timer_idle_waits
            ),
            "no_inflight_external_timer_wait_count": len(self.timer_idle_waits),
            "maximum_no_inflight_external_timer_wait_ms": max(
                (row["duration_ms"] for row in self.timer_idle_waits), default=0.,
            ),
            "no_inflight_external_timer_wait_intervals": self.timer_idle_waits,
            "external_idle_accounting_scope": (
                "actual driver timer sleeps without any active request or event "
                "coroutine; excludes server admission and KV recovery waiting"
            ),
            "per_turn_delay_clamping": False,
            "reported_jct_clock": "actual_benchmark_wall_time_with_declared_idle_policy",
            "original_application_wall_time_reconstructed": False,
        }
