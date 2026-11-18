"""Explicit Oracle experiment for yielding at an actually blocked tool boundary."""

from collections import OrderedDict, deque
from dataclasses import asdict
from itertools import islice
import math
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.tool_boundary_preemption import BOUNDED_KIND, KIND, pair_gain_ms


class ToolBoundaryPreemption:
    max_candidates = 8

    def __init__(self, admission, raw):
        from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle

        self.admission = admission
        self.critical_path = (
            isinstance(admission._work, WorkflowWorkOracle)
            and not admission._work.model_work_only
            and raw.get("admission_order") == "global_ready_remaining_path"
        )
        config = raw.get("tool_boundary_preemption")
        self.bounded = isinstance(config, dict) and config.get("kind") == BOUNDED_KIND
        fields = {"kind", "margin_ms", "cooldown_ms", "ready_wait_protection_ms"}
        if self.bounded:
            fields |= {
                "min_work_ratio", "min_switch_interval_ms", "migration_window_ms",
                "migration_budget_pool_multiples", "native_probe_budget_ms",
                "returned_boundary_ttl_ms",
            }
        if (
            not isinstance(config, dict) or config.get("kind") not in (KIND, BOUNDED_KIND)
            or set(config) != fields
            or not isinstance(admission._work, WorkflowWorkOracle)
            or not (admission._work.model_work_only or self.critical_path)
            or self.critical_path and not self.bounded
            or raw.get("remaining_work_ranking_enabled") is not True
            or raw.get("shortest_work_first_admission") is not True
        ):
            raise ValueError("tool-boundary preemption requires an explicit shortest-work Oracle")
        for name in fields - {"kind"}:
            value = config[name]
            zero_protection = self.critical_path and name == "ready_wait_protection_ms" and value == 0
            if (
                type(value) not in (int, float) or not math.isfinite(value)
                or value <= 0 and not zero_protection
            ):
                raise ValueError("tool-boundary costs and protection intervals must be positive")
        if self.bounded and (
            config["min_work_ratio"] < 1
            or config["min_work_ratio"] == 1 and not self.critical_path
        ):
            raise ValueError("bounded handovers need strict work hysteresis")
        if self.critical_path and config["ready_wait_protection_ms"] != 0:
            raise ValueError("global greedy Oracle excludes ready-wait protection")
        self.config = dict(config)
        self.seconds_per_work_token = admission._work.artifact.model_seconds_per_work_token
        self.waiting = OrderedDict()
        self.cooldowns = {}
        self.preempted = {}
        self.protected = {}
        self.ready_since = OrderedDict()
        self.served = OrderedDict()
        self._records = OrderedDict()
        self.migration_history = deque()
        self.last_switch_ms = float("-inf")
        self.active_plan = None
        self.active_details = None
        self.active_consumer = None
        self.boundaries = {}
        self.last_serviced = {}
        self.prepared_handover = None

    def _rearm(self):
        offload = getattr(self.admission.runtime, "_predicted_tool_offload", None)
        if offload is not None:
            offload.rearm()

    def events(self, events):
        for event in events:
            owner = event.invocation_id
            if event.kind is Kind.TOOL_START:
                self.waiting[owner] = None
                if self.bounded:
                    key = self.last_serviced.get(owner) or self.admission.latest.get(owner)
                    if key is not None:
                        self.boundaries[owner] = {
                            "key": key, "returned_ms": None, "nodes": self._boundary_nodes(key),
                        }
                self._rearm()
            elif event.kind is Kind.TOOL_END:
                token = self.boundaries.get(owner)
                if self.bounded and token is not None:
                    token["returned_ms"] = time.monotonic() * 1000.
                    token["nodes"] = self._boundary_nodes(token["key"]) or token["nodes"]
                    self._rearm()
                else:
                    self.waiting.pop(owner, None)
            elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
                self.forget(owner)
            elif event.kind is Kind.WORKFLOW_END:
                self.cooldowns.pop(event.workflow_id, None)
            if event.kind in (Kind.TOOL_END, Kind.LLM_SUBMIT):
                key = self.admission.latest.get(owner)
                if key is not None:
                    self.registered(key)

    def registered(self, key):
        ready = self.admission._candidate(key.request_id, key, include_first=True) is not None
        if ready and key not in self.served:
            fresh = key not in self.ready_since
            self.ready_since.setdefault(key, time.monotonic() * 1000.)
            if self.bounded and fresh:
                self._rearm()
            while len(self.ready_since) > 512:
                self.ready_since.popitem(last=False)
        debt = self.preempted.get(key.invocation_id)
        if debt is None or debt["ready_since_ms"] is not None:
            return
        if ready:
            debt["ready_since_ms"] = self.ready_since.get(key, time.monotonic() * 1000.)

    def _boundary_nodes(self, key):
        runtime = self.admission.runtime
        members = runtime._pressure_candidate_groups().get(key, ())
        return tuple(
            (node_id, runtime._parent_pressure_candidates[node_id][1])
            for node_id in islice(members, 64)
            if runtime._parent_pressure_candidates.get(node_id, (None,))[0] == key
        )

    def promote_boundary_nodes(self, key):
        runtime = self.admission.runtime
        token = self.boundaries.get(key.invocation_id)
        if token is None or not self.returned_candidate(key) or not runtime._live_tool_boundary_pair(key):
            return
        cache = runtime._native_cache
        if (
            cache.session_refs._session_generations.get(key.session_id) != key.session_generation
            or key.session_id in cache.session_refs._closed_session_ids
        ):
            return
        for node_id, created in token["nodes"]:
            previous = runtime._parent_pressure_candidates.get(node_id)
            if previous is not None and previous not in ((token["key"], created), (key, created)):
                continue
            try:
                node = cache.tree_core.node_by_id(node_id)
                if node.creation_time != created or key.session_id not in node.component_data[0].session_ids:
                    continue
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
            runtime._remember_parent_pressure_node(node_id, key, created)
            self.admission.counts["tool_boundary_previous_epoch_nodes_revalidated"] += 1

    def serviced(self, batch):
        now = time.monotonic() * 1000.
        for request in getattr(batch, "reqs", ()):
            record = self.admission.requests.get(request.rid)
            if record is None:
                continue
            key = record[0]
            self.last_serviced[key.invocation_id] = key
            token = self.boundaries.get(key.invocation_id)
            if token is not None and key != token["key"]:
                self.boundaries.pop(key.invocation_id, None)
                self.waiting.pop(key.invocation_id, None)
            self.served[key] = None
            while len(self.served) > 512:
                self.served.popitem(last=False)
            debt = self.preempted.pop(key.invocation_id, None)
            since = self.ready_since.pop(
                key, debt["ready_since_ms"] if debt is not None else None,
            )
            if (
                since is not None and self.config["ready_wait_protection_ms"] > 0
                and now - since >= self.config["ready_wait_protection_ms"]
            ):
                # Protect the tool wait following this exact service episode.
                # The original forecast-based offloader remains independent.
                self.protected[key.invocation_id] = key
                self.admission.counts["tool_boundary_long_wait_service_protections"] += 1

    def forget(self, owner):
        self.waiting.pop(owner, None)
        self.preempted.pop(owner, None)
        self.protected.pop(owner, None)
        self.boundaries.pop(owner, None)
        self.last_serviced.pop(owner, None)
        if self.prepared_handover is not None and owner in (
            self.prepared_handover[0].invocation_id, self.prepared_handover[1].invocation_id,
        ):
            self.prepared_handover = None
        for table in (self.ready_since, self.served):
            for key in tuple(table):
                if key.invocation_id == owner:
                    table.pop(key)

    def donor_keys(self):
        if self.bounded:
            return self._ordered_donor_keys()
        keys = []
        for owner in tuple(islice(self.waiting, self.max_candidates)):
            self.waiting.move_to_end(owner)
            key = self.admission.latest.get(owner)
            if key is not None and self.donor_candidate(key):
                keys.append(key)
        return tuple(keys)

    def _ordered_donor_keys(self):
        a, candidates, work = self.admission, [], {}
        # Metadata only for at most 64 rotating owners; native guards are
        # checked for just the eight longest current candidates.
        for owner in tuple(islice(self.waiting, 64)):
            self.waiting.move_to_end(owner)
            token = self.boundaries.get(owner)
            if (
                token is not None and token["returned_ms"] is not None
                and time.monotonic() * 1000. - token["returned_ms"]
                > self.config["returned_boundary_ttl_ms"]
            ):
                self.waiting.pop(owner, None)
                self.boundaries.pop(owner, None)
                continue
            key = a.latest.get(owner)
            item = a.policy.programs.get(owner) if a.policy is not None else None
            debt = self.preempted.get(owner)
            continuing = debt is not None and not debt["grant_complete"] and debt["donor"] == key
            if (
                key is None or item is None or not (item.active or continuing)
                or not (item.acting and not item.pending or self.returned_candidate(key))
                or not self.donor_candidate(key)
            ):
                continue
            if key.root_workflow_id not in work:
                prediction = a._work.prediction(key.root_workflow_id)
                work[key.root_workflow_id] = (
                    self.priority_work(prediction) if prediction is not None else -1.
                )
            candidates.append((work[key.root_workflow_id], key))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return tuple(key for _work, key in candidates[:self.max_candidates])

    def priority_work(self, prediction):
        # Preserve the existing millisecond cost arithmetic when ranking by path seconds.
        return (
            prediction.remaining_path_s / self.seconds_per_work_token
            if self.critical_path else prediction.remaining_work
        )

    def migration_allowance(self):
        now = time.monotonic() * 1000.
        window = self.config["migration_window_ms"]
        while self.migration_history and self.migration_history[0][0] <= now - window:
            self.migration_history.popleft()
        budget = int(
            self.admission.policy.capacity_tokens * self.config["migration_budget_pool_multiples"]
        )
        return max(0, budget - sum(tokens for _when, tokens in self.migration_history))

    def switch_due(self):
        return time.monotonic() * 1000. >= (
            self.last_switch_ms + self.config["min_switch_interval_ms"]
        )

    def backup_issued(self, donor, consumer):
        self.prepared_handover = (
            donor, consumer, time.monotonic() * 1000. + self.config["returned_boundary_ttl_ms"],
        )
        self.admission.counts["tool_boundary_backup_service_holds"] += 1

    def holds_request(self, key):
        hold = self.prepared_handover
        if hold is None or hold[0].invocation_id != key.invocation_id:
            return False
        a, runtime = self.admission, self.admission.runtime
        consumer = a._candidate(hold[1].request_id, hold[1], include_first=True)
        live = bool(
            time.monotonic() * 1000. < hold[2] and consumer is not None
            and runtime._unallocated_waiting_full_request(hold[1], consumer[1])
            and (key == hold[0] or self.returned_candidate(key))
            and self.donor_candidate(key)
        )
        if not live:
            self.prepared_handover = None
            a.counts["tool_boundary_backup_service_hold_releases"] += 1
        return live

    def plan_credit(self, consumer, donor):
        if self.active_plan is None or self.active_consumer != consumer or donor not in self.active_plan.donors:
            return None
        if not all(self.donor_candidate(key) for key in self.active_plan.donors):
            return 0
        policy = self.admission.policy
        return sum(
            item.tokens + policy.buffer_tokens
            for key in self.active_plan.donors
            for item in (policy.programs[key.invocation_id],) if item.active
        )

    def returned_candidate(self, key):
        if not self.bounded:
            return False
        token = self.boundaries.get(key.invocation_id)
        if token is None or token["returned_ms"] is None:
            return False
        previous = token["key"]
        if (
            not 0 <= time.monotonic() * 1000. - token["returned_ms"]
            <= self.config["returned_boundary_ttl_ms"]
            or key.context_epoch != previous.context_epoch + 1
            or any(getattr(key, name) != getattr(previous, name) for name in (
                "invocation_id", "root_workflow_id", "context_id", "session_id", "session_generation",
            ))
            or key.request_id == previous.request_id
        ):
            return False
        a, runtime = self.admission, self.admission.runtime
        invocation = runtime.graph.invocations.get(key.invocation_id)
        join = runtime.graph.joins.get(invocation.join_id) if invocation is not None else None
        if join is not None and not join.satisfied:
            return False
        found = a._candidate(key.request_id, key, include_first=True, include_active=True)
        return bool(
            found is not None and found[0].pending
            and runtime._unallocated_waiting_full_request(key, found[1])
            and not any(found[1] is request for request in
                        getattr(runtime._native_running_batch, "reqs", ()))
        )

    def release_authorized(self, key):
        offload = getattr(self.admission.runtime, "_predicted_tool_offload", None)
        pair = getattr(offload, "replacement", None)
        return bool(
            self.bounded and self.active_plan is not None and key in self.active_plan.donors
            and pair is not None and pair.donor == key and pair.consumer is not None
            and pair.consumer[1] == self.active_consumer and self.donor_candidate(key)
        )

    def donor_candidate(self, key):
        a, runtime = self.admission, self.admission.runtime
        item = a.policy.programs.get(key.invocation_id) if a.policy is not None else None
        invocation = runtime.graph.invocations.get(key.invocation_id)
        previous = self.preempted.get(key.invocation_id)
        continuing = bool(
            previous is not None and previous["donor"] == key and not previous["grant_complete"]
        )
        token = self.boundaries.get(key.invocation_id)
        protected = self.protected.get(key.invocation_id)
        waiting = bool(
            invocation is not None and invocation.state is InvocationState.WAIT_TOOL
            and len(invocation.active_tool_calls) == 1 and item is not None
            and item.acting and not item.pending and runtime._agent_observed_blocked_budget_key(key)
        )
        returned = self.returned_candidate(key)
        return bool(
            key.invocation_id in self.waiting and item is not None
            and (item.active or continuing) and not item.marked_for_pause
            and invocation is not None and (waiting or returned)
            and not invocation.blocking_child_ids
            and a.latest.get(key.invocation_id) == key
            and runtime.context_sessions.get(key.context_id) == key
            and not runtime._terminal(key)
            and protected != key
            and not (protected is not None and token is not None and protected == token["key"])
            and (continuing or time.monotonic() * 1000. >= self.cooldowns.get(key.root_workflow_id, 0.))
        )

    def evaluate(
        self, donor, consumer, *, backup_ms, consumer_restore_ms, donor_restore_ms,
        original_forecast_allowed=False,
    ):
        a = self.admission
        started = time.thread_time_ns()
        try:
            if (
                donor.root_workflow_id == consumer.root_workflow_id
                or not self.donor_candidate(donor)
            ):
                return None
            target = a._candidate(consumer.request_id, consumer, include_first=True)
            if target is None or target[0].active or not target[0].pending:
                return None
            previous = self.preempted.get(donor.invocation_id)
            if previous is not None and not previous["grant_complete"] and previous["consumer"] != consumer:
                return None
            # These are the actual synchronous Oracle caches, refreshed by native
            # progress. No graph scan, prompt inference or future tool lookup.
            left = a._work.prediction(donor.root_workflow_id)
            right = a._work.prediction(consumer.root_workflow_id)
            if left is None or right is None or left.evidence != "oracle" or right.evidence != "oracle":
                return None
            if self.bounded:
                if (
                    self.active_plan is None or self.active_consumer != consumer
                    or donor not in self.active_plan.donors
                    or self.priority_work(left)
                    <= self.priority_work(right) * self.config["min_work_ratio"]
                ):
                    return None
                return {
                    **self.active_details,
                    "tool_boundary_original_forecast_allowed": original_forecast_allowed,
                }
            gain = pair_gain_ms(
                left.remaining_work, right.remaining_work,
                model_seconds_per_work_token=self.seconds_per_work_token,
                backup_ms=backup_ms, consumer_restore_ms=consumer_restore_ms,
                donor_restore_ms=donor_restore_ms, margin_ms=self.config["margin_ms"],
            )
            detail = {
                "tool_boundary_preemption": True,
                "tool_boundary_policy": KIND,
                "tool_boundary_surrogate_gain_ms": gain,
                "tool_boundary_donor_remaining_work": left.remaining_work,
                "tool_boundary_consumer_remaining_work": right.remaining_work,
                "tool_boundary_donor_prediction_revision": left.revision,
                "tool_boundary_consumer_prediction_revision": right.revision,
                "tool_boundary_backup_cost_ms": backup_ms,
                "tool_boundary_consumer_restore_cost_ms": consumer_restore_ms,
                "tool_boundary_donor_restore_cost_ms": donor_restore_ms,
                "tool_boundary_margin_ms": self.config["margin_ms"],
                "tool_boundary_current_tool_residual_assumed_ms": 0.,
                "tool_boundary_original_forecast_allowed": original_forecast_allowed,
                "tool_boundary_score_is_not_counterfactual_JCT": True,
            }
            a.counts["tool_boundary_pair_evaluations"] += 1
            a.counts["tool_boundary_pair_positive" if gain > 0 else "tool_boundary_pair_rejected"] += 1
            signature = donor, consumer
            previous = self._records.get(signature)
            state = gain > 0, original_forecast_allowed
            if previous != state:
                self._records[signature] = state
                while len(self._records) > 512:
                    self._records.popitem(last=False)
                writer = getattr(a.runtime, "_opportunity_writer", None)
                if writer is not None:
                    writer.record({
                        "event": "tool_boundary_preemption_pair",
                        "ts_ms": time.time() * 1000., "monotonic_s": time.monotonic(),
                        "donor": asdict(donor), "consumer": asdict(consumer),
                        "allowed_by_surrogate": gain > 0, **detail,
                        "physical_capacity_credit": False,
                    })
            return detail if gain > 0 else None
        finally:
            a.counts["tool_boundary_pair_cpu_ns"] += time.thread_time_ns() - started

    def released(self, key, consumer, *, tokens=0):
        self.preempted.setdefault(key.invocation_id, {
            "donor": key, "consumer": consumer, "ready_since_ms": None, "grant_complete": False,
        })
        self.admission.counts["tool_boundary_physical_release_notifications"] += 1
        if self.bounded and tokens > 0:
            # Charge the observed release and its possible later reload now,
            # including partial handovers which never obtain a consumer grant.
            self.migration_history.append((time.monotonic() * 1000., 2 * tokens))

    def granted(self, donor, consumer):
        debt = self.preempted.get(donor.invocation_id)
        if debt is None or debt["donor"] != donor or debt["consumer"] != consumer:
            raise ValueError("tool-boundary grant has no matching actual physical release")
        debt["grant_complete"] = True
        self.cooldowns[donor.root_workflow_id] = (
            time.monotonic() * 1000. + self.config["cooldown_ms"]
        )
        self.admission.counts["tool_boundary_replacement_grants"] += 1
        if self.bounded:
            self.last_switch_ms = time.monotonic() * 1000.
            if self.prepared_handover is not None and self.prepared_handover[1] == consumer:
                self.prepared_handover = None

    def has_release(self, donor, consumer):
        debt = self.preempted.get(donor.invocation_id)
        return bool(debt is not None and debt["donor"] == donor and debt["consumer"] == consumer)

    def status(self):
        return {
            **self.config, "enabled": True, "oracle_only": True,
            "future_tool_durations_used": self.critical_path,
            "priority_basis": (
                "remaining_model_tool_critical_path_seconds" if self.critical_path
                else "remaining_model_work"
            ),
            "pending_actual_tool_owners": len(self.waiting),
            "old_forecast_offload_and_JOIN_rights_retained": True,
            "preemption_requires_actual_ready_capacity_consumer": True,
            "running_native_requests_preempted": False,
            "returned_tool_boundary_unallocated_successor_supported": self.bounded,
            "surrogate_is_optimal_JCT_bound": False,
        }
