"""Current SGLang event/admission adapter for the first external paper policy."""

from __future__ import annotations

from collections import Counter
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.continuum_history import ContinuumHistory
from beliefkv.policy.paper_baselines import ThunderWorkingSet, continuum_public_priority
from beliefkv.runtime.continuum_pins import ContinuumPins
from beliefkv.runtime.sglang_v0520_admission import (
    _request_key, compile_native_prefill_plan,
)


def _isolated(runtime):
    forbidden = (
        runtime._custom_kv_management, runtime.enable_resident_first,
        runtime.enable_join_parent_priority, runtime.enable_join_last_child_priority,
        runtime.enable_final_stage_priority, runtime._tool_continuity is not None,
        runtime._model_worker is not None, runtime._semantic_worker is not None,
        runtime._online_tool_timing is not None,
    )
    if runtime.internal_comparison_mode != "scheduler_only" or any(forbidden):
        raise ValueError("paper adapter must not inherit BeliefKV policy decisions")


def _retired(runtime, workflows, events):
    for event in events:
        if event.kind is Kind.WORKFLOW_END:
            yield from (
                invocation for invocation, workflow in tuple(workflows.items())
                if workflow == event.workflow_id
            )
        elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
            invocation = runtime.graph.invocations.get(event.invocation_id)
            if invocation is not None and invocation.state.terminal:
                yield event.invocation_id


class ThunderAgentAdapter:
    policy_name = "thunderagent_public_single_backend"
    author_commit = "7ddc8610270e56d3b109eed8796b3a4360fc67c9"

    def __init__(self, runtime):
        _isolated(runtime)
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
        for invocation in _retired(self.runtime, self.workflows, events):
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


class ContinuumPublicAdapter:
    policy_name = "continuum_public_single_backend"
    author_commit = "316a58794a6ff86b216e579b74fd56ed0c5a911f"

    def __init__(self, runtime):
        _isolated(runtime)
        self.runtime = runtime
        self.cache = self.tools = None
        self.requests = {}
        self.workflows = {}
        self.arrivals = {}
        self.program_priorities = {}
        self.queued = {}
        self.admitted = set()
        self.history = ContinuumHistory()
        self.pins = ContinuumPins()
        self.counts = Counter()
        self._sequence = 0
        self._running_count = self._batch_admitted = 0

    def attach(self, cache):
        capacity = getattr(getattr(cache, "token_to_kv_pool_allocator", None), "size_full", None)
        if getattr(cache, "tree_components", ()) != (0,) or type(capacity) is not int or capacity <= 0:
            raise ValueError("paper adapter requires measured FULL-only KV capacity")
        if self.cache is not None:
            raise ValueError("paper adapter cannot silently change native cache")
        from beliefkv.runtime.continuum_tools import ContinuumTools

        self.cache, self.tools = cache, ContinuumTools()

    def registered(self, req, key):
        if self.cache is None or key.request_id in self.requests:
            raise ValueError("paper request requires a native cache and unique identity")
        now = time.monotonic()
        invocation = key.invocation_id
        if invocation not in self.arrivals:
            self._sequence += 1
            self.arrivals[invocation] = now
            self.program_priorities[invocation] = self._sequence
        if self.history.arrived(invocation, now=now) is not None:
            self.counts["observed_reentry_gaps"] += 1
        req.priority = self.program_priorities[invocation]
        self.requests[key.request_id] = key
        self.workflows[invocation] = key.root_workflow_id
        self.queued[invocation] = key
        self.pins.refresh(self.queued, now=now)
        self.counts["registered"] += 1

    def events(self, events):
        for invocation in _retired(self.runtime, self.workflows, events):
            self.forget(invocation)

    def forget(self, invocation):
        self.pins.release(invocation, "program_retired")
        self.history.forget(invocation)
        self.workflows.pop(invocation, None)
        self.arrivals.pop(invocation, None)
        self.program_priorities.pop(invocation, None)
        self.queued.pop(invocation, None)
        for request, key in tuple(self.requests.items()):
            if key.invocation_id == invocation:
                self.requests.pop(request)
                self.admitted.discard(request)
        self.counts["programs_retired"] += 1

    def tick(self, *, now=None):
        self.pins.refresh(self.queued, now=time.monotonic() if now is None else now)

    def plan(self, native_order, *, running_batch, adder):
        self.tick()
        self._running_count = len(getattr(running_batch, "reqs", ()))
        self._batch_admitted = 0
        authorized = []
        for index, req in enumerate(native_order):
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
            authorized.append((continuum_public_priority(
                ttl_retained=key.invocation_id in self.pins.pins,
                program_arrival=self.arrivals[key.invocation_id], native_position=index,
            ), key))
        authorized.sort(key=lambda item: item[0])
        self.counts["queue_plans"] += 1
        return compile_native_prefill_plan(
            [key for _priority, key in authorized],
            semantic_revision=self.runtime.semantic_revision,
        )

    def candidate_result(self, req, *, admitted, result):
        key = self.requests.get(getattr(req, "rid", None))
        if key is None:
            return False
        if admitted:
            self.queued.pop(key.invocation_id, None)
            self.admitted.add(key.request_id)
            self._batch_admitted += 1
        elif result == "NO_TOKEN" and not self._running_count and not self._batch_admitted:
            return self.pins.relieve_pressure(running_requests=0) is not None
        return False

    def decode_pressure(self, batch):
        return self.pins.relieve_pressure(
            running_requests=len(getattr(batch, "reqs", ())),
        ) is not None

    def requeued(self, requests, *, is_retracted):
        for req in requests:
            key = self.runtime.visible.get(req.rid)
            if key is None or req.rid not in self.requests:
                raise ValueError("paper requeue lacks its registered request")
            self.requests[req.rid] = key
            self.queued[key.invocation_id] = key
            self.admitted.discard(req.rid)
            self.counts["native_retractions" if is_retracted else "native_requeues"] += 1

    def completed(self, batch, completed_ids):
        for req in getattr(batch, "reqs", ()):
            if req.rid not in completed_ids:
                continue
            key = self.requests.pop(req.rid, None)
            if key is None:
                continue
            self.admitted.discard(req.rid)
            self.queued.pop(key.invocation_id, None)
            now = time.monotonic()
            tool = self.tools.identify(req)
            ttl = self.history.public_ttl(tool)
            self.history.completed(key.invocation_id, tool=tool, now=now)
            self.pins.retain(self.cache, key, ttl=ttl, now=now)
            self.counts["parsed_tool" if tool is not None else "unidentified_or_return"] += 1
            self.counts["completed"] += 1

    def aborted(self, request_id, *, all_requests=False):
        if all_requests:
            for invocation in tuple(self.workflows):
                self.forget(invocation)
            return
        for rid, key in tuple(self.requests.items()):
            if all_requests or rid.startswith(request_id):
                self.forget(key.invocation_id)

    def status(self):
        return {
            "policy": self.policy_name, "author_commit": self.author_commit,
            "program_identity": "invocation_id", "future_information_used": False,
            "native_capacity_authority_preserved": True,
            "scope": "public_policy_current_model_native_preemption_adaptation",
            "programs": len(self.workflows), "pending_requests": len(self.requests),
            "ttl_pins": len(self.pins.pins), "pin_counts": dict(self.pins.counts),
            "observed_tool_types": len(self.history.tool_gap_counts),
            "counts": dict(self.counts),
        }

    def close(self):
        self.pins.close()
        self.requests.clear()
        self.workflows.clear()
        self.arrivals.clear()
        self.program_priorities.clear()
        self.queued.clear()
        self.admitted.clear()
        self.history.departures.clear()


def create_adapter(runtime, name):
    if name == ThunderAgentAdapter.policy_name:
        return ThunderAgentAdapter(runtime)
    if name == ContinuumPublicAdapter.policy_name:
        return ContinuumPublicAdapter(runtime)
    raise ValueError("paper policy lacks a qualified runtime adapter: " + name)
