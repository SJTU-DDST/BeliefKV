"""Compose agent working-set admission with BeliefKV's native migration paths."""

from collections import Counter
import time

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.agent_working_set import AgentWorkingSet


class AgentAdmission:
    def __init__(
        self, runtime, *, strategy="working_set", coadmit_join_children=False,
        blocked_lending="idle",
        workflow_entry_min_ready=0, tool_loan_return=False,
        recall_selection="largest",
        join_dependency_admission=False, blocked_budget_proof="predicted_pressure",
    ):
        self.runtime = runtime
        self.policy: AgentWorkingSet | None = None
        self.strategy = strategy
        self.coadmit_join_children = coadmit_join_children
        if blocked_lending not in ("idle", "backed_demand", "backed_demand_recall") or (
            blocked_lending != "idle" and strategy != "thunderagent_retained"
        ):
            raise ValueError("backed blocked lending requires retained agent admission")
        self.blocked_lending = blocked_lending
        if type(join_dependency_admission) is not bool or (
            join_dependency_admission and strategy != "thunderagent_retained"
        ):
            raise ValueError("JOIN-dependent admission requires retained agent eligibility")
        self.join_dependency_admission = join_dependency_admission
        if blocked_budget_proof not in ("predicted_pressure", "observed_blocked") or (
            blocked_budget_proof != "predicted_pressure" and blocked_lending == "idle"
        ):
            raise ValueError("observed blocked budget proof requires backed lending")
        self.blocked_budget_proof = blocked_budget_proof
        if type(tool_loan_return) is not bool or (
            tool_loan_return and blocked_lending != "backed_demand_recall"
        ):
            raise ValueError("tool loan return requires recorded blocked lending")
        self.tool_loan_return = tool_loan_return
        if recall_selection not in ("largest", "cold_fit") or (
            recall_selection != "largest" and blocked_lending != "backed_demand_recall"
        ):
            raise ValueError("recall selection requires recorded blocked lending")
        self.recall_selection = recall_selection
        if (
            type(workflow_entry_min_ready) is not int or not 0 <= workflow_entry_min_ready <= 256
            or workflow_entry_min_ready and strategy != "thunderagent_retained"
        ):
            raise ValueError("workflow entry requires retained agent admission")
        self.workflow_entry_min_ready = workflow_entry_min_ready
        if strategy not in (
            "working_set", "thunderagent", "thunderagent_retained", "thunderagent_workflow",
        ):
            raise ValueError("unsupported agent admission strategy")
        self.requests, self.latest, self.workflows = {}, {}, {}
        self._live_requests = {}
        self.tool_waits = {}
        self.next_tick = 0.
        self._next_progress_refresh = 0.
        self.restore_backoff = {}
        self.counts = Counter()

    def attach(self, cache):
        capacity = getattr(getattr(cache, "token_to_kv_pool_allocator", None), "size_full", None)
        if getattr(cache, "tree_components", ()) != (0,):
            raise ValueError("agent working-set admission currently requires FULL-only KV")
        if self.policy is not None:
            raise ValueError("agent working-set capacity cannot silently change")
        if self.strategy == "thunderagent_workflow":
            from beliefkv.policy.workflow_agent_service import WorkflowAgentMigration

            self.policy = WorkflowAgentMigration(capacity)
        elif self.strategy in ("thunderagent", "thunderagent_retained"):
            from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration

            self.policy = ThunderAgentMigration(
                capacity, retain_blocked=self.strategy == "thunderagent_retained",
                coadmit_join_children=self.coadmit_join_children,
                recall_join_loans=self.blocked_lending == "backed_demand_recall",
                workflow_entry_min_ready=self.workflow_entry_min_ready,
                recall_tool_loans=self.tool_loan_return,
                recall_selection=self.recall_selection,
                recall_urgent=(
                    self._recall_urgent if self.recall_selection == "cold_fit" else None
                ),
                observe_recall=(
                    self._observe_recall
                    if getattr(self.runtime, "_opportunity_writer", None) is not None else None
                ),
                admission_dependency=(
                    self._admission_dependency if self.join_dependency_admission else None
                ),
                observe_lending=(
                    self._observe_lending if self.join_dependency_admission
                    and getattr(self.runtime, "_opportunity_writer", None) is not None else None
                ),
            )
        else:
            self.policy = AgentWorkingSet(capacity)

    def _observe_recall(self, detail):
        self.runtime._opportunity_writer.record({
            "event": "agent_loan_recall_decision", "ts_ms": time.time() * 1000.,
            **detail,
            "native_capacity_authority_preserved": True,
            "semantics": "Executed logical eligibility return; not physical KV ownership, freed pages or saved H2D.",
        })

    def _recall_urgent(self, invocation):
        key = self.latest.get(invocation)
        return bool(key is not None and self.runtime._last_join_child_has_waiting_parent(key))

    def _admission_dependency(self, invocation):
        """Rank only necessary members of a currently observed blocked ALL-JOIN."""
        child = self.runtime.graph.invocations.get(invocation)
        if child is None or child.state.terminal:
            return 2
        rank = 2
        for identity in self.runtime._join_by_invocation.get(invocation, ()):
            join = self.runtime.graph.joins.get(identity)
            if (
                join is None or join.satisfied or join.mode.value != "all"
                or join.workflow_id != child.workflow_id
                or invocation not in join.member_invocation_ids - join.completed_member_ids
            ):
                continue
            if any(
                (parent := self.runtime.graph.invocations.get(waiter)) is not None
                and parent.state is InvocationState.WAIT_JOIN and parent.join_id == identity
                and parent.workflow_id == child.workflow_id and not parent.active_tool_calls
                and not parent.blocking_child_ids
                for waiter in join.waiter_invocation_ids
            ):
                rank = min(rank, int(len(join.member_invocation_ids - join.completed_member_ids) > 1))
        return rank

    def _observe_lending(self, detail):
        self.runtime._opportunity_writer.record({
            "event": "agent_blocked_budget_lent", "ts_ms": time.time() * 1000.,
            **detail, "backing_proof": self.blocked_budget_proof,
            "native_capacity_authority_preserved": True,
            "semantics": "Executed logical budget handover; not physical eviction or freed-page evidence.",
        })

    def registered(self, req, key):
        if self.policy is None or key.request_id in self.requests:
            raise ValueError("agent admission requires a cache and unique request")
        old = self.latest.get(key.invocation_id)
        if old is not None and (
            old.root_workflow_id, old.context_id,
        ) != (key.root_workflow_id, key.context_id):
            raise ValueError("an agent cannot change workflow or context identity")
        if key.invocation_id not in self.policy.agents:
            self._refresh_streaming_progress()
        critical = key.invocation_id in self.runtime._join_priority_pending
        workflow = (
            {"workflow_id": key.root_workflow_id}
            if self.strategy == "thunderagent_workflow" or self.workflow_entry_min_ready else {}
        )
        self.policy.arrive(
            key.invocation_id, len(req.origin_input_ids),
            now=time.monotonic(), critical=critical, **workflow,
        )
        if old is not None and (old.session_id, old.session_generation) != (
            key.session_id, key.session_generation,
        ):
            self.policy.release_restore(key.invocation_id)
        self.requests[key.request_id] = key
        self._live_requests[key.request_id] = req
        self.latest[key.invocation_id] = key
        self.workflows[key.invocation_id] = key.root_workflow_id
        self.tool_waits.pop(key.invocation_id, None)
        self.counts["registered"] += 1

    def allows(self, key):
        return bool(
            self.policy is not None and key is not None
            and self.requests.get(key.request_id) == key
            and self.policy.allowed(key.invocation_id)
        )

    def admitted(self, req):
        key = self.requests.get(req.rid)
        if key is not None:
            if self.holds_restore(key):
                self.counts["native_restore_consumers"] += 1
            self.policy.begin(key.invocation_id)

    def selected(self, batch):
        if self.strategy != "thunderagent_workflow" or self.policy is None:
            return
        self.policy.observe_service((
            key.invocation_id for req in getattr(batch, "reqs", ())
            if (key := self.requests.get(req.rid)) is not None
        ), now=time.monotonic())

    def order_ready(self, ordered, keys, *, now, protected_head=False):
        if (
            self.strategy != "thunderagent_workflow" or self.policy is None
            or len(ordered) < 2
        ):
            return
        start = int(protected_head)
        candidates = {
            keys[index].invocation_id: (index, req)
            for index, req in ordered[start:] if keys.get(index) is not None
        }
        identity = self.policy.overdue_candidate(candidates, now=now)
        if identity is not None and (pair := candidates[identity]) != ordered[start]:
            ordered.remove(pair)
            ordered.insert(start, pair)
            self.counts["workflow_overdue_prefill_promoted"] += 1

    def completed(self, batch, completed_ids):
        finished = set(completed_ids)
        now = time.monotonic()
        for req in getattr(batch, "reqs", ()):
            if req.rid not in finished:
                continue
            key = self.requests.pop(req.rid, None)
            self._live_requests.pop(req.rid, None)
            if key is not None and key.invocation_id in self.policy.agents:
                self.policy.completed(
                    key.invocation_id, len(req.origin_input_ids) + len(req.output_ids),
                    now=now,
                )
                self.counts["completed"] += 1
        if finished:
            self.restore_backoff.clear()

    def events(self, events):
        if self.policy is None:
            return
        now = time.monotonic()
        for event in events:
            if event.kind is Kind.WORKFLOW_END:
                for invocation, workflow in tuple(self.workflows.items()):
                    if workflow == event.workflow_id:
                        self.forget(invocation, now=now)
                if self.strategy == "thunderagent_workflow":
                    self.policy.forget_workflow(event.workflow_id)
                    for join in self.runtime.graph.joins.values():
                        if join.workflow_id == event.workflow_id:
                            self.policy.forget_join(join.join_id)
                elif self.coadmit_join_children:
                    for join in self.runtime.graph.joins.values():
                        if join.workflow_id == event.workflow_id:
                            self.policy.forget_sibling_join(join.join_id)
                continue
            if self.strategy == "thunderagent_workflow":
                if event.kind in (Kind.JOIN_CREATE, Kind.JOIN_WAIT):
                    join = self.runtime.graph.joins.get(event.join_id)
                    if (
                        join is not None and join.mode.value == "all" and not join.satisfied
                        and any(
                            (waiter := self.runtime.graph.invocations.get(identity)) is not None
                            and waiter.state is InvocationState.WAIT_JOIN
                            for identity in join.waiter_invocation_ids
                        )
                    ):
                        self.policy.observe_join(
                            event.join_id, join.member_invocation_ids, now=now,
                        )
                elif event.kind in (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT):
                    self.policy.forget_join(event.join_id)
            elif self.coadmit_join_children:
                if event.kind in (Kind.JOIN_CREATE, Kind.JOIN_WAIT):
                    join = self.runtime.graph.joins.get(event.join_id)
                    if (
                        join is not None and join.mode.value == "all" and not join.satisfied
                        and any(
                            (waiter := self.runtime.graph.invocations.get(identity)) is not None
                            and waiter.state is InvocationState.WAIT_JOIN
                            for identity in join.waiter_invocation_ids
                        )
                    ):
                        self.policy.observe_sibling_join(
                            event.join_id, join.member_invocation_ids, now=now,
                        )
                elif event.kind in (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT):
                    self.policy.forget_sibling_join(event.join_id)
            invocation = self.runtime.graph.invocations.get(event.invocation_id)
            if invocation is not None and invocation.state.terminal:
                self.forget(event.invocation_id, now=now)
                continue
            if event.kind in (Kind.CALL, Kind.SPAWN, Kind.JOIN_WAIT):
                if invocation is not None and invocation.state in (
                    InvocationState.WAIT_JOIN, InvocationState.WAIT_CHILD,
                ):
                    self.policy.block(event.invocation_id, "join", now=now)
            elif event.kind in (Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT):
                join = self.runtime.graph.joins.get(event.join_id)
                if join is not None:
                    for waiter in join.waiter_invocation_ids:
                        state = self.runtime.graph.invocations.get(waiter)
                        if (
                            state is not None and state.join_id == join.join_id
                            and state.state is InvocationState.READY
                        ):
                            self.policy.unblock(waiter, now=now, critical=True)
            elif event.kind is Kind.TOOL_START:
                if event.invocation_id in self.latest:
                    identity = str(event.attributes.get("tool_run_id")
                                   or event.attributes.get("tool_call_id") or "_legacy")
                    self.tool_waits.setdefault(event.invocation_id, {})[identity] = now
            elif event.kind is Kind.TOOL_END:
                identity = str(event.attributes.get("tool_run_id")
                               or event.attributes.get("tool_call_id") or "_legacy")
                waiting = self.tool_waits.get(event.invocation_id)
                matched = waiting is not None and identity in waiting
                if waiting is not None:
                    waiting.pop(identity, None)
                    if not waiting:
                        del self.tool_waits[event.invocation_id]
                if (
                    invocation is not None and not invocation.active_tool_calls
                    and not self.tool_waits.get(event.invocation_id)
                    and invocation.state not in (
                        InvocationState.WAIT_JOIN, InvocationState.WAIT_CHILD,
                    )
                ):
                    if matched and self.tool_loan_return:
                        self.policy.note_tool_return(event.invocation_id)
                    self.policy.unblock(event.invocation_id, now=now)
        self.restore_backoff.clear()

    def _refresh_streaming_progress(self):
        generated = getattr(self.policy, "generated", None)
        if not callable(generated):
            return
        self.counts["streaming_progress_rounds"] += 1
        for request, key in self.requests.items():
            req = self._live_requests.get(request)
            item = self.policy.agents.get(key.invocation_id)
            if (
                req is not None and self.runtime.visible.get(request) == key
                and item is not None and item.active and item.pending and not item.acting
            ):
                tokens = len(req.origin_input_ids) + len(req.output_ids)
                if tokens != item.tokens:
                    self.counts["streaming_progress_updates"] += 1
                generated(key.invocation_id, tokens)
                self.counts["streaming_progress_checks"] += 1

    def tick(self, *, now=None):
        timestamp = time.monotonic() if now is None else now
        if self.policy is None or timestamp < self.next_tick:
            return
        self.next_tick = timestamp + 1.
        self.counts["control_ticks"] += 1
        if timestamp >= self._next_progress_refresh:
            self._next_progress_refresh = timestamp + 5.
            self._refresh_streaming_progress()
        # Reuse the existing causal prediction; never parse tool text or run a
        # second estimator here. Native eviction still validates physical state.
        for invocation, waiting in tuple(self.tool_waits.items()):
            started = min(waiting.values())
            key = self.latest.get(invocation)
            item = self.policy.agents.get(invocation)
            if key is not None and item is not None and item.active:
                if self.runtime._long_tool_wait(key):
                    self.policy.block(invocation, "long_tool", now=timestamp)
                elif timestamp - started >= 1.:
                    self.policy.block(invocation, "elapsed_tool", now=timestamp)
        self.policy.rebalance(now=timestamp)
        if self.blocked_lending in ("backed_demand", "backed_demand_recall"):
            ceiling = getattr(self.runtime, "_native_max_running", None)
            running = len(getattr(self.runtime._native_running_batch, "reqs", ()))
            if ceiling is None or running < ceiling:
                self.policy.lend_backed_blocked(
                    self._can_lend_blocked, now=timestamp,
                )

    def _can_lend_blocked(self, invocation):
        item = self.policy.agents.get(invocation)
        key = self.latest.get(invocation)
        return bool(
            item is not None and item.active and item.blocked and not item.pending
            and not item.in_flight and key is not None
            and (
                self.runtime._agent_blocked_prefix_backed(key, observed_blocked=True)
                if self.blocked_budget_proof == "observed_blocked"
                else self.runtime._agent_blocked_prefix_backed(key)
            )
        )

    def claim_restore(self, key, *, source):
        if self.policy is None:
            return False
        critical = source == "join_ticket"
        if self.strategy in ("thunderagent_retained", "thunderagent_workflow") and not critical:
            if source == "tool_wait" and self.holds_restore(key, source=source):
                self.counts["tool_warming_on_retained_eligibility"] += 1
                return True
            self.counts["ordinary_restore_disabled"] += 1
            return False
        previous = self.latest.get(key.invocation_id)
        if previous is None or (
            previous.root_workflow_id, previous.context_id,
            previous.session_id, previous.session_generation,
        ) != (
            key.root_workflow_id, key.context_id, key.session_id, key.session_generation,
        ) or key.context_epoch not in (
            previous.context_epoch - int(critical), previous.context_epoch, previous.context_epoch + 1,
        ):
            return False
        if not critical and not self.allows(key):
            self.counts["restore_outside_active_set"] += 1
            return False
        now = time.monotonic()
        signature = key, source
        if now < self.restore_backoff.get(signature, 0.):
            return False
        if not self.policy.reserve_restore(key.invocation_id, now=now, critical=critical):
            self.restore_backoff[signature] = now + .2
            return False
        return True

    def holds_restore(self, key, *, source="join_ticket"):
        if self.policy is None or key is None:
            return False
        previous = self.latest.get(key.invocation_id)
        if previous is None or (
            previous.root_workflow_id, previous.context_id,
            previous.session_id, previous.session_generation,
        ) != (
            key.root_workflow_id, key.context_id, key.session_id, key.session_generation,
        ) or key.context_epoch not in (
            previous.context_epoch - 1, previous.context_epoch, previous.context_epoch + 1,
        ):
            return False
        item = self.policy.agents.get(key.invocation_id)
        if source == "tool_wait" and self.strategy in (
            "thunderagent_retained", "thunderagent_workflow",
        ):
            invocation = self.runtime.graph.invocations.get(key.invocation_id)
            return bool(
                item is not None and item.active and not item.in_flight and not item.pending
                and invocation is not None and invocation.state is InvocationState.WAIT_TOOL
                and key.context_epoch == previous.context_epoch
            )
        return bool(
            item is not None and item.active
            and self.policy.restore_owner == key.invocation_id
            and item.restore_until > time.monotonic()
        )

    def requeued(self, requests, *, is_retracted):
        now = time.monotonic()
        for req in requests:
            key = self.runtime.visible[req.rid]
            old = self.requests.get(req.rid)
            if old is None or (
                old.root_workflow_id, old.invocation_id, old.context_id,
            ) != (key.root_workflow_id, key.invocation_id, key.context_id):
                raise ValueError("agent requeue changed its logical identity")
            self.requests[req.rid] = self.latest[key.invocation_id] = key
            self._live_requests[req.rid] = req
            self.policy.requeued(key.invocation_id, now=now)
            self.counts["retracted" if is_retracted else "requeued"] += 1

    def forget(self, invocation, *, now):
        if self.policy is not None:
            self.policy.forget(invocation, now=now)
        self.latest.pop(invocation, None)
        self.workflows.pop(invocation, None)
        self.tool_waits.pop(invocation, None)
        for rid, key in tuple(self.requests.items()):
            if key.invocation_id == invocation:
                del self.requests[rid]
                self._live_requests.pop(rid, None)
        self.counts["retired"] += 1

    def aborted(self, request_id, *, all_requests=False):
        if all_requests:
            self.close()
            return
        now = time.monotonic()
        for rid, key in tuple(self.requests.items()):
            if all_requests or rid.startswith(request_id):
                self.forget(key.invocation_id, now=now)

    def status(self):
        policy = self.policy
        active = [item for item in policy.agents.values() if item.active] if policy else []
        return {
            "policy": (
                "thunderagent_retained_with_native_migration"
                if self.strategy == "thunderagent_retained" else
                "thunderagent_workflow_with_native_migration"
                if self.strategy == "thunderagent_workflow" else
                "thunderagent_with_native_migration" if self.strategy == "thunderagent"
                else "agent_working_set_with_native_migration"
            ),
            "strategy": self.strategy,
            "identity": "invocation_id", "future_information_used": False,
            "native_capacity_authority_preserved": True,
            "capacity_tokens": policy.capacity if policy is not None else None,
            "logical_active_tokens": policy.used_tokens if policy is not None else 0,
            "active_agents": len(active),
            "active_invocation_ids": [item.identity for item in active],
            "blocked_active_invocation_ids": [
                item.identity for item in active if item.blocked
            ],
            "agents": len(self.latest), "pending_requests": len(self.requests),
            "recovery_consumer": policy.restore_owner if policy else None,
            "capacity_semantics": "conservative_sum_of_observed_agent_contexts_plus_growth_buffer",
            "streaming_context_accounting": (
                "observed_native_outputs_at_new_agent_arrival_and_five_second_control"
                if callable(getattr(policy, "generated", None)) else "request_boundaries_only"
            ),
            "shared_prefixes_deduplicated": False,
            "control_interval_ms": getattr(policy, "control_interval_s", 1.) * 1000.,
            "maintenance_interval_ms": 1000, "restore_reservation_ms": 1500,
            "restore_reservation_scope": (
                "join_eos_only" if self.strategy in ("thunderagent_retained", "thunderagent_workflow")
                else "selected_agent"
            ),
            "ordinary_restore_reservation_ms": (
                0 if self.strategy in ("thunderagent_retained", "thunderagent_workflow") else 1500
            ),
            "coadmit_join_children": self.coadmit_join_children,
            "blocked_lending": self.blocked_lending,
            "join_dependency_admission": self.join_dependency_admission,
            "blocked_budget_proof": self.blocked_budget_proof,
            "active_agent_budgets": [
                {
                    "invocation_id": item.identity, "tokens": item.tokens,
                    "buffer_tokens": policy.buffer_tokens, "pending": item.pending,
                    "in_flight": item.in_flight, "blocked": item.blocked,
                } for item in active
            ] if self.join_dependency_admission else None,
            "workflow_entry_min_ready_agents": self.workflow_entry_min_ready,
            "workflow_entry_scope": "unstarted_root_workflow_only",
            "workflow_entry_started_workflows": len(getattr(policy, "started_workflows", ())),
            "join_loan_recall": bool(getattr(policy, "recall_join_loans", False)),
            "recall_selection": self.recall_selection,
            "tool_loan_return": self.tool_loan_return,
            "tool_loan_return_boundary": "matched_all_tools_returned;submitted_successor;nonrunning_borrower",
            "tool_loan_return_priority": "below_join;recorded_lenders_only",
            "join_loan_scope": "logical_admissions_during_open_blocked_lending_period",
            "join_loan_recall_boundary": "submitted_join_successor;nonrunning_borrower",
            "join_loan_borrowers": {
                lender: sorted(borrowers)
                for lender, borrowers in getattr(policy, "loan_borrowers", {}).items()
            },
            "blocked_lending_max_probes_per_tick": 8,
            "blocked_lending_max_path_nodes": 64,
            "blocked_lending_max_admissions_per_tick": 1,
            "tool_warming_eligibility": (
                "existing_active_tool_wait_only_no_new_reservation"
                if self.strategy in ("thunderagent_retained", "thunderagent_workflow")
                else "selected_agent_restore_reservation"
            ),
            "eligibility_starvation_seconds": policy.aging_s if policy else None,
            "eligibility_release":
                (
                    "observed_growth_terminal_idle_or_workflow_service_handover"
                    if self.strategy == "thunderagent_workflow" else
                    "observed_backed_blocking_demand_terminal_growth_or_bounded_starvation"
                    if self.blocked_lending in ("backed_demand", "backed_demand_recall") else
                    "observed_growth_terminal_no_eligible_work_or_bounded_starvation"
                    if self.strategy == "thunderagent_retained" else
                    "observed_join_long_tool_terminal_growth_or_bounded_starvation"
                ),
            "blocked_agents_retain_eligibility": bool(
                getattr(policy, "retain_blocked", False)
            ),
            "counts": dict(self.counts), "policy_counts": dict(policy.counts) if policy else {},
            "workflow_service": (
                {
                    "first_service_target_s": policy.first_service_target_s,
                    "ready_gap_target_s": policy.ready_gap_target_s,
                    "minimum_agent_tenure_s": policy.minimum_tenure_s,
                    "handover_interval_s": policy.opportunity_interval_s,
                    "observation": "selected_batch_submission_not_token_delivery",
                    "capacity_or_running_work_may_defer_target": True,
                    "accounts": {
                        workflow: {
                            "first_submit": item.first_submit,
                            "first_service": item.first_service,
                            "last_service": item.last_service,
                            "ready_agents": len(item.ready),
                            "service_batches": item.service_batches,
                            "max_observed_ready_gap_s": item.max_ready_gap,
                        } for workflow, item in policy.service.items()
                    },
                } if self.strategy == "thunderagent_workflow" and policy is not None
                else None
            ),
        }

    def close(self):
        if self.policy is not None:
            self.policy.clear()
        self.requests.clear()
        self._live_requests.clear()
        self.latest.clear()
        self.workflows.clear()
        self.tool_waits.clear()
        self.restore_backoff.clear()
