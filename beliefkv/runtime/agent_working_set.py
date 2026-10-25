"""Compose agent working-set admission with BeliefKV's native migration paths."""

from collections import Counter
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.agent_working_set import AgentWorkingSet


class AgentAdmission:
    def __init__(self, runtime):
        self.runtime = runtime
        self.policy: AgentWorkingSet | None = None
        self.requests, self.latest, self.workflows = {}, {}, {}
        self.tool_waits = {}
        self.next_tick = 0.
        self.restore_backoff = {}
        self.counts = Counter()

    def attach(self, cache):
        capacity = getattr(getattr(cache, "token_to_kv_pool_allocator", None), "size_full", None)
        if getattr(cache, "tree_components", ()) != (0,):
            raise ValueError("agent working-set admission currently requires FULL-only KV")
        if self.policy is not None:
            raise ValueError("agent working-set capacity cannot silently change")
        self.policy = AgentWorkingSet(capacity)

    def registered(self, req, key):
        if self.policy is None or key.request_id in self.requests:
            raise ValueError("agent admission requires a cache and unique request")
        old = self.latest.get(key.invocation_id)
        if old is not None and (
            old.root_workflow_id, old.context_id,
        ) != (key.root_workflow_id, key.context_id):
            raise ValueError("an agent cannot change workflow or context identity")
        critical = key.invocation_id in self.runtime._join_priority_pending
        self.policy.arrive(
            key.invocation_id, len(req.origin_input_ids),
            now=time.monotonic(), critical=critical,
        )
        if old is not None and (old.session_id, old.session_generation) != (
            key.session_id, key.session_generation,
        ):
            self.policy.release_restore(key.invocation_id)
        self.requests[key.request_id] = key
        self.latest[key.invocation_id] = key
        self.workflows[key.invocation_id] = key.root_workflow_id
        self.tool_waits.pop(key.invocation_id, None)
        self.counts["registered"] += 1

    def allows(self, key):
        return bool(
            self.policy is not None and key is not None
            and self.requests.get(key.request_id) == key
            and self.policy.allowed(key.invocation_id)
        )

    def admitted(self, req):
        key = self.requests.get(req.rid)
        if key is not None:
            self.policy.begin(key.invocation_id)

    def completed(self, batch, completed_ids):
        finished = set(completed_ids)
        now = time.monotonic()
        for req in getattr(batch, "reqs", ()):
            if req.rid not in finished:
                continue
            key = self.requests.pop(req.rid, None)
            if key is not None and key.invocation_id in self.policy.agents:
                self.policy.completed(
                    key.invocation_id, len(req.origin_input_ids) + len(req.output_ids),
                    now=now,
                )
                self.counts["completed"] += 1
        if finished:
            self.restore_backoff.clear()

    def events(self, events):
        if self.policy is None:
            return
        now = time.monotonic()
        for event in events:
            if event.kind is Kind.WORKFLOW_END:
                for invocation, workflow in tuple(self.workflows.items()):
                    if workflow == event.workflow_id:
                        self.forget(invocation, now=now)
                continue
            invocation = self.runtime.graph.invocations.get(event.invocation_id)
            if invocation is not None and invocation.state.terminal:
                self.forget(event.invocation_id, now=now)
                continue
            if event.kind in (Kind.CALL, Kind.SPAWN, Kind.JOIN_WAIT):
                if invocation is not None and invocation.state in (
                    InvocationState.WAIT_JOIN, InvocationState.WAIT_CHILD,
                ):
                    self.policy.block(event.invocation_id, "join", now=now)
            elif event.kind in (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT):
                join = self.runtime.graph.joins.get(event.join_id)
                if join is not None:
                    for waiter in join.waiter_invocation_ids:
                        self.policy.unblock(waiter, now=now, critical=True)
            elif event.kind is Kind.TOOL_START:
                if event.invocation_id in self.latest:
                    identity = str(event.attributes.get("tool_run_id")
                                   or event.attributes.get("tool_call_id") or "_legacy")
                    self.tool_waits.setdefault(event.invocation_id, {})[identity] = now
            elif event.kind is Kind.TOOL_END:
                identity = str(event.attributes.get("tool_run_id")
                               or event.attributes.get("tool_call_id") or "_legacy")
                waiting = self.tool_waits.get(event.invocation_id)
                if waiting is not None:
                    waiting.pop(identity, None)
                    if not waiting:
                        del self.tool_waits[event.invocation_id]
                if invocation is not None and not invocation.active_tool_calls:
                    self.policy.unblock(event.invocation_id, now=now)
        self.restore_backoff.clear()

    def tick(self, *, now=None):
        timestamp = time.monotonic() if now is None else now
        if self.policy is None or timestamp < self.next_tick:
            return
        self.next_tick = timestamp + 1.
        self.counts["control_ticks"] += 1
        # Reuse the existing causal prediction; never parse tool text or run a
        # second estimator here. Native eviction still validates physical state.
        for invocation, waiting in tuple(self.tool_waits.items()):
            started = min(waiting.values())
            key = self.latest.get(invocation)
            item = self.policy.agents.get(invocation)
            if key is not None and item is not None and item.active:
                if self.runtime._long_tool_wait(key):
                    self.policy.block(invocation, "long_tool", now=timestamp)
                elif timestamp - started >= 1.:
                    self.policy.block(invocation, "elapsed_tool", now=timestamp)
        self.policy.rebalance(now=timestamp)

    def claim_restore(self, key, *, source):
        if self.policy is None:
            return False
        critical = source == "join_ticket"
        previous = self.latest.get(key.invocation_id)
        if previous is None or (
            previous.root_workflow_id, previous.context_id,
            previous.session_id, previous.session_generation,
        ) != (
            key.root_workflow_id, key.context_id, key.session_id, key.session_generation,
        ) or key.context_epoch not in (
            previous.context_epoch - int(critical), previous.context_epoch, previous.context_epoch + 1,
        ):
            return False
        if not critical and not self.allows(key):
            self.counts["restore_outside_active_set"] += 1
            return False
        now = time.monotonic()
        signature = key, source
        if now < self.restore_backoff.get(signature, 0.):
            return False
        if not self.policy.reserve_restore(key.invocation_id, now=now, critical=critical):
            self.restore_backoff[signature] = now + .2
            return False
        return True

    def holds_restore(self, key):
        if self.policy is None or key is None:
            return False
        previous = self.latest.get(key.invocation_id)
        if previous is None or (
            previous.root_workflow_id, previous.context_id,
            previous.session_id, previous.session_generation,
        ) != (
            key.root_workflow_id, key.context_id, key.session_id, key.session_generation,
        ) or key.context_epoch not in (
            previous.context_epoch - 1, previous.context_epoch, previous.context_epoch + 1,
        ):
            return False
        item = self.policy.agents.get(key.invocation_id)
        return bool(
            item is not None and item.active
            and self.policy.restore_owner == key.invocation_id
            and item.restore_until > time.monotonic()
        )

    def requeued(self, requests, *, is_retracted):
        now = time.monotonic()
        for req in requests:
            key = self.runtime.visible[req.rid]
            old = self.requests.get(req.rid)
            if old is None or (
                old.root_workflow_id, old.invocation_id, old.context_id,
            ) != (key.root_workflow_id, key.invocation_id, key.context_id):
                raise ValueError("agent requeue changed its logical identity")
            self.requests[req.rid] = self.latest[key.invocation_id] = key
            self.policy.requeued(key.invocation_id, now=now)
            self.counts["retracted" if is_retracted else "requeued"] += 1

    def forget(self, invocation, *, now):
        if self.policy is not None:
            self.policy.forget(invocation, now=now)
        self.latest.pop(invocation, None)
        self.workflows.pop(invocation, None)
        self.tool_waits.pop(invocation, None)
        for rid, key in tuple(self.requests.items()):
            if key.invocation_id == invocation:
                del self.requests[rid]
        self.counts["retired"] += 1

    def aborted(self, request_id, *, all_requests=False):
        if all_requests:
            self.close()
            return
        now = time.monotonic()
        for rid, key in tuple(self.requests.items()):
            if all_requests or rid.startswith(request_id):
                self.forget(key.invocation_id, now=now)

    def status(self):
        policy = self.policy
        return {
            "policy": "agent_working_set_with_native_migration",
            "identity": "invocation_id", "future_information_used": False,
            "native_capacity_authority_preserved": True,
            "capacity_tokens": policy.capacity if policy is not None else None,
            "logical_active_tokens": policy.used_tokens if policy is not None else 0,
            "active_agents": sum(item.active for item in policy.agents.values()) if policy else 0,
            "agents": len(self.latest), "pending_requests": len(self.requests),
            "recovery_consumer": policy.restore_owner if policy else None,
            "capacity_semantics": "conservative_sum_of_observed_agent_contexts_plus_growth_buffer",
            "shared_prefixes_deduplicated": False,
            "control_interval_ms": 1000, "restore_reservation_ms": 1500,
            "counts": dict(self.counts), "policy_counts": dict(policy.counts) if policy else {},
        }

    def close(self):
        if self.policy is not None:
            self.policy.agents.clear()
            self.policy.used_tokens = 0
            self.policy.restore_owner = None
        self.requests.clear()
        self.latest.clear()
        self.workflows.clear()
        self.tool_waits.clear()
        self.restore_backoff.clear()
