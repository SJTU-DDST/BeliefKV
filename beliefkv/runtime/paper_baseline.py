"""Current SGLang event/admission adapter for the first external paper policy."""

from __future__ import annotations

from collections import Counter
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.paper_baselines import ThunderWorkingSet
from beliefkv.runtime.sglang_v0520_admission import (
    _request_key, compile_native_prefill_plan,
)


class ThunderAgentAdapter:
    policy_name = "thunderagent_public_single_backend"
    author_commit = "7ddc8610270e56d3b109eed8796b3a4360fc67c9"

    def __init__(self, runtime):
        forbidden = (
            runtime._custom_kv_management, runtime.enable_resident_first,
            runtime.enable_join_parent_priority, runtime.enable_join_last_child_priority,
            runtime.enable_final_stage_priority, runtime._tool_continuity is not None,
            runtime._model_worker is not None, runtime._semantic_worker is not None,
            runtime._online_tool_timing is not None,
        )
        if runtime.internal_comparison_mode != "scheduler_only" or any(forbidden):
            raise ValueError("paper adapter must not inherit BeliefKV policy decisions")
        self.runtime = runtime
        self.policy: ThunderWorkingSet | None = None
        self.requests = {}
        self.workflows: dict[str, str] = {}
        self.counts = Counter()
        self.next_tick = 0.
        self.last_forced = set()

    def attach(self, cache):
        components = getattr(cache, "tree_components", ())
        capacity = getattr(getattr(cache, "token_to_kv_pool_allocator", None), "size_full", None)
        if components != (0,) or type(capacity) is not int or capacity <= 0:
            raise ValueError("paper adapter requires measured FULL-only KV capacity")
        if self.policy is not None:
            raise ValueError("paper adapter cannot silently change cache capacity")
        self.policy = ThunderWorkingSet(capacity)

    def registered(self, req, key):
        if self.policy is None:
            raise ValueError("paper adapter has no native cache")
        if key.request_id in self.requests:
            raise ValueError("duplicate paper-baseline request")
        tokens = len(getattr(req, "origin_input_ids", ()))
        now = time.monotonic()
        self.policy.arrive(key.invocation_id, tokens, now=now)
        self.requests[key.request_id] = key, now
        self.workflows[key.invocation_id] = key.root_workflow_id
        self.counts["registered"] += 1

    def events(self, events):
        for event in events:
            if event.kind is Kind.WORKFLOW_END:
                retired = [
                    invocation for invocation, workflow in self.workflows.items()
                    if workflow == event.workflow_id
                ]
            elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
                invocation = self.runtime.graph.invocations.get(event.invocation_id)
                retired = (
                    [event.invocation_id]
                    if invocation is not None and invocation.state.terminal else []
                )
            else:
                continue
            for invocation in retired:
                self.forget(invocation)

    def forget(self, invocation):
        if self.policy is not None:
            self.policy.forget(invocation)
        self.workflows.pop(invocation, None)
        for request, (key, _when) in tuple(self.requests.items()):
            if key.invocation_id == invocation:
                self.requests.pop(request)
        self.counts["programs_retired"] += 1

    def tick(self, *, now=None):
        if self.policy is None:
            return
        if now is None:
            now = time.monotonic()
        if now < self.next_tick:
            return
        self.next_tick = now + 5.
        resumed = self.policy.tick(now=now)
        self.counts["control_ticks"] += 1
        self.counts["resumed"] += len(resumed)
        # Match the public router's 1800 second pending-request timeout.
        for request, (key, arrived) in self.requests.items():
            item = self.policy.programs.get(key.invocation_id)
            if item is not None and not item.active and now - arrived >= 1800.:
                item.active = True
                self.last_forced.add(request)
                self.counts["public_timeout_force_resumed"] += 1

    def plan(self, native_order, *, running_batch, adder):
        self.tick()
        runtime = self.runtime
        authorized = []
        for req in native_order:
            if getattr(req, "beliefkv_metadata", None) is None:
                continue
            if len(authorized) == 512:
                break
            key = _request_key(req, known_keys=runtime.visible)
            if key is None or runtime.visible.get(key.request_id) != key or runtime._terminal(key):
                continue
            item = self.policy.programs.get(key.invocation_id)
            if item is not None and item.active:
                authorized.append(key)
            else:
                self.counts["deferred_selections"] += 1
        return compile_native_prefill_plan(
            authorized, semantic_revision=runtime.semantic_revision,
        )

    def completed(self, batch, completed_ids):
        if self.policy is None:
            return
        completed = set(completed_ids)
        if not completed:
            return
        now = time.monotonic()
        for req in getattr(batch, "reqs", ()):
            if req.rid not in completed:
                continue
            record = self.requests.pop(req.rid, None)
            self.last_forced.discard(req.rid)
            if record is None:
                continue
            key, _arrived = record
            if key.invocation_id not in self.policy.programs:
                continue
            tokens = len(getattr(req, "origin_input_ids", ())) + len(getattr(req, "output_ids", ()))
            self.policy.completed(key.invocation_id, tokens, now=now)
            self.counts["completed"] += 1

    def aborted(self, request_id, *, all_requests=False):
        for rid, (key, _arrived) in tuple(self.requests.items()):
            if all_requests or rid.startswith(request_id):
                self.forget(key.invocation_id)
                self.last_forced.discard(rid)

    def status(self):
        programs = self.policy.programs.values() if self.policy is not None else ()
        active = sum(item.active for item in programs)
        return {
            "policy": self.policy_name, "author_commit": self.author_commit,
            "program_identity": "invocation_id", "future_information_used": False,
            "native_capacity_authority_preserved": True,
            "scope": "single_backend_exact_token_ingress_policy_adaptation",
            "capacity_tokens": self.policy.capacity_tokens if self.policy is not None else None,
            "programs": len(self.workflows), "active_programs": active,
            "pending_requests": len(self.requests), "counts": dict(self.counts),
        }

    def close(self):
        self.requests.clear()
        self.workflows.clear()
        self.last_forced.clear()
        if self.policy is not None:
            self.policy.programs.clear()


def create_adapter(runtime, name):
    if name == ThunderAgentAdapter.policy_name:
        return ThunderAgentAdapter(runtime)
    raise ValueError("paper policy lacks a qualified runtime adapter: " + name)
