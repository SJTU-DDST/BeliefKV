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
        self._shadow_misses = None

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
        boundary = self.offload._boundary_policy()
        if boundary is not None and boundary.bounded:
            group_credit = boundary.plan_credit(key, donor)
            if group_credit is not None:
                credit = group_credit
                if not owner.active and demand[0] <= 0:
                    return None
        return demand if demand[1] <= credit and (demand[0] > 0 or demand[1] > 0) else None

    def _release_plan(self, key, *, advance=True, max_nodes=None):
        runtime, cache = self.offload.runtime, self.offload.runtime._native_cache
        limit = self.offload.max_nodes if max_nodes is None else max_nodes
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("release planning requires a bounded node window")
        pending, selected, total = {}, [], 0
        nodes = tuple(runtime._pressure_candidate_groups().get(key, ()))
        start = self.offload.node_cursor % len(nodes) if nodes else 0
        if advance:
            self.offload.node_cursor += limit
        for node_id in islice((*nodes[start:], *nodes[:start]), limit):
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
                    and len(node.children) <= limit
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

    def _grant(self, *, blocked_donor=None, native_fit=None):
        runtime = self.offload.runtime
        agents = runtime._agent_admission
        request, key = self.consumer
        demand = self.demand(request, key, refresh=True)
        if demand is None or demand[0] > 0:
            return False
        if blocked_donor is None and demand[1] > 0:
            return False
        if blocked_donor is not None and demand[1] <= 0:
            return False
        item, _request = agents._candidate(key.request_id, key, include_first=True)
        before = agents.policy.remaining_capacity()
        join_id = runtime._join_priority_pending.get(key.invocation_id)
        if join_id is not None and not runtime._ready_join_parent(request, key):
            join_id = None
        if not agents._admit_from_free_capacity(
            (item,), event="agent_dispatch_free_capacity_admitted", join_id=join_id,
            choice_keys=self.order, choice_sequence=agents.counts["free_dispatch_passes"],
            **({"blocked_donor": blocked_donor} if blocked_donor is not None else {}),
        ):
            return False
        agents._dispatch_pending.pop(key.invocation_id, None)
        agents.counts["free_continuation_admitted"] += 1
        agents.counts["free_join_continuation_admitted" if join_id else
                      "free_ordinary_continuation_admitted"] += 1
        agents.counts["free_dispatch_new_programs_admitted"] += item.steps == 1
        agents.counts["tool_event_replacement_grants"] += 1
        if blocked_donor is not None:
            agents.counts["tool_event_replacement_logical_only_grants"] += 1
        boundary = self.offload._boundary_policy()
        boundary_choice = bool(boundary is not None and boundary.has_release(self.donor, key))
        boundary_donors = (
            tuple(dict.fromkeys((
                *boundary.active_plan.donors,
                *(debt["donor"] for debt in boundary.preempted.values()
                  if debt["consumer"] == key and not debt["grant_complete"]),
            )))
            if boundary_choice and boundary.bounded and boundary.active_plan is not None
            else (self.donor,)
        )
        if boundary is not None and boundary_choice:
            for donor in boundary_donors:
                if boundary.has_release(donor, key):
                    boundary.granted(donor, key)
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
                "physical_capacity_authority": (
                    "fresh_native_budget_without_demotion" if blocked_donor is not None
                    else "settled_private_release_then_fresh_native_budget"
                ),
                "ordinary_restore_lease": False,
                **({
                    "logical_only": True, "physical_capacity_credit": False,
                    "released_device_tokens": 0, "released_host_tokens": 0,
                    "logical_deficit_tokens": demand[1], "native_fit": native_fit,
                } if blocked_donor is not None else {}),
                **({
                    "tool_boundary_preemption": True,
                    "tool_boundary_original_forecast_allowed": self.offload.action_detail.get(
                        "tool_boundary_original_forecast_allowed",
                    ) if len(boundary_donors) == 1 else None,
                    **({
                        "tool_boundary_policy": boundary.config["kind"],
                        "tool_boundary_donors": [asdict(donor) for donor in boundary_donors],
                        "tool_boundary_surrogate_gain_ms": boundary.active_plan.gain_ms,
                    } if boundary.bounded and boundary.active_plan is not None else {}),
                } if boundary_choice else {}),
            })
        return True

    def _logical_handoff_guard(self, key, now):
        runtime = self.offload.runtime
        agents = runtime._agent_admission
        gate = getattr(runtime, "_subagent_materialization", None)
        guard = getattr(agents, "_ready_service_guard", None)
        if (
            not getattr(gate, "include_root_entries", False)
            or agents._join_handoff is None or guard is None or not guard.fit_after_s
            or guard.target not in (None, key) or guard.fit_target is not None
            or key in guard.fit_attempted
            or agents.policy.programs[key.invocation_id].steps <= 1
            or gate.awaiting_first_prefill(key)
        ):
            return None
        entry = guard.since.get(key.invocation_id)
        if entry is None or entry[0] != key or now < entry[1] + guard.fit_after_s:
            return None
        return guard

    def _grant_without_demotion(self, guard, request, key, donor):
        runtime = self.offload.runtime
        agents = runtime._agent_admission
        agents.counts["tool_event_replacement_logical_only_probes"] += 1
        native = guard._native_fit(key, request)
        if native is None or native["first_chunk_and_restore_tokens"] > native["device_free_tokens"]:
            agents.counts["tool_event_replacement_logical_only_no_native_fit"] += 1
            return False
        if not runtime._agent_blocked_prefix_backed(donor, observed_blocked=True):
            agents.counts["tool_event_replacement_logical_only_no_settled_backing"] += 1
            return False
        if not self.offload._candidate_donor_eligible(donor):
            return False
        if not self._grant(blocked_donor=donor, native_fit=native):
            return False
        guard.fit_attempted.add(key)
        return True

    def run(self, native_order, *, running_batch, adder):
        runtime, agents = self.offload.runtime, self.offload.runtime._agent_admission
        now = time.monotonic() * 1000.
        if (
            not self.enabled or now < self.next_ms or runtime.physical_disabled
            or agents.policy is None
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
        boundary = self.offload._boundary_policy()
        if not runtime.tool_wait_hints and not (boundary is not None and boundary.waiting):
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
        self.order = keys
        agents.counts["tool_event_replacement_choices"] += 1
        agents.counts["free_dispatch_passes"] += 1
        saved = (
            self.offload.consumer, self.offload._admission_adder,
            self.offload._dispatch_stage, runtime._capacity_waiting_queue,
        )
        self.offload._admission_adder = adder
        self.offload._dispatch_stage = "event_tool_replacement"
        runtime._capacity_waiting_queue = native_order
        self._shadow_misses = {}
        try:
            return self._replace()
        finally:
            (
                self.offload.consumer, self.offload._admission_adder,
                self.offload._dispatch_stage, runtime._capacity_waiting_queue,
            ) = saved
            self.consumer = self.donor = None
            self.order = ()
            self._shadow_misses = None
            agents.counts["tool_event_replacement_cpu_ns"] += time.thread_time_ns() - started

    def _shadow_signature(self, donor, headroom):
        runtime = self.offload.runtime
        return (
            id(runtime._native_cache), headroom, runtime.semantic_revision,
            runtime.graph.graph_version, runtime._context_sessions_revision,
            runtime.counts["parent_pressure_index_updates"],
            runtime.physical_ledger.pending_count,
            id(runtime.tool_wait_hints.get(donor.context_id)),
        )

    def _shadow_selection(self, donor, headroom, source):
        runtime, agents = self.offload.runtime, self.offload.runtime._agent_admission
        misses = self._shadow_misses
        # A tool-boundary capture can depend on its selected consumer. Only
        # ordinary tool-wait misses are shared inside this synchronous pass.
        reusable = misses is not None and source == "tool_wait"
        if reusable and misses.get(donor) == self._shadow_signature(donor, headroom):
            runtime.shadow_candidate = None
            agents.counts["tool_event_replacement_shadow_misses_reused"] += 1
            return None
        agents.counts["tool_event_replacement_shadow_probes"] += 1
        selection = runtime._refreshed_shadow_backup_selection(
            context_id=donor.context_id, host_full_free_tokens=headroom.host_full_free_tokens,
            **({"source": source} if source != "tool_wait" else {}),
        )
        if reusable and selection is None:
            misses[donor] = self._shadow_signature(donor, headroom)
        return selection

    def _replace(self):
        controller, runtime = self.offload, self.offload.runtime
        agents = runtime._agent_admission
        boundary = self.offload._boundary_policy()
        if boundary is not None:
            self.order = tuple(agents.order_tool_replacement(self.order))
            if boundary.bounded:
                from beliefkv.runtime.tool_boundary_schedule import replace_at_tool_boundary

                result = replace_at_tool_boundary(self, boundary)
                if result is not None:
                    return result
        donors = controller._replacement_donors()
        donor_window_checked = False
        logical_handoff_probed = False
        prechecked_donor = None
        if boundary is None:
            # An impossible pair needs neither Oracle path queries nor a
            # prediction worker readout. Reuse only this pass's donor check.
            if not any(self._logical_window_can_fit(key, donors) for key in self.order):
                agents.counts["tool_event_replacement_logical_window_skips"] += len(self.order)
                self.order = ()
            else:
                prechecked_donor = self._first_candidate_donor(donors)
                donor_window_checked = True
                if prechecked_donor is None:
                    agents.counts["tool_event_replacement_no_eligible_donor"] += 1
                    self.order = ()
                else:
                    self.order = tuple(agents.order_tool_replacement(self.order))
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
            guard = (
                self._logical_handoff_guard(key, time.monotonic())
                if boundary is None and not logical_handoff_probed and demand[0] == 0 else None
            )
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
                    or not prechecked and not controller._candidate_donor_eligible(donor)
                    or not runtime._agent_observed_blocked_budget_key(donor)
                    or not self.permits(*self.consumer, donor)
                ):
                    continue
                if guard is not None:
                    logical_handoff_probed = True
                    if self._grant_without_demotion(guard, request, key, donor):
                        controller.cursor += offset + 1
                        return True
                    guard = None
                index_revision = runtime.counts["parent_pressure_index_updates"]
                semantic_revision = runtime.semantic_revision
                boundary = controller._boundary_policy()
                backup_source = (
                    "tool_boundary" if boundary is not None and boundary.donor_candidate(donor)
                    else "tool_wait"
                )
                release_order, available = self._release_plan(donor, advance=False)
                selection = None
                if available >= max(1, demand[0]):
                    controller.node_cursor += controller.max_nodes
                    agents.counts["tool_event_replacement_shadow_refresh_skips"] += 1
                else:
                    headroom = observe_static_full_mamba_headroom(runtime._native_cache)
                    selection = self._shadow_selection(donor, headroom, backup_source)
                    if (
                        runtime.counts["parent_pressure_index_updates"] == index_revision
                        and runtime.semantic_revision == semantic_revision
                    ):
                        # Shadow capture only reads native state and registers
                        # candidates. With no registration/event, keep this
                        # same-call plan; actual release still revalidates KV.
                        controller.node_cursor += controller.max_nodes
                        agents.counts["tool_event_replacement_release_plan_reuses"] += 1
                    else:
                        release_order, available = self._release_plan(donor)
                    agents.counts["tool_event_replacement_shadow_refresh_fallbacks"] += 1
                if available >= max(1, demand[0]):
                    if self._shadow_misses is not None:
                        self._shadow_misses.clear()
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
                if self._shadow_misses is not None:
                    self._shadow_misses.clear()
                if not controller._prepare_allowed(step, headroom):
                    continue
                issued = runtime.issue_shadow_backup_steps(
                    step, source=backup_source, _selection_candidate=candidate,
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
        controller, runtime = self.offload, self.offload.runtime
        agents = runtime._agent_admission
        for offset in range(min(len(donors), controller.max_contexts)):
            donor = donors[(controller.cursor + offset) % len(donors)]
            agents.counts["tool_event_replacement_donor_prefilter_checks"] += 1
            if (
                donor.invocation_id in agents.policy.programs
                and controller._candidate_donor_eligible(donor)
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
