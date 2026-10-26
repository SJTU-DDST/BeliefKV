"""Compose sessionless native prefix migration with existing agent eligibility."""

from array import array
from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import islice
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.paper_prefix_transfers import NativePrefixTransfers


@dataclass
class _Context:
    key: object
    observed_key: object
    snapshot: object = None
    pending_request: str | None = None
    completed_at: float | None = None

    @property
    def owner(self):
        return self.key.root_workflow_id, self.key.context_id, self.key.context_epoch


class FullPrefixMigration:
    interval_s = .2
    max_checks = 16
    spawn_batch_bytes = 4 * 1024**3
    cold_batch_bytes = 128 * 1024**2
    idle_lend_s = 1.

    def __init__(self, runtime, *, clock=time.monotonic, max_contexts=4096):
        if not runtime._custom_kv_management or runtime._paper_baseline is not None:
            raise ValueError("Full prefix migration requires isolated Full KV policy")
        self.runtime, self.clock, self.max_contexts = runtime, clock, max_contexts
        self.contexts, self.delegators, self.restores = OrderedDict(), OrderedDict(), OrderedDict()
        self.requests = {}
        self._native_requests, self.admitted_requests = {}, set()
        self.counts = Counter()
        self.cache = self.transfers = None
        self.next_tick = 0.
        self.next_restore = 0.
        self._authority_active = False
        self._paths, self._members, self._generations = {}, {}, {}
        self._previous_observer = None
        self._observer = self._node_changed

    @contextmanager
    def _authority(self):
        previous = self._authority_active
        self._authority_active = True
        try:
            yield
        finally:
            self._authority_active = previous

    def attach(self, cache):
        if self.cache is not None:
            raise ValueError("Full prefix migration cannot change native caches")
        with self._authority():
            self.transfers = NativePrefixTransfers(
                cache, can_mutate=lambda: self._authority_active, max_nodes=1024,
            )
            self.cache = cache
            self._previous_observer = cache.tree_core.beliefkv_full_resident_observer
            cache.tree_core.beliefkv_full_resident_observer = self._observer

    @staticmethod
    def _input_key(req, *, completed=False):
        from sglang.srt.mem_cache.radix_cache import RadixKey

        if (
            getattr(req, "session_id", None) is not None
            or getattr(req, "input_embeds", None) is not None
            or getattr(req, "positional_embed_overrides", None) is not None
        ):
            raise ValueError("sessionless Full requires plain token prefix identity")
        tokens = req.origin_input_ids
        if completed:
            tokens = array("q", tokens)
            tokens.extend(getattr(req, "output_ids", ()))
        return RadixKey(
            tokens, getattr(req, "extra_key", None),
            cache_salt=getattr(req, "cache_salt", None), limit=max(0, len(tokens) - 1),
        )

    def _capture(self, context):
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams

        match = self.cache.match_prefix(MatchPrefixParams(
            key=context.observed_key, cow_mamba=False,
        ))
        snapshot = self.transfers.capture(match.best_match_node, owner=context.owner)
        context.snapshot = snapshot
        self._forget_members(context.key.context_id)
        if snapshot is not None:
            for node in self.transfers.resolve(snapshot, owner=context.owner):
                self._add_member(node, context.key.context_id)
        self.counts["native_prefix_captures"] += 1
        return snapshot

    def _forget_members(self, identity):
        for node_id in self._paths.pop(identity, ()):
            members = self._members.get(node_id)
            if members is not None:
                members.discard(identity)
                if not members:
                    self._members.pop(node_id, None)
                    self._generations.pop(node_id, None)

    def _add_member(self, node, identity):
        self._paths.setdefault(identity, set()).add(node.id)
        self._members.setdefault(node.id, set()).add(identity)
        self._generations[node.id] = node.creation_time

    def _node_changed(self, node):
        if self._previous_observer is not None:
            self._previous_observer(node)
        if self.transfers._node(node.id) is not node:
            for identity in self._members.pop(node.id, ()):
                self._paths[identity].discard(node.id)
            self._generations.pop(node.id, None)
            return
        inherited = set()
        for child in node.children.values():
            inherited.update(self._members.get(child.id, ()))
        for identity in inherited - self._members.get(node.id, set()):
            self._add_member(node, identity)
            self.counts["split_prefix_memberships"] += 1

    def registered(self, req, key):
        if key.session_id is not None:
            return
        if self.cache is None or key.request_id in self.requests:
            raise ValueError("sessionless Full requires an attached cache and unique request")
        old = self.contexts.get(key.context_id)
        if old is not None and (
            old.key.root_workflow_id != key.root_workflow_id
            or old.key.context_epoch > key.context_epoch
            or old.pending_request is not None
        ):
            raise ValueError("sessionless Full context ownership changed illegally")
        if old is None and len(self.contexts) >= self.max_contexts:
            raise ValueError("sessionless Full observed context bound reached")
        with self._authority():
            context = _Context(key, self._input_key(req), pending_request=key.request_id)
            self.contexts[key.context_id] = context
            self.requests[key.request_id] = key
            self._native_requests[key.request_id] = req
            self._capture(context)
            self.restores.pop(key.context_id, None)
        self.counts["registered"] += 1

    def bound_key(self, identity):
        context = self.contexts.get(identity)
        return context.key if context is not None else None

    def completed(self, batch, completed_ids):
        if not completed_ids:
            return
        finished = set(completed_ids)
        with self._authority():
            for req in getattr(batch, "reqs", ()):
                if req.rid not in finished or (key := self.requests.pop(req.rid, None)) is None:
                    continue
                self._native_requests.pop(req.rid, None)
                self.admitted_requests.discard(req.rid)
                self.next_restore = 0.
                context = self.contexts.get(key.context_id)
                if context is None or context.key != key:
                    continue
                context.observed_key = self._input_key(req, completed=True)
                context.pending_request = None
                context.completed_at = self.clock()
                self._capture(context)
                self.counts["completed"] += 1
                if self.runtime._observed_final_eos_live(key, self.clock() * 1000.):
                    self._queue_child_eos(key.invocation_id)

    def _queue_child_eos(self, child):
        for join_id in self.runtime._join_by_invocation.get(child, ()):
            join = self.runtime.graph.joins.get(join_id)
            if (
                join is not None and not join.satisfied and join.mode.value == "all"
                and join.member_invocation_ids - join.completed_member_ids == {child}
            ):
                self._queue_join(join, source="observed_eos")

    def _queue_join(self, join, *, source):
        for waiter in join.waiter_invocation_ids:
            parent = self.runtime.graph.invocations.get(waiter)
            context = self.contexts.get(parent.context_id) if parent is not None else None
            if (
                context is not None and context.pending_request is None
                and parent.state in (InvocationState.WAIT_JOIN, InvocationState.READY)
                and not self.runtime._terminal(context.key)
            ):
                self.restores[context.key.context_id] = (
                    context.key, join.join_id, source, self.clock() + 1.5,
                )
                self.next_tick = 0.
                self.next_restore = 0.

    def events(self, events):
        for event in events:
            if event.kind in (Kind.SPAWN, Kind.CALL):
                parent = self.runtime.graph.invocations.get(event.invocation_id)
                if parent is not None and parent.context_id in self.contexts:
                    self.delegators[parent.context_id] = None
                    self.next_tick = 0.
            elif event.kind in (Kind.RETURN, Kind.JOIN_SATISFIED):
                joins = (
                    (event.join_id,) if event.kind is Kind.JOIN_SATISFIED else
                    self.runtime._join_by_invocation.get(event.invocation_id, ())
                )
                for join_id in joins:
                    join = self.runtime.graph.joins.get(join_id)
                    if join is not None and join.satisfied:
                        self._queue_join(join, source="confirmed_join")
            if event.kind in (
                Kind.CONTEXT_ADVANCE, Kind.CONTEXT_COMPACT, Kind.INVOCATION_CANCEL,
            ):
                self.forget(event.context_id)
            elif event.kind is Kind.TOOL_START:
                self.restores.pop(event.context_id, None)
            elif event.kind is Kind.JOIN_TIMEOUT:
                for identity, ticket in tuple(self.restores.items()):
                    if ticket[1] == event.join_id:
                        del self.restores[identity]
            elif event.kind is Kind.WORKFLOW_END:
                for identity, context in tuple(self.contexts.items()):
                    if context.key.root_workflow_id == event.workflow_id:
                        self.forget(identity)

    def forget(self, identity):
        context = self.contexts.pop(identity, None)
        self._forget_members(identity)
        self.delegators.pop(identity, None)
        self.restores.pop(identity, None)
        if context is not None:
            self.requests.pop(context.pending_request, None)
            self._native_requests.pop(context.pending_request, None)
            self.admitted_requests.discard(context.pending_request)

    def admitted(self, req):
        if req.rid in self.requests:
            self.admitted_requests.add(req.rid)

    def requeued(self, requests):
        from beliefkv.runtime.sglang_v0520_admission import _request_key

        for req in requests:
            if req.rid not in self.requests:
                continue
            key = _request_key(req)
            previous = self.requests[req.rid]
            if key is None or (
                key.root_workflow_id, key.context_id, key.context_epoch,
            ) != (previous.root_workflow_id, previous.context_id, previous.context_epoch):
                raise ValueError("requeued Full prefix owner changed")
            self.requests[req.rid] = key
            self.contexts[key.context_id].key = key
            self.admitted_requests.discard(req.rid)

    def aborted(self, request, *, all_requests=False):
        identities = (
            tuple(self.contexts) if all_requests else
            tuple(key.context_id for rid, key in self.requests.items() if rid.startswith(request))
        )
        for identity in identities:
            self.forget(identity)

    def _blocked(self, context, now):
        if context.pending_request is not None or self.runtime._terminal(context.key):
            return False
        agents = self.runtime._agent_admission
        if agents is not None and agents.holds_restore(context.key):
            return False
        invocation = self.runtime.graph.invocations.get(context.key.invocation_id)
        if invocation is None:
            return False
        if invocation.state in (InvocationState.WAIT_CHILD, InvocationState.WAIT_JOIN):
            return True
        item = agents.policy.agents.get(context.key.invocation_id) if agents is not None else None
        if (
            invocation.state is InvocationState.READY and item is not None
            and item.blocked == "observed_idle" and context.completed_at is not None
            and now - context.completed_at >= self.idle_lend_s
        ):
            return True
        return bool(
            invocation.state is InvocationState.WAIT_TOOL
            and invocation.active_tool_start_ms is not None
            and (
                self.runtime._long_tool_wait(context.key)
                or now * 1000. - invocation.active_tool_start_ms >= 1000.
            )
        )

    def _lend_idle(self, now):
        agents = self.runtime._agent_admission
        if agents is None:
            return
        policy = agents.policy
        if any(
            item.in_flight or item.active and item.pending and not item.blocked
            for item in policy.agents.values()
        ) or not any(
            item.pending and not item.active and not item.blocked
            for item in policy.agents.values()
        ):
            return
        for context in tuple(islice(self.contexts.values(), self.max_checks)):
            item = policy.agents.get(context.key.invocation_id)
            invocation = self.runtime.graph.invocations.get(context.key.invocation_id)
            if (
                context.pending_request is not None or context.completed_at is None
                or now - context.completed_at < self.idle_lend_s
                or item is None or not item.active or item.pending or item.in_flight
                or item.blocked or invocation is None or invocation.state is not InvocationState.READY
                or item.restore_until > now or self.runtime._terminal(context.key)
            ):
                continue
            # No next call is submitted. This is measured inactivity, not JOIN
            # knowledge, and arrival resets the normal agent eligibility state.
            policy.block(context.key.invocation_id, "observed_idle", now=now)
            self.counts["eligibility_lent_observed_idle"] += 1
            if any(
                item.active and item.pending and not item.blocked
                for item in policy.agents.values()
            ):
                break

    def _can_demote(self, node):
        members = self._members.get(node.id)
        if not members or self._generations.get(node.id) != node.creation_time:
            return False
        if node.component_data[0].session_ids:
            return False
        now = self.clock()
        return all(
            (context := self.contexts.get(identity)) is not None
            and context.pending_request is None
            and (self.runtime._terminal(context.key) or self._blocked(context, now))
            and identity not in self.restores
            for identity in members
        )

    def _backup(self, context, *, max_bytes):
        if context.snapshot is None:
            return ()
        unit = self.cache.host_pool_group.anchor_entry.host_pool.size_per_token
        issued = self.transfers.backup_prefix(
            context.snapshot, owner=context.owner, observed_key=context.observed_key,
            max_new_tokens=max_bytes // unit,
        )
        self.counts["d2h_enqueued_tokens"] += sum(item[2] for item in issued)
        return issued

    def _restore(self, now):
        if now < self.next_restore:
            return
        self.next_restore = now + self.interval_s
        for identity, (key, join_id, source, deadline) in tuple(self.restores.items()):
            context = self.contexts.get(identity)
            join = self.runtime.graph.joins.get(join_id)
            if (
                context is None or context.key != key or context.pending_request is not None
                or now >= deadline or self.runtime._terminal(key) or join is None
                or key.invocation_id not in join.waiter_invocation_ids
                or source == "confirmed_join" and not join.satisfied
            ):
                self.restores.pop(identity, None)
                continue
            if context.snapshot is None:
                self.restores.pop(identity, None)
                continue
            status = self.transfers.status(context.snapshot, owner=context.owner)
            if status == "gpu":
                self.restores.pop(identity, None)
                continue
            if status != "cpu_backup":
                continue
            allocator = self.cache.token_to_kv_pool_allocator
            reserve = self._native_reserve()
            path = self.transfers.resolve(context.snapshot, owner=context.owner)
            if path is None:
                continue
            needed = sum(len(node.key) for node in path if node.component_data[0].value is None)
            if needed + reserve > allocator.available_size():
                self.counts["restore_native_capacity_deferred"] += 1
                continue
            agents = self.runtime._agent_admission
            if agents is None or not agents.claim_restore(key, source="join_ticket"):
                continue
            # Keep observed admitted-but-unallocated input and one decode step free.
            issued = self.transfers.restore(
                context.snapshot, owner=context.owner,
                available_tokens=max(0, allocator.available_size() - reserve),
            )
            self.counts["h2d_enqueued_tokens"] += sum(item[2] for item in issued)
            if issued:
                self.counts[source + "_restore_batches"] += 1
                return

    def _native_reserve(self):
        active = {
            rid: self._native_requests[rid] for rid in self.admitted_requests
            if rid in self._native_requests
        }
        for req in getattr(self.runtime._native_running_batch, "reqs", ()):
            if not req.finished():
                active.setdefault(req.rid, req)
        total = 0
        for req in active.values():
            allocated = getattr(getattr(req, "kv", None), "kv_allocated_len", 0)
            total += max(0, len(req.origin_input_ids) + len(getattr(req, "output_ids", ())) - allocated) + 1
        return total

    def dispatch(self):
        now = self.clock()
        if self.cache is None or self.runtime.physical_disabled:
            return
        with self._authority():
            self._restore(now)
            if now < self.next_tick or not self.runtime.enable_prepare_host:
                return
            self.next_tick = now + self.interval_s
            self.counts["maintenance_rounds"] += 1
            self._lend_idle(now)
            if not self.runtime.enable_background_prepare:
                return
            for identity in tuple(islice(self.delegators, 4)):
                context = self.contexts.get(identity)
                if context is None or self.runtime._terminal(context.key):
                    self.delegators.pop(identity, None)
                    continue
                self.delegators.move_to_end(identity)
                if self._backup(context, max_bytes=self.spawn_batch_bytes):
                    break
                if context.snapshot is not None:
                    path = self.transfers.resolve(context.snapshot, owner=context.owner)
                    if path is not None and all(node.component_data[0].host_value is not None for node in path):
                        self.delegators.pop(identity, None)
            for identity in tuple(islice(self.contexts, self.max_checks)):
                context = self.contexts[identity]
                self.contexts.move_to_end(identity)
                self.counts["cold_owner_checks"] += 1
                if identity in self.restores or not self._blocked(context, now):
                    continue
                if context.snapshot is not None:
                    count = self.transfers.demote(
                        context.snapshot, owner=context.owner,
                        max_tokens=context.snapshot.prefix_tokens, can_demote=self._can_demote,
                    )
                    self.counts["demoted_tokens"] += count
                if not self.cache.ongoing_write_through:
                    self._backup(context, max_bytes=self.cold_batch_bytes)

    def transfer_completed(self, commit):
        handled = self.transfers.transfer_completed(commit)
        if handled:
            self.next_tick = 0.
            self.next_restore = 0.
        return handled

    def status(self):
        return {
            "scope": "full_sessionless_observed_prefix_migration",
            "contexts": len(self.contexts), "requests": len(self.requests),
            "pending_join_restores": len(self.restores),
            "control_interval_ms": self.interval_s * 1000.,
            "cold_owner_checks_per_round": self.max_checks,
            "idle_lend_owner_checks_per_round": self.max_checks,
            "ordinary_restore_binding": False,
            "idle_lend_seconds": self.idle_lend_s,
            "idle_lend_requires_no_eligible_compute_and_actual_waiter": True,
            "inferred_join_boundaries_used": False,
            "counts": dict(self.counts),
            "native_transfer_counts": dict(self.transfers.counts) if self.transfers else {},
            "native_pending_commands": (
                self.transfers.receipts.pending_count if self.transfers else 0
            ),
        }

    def close(self):
        if self.cache is not None:
            if self.cache.tree_core.beliefkv_full_resident_observer is self._observer:
                self.cache.tree_core.beliefkv_full_resident_observer = self._previous_observer
            self.transfers.close()
        self.contexts.clear()
        self.requests.clear()
        self._native_requests.clear()
        self.admitted_requests.clear()
        self.delegators.clear()
        self.restores.clear()
        self._paths.clear()
        self._members.clear()
        self._generations.clear()
