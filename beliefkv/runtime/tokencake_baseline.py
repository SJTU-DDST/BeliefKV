"""TokenCake spatial scheduling bound to native sequential admission.

This is the admission portion of the observed-metadata port, not a registered
full baseline. Lifecycle preservation and the separate paper upload policy
must be bound and physically qualified before registering an experiment arm.
"""

from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, replace
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.tokencake import (
    AgentHistory, TokenCakeMetadata, partition_capacity, request_score,
)
from beliefkv.runtime.paper_baseline import _isolated
from beliefkv.runtime.paper_prefix_transfers import NativePrefixTransfers
from beliefkv.runtime.sglang_v0520_admission import _request_key, compile_native_prefill_plan
from beliefkv.runtime.tokencake_capacity import NativeCapacityNotReady, TokenCakeNativeCapacity


@dataclass
class _Request:
    request: object
    key: object
    arrived: float
    metadata: TokenCakeMetadata
    queued_at: float
    started: float | None = None
    probe: object = None
    retry_capacity: tuple[int, int] | None = None


class TokenCakeAdmissionAdapter:
    author_commit = "1901ebf31fdf63590148e61d5c2f65e9fb7ec010"
    request_limit = 512
    history_limit = 4096
    partition_steps = 500
    affinity_band = 500
    preemption_band = 3000

    def __init__(self, runtime, *, generation_budget, context_limit, clock=time.monotonic):
        _isolated(runtime)
        if (
            type(generation_budget) is not int or generation_budget < 0
            or type(context_limit) is not int or context_limit <= 0
        ):
            raise ValueError("TokenCake requires a separate generation budget and context limit")
        self.runtime, self.clock = runtime, clock
        self.generation_budget, self.context_limit = generation_budget, context_limit
        self.cache = self.transfers = self.capacity = None
        self.requests, self.scores, self.agent_scores = {}, {}, {}
        self.history = OrderedDict()
        self.workflow_started, self.join_groups = {}, {}
        self._first_workflow_at = None
        self._authority_active = False
        self._metadata_dirty = True
        self._step, self.reserve_ratio = 0, .05
        self._completion_epoch = 0
        self._native_steps = False
        self._prepared_waiting = None
        self._last_partition_step = -1
        self._cache_generation = None
        self.counts = Counter()

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
            raise ValueError("TokenCake cannot silently change its native cache")
        if getattr(cache, "disable", True) is not False or getattr(cache, "sidecar_pool_specs", ()):
            raise ValueError("TokenCake requires the common plain FULL-only native cache")
        with self._authority():
            self._bind_native_owners(cache)
            self.cache = cache

    def _bind_native_owners(self, cache):
        self.transfers = NativePrefixTransfers(
            cache, can_mutate=lambda: self._authority_active, max_nodes=1024,
            clock=self.clock,
        )
        self.capacity = TokenCakeNativeCapacity(
            self.transfers, generation_budget=self.generation_budget,
            context_limit=self.context_limit,
        )
        root = cache.tree_core.root_node
        self._cache_generation = root.id, root.creation_time

    def _before_native_cache_reset(self):
        return None

    def _after_native_cache_reset(self):
        return None

    def _check_cache_generation(self):
        if self.cache is None:
            return
        root = self.cache.tree_core.root_node
        if (root.id, root.creation_time) == self._cache_generation:
            return
        if self.capacity.requests or self.capacity.reservations:
            raise ValueError("TokenCake native cache reset discarded active request ownership")
        with self._authority():
            self._before_native_cache_reset()
            plan, total = self.capacity.window.plan, self.capacity.window.total
            self.capacity.close()
            self.transfers.close()
            self._bind_native_owners(self.cache)
            if self.capacity.window.total != total:
                raise ValueError("TokenCake native cache reset changed the registered capacity")
            self.capacity.set_plan(plan)
            self._after_native_cache_reset()
        for item in self.requests.values():
            item.probe = None
        self._prepared_waiting = None
        self._metadata_dirty = True
        self.counts["native_cache_resets"] += 1

    def _history(self, agent_type):
        result = self.history.setdefault(agent_type, AgentHistory())
        self.history.move_to_end(agent_type)
        while len(self.history) > self.history_limit:
            self.history.popitem(last=False)
        return result

    def _start_workflow(self, workflow_id):
        now = self.clock()
        if self._first_workflow_at is None:
            self._first_workflow_at = now
        return self.workflow_started.setdefault(workflow_id, now)

    def _metadata(self, req, key):
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        depth, ancestors = 0, set()
        parent = invocation.parent_invocation_id if invocation is not None else None
        while parent is not None:
            if parent in ancestors:
                raise ValueError("TokenCake observed a cyclic invocation ancestry")
            ancestors.add(parent)
            depth += 1
            record = self.runtime.graph.invocations.get(parent)
            parent = record.parent_invocation_id if record is not None else None
        started = self._start_workflow(key.root_workflow_id)
        # Unavailable future depth, progress and importance remain neutral.
        metadata = TokenCakeMetadata(
            agent_type=invocation.agent_definition_id if invocation is not None else "unknown",
            depth=depth, in_degree=int(depth > 0),
            out_degree=len(invocation.child_invocation_ids) if invocation is not None else 0,
            application_started_at_s=started,
            application_start_offset_s=max(0., started - self._first_workflow_at),
            join_group=self.join_groups.get(key.invocation_id, ""),
        )
        declared = req.beliefkv_metadata.get("tokencake")
        if declared is not None:
            if type(declared) is not dict:
                raise ValueError("TokenCake application metadata must be an object")
            metadata = replace(metadata, **declared)
        if not metadata.agent_type:
            metadata = replace(metadata, agent_type="unknown")
        return metadata

    def registered(self, req, key):
        self._check_cache_generation()
        if self.cache is None or key.request_id in self.requests:
            raise ValueError("TokenCake requires a native cache and unique request identity")
        if len(self.requests) >= self.request_limit:
            raise ValueError("TokenCake live request limit reached")
        now = self.clock()
        self.requests[key.request_id] = _Request(
            req, key, now, self._metadata(req, key), now,
        )
        self.counts["registered"] += 1

    def events(self, events):
        self._metadata_dirty = True
        for event in events:
            if event.kind is Kind.WORKFLOW_START:
                self._start_workflow(event.workflow_id)
            elif event.kind is Kind.JOIN_CREATE:
                for member in event.member_invocation_ids:
                    self.join_groups[member] = event.join_id
            elif event.kind in (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT):
                for member, group in tuple(self.join_groups.items()):
                    if group == event.join_id:
                        del self.join_groups[member]
            elif event.kind is Kind.WORKFLOW_END:
                for rid, item in tuple(self.requests.items()):
                    if item.key.root_workflow_id == event.workflow_id:
                        self._forget(rid)
                workflow = self.runtime.graph.workflows.get(event.workflow_id)
                if workflow is not None:
                    for invocation in workflow.invocation_ids:
                        self.join_groups.pop(invocation, None)
                self.workflow_started.pop(event.workflow_id, None)

    def _record_time(self, name, started):
        elapsed = time.perf_counter_ns() - started
        self.counts[name + "_wall_ns"] += elapsed
        self.counts[name + "_max_wall_ns"] = max(
            self.counts[name + "_max_wall_ns"], elapsed,
        )

    def _update_scores(self):
        now = self.clock()
        if self._metadata_dirty:
            for item in self.requests.values():
                item.metadata = self._metadata(item.request, item.key)
            self._metadata_dirty = False
        self.agent_scores = {
            rid: request_score(item.metadata, item.arrived, now)
            for rid, item in self.requests.items()
        }
        self.scores = self.agent_scores.copy()
        highest = {}
        for rid, item in self.requests.items():
            metadata = item.metadata
            if metadata.application_started_at_s > 0 and metadata.join_group:
                group = item.key.root_workflow_id, metadata.join_group
                highest[group] = max(highest.get(group, 0), self.scores[rid])
        for rid, item in self.requests.items():
            group = item.key.root_workflow_id, item.metadata.join_group
            if group in highest:
                self.scores[rid] = highest[group]

    def _begin_step(self, waiting, *, advance=True):
        self._update_scores()
        self._step += int(advance)
        ledger = self.capacity
        waiting_counts, waiting_time, waiting_scores = Counter(), Counter(), {}
        now = self.clock()
        for req in waiting:
            item = self.requests[req.rid]
            agent = item.metadata.agent_type
            waiting_counts[agent] += 1
            waiting_time[agent] += max(0., now - item.arrived)
            waiting_scores[agent] = max(waiting_scores.get(agent, 0), self.scores[req.rid])
        if not ledger.window.plan.scores or (
            self._step % self.partition_steps == 0
            and self._last_partition_step != self._step
        ):
            importance = {agent: value.importance for agent, value in self.history.items()}
            used = Counter()
            for rid, item in self.requests.items():
                agent = item.metadata.agent_type
                importance[agent] = max(importance.get(agent, 0.), item.metadata.importance)
                claim = ledger.requests.get(rid)
                if claim is not None:
                    used[agent] += sum(units for _, units in claim.private) + sum(
                        ledger.extents[identity].tokens for identity in claim.prefix
                    )
            scores = {
                agent: self.history.get(agent, AgentHistory()).score(
                    value, waiting_counts[agent],
                    waiting_time[agent] / max(1, waiting_counts[agent]),
                ) for agent, value in importance.items()
            }
            total = ledger.window.total
            ledger.refresh()
            usage = 1 - ledger.window.free / total
            if usage >= .75:
                self.reserve_ratio += .05
            elif usage <= .40:
                self.reserve_ratio -= .05
            self.reserve_ratio = min(.30, max(.05, self.reserve_ratio))
            ledger.set_plan(partition_capacity(
                scores, importance, used, total, self.reserve_ratio, .75,
            ))
            self._last_partition_step = self._step
            self.counts["partition_updates"] += 1
        ledger.set_waiting(waiting_scores)
        self._prepared_waiting = frozenset(req.rid for req in waiting)

    def _affinity(self, req):
        if req.rid in self.capacity.requests:
            return 0., 0
        try:
            probe = self.capacity.probe(req)
        except NativeCapacityNotReady:
            return 0., 0
        shared = sum(
            tokens for identity, tokens in probe.extents
            if self.transfers._node(identity[0]).component_data[0].lock_ref > 0
        )
        demand = self.capacity.admission_demand(probe)
        return (shared / max(1, shared + demand), shared) if shared and shared >= demand else (0., 0)

    def _ordered(self, waiting):
        ordered = sorted(waiting, key=lambda req: (
            -self.scores[req.rid], -self.agent_scores[req.rid],
            getattr(req, "priority", 0) or 0, self.requests[req.rid].arrived, req.rid,
        ))
        if self.capacity.window.uncommitted > self.capacity.window.total * .25:
            return ordered
        result, start = [], 0
        while start < len(ordered):
            highest = self.scores[ordered[start].rid]
            stop = start + 1
            while stop < len(ordered) and self.scores[ordered[stop].rid] >= highest - self.affinity_band:
                stop += 1
            result.extend(sorted(ordered[start:stop], key=self._affinity, reverse=True))
            start = stop
        return result

    def plan(self, native_order, *, running_batch, adder):
        self._check_cache_generation()
        started = time.perf_counter_ns()
        with self._authority():
            waiting = [
                req for req in native_order
                if req.rid in self.requests
                and _request_key(req, known_keys=self.runtime.visible) == self.requests[req.rid].key
                and not self.runtime._terminal(self.requests[req.rid].key)
            ]
            if (
                not self._native_steps or self._metadata_dirty
                or frozenset(req.rid for req in waiting) != self._prepared_waiting
            ):
                self._begin_step(waiting, advance=not self._native_steps)
            ordered = self._ordered(waiting)
            plan = compile_native_prefill_plan(
                [self.requests[req.rid].key for req in ordered],
                semantic_revision=self.runtime.semantic_revision,
            )
        self.counts["queue_plans"] += 1
        self._record_time("plan", started)
        return plan

    def allow_candidate(self, req, *, running_batch, adder):
        self._check_cache_generation()
        item = self.requests.get(req.rid)
        if item is None:
            return True
        started = time.perf_counter_ns()
        try:
            with self._authority():
                item.probe = None
                try:
                    probe = self.capacity.probe(req)
                except NativeCapacityNotReady:
                    self.counts["loading_candidates_deferred"] += 1
                    return False
                if item.retry_capacity is not None and self.capacity.requests:
                    self.capacity.refresh()
                    previous_free, completed = item.retry_capacity
                    if (
                        completed == self._completion_epoch
                        and self.capacity.window.uncommitted <= previous_free
                    ):
                        self.counts["native_resume_deferred"] += 1
                        return False
                allowed = self.capacity.allows(
                    probe, agent_type=item.metadata.agent_type, score=self.scores[req.rid],
                )
                if allowed:
                    item.probe = probe
                else:
                    self._history(item.metadata.agent_type).deferrals += 1
                    self.counts["capacity_candidates_deferred"] += 1
                return allowed
        finally:
            self._record_time("guard", started)

    def candidate_result(self, req, *, admitted, result):
        item = self.requests.get(req.rid)
        if item is None:
            return False
        if admitted:
            if item.probe is None:
                raise ValueError("TokenCake native admission bypassed its capacity guard")
            with self._authority():
                self.capacity.stage(
                    req, agent_type=item.metadata.agent_type, score=self.scores[req.rid],
                    probe=item.probe, planned_private_tokens=req.extend_range.length,
                )
            self.counts["admitted"] += 1
            item.retry_capacity = None
        item.probe = None
        return False

    def selected(self, batch):
        if batch is None:
            return
        requests = tuple(req for req in batch.reqs if req.rid in self.requests)
        with self._authority():
            self.capacity.mark_allocated(requests)
            for req in requests:
                item = self.requests[req.rid]
                if req.rid not in self.capacity.requests:
                    raise ValueError("TokenCake selected a request without native admission ownership")
                self.capacity.commit(
                    req, agent_type=item.metadata.agent_type, score=self.scores[req.rid],
                )
                history, metadata = self._history(item.metadata.agent_type), item.metadata
                history.deferrals = max(0, history.deferrals - 1)
                history.importance = metadata.importance
                if item.started is None:
                    item.started = self.clock()
                    history.samples += 1
                    history.input_tokens += len(req.origin_input_ids)
                    history.depth += metadata.depth
                    history.out_degree += metadata.out_degree
                    history.in_degree += metadata.in_degree
                    history.elapsed += metadata.application_elapsed_s
                    history.remaining += metadata.remaining_depth

    def before_release(self, req, *, reason):
        item = self.requests.get(req.rid)
        if item is None:
            return
        with self._authority():
            if self.capacity.release(req.rid):
                self._history(item.metadata.agent_type).preemptions += 1
                self.counts["native_preemptions"] += 1
        item.probe = None
        item.queued_at = self.clock()

    def _released_and_cost(self, req):
        claim = self.capacity.requests.get(req.rid)
        if claim is None:
            return 0, max(0, getattr(req.kv, "kv_committed_len", 0))
        released = sum(units for _, units in claim.private)
        preserved = 0
        for identity in claim.prefix:
            node = self.transfers._node(identity[0])
            extent = self.capacity.extents[identity]
            full = node.component_data[0]
            if full.lock_ref == 1 and extent.users == {req.rid}:
                released += extent.tokens
            if full.host_value is not None and node.write_through_pending_id is None:
                preserved += extent.tokens
        computed = getattr(req.kv, "kv_committed_len", 0)
        return released, max(0, computed - preserved)

    def decode_victim(self, batch, indices):
        self._update_scores()
        with self._authority():
            self.capacity.refresh()
            candidates = [index for index in indices if batch.reqs[index].rid in self.requests]
            if not candidates or len(candidates) != len(indices):
                return None
            lowest = min(self.scores[batch.reqs[index].rid] for index in candidates)
            candidates = [
                index for index in candidates
                if self.scores[batch.reqs[index].rid] <= lowest + self.preemption_band
            ]
            estimates = {
                index: self._released_and_cost(batch.reqs[index]) for index in candidates
            }
            needed = max(
                0, batch.new_tokens_required_next_decode(list(indices))
                - self.cache.token_to_kv_pool_allocator.available_size()
                - self.cache.evictable_size(),
            )
            sufficient = [index for index in candidates if estimates[index][0] >= needed]
            if sufficient:
                candidates = sufficient
            else:
                positive = [index for index in candidates if estimates[index][0] > 0]
                if positive:
                    candidates = positive

            def cost(index):
                req = batch.reqs[index]
                item, (released, computed) = self.requests[req.rid], estimates[index]
                return (
                    len(req.output_ids) >= .9 * self.generation_budget,
                    computed / max(1, released), computed, -released,
                    (self.scores[req.rid], -(getattr(req, "priority", 0) or 0), -item.arrived, req.rid),
                )

            self.counts["native_victim_selections"] += 1
            return min(candidates, key=cost)

    def requeued(self, requests, *, is_retracted):
        for req in requests:
            item = self.requests.get(req.rid)
            if item is None:
                continue
            key = _request_key(req, known_keys=self.runtime.visible)
            if key is None or (
                key.root_workflow_id, key.invocation_id, key.context_id, key.context_epoch,
            ) != (
                item.key.root_workflow_id, item.key.invocation_id,
                item.key.context_id, item.key.context_epoch,
            ):
                raise ValueError("TokenCake native requeue changed program ownership")
            if is_retracted and req.rid in self.capacity.requests:
                raise ValueError("TokenCake retraction bypassed pre-release ownership transfer")
            item.key, item.request, item.probe = key, req, None
            if is_retracted:
                with self._authority():
                    self.capacity.refresh()
                item.retry_capacity = (
                    self.capacity.window.uncommitted, self._completion_epoch,
                )
            self.counts["native_requeues"] += 1

    def _forget(self, rid):
        with self._authority():
            self.capacity.release(rid)
        self.requests.pop(rid, None)
        self.scores.pop(rid, None)
        self.agent_scores.pop(rid, None)

    def completed(self, batch, completed_ids):
        for req in getattr(batch, "reqs", ()):
            item = self.requests.get(req.rid)
            if item is None or req.rid not in completed_ids:
                continue
            if item.started is not None:
                history = self._history(item.metadata.agent_type)
                history.importance = item.metadata.importance
                history.output_tokens += len(req.output_ids)
                history.completed += 1
                history.duration += max(0., self.clock() - item.started)
            was_admitted = req.rid in self.capacity.requests
            self._forget(req.rid)
            self._completion_epoch += int(was_admitted)
            self.counts["completed"] += 1

    def aborted(self, request_id, *, all_requests=False):
        for rid in tuple(self.requests):
            if all_requests or rid.startswith(request_id):
                self._forget(rid)
                self.counts["aborted"] += 1

    def transfer_completed(self, commit):
        return self.transfers.transfer_completed(commit)

    def tick(self, *, now=None):
        self._check_cache_generation()
        if self.capacity is None or not self.requests:
            return
        started = time.perf_counter_ns()
        with self._authority():
            waiting = [
                item.request for rid, item in self.requests.items()
                if rid not in self.capacity.requests and not self.runtime._terminal(item.key)
            ]
            self._native_steps = True
            self._begin_step(waiting)
        self.counts["control_steps"] += 1
        self._record_time("control", started)

    def status(self):
        return {
            "scope": "tokencake_observed_metadata_native_admission_component",
            "author_commit": self.author_commit,
            "program_identity": "invocation_id", "future_information_used": False,
            "native_capacity_authority_preserved": True,
            "complete_baseline": False,
            "generation_budget_source": "separately_declared_serving_budget",
            "generation_budget": self.generation_budget, "context_limit": self.context_limit,
            "scheduler_steps": self._step,
            "partition_adjustment_steps": self.partition_steps,
            "partition_clock": "native_scheduler_step",
            "pending_requests": len(self.requests), "counts": dict(self.counts),
            "capacity_counts": dict(self.capacity.counts) if self.capacity is not None else {},
            "remaining": ["lifecycle_preservation", "separate_paper_upload", "gpu_qualification"],
        }

    def close(self):
        with self._authority():
            if self.capacity is not None:
                self.capacity.close()
            if self.transfers is not None:
                self.transfers.close()
        self.requests.clear()
        self.scores.clear()
        self.agent_scores.clear()


class TokenCakePublicAdapter(TokenCakeAdmissionAdapter):
    policy_name = "tokencake_public_observed_metadata"

    def __init__(
        self, runtime, *, generation_budget, context_limit, running_limit,
        prefill_token_budget, chunked_prefill_tokens, policy_block_tokens,
        eviction_window_tokens, clock=time.monotonic,
    ):
        super().__init__(
            runtime, generation_budget=generation_budget, context_limit=context_limit,
            clock=clock,
        )
        self._temporal_settings = {
            "running_limit": running_limit, "prefill_token_budget": prefill_token_budget,
            "chunked_prefill_tokens": chunked_prefill_tokens,
            "policy_block_tokens": policy_block_tokens, "eviction_window_tokens": eviction_window_tokens,
        }
        if any(type(value) is not int or value <= 0 for value in self._temporal_settings.values()):
            raise ValueError("TokenCake requires explicit positive temporal port parameters")
        self.temporal = None
        self._transfer_error = None

    def attach(self, cache):
        from beliefkv.runtime.tokencake_lifecycle import TokenCakeNativeLifecycle

        super().attach(cache)
        self.temporal = TokenCakeNativeLifecycle(self, **self._temporal_settings)

    def _before_native_cache_reset(self):
        if self.temporal is not None:
            self.temporal.native_reset_succeeded()

    def _after_native_cache_reset(self):
        if self.temporal is not None:
            self.temporal.rebind_transfers()

    def registered(self, req, key):
        super().registered(req, key)
        with self._authority():
            self.temporal.registered(req, self.requests[key.request_id])

    def events(self, events):
        super().events(events)
        if self.temporal is not None:
            with self._authority():
                self.temporal.events(events)

    def completed(self, batch, completed_ids):
        with self._authority():
            for req in getattr(batch, "reqs", ()):
                if req.rid in completed_ids:
                    self.temporal.completed(req)
        super().completed(batch, completed_ids)

    def aborted(self, request_id, *, all_requests=False):
        with self._authority():
            self.temporal.aborted(request_id, all_requests=all_requests)
        super().aborted(request_id, all_requests=all_requests)

    def transfer_completed(self, commit):
        try:
            with self._authority():
                handled = super().transfer_completed(commit)
                self.temporal.transfer_completed(commit)
                return handled
        except Exception as error:
            self._transfer_error = repr(error)
            raise

    def tick(self, *, now=None):
        if self._transfer_error is not None:
            raise ValueError("TokenCake native transfer observer failed: " + self._transfer_error)
        super().tick(now=now)
        if self.temporal is not None:
            started = time.perf_counter_ns()
            with self._authority():
                self.temporal.tick()
            self._record_time("preservation", started)

    def status(self):
        state = super().status()
        state.update(
            policy=self.policy_name,
            scope="public_policy_observed_metadata_native_adaptation",
            runtime_implementation_complete=True,
            graph_information_source="observed_events_or_explicit_application_metadata",
            original_application_declared_graph_available=False,
            temporal_policy="public_host_preservation_then_native_demand_restore",
            predictive_upload_enabled=False,
            temporal_parameters=dict(self._temporal_settings),
            transfer_observer_error=self._transfer_error,
            preservation_counts=dict(self.temporal.counts) if self.temporal is not None else {},
            lifecycle_counts=dict(self.temporal.registry.counts) if self.temporal is not None else {},
            transfer_counts=dict(self.transfers.counts) if self.transfers is not None else {},
            remaining=["native_gpu_qualification", "matched_full_population_comparison"],
        )
        return state

    def close(self):
        if self.temporal is not None:
            with self._authority():
                self.temporal.close()
        super().close()


class TokenCakePaperAdapter(TokenCakePublicAdapter):
    policy_name = "tokencake_paper_observed_metadata"

    def attach(self, cache):
        from beliefkv.runtime.tokencake_paper import TokenCakePaperLifecycle

        TokenCakeAdmissionAdapter.attach(self, cache)
        self.temporal = TokenCakePaperLifecycle(self, **self._temporal_settings)

    def completed(self, batch, completed_ids):
        self.temporal.observe_decode(batch)
        super().completed(batch, completed_ids)

    def allow_candidate(self, req, *, running_batch, adder):
        if super().allow_candidate(req, running_batch=running_batch, adder=adder):
            return True
        key = _request_key(req)
        with self._authority():
            released = self.temporal.release_for_pressure(
                excluded_owner=self.temporal._owner(key) if key is not None else None,
            )
        return released and super().allow_candidate(
            req, running_batch=running_batch, adder=adder,
        )

    def candidate_result(self, req, *, admitted, result):
        changed = super().candidate_result(req, admitted=admitted, result=result)
        with self._authority():
            if admitted:
                self.temporal.consumer_admitted(req)
            elif result == "NO_TOKEN":
                key = _request_key(req)
                changed = self.temporal.release_for_pressure(
                    excluded_owner=self.temporal._owner(key) if key is not None else None,
                )
        return changed

    def decode_pressure(self, batch):
        with self._authority():
            return self.temporal.release_for_pressure()

    def status(self):
        state = super().status()
        state.update(
            scope="paper_strategy_observed_metadata_native_adaptation",
            complete_baseline=True,
            temporal_policy="paper_first_fit_and_deadline_progressive_upload",
            predictive_upload_enabled=True,
            upload_priority="normalized_current_request_score_plus_normalized_deadline_urgency",
            upload_capacity="paper_equations_3_4_and_native_fixed_generation_commitments",
            upload_reservations=len(self.capacity.reservations) if self.capacity is not None else 0,
            observed_decode_tokens_per_second=self.temporal.decode_rate if self.temporal is not None else None,
            serving_generation_budget_source="separately_declared_not_recorded_output_length",
            pressure_release="optional_upload_reservation_before_native_request_preemption",
            remaining=["matched_full_population_comparison", "paired_repetitions"],
        )
        return state
