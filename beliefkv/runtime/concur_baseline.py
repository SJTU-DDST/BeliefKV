"""Paper CONCUR admission with explicit observed dynamic-JOIN compatibility."""

from collections import Counter
from dataclasses import asdict
import os
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.concur_admission import ConcurAdmission
from beliefkv.policy.paper_baselines import ConcurWindow
from beliefkv.runtime.concur_feedback import ConcurFeedback
from beliefkv.runtime.paper_baseline import _isolated, _retired
from beliefkv.runtime.sglang_v0520_admission import _request_key, compile_native_prefill_plan


class ConcurAdapter:
    policy_name = "concur_paper_observed_join"

    def __init__(self, runtime):
        _isolated(runtime)
        self.runtime = runtime
        self.initial = int(os.environ.get("BELIEFKV_CONCUR_INITIAL_AGENTS", "18"))
        self.maximum = int(os.environ.get("BELIEFKV_CONCUR_MAX_AGENTS", "512"))
        self.interval_ms = int(os.environ.get("BELIEFKV_CONCUR_INTERVAL_MS", "1000"))
        if not 1 <= self.interval_ms <= 60_000:
            raise ValueError("CONCUR control interval must be in [1, 60000] ms")
        self.policy = ConcurAdmission(ConcurWindow(self.initial, maximum=self.maximum))
        self.feedback = None
        self.requests, self.workflows = {}, {}
        self.counts = Counter()
        self.next_tick = 0.
        self.last_sample = None

    def attach(self, cache):
        if self.feedback is not None:
            raise ValueError("CONCUR cannot silently change native cache")
        self.feedback = ConcurFeedback(cache)

    def registered(self, req, key):
        if self.feedback is None or key.request_id in self.requests:
            raise ValueError("CONCUR requires a native cache and unique request")
        if not self.policy.agents:
            self.next_tick = time.monotonic() + self.interval_ms / 1000.
        self.policy.arrive(key.invocation_id)
        self.requests[key.request_id] = key
        self.workflows[key.invocation_id] = key.root_workflow_id
        self.counts["registered"] += 1

    def events(self, events):
        for invocation in _retired(self.runtime, self.workflows, events):
            self.forget(invocation)
        for event in events:
            if event.kind in (Kind.CALL, Kind.SPAWN, Kind.JOIN_WAIT):
                invocation = self.runtime.graph.invocations.get(event.invocation_id)
                if invocation is not None and invocation.state in (
                    InvocationState.WAIT_JOIN, InvocationState.WAIT_CHILD,
                ):
                    self.counts["observed_join_permits_yielded"] += self.policy.set_blocked(
                        event.invocation_id, True,
                    )
            elif event.kind in (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT):
                join = self.runtime.graph.joins.get(event.join_id)
                if join is not None:
                    for invocation in join.waiter_invocation_ids:
                        self.policy.set_blocked(invocation, False)

    def forget(self, invocation):
        self.policy.forget(invocation)
        self.workflows.pop(invocation, None)
        for rid, key in tuple(self.requests.items()):
            if key.invocation_id == invocation:
                self.requests.pop(rid)
                if self.feedback is not None:
                    self.feedback.forget(rid)
        self.counts["programs_retired"] += 1

    def tick(self, *, now=None):
        timestamp = time.monotonic() if now is None else now
        if self.feedback is None or not self.policy.agents or timestamp < self.next_tick:
            return
        self.next_tick = timestamp + self.interval_ms / 1000.
        sample = self.feedback.sample(self.counts["control_ticks"])
        self.counts["control_ticks"] += 1
        if sample is None:
            return
        before = self.policy.window.window
        after = self.policy.window.observe(
            sample_id=sample.sample_id, gpu_usage=sample.gpu_usage,
            gpu_hit_rate=sample.gpu_hit_rate,
        )
        self.counts["window_increases"] += after > before
        self.counts["window_decreases"] += after < before
        self.last_sample = sample.to_dict()
        self.policy.rebalance()

    def plan(self, native_order, *, running_batch, adder):
        self.tick()
        authorized = []
        for req in native_order:
            if getattr(req, "beliefkv_metadata", None) is None:
                continue
            if len(authorized) == 512:
                break
            key = _request_key(req, known_keys=self.runtime.visible)
            if (
                key is None or self.runtime.visible.get(key.request_id) != key
                or self.requests.get(key.request_id) != key or self.runtime._terminal(key)
            ):
                continue
            agent = self.policy.agents.get(key.invocation_id)
            if agent is not None and agent.active and not agent.blocked:
                authorized.append(key)
            else:
                self.counts["deferred_selections"] += 1
        return compile_native_prefill_plan(
            authorized, semantic_revision=self.runtime.semantic_revision,
        )

    def candidate_result(self, req, *, admitted, result):
        key = self.requests.get(getattr(req, "rid", None))
        if admitted and key is not None:
            self.policy.begin(key.invocation_id)
        return False

    def selected(self, batch):
        mode = getattr(batch, "forward_mode", None)
        if mode is None or not mode.is_extend():
            return
        for req in getattr(batch, "reqs", ()):
            if req.rid in self.requests:
                self.feedback.observe_request(req)

    def requeued(self, requests, *, is_retracted):
        for req in requests:
            key = self.runtime.visible.get(req.rid)
            if key is None or req.rid not in self.requests:
                raise ValueError("CONCUR requeue lacks a registered request")
            self.requests[req.rid] = key
            self.counts["native_retractions" if is_retracted else "native_requeues"] += 1

    def completed(self, batch, completed_ids):
        completed = set(completed_ids)
        for req in getattr(batch, "reqs", ()):
            if req.rid not in completed or req.rid not in self.requests:
                continue
            self.feedback.observe_request(req)
            key = self.requests.pop(req.rid)
            self.feedback.forget(req.rid)
            self.policy.completed(key.invocation_id)
            self.counts["completed"] += 1

    def aborted(self, request_id, *, all_requests=False):
        if all_requests:
            for invocation in tuple(self.workflows):
                self.forget(invocation)
        else:
            for rid, key in tuple(self.requests.items()):
                if rid.startswith(request_id):
                    self.forget(key.invocation_id)

    def status(self):
        return {
            "policy": self.policy_name, "program_identity": "invocation_id",
            "future_information_used": False, "native_capacity_authority_preserved": True,
            "scope": "paper_aimd_with_explicit_observed_join_compatibility",
            "initial_agents": self.initial, "maximum_agents": self.maximum,
            "control_interval_ms": self.interval_ms, "window": self.policy.window.window,
            "control_law": asdict(self.policy.window.config),
            "programs": len(self.policy.agents), "pending_requests": len(self.requests),
            "active_agents": sum(item.active for item in self.policy.agents.values()),
            "boundary_pauses": sum(item.pause_at_boundary for item in self.policy.agents.values()),
            "last_feedback": self.last_sample,
            "feedback_semantics": "native_non_evictable_gpu_usage_and_new_request_gpu_hits_only",
            "feedback_counts": dict(self.feedback.counts) if self.feedback is not None else {},
            "counts": dict(self.counts),
        }

    def close(self):
        self.requests.clear()
        self.workflows.clear()
        self.policy.agents.clear()
        if self.feedback is not None:
            self.feedback.seen.clear()
