"""Release private KV at the first forecasted tool-wait boundary."""

from collections import OrderedDict
from dataclasses import dataclass
from itertools import islice
import time

from beliefkv.runtime.clock_evidence import SEMANTIC_FRAME_INTERVAL_MS
from beliefkv.runtime.predicted_tool_offload import PredictedToolOffload
from beliefkv.runtime.sglang_v0520_observer import (
    normalize_native_creation_time,
    observe_static_full_mamba_headroom,
)
from beliefkv.runtime.sglang_v0520_physical import ShadowBackupStep


@dataclass(frozen=True)
class _Episode:
    key: object
    call_id: str
    started_ms: float


@dataclass(frozen=True)
class _PrivateTail:
    nodes: tuple[tuple[int, float], ...]
    backups: tuple[ShadowBackupStep, ...]
    backup_tokens: int
    release_tokens: int


class ToolStartOffload(PredictedToolOffload):
    """A bounded early attempt, alongside the existing demand-driven fallback."""

    max_episodes = 512

    def __init__(self, runtime, demand_offload):
        super().__init__(runtime)
        self.demand_offload = demand_offload
        self.seen = OrderedDict()
        self.pending = OrderedDict()
        self.after_completion = {}
        self.backup = None
        self.allocation_offer = None
        self.detail = {}

    def _episode(self, key):
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        if invocation is None or len(invocation.active_tool_calls) != 1:
            return None
        call_id, (_, started_ms) = next(iter(invocation.active_tool_calls.items()))
        return _Episode(key, call_id, started_ms)

    def note_hint(self, hint):
        episode = self._episode(hint.key)
        if episode is None:
            return
        context = hint.key.context_id
        previous = self.seen.get(context)
        if previous == episode:
            return
        if previous is not None:
            self.after_completion.pop(previous.key.request_id, None)
        self.seen[context] = episode
        self.seen.move_to_end(context)
        self.pending[context] = episode
        self.pending.move_to_end(context)
        if len(self.seen) > self.max_episodes:
            old_context, old = self.seen.popitem(last=False)
            self.pending.pop(old_context, None)
            self.after_completion.pop(old.key.request_id, None)
        self.runtime.counts["tool_start_offload_forecast_episodes"] += 1

    def completed(self, completed_ids):
        if not self.after_completion:
            return
        for request_id in completed_ids:
            episode = self.after_completion.pop(request_id, None)
            if episode is None:
                continue
            context = episode.key.context_id
            if self.seen.get(context) == episode:
                self.pending[context] = episode
                self.runtime.counts["tool_start_offload_native_completion_rearms"] += 1

    def _eligible(self, key):
        hint = self.runtime._live_tool_hint(key)
        agents = self.runtime._agent_admission
        item = agents.policy.programs.get(key.invocation_id) if agents.policy is not None else None
        return bool(
            hint is not None and hint.predictor_sha256 == self.runtime.predictor_sha256
            and item is not None and item.acting and self._unprotected_wait(key)
        )

    def _action_detail(self):
        return self.detail

    def _host_tokens(self, headroom):
        runtime = self.runtime
        host = runtime._host_demand_capacity
        tokens = headroom.host_full_free_tokens
        if host is None:
            return tokens
        unit = runtime._residency_pool_bytes[0]
        return min(tokens, host.optional_batch(tokens * unit) // unit)

    def _private_tail(self, key, *, target, host_tokens):
        runtime, cache = self.runtime, self.runtime._native_cache
        anchors = runtime.snapshot_session_anchors(
            cache, context_id=key.context_id, context_epoch=key.context_epoch,
        )
        if anchors is None or anchors.key != key:
            return None
        tree = cache.tree_core
        plans = []
        for leaf_id, leaf_created in dict(anchors.component_leaves).get(0, ()):
            try:
                node = tree.node_by_id(leaf_id)
            except (KeyError, RuntimeError, ValueError):
                continue
            if node.creation_time != leaf_created:
                continue
            nodes, missing = [], []
            release, backup, child = 0, 0, None
            for _ in range(self.max_nodes):
                if (
                    node is None or node is tree.root_node or len(node.children) > self.max_nodes
                    or node.id in cache.ongoing_write_through
                    or node.write_through_pending_id is not None or node.load_back_pending_id is not None
                ):
                    break
                full = node.component_data[0]
                if (
                    full.session_ref != 1
                    or (full.session_ids is not None and full.session_ids != {key.session_id})
                    or full.lock_ref or full.host_lock_ref
                    or any(
                        sibling is not child and sibling.component_data[0].value is not None
                        for sibling in node.children.values()
                    )
                ):
                    break
                if full.value is None:
                    if nodes or full.host_value is None:
                        break
                else:
                    tokens = len(full.value)
                    if not tokens or full.host_value is not None and len(full.host_value) != tokens:
                        break
                    created = normalize_native_creation_time(node.creation_time)
                    if full.host_value is None:
                        if backup + tokens > host_tokens:
                            break
                        backup += tokens
                        missing.append((node.id, created))
                    release += tokens
                    nodes.append((node.id, created))
                    if release >= target:
                        plans.append(_PrivateTail(
                            tuple(nodes),
                            tuple(ShadowBackupStep(
                                key, leaf_id, leaf_created, node_id, created,
                                missing_full_prefix_tokens=backup,
                            ) for node_id, created in reversed(missing)),
                            backup, release,
                        ))
                        break
                child, node = node, node.parent
        return min(plans, key=lambda plan: (plan.backup_tokens, plan.release_tokens), default=None)

    def _quote(self, episode, *, backup_tokens, release_tokens):
        runtime, key = self.runtime, episode.key
        hint = runtime._live_tool_hint(key)
        if (
            hint is None or self._episode(key) != episode
            or runtime._residency_pool_bytes is None
        ):
            return None
        unit = runtime._residency_pool_bytes[0]
        d2h = self.demand_offload._service_ms(backup_tokens * unit, "d2h")
        h2d = self.demand_offload._service_ms(release_tokens * unit, "h2d")
        threshold = d2h + h2d + SEMANTIC_FRAME_INTERVAL_MS
        now = time.monotonic() * 1000.
        probability = hint.release_probability_within(threshold, now_ms=now)
        minimum_survival = runtime.tool_offload_min_survival
        if probability is None:
            if hint.release_cdf or hint.residual_duration_mass:
                return None
            remaining = runtime._tool_offload_remaining(hint, now_ms=now)
            if remaining is None or remaining <= threshold:
                return None
        elif probability > 1. - minimum_survival + 1e-12:
            return None
        return {
            "dispatch_stage": "tool_start",
            "tool_call_id": episode.call_id,
            "tool_start_monotonic_ms": episode.started_ms,
            "remaining_threshold_ms": threshold,
            "backup_service_ms": d2h, "restore_service_ms": h2d,
            "backup_tokens_budgeted": backup_tokens,
            "restore_tokens_budgeted": release_tokens,
            "minimum_remaining_survival": minimum_survival,
            "predicted_completion_probability_within_threshold": probability,
        }

    def _release_tail(self, key, plan, detail):
        runtime = self.runtime
        for node_id, created in plan.nodes:
            runtime._remember_parent_pressure_node(node_id, key, created)
        self.detail = detail
        before = runtime.counts["predicted_tool_offload_freed_component_0"]
        try:
            released = self._evict_backed(key, node_ids=tuple(node for node, _ in plan.nodes))
        finally:
            self.detail = {}
        runtime.counts["tool_start_offload_releases"] += released
        if released:
            tokens = runtime.counts["predicted_tool_offload_freed_component_0"] - before
            runtime.counts["tool_start_offload_released_full_tokens"] += tokens
            runtime.counts["tool_start_offload_partial_releases"] += tokens < plan.release_tokens
        return released

    def _offer_settled_allocation(self, episode, cache):
        runtime = self.runtime
        incumbent = self.allocation_offer
        if incumbent is not None:
            old_cache, old = incumbent
            if old_cache is cache and self._episode(old.key) == old and self._eligible(old.key):
                return
            self.clear_allocation_offer()
        self.allocation_offer = (cache, episode)
        cache._beliefkv_tool_start_settled_shortfall = self._release_for_native_shortfall
        runtime.counts["tool_start_offload_settled_alloc_offers"] += 1

    def clear_allocation_offer(self):
        offer = self.allocation_offer
        if offer is not None:
            offer[0]._beliefkv_tool_start_settled_shortfall = None
            self.allocation_offer = None

    def _release_for_native_shortfall(self, tokens):
        offer = self.allocation_offer
        if offer is None:
            return
        cache, episode = offer
        self.clear_allocation_offer()
        runtime, key = self.runtime, episode.key
        started = time.thread_time_ns()
        try:
            runtime.counts["tool_start_offload_settled_alloc_probes"] += 1
            if (
                type(tokens) is not int or tokens <= 0 or runtime.physical_disabled
                or cache is not runtime._native_cache or self._episode(key) != episode
                or not self._eligible(key)
                or not runtime._agent_admission.policy.programs[key.invocation_id].active
            ):
                return
            plan = self._private_tail(key, target=tokens, host_tokens=0)
            if plan is None or plan.backups:
                runtime.counts["tool_start_offload_settled_alloc_insufficient"] += 1
                return
            detail = self._quote(episode, backup_tokens=0, release_tokens=plan.release_tokens)
            if detail is None:
                runtime.counts["tool_start_offload_settled_alloc_timing_rejected"] += 1
                return
            if self._release_tail(key, plan, {
                **detail, "minimum_release_tokens": tokens,
                "tool_start_release_trigger": "native_prewrite_shortfall",
            }):
                runtime.counts["tool_start_offload_settled_alloc_releases"] += 1
        finally:
            runtime.counts["tool_start_offload_cpu_ns"] += time.thread_time_ns() - started

    def _try_episode(self, episode, *, after_backup, waiting_queue, running_batch):
        runtime, key = self.runtime, episode.key
        agents, cache = runtime._agent_admission, runtime._native_cache
        owner = agents.policy.programs.get(key.invocation_id) if agents.policy is not None else None
        if (
            not after_backup and owner is not None and owner.pending and owner.active
            and agents.latest.get(key.invocation_id) == key
            and self._episode(key) == episode and runtime._live_tool_hint(key) is not None
        ):
            self.after_completion[key.request_id] = episode
            return False
        if self._episode(key) != episode or not self._eligible(key):
            runtime.counts["tool_start_offload_stale_or_protected"] += 1
            return False
        if not owner.active or not (waiting_queue or getattr(running_batch, "reqs", ())):
            runtime.counts["tool_start_offload_no_active_donor_or_demand"] += 1
            return False
        headroom = observe_static_full_mamba_headroom(cache)
        if not headroom.observable:
            return False
        budget = runtime._current_residency_budget(include_evictable=True, headroom=headroom)
        reserve = max(budget.reserve_full_tokens, runtime._native_chunk_input_reserve or 0)
        if budget.source != "native_next_prefill" or reserve <= 0:
            return False
        logical_free = agents.policy.remaining_capacity()
        if (
            headroom.device_full_free_tokens + budget.evictable_full_tokens >= reserve
            and logical_free >= reserve
        ):
            runtime.counts["tool_start_offload_no_pressure"] += 1
            if (
                headroom.device_full_free_tokens < reserve
                and hasattr(cache, "_beliefkv_tool_start_settled_shortfall")
                and self._quote(episode, backup_tokens=0, release_tokens=0) is not None
            ):
                # Let the allocator confirm a write-back shortfall before using a settled copy.
                self._offer_settled_allocation(episode, cache)
            return False
        if self._quote(episode, backup_tokens=0, release_tokens=0) is None:
            runtime.counts["tool_start_offload_timing_rejected"] += 1
            return False

        # Release enough for an actual prefill chunk, or the whole small owner.
        target = min(owner.tokens, runtime._native_chunk_input_reserve or reserve)
        can_backup = (
            not after_backup
            and not runtime.physical_ledger.pending_action_count("PREPARE_HOST")
        )
        host_tokens = self._host_tokens(headroom) if can_backup else 0
        plan = self._private_tail(key, target=target, host_tokens=host_tokens)
        if plan is None:
            runtime.counts["tool_start_offload_no_useful_private_tail"] += 1
            return False
        detail = self._quote(
            episode, backup_tokens=plan.backup_tokens, release_tokens=plan.release_tokens,
        )
        if detail is None:
            runtime.counts["tool_start_offload_round_trip_rejected"] += 1
            return False
        detail.update(
            after_tool_start_backup=after_backup,
            device_free_tokens_before=headroom.device_full_free_tokens,
            device_evictable_tokens_before=budget.evictable_full_tokens,
            logical_free_tokens_before=logical_free,
            native_reserve_tokens=reserve,
            minimum_release_tokens=target,
        )
        if not plan.backups:
            released = self._release_tail(key, plan, detail)
            if released:
                gate = runtime._subagent_materialization
                if gate is not None and gate.pending:
                    gate.capacity_changed()
                if agents._dispatch_pending:
                    agents._admit_free_continuation()
            return released

        def revalidate_steps():
            fresh = observe_static_full_mamba_headroom(cache)
            if fresh.observable and self._private_tail(
                key, target=target, host_tokens=self._host_tokens(fresh),
            ) == plan:
                return plan.backups
            return ()

        issued = runtime.issue_shadow_backup_steps(
            plan.backups[0], source="tool_start", _revalidate_steps=revalidate_steps,
            _authorization=lambda: (
                self._eligible(key) and owner.active
                and self._quote(
                    episode, backup_tokens=plan.backup_tokens, release_tokens=plan.release_tokens,
                ) is not None
            ),
        )
        if not issued:
            return False
        commands = tuple(command for _, command in issued)
        self.backup = (episode, commands)
        runtime.counts["tool_start_offload_backups"] += len(commands)
        if runtime._opportunity_writer is not None:
            runtime._opportunity_writer.record({
                "event": "predicted_tool_offload_backup", "ts_ms": time.time() * 1000.,
                "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
                "context_id": key.context_id, "context_epoch": key.context_epoch,
                "command_ids": commands, **detail,
            })
        return True

    def run(self, waiting_queue, *, running_batch, adder=None):
        if not self.pending and self.backup is None:
            return
        runtime, cache = self.runtime, self.runtime._native_cache
        if (
            runtime.physical_disabled or not getattr(cache, "is_write_back", False)
            or getattr(cache, "tree_components", ()) != (0,)
            or not callable(getattr(cache, "_can_beliefkv_full_demote", None))
        ):
            return
        if self.backup is not None and not self.pending and any(
            runtime.physical_ledger.is_pending(command) for command in self.backup[1]
        ):
            return
        started = time.thread_time_ns()
        try:
            if runtime.event_server is not None:
                runtime.event_server.drain(max_messages=128)
            if runtime.physical_disabled or cache is not runtime._native_cache:
                return
            if adder is not None:
                runtime._observe_native_admission_capacity(running_batch=running_batch, adder=adder)
            if self.backup is not None:
                episode, commands = self.backup
                if not any(runtime.physical_ledger.is_pending(command) for command in commands):
                    self.backup = None
                    if runtime._physical_actions_completed(commands, action="PREPARE_HOST"):
                        if self._try_episode(
                            episode, after_backup=True, waiting_queue=waiting_queue,
                            running_batch=running_batch,
                        ):
                            return
                    else:
                        runtime.counts["tool_start_offload_backup_not_acked"] += 1
            for context in tuple(islice(self.pending, self.max_contexts)):
                episode = self.pending.pop(context, None)
                if episode is None:
                    continue
                runtime.counts["tool_start_offload_probes"] += 1
                if self._try_episode(
                    episode, after_backup=False, waiting_queue=waiting_queue,
                    running_batch=running_batch,
                ):
                    break
        finally:
            runtime.counts["tool_start_offload_cpu_ns"] += time.thread_time_ns() - started
