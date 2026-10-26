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
        if type(tool_loan_return) is not bool or (
            tool_loan_return and blocked_lending != "backed_demand_recall"
        ):
            raise ValueError("tool loan return requires recorded blocked lending")
        self.tool_loan_return = tool_loan_return
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
        self.tool_waits = {}
        self.next_tick = 0.
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
            )
        else:
            self.policy = AgentWorkingSet(capacity)

    def registered(self, req, key):
        if self.policy is None or key.request_id in self.requests:
            raise ValueError("agent admission requires a cache and unique request")
        old = self.latest.get(key.invocation_id)
        if old is not None and (
            old.root_workflow_id, old.context_id,
        ) != (key.root_workflow_id, key.context_id):
            raise ValueError("an agent cannot change workflow or context identity")
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

    def tick(self, *, now=None):
        timestamp = time.monotonic() if now is None else now
        if self.policy is None or timestamp < self.next_tick:
            return
        self.next_tick = timestamp + 1.
        self.counts["control_ticks"] += 1
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
            and self.runtime._agent_blocked_prefix_backed(key)
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
            "workflow_entry_min_ready_agents": self.workflow_entry_min_ready,
            "workflow_entry_scope": "unstarted_root_workflow_only",
            "workflow_entry_started_workflows": len(getattr(policy, "started_workflows", ())),
            "join_loan_recall": bool(getattr(policy, "recall_join_loans", False)),
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
        self.latest.clear()
        self.workflows.clear()
        self.tool_waits.clear()
        self.restore_backoff.clear()
