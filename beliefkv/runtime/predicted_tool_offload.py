"""Proactively release settled private KV during predicted long tool waits."""

from collections import OrderedDict
from itertools import islice
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

    def _eligible(self, key):
        runtime = self.runtime
        hint = runtime._live_tool_hint(key)
        if (
            hint is None or hint.predictor_sha256 != runtime.predictor_sha256
            or len(runtime.graph.invocations[key.invocation_id].active_tool_calls) != 1
            or not runtime._predicted_long_tool_wait(key)
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
            rank = runtime._prepare_step_rank(
                step, headroom, full_pressure=True, mamba_pressure=False,
            )
            if rank is None:
                continue
            issued = runtime.issue_shadow_backup_steps(
                step, source="tool_wait", _selection_candidate=candidate,
                _authorization=lambda: self._eligible(key),
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
                    })
                self.cursor += offset + 1
                return
        self.cursor += self.max_contexts
