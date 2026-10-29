"""KVFlow policy port with an explicitly observed dynamic invocation graph.

Published replay plans are driver-private. Only native requests, completed
same-context inputs and already applied causal events reach this adapter.
"""

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.kvflow import FixedPromptHistory, StepGraph, StepNode
from beliefkv.runtime.kvflow_cache import KVFlowCachePriorities
from beliefkv.runtime.paper_baseline import _isolated
from beliefkv.runtime.paper_prefix_transfers import NativePrefixTransfers
from beliefkv.runtime.sglang_v0520_admission import _request_key, compile_native_prefill_plan


@dataclass
class _Context:
    owner: tuple[str, str, int]
    prior_input: object = None
    fixed_key: object = None
    snapshot: object = None
    backed: bool = False


@dataclass
class _Request:
    request: object
    key: object
    input_key: object
    snapshot: object = None
    matched_revision: int = -1


class KVFlowObservedAdapter:
    policy_name = "kvflow_paper_observed_graph"
    interval_s = .2
    prefetch_limit = 4
    path_check_limit = 16
    context_limit = 512
    path_node_limit = 1024

    def __init__(self, runtime):
        _isolated(runtime)
        self.runtime = runtime
        self.cache = self.transfers = self.priorities = None
        self.history = FixedPromptHistory()
        self.contexts, self.requests, self.admitted = {}, {}, set()
        self.steps, self._prefetch_order = {}, ()
        self._graph_dirty, self._native_authority = True, False
        self._inflight = {}
        self._next_tick = 0.
        self._scan_offsets = {}
        self.counts = Counter()

    @contextmanager
    def _authority(self):
        previous = self._native_authority
        self._native_authority = True
        try:
            yield
        finally:
            self._native_authority = previous

    def attach(self, cache):
        if self.cache is not None:
            raise ValueError("KVFlow cannot silently change native cache")
        if (
            getattr(cache, "disable", True) is not False
            or getattr(cache, "sidecar_pool_specs", ())
        ):
            raise ValueError("KVFlow requires the common plain FULL-only radix cache")
        with self._authority():
            self.transfers = NativePrefixTransfers(
                cache, can_mutate=lambda: self._native_authority,
                max_nodes=self.path_node_limit,
            )
            self.priorities = KVFlowCachePriorities(
                self.transfers, max_anchors=self.context_limit,
            )
            self.cache = cache

    @staticmethod
    def _owner(key):
        return key.root_workflow_id, key.context_id, key.context_epoch

    def _input_key(self, req):
        from sglang.srt.mem_cache.radix_cache import RadixKey

        if (
            getattr(req, "input_embeds", None) is not None
            or getattr(req, "positional_embed_overrides", None) is not None
            or getattr(req, "session", None) is not None
            or getattr(req, "session_params", None) is not None
        ):
            raise ValueError("KVFlow requires plain token requests without session overrides")
        session_id = getattr(req, "session_id", None)
        if session_id is not None:
            metadata = getattr(req, "beliefkv_metadata", None)
            if (
                type(metadata) is not dict
                or metadata.get("full_prompt_replay_guaranteed") is not True
            ):
                raise ValueError("KVFlow radix sessions require an explicit full-prompt contract")
            generation = getattr(req, "session_generation", None)
            refs = getattr(self.cache, "session_refs", None)
            if (
                getattr(self.cache, "enable_session_radix_cache", False) is not True
                or type(session_id) is not str or not session_id
                or type(generation) is not int or generation < 0
                or getattr(refs, "_session_generations", {}).get(session_id) != generation
                or session_id in getattr(refs, "_closed_session_ids", ())
            ):
                raise ValueError("KVFlow requires a live native radix session generation")
            # Native radix identity does not reconstruct or namespace the prompt.
            # Match exactly the complete tokens and namespace provided by ingress.
        return RadixKey(
            req.origin_input_ids, req.extra_key, cache_salt=req.cache_salt,
        )

    def registered(self, req, key):
        if self.cache is None or key.request_id in self.requests:
            raise ValueError("KVFlow requires a native cache and unique request")
        input_key = self._input_key(req)
        owner = self._owner(key)
        with self._authority():
            context = self.contexts.get(key.context_id)
            if context is not None and context.owner != owner:
                if context.owner[0] != owner[0] or context.owner[2] > owner[2]:
                    raise ValueError("KVFlow context workflow or epoch changed illegally")
                self._forget_context(key.context_id)
                context = None
            if context is None:
                if len(self.contexts) >= self.context_limit:
                    raise ValueError("KVFlow observed context bound reached")
                context = self.contexts[key.context_id] = _Context(owner)
            prior = context.prior_input
            if prior is not None and (prior.extra_key, prior.cache_salt) != (
                input_key.extra_key, input_key.cache_salt,
            ):
                self.history.forget(key.context_id)
                self.priorities.forget(key.context_id)
                context.prior_input = context.snapshot = None
                context.backed = False
                prior = None
                self.counts["namespace_history_resets"] += 1
            old_boundary = self.history.boundaries.get(key.context_id, 0)
            prefix_hit = prior.match_at(input_key, 0) if prior is not None else None
            boundary = self.history.observe(
                key.context_id, prompt_tokens=len(input_key), prior_prefix_hit=prefix_hit,
            )
            if boundary != old_boundary:
                context.snapshot, context.backed = None, False
                self.priorities.forget(key.context_id)
                self.counts["fixed_boundary_updates"] += 1
            if boundary:
                from sglang.srt.mem_cache.radix_cache import RadixKey

                context.fixed_key = RadixKey(
                    input_key.token_ids, input_key.extra_key, limit=boundary,
                    cache_salt=input_key.cache_salt,
                )
                self._ensure_anchor(key.context_id)
            else:
                context.fixed_key = None
            self.requests[key.request_id] = _Request(req, key, input_key)
            self._graph_dirty = True
            self.counts["registered"] += 1
            self.counts["native_radix_session_requests"] += key.session_id is not None

    def _capture(self, radix_key, owner):
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams

        match = self.cache.match_prefix(MatchPrefixParams(key=radix_key, cow_mamba=False))
        self.counts["native_prefix_matches"] += 1
        return self.transfers.capture(match.best_match_node, owner=owner)

    def _leaf(self, snapshot, owner):
        if snapshot is None or snapshot.owner != owner:
            return None
        root = self.cache.tree_core.root_node
        node = self.transfers._node(snapshot.leaf_id)
        if (
            root.id != snapshot.root_id or root.creation_time != snapshot.root_creation_time
            or node is None or node.creation_time != snapshot.leaf_creation_time
            or (node.key.extra_key, node.key.cache_salt) != snapshot.namespace
        ):
            return None
        return node

    def _ensure_anchor(self, agent):
        context = self.contexts[agent]
        if context.fixed_key is None:
            return None
        snapshot = context.snapshot
        if (
            self._leaf(snapshot, context.owner) is not None
            and snapshot.prefix_tokens == len(context.fixed_key)
        ):
            return snapshot
        snapshot = self._capture(context.fixed_key, context.owner)
        context.snapshot, context.backed = snapshot, False
        if snapshot is None:
            self.priorities.forget(agent)
        else:
            self.priorities.set_anchor(agent, snapshot, owner=context.owner)
        return snapshot

    def _required_snapshot(self, item):
        """Match the native request's entire reusable prefix, including its suffix."""
        from sglang.srt.mem_cache.radix_cache import RadixKey

        if (
            item.matched_revision == self.priorities.revision
            and (item.snapshot is None or self._leaf(
                item.snapshot, self._owner(item.key),
            ) is not None)
        ):
            return item.snapshot
        req = item.request
        if getattr(req, "output_ids", ()):
            req._refresh_fill_ids()
            tokens = req.full_untruncated_fill_ids
        else:
            tokens = req.origin_input_ids
        key = RadixKey(
            tokens, req.extra_key, cache_salt=req.cache_salt,
            limit=req._compute_max_prefix_len(len(tokens)),
        )
        item.snapshot = self._capture(key, self._owner(item.key))
        item.matched_revision = self.priorities.revision
        return item.snapshot

    def _status(self, snapshot, owner):
        if snapshot is None:
            return "missing"
        leaf = self._leaf(snapshot, owner)
        if leaf is None:
            return "invalid"
        # FULL leaf residency implies resident ancestry; native eviction is leaf-first.
        if leaf.component_data[0].value is not None and leaf.load_back_pending_id is None:
            return "gpu"
        return self.transfers.status(snapshot, owner=owner)

    def _refresh_steps(self):
        if not self._graph_dirty or self.priorities is None:
            return
        started = time.perf_counter_ns()
        graph = self.runtime.graph
        rows, completed, executing, unavailable = [], [], [], []
        for invocation in graph.invocations.values():
            workflow = graph.workflows.get(invocation.workflow_id)
            if workflow is None or workflow.end_ts_ms is not None:
                continue
            identity, state = invocation.invocation_id, invocation.state
            predecessors, mode = (), "all"
            if state is InvocationState.WAIT_JOIN:
                join = graph.joins.get(invocation.join_id)
                if join is not None and not join.satisfied:
                    predecessors = tuple(sorted(join.member_invocation_ids))
                    mode = join.mode.value
            elif state is InvocationState.WAIT_CHILD:
                predecessors = tuple(sorted(invocation.blocking_child_ids))
            rows.append(StepNode(identity, invocation.context_id, predecessors, mode))
            if state.terminal:
                completed.append(identity)
            elif state in (InvocationState.WAIT_MESSAGE, InvocationState.CREATED):
                unavailable.append(identity)
            elif not predecessors:
                executing.append(identity)
        self.steps = (
            StepGraph(rows, information_source="observed_events").agent_steps(
                completed=completed, executing=executing, unavailable=unavailable,
            ) if rows else {}
        )
        self.priorities.set_steps(self.steps)
        self._prefetch_order = tuple(sorted(
            (agent for agent, step in self.steps.items() if step <= 1),
            key=lambda agent: (self.steps[agent], agent),
        ))
        self._graph_dirty = False
        self.counts["graph_updates"] += 1
        self._record_time("graph_update", started)

    def _record_time(self, name, started):
        elapsed = time.perf_counter_ns() - started
        self.counts[name + "_wall_ns"] += elapsed
        self.counts[name + "_max_wall_ns"] = max(
            self.counts[name + "_max_wall_ns"], elapsed,
        )

    def events(self, events):
        with self._authority():
            for event in events:
                if event.kind is Kind.WORKFLOW_END:
                    for agent, context in tuple(self.contexts.items()):
                        if context.owner[0] == event.workflow_id:
                            self._forget_context(agent)
                elif event.kind is Kind.CONTEXT_COMPACT:
                    self._forget_context(event.context_id)
                elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
                    if (
                        event.attributes.get("context_retired") is True
                        or event.attributes.get("subagent_context_close") is True
                    ):
                        invocation = self.runtime.graph.invocations.get(event.invocation_id)
                        self._forget_context(
                            event.context_id or (
                                invocation.context_id if invocation is not None else None
                            ),
                        )
                if event.kind not in (
                    Kind.STRUCTURED_ACTION, Kind.CALL_CENSORED, Kind.TOOL_WAIT_OBSERVATION,
                ):
                    self._graph_dirty = True

    def _forget_context(self, agent):
        if agent not in self.contexts:
            return
        self.priorities.forget(agent)
        self.history.forget(agent)
        self.contexts.pop(agent)
        self._inflight.pop(agent, None)
        for rid, item in tuple(self.requests.items()):
            if item.key.context_id == agent:
                self.requests.pop(rid)
                self.admitted.discard(rid)
        self.counts["contexts_retired"] += 1

    def transfer_completed(self, commit):
        return self.transfers.transfer_completed(commit)

    def _native_growth_reserve(self):
        active = {
            rid: self.requests[rid].request
            for rid in self.admitted if rid in self.requests
        }
        for req in getattr(getattr(self.runtime, "_native_running_batch", None), "reqs", ()):
            if not req.finished():
                active.setdefault(req.rid, req)
        # Native chunk admission already promised the known input, although
        # later chunks have no physical allocation yet. Preserve that promise
        # and one observed decode step without reading final output limits.
        return sum(
            max(
                0, len(req.origin_input_ids) + len(req.output_ids)
                - getattr(getattr(req, "kv", None), "kv_allocated_len", 0),
            ) + 1
            for req in active.values()
        )

    def tick(self, *, now=None):
        if self.cache is None:
            return
        now = time.monotonic() if now is None else now
        with self._authority(), self.transfers.loading_batch():
            self._refresh_steps()
            if now < self._next_tick:
                return
            started = time.perf_counter_ns()
            self._next_tick = now + self.interval_s
            self.counts["maintenance_ticks"] += 1
            for agent, snapshot in tuple(self._inflight.items()):
                context = self.contexts.get(agent)
                if context is None or self._status(snapshot, context.owner) != "loading":
                    self._inflight.pop(agent)
                    self.counts["prefetch_settled_or_invalidated"] += 1
            busy = {
                item.key.context_id for rid, item in self.requests.items()
                if rid in self.admitted
            }
            native_reserve = self._native_growth_reserve()
            self.counts["native_growth_reserve_tokens"] = native_reserve
            self.counts["native_growth_reserve_max_tokens"] = max(
                self.counts["native_growth_reserve_max_tokens"], native_reserve,
            )
            path_checks, copies, reclaim_checks = 0, 0, 0
            groups = {}
            for agent in self._prefetch_order:
                groups.setdefault(self.steps[agent], []).append(agent)
            ordered = []
            for step, agents in groups.items():
                start = self._scan_offsets.get(step, 0) % len(agents)
                ordered.extend(
                    (agent, step, (start + offset) % len(agents))
                    for offset, agent in enumerate(agents[start:] + agents[:start])
                )
            # Settled Host-only targets take precedence over optional backups.
            # Otherwise a full Host pool could consume every path check forever.
            cold, other = [], []
            for entry in ordered:
                context = self.contexts.get(entry[0])
                leaf = self._leaf(context.snapshot, context.owner) if context else None
                (cold if leaf is not None and leaf.component_data[0].value is None
                 else other).append(entry)
            for agent, step, index in cold + other:
                context = self.contexts.get(agent)
                if context is None or agent in busy or agent in self._inflight:
                    continue
                if context.fixed_key is None:
                    continue
                leaf = self._leaf(context.snapshot, context.owner)
                if (
                    leaf is not None and context.backed
                    and leaf.component_data[0].value is not None
                    and leaf.component_data[0].host_value is not None
                    and leaf.load_back_pending_id is None
                ):
                    continue
                if path_checks == self.path_check_limit:
                    self.counts["maintenance_path_bound"] += 1
                    break
                path_checks += 1
                self._scan_offsets[step] = index + 1
                snapshot = self._ensure_anchor(agent)
                if snapshot is None:
                    continue
                path = self.transfers.resolve(snapshot, owner=context.owner)
                if path is None or any(
                    node.load_back_pending_id is not None for node in path
                ):
                    continue
                if all(node.component_data[0].value is not None for node in path):
                    context.backed = all(
                        node.component_data[0].host_value is not None
                        and node.write_through_pending_id is None for node in path
                    )
                    if not context.backed and copies < self.prefetch_limit:
                        if self.cache.token_to_kv_pool_allocator.available_size() < native_reserve:
                            self.counts["backup_native_growth_deferred"] += 1
                            continue
                        issued = self.transfers.backup(snapshot, owner=context.owner)
                        copies += bool(issued)
                    continue
                if len(self._inflight) >= self.prefetch_limit:
                    continue
                required = sum(
                    len(node.key) for node in path if node.component_data[0].value is None
                )
                allocator = self.cache.token_to_kv_pool_allocator
                receipt = self.cache.inc_lock_ref(snapshot.leaf_id).to_dec_params()
                try:
                    free = allocator.available_size()
                    target = required + native_reserve
                    if target > free:
                        # The paper permits prefetch using evictable capacity.
                        # Protect the target while the native heap reclaims peers.
                        if target > free + self.cache.evictable_size():
                            self.counts["prefetch_capacity_declined"] += 1
                            continue
                        _, checked = self.priorities.reclaim_for_prefetch(
                            target - free,
                            max_nodes=max(0, self.path_check_limit - reclaim_checks),
                        )
                        reclaim_checks += checked
                        free = allocator.available_size()
                    issued = self.transfers.restore(
                        snapshot, owner=context.owner,
                        available_tokens=max(0, free - native_reserve),
                    )
                finally:
                    self.cache.dec_lock_ref(snapshot.leaf_id, receipt)
                if issued:
                    self._inflight[agent] = snapshot
                    self.counts["prefetches_started"] += 1
            self.counts["maintenance_path_checks"] += path_checks
            self._record_time("maintenance", started)

    def plan(self, native_order, *, running_batch, adder):
        if getattr(adder, "can_run_list", ()):
            with self._authority():
                self._refresh_steps()
            self.counts["maintenance_deferred_after_native_selection"] += 1
        else:
            self.tick()
        started = time.perf_counter_ns()
        resident, demand = [], []
        with self._authority():
            for req in native_order:
                if getattr(req, "beliefkv_metadata", None) is None:
                    continue
                if len(resident) + len(demand) == 512:
                    break
                key = _request_key(req, known_keys=self.runtime.visible)
                item = self.requests.get(getattr(req, "rid", None))
                if (
                    key is None or item is None or item.key != key
                    or self.runtime.visible.get(key.request_id) != key
                    or self.runtime._terminal(key)
                ):
                    continue
                snapshot = self._required_snapshot(item)
                status = self._status(snapshot, self._owner(key))
                if status == "loading":
                    self.counts["loading_requests_deferred"] += 1
                    continue
                # Native demand recovery remains the liveness path when no
                # speculative capacity is available. It retains native fences.
                (demand if status == "cpu_backup" else resident).append(key)
        self.counts["queue_plans"] += 1
        result = compile_native_prefill_plan(
            resident + demand, semantic_revision=self.runtime.semantic_revision,
        )
        self._record_time("queue_plan", started)
        return result

    def candidate_result(self, req, *, admitted, result):
        if admitted and req.rid in self.requests:
            self.admitted.add(req.rid)
        return False

    def selected(self, batch):
        self.admitted.update(
            req.rid for req in getattr(batch, "reqs", ()) if req.rid in self.requests
        )

    def requeued(self, requests, *, is_retracted):
        for req in requests:
            item = self.requests.get(req.rid)
            key = self.runtime.visible.get(req.rid)
            if item is None or key is None or self._owner(item.key) != self._owner(key):
                raise ValueError("KVFlow requeue changed context ownership")
            item.key, item.request = key, req
            item.snapshot, item.matched_revision = None, -1
            self.admitted.discard(req.rid)
            self.counts["native_retractions" if is_retracted else "native_requeues"] += 1

    def completed(self, batch, completed_ids):
        completed = set(completed_ids)
        with self._authority():
            for req in getattr(batch, "reqs", ()):
                if req.rid not in completed:
                    continue
                item = self.requests.pop(req.rid, None)
                if item is None:
                    continue
                self.admitted.discard(req.rid)
                context = self.contexts.get(item.key.context_id)
                if context is not None and context.owner == self._owner(item.key):
                    context.prior_input = item.input_key
                    self._ensure_anchor(item.key.context_id)
                self.counts["completed"] += 1

    def aborted(self, request_id, *, all_requests=False):
        for rid in tuple(self.requests):
            if all_requests or rid.startswith(request_id):
                self.requests.pop(rid)
                self.admitted.discard(rid)
                self.counts["aborted"] += 1

    def status(self):
        return {
            "policy": self.policy_name, "program_identity": "invocation_id",
            "cache_identity": "workflow_context_epoch", "future_information_used": False,
            "request_transport": "plain_tokens_or_full_prompt_native_radix_session",
            "conversation_session_reconstruction_supported": False,
            "native_capacity_authority_preserved": True,
            "graph_information_source": "observed_events",
            "fixed_prefix_source": "completed_same_context_input_lcp",
            "application_declared_graph_equivalence_claimed": False,
            "control_interval_ms": round(self.interval_s * 1000),
            "prefetch_concurrency_limit": self.prefetch_limit,
            "path_checks_per_interval": self.path_check_limit,
            "native_eviction_checks_per_interval": self.path_check_limit,
            "path_node_limit": self.path_node_limit,
            "speculative_native_capacity_reserve": "unallocated_observed_admitted_input_plus_one_decode_step",
            "contexts": len(self.contexts), "pending_requests": len(self.requests),
            "inflight_prefetches": len(self._inflight),
            "counts": dict(self.counts),
            "cache_counts": dict(self.priorities.counts) if self.priorities else {},
            "transfer_counts": dict(self.transfers.counts) if self.transfers else {},
        }

    def close(self):
        if self.priorities is not None:
            with self._authority():
                self.priorities.close()
                self.transfers.close()
        self.contexts.clear()
        self.requests.clear()
        self.admitted.clear()
        self.history.boundaries.clear()
        self._inflight.clear()
        self._scan_offsets.clear()
