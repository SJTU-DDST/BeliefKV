"""Bounded semantic admission for v0.5.20's native FULL/MAMBA scheduler.

No physical action or capacity certificate is issued by this runtime.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.event_channel import RuntimeEventDatagramServer
from beliefkv.runtime.sglang_v0520_admission import (
    _request_key,
    NativePrefillPlan,
    PrefillCandidateKey,
    compile_native_prefill_plan,
)
from beliefkv.runtime.sglang_v0520_prediction import (
    NativeDemandHint,
    NativeJoinWaitHint,
    NativeToolWaitHint,
    PREDICTION_ATTRIBUTE,
    parse_native_demand_hint,
    validate_admission_artifact,
)
from beliefkv.runtime.sglang_v0520_physical import (
    ActionLocalPrefetchCandidate,
    ActionLocalShadowCandidate,
    ContextSessionAnchors,
    PhysicalActionCompleted,
    PhysicalActionExpectation,
    PhysicalReceiptError,
    PhysicalTransactionLedger,
    PrefetchLoadStep,
    ShadowBackupStep,
    capture_action_local_shadow,
    next_prefetch_gpu_step,
    next_shadow_backup_step,
    prefetch_expectation_from_native_op,
    shadow_expectation_from_native_op,
)
from beliefkv.predictor.structured_frontier import LocalFrontierFeatures

if TYPE_CHECKING:
    from beliefkv.core.events import RuntimeEvent


@dataclass
class _AdmissionPrefetchLease:
    key: PrefillCandidateKey
    expires_at: float
    command_id: str | None = None
    issued_nodes: int = 0


CHILD_COMPLETION_INTENT = "beliefkv_child_completion_intent"


@dataclass
class _JoinPrefetchTicket:
    key: PrefillCandidateKey
    join_id: str
    join_mode: str
    member_ids: tuple[str, ...]
    phase: str
    expires_at: float
    command_id: str | None = None
    issued_nodes: int = 0


class NativeAdmissionRuntime:
    """Rebind causal order to live request identities at each prefill safe point."""

    def __init__(
        self,
        *,
        event_socket_path: str | None = None,
        predictor_sha256: str | None = None,
        predictor_artifact_path: str | None = None,
        model_path: str | None = None,
        enable_local_predictor: bool = False,
        enable_admission_prefetch: bool = False,
    ) -> None:
        if enable_admission_prefetch and (
            not enable_local_predictor or not predictor_artifact_path
        ):
            raise ValueError("admission H2D requires a pinned, live action predictor")
        if bool(predictor_sha256) != bool(predictor_artifact_path):
            raise ValueError("predictive admission requires both artifact and SHA-256")
        if predictor_sha256 is not None and (
            type(predictor_sha256) is not str
            or len(predictor_sha256) != 64
            or any(c not in "0123456789abcdef" for c in predictor_sha256)
            or not event_socket_path
        ):
            raise ValueError("predictive admission requires a pinned SHA-256 and event socket")
        if predictor_sha256 is not None:
            if not model_path:
                raise ValueError("predictive admission requires a model path")
            validate_admission_artifact(
                predictor_artifact_path,
                expected_sha256=predictor_sha256,
                model_path=model_path,
                require_physical_actions=enable_admission_prefetch,
            )
        self.semantic_revision = 0
        self.predictor_sha256 = predictor_sha256
        self.demand_hints: dict[str, NativeDemandHint] = {}
        self.tool_wait_hint: NativeToolWaitHint | None = None
        self.join_wait_hint: NativeJoinWaitHint | None = None
        self._join_ticket: _JoinPrefetchTicket | None = None
        self.enable_admission_prefetch = enable_admission_prefetch
        self._admission_lease: _AdmissionPrefetchLease | None = None
        self.shadow_candidate: ActionLocalShadowCandidate | None = None
        self._native_cache: object | None = None
        self._context_tokens: dict[str, tuple[int, int, int, bool]] = {}
        self._last_model_signature: tuple[object, ...] | None = None
        self._model_worker = None
        if enable_local_predictor:
            if predictor_sha256 is None or predictor_artifact_path is None:
                raise ValueError("local admission predictor requires a calibrated artifact")
            from beliefkv.runtime.sglang_v0520_predictor_worker import (
                NativePredictorWorker,
            )

            self._model_worker = NativePredictorWorker(
                predictor_artifact_path, predictor_sha256
            )
        self.graph = RuntimeCausalContextGraph(strict_timestamps=False)
        self._join_by_invocation: dict[str, set[str]] = {}
        self.frontier = CausalFrontierScheduler(self.graph)
        self.visible: dict[str, PrefillCandidateKey] = {}
        self.context_sessions: dict[str, PrefillCandidateKey] = {}
        self.physical_ledger = PhysicalTransactionLedger()
        self.completed_physical_actions: deque[PhysicalActionCompleted] = deque(
            maxlen=128
        )
        self.physical_disabled = False
        self.counts: Counter[str] = Counter()
        self.event_server = (
            RuntimeEventDatagramServer(event_socket_path, self.on_events)
            if event_socket_path
            else None
        )

    def close(self) -> None:
        if self._model_worker is not None:
            self._model_worker.close()
            self._model_worker = None
        if self.event_server is not None:
            self.event_server.close()
            self.event_server = None

    def attach_native_cache(self, cache: object) -> None:
        self._native_cache = cache

    def predictor_fileno(self) -> int | None:
        if self._model_worker is None or self._model_worker.disabled:
            return None
        return self._model_worker.fileno()

    def on_events(self, events: tuple[RuntimeEvent, ...]) -> None:
        # A provisional callback can arrive behind a confirmed RETURN/epoch
        # advance. It is advisory, so a stale one must not discard the RCCG.
        filtered = []
        for event in events:
            if event.kind is RuntimeEventKind.STRUCTURED_ACTION and (
                event.attributes.get(CHILD_COMPLETION_INTENT) is True
            ):
                invocation = self.graph.invocations.get(event.invocation_id)
                context = self.graph.contexts.get(event.context_id)
                if (
                    invocation is None or invocation.state.terminal
                    or invocation.context_id != event.context_id
                    or context is None or context.epoch != event.context_epoch
                    or invocation.workflow_id != event.workflow_id
                ):
                    self.counts["join_intent_stale"] += 1
                    continue
            filtered.append(event)
        events = tuple(filtered)
        if not events:
            return
        hints = []
        for event in events:
            raw = event.attributes.get(PREDICTION_ATTRIBUTE)
            if raw is None:
                continue
            if self.predictor_sha256 is None:
                self.counts["unconfigured_prediction_ignored"] += 1
                continue
            hint = parse_native_demand_hint(
                event, raw, expected_sha256=self.predictor_sha256
            )
            if self.visible.get(hint.key.request_id) != hint.key:
                raise ValueError("admission prediction has no matching live request")
            hints.append(hint)
        try:
            self.graph.apply_batch(events, atomic=False)
        except Exception:
            # A partially applied batch cannot remain a scheduling authority.
            self.graph = RuntimeCausalContextGraph(strict_timestamps=False)
            self._join_by_invocation.clear()
            self.frontier = CausalFrontierScheduler(self.graph)
            self.demand_hints.clear()
            self._last_model_signature = None
            self.context_sessions.clear()
            self.tool_wait_hint = None
            self.join_wait_hint = None
            self._join_ticket = None
            self._admission_lease = None
            self.shadow_candidate = None
            self._context_tokens.clear()
            self.semantic_revision += 1
            self.counts["causal_mirror_discarded"] += 1
            raise
        else:
            for event in events:
                if event.kind is RuntimeEventKind.JOIN_CREATE and event.join_id:
                    join = self.graph.joins.get(event.join_id)
                    if join is not None:
                        for member in join.member_invocation_ids:
                            self._join_by_invocation.setdefault(member, set()).add(join.join_id)
                elif event.kind is RuntimeEventKind.JOIN_WAIT and event.join_id and event.invocation_id:
                    self._join_by_invocation.setdefault(event.invocation_id, set()).add(event.join_id)
            for hint in hints:
                if self._terminal(hint.key):
                    self.counts["terminal_prediction_ignored"] += 1
                else:
                    self.demand_hints[hint.key.request_id] = hint
                    self.counts["prediction_accepted"] += 1
            for event in events:
                context_id = event.context_id
                if context_id is None and event.invocation_id is not None:
                    invocation = self.graph.invocations.get(event.invocation_id)
                    context_id = (
                        invocation.context_id if invocation is not None else None
                    )
                if context_id is not None:
                    key = self.context_sessions.get(context_id)
                    if key is not None and self._terminal(key):
                        del self.context_sessions[context_id]
                        self._context_tokens.pop(context_id, None)
                    if event.kind in (
                        RuntimeEventKind.TOOL_END,
                        RuntimeEventKind.RETURN,
                        RuntimeEventKind.INVOCATION_CANCEL,
                        RuntimeEventKind.CONTEXT_ADVANCE,
                    ) and self.tool_wait_hint is not None and (
                        self.tool_wait_hint.key.context_id == context_id
                    ):
                        self.tool_wait_hint = None
                        self.shadow_candidate = None
                if event.kind is RuntimeEventKind.WORKFLOW_END:
                    self.context_sessions = {
                        context: key
                        for context, key in self.context_sessions.items()
                        if key.root_workflow_id != event.workflow_id
                    }
                    self._context_tokens = {
                        context: tokens
                        for context, tokens in self._context_tokens.items()
                        if context in self.context_sessions
                    }
                    if self.tool_wait_hint is not None and (
                        self.tool_wait_hint.key.root_workflow_id == event.workflow_id
                    ):
                        self.tool_wait_hint = None
                        self.shadow_candidate = None
                if event.kind is RuntimeEventKind.TOOL_START and context_id is not None:
                    self._submit_tool_wait(context_id)
            for event in events:
                if event.kind is RuntimeEventKind.STRUCTURED_ACTION:
                    self._observe_child_completion_intent(event)
                elif event.kind in (
                    RuntimeEventKind.RETURN, RuntimeEventKind.JOIN_SATISFIED,
                    RuntimeEventKind.JOIN_TIMEOUT, RuntimeEventKind.INVOCATION_CANCEL,
                ):
                    self._advance_join_ticket(event)
            if self.join_wait_hint is not None and not self._live_join_hint(
                self.join_wait_hint
            ):
                self.join_wait_hint = None
            if self._join_ticket is not None and not self._live_join_ticket():
                self._join_ticket = None
            if any(event.kind in (
                RuntimeEventKind.JOIN_CREATE, RuntimeEventKind.JOIN_WAIT,
                RuntimeEventKind.JOIN_SATISFIED, RuntimeEventKind.JOIN_TIMEOUT,
                RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL,
                RuntimeEventKind.TOOL_END, RuntimeEventKind.TOOL_START,
            ) for event in events):
                self._submit_join_wait(events)
            if len(self.demand_hints) > 1024:
                now_ms = time.monotonic() * 1000
                self.demand_hints = {
                    rid: hint for rid, hint in self.demand_hints.items()
                    if hint.expires_monotonic_ms > now_ms
                    and self.visible.get(rid) == hint.key
                }
            self.semantic_revision += 1

    def scheduler_step(self) -> None:
        if self.event_server is not None:
            self.event_server.drain(max_messages=16)
        if self.tool_wait_hint is not None and not self.tool_wait_hint.live(
            self.tool_wait_hint.key, now_ms=time.monotonic() * 1000
        ):
            self.tool_wait_hint = None
            self.shadow_candidate = None
            self.counts["tool_wait_expired"] += 1
        if self.join_wait_hint is not None and not self._live_join_hint(
            self.join_wait_hint
        ):
            self.join_wait_hint = None
            self.counts["join_wait_expired"] += 1
        if self._model_worker is not None:
            hints = self._model_worker.poll()
            for hint in hints:
                if isinstance(hint, NativeToolWaitHint):
                    self._accept_tool_wait(hint)
                    continue
                if isinstance(hint, NativeJoinWaitHint):
                    if self._live_join_hint(hint):
                        self.join_wait_hint = hint
                        ticket = self._join_ticket
                        if ticket is None or (
                            ticket.key, ticket.join_id
                        ) != (hint.key, hint.join_id):
                            self._join_ticket = _JoinPrefetchTicket(
                                hint.key, hint.join_id, hint.join_mode,
                                hint.member_ids, "probabilistic",
                                hint.expires_monotonic_ms / 1000,
                            )
                        self.counts["join_wait_accepted"] += 1
                    else:
                        self.counts["join_wait_result_stale"] += 1
                    continue
                if (
                    hint.predictor_sha256 == self.predictor_sha256
                    and hint.live(hint.key, now_ms=time.monotonic() * 1000)
                    and self.visible.get(hint.key.request_id) == hint.key
                    and not self._terminal(hint.key)
                    and (
                        hint.invocation_revision_ts_ms is None
                        or getattr(
                            self.graph.invocations.get(hint.key.invocation_id),
                            "updated_ts_ms",
                            None,
                        ) == hint.invocation_revision_ts_ms
                    )
                ):
                    self.demand_hints[hint.key.request_id] = hint
                    self.counts["model_prediction_accepted"] += 1
                    self.semantic_revision += 1
            if self._model_worker.disabled:
                self.counts["model_worker_disabled"] = 1
        expired = self.physical_ledger.expire()
        self.counts["physical_expired"] += len(expired)
        lease = self._admission_lease
        if lease is not None and lease.command_id is not None and (
            lease.command_id in expired
        ):
            self._admission_lease = None
            self.counts["admission_prefetch_expired"] += 1
        ticket = self._join_ticket
        if ticket is not None and ticket.command_id in expired:
            ticket.command_id = None
            self.counts["join_prefetch_expired"] += 1
        if ticket is not None and not self._live_join_ticket():
            self._join_ticket = None

    def _join_parent_key(self, join_id: str) -> PrefillCandidateKey | None:
        join = self.graph.joins.get(join_id)
        if join is None or not 0 < len(join.member_invocation_ids) <= 8:
            return None
        for parent_id in sorted(join.waiter_invocation_ids):
            parent = self.graph.invocations.get(parent_id)
            key = self.context_sessions.get(parent.context_id) if parent else None
            if (
                key is not None and key.invocation_id == parent_id
                and key.session_id is not None
                and key.session_generation is not None
                and not self._terminal(key)
            ):
                return key
        return None

    def _observe_child_completion_intent(self, event: RuntimeEvent) -> None:
        if event.attributes.get(CHILD_COMPLETION_INTENT) is not True:
            return
        join_id = event.join_id
        child_id = event.invocation_id
        join = self.graph.joins.get(join_id) if isinstance(join_id, str) else None
        child = self.graph.invocations.get(child_id) if child_id else None
        context = self.graph.contexts.get(event.context_id) if event.context_id else None
        if (
            join is None or join.satisfied or child is None or context is None
            or join.workflow_id != event.workflow_id
            or child.workflow_id != event.workflow_id
            or child.context_id != event.context_id
            or child.state.terminal
            or context.epoch != event.context_epoch
            or child_id not in join.member_invocation_ids - join.completed_member_ids
            or (
                join.mode.value == "all"
                and len(join.member_invocation_ids - join.completed_member_ids) != 1
            )
            or event.attributes.get("structured_action_names") != ["ChildCompletion"]
            or not isinstance(event.attributes.get("request_id"), str)
            or not event.attributes["request_id"]
        ):
            self.counts["join_intent_stale"] += 1
            return
        key = self._join_parent_key(join_id)
        parent = self.graph.invocations.get(key.invocation_id) if key else None
        if key is None or parent.state is not InvocationState.WAIT_JOIN:
            self.counts["join_intent_stale"] += 1
            return
        ticket = self._join_ticket
        if ticket is None or (ticket.key, ticket.join_id) != (key, join_id):
            ticket = _JoinPrefetchTicket(
                key, join_id, join.mode.value,
                tuple(sorted(join.member_invocation_ids)),
                "provisional", time.monotonic() + 2.0,
            )
            self._join_ticket = ticket
        else:
            ticket.phase = "provisional"
            ticket.expires_at = time.monotonic() + 2.0
        self.counts["join_intent_accepted"] += 1

    def _advance_join_ticket(self, event: RuntimeEvent) -> None:
        ticket = self._join_ticket
        if ticket is None:
            if not self.enable_admission_prefetch or event.kind not in (
                RuntimeEventKind.RETURN, RuntimeEventKind.JOIN_SATISFIED,
            ):
                return
            candidate_joins = (
                (event.join_id,) if event.join_id is not None else
                tuple(sorted(self._join_by_invocation.get(event.invocation_id, ())))
            )
            for join_id in candidate_joins:
                join = self.graph.joins.get(join_id)
                if (
                    join is None or not join.satisfied
                    or join.workflow_id != event.workflow_id
                    or event.kind is RuntimeEventKind.RETURN
                    and event.invocation_id not in join.member_invocation_ids
                ):
                    continue
                key = self._join_parent_key(join_id)
                parent = self.graph.invocations.get(key.invocation_id) if key else None
                if (
                    key is None or parent.state is not InvocationState.READY
                    or parent.join_id != join_id
                ):
                    continue
                self._join_ticket = _JoinPrefetchTicket(
                    key, join_id, join.mode.value,
                    tuple(sorted(join.member_invocation_ids)),
                    "confirmed", time.monotonic() + 2.0,
                )
                self.counts["join_reentry_confirmed"] += 1
                return
            return
        join = self.graph.joins.get(ticket.join_id)
        if join is None or join.workflow_id != ticket.key.root_workflow_id:
            self._join_ticket = None
            return
        if event.kind is RuntimeEventKind.JOIN_TIMEOUT and event.join_id == ticket.join_id:
            self._join_ticket = None
        elif event.kind is RuntimeEventKind.INVOCATION_CANCEL and (
            event.invocation_id == ticket.key.invocation_id
            or event.invocation_id in ticket.member_ids
        ):
            self._join_ticket = None
        elif join.satisfied and (
            event.join_id == ticket.join_id
            or event.invocation_id in ticket.member_ids
        ):
            if ticket.phase != "confirmed":
                ticket.phase = "confirmed"
                ticket.expires_at = time.monotonic() + 2.0
                self.counts["join_reentry_confirmed"] += 1
        elif event.kind is RuntimeEventKind.RETURN and event.invocation_id in ticket.member_ids:
            ticket.phase = "probabilistic"

    def _live_join_ticket(self) -> bool:
        ticket = self._join_ticket
        if ticket is None or time.monotonic() >= ticket.expires_at:
            return False
        key = ticket.key
        join = self.graph.joins.get(ticket.join_id)
        parent = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        return bool(
            self.enable_admission_prefetch and not self.physical_disabled
            and join is not None and join.workflow_id == key.root_workflow_id
            and join.mode.value == ticket.join_mode
            and tuple(sorted(join.member_invocation_ids)) == ticket.member_ids
            and key.invocation_id in join.waiter_invocation_ids
            and self.context_sessions.get(key.context_id) == key
            and context is not None and context.epoch == key.context_epoch
            and parent is not None and parent.context_id == key.context_id
            and parent.workflow_id == key.root_workflow_id
            and parent.join_id == ticket.join_id
            and parent.state is (
                InvocationState.READY if ticket.phase == "confirmed"
                else InvocationState.WAIT_JOIN
            )
            and join.satisfied == (ticket.phase == "confirmed")
            and not self._terminal(key)
        )

    def dispatch_join_prefetch(self) -> None:
        """One action-local native H2D per safe point, never grant admission."""
        ticket = self._join_ticket
        if ticket is None or not self._live_join_ticket():
            self._join_ticket = None
            return
        if ticket.command_id is not None:
            if self.physical_ledger.is_pending(ticket.command_id):
                return
            if not any(
                action.command_id == ticket.command_id
                and action.action == "PREFETCH_GPU"
                for action in self.completed_physical_actions
            ):
                self._join_ticket = None
                self.counts["join_prefetch_lost_ack"] += 1
                return
            ticket.command_id = None
            self.counts["join_prefetch_acked"] += 1
        if ticket.issued_nodes >= 2 or self.physical_ledger.pending_count:
            return
        if ticket.phase == "probabilistic":
            hint = self.join_wait_hint
            if hint is None or not self._live_join_hint(hint):
                return
            remaining_p10_ms = hint.wait_p10_ms - (
                time.monotonic() * 1000 - hint.issued_monotonic_ms
            )
            if remaining_p10_ms > 1_000:
                return
        step = self.refreshed_prefetch_gpu_step(source="join_ticket")
        if step is None:
            self.counts["join_prefetch_no_cpu_node"] += 1
            return
        command = self.issue_prefetch_gpu_step(step, source="join_ticket")
        if command is not None:
            ticket.command_id = command
            ticket.issued_nodes += 1
            self.counts[f"join_prefetch_{ticket.phase}_issued"] += 1

    def _submit_tool_wait(self, context_id: str) -> None:
        worker = self._model_worker
        key = self.context_sessions.get(context_id)
        if worker is None or worker.disabled or key is None:
            self.counts["tool_wait_predictor_unavailable"] += 1
            return
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(context_id)
        if (
            invocation is None
            or context is None
            or context.epoch != key.context_epoch
            or context.workflow_id != key.root_workflow_id
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != context_id
            or invocation.state.value != "wait_tool"
            or self._terminal(key)
        ):
            self.counts["tool_wait_context_stale"] += 1
            return
        stored = self._context_tokens.get(context_id)
        prompt, output, is_child = (
            (stored[1], stored[2], stored[3])
            if stored is not None and stored[0] == key.context_epoch
            else (0, 0, False)
        )
        features = LocalFrontierFeatures(
            invocation_id=key.invocation_id,
            state=invocation.state.value,
            agent_definition_id=invocation.agent_definition_id,
            tool_family=invocation.active_tool_family or "unknown",
            generated_tokens=output,
            current_sequence_tokens=prompt + output,
            active_tool_count=1,
            llm_round=invocation.llm_round,
            child_count=len(invocation.child_invocation_ids),
            unfinished_child_count=len(invocation.blocking_child_ids),
            is_child=is_child,
        )
        worker.submit_tool_wait(((key, features, invocation.updated_ts_ms),))
        self.counts["tool_wait_submitted"] += 1

    def _accept_tool_wait(self, hint: NativeToolWaitHint) -> None:
        key = hint.key
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            hint.predictor_sha256 != self.predictor_sha256
            or self._model_worker is None
            or self.context_sessions.get(key.context_id) != key
            or context is None
            or context.epoch != key.context_epoch
            or context.workflow_id != key.root_workflow_id
            or invocation is None
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or invocation.state.value != "wait_tool"
            or invocation.updated_ts_ms != hint.invocation_revision_ts_ms
            or self._terminal(key)
            or not hint.live(key, now_ms=time.monotonic() * 1000)
        ):
            self.counts["tool_wait_result_stale"] += 1
            return
        self.tool_wait_hint = hint
        self.counts["tool_wait_accepted"] += 1
        self.shadow_candidate = (
            self.capture_shadow_candidate(
                self._native_cache,
                context_id=key.context_id,
                context_epoch=key.context_epoch,
            )
            if self._native_cache is not None
            else None
        )
        self.counts[
            "tool_wait_shadow_available"
            if self.shadow_candidate is not None
            else "tool_wait_shadow_unavailable"
        ] += 1

    def _live_join_hint(self, hint: NativeJoinWaitHint) -> bool:
        key = hint.key
        parent = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        join = self.graph.joins.get(hint.join_id)
        return bool(
            hint.predictor_sha256 == self.predictor_sha256
            and hint.live(key, now_ms=time.monotonic() * 1000)
            and self.context_sessions.get(key.context_id) == key
            and context is not None
            and context.epoch == key.context_epoch
            and context.workflow_id == key.root_workflow_id
            and parent is not None
            and parent.context_id == key.context_id
            and parent.workflow_id == key.root_workflow_id
            and parent.state is InvocationState.WAIT_JOIN
            and parent.join_id == hint.join_id
            and parent.updated_ts_ms == hint.invocation_revision_ts_ms
            and join is not None
            and join.workflow_id == key.root_workflow_id
            and not join.satisfied
            and join.mode.value == hint.join_mode
            and tuple(sorted(join.member_invocation_ids)) == hint.member_ids
            and tuple(
                (
                    child_id, self.graph.invocations[child_id].updated_ts_ms,
                    self.graph.invocations[child_id].state.value,
                    self.graph.contexts[self.graph.invocations[child_id].context_id].epoch,
                )
                for child_id in sorted(join.member_invocation_ids - join.completed_member_ids)
            ) == hint.child_revisions
            and not self._terminal(key)
        )

    def _submit_join_wait(self, events: tuple[RuntimeEvent, ...]) -> None:
        worker = self._model_worker
        if worker is None or worker.disabled:
            return
        affected = {event.invocation_id for event in events if event.invocation_id}
        affected_joins = {event.join_id for event in events if event.join_id}
        for invocation_id in affected:
            affected_joins.update(self._join_by_invocation.get(invocation_id, ()))
        for join_id in sorted(affected_joins):
            join = self.graph.joins.get(join_id)
            if join is None:
                continue
            if join.satisfied or not 0 < len(join.member_invocation_ids) <= 8:
                continue
            for parent_id in sorted(join.waiter_invocation_ids):
                parent = self.graph.invocations[parent_id]
                key = self.context_sessions.get(parent.context_id)
                if (
                    key is None or key.invocation_id != parent_id
                    or parent.state is not InvocationState.WAIT_JOIN
                    or key.session_id is None or self._terminal(key)
                ):
                    continue
                children = []
                for child_id in sorted(
                    join.member_invocation_ids - join.completed_member_ids
                ):
                    child = self.graph.invocations.get(child_id)
                    child_context = (
                        self.graph.contexts.get(child.context_id)
                        if child is not None else None
                    )
                    if child is None or child_context is None or child.state.terminal:
                        break
                    stored = self._context_tokens.get(child.context_id)
                    prompt, output, is_child = (
                        (stored[1], stored[2], stored[3])
                        if stored is not None
                        and stored[0] == child_context.epoch
                        else (0, 0, True)
                    )
                    children.append((
                        child_id,
                        LocalFrontierFeatures(
                            invocation_id=child_id, state=child.state.value,
                            agent_definition_id=child.agent_definition_id,
                            tool_family=child.active_tool_family or "unknown",
                            generated_tokens=output,
                            current_sequence_tokens=prompt + output,
                            llm_round=child.llm_round,
                            child_count=len(child.child_invocation_ids),
                            unfinished_child_count=len(child.blocking_child_ids),
                            is_child=is_child,
                        ),
                        child.updated_ts_ms,
                        child_context.epoch,
                    ))
                if len(children) != len(join.member_invocation_ids - join.completed_member_ids):
                    continue
                worker.submit_join_wait(((
                    key, parent.updated_ts_ms, join.join_id, join.mode.value,
                    tuple(sorted(join.member_invocation_ids)),
                    tuple(sorted(join.completed_member_ids)), tuple(children),
                ),))
                self.counts["join_wait_submitted"] += 1
                return

    def register_physical_action(self, expected: PhysicalActionExpectation) -> None:
        """Accept only a live causal identity; this does not issue the transfer."""
        context = self.graph.contexts.get(expected.context_id)
        session_key = self.context_sessions.get(expected.context_id)
        invocation = (
            self.graph.invocations.get(session_key.invocation_id)
            if session_key is not None
            else None
        )
        session_waiting = (
            session_key is not None
            and session_key.context_epoch == expected.context_epoch
            and session_key.session_id is not None
            and session_key.session_id == expected.session_id
            and session_key.session_generation == expected.session_generation
            and not self._terminal(session_key)
            and invocation is not None
            and invocation.state.value in ("wait_tool", "wait_child", "wait_join")
        )
        visible = any(
            key.context_id == expected.context_id
            and key.context_epoch == expected.context_epoch
            and (
                expected.session_id is None
                or (
                    key.session_id == expected.session_id
                    and key.session_generation == expected.session_generation
                )
            )
            and not self._terminal(key)
            for key in self.visible.values()
        )
        confirmed_join = (
            expected.action == "PREFETCH_GPU"
            and self._live_join_ticket()
            and self._join_ticket is not None
            and self._join_ticket.phase == "confirmed"
            and self._join_ticket.key.context_id == expected.context_id
            and self._join_ticket.key.context_epoch == expected.context_epoch
            and self._join_ticket.key.session_id == expected.session_id
            and self._join_ticket.key.session_generation == expected.session_generation
        )
        if (
            self.physical_disabled
            or context is None
            or context.epoch != expected.context_epoch
            or not (visible or session_waiting or confirmed_join)
        ):
            raise PhysicalReceiptError("physical action has no live causal context")
        self.physical_ledger.register(expected)

    def snapshot_session_anchors(
        self, cache: object, *, context_id: str, context_epoch: int
    ) -> ContextSessionAnchors | None:
        """Request-local lookup for a finished tool-waiting context."""
        key = self.context_sessions.get(context_id)
        context = self.graph.contexts.get(context_id)
        if (
            key is None
            or key.context_epoch != context_epoch
            or context is None
            or context.epoch != context_epoch
            or key.session_id is None
            or key.session_generation is None
            or self._terminal(key)
        ):
            return None
        try:
            leaves = cache.session_refs.snapshot_session_leaf_anchors(
                key.session_id, key.session_generation, max_leaves=8
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if leaves is None or not any(anchors for _, anchors in leaves):
            return None
        return ContextSessionAnchors(
            key=key, component_leaves=leaves,
            captured_monotonic_s=time.monotonic(),
        )

    def capture_shadow_candidate(
        self, cache: object, *, context_id: str, context_epoch: int,
        for_prefetch: bool = False,
    ) -> ActionLocalShadowCandidate | ActionLocalPrefetchCandidate | None:
        anchors = self.snapshot_session_anchors(
            cache, context_id=context_id, context_epoch=context_epoch
        )
        if anchors is None:
            return None
        return capture_action_local_shadow(
            cache, anchors, for_prefetch=for_prefetch
        )

    def refreshed_shadow_backup_step(self) -> ShadowBackupStep | None:
        """Recheck a tool wait and its native closure at the action safe point."""
        hint = self.tool_wait_hint
        cache = self._native_cache
        if hint is None or cache is None:
            return None
        key = hint.key
        invocation = self.graph.invocations.get(key.invocation_id)
        if (
            hint.predictor_sha256 != self.predictor_sha256
            or not hint.live(key, now_ms=time.monotonic() * 1000)
            or self.context_sessions.get(key.context_id) != key
            or invocation is None
            or invocation.state.value != "wait_tool"
            or invocation.updated_ts_ms != hint.invocation_revision_ts_ms
            or self._terminal(key)
        ):
            self.tool_wait_hint = None
            self.shadow_candidate = None
            return None
        candidate = self.capture_shadow_candidate(
            cache, context_id=key.context_id, context_epoch=key.context_epoch
        )
        self.shadow_candidate = candidate
        return next_shadow_backup_step(candidate) if candidate is not None else None

    def issue_shadow_backup_step(self, step: ShadowBackupStep) -> str | None:
        """Submit a revalidated native shadow; return its ID, not ACK credit.

        The scheduler's action policy must first authorize the step. This
        method supplies transaction safety only and is not an action planner.
        """
        cache = self._native_cache
        if (
            self.physical_disabled
            or cache is None
            or not isinstance(step, ShadowBackupStep)
            or self.refreshed_shadow_backup_step() != step
        ):
            self.counts["shadow_step_stale"] += 1
            return None
        command_id = f"beliefkv-shadow-{uuid4().hex}"
        registered = False

        def before_enqueue(operation: object) -> bool:
            nonlocal registered
            try:
                expected = shadow_expectation_from_native_op(
                    command_id, step, operation, cache.cache_controller
                )
                self.register_physical_action(expected)
            except (PhysicalReceiptError, AttributeError, TypeError, ValueError):
                self.counts["shadow_reservation_rejected"] += 1
                return False
            registered = True
            return True

        outcome = cache.prepare_host_shadow(
            session_id=step.key.session_id,
            session_generation=step.key.session_generation,
            leaf_node_id=step.leaf_node_id,
            leaf_creation_time=step.leaf_creation_time,
            node_id=step.node_id,
            node_creation_time=step.creation_time,
            beliefkv_command_id=command_id,
            beliefkv_before_enqueue=before_enqueue,
        )
        if not outcome.issued:
            if registered:
                # Native confirmed that the operation was not enqueued.
                self.physical_ledger.cancel_unsubmitted(command_id)
            self.counts["shadow_native_declined"] += 1
            return None
        if not registered or outcome.node_id != step.node_id:
            self.physical_disabled = True
            raise PhysicalReceiptError("native shadow issued without a matching reservation")
        self.counts["shadow_native_issued"] += 1
        return command_id

    def refreshed_prefetch_gpu_step(
        self, *, source: str = "tool_wait"
    ) -> PrefetchLoadStep | None:
        """Revalidate the explicit causal or admission source before native H2D."""
        cache = self._native_cache
        if cache is None:
            return None
        if source == "tool_wait":
            hint = self.tool_wait_hint
            if hint is None:
                return None
            key = hint.key
            valid_source = (
                hint.predictor_sha256 == self.predictor_sha256
                and hint.live(key, now_ms=time.monotonic() * 1000)
            )
        elif source == "admission":
            lease = self._admission_lease
            if lease is None or not self.enable_admission_prefetch:
                return None
            key = lease.key
            hint = self.demand_hints.get(key.request_id)
            valid_source = (
                hint is not None
                and hint.predictor_sha256 == self.predictor_sha256
                and hint.live(key, now_ms=time.monotonic() * 1000)
                and time.monotonic() < lease.expires_at
                and self.visible.get(key.request_id) == key
            )
        elif source == "join_wait":
            hint = self.join_wait_hint
            if hint is None or not self.enable_admission_prefetch:
                return None
            key = hint.key
            valid_source = self._live_join_hint(hint)
        elif source == "join_ticket":
            ticket = self._join_ticket
            if ticket is None or not self._live_join_ticket():
                return None
            key = ticket.key
            hint = self.join_wait_hint
            valid_source = (
                ticket.phase != "probabilistic"
                or hint is not None and self._live_join_hint(hint)
            )
        else:
            return None
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            not valid_source
            or self.context_sessions.get(key.context_id) != key
            or context is None
            or context.epoch != key.context_epoch
            or context.workflow_id != key.root_workflow_id
            or invocation is None
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or invocation.state.value != (
                "ready" if source == "admission"
                or source == "join_ticket" and ticket.phase == "confirmed"
                else "wait_join" if source in ("join_wait", "join_ticket")
                else "wait_tool"
            )
            or (
                source != "join_ticket"
                and invocation.updated_ts_ms != hint.invocation_revision_ts_ms
            )
            or self._terminal(key)
        ):
            return None
        candidate = self.capture_shadow_candidate(
            cache, context_id=key.context_id, context_epoch=key.context_epoch,
            for_prefetch=True,
        )
        return next_prefetch_gpu_step(candidate) if candidate is not None else None

    def issue_prefetch_gpu_step(
        self, step: PrefetchLoadStep, *, source: str = "tool_wait"
    ) -> str | None:
        """Submit one bounded native H2D; completion requires matching ACK."""
        cache = self._native_cache
        if (
            self.physical_disabled
            or cache is None
            or not isinstance(step, PrefetchLoadStep)
            or self.refreshed_prefetch_gpu_step(source=source) != step
        ):
            self.counts["prefetch_step_stale"] += 1
            return None
        command_id = f"beliefkv-prefetch-{uuid4().hex}"
        registered = False

        def before_enqueue(operation: object) -> bool:
            nonlocal registered
            try:
                expected = prefetch_expectation_from_native_op(
                    command_id, step, operation, cache.cache_controller
                )
                self.register_physical_action(expected)
            except (PhysicalReceiptError, AttributeError, TypeError, ValueError):
                self.counts["prefetch_reservation_rejected"] += 1
                return False
            registered = True
            return True

        outcome = cache.prefetch_gpu_session_node(
            session_id=step.key.session_id,
            session_generation=step.key.session_generation,
            leaf_node_id=step.leaf_node_id,
            leaf_creation_time=step.leaf_creation_time,
            node_id=step.node_id,
            node_creation_time=step.creation_time,
            beliefkv_command_id=command_id,
            beliefkv_before_enqueue=before_enqueue,
        )
        if not outcome.issued:
            if registered:
                self.physical_ledger.cancel_unsubmitted(command_id)
            self.counts["prefetch_native_declined"] += 1
            return None
        if not registered or outcome.node_id != step.node_id:
            self.physical_disabled = True
            raise PhysicalReceiptError("native prefetch issued without a matching reservation")
        self.counts["prefetch_native_issued"] += 1
        return command_id

    def defer_prefill_for_prefetch(self, req: object) -> bool:
        """Hold at most one READY request in waiting until bounded native H2D ACK.

        This runs after the native slot test but before prefix match or running
        admission. A failed/expired step falls back to ordinary PrefillAdder.
        """
        if not self.enable_admission_prefetch or self.physical_disabled:
            return False
        key = _request_key(req)
        if key is None:
            return False
        lease = self._admission_lease
        if lease is not None and lease.key != key:
            if (
                lease.key.request_id != key.request_id
                and (time.monotonic() < lease.expires_at
                     or lease.command_id is not None
                     and self.physical_ledger.is_pending(lease.command_id))
            ):
                return False
            self._admission_lease = None
            lease = None
        if (
            lease is not None
            and lease.command_id is not None
            and self.physical_ledger.is_pending(lease.command_id)
            and not any(
                action.command_id == lease.command_id and action.action == "PREFETCH_GPU"
                for action in self.completed_physical_actions
            )
        ):
            self.counts["admission_prefetch_waiting_ack"] += 1
            return True
        hint = self.demand_hints.get(key.request_id)
        invocation = self.graph.invocations.get(key.invocation_id)
        if (
            self.visible.get(key.request_id) != key
            or self.context_sessions.get(key.context_id) != key
            or key.session_id is None
            or key.session_generation is None
            or invocation is None
            or invocation.state.value != "ready"
            or hint is None
            or hint.predictor_sha256 != self.predictor_sha256
            or not hint.live(key, now_ms=time.monotonic() * 1000)
            or hint.invocation_revision_ts_ms != invocation.updated_ts_ms
            or self._terminal(key)
        ):
            if lease is not None:
                self._admission_lease = None
            return False
        if lease is None:
            lease = _AdmissionPrefetchLease(key, time.monotonic() + 2.0)
            self._admission_lease = lease
        if lease.command_id is not None:
            if any(
                action.command_id == lease.command_id and action.action == "PREFETCH_GPU"
                for action in self.completed_physical_actions
            ):
                lease.command_id = None
                self.counts["admission_prefetch_acked"] += 1
            elif not self.physical_ledger.is_pending(lease.command_id):
                self._admission_lease = None
                self.counts["admission_prefetch_lost_ack"] += 1
                return False
            else:
                self._admission_lease = None
                return False
        if time.monotonic() >= lease.expires_at or lease.issued_nodes >= 2:
            self._admission_lease = None
            return False
        step = self.refreshed_prefetch_gpu_step(source="admission")
        if step is None:
            self._admission_lease = None
            return False
        command = self.issue_prefetch_gpu_step(step, source="admission")
        if command is None:
            self._admission_lease = None
            return False
        lease.command_id = command
        lease.issued_nodes += 1
        self.counts["admission_prefetch_issued"] += 1
        return True

    def on_native_transfer_commit(self, commit: object) -> None:
        """Observe synchronized native ACKs, never infer completion from enqueue."""
        if self.physical_disabled:
            return
        live_epochs = {
            context_id: context.epoch
            for context_id in self.physical_ledger.pending_context_ids
            if (context := self.graph.contexts.get(context_id)) is not None
        }
        live_sessions = {
            context_id: (key.session_id, key.session_generation)
            for context_id in self.physical_ledger.pending_context_ids
            if (key := self.context_sessions.get(context_id)) is not None
            and key.session_id is not None
            and key.session_generation is not None
        }
        try:
            completed = self.physical_ledger.observe(
                commit,
                live_context_epochs=live_epochs,
                live_context_sessions=live_sessions,
            )
        except PhysicalReceiptError:
            self.physical_disabled = True
            self.counts["physical_receipt_failed"] += 1
            return
        self.completed_physical_actions.extend(completed)
        self.counts["native_physical_completed"] += len(completed)

    def register_visible_request(self, req: object) -> bool:
        key = _request_key(req)
        if key is None or self._terminal(key):
            self.counts["invalid_request_identity"] += 1
            return False
        if key.request_id in self.visible:
            raise ValueError(f"duplicate visible request: {key.request_id}")
        if (
            self.tool_wait_hint is not None
            and self.tool_wait_hint.key.context_id == key.context_id
            and self.tool_wait_hint.key != key
        ):
            self.tool_wait_hint = None
            self.shadow_candidate = None
            self._context_tokens.pop(key.context_id, None)
        self.visible[key.request_id] = key
        if key.session_id is not None and key.session_generation is not None:
            self.context_sessions[key.context_id] = key
        else:
            self.context_sessions.pop(key.context_id, None)
        self.semantic_revision += 1
        return True

    def _terminal(self, key: PrefillCandidateKey) -> bool:
        workflow = self.graph.workflows.get(key.root_workflow_id)
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        return bool(
            (workflow is not None and workflow.end_ts_ms is not None)
            or (
                invocation is not None
                and invocation.workflow_id == key.root_workflow_id
                and invocation.state.terminal
            )
            or (
                context is not None
                and context.workflow_id == key.root_workflow_id
                and context.epoch > key.context_epoch
            )
        )

    def terminal_waiting_request_ids(self, native_order: Sequence[object]) -> tuple[str, ...]:
        return tuple(
            key.request_id
            for req in native_order
            if (key := _request_key(req)) is not None
            and key.request_id in self.visible
            and self._terminal(key)
        )

    def retire_terminal_request(self, request_id: str) -> None:
        if request_id in self.visible:
            del self.visible[request_id]
            self.demand_hints.pop(request_id, None)
            self._forget_session(request_id)
            self.semantic_revision += 1
            self.counts["terminal_waiting_aborted"] += 1

    def on_requests_requeued(
        self, requests: Sequence[object], *, is_retracted: bool
    ) -> None:
        for req in requests:
            key = _request_key(req)
            if key is None or key.request_id not in self.visible:
                raise ValueError("requeued request has no live tagged identity")
            if (
                self.tool_wait_hint is not None
                and self.tool_wait_hint.key.context_id == key.context_id
                and self.tool_wait_hint.key != key
            ):
                self.tool_wait_hint = None
                self.shadow_candidate = None
                self._context_tokens.pop(key.context_id, None)
            self.visible[key.request_id] = key
            if key.session_id is not None and key.session_generation is not None:
                self.context_sessions[key.context_id] = key
            else:
                self.context_sessions.pop(key.context_id, None)
            self.demand_hints.pop(key.request_id, None)
            self.semantic_revision += 1

    def _forget_session(self, request_id: str) -> None:
        for context_id, key in tuple(self.context_sessions.items()):
            if key.request_id == request_id:
                del self.context_sessions[context_id]
                self._context_tokens.pop(context_id, None)
                if (
                    self.tool_wait_hint is not None
                    and self.tool_wait_hint.key.context_id == context_id
                ):
                    self.tool_wait_hint = None
                    self.shadow_candidate = None

    def _causal_rank(
        self,
        req: object,
        index: int,
        ready_ranks: dict[str, tuple[int, int]],
    ) -> tuple[int, int, int]:
        key = _request_key(req)
        if key is None:
            return (5, 0, index)
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            invocation is None
            or context is None
            or invocation.workflow_id != key.root_workflow_id
            or context.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or context.epoch != key.context_epoch
        ):
            return (5, 0, index)
        causal_class, depth = ready_ranks.get(key.invocation_id, (5, 0))
        return (causal_class, depth, index)

    def plan_native_prefill(
        self, native_order: Sequence[object], *, running_batch: object, adder: object
    ) -> NativePrefillPlan:
        # Only sort tagged slots; PrefillAdder retains all FULL/MAMBA decisions.
        # Unknown or stale causal state keeps native order.
        tagged = []
        for index, req in enumerate(native_order):
            if getattr(req, "beliefkv_metadata", None) is None:
                continue
            if len(tagged) == 512:
                break
            tagged.append((index, req))
        if len(native_order) > 512:
            self.counts["candidate_bound"] += 1
        workflows = {
            key.root_workflow_id
            for _, req in tagged
            if (key := _request_key(req)) is not None
            and self.visible.get(key.request_id) == key
            and key.root_workflow_id in self.graph.workflows
        }
        ready_ranks = {
            item.invocation_id: (item.score[0], -item.unblock_depth)
            for workflow_id in workflows
            for item in self.frontier.candidates(workflow_id)
        }
        self._submit_local_predictions(tagged, ready_ranks)
        now_ms = time.monotonic() * 1000
        ranks = {
            index: self._causal_rank(req, index, ready_ranks)
            for index, req in tagged
        }
        valid_hints: dict[int, NativeDemandHint] = {}
        members = Counter((rank[0], rank[1]) for rank in ranks.values())
        hinted = Counter()
        for index, req in tagged:
            key = _request_key(req)
            if key is None:
                continue
            hint = self.demand_hints.get(key.request_id)
            if (
                hint is not None
                and hint.live(key, now_ms=now_ms)
                and (
                    hint.invocation_revision_ts_ms is None
                    or getattr(
                        self.graph.invocations.get(key.invocation_id),
                        "updated_ts_ms",
                        None,
                    ) == hint.invocation_revision_ts_ms
                )
            ):
                valid_hints[index] = hint
                hinted[ranks[index][:2]] += 1
        ordered = sorted(
            tagged,
            key=lambda pair: (
                *ranks[pair[0]][:2],
                valid_hints[pair[0]].next_output_tokens
                if ranks[pair[0]][0] < 5
                and hinted[ranks[pair[0]][:2]] == members[ranks[pair[0]][:2]]
                else pair[0],
                pair[0],
            ),
        )
        return compile_native_prefill_plan(
            [
                req for _, req in ordered
                if (key := _request_key(req)) is not None
                and self.visible.get(key.request_id) == key
                and not self._terminal(key)
            ],
            semantic_revision=self.semantic_revision,
        )

    def _submit_local_predictions(
        self,
        tagged: list[tuple[int, object]],
        ready_ranks: dict[str, tuple[int, int]],
    ) -> None:
        if self._model_worker is None or self._model_worker.disabled:
            return
        tasks = []
        signatures = []
        for _, req in tagged:
            key = _request_key(req)
            if (
                key is None
                or self.visible.get(key.request_id) != key
                or key.invocation_id not in ready_ranks
                or self._terminal(key)
            ):
                continue
            invocation = self.graph.invocations[key.invocation_id]
            metadata = req.beliefkv_metadata
            prompt_tokens = len(getattr(req, "origin_input_ids", ()))
            output_tokens = len(getattr(req, "output_ids", ()))
            features = LocalFrontierFeatures(
                invocation_id=key.invocation_id,
                state=invocation.state.value,
                agent_definition_id=invocation.agent_definition_id,
                tool_family=invocation.active_tool_family or "unknown",
                generated_tokens=output_tokens,
                current_sequence_tokens=prompt_tokens + output_tokens,
                llm_round=invocation.llm_round,
                child_count=len(invocation.child_invocation_ids),
                unfinished_child_count=len(invocation.blocking_child_ids),
                is_child=metadata.get("parent_invocation_id") is not None,
            )
            signatures.append((key, invocation.updated_ts_ms, prompt_tokens, output_tokens))
            tasks.append((key, features, invocation.updated_ts_ms))
            if len(tasks) == 8:
                break
        signature = tuple(signatures)
        if tasks and signature != self._last_model_signature:
            self._last_model_signature = signature
            self._model_worker.submit(tuple(tasks))

    def on_prefill_selection(self, rejected: tuple[tuple[str, str], ...]) -> None:
        self.counts.update(reason for _, reason in rejected)

    def on_prefill_candidate_result(
        self, req: object, *, admitted: bool, result: str
    ) -> None:
        if getattr(req, "beliefkv_metadata", None) is not None:
            self.counts["native_admitted" if admitted else f"native_{result}"] += 1
            if admitted:
                self.demand_hints.pop(req.rid, None)

    def on_batch_selected(self, batch: object) -> None:
        pass

    def on_batch_completed(self, batch: object) -> None:
        for req in batch.reqs:
            if req.rid in self.visible and req.finished():
                context_id = self.visible[req.rid].context_id
                key = self.visible[req.rid]
                if (
                    self._model_worker is not None
                    and key.session_id is not None
                    and key.session_generation is not None
                ):
                    self._context_tokens[context_id] = (
                        key.context_epoch,
                        len(getattr(req, "origin_input_ids", ())),
                        len(getattr(req, "output_ids", ())),
                        req.beliefkv_metadata.get("parent_invocation_id") is not None,
                    )
                del self.visible[req.rid]
                self.demand_hints.pop(req.rid, None)
                session_key = self.context_sessions.get(context_id)
                if (
                    session_key is not None
                    and session_key.request_id == req.rid
                    and self._terminal(session_key)
                ):
                    self._forget_session(req.rid)
                    self._context_tokens.pop(context_id, None)
                self.semantic_revision += 1

    def on_abort_request(self, abort: object) -> None:
        removed = [
            rid
            for rid in self.visible
            if getattr(abort, "abort_all", False) or rid.startswith(abort.rid)
        ]
        for rid in removed:
            del self.visible[rid]
            self.demand_hints.pop(rid, None)
            self._forget_session(rid)
            self.semantic_revision += 1
        for context_id, key in tuple(self.context_sessions.items()):
            if getattr(abort, "abort_all", False) or key.request_id.startswith(abort.rid):
                del self.context_sessions[context_id]
                self._context_tokens.pop(context_id, None)
                if self.tool_wait_hint is not None and (
                    self.tool_wait_hint.key.context_id == context_id
                ):
                    self.tool_wait_hint = None
                    self.shadow_candidate = None

    def running_batch_retraction_barrier_required(self, batch: object) -> bool:
        return False

    def on_running_batch_retraction_barrier_drained(self, batch: object) -> None:
        pass

    def plan_running_batch_retraction(self, batch: object) -> None:
        return None
