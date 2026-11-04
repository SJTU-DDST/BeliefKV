"""Select a ready consumer before releasing a blocked reference agent."""

from dataclasses import asdict
from itertools import islice
import time

from beliefkv.runtime.native_transfer_policy import consumer_input_reserve
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


class ReferenceToolReplacement:
    max_candidates = 8

    def __init__(self, offload):
        self.offload = offload
        self.consumer = None
        self.donor = None
        self.order = ()
        self.next_ms = 0.

    @property
    def enabled(self):
        runtime = self.offload.runtime
        return bool(
            getattr(runtime, "agent_residency_pause", False)
            and getattr(runtime, "tool_offload_before_admission", False)
            and getattr(runtime._agent_admission, "strategy", None) == "thunderagent_reference"
        )

    def demand(self, request, key, *, refresh=False):
        runtime = self.offload.runtime
        agents = runtime._agent_admission
        found = agents._candidate(key.request_id, key, include_first=True)
        if (
            found is None or found[1] is not request
            or not runtime._unallocated_waiting_full_request(key, request)
            or not any(req is request for req in islice(runtime._capacity_waiting_queue, 512))
        ):
            return None
        observed = runtime._read_request_reentry(request, key=key, refresh=refresh)
        cost = runtime._request_restore_cost(request, observed)
        if cost is None:
            if found[0].steps != 1:
                return None
            inputs = len(request.origin_input_ids)
            if observed is None:
                # A first invocation without a checkpoint still has a strict
                # full-input allocation bound. Reserve the entire context and
                # worst-case copy cost; never infer that an old prefix is hot.
                if runtime._residency_pool_bytes is None or not callable(getattr(
                    runtime._native_cache, "inspect_beliefkv_reentry", None,
                )):
                    return None
                observed = {"missing_full_tokens": inputs, "missing_mamba_slots": 0}
                cost = inputs * runtime._residency_pool_bytes[0], 0
            elif (
                observed.get("checkpoint_tokens") == 0
                and observed.get("missing_full_tokens") == 0
                and observed.get("missing_mamba_slots") == 0
            ):
                chunk = runtime._native_chunk_input_reserve
                cost = 0, min(inputs, chunk) if type(chunk) is int and chunk > 0 else inputs
            else:
                return None
        headroom = observe_static_full_mamba_headroom(runtime._native_cache)
        if not headroom.observable or observed.get("missing_mamba_slots") != 0:
            return None
        budget = runtime._current_residency_budget(headroom=headroom)
        if budget.request_slots <= 0:
            return None
        reserve = consumer_input_reserve(
            runtime, budget, cost[1], adder=self.offload._admission_adder,
        )
        needed = observed["missing_full_tokens"] + reserve.total_tokens
        logical = found[0].tokens + agents.policy.buffer_tokens - agents.policy.remaining_capacity()
        return max(0, needed - headroom.device_full_free_tokens), logical, cost[0]

    def permits(self, request, key, donor):
        return self._permitted_demand(request, key, donor) is not None

    def _permitted_demand(self, request, key, donor):
        if self.consumer != (request, key) or donor != self.donor:
            return None
        demand = self.demand(request, key)
        agents = self.offload.runtime._agent_admission
        owner = agents.policy.programs.get(donor.invocation_id)
        if demand is None or owner is None:
            return None
        # A physical release may already have paused A during this same pass.
        credit = owner.tokens + agents.policy.buffer_tokens if owner.active else 0
        return demand if demand[1] <= credit and (demand[0] > 0 or demand[1] > 0) else None

    def _release_plan(self, key, *, advance=True):
        runtime, cache = self.offload.runtime, self.offload.runtime._native_cache
        pending, selected, total = {}, [], 0
        nodes = tuple(runtime._pressure_candidate_groups().get(key, ()))
        start = self.offload.node_cursor % len(nodes) if nodes else 0
        if advance:
            self.offload.node_cursor += self.offload.max_nodes
        for node_id in islice((*nodes[start:], *nodes[:start]), self.offload.max_nodes):
            try:
                node = cache.tree_core.node_by_id(node_id)
                full = node.component_data[0]
                if (
                    runtime._parent_pressure_candidates.get(node_id) == (key, node.creation_time)
                    and full.value is not None and full.host_value is not None
                    and full.session_ref == 1
                    and not any(
                        data.session_ids and data.session_ids != {key.session_id}
                        for data in node.component_data
                    )
                    and len(full.value) == len(full.host_value) == len(node.key)
                    and node.id not in cache.ongoing_write_through
                    and node.write_through_pending_id is None and node.load_back_pending_id is None
                    and len(node.children) <= self.offload.max_nodes
                    and all(
                        data.lock_ref == 0 and data.session_ref <= 1
                        and (data.value is None or data.host_value is not None or component == 2)
                        for component, data in enumerate(node.component_data)
                    )
                ):
                    pending[node_id] = node
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
        # Ancestors become native leaves only after their private descendants
        # are released. This read-only plan grants no physical capacity credit.
        released = set()
        while pending:
            ready = [
                node for node in pending.values()
                if all(child.component_data[0].value is None or child.id in released
                       for child in node.children.values())
            ]
            if not ready:
                break
            for node in ready:
                selected.append(node.id)
                released.add(node.id)
                total += len(node.component_data[0].value)
                pending.pop(node.id)
        return tuple(selected), total

    def _grant(self):
        runtime = self.offload.runtime
        agents = runtime._agent_admission
        request, key = self.consumer
        demand = self.demand(request, key, refresh=True)
        if demand is None or demand[0] > 0 or demand[1] > 0:
            return False
        item, _request = agents._candidate(key.request_id, key, include_first=True)
        before = agents.policy.remaining_capacity()
        join_id = runtime._join_priority_pending.get(key.invocation_id)
        if join_id is not None and not runtime._ready_join_parent(request, key):
            join_id = None
        if not agents._admit_from_free_capacity(
            (item,), event="agent_dispatch_free_capacity_admitted", join_id=join_id,
            choice_keys=self.order, choice_sequence=agents.counts["free_dispatch_passes"],
        ):
            return False
        agents._dispatch_pending.pop(key.invocation_id, None)
        agents.counts["free_continuation_admitted"] += 1
        agents.counts["free_join_continuation_admitted" if join_id else
                      "free_ordinary_continuation_admitted"] += 1
        agents.counts["free_dispatch_new_programs_admitted"] += item.steps == 1
        agents.counts["tool_event_replacement_grants"] += 1
        runtime.semantic_revision += 1
        writer = getattr(runtime, "_opportunity_writer", None)
        if writer is not None:
            writer.record({
                "event": "agent_predicted_tool_replacement",
                "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                "consumer": asdict(key), "donor": asdict(self.donor),
                "remaining_before_tokens": before,
                "remaining_after_tokens": agents.policy.remaining_capacity(),
                "coalescing_window_bypassed": True,
                "physical_capacity_authority": "settled_private_release_then_fresh_native_budget",
                "ordinary_restore_lease": False,
            })
        return True

    def run(self, native_order, *, running_batch, adder):
        runtime, agents = self.offload.runtime, self.offload.runtime._agent_admission
        now = time.monotonic() * 1000.
        if (
            not self.enabled or now < self.next_ms or runtime.physical_disabled
            or not runtime.tool_wait_hints or agents.policy is None
            or not agents._dispatch_pending or runtime._native_cache.tree_components != (0,)
            or not getattr(runtime._native_cache, "is_write_back", False)
            or not all(callable(getattr(runtime._native_cache, name, None)) for name in (
                "_can_beliefkv_full_demote", "_demote",
                "_reclaim_beliefkv_full_preserving_mamba",
            ))
            or adder is None or getattr(adder, "can_run_list", None)
            or getattr(adder, "rem_chunk_tokens", None) == 0
            or type(getattr(adder, "rem_input_tokens", None)) is not int
            or adder.rem_input_tokens <= 0
            or type(getattr(adder, "max_running_requests", None)) is not int
            or len(getattr(running_batch, "reqs", ())) >= adder.max_running_requests
        ):
            return False
        rows = getattr(getattr(runtime._native_cache, "req_to_token_pool", None), "available_size", None)
        if not callable(rows) or rows() <= 0 or len(runtime._join_priority_pending) > agents.max_join_checks:
            return False
        self.next_ms = now + self.offload.interval_ms
        started = time.thread_time_ns()
        if runtime.event_server is not None:
            runtime.event_server.drain(max_messages=128)
        if runtime.physical_disabled:
            return False
        runtime._observe_native_admission_capacity(running_batch=running_batch, adder=adder)
        agents._refresh_streaming_progress()
        pending = tuple(islice(agents._dispatch_pending.values(), self.max_candidates))
        joins = tuple(
            agents.latest[identity] for identity in runtime._join_priority_pending
            if identity in agents.latest
        )
        waiting = {id(req): req for req in islice(native_order, 512)}
        keys = []
        for key in dict.fromkeys((*joins, *pending)):
            found = agents._candidate(key.request_id, key, include_first=True)
            if found is not None and id(found[1]) in waiting:
                keys.append(key)
                if len(keys) == self.max_candidates:
                    break
        if not keys:
            agents.counts["tool_event_replacement_cpu_ns"] += time.thread_time_ns() - started
            return False
        for key in pending:
            agents._dispatch_pending.move_to_end(key.invocation_id)
        self.order = tuple(agents.order_tool_replacement(keys))
        agents.counts["tool_event_replacement_choices"] += 1
        agents.counts["free_dispatch_passes"] += 1
        saved = (
            self.offload.consumer, self.offload._admission_adder,
            self.offload._dispatch_stage, runtime._capacity_waiting_queue,
        )
        self.offload._admission_adder = adder
        self.offload._dispatch_stage = "event_tool_replacement"
        runtime._capacity_waiting_queue = native_order
        try:
            return self._replace()
        finally:
            (
                self.offload.consumer, self.offload._admission_adder,
                self.offload._dispatch_stage, runtime._capacity_waiting_queue,
            ) = saved
            self.consumer = self.donor = None
            self.order = ()
            agents.counts["tool_event_replacement_cpu_ns"] += time.thread_time_ns() - started

    def _replace(self):
        from beliefkv.runtime.predicted_tool_offload import PredictedToolOffload

        controller, runtime = self.offload, self.offload.runtime
        agents = runtime._agent_admission
        donors = tuple(hint.key for hint in runtime.tool_wait_hints.values())
        donor_window_checked = False
        prechecked_donor = None
        for key in self.order:
            request = agents._live_requests.get(key.request_id)
            if request is None:
                continue
            if not self._logical_window_can_fit(key, donors):
                agents.counts["tool_event_replacement_logical_window_skips"] += 1
                continue
            if not donor_window_checked:
                prechecked_donor = self._first_candidate_donor(donors)
                if prechecked_donor is None:
                    agents.counts["tool_event_replacement_no_eligible_donor"] += 1
                    break
                donor_window_checked = True
            self.consumer = controller.consumer = (request, key)
            demand = self.demand(*self.consumer, refresh=True)
            if demand is None or demand[0] <= 0 and demand[1] <= 0:
                continue
            for offset in range(min(len(donors), controller.max_contexts)):
                donor = donors[(controller.cursor + offset) % len(donors)]
                self.donor = donor
                item = agents.policy.programs.get(donor.invocation_id)
                # Reuse this preliminary check once. Eviction and backup
                # authorization still recheck the live wait and protection.
                prechecked = donor == prechecked_donor
                if prechecked:
                    prechecked_donor = None
                if (
                    item is None
                    or not prechecked and not PredictedToolOffload._eligible(controller, donor)
                    or not runtime._agent_observed_blocked_budget_key(donor)
                    or not self.permits(*self.consumer, donor)
                ):
                    continue
                release_order, available = self._release_plan(donor, advance=False)
                selection = None
                if available >= max(1, demand[0]):
                    controller.node_cursor += controller.max_nodes
                    agents.counts["tool_event_replacement_shadow_refresh_skips"] += 1
                else:
                    headroom = observe_static_full_mamba_headroom(runtime._native_cache)
                    selection = runtime._refreshed_shadow_backup_selection(
                        context_id=donor.context_id,
                        host_full_free_tokens=headroom.host_full_free_tokens,
                    )
                    release_order, available = self._release_plan(donor)
                    agents.counts["tool_event_replacement_shadow_refresh_fallbacks"] += 1
                if available >= max(1, demand[0]):
                    if not controller._evict_backed(donor, node_ids=release_order):
                        continue
                    controller.cursor += offset + 1
                    return self._grant()
                if (
                    selection is None or runtime.physical_ledger.pending_action_count("PREPARE_HOST")
                    or not controller._eligible(donor)
                ):
                    continue
                step, candidate = selection
                node = runtime._native_cache.tree_core.node_by_id(step.node_id)
                full = node.component_data[0]
                if full.value is None or len(full.value) + available < max(1, demand[0]):
                    continue
                if not controller._prepare_allowed(step, headroom):
                    continue
                issued = runtime.issue_shadow_backup_steps(
                    step, source="tool_wait", _selection_candidate=candidate,
                    _authorization=lambda: controller._authorize_backup(donor, step),
                )
                if issued:
                    for backed, _command in issued:
                        runtime._remember_parent_pressure_node(
                            backed.node_id, backed.key, backed.creation_time,
                        )
                    runtime.counts["predicted_tool_offload_backup_issued"] += len(issued)
                    agents.counts["tool_event_replacement_backup_attempts"] += 1
                    controller.cursor += offset + 1
                    return False
        agents.counts["tool_event_replacement_no_complete_pair"] += 1
        controller.cursor += controller.max_contexts
        return False

    def _first_candidate_donor(self, donors):
        from beliefkv.runtime.predicted_tool_offload import PredictedToolOffload

        controller, runtime = self.offload, self.offload.runtime
        agents = runtime._agent_admission
        for offset in range(min(len(donors), controller.max_contexts)):
            donor = donors[(controller.cursor + offset) % len(donors)]
            agents.counts["tool_event_replacement_donor_prefilter_checks"] += 1
            if (
                donor.invocation_id in agents.policy.programs
                and PredictedToolOffload._eligible(controller, donor)
                and runtime._agent_observed_blocked_budget_key(donor)
            ):
                return donor
        return None

    def _logical_window_can_fit(self, key, donors):
        agents = self.offload.runtime._agent_admission
        target = agents.policy.programs.get(key.invocation_id)
        if target is None:
            return False
        required = target.tokens + agents.policy.buffer_tokens - agents.policy.remaining_capacity()
        if required <= 0:
            return True
        # Only reject an impossible single-donor replacement. Eligibility,
        # physical capacity and the actual release are still checked later.
        for offset in range(min(len(donors), self.offload.max_contexts)):
            donor = donors[(self.offload.cursor + offset) % len(donors)]
            owner = agents.policy.programs.get(donor.invocation_id)
            if owner is not None and owner.active and (
                owner.tokens + agents.policy.buffer_tokens >= required
            ):
                return True
        return False
