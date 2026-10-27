"""Proactively release settled private KV during predicted long tool waits."""

from collections import OrderedDict
from itertools import islice
import os
import time

from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


class PredictedToolOffload:
    interval_ms = 200.
    max_contexts = 8
    max_nodes = 16

    def __init__(self, runtime):
        self.runtime = runtime
        self.next_ms = 0.
        self.cursor = 0
        self.node_cursor = 0
        self.frontiers = OrderedDict()

    @classmethod
    def from_environment(cls, runtime):
        policy = os.environ.get("BELIEFKV_TOOL_OFFLOAD_POLICY", "fixed")
        if policy not in ("fixed", "demand_cost"):
            raise ValueError("tool offload policy must be fixed or demand_cost")
        return DemandAwareToolOffload(runtime) if policy == "demand_cost" else cls(runtime)

    def _prediction_allows(self, key):
        return self.runtime._predicted_long_tool_wait(key)

    def _eligible(self, key):
        runtime = self.runtime
        hint = runtime._live_tool_hint(key)
        if (
            hint is None or hint.predictor_sha256 != runtime.predictor_sha256
            or len(runtime.graph.invocations[key.invocation_id].active_tool_calls) != 1
            or not self._prediction_allows(key)
        ):
            return False
        agents = runtime._agent_admission
        if agents is not None:
            item = agents.policy.agents.get(key.invocation_id)
            if agents.holds_restore(key) or item is not None and item.in_flight:
                return False
        ticket = runtime._tool_ticket
        return not (
            ticket is not None and ticket.key == key
            or key in runtime._tool_inflight_tickets
            or any(
                lease.key.context_id == key.context_id
                for lease in runtime._prefetch_service_leases.values()
            )
        )

    def _before_eviction(self, key, node):
        return True

    def _after_eviction(self, key, freed):
        pass

    def _prepare_allowed(self, step, headroom):
        return self.runtime._prepare_step_rank(
            step, headroom, full_pressure=True, mamba_pressure=False,
        ) is not None

    def _authorize_backup(self, key, step):
        return self._eligible(key)

    def _action_detail(self):
        return {}

    def _evict_backed(self, key):
        runtime, cache = self.runtime, self.runtime._native_cache
        members = runtime._pressure_candidate_groups().get(key, ())
        if not members:
            return False
        ordered = tuple(members)
        start = self.node_cursor % len(ordered)
        self.node_cursor += self.max_nodes
        pending = list(islice(
            (*ordered[start:], *ordered[:start]), self.max_nodes,
        ))
        frontier = self.frontiers.pop(key, None)
        if frontier is not None and runtime._parent_pressure_candidates.get(frontier[0]) == (
            key, frontier[1],
        ):
            pending.append(frontier[0])
        visited, tracker = set(), {}
        for _ in range(self.max_nodes):
            if not pending or not self._eligible(key):
                break
            node_id = pending.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            record = runtime._parent_pressure_candidates.get(node_id)
            try:
                node = cache.tree_core.node_by_id(node_id)
            except (KeyError, RuntimeError, ValueError):
                continue
            if (
                record != (key, node.creation_time)
                or cache.session_refs._session_generations.get(key.session_id)
                != key.session_generation
                or key.session_id in cache.session_refs._closed_session_ids
                or any(
                    data.session_ids and data.session_ids != {key.session_id}
                    for data in node.component_data
                )
                or not cache._can_beliefkv_full_demote(node, full_session_ref=1)
                or not self._before_eviction(key, node)
            ):
                continue
            # Do not initiate an untagged state backup or wait on DMA here.
            state = node.component_data[2]
            before = dict(tracker)
            if (
                state.value is not None and state.host_value is None
                and state.session_ref > 0
            ):
                if not cache._reclaim_beliefkv_full_preserving_mamba(
                    node, tracker, full_session_ref=1,
                ):
                    continue
            else:
                cache._demote(node.id, tracker)
            freed = {
                int(component): count - before.get(component, 0)
                for component, count in tracker.items()
                if count > before.get(component, 0)
            }
            if not freed:
                continue
            self._after_eviction(key, freed)
            runtime._invalidate_wait_transfer_observation(key)
            runtime.counts["predicted_tool_offload_evicted_nodes"] += 1
            for component, units in freed.items():
                runtime.counts[f"predicted_tool_offload_freed_component_{component}"] += units
            unit_sizes = runtime._residency_pool_bytes
            freed_bytes = (
                freed.get(0, 0) * unit_sizes[0] + freed.get(2, 0) * unit_sizes[1]
                if unit_sizes is not None else None
            )
            if freed_bytes is not None:
                runtime.counts["predicted_tool_offload_freed_bytes"] += freed_bytes
            if runtime._opportunity_writer is not None:
                runtime._opportunity_writer.record({
                    "event": "predicted_tool_kv_evicted",
                    "ts_ms": time.time() * 1000.,
                    "workflow_id": key.root_workflow_id,
                    "invocation_id": key.invocation_id,
                    "context_id": key.context_id, "context_epoch": key.context_epoch,
                    "session_id": key.session_id,
                    "session_generation": key.session_generation,
                    "node_id": node.id, "creation_time": node.creation_time,
                    "freed_units": freed,
                    "freed_bytes": freed_bytes,
                    "evidence": "current_prediction;settled_host;native_unlocked_private_leaf",
                    **self._action_detail(),
                })
            parent = node.parent
            if parent is not None and parent.id in members:
                # An ancestor checked before its leaf was released may now fit.
                visited.discard(parent.id)
                pending.append(parent.id)
                self.frontiers[key] = (parent.id, parent.creation_time)
                self.frontiers.move_to_end(key)
                if len(self.frontiers) > 256:
                    self.frontiers.popitem(last=False)
            else:
                self.frontiers.pop(key, None)
        return any(tracker.values())

    def dispatch(self):
        runtime, cache = self.runtime, self.runtime._native_cache
        now_ms = time.monotonic() * 1000.
        if (
            runtime.physical_disabled or not runtime.enable_prepare_host
            or cache is None or not runtime.tool_wait_hints or now_ms < self.next_ms
        ):
            return
        self.next_ms = now_ms + self.interval_ms
        if not getattr(cache, "is_write_back", False) or not all(
            callable(getattr(cache, method, None)) for method in (
                "_can_beliefkv_full_demote", "_demote",
                "_reclaim_beliefkv_full_preserving_mamba",
            )
        ):
            runtime.counts["predicted_tool_offload_unsupported_cache"] += 1
            return
        if runtime.event_server is not None:
            runtime.event_server.drain(max_messages=128)
        if runtime.physical_disabled:
            return
        keys = tuple(hint.key for hint in runtime.tool_wait_hints.values())
        headroom = None
        for offset in range(min(len(keys), self.max_contexts)):
            key = keys[(self.cursor + offset) % len(keys)]
            if not self._eligible(key):
                self.frontiers.pop(key, None)
                continue
            runtime.counts["predicted_tool_offload_context_checks"] += 1
            if headroom is None:
                headroom = observe_static_full_mamba_headroom(cache)
            selection = runtime._refreshed_shadow_backup_selection(
                context_id=key.context_id,
                host_full_free_tokens=(
                    headroom.host_full_free_tokens if headroom.observable else None
                ),
            )
            if self._evict_backed(key):
                self.cursor += offset + 1
                return
            if (
                selection is None or not headroom.observable
                or runtime.physical_ledger.pending_action_count("PREPARE_HOST")
                or not self._eligible(key)
            ):
                continue
            step, candidate = selection
            if not self._prepare_allowed(step, headroom):
                continue
            issued = runtime.issue_shadow_backup_steps(
                step, source="tool_wait", _selection_candidate=candidate,
                _authorization=lambda: self._authorize_backup(key, step),
            )
            if issued:
                for item, _ in issued:
                    runtime._remember_parent_pressure_node(
                        item.node_id, item.key, item.creation_time,
                    )
                runtime.counts["predicted_tool_offload_backup_issued"] += len(issued)
                if runtime._opportunity_writer is not None:
                    runtime._opportunity_writer.record({
                        "event": "predicted_tool_offload_backup",
                        "ts_ms": time.time() * 1000.,
                        "context_id": key.context_id, "context_epoch": key.context_epoch,
                        "command_ids": tuple(command for _, command in issued),
                        "evidence": "enqueue_only;physical_eviction_requires_settled_host",
                        **self._action_detail(),
                    })
                self.cursor += offset + 1
                return
        self.cursor += self.max_contexts


class DemandAwareToolOffload(PredictedToolOffload):
    """Lend blocked private pages only to an actual capacity-stalled consumer."""

    max_consumers = 8
    fallback_bytes_per_ms = 8_000_000.

    def __init__(self, runtime):
        super().__init__(runtime)
        self.consumer = None
        self.consumer_cursor = 0
        self.evicted_tokens = OrderedDict()
        self.action_detail = {}

    def _action_detail(self):
        return self.action_detail

    def _prediction_allows(self, key):
        hint = self.runtime._live_tool_hint(key)
        now_ms = time.monotonic() * 1000.
        online = self.runtime._online_tool_timing
        return (
            hint is not None
            and hint.remaining_quantile(.1, now_ms=now_ms) is not None
            and (online is None or online.prefetch_supported(
                key.root_workflow_id, key.invocation_id, now_ms=now_ms,
                scope=hint.residual_support_detail.removeprefix("online_").removesuffix("_command"),
            ))
        )

    def _demand(self, request, key, donor=None):
        runtime = self.runtime
        if not runtime._unallocated_waiting_full_request(key, request):
            return None
        observation = runtime._read_request_reentry(request, key=key, refresh=True)
        cost = runtime._request_restore_cost(request, observation)
        if cost is None or not observation["missing_full_tokens"]:
            return None
        agents = runtime._agent_admission
        if agents is not None and not agents.allows(key):
            policy = agents.policy
            owner = policy.agents.get(donor.invocation_id) if donor is not None else None
            target = policy.agents.get(key.invocation_id)
            if (
                agents.blocked_lending == "idle" or owner is None or target is None
                or not owner.active or owner.in_flight or not owner.blocked
                or target.blocked or target.in_flight or not target.pending
                or target.tokens + policy.buffer_tokens
                > policy.remaining_capacity() + owner.tokens + policy.buffer_tokens
            ):
                return None
        headroom = observe_static_full_mamba_headroom(runtime._native_cache)
        if not headroom.observable:
            return None
        budget = runtime._current_residency_budget()
        reserve = max(
            budget.reserve_full_tokens,
            runtime._native_page_size * (
                len(getattr(runtime._native_running_batch, "reqs", ())) + 1
            ),
        )
        needed = observation["missing_full_tokens"] + cost[1] + reserve
        deficit = needed - headroom.device_full_free_tokens
        return (key, deficit, cost[0]) if deficit > 0 else None

    def _live_consumer(self, donor):
        if self.consumer is None:
            return None
        request, key = self.consumer
        if not any(item is request for item in islice(
            self.runtime._capacity_waiting_queue, 512,
        )):
            return None
        return self._demand(request, key, donor)

    def _eligible(self, key):
        return super()._eligible(key) and self._live_consumer(key) is not None

    def _service_ms(self, size_bytes, direction):
        if not size_bytes:
            return 0.
        estimate = (
            self.runtime._estimate_d2h_service if direction == "d2h"
            else self.runtime._estimate_h2d_service
        )(size_bytes, shape="full")
        if estimate is not None and estimate.enqueue_to_submit_p90_ms is not None:
            return estimate.submit_to_ack_p90_ms + estimate.enqueue_to_submit_p90_ms
        self.runtime.counts["tool_offload_cost_cold_service_estimate"] += 1
        # Explicit cold-start engineering bound; not measured PCIe bandwidth.
        return size_bytes / self.fallback_bytes_per_ms

    def _worth_offloading(self, key, *, backup_tokens, release_tokens):
        hint = self.runtime._live_tool_hint(key)
        consumer = self._live_consumer(key)
        if hint is None or consumer is None:
            return False
        unit = self.runtime._residency_pool_bytes
        if unit is None:
            return False
        restore_tokens = self.evicted_tokens.get(key, 0) + release_tokens
        restore = self.runtime._estimate_h2d_service(restore_tokens * unit[0], shape="full")
        load_consumer = self.runtime._estimate_h2d_service(consumer[2], shape="full")
        backup = self.runtime._estimate_d2h_service(backup_tokens * unit[0], shape="full") if backup_tokens else None
        agents = self.runtime._agent_admission
        logical_handover_ms = (
            1000. if agents is not None and not agents.allows(consumer[0]) else 0.
        )
        cold = (
            restore is None or restore.enqueue_to_submit_p90_ms is None
            or load_consumer is None or load_consumer.enqueue_to_submit_p90_ms is None
            or backup_tokens > 0 and (
                backup is None or backup.enqueue_to_submit_p90_ms is None
            )
        )
        threshold = (
            self._service_ms(backup_tokens * unit[0], "d2h")
            + self._service_ms(consumer[2], "h2d")
            + self._service_ms(restore_tokens * unit[0], "h2d")
            + 2 * self.interval_ms
            + logical_handover_ms
        )
        if cold:
            threshold = max(1000., threshold)
        now_ms = time.monotonic() * 1000.
        probability = hint.release_probability_within(threshold, now_ms=now_ms)
        remaining = hint.remaining_quantile(.1, now_ms=now_ms)
        allowed = (
            probability <= .1 if probability is not None
            else remaining is not None and remaining >= threshold
        )
        self.runtime.counts["tool_offload_threshold_us_last"] = int(threshold * 1000)
        self.runtime.counts["tool_offload_cost_rejected"] += not allowed
        self.action_detail = {
            "remaining_threshold_ms": threshold, "cold_transfer_cost": cold,
            "predicted_completion_probability_within_threshold": probability,
            "backup_tokens_budgeted": backup_tokens, "restore_tokens_budgeted": restore_tokens,
            "beneficiary_restore_bytes_budgeted": consumer[2],
            "logical_handover_budget_ms": logical_handover_ms,
            "beneficiary_request_id": self.consumer[1].request_id,
            "beneficiary_context_id": self.consumer[1].context_id,
        }
        return allowed

    def _before_eviction(self, key, node):
        full = node.component_data[0]
        return self._live_consumer(key) is not None and self._worth_offloading(
            key, backup_tokens=0, release_tokens=len(full.value),
        )

    def _after_eviction(self, key, freed):
        self.evicted_tokens[key] = self.evicted_tokens.get(key, 0) + freed.get(0, 0)
        self.evicted_tokens.move_to_end(key)
        while len(self.evicted_tokens) > 256:
            self.evicted_tokens.popitem(last=False)

    def _prepare_allowed(self, step, headroom):
        full = self.runtime._native_cache.tree_core.node_by_id(step.node_id).component_data[0]
        missing = max(
            step.missing_full_prefix_tokens,
            len(full.value) if full.value is not None and full.host_value is None else 0,
        )
        return (
            missing <= headroom.host_full_free_tokens
            and self._worth_offloading(
                step.key, backup_tokens=missing, release_tokens=missing,
            )
        )

    def _authorize_backup(self, key, step):
        return self._eligible(key) and self._prepare_allowed(
            step, observe_static_full_mamba_headroom(self.runtime._native_cache),
        )

    def dispatch(self):
        if time.monotonic() * 1000. < self.next_ms:
            return
        runtime = self.runtime
        if (
            runtime.physical_disabled or runtime._native_cache is None
            or not runtime.enable_prepare_host or not runtime.tool_wait_hints
        ):
            return
        # This extension uses FULL-only capacity. Hybrid state stays on the
        # historical, separately declared path.
        if getattr(runtime._native_cache, "tree_components", ()) != (0,):
            runtime.counts["tool_offload_demand_requires_full_only"] += 1
            return
        waiting = runtime._capacity_waiting_queue
        self.consumer = None
        count = len(waiting)
        for offset in range(min(count, self.max_consumers)):
            request = waiting[(self.consumer_cursor + offset) % count]
            key = runtime._executable_waiting_key(request)
            if key is not None:
                # Logical borrowing is checked against each actual donor later.
                observation = runtime._read_request_reentry(request, key=key)
                if observation is not None and observation.get("missing_full_tokens", 0) > 0:
                    self.consumer = (request, key)
                    break
        self.consumer_cursor += self.max_consumers
        if self.consumer is None:
            self.next_ms = time.monotonic() * 1000. + self.interval_ms
            runtime.counts["tool_offload_no_host_waiting_consumer"] += 1
            return
        for key in tuple(self.evicted_tokens):
            if runtime._live_tool_hint(key) is None:
                self.evicted_tokens.pop(key)
        super().dispatch()
