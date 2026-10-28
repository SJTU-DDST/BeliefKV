"""Advance the physical recovery associated with an observed JOIN budget grant."""

from collections import Counter, OrderedDict
from dataclasses import asdict
from itertools import islice
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.native_transfer_policy import consumer_input_reserve
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


class JoinPhysicalRecovery:
    max_plans = 24
    max_donors = 8
    max_nodes = 64

    def __init__(self, handoff):
        self.handoff = handoff
        self.runtime = handoff.admission.runtime
        self.plans = OrderedDict()
        self.consumer = None
        self._admission_adder = None
        self._dispatch_stage = "before_native_admission"
        self._opportunity = None
        self._required_tokens = 0
        self._active_donors = ()
        self._released = {}
        self._reserved = {}

    def remember(self, key, join_id, donors):
        a = self.handoff.admission
        self.plans[key.invocation_id] = (
            key, join_id, tuple(a.latest[item.identity] for item in donors), 0.,
        )
        self.plans.move_to_end(key.invocation_id)
        while len(self.plans) > self.max_plans:
            self.plans.popitem(last=False)

    def ensure(self, key):
        """A physically cold parent may already have complete logical eligibility."""
        plan = self.plans.get(key.invocation_id)
        if plan is not None and self._same_context(plan[0], key):
            return
        a, runtime = self.handoff.admission, self.runtime
        parent = runtime.graph.invocations.get(key.invocation_id)
        request = a._live_requests.get(key.request_id)
        if parent is None or not (
            a._waiting_on_join(parent, runtime.graph.joins.get(parent.join_id))
            or request is not None and runtime._ready_join_parent(request, key)
        ):
            return
        candidates = []
        for item in islice(a.policy.programs.values(), self.handoff.max_donors):
            donor = runtime.graph.invocations.get(item.identity)
            if (
                donor is not None and donor.state is InvocationState.WAIT_JOIN
                and self.handoff._blocked_donor(item, key)
            ):
                candidates.append(item)
                if len(candidates) == self.max_donors:
                    break
        if candidates:
            join_id = (
                parent.join_id if parent.state is InvocationState.WAIT_JOIN
                else runtime._join_priority_pending.get(key.invocation_id)
            )
            self.remember(key, join_id, candidates)
            a.counts["join_physical_plans_without_logical_handover"] += 1

    @staticmethod
    def _same_context(left, right):
        return (
            left.root_workflow_id, left.invocation_id, left.context_id,
            left.session_id, left.session_generation,
        ) == (
            right.root_workflow_id, right.invocation_id, right.context_id,
            right.session_id, right.session_generation,
        ) and right.context_epoch in (left.context_epoch, left.context_epoch + 1)

    def _eligible(self, key):
        a = self.handoff.admission
        item = a.policy.programs.get(key.invocation_id)
        return bool(
            self.consumer is not None and key in self._active_donors
            and item is not None and a.latest.get(key.invocation_id) == key
            and self.handoff._blocked_donor(item, self.consumer[1])
            # Tool donors remain on the existing dynamic survival/cost path.
            and self.runtime.graph.invocations[key.invocation_id].state is InvocationState.WAIT_JOIN
        )

    def _consumer_live(self):
        request, key = self.consumer
        a = self.handoff.admission
        if request is not None:
            return (
                a.runtime.visible.get(request.rid) == key and a.allows(key)
                and a.runtime._executable_waiting_key(request, key=key) == key
                and a.runtime._ready_join_parent(request, key)
            )
        ticket = a.runtime._join_ticket
        return bool(
            ticket is not None and ticket.key == key
            and a.runtime._live_join_ticket(ticket)
            and a.runtime._join_ticket_has_native_eos(ticket)
            and a.holds_restore(key)
        )

    def _worth_offloading(self, key, **_cost):
        return self._eligible(key) and self._consumer_live()

    def _after_eviction(self, key, freed):
        self._released[key] = self._released.get(key, 0) + freed.get(0, 0)

    def _after_handoff(self, key, reserved):
        self._reserved[key] = self._reserved.get(key, 0) + reserved.get(0, 0)

    def _action_detail(self):
        return {
            "handoff_source": "observed_join_budget_plan",
            "dispatch_stage": self._dispatch_stage,
            "beneficiary_request_id": self.consumer[1].request_id,
        }

    def before_admission(self, candidates, *, running_batch, adder):
        if not self.plans:
            return
        for request in islice(candidates, self.max_donors):
            key = self.runtime.visible.get(getattr(request, "rid", None))
            if key is not None and key.invocation_id in self.plans:
                self.advance(key, request=request, adder=adder, running_batch=running_batch)
                return

    def advance(self, key, *, request=None, adder=None, running_batch=None, opportunity=None):
        runtime = self.runtime
        if key.invocation_id not in self.plans:
            return False
        if runtime.event_server is not None:
            runtime.event_server.drain(max_messages=128)
        plan = self.plans.get(key.invocation_id)
        if (
            plan is None or runtime.physical_disabled
            or getattr(runtime._native_cache, "tree_components", ()) != (0,)
            or not self._same_context(plan[0], key)
        ):
            return False
        now = time.monotonic()
        if now < plan[3]:
            return False
        self.plans[key.invocation_id] = (*plan[:3], now + .2)
        runtime.counts["join_physical_plan_attempts"] += 1
        saved = (
            self.consumer, self._admission_adder, self._dispatch_stage, self._opportunity,
            self._required_tokens, self._active_donors, self._released, self._reserved,
        )
        self.consumer = request, key
        self._admission_adder = adder
        self._dispatch_stage = "before_native_admission" if request is not None else "observed_eos_restore"
        self._opportunity = opportunity
        self._required_tokens = 0
        self._released = {}
        self._reserved = {}
        outcome, missing, observed_free = "no_safe_donor", None, None
        checks = Counter()
        try:
            if not self._consumer_live():
                outcome = "consumer_not_live"
                return False
            headroom = observe_static_full_mamba_headroom(runtime._native_cache)
            if not headroom.observable:
                outcome = "headroom_unobservable"
                return False
            observed_free = headroom.device_full_free_tokens
            budget = runtime._current_residency_budget(headroom=headroom)
            if request is not None:
                observation = runtime._read_request_reentry(request, key=key, refresh=True)
                cost = runtime._request_restore_cost(request, observation)
                if cost is None:
                    outcome = "restore_cost_unavailable"
                    return False
                missing = observation["missing_full_tokens"]
                reserve = consumer_input_reserve(runtime, budget, cost[1], adder=adder).total_tokens
            else:
                if opportunity is None or opportunity.anchors.key != key:
                    outcome = "restore_opportunity_unavailable"
                    return False
                missing, reserve = opportunity.required_full_tokens, budget.reserve_full_tokens
            self._required_tokens = missing + reserve
            if not missing:
                outcome = "no_missing_prefix"
                return False
            if headroom.device_full_free_tokens >= self._required_tokens:
                outcome = "physical_capacity_already_fits"
                return False
            self._active_donors = plan[2]
            changed, checked = False, 0
            for donor_key in islice(self._active_donors, self.max_donors):
                checks["donors_checked"] += 1
                if not self._eligible(donor_key) or not self._consumer_live():
                    donor = runtime.graph.invocations.get(donor_key.invocation_id)
                    reason = (
                        "tool_donor_uses_prediction_path"
                        if donor is not None and donor.state is InvocationState.WAIT_TOOL
                        else "donor_or_consumer_not_live"
                    )
                    checks[reason] += 1
                    continue
                anchors = runtime._snapshot_key_session_anchors(
                    runtime._native_cache, donor_key, reusable_input_tokens=None,
                )
                if anchors is None:
                    checks["donor_anchors_unavailable"] += 1
                    continue
                for node_id, created in dict(anchors.component_leaves).get(0, ()):
                    cache = runtime._native_cache
                    try:
                        node = cache.tree_core.node_by_id(node_id)
                    except (KeyError, RuntimeError, ValueError):
                        checks["donor_node_unavailable"] += 1
                        continue
                    if node.creation_time != created:
                        checks["donor_node_stale"] += 1
                        continue
                    while checked < self.max_nodes and node is not cache.tree_core.root_node:
                        checked += 1
                        checks["nodes_checked"] += 1
                        if not self._eligible(donor_key) or not self._consumer_live():
                            checks["donor_or_consumer_changed"] += 1
                            break
                        if observe_static_full_mamba_headroom(cache).device_full_free_tokens >= self._required_tokens:
                            outcome = "physical_capacity_now_fits"
                            return changed
                        full = node.component_data[0]
                        if (
                            full.session_ids != {donor_key.session_id} or full.session_ref != 1
                            or any(data.host_lock_ref for data in node.component_data)
                        ):
                            checks["donor_shared_or_host_locked"] += 1
                            break
                        if full.value is not None and cache._can_beliefkv_full_demote(node, full_session_ref=1):
                            freed = {}
                            cache._demote(node.id, freed)
                            self._after_eviction(donor_key, freed)
                            runtime._invalidate_wait_transfer_observation(donor_key)
                            changed |= bool(freed.get(0))
                        elif full.value is not None and full.host_value is None:
                            from beliefkv.runtime.layer_handoff import try_handoff

                            if getattr(runtime, "layer_handoff_enabled", False) and try_handoff(self, donor_key, None):
                                outcome = "layer_handoff_submitted"
                                changed = True
                                return True
                            # Multiple small donors use ACK-gated native backup
                            # and reclaim; they do not masquerade as one layer transaction.
                            selection = runtime._refreshed_shadow_backup_selection(
                                context_id=donor_key.context_id, source="join_prepare",
                            )
                            if selection is not None and self._consumer_live() and self._eligible(donor_key):
                                step, candidate = selection
                                manager = runtime._host_demand_capacity
                                if manager is None or manager.ensure(
                                    max(step.missing_full_prefix_tokens, len(full.value)),
                                    protected=(node.id,),
                                ):
                                    issued = runtime.issue_shadow_backup_steps(
                                        step, source="join_prepare", _selection_candidate=candidate,
                                        _authorization=lambda: self._consumer_live() and self._eligible(donor_key),
                                    )
                                    changed |= bool(issued)
                                    if issued:
                                        outcome = "backup_issued_waiting_ack"
                                        return True
                                    checks["backup_not_issued"] += 1
                                else:
                                    checks["host_capacity_unavailable"] += 1
                            else:
                                checks["backup_selection_or_authority_unavailable"] += 1
                            break
                        else:
                            checks[
                                "donor_not_device_resident"
                                if full.value is None else "native_demotion_refused"
                            ] += 1
                            break
                        node = node.parent
            return changed
        except BaseException:
            outcome = "error"
            raise
        finally:
            try:
                released, reserved = sum(self._released.values()), sum(self._reserved.values())
                if outcome != "error":
                    if reserved:
                        outcome = "layer_handoff_submitted"
                    elif released:
                        outcome = "native_pages_released"
                runtime.counts[f"join_physical_plan_outcome_{outcome}"] += 1
                runtime.counts["join_planned_physical_release_tokens"] += released
                runtime.counts["join_planned_reserved_handoff_tokens"] += reserved
                for reason, count in checks.items():
                    runtime.counts[f"join_physical_plan_{reason}"] += count
                writer = runtime._opportunity_writer
                if writer is not None:
                    writer.record({
                        "event": "agent_join_physical_plan_result",
                        "ts_ms": time.time() * 1000., "join_id": plan[1],
                        "target_key": asdict(key), "outcome": outcome,
                        "dispatch_stage": self._dispatch_stage,
                        "observed_device_free_tokens": observed_free,
                        "missing_full_tokens": missing,
                        "required_device_tokens": self._required_tokens,
                        "checks": dict(checks),
                        "released_device_tokens": released,
                        "reserved_handoff_tokens": reserved,
                        "scope": "one bounded attempt; not a migration opportunity or saved time",
                    })
                if (self._released or self._reserved) and writer is not None:
                    writer.record({
                        "event": "agent_join_planned_physical_release",
                        "ts_ms": time.time() * 1000., "join_id": plan[1],
                        "target_key": asdict(key), "required_device_tokens": self._required_tokens,
                        "donors": [
                            {"key": asdict(donor), "released_tokens": tokens}
                            for donor, tokens in self._released.items()
                        ],
                        "reserved_handoff_donors": [
                            {"key": asdict(donor), "reserved_tokens": tokens}
                            for donor, tokens in self._reserved.items()
                        ],
                        "scope": "native free-list release and reserved extents counted separately",
                    })
            finally:
                (
                    self.consumer, self._admission_adder, self._dispatch_stage, self._opportunity,
                    self._required_tokens, self._active_donors, self._released, self._reserved,
                ) = saved

    def forget(self, invocation):
        self.plans.pop(invocation, None)

    def serviced(self, batch):
        if not self.plans:
            return
        a = self.handoff.admission
        for request in getattr(batch, "reqs", ()):
            key = self.runtime.visible.get(request.rid)
            plan = self.plans.get(key.invocation_id) if key is not None else None
            if plan is not None and self._same_context(plan[0], key):
                self.plans.pop(key.invocation_id)
                a.counts["join_physical_plan_native_services"] += 1
