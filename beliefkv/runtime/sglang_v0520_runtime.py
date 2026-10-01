"""Bounded semantic admission and action-local native H2D for FULL/MAMBA.

The SGLang cache remains the physical capacity and transfer authority.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import islice
import math
import json
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.predictor.composer import observed_boundary_action
from beliefkv.predictor.completion_lead import (
    CompletionLead,
    load_pinned_completion_lead,
)
from beliefkv.runtime.event_channel import RuntimeEventDatagramServer
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry
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
    SessionH2DOpportunity,
    ShadowBackupStep,
    capture_action_local_shadow,
    inspect_session_h2d_opportunity,
    next_prefetch_gpu_step,
    next_shadow_backup_step,
    prefetch_expectation_from_native_op,
    shadow_expectation_from_native_op,
)
from beliefkv.runtime.sglang_v0520_observer import (
    normalize_native_creation_time,
    observe_static_full_mamba_headroom,
)
from beliefkv.predictor.structured_frontier import LocalFrontierFeatures
from beliefkv.runtime.semantic_report_worker import (
    SEMANTIC_TEXT, SemanticReportInput, SemanticReportReply, SemanticReportWorker,
)

if TYPE_CHECKING:
    from beliefkv.core.events import RuntimeEvent


@dataclass
class _AdmissionPrefetchLease:
    key: PrefillCandidateKey
    expires_at: float
    command_id: str | None = None
    issued_nodes: int = 0


CHILD_COMPLETION_INTENT = "beliefkv_child_completion_intent"
WAIT_REFRESH_AGE_MS = 2_000.0
WAIT_REFRESH_SPACING_MS = 500.0


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
    no_step_recorded: bool = False
    drained_for_issued_nodes: int | None = None
    stage_bound: bool = False


@dataclass(frozen=True)
class _CompletionReentryHint:
    key: PrefillCandidateKey
    join_id: str
    child_id: str
    child_epoch: int
    issued_monotonic_ms: float


@dataclass
class _ChildFinalStage:
    key: PrefillCandidateKey
    join_id: str
    child_id: str
    child_epoch: int
    expected_tokens: int
    expires_at: float
    request_id: str | None = None
    generated_tokens: int = 0
    last_service_at: float | None = None
    tokens_per_second: float | None = None
    issued_nodes: int = 0
    semantic_only: bool = False


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
        enable_confirmed_join_canary: bool = False,
        enable_final_stage_prefetch: bool = False,
        completion_lead: CompletionLead | None = None,
        opportunity_dir: str | None = None,
    ) -> None:
        stage_setting = os.environ.get("BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "0")
        if stage_setting not in ("0", "1"):
            raise ValueError("final stage prefetch setting must be 0 or 1")
        enable_final_stage_prefetch = (
            enable_final_stage_prefetch or stage_setting == "1"
        )
        if enable_final_stage_prefetch and (
            not event_socket_path or enable_confirmed_join_canary
        ):
            raise ValueError("final stage prefetch requires an event socket and no canary")
        if enable_confirmed_join_canary and (
            enable_admission_prefetch
            or enable_local_predictor
            or predictor_sha256 is not None
            or predictor_artifact_path is not None
            or not event_socket_path
        ):
            raise ValueError(
                "confirmed JOIN canary requires an event socket and no predictor "
                "or predictive admission"
            )
        if enable_admission_prefetch and (
            not enable_local_predictor or not predictor_artifact_path
        ):
            raise ValueError("admission H2D requires a pinned, live action predictor")
        completion_path = os.environ.get("BELIEFKV_COMPLETION_LEAD_ARTIFACT")
        completion_sha = os.environ.get("BELIEFKV_COMPLETION_LEAD_SHA256")
        if bool(completion_path) != bool(completion_sha):
            raise ValueError("completion lead requires both artifact and SHA-256")
        if completion_path:
            if completion_lead is not None:
                raise ValueError("completion lead supplied by two sources")
            completion_lead = load_pinned_completion_lead(
                completion_path, completion_sha
            )
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
        self.tool_wait_hints: dict[str, NativeToolWaitHint] = {}
        self.join_wait_hints: dict[str, NativeJoinWaitHint] = {}
        self.completion_lead = completion_lead
        self._completion_hints: dict[str, _CompletionReentryHint] = {}
        self._final_stages: dict[str, _ChildFinalStage] = {}
        self._final_request_stages: dict[str, _ChildFinalStage] = {}
        self._h2d_samples: deque[tuple[int, float]] = deque(maxlen=16)
        self._final_priority_normal_admissions = 4
        self._final_priority_promoted: str | None = None
        self._final_priority_native_rank: int | None = None
        self._join_ticket: _JoinPrefetchTicket | None = None
        self.enable_admission_prefetch = enable_admission_prefetch
        self.enable_final_stage_prefetch = enable_final_stage_prefetch
        priority_setting = os.environ.get("BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY")
        self.enable_final_stage_priority = (
            None if priority_setting is None else priority_setting == "1"
        )
        self.enable_prepare_host = os.environ.get("BELIEFKV_ENABLE_PREPARE_HOST", "1") == "1"
        semantic_artifact = os.environ.get("BELIEFKV_SEMANTIC_REPORT_ARTIFACT")
        self._semantic_worker = None
        self._semantic_score_threshold = .5
        if semantic_artifact:
            report = json.loads(
                (Path(semantic_artifact).resolve().parent / "report.json").read_text()
            )
            threshold = report["calibration"]["semantic_event"]["request_operating_point"]["threshold"]
            if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
                raise ValueError("semantic H2D needs the frozen calibration operating point")
            self._semantic_score_threshold = float(threshold)
            self._semantic_worker = SemanticReportWorker(semantic_artifact)
        self._semantic_progress: dict[str, deque[tuple[float, int]]] = {}
        self._semantic_keys: dict[str, PrefillCandidateKey] = {}
        self._semantic_frames: dict[str, RuntimeEvent] = {}
        self._semantic_forecasts: dict[str, SemanticReportReply] = {}
        self._semantic_finished: dict[str, tuple[float, int]] = {}
        self._semantic_submit_ms: dict[str, float] = {}
        self._semantic_tool_counts: Counter[str] = Counter()
        self._runtime_state_next_ms = 0.
        self.enable_confirmed_join_canary = enable_confirmed_join_canary
        self._admission_lease: _AdmissionPrefetchLease | None = None
        self.shadow_candidate: ActionLocalShadowCandidate | None = None
        self._native_cache: object | None = None
        self._context_tokens: dict[str, tuple[int, int, int, bool]] = {}
        self._boundary_history: dict[str, deque[str]] = {}
        self._tool_metadata: dict[
            str, tuple[str, str, str, float | None, str, float | None, int]
        ] = {}
        self._next_wait_refresh_ms = 0.0
        self._refresh_join_next = False
        self._scan_unhinted_next = False
        self._scan_join_next = False
        self._scan_offsets = {"tool_wait": 0, "join_wait": 0}
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
        self._noncontinuing_joins: set[str] = set()
        self.frontier = CausalFrontierScheduler(self.graph)
        self.visible: dict[str, PrefillCandidateKey] = {}
        self.context_sessions: dict[str, PrefillCandidateKey] = {}
        self.physical_ledger = PhysicalTransactionLedger()
        self.completed_physical_actions: deque[PhysicalActionCompleted] = deque(
            maxlen=128
        )
        self.physical_disabled = False
        self.counts: Counter[str] = Counter()
        if enable_confirmed_join_canary:
            self.counts["confirmed_join_canary_configured"] = 1
        opportunity_dir = opportunity_dir or os.environ.get(
            "BELIEFKV_ADMISSION_OPPORTUNITY_DIR"
        )
        self._opportunity_writer = (
            NativeOpportunityTelemetry(opportunity_dir) if opportunity_dir else None
        )
        self._opportunity_next_ms = 0.0
        self._opportunity_cursor = 0
        self.event_server = (
            RuntimeEventDatagramServer(event_socket_path, self.on_events)
            if event_socket_path
            else None
        )

    @property
    def tool_wait_hint(self) -> NativeToolWaitHint | None:
        if not self.tool_wait_hints:
            return None
        now_ms = time.monotonic() * 1000
        return min(
            self.tool_wait_hints.values(),
            key=lambda hint: (
                hint.wait_p10_ms - (now_ms - hint.issued_monotonic_ms),
                hint.issued_monotonic_ms, hint.key.context_id,
            ),
        )

    @tool_wait_hint.setter
    def tool_wait_hint(self, hint: NativeToolWaitHint | None) -> None:
        if hint is None:
            self.tool_wait_hints.clear()
        else:
            self.tool_wait_hints[hint.key.context_id] = hint

    @property
    def join_wait_hint(self) -> NativeJoinWaitHint | None:
        if not self.join_wait_hints:
            return None
        now_ms = time.monotonic() * 1000
        return min(
            self.join_wait_hints.values(),
            key=lambda hint: (
                hint.wait_p10_ms - (now_ms - hint.issued_monotonic_ms),
                hint.issued_monotonic_ms, hint.join_id,
            ),
        )

    @join_wait_hint.setter
    def join_wait_hint(self, hint: NativeJoinWaitHint | None) -> None:
        if hint is None:
            self.join_wait_hints.clear()
        else:
            self.join_wait_hints[hint.join_id] = hint

    def close(self) -> None:
        self._discard_join_ticket("shutdown")
        self._final_stages.clear()
        self._final_request_stages.clear()
        if self._opportunity_writer is not None:
            self._record_runtime_state(final=True)
            self._opportunity_writer.close()
            self._opportunity_writer = None
        if self._model_worker is not None:
            self._model_worker.close()
            self._model_worker = None
        if self._semantic_worker is not None:
            self._semantic_worker.close()
            self._semantic_worker = None
        if self.event_server is not None:
            self.event_server.close()
            self.event_server = None

    def _discard_join_ticket(self, reason: str) -> None:
        ticket = self._join_ticket
        if (
            ticket is not None
            and ticket.phase == "confirmed"
            and self.enable_confirmed_join_canary
            and self._opportunity_writer is not None
        ):
            self._opportunity_writer.record({
                "event": "confirmed_join_ticket_closed",
                "ts_ms": time.time() * 1000.0,
                "join_id": ticket.join_id,
                "workflow_id": ticket.key.root_workflow_id,
                "context_id": ticket.key.context_id,
                "context_epoch": ticket.key.context_epoch,
                "reason": reason,
                "issued_nodes": ticket.issued_nodes,
                "no_step_recorded": ticket.no_step_recorded,
                "command_id": ticket.command_id,
            })
        self._join_ticket = None

    def attach_native_cache(self, cache: object) -> None:
        self._native_cache = cache

    def predictor_fileno(self) -> int | None:
        if self._semantic_worker is not None:
            return self._semantic_worker.fileno()
        if self._model_worker is None or self._model_worker.disabled:
            return None
        return self._model_worker.fileno()

    def on_events(self, events: tuple[RuntimeEvent, ...]) -> None:
        # A provisional callback can arrive behind a confirmed RETURN/epoch
        # advance. It is advisory, so a stale one must not discard the RCCG.
        filtered = []
        for event in events:
            if event.attributes.get(SEMANTIC_TEXT) is True:
                self._capture_semantic_text(event)
                continue
            if event.kind is RuntimeEventKind.STRUCTURED_ACTION and (
                event.attributes.get(CHILD_COMPLETION_INTENT) is True
            ):
                invocation = self.graph.invocations.get(event.invocation_id)
                context = self.graph.contexts.get(event.context_id)
                if (
                    invocation is None or invocation.state.terminal
                    or invocation.context_id != event.context_id
                    or context is None
                    or invocation.workflow_id != event.workflow_id
                ):
                    self.counts["join_intent_stale"] += 1
                    continue
                if context.epoch != event.context_epoch:
                    if (
                        event.attributes.get("child_completion_signal_kind") != "stage"
                        or event.attributes.get("source") != "deepagents_completion_stage"
                        or event.context_epoch is None
                        or context.epoch != event.context_epoch + 1
                        or invocation.active_tool_family is not None
                    ):
                        self.counts["join_intent_stale"] += 1
                        continue
                    current_requests = [
                        rid for rid, key in self.visible.items()
                        if key.invocation_id == invocation.invocation_id
                        and key.context_id == event.context_id
                        and key.context_epoch == context.epoch
                    ]
                    if len(current_requests) != 1:
                        self.counts["final_stage_epoch_handoff_without_request"] += 1
                        continue
                    event = replace(
                        event,
                        context_epoch=context.epoch,
                        attributes={
                            **event.attributes,
                            "completion_stage_origin_epoch": event.context_epoch,
                            "completion_stage_bound_request_id": current_requests[0],
                        },
                    )
                    self.counts["final_stage_epoch_handoff"] += 1
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
            self._noncontinuing_joins.clear()
            self.frontier = CausalFrontierScheduler(self.graph)
            self.demand_hints.clear()
            self._last_model_signature = None
            self.context_sessions.clear()
            self.tool_wait_hint = None
            self.join_wait_hint = None
            self._completion_hints.clear()
            self._final_stages.clear()
            self._final_request_stages.clear()
            self._discard_join_ticket("causal_mirror_discarded")
            self._admission_lease = None
            self.shadow_candidate = None
            self._context_tokens.clear()
            self._boundary_history.clear()
            self._tool_metadata.clear()
            self._next_wait_refresh_ms = 0.0
            self._scan_offsets = {"tool_wait": 0, "join_wait": 0}
            self.semantic_revision += 1
            self.counts["causal_mirror_discarded"] += 1
            raise
        else:
            for event in events:
                if event.kind is RuntimeEventKind.JOIN_CREATE and event.join_id:
                    join = self.graph.joins.get(event.join_id)
                    if join is not None:
                        if event.attributes.get("parent_prefix_continuation") is False:
                            self._noncontinuing_joins.add(join.join_id)
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
                if self._model_worker is not None and event.invocation_id is not None:
                    boundary = observed_boundary_action(event)
                    if boundary is not None:
                        self._boundary_history.setdefault(
                            event.invocation_id, deque(maxlen=8)
                        ).append(boundary)
                    if event.kind is RuntimeEventKind.TOOL_START:
                        attrs = event.attributes
                        self._tool_metadata[event.invocation_id] = (
                            str(attrs.get("backend_class") or "unknown"),
                            str(
                                attrs.get("command_class")
                                or attrs.get("tool_name")
                                or attrs.get("backend_class")
                                or "unknown"
                            ),
                            str(attrs.get("observed_command_class") or "unknown"),
                            (
                                float(attrs["previous_same_input_duration_ms"])
                                if type(attrs.get("previous_same_input_duration_ms"))
                                in (int, float) else None
                            ),
                            str(attrs.get("previous_same_input_status") or ""),
                            (
                                float(attrs["project_class_duration_median_ms"])
                                if type(attrs.get("project_class_duration_median_ms"))
                                in (int, float) else None
                            ),
                            int(attrs.get("project_class_completed_support") or 0),
                        )
                    elif event.kind in (
                        RuntimeEventKind.TOOL_END,
                        RuntimeEventKind.RETURN,
                        RuntimeEventKind.INVOCATION_CANCEL,
                    ):
                        self._tool_metadata.pop(event.invocation_id, None)
                    if event.kind in (
                        RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL
                    ):
                        self._boundary_history.pop(event.invocation_id, None)
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
                    ):
                        self.tool_wait_hints.pop(context_id, None)
                        self.shadow_candidate = None
                if event.kind is RuntimeEventKind.WORKFLOW_END:
                    for key in tuple(self._semantic_keys.values()):
                        if key.root_workflow_id == event.workflow_id:
                            self._clear_semantic_invocation(key.invocation_id)
                    self._noncontinuing_joins = {
                        join_id for join_id in self._noncontinuing_joins
                        if (join := self.graph.joins.get(join_id)) is not None
                        and join.workflow_id != event.workflow_id
                    }
                    for invocation_id in (
                        set(self._boundary_history) | set(self._tool_metadata)
                    ):
                        invocation = self.graph.invocations.get(invocation_id)
                        if invocation is not None and (
                            invocation.workflow_id == event.workflow_id
                        ):
                            self._boundary_history.pop(invocation_id, None)
                            self._tool_metadata.pop(invocation_id, None)
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
                    self.tool_wait_hints = {
                        context: hint for context, hint in self.tool_wait_hints.items()
                        if hint.key.root_workflow_id != event.workflow_id
                    }
                    self.join_wait_hints = {
                        join_id: hint for join_id, hint in self.join_wait_hints.items()
                        if hint.key.root_workflow_id != event.workflow_id
                    }
                    self.shadow_candidate = None
                if event.kind is RuntimeEventKind.TOOL_START and context_id is not None:
                    self._submit_tool_wait(context_id)
            for event in events:
                if event.kind is RuntimeEventKind.STRUCTURED_ACTION:
                    self._observe_child_completion_intent(event)
                elif event.kind is RuntimeEventKind.LLM_SUBMIT:
                    self._clear_semantic_invocation(event.invocation_id)
                    self._bind_final_request(event)
                elif event.kind is RuntimeEventKind.TOOL_START:
                    self._semantic_tool_counts[event.invocation_id] += 1
                    self._clear_semantic_invocation(event.invocation_id)
                    for join_id, stage in tuple(self._final_stages.items()):
                        if stage.child_id == event.invocation_id:
                            self._clear_final_stage(join_id)
                            self.counts["final_stage_tool_invalidated"] += 1
                elif event.kind in (
                    RuntimeEventKind.RETURN, RuntimeEventKind.JOIN_SATISFIED,
                    RuntimeEventKind.JOIN_TIMEOUT, RuntimeEventKind.INVOCATION_CANCEL,
                ):
                    if event.invocation_id is not None:
                        self._clear_semantic_invocation(event.invocation_id)
                    self._advance_join_ticket(event)
                    for join_id, stage in tuple(self._final_stages.items()):
                        if (
                            stage.child_id == event.invocation_id
                            or stage.key.invocation_id == event.invocation_id
                            or stage.join_id == event.join_id
                        ):
                            self._clear_final_stage(join_id)
            self.join_wait_hints = {
                join_id: hint for join_id, hint in self.join_wait_hints.items()
                if self._live_join_hint(hint)
            }
            self._completion_hints = {
                join_id: hint for join_id, hint in self._completion_hints.items()
                if self._live_completion_hint(hint)
            }
            if self._join_ticket is not None and not self._live_join_ticket():
                self._discard_join_ticket("event_invalidated")
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

    def _clear_semantic_invocation(self, invocation_id: str) -> None:
        for rid, key in tuple(self._semantic_keys.items()):
            if key.invocation_id == invocation_id:
                self._semantic_keys.pop(rid, None)
                self._semantic_frames.pop(rid, None)
                self._semantic_forecasts.pop(rid, None)
                self._semantic_progress.pop(rid, None)
                self._semantic_finished.pop(rid, None)
                self._semantic_submit_ms.pop(rid, None)

    def _semantic_key_live(self, key: PrefillCandidateKey, now_ms: float) -> bool:
        child = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        ended = self._semantic_finished.get(key.request_id)
        return bool(
            child is not None and not child.state.terminal
            and child.active_tool_family is None
            and child.workflow_id == key.root_workflow_id
            and child.context_id == key.context_id
            and context is not None and context.epoch == key.context_epoch
            and context.workflow_id == key.root_workflow_id
            and self.context_sessions.get(key.context_id) == key
            and (
                self.visible.get(key.request_id) == key
                or ended is not None and now_ms - ended[0] <= 2_000
            )
        )

    def _capture_semantic_text(self, event: RuntimeEvent) -> None:
        self.counts["semantic_text_received"] += 1
        rid = event.attributes.get("request_id")
        key = self.visible.get(rid) or self._semantic_keys.get(rid)
        if key is None or (
            key.root_workflow_id != event.workflow_id
            or
            key.invocation_id != event.invocation_id
            or key.context_id != event.context_id
            or key.context_epoch != event.context_epoch
        ):
            self.counts["semantic_text_stale"] += 1
            return
        if event.attributes.get("tool_chunk") is True:
            self._clear_semantic_invocation(key.invocation_id)
            for join_id, stage in tuple(self._final_stages.items()):
                if stage.child_id == key.invocation_id:
                    self._clear_final_stage(join_id)
            self.counts["semantic_tool_chunk_invalidated"] += 1
            return
        if self._semantic_worker is None:
            return
        text, chars = (
            event.attributes.get("content_tail"),
            event.attributes.get("content_chars"),
        )
        if type(text) is not str or type(chars) is not int or chars < 32:
            return
        if len(self._semantic_frames) >= 128 and rid not in self._semantic_frames:
            self.counts["semantic_text_capacity"] += 1
            return
        self._semantic_keys[rid] = key
        self._semantic_frames[rid] = event

    def _semantic_parent(self, child_id: str) -> tuple[str, PrefillCandidateKey] | None:
        for join_id in sorted(self._join_by_invocation.get(child_id, ())):
            join = self.graph.joins.get(join_id)
            key = self._join_parent_key(join_id)
            if (
                join is not None and not join.satisfied and join.mode.value == "all"
                and join.member_invocation_ids - join.completed_member_ids == {child_id}
                and key is not None
                and self.graph.invocations[key.invocation_id].state is InvocationState.WAIT_JOIN
            ):
                return join_id, key
        return None

    def _semantic_rate(self, rid: str) -> float | None:
        progress = self._semantic_progress.get(rid, ())
        if len(progress) < 2:
            return None
        end_ms, end_tokens = progress[-1]
        prior = next(
            ((ts, tokens) for ts, tokens in progress if ts >= end_ms - 500),
            progress[0],
        )
        if prior[0] >= end_ms or prior[1] >= end_tokens:
            return None
        return min(500., max(1., (end_tokens - prior[1]) * 1000 / (end_ms - prior[0])))

    def _poll_semantic_reports(self, now_ms: float) -> None:
        worker = self._semantic_worker
        if worker is None:
            return
        for reply in worker.poll():
            item = reply.observation
            if (
                not self._semantic_key_live(item.key, now_ms)
                or not 0 <= now_ms - item.observed_ts_ms <= 1_500
            ):
                self.counts["semantic_result_stale"] += 1
                continue
            self._semantic_forecasts[item.key.request_id] = reply
            self.counts["semantic_result_accepted"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "semantic_child_forecast", "ts_ms": time.time() * 1000,
                    "request_id": item.key.request_id,
                    "context_id": item.key.context_id,
                    "context_epoch": item.key.context_epoch,
                    "score": reply.final_score,
                    "remaining_tokens": reply.middle_tokens,
                    "lower_tokens": reply.lower_tokens, "upper_tokens": reply.upper_tokens,
                    "inference_ms": reply.inference_ms,
                    "observation_age_ms": now_ms - item.observed_ts_ms,
                    "observed_output_tokens": item.observed_output_tokens,
                })
            if reply.final_score < self._semantic_score_threshold:
                continue
            parent = self._semantic_parent(item.key.invocation_id)
            if parent is None:
                continue
            join_id, parent_key = parent
            if join_id not in self._final_stages:
                progress = self._semantic_progress.get(item.key.request_id, ())
                stage = _ChildFinalStage(
                    parent_key, join_id, item.key.invocation_id,
                    item.key.context_epoch,
                    item.observed_output_tokens + max(0, int(reply.middle_tokens)),
                    time.monotonic() + 2.,
                    request_id=item.key.request_id, semantic_only=True,
                    generated_tokens=progress[-1][1] if progress else 0,
                    tokens_per_second=self._semantic_rate(item.key.request_id),
                )
                self._final_stages[join_id] = stage
                self._final_request_stages[item.key.request_id] = stage
                self.counts["semantic_final_stage_created"] += 1
        self.counts["semantic_worker_ready"] = int(worker.ready)
        self.counts["semantic_worker_disabled"] = int(worker.disabled)
        self.counts["semantic_worker_dropped"] = worker.dropped
        if worker.disabled:
            return
        for rid, event in tuple(self._semantic_frames.items()):
            key = self._semantic_keys.get(rid)
            if key is None or not self._semantic_key_live(key, now_ms):
                self._semantic_frames.pop(rid, None)
                self._semantic_forecasts.pop(rid, None)
                continue
            if now_ms - self._semantic_submit_ms.get(rid, 0.) < 250:
                continue
            if now_ms - event.ts_ms > 1_500:
                continue
            progress = self._semantic_progress.get(rid, ())
            # Match offline features: only server progress older than the delivered text.
            observed = next((tokens for ts, tokens in reversed(progress)
                             if ts <= event.ts_ms - 100), None)
            if observed is None or observed < 1:
                continue
            child = self.graph.invocations.get(key.invocation_id)
            native_stage = next((
                stage for stage in self._final_stages.values()
                if stage.child_id == key.invocation_id and not stage.semantic_only
                and self._live_final_stage(stage)
            ), None)
            worker.submit(SemanticReportInput(
                key, event.ts_ms, observed, event.attributes["content_chars"],
                event.attributes["content_tail"][-1024:],
                native_stage is not None,
                native_stage.expected_tokens if native_stage else 0,
                self._semantic_tool_counts[key.invocation_id],
                child.llm_round,
            ))
            self._semantic_submit_ms[rid] = now_ms
            self.counts["semantic_input_submitted"] += 1

    def scheduler_step(self, waiting_queue: Sequence[object] = ()) -> None:
        if self.event_server is not None:
            self.event_server.drain(max_messages=16)
        now_ms = time.monotonic() * 1000
        self._poll_semantic_reports(now_ms)
        for join_id, stage in tuple(self._final_stages.items()):
            if not self._live_final_stage(stage):
                self._clear_final_stage(join_id)
        expired_tool = [
            context for context, hint in self.tool_wait_hints.items()
            if not hint.live(hint.key, now_ms=now_ms)
        ]
        for context in expired_tool:
            self.tool_wait_hints.pop(context)
        if expired_tool:
            self.shadow_candidate = None
            self.counts["tool_wait_expired"] += len(expired_tool)
        expired_join = [
            join_id for join_id, hint in self.join_wait_hints.items()
            if not self._live_join_hint(hint)
        ]
        for join_id in expired_join:
            self.join_wait_hints.pop(join_id)
        self.counts["join_wait_expired"] += len(expired_join)
        self._completion_hints = {
            join_id: hint for join_id, hint in self._completion_hints.items()
            if self._live_completion_hint(hint)
        }
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
                        if ticket is not None and self._live_join_ticket() and (
                            ticket.phase in ("provisional", "confirmed")
                            or ticket.command_id is not None
                        ):
                            self.counts["join_wait_ticket_preserved"] += 1
                            self.counts["join_wait_accepted"] += 1
                            continue
                        if ticket is None or (
                            ticket.key, ticket.join_id
                        ) != (hint.key, hint.join_id):
                            if ticket is None or not self._live_join_ticket():
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
            self._refresh_live_wait_hint()
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
            self._discard_join_ticket(
                "expired" if time.monotonic() >= ticket.expires_at
                else "safe_point_invalidated"
            )
        self._sample_h2d_opportunities(waiting_queue, now_ms=now_ms)
        if self._opportunity_writer is not None and now_ms >= self._runtime_state_next_ms:
            self._runtime_state_next_ms = now_ms + 1_000
            self._record_runtime_state()

    def _record_runtime_state(self, *, final: bool = False) -> None:
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "admission_runtime_state", "ts_ms": time.time() * 1000,
                "final": final, "counts": dict(self.counts),
                "physical_disabled": self.physical_disabled,
                "final_stage_prefetch": self.enable_final_stage_prefetch,
                "final_stage_priority": (
                    self.enable_final_stage_priority
                    if self.enable_final_stage_priority is not None
                    else self.enable_admission_prefetch or self.enable_final_stage_prefetch
                ),
                "prepare_host": self.enable_prepare_host,
                "semantic_worker_configured": self._semantic_worker is not None,
                "semantic_worker_error": (
                    self._semantic_worker.error if self._semantic_worker else ""
                ),
            })

    def _missing_opportunity_detail(
        self, key: PrefillCandidateKey, *, admission_candidate: bool,
    ) -> str:
        """Distinguish stale causal state from a missing native anchor snapshot."""
        if self.context_sessions.get(key.context_id) != key:
            return "context_binding_changed"
        context = self.graph.contexts.get(key.context_id)
        if context is None or context.epoch != key.context_epoch:
            return "context_epoch_changed"
        invocation = self.graph.invocations.get(key.invocation_id)
        if (
            invocation is None
            or invocation.context_id != key.context_id
            or invocation.workflow_id != key.root_workflow_id
            or self._terminal(key)
        ):
            return "invocation_not_live"
        if invocation.state in (
            InvocationState.WAIT_TOOL, InvocationState.WAIT_JOIN,
            InvocationState.READY,
        ) or (
            admission_candidate
            and invocation.state is InvocationState.RUNNING_LLM
            and self.visible.get(key.request_id) == key
        ):
            cache = self._native_cache
            if cache is None or key.session_id is None or key.session_generation is None:
                return "anchor_snapshot_unavailable"
            try:
                leaves = cache.session_refs.snapshot_session_leaf_anchors(
                    key.session_id, key.session_generation, max_leaves=8
                )
                if leaves is None:
                    return "native_anchor_snapshot_rejected"
                if not any(component_leaves for _, component_leaves in leaves):
                    return "session_has_no_cached_leaves"
            except (AttributeError, KeyError, TypeError, ValueError):
                return "native_anchor_snapshot_failed"
            return "anchor_snapshot_normalization_failed"
        return "invocation_state_changed"

    def _sample_h2d_opportunities(
        self, waiting_queue: Sequence[object], *, now_ms: float,
    ) -> None:
        writer = self._opportunity_writer
        if writer is None or now_ms < self._opportunity_next_ms:
            return
        self._opportunity_next_ms = now_ms + 1000.0
        sample_start = time.perf_counter()
        # Bound both the native queue traversal and session map scan at the
        # scheduler safe point; report the unseen tail in the census row.
        waiting: dict[str, PrefillCandidateKey] = {}
        for req in islice(waiting_queue, 512):
            key = _request_key(req)
            if key is not None and self.visible.get(key.request_id) == key:
                waiting[key.context_id] = key
        keys = dict(waiting)
        for context_id, key in islice(self.context_sessions.items(), 2048):
            invocation = self.graph.invocations.get(key.invocation_id)
            if invocation is not None and invocation.state in (
                InvocationState.WAIT_TOOL, InvocationState.WAIT_JOIN,
            ):
                keys.setdefault(context_id, key)
        candidates = list(keys.values())
        start = self._opportunity_cursor % len(candidates) if candidates else 0
        selected = (candidates[start:] + candidates[:start])[:16]
        self._opportunity_cursor = (start + len(selected)) % len(candidates) if candidates else 0
        wall_ms = time.time() * 1000
        census = {
            "event": "safe_point_census", "ts_ms": wall_ms,
            "monotonic_ms": now_ms, "semantic_revision": self.semantic_revision,
            "waiting_queue_size": len(waiting_queue),
            "waiting_scanned": min(len(waiting_queue), 512),
            "session_count": len(self.context_sessions),
            "sessions_scanned": min(len(self.context_sessions), 2048),
            "candidate_count": len(candidates), "sampled": len(selected),
            "pending_transfers": self.physical_ledger.pending_count,
        }
        for key in selected:
            invocation = self.graph.invocations.get(key.invocation_id)
            source = (
                "admission_candidate" if waiting.get(key.context_id) == key
                else "join_wait" if invocation and invocation.state is InvocationState.WAIT_JOIN
                else "tool_wait"
            )
            row = {
                "event": "session_h2d_opportunity", "ts_ms": wall_ms,
                "monotonic_ms": now_ms, "source": source,
                "workflow_id": key.root_workflow_id,
                "request_id": key.request_id, "invocation_id": key.invocation_id,
                "context_id": key.context_id, "context_epoch": key.context_epoch,
                "attempt_id": key.attempt_id, "session_id": key.session_id,
                "session_generation": key.session_generation,
                "invocation_state": invocation.state.value if invocation else None,
                "semantic_revision": self.semantic_revision,
            }
            observation = self.inspect_context_h2d_opportunity(
                context_id=key.context_id, context_epoch=key.context_epoch,
                admission_candidate=source == "admission_candidate",
            )
            if observation is None:
                row["reason"] = (
                    "no_bound_session" if key.session_id is None
                    or key.session_generation is None else
                    "native_cache_unavailable" if self._native_cache is None else
                    "no_live_session_or_anchors"
                )
                if row["reason"] == "no_live_session_or_anchors":
                    row["no_live_detail"] = self._missing_opportunity_detail(
                        key, admission_candidate=source == "admission_candidate",
                    )
            else:
                headroom = observation.headroom
                step = observation.step
                row.update({
                    "reason": (
                        "headroom_unobservable" if not headroom.observable
                        else (observation.no_step_reason or "no_host_backed_step")
                        if step is None
                        else "fits_current_free_lists"
                        if observation.fits_current_free_lists else "insufficient_free_lists"
                    ),
                    "headroom_reason": headroom.reason,
                    "headroom_observable": headroom.observable,
                    "device_full_free_tokens": headroom.device_full_free_tokens,
                    "device_mamba_free_slots": headroom.device_mamba_free_slots,
                    "host_full_free_tokens": headroom.host_full_free_tokens,
                    "host_mamba_free_slots": headroom.host_mamba_free_slots,
                    "required_full_tokens": observation.required_full_tokens,
                    "required_mamba_slots": observation.required_mamba_slots,
                    "host_backed_full_missing_device_tokens":
                        observation.host_backed_full_missing_device_tokens,
                    "host_backed_mamba_missing_device_nodes":
                        observation.host_backed_mamba_missing_device_nodes,
                    "unbacked_full_nodes": observation.unbacked_full_nodes,
                    "unbacked_mamba_leaves": observation.unbacked_mamba_leaves,
                    "blocked_detail": observation.blocked_detail,
                    "node_id": step.node_id if step else None,
                    "node_creation_time": step.creation_time if step else None,
                    "leaf_node_id": step.leaf_node_id if step else None,
                    "leaf_creation_time": step.leaf_creation_time if step else None,
                    "reusable_input_tokens": observation.anchors.reusable_input_tokens,
                    "fits_current_free_lists": observation.fits_current_free_lists,
                })
            if source in ("tool_wait", "join_wait") and key.session_id is not None:
                self._observe_prepare_opportunity(key, row, observation)
            writer.record(row)
        census["sample_wall_ms"] = (time.perf_counter() - sample_start) * 1000
        writer.record(census)

    def _observe_prepare_opportunity(
        self, key: PrefillCandidateKey, row: dict,
        h2d: SessionH2DOpportunity | None,
    ) -> None:
        cache = self._native_cache
        if cache is None:
            row["prepare_reason"] = "native_cache_unavailable"
            return
        policy = getattr(getattr(cache, "cache_controller", None), "write_policy", None)
        if (
            getattr(cache, "enable_session_radix_cache", False) is not True
            or policy not in ("write_through", "write_through_selective", "write_back")
            or (
                policy == "write_back"
                and getattr(getattr(cache, "tree_core", None), "is_write_back", False)
                is not True
            )
        ):
            row["prepare_reason"] = "native_prepare_prerequisites_disabled"
            return
        candidate = self.capture_shadow_candidate(
            cache, context_id=key.context_id, context_epoch=key.context_epoch,
        )
        if not isinstance(candidate, ActionLocalShadowCandidate):
            row["prepare_reason"] = "no_observable_shadow_closure"
            return
        step = next_shadow_backup_step(candidate)
        if step is None:
            row["prepare_reason"] = "no_eligible_shadow_step"
            return
        node = next(node for node in candidate.nodes if node.node_id == step.node_id)
        full_needed = max(node.full_device_tokens - node.full_host_tokens, 0)
        mamba_needed = int(node.mamba_device_present and not node.mamba_host_present)
        headroom = (
            h2d.headroom if h2d is not None else
            observe_static_full_mamba_headroom(cache)
        )
        fits = (
            headroom.host_full_free_tokens >= full_needed
            and headroom.host_mamba_free_slots >= mamba_needed
            if headroom.observable else None
        )
        row.update({
            "prepare_reason": (
                "headroom_unobservable" if not headroom.observable else
                "fits_current_host_free_lists" if fits else
                "insufficient_host_free_lists"
            ),
            "prepare_node_id": step.node_id,
            "prepare_node_creation_time": step.creation_time,
            "prepare_leaf_node_id": step.leaf_node_id,
            "prepare_leaf_creation_time": step.leaf_creation_time,
            "prepare_required_full_tokens": full_needed,
            "prepare_required_mamba_slots": mamba_needed,
            "prepare_host_full_free_tokens": headroom.host_full_free_tokens,
            "prepare_host_mamba_free_slots": headroom.host_mamba_free_slots,
            "prepare_fits_current_host_free_lists": fits,
        })

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

    def _refresh_live_wait_hint(self) -> None:
        worker = self._model_worker
        if worker is None or not callable(
            ready := getattr(worker, "idle_for_refresh", None)
        ) or not ready():
            return
        now_ms = time.monotonic() * 1000
        if now_ms < self._next_wait_refresh_ms:
            return
        tool = min(
            (hint for hint in self.tool_wait_hints.values()
             if hint.live(hint.key, now_ms=now_ms)
             and now_ms - hint.issued_monotonic_ms >= WAIT_REFRESH_AGE_MS),
            key=lambda hint: hint.issued_monotonic_ms, default=None,
        )
        join = min(
            (hint for hint in self.join_wait_hints.values()
             if self._live_join_hint(hint)
             and now_ms - hint.issued_monotonic_ms >= WAIT_REFRESH_AGE_MS),
            key=lambda hint: hint.issued_monotonic_ms, default=None,
        )
        due_tool = tool is not None
        due_join = join is not None
        self._next_wait_refresh_ms = now_ms + WAIT_REFRESH_SPACING_MS
        if (not (due_tool or due_join) or self._scan_unhinted_next) and (
            self._scan_unhinted_wait()
        ):
            self._scan_unhinted_next = False
            return
        if not (due_tool or due_join):
            return
        self._scan_unhinted_next = True
        use_join = due_join and (not due_tool or self._refresh_join_next)
        self._refresh_join_next = not use_join
        if use_join:
            self._submit_join_wait((), join_ids={join.join_id})
            self.counts["join_wait_refresh_submitted"] += 1
        else:
            self._submit_tool_wait(tool.key.context_id)
            self.counts["tool_wait_refresh_submitted"] += 1

    def _scan_unhinted_wait(self) -> bool:
        """Revisit overflowed or unhinted waits only while the worker is idle."""
        tools = sorted(
            context_id for context_id, key in self.context_sessions.items()
            if context_id not in self.tool_wait_hints
            and (invocation := self.graph.invocations.get(key.invocation_id)) is not None
            and invocation.state is InvocationState.WAIT_TOOL
            and not self._terminal(key)
        )
        joins = sorted(
            join_id for join_id, join in self.graph.joins.items()
            if join_id not in self.join_wait_hints and not join.satisfied
            and self._join_parent_key(join_id) is not None
        )
        if not tools and not joins:
            return False
        use_join = bool(joins) and (not tools or self._scan_join_next)
        kind = "join_wait" if use_join else "tool_wait"
        targets = joins if use_join else tools
        offset = self._scan_offsets[kind] % len(targets)
        selected = (targets[offset:] + targets[:offset])[:8]
        self._scan_offsets[kind] = (offset + len(selected)) % len(targets)
        self._scan_join_next = not use_join
        if use_join:
            self._submit_join_wait((), join_ids=set(selected))
        else:
            for context_id in selected:
                self._submit_tool_wait(context_id)
        self.counts[f"{kind}_unhinted_scanned"] += len(selected)
        return True

    def _observe_child_completion_intent(self, event: RuntimeEvent) -> None:
        if self.enable_confirmed_join_canary:
            return
        if event.attributes.get(CHILD_COMPLETION_INTENT) is not True:
            return
        join_id = event.join_id
        if join_id in self._noncontinuing_joins:
            self.counts["join_prefetch_prefix_discontinuous"] += 1
            return
        child_id = event.invocation_id
        join = self.graph.joins.get(join_id) if isinstance(join_id, str) else None
        child = self.graph.invocations.get(child_id) if child_id else None
        context = self.graph.contexts.get(event.context_id) if event.context_id else None
        signal_kind = event.attributes.get(
            "child_completion_signal_kind", "explicit"
        )
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
            or signal_kind not in ("stage", "explicit", "natural_final")
            or (
                signal_kind != "stage"
                and (
                    event.attributes.get("structured_action_names") != (
                        ["ChildCompletion"] if signal_kind == "explicit" else []
                    )
                    or not isinstance(event.attributes.get("request_id"), str)
                    or not event.attributes["request_id"]
                )
            )
        ):
            self.counts["join_intent_stale"] += 1
            return
        key = self._join_parent_key(join_id)
        parent = self.graph.invocations.get(key.invocation_id) if key else None
        if key is None or parent.state is not InvocationState.WAIT_JOIN:
            self.counts["join_intent_stale"] += 1
            return
        if signal_kind == "stage":
            estimated = event.attributes.get("estimated_final_report_tokens")
            if type(estimated) is not int or not 1 <= estimated <= 4096:
                self.counts["final_stage_no_estimate"] += 1
                return
            self._clear_final_stage(join_id)
            bound_request_id = event.attributes.get(
                "completion_stage_bound_request_id"
            )
            stage = _ChildFinalStage(
                key, join_id, child_id, context.epoch, estimated,
                time.monotonic() + 120.0,
                request_id=bound_request_id,
            )
            self._final_stages[join_id] = stage
            if bound_request_id is not None:
                self._final_request_stages[bound_request_id] = stage
            self.counts["final_stage_accepted"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "child_final_stage_accepted",
                    "ts_ms": time.time() * 1000.0,
                    "workflow_id": event.workflow_id,
                    "join_id": join_id,
                    "child_invocation_id": child_id,
                    "context_epoch": context.epoch,
                    "estimated_final_report_tokens": estimated,
                    "epoch_handoff": bound_request_id is not None,
                })
            return
        if join_id in self._final_stages:
            # The explicit stage is governed by service progress, not a
            # second, unconditional two-second completion-intent lease.
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
        self.counts[f"join_intent_{signal_kind}_accepted"] += 1
        age_ms = time.monotonic() * 1000 - event.ts_ms
        if age_ms >= 0:
            for limit in (50, 100, 250, 500):
                if age_ms <= limit:
                    self.counts[f"join_intent_delivery_le_{limit}ms"] += 1
            if age_ms > 500:
                self.counts["join_intent_delivery_over_500ms"] += 1
        if (
            self.completion_lead is not None
            and 0 <= age_ms <= self.completion_lead.p90_ms
        ):
            self._completion_hints[join_id] = _CompletionReentryHint(
                key, join_id, child_id, context.epoch, event.ts_ms
            )
            self.counts["join_completion_forecast_accepted"] += 1
        elif self.completion_lead is not None:
            self.counts["join_completion_forecast_too_late"] += 1

    def _clear_final_stage(self, join_id: str) -> None:
        stage = self._final_stages.pop(join_id, None)
        if stage is not None and stage.request_id is not None:
            self._final_request_stages.pop(stage.request_id, None)
        ticket = self._join_ticket
        if (
            ticket is not None and ticket.join_id == join_id
            and ticket.stage_bound and ticket.phase == "provisional"
        ):
            self._discard_join_ticket("final_stage_invalidated")

    def _live_final_stage(self, stage: _ChildFinalStage) -> bool:
        join = self.graph.joins.get(stage.join_id)
        child = self.graph.invocations.get(stage.child_id)
        context = self.graph.contexts.get(child.context_id) if child else None
        parent = self.graph.invocations.get(stage.key.invocation_id)
        return bool(
            time.monotonic() < stage.expires_at
            and join is not None and not join.satisfied
            and join.mode.value == "all"
            and join.member_invocation_ids - join.completed_member_ids
            == {stage.child_id}
            and child is not None and not child.state.terminal
            and context is not None and context.epoch == stage.child_epoch
            and parent is not None and parent.state is InvocationState.WAIT_JOIN
            and parent.join_id == stage.join_id
            and self.context_sessions.get(stage.key.context_id) == stage.key
        )

    def _bind_final_request(self, event: RuntimeEvent) -> None:
        for stage in self._final_stages.values():
            if (
                stage.child_id == event.invocation_id
                and stage.request_id is None
                and type(event.attributes.get("request_id")) is str
                and event.context_id is not None
                and event.context_epoch is not None
                and event.context_epoch > stage.child_epoch
            ):
                stage.child_epoch = event.context_epoch
                stage.request_id = event.attributes["request_id"]
                self._final_request_stages[stage.request_id] = stage
                self.counts["final_request_bound"] += 1

    def _roll_final_stage(self) -> None:
        if not (
            self.enable_final_stage_prefetch or self.enable_admission_prefetch
        ) or self.physical_disabled:
            return
        if len(self._h2d_samples) < 3 or self.physical_ledger.pending_count:
            return
        if self._join_ticket is not None and self._live_join_ticket():
            return
        for stage in tuple(self._final_stages.values()):
            if self._semantic_worker is not None and stage.request_id is not None:
                progress = self._semantic_progress.get(stage.request_id, ())
                if progress:
                    stage.generated_tokens = progress[-1][1]
                    stage.tokens_per_second = self._semantic_rate(stage.request_id)
            if (
                not self._live_final_stage(stage) or stage.request_id is None
                or stage.generated_tokens < 16
                or stage.tokens_per_second is None
                or stage.issued_nodes >= 2
            ):
                continue
            child_key = self.visible.get(stage.request_id) or self._semantic_keys.get(stage.request_id)
            if (
                child_key is None or child_key.invocation_id != stage.child_id
                or child_key.context_epoch != stage.child_epoch
            ):
                continue
            if self._semantic_worker is not None:
                forecast = self._semantic_forecasts.get(stage.request_id)
                now_ms = time.monotonic() * 1000
                if (
                    forecast is None or forecast.final_score < self._semantic_score_threshold
                    or now_ms - forecast.observation.observed_ts_ms > 1_500
                    or not self._semantic_key_live(forecast.observation.key, now_ms)
                ):
                    self.counts["semantic_latest_start_no_live_forecast"] += 1
                    continue
                progress = self._semantic_progress.get(stage.request_id, ())
                generated = progress[-1][1] if progress else stage.generated_tokens
                remaining = max(
                    0., forecast.middle_tokens
                    - max(0, generated - forecast.observation.observed_output_tokens),
                )
                # EOS is known GPU progress, not confirmation that the child RETURNed.
                if stage.request_id in self._semantic_finished:
                    remaining = 0.
                remaining_ms = remaining * 1000 / stage.tokens_per_second
            else:
                remaining = stage.expected_tokens - stage.generated_tokens
                if remaining < 4:
                    continue
                remaining_ms = remaining * 1000 / stage.tokens_per_second
            if remaining_ms > 2_000:
                if self._semantic_worker is not None:
                    self.counts["semantic_h2d_too_early"] += 1
                continue
            observation = self.inspect_context_h2d_opportunity(
                context_id=stage.key.context_id,
                context_epoch=stage.key.context_epoch,
            )
            if observation is None or observation.step is None:
                if self._semantic_worker is not None:
                    self.counts["semantic_h2d_no_host_target"] += 1
                continue
            if observation.fits_current_free_lists is not True:
                if self._semantic_worker is not None:
                    self.counts["semantic_h2d_no_free_capacity"] += 1
                continue
            cache = self._native_cache
            entries = getattr(
                getattr(getattr(cache, "cache_controller", None),
                        "mem_pool_host", None), "entry_map", {},
            )
            try:
                required_bytes = sum(
                    tokens * entries[name].host_pool.size_per_token
                    for name, tokens in (
                        ("kv", observation.required_full_tokens),
                        ("mamba", observation.required_mamba_slots),
                    )
                )
            except (AttributeError, KeyError, TypeError):
                continue
            if required_bytes <= 0:
                continue
            # Use measured synchronized H2D ACKs, including queueing. A lack
            # of evidence leaves this signal advisory, not a transfer permit.
            h2d_ms = max(
                duration_ms * required_bytes / sample_bytes
                for sample_bytes, duration_ms in self._h2d_samples
            )
            # Do not occupy HBM far ahead of RETURN, even when the largest
            # historical transfer was slow.
            if remaining_ms > min(h2d_ms + 250, 2_000):
                if self._semantic_worker is not None:
                    self.counts["semantic_h2d_not_latest_start"] += 1
                continue
            join = self.graph.joins[stage.join_id]
            self._join_ticket = _JoinPrefetchTicket(
                stage.key, stage.join_id, join.mode.value,
                tuple(sorted(join.member_invocation_ids)),
                "provisional", min(stage.expires_at, time.monotonic() + 2.0),
                issued_nodes=stage.issued_nodes,
                stage_bound=True,
            )
            self.counts["final_stage_latest_start"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "final_stage_latest_start",
                    "ts_ms": time.time() * 1000,
                    "join_id": stage.join_id,
                    "workflow_id": stage.key.root_workflow_id,
                    "child_request_id": stage.request_id,
                    "generated_tokens": stage.generated_tokens,
                    "expected_tokens": stage.expected_tokens,
                    "remaining_ms": remaining_ms,
                    "h2d_ms": h2d_ms,
                    "required_bytes": required_bytes,
                })
            break

    def _live_completion_hint(self, hint: _CompletionReentryHint) -> bool:
        if self.completion_lead is None:
            return False
        now_ms = time.monotonic() * 1000
        join = self.graph.joins.get(hint.join_id)
        parent = self.graph.invocations.get(hint.key.invocation_id)
        child = self.graph.invocations.get(hint.child_id)
        context = self.graph.contexts.get(child.context_id) if child else None
        return bool(
            hint.issued_monotonic_ms <= now_ms
            <= hint.issued_monotonic_ms + self.completion_lead.p90_ms
            and join is not None and not join.satisfied
            and join.workflow_id == hint.key.root_workflow_id
            and hint.child_id in join.member_invocation_ids - join.completed_member_ids
            and parent is not None and parent.state is InvocationState.WAIT_JOIN
            and parent.join_id == hint.join_id
            and self.context_sessions.get(hint.key.context_id) == hint.key
            and child is not None and not child.state.terminal
            and context is not None and context.epoch == hint.child_epoch
        )

    def read_only_join_completion_forecast(
        self, join_id: str
    ) -> tuple[float, float, float] | None:
        """Conditional on a fresh completion intent; never authorizes H2D."""
        hint = self._completion_hints.get(join_id)
        if hint is None or not self._live_completion_hint(hint):
            self._completion_hints.pop(join_id, None)
            return None
        assert self.completion_lead is not None
        age_ms = time.monotonic() * 1000 - hint.issued_monotonic_ms
        return tuple(
            max(0.0, quantile - age_ms)
            for quantile in (
                self.completion_lead.p10_ms,
                self.completion_lead.p50_ms,
                self.completion_lead.p90_ms,
            )
        )

    def _advance_join_ticket(self, event: RuntimeEvent) -> None:
        ticket = self._join_ticket
        if ticket is not None and ticket.join_id in self._noncontinuing_joins:
            self._discard_join_ticket("noncontinuing_join")
            return
        if ticket is None:
            if not (
                self.enable_admission_prefetch or self.enable_confirmed_join_canary
            ) or event.kind not in (
                RuntimeEventKind.RETURN, RuntimeEventKind.JOIN_SATISFIED,
            ):
                return
            candidate_joins = (
                (event.join_id,) if event.join_id is not None else
                tuple(sorted(self._join_by_invocation.get(event.invocation_id, ())))
            )
            for join_id in candidate_joins:
                if join_id in self._noncontinuing_joins:
                    self.counts["join_prefetch_prefix_discontinuous"] += 1
                    continue
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
                if self.enable_confirmed_join_canary and self._opportunity_writer:
                    self._opportunity_writer.record({
                        "event": "confirmed_join_ticket",
                        "ts_ms": time.time() * 1000.0,
                        "join_id": join_id,
                        "workflow_id": key.root_workflow_id,
                        "context_id": key.context_id,
                        "context_epoch": key.context_epoch,
                        "session_id": key.session_id,
                        "session_generation": key.session_generation,
                    })
                return
            return
        join = self.graph.joins.get(ticket.join_id)
        if join is None or join.workflow_id != ticket.key.root_workflow_id:
            self._discard_join_ticket("join_removed_or_changed")
            return
        if event.kind is RuntimeEventKind.JOIN_TIMEOUT and event.join_id == ticket.join_id:
            self._discard_join_ticket("join_timeout")
        elif event.kind is RuntimeEventKind.INVOCATION_CANCEL and (
            event.invocation_id == ticket.key.invocation_id
            or event.invocation_id in ticket.member_ids
        ):
            self._discard_join_ticket("invocation_canceled")
        elif join.satisfied and (
            event.join_id == ticket.join_id
            or event.invocation_id in ticket.member_ids
        ):
            if self._semantic_worker is not None:
                self._discard_join_ticket("semantic_join_already_returned")
                return
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
            (
                self.enable_admission_prefetch
                or self.enable_final_stage_prefetch and ticket.stage_bound
                or self.enable_confirmed_join_canary and ticket.phase == "confirmed"
            ) and not self.physical_disabled
            and ticket.join_id not in self._noncontinuing_joins
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
            and (
                not ticket.stage_bound or ticket.phase == "confirmed"
                or (
                    (stage := self._final_stages.get(ticket.join_id)) is not None
                    and stage.key == key and self._live_final_stage(stage)
                )
            )
            and not self._terminal(key)
        )

    def dispatch_join_prefetch(self) -> None:
        """One action-local native H2D per safe point, never grant admission."""
        self._roll_final_stage()
        ticket = self._join_ticket
        if ticket is None or not self._live_join_ticket():
            if ticket is not None:
                self._discard_join_ticket(
                    "expired" if time.monotonic() >= ticket.expires_at
                    else "dispatch_invalidated"
                )
            return
        if ticket.command_id is not None:
            if self.physical_ledger.is_pending(ticket.command_id):
                return
            if not any(
                action.command_id == ticket.command_id
                and action.action == "PREFETCH_GPU"
                for action in self.completed_physical_actions
            ):
                self._discard_join_ticket("action_ack_missing")
                self.counts["join_prefetch_lost_ack"] += 1
                return
            ticket.command_id = None
            self.counts["join_prefetch_acked"] += 1
        max_nodes = 1 if self.enable_confirmed_join_canary else 2
        if ticket.issued_nodes >= max_nodes or self.physical_ledger.pending_count:
            return
        if ticket.phase == "probabilistic":
            hint = self.join_wait_hints.get(ticket.join_id)
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
            if (
                self.enable_confirmed_join_canary
                and not ticket.no_step_recorded
                and self._opportunity_writer is not None
            ):
                observation = self.inspect_context_h2d_opportunity(
                    context_id=ticket.key.context_id,
                    context_epoch=ticket.key.context_epoch,
                )
                no_step_record = {
                    "event": "confirmed_join_no_h2d_step",
                    "ts_ms": time.time() * 1000.0,
                    "join_id": ticket.join_id,
                    "workflow_id": ticket.key.root_workflow_id,
                    "context_id": ticket.key.context_id,
                    "context_epoch": ticket.key.context_epoch,
                    "session_id": ticket.key.session_id,
                    "session_generation": ticket.key.session_generation,
                    "reason": (
                        "no_live_session_or_anchors" if observation is None
                        else observation.no_step_reason
                        if observation.step is None
                        else "step_changed_during_revalidation"
                    ),
                    "host_backed_full_missing_device_tokens": (
                        observation.host_backed_full_missing_device_tokens
                        if observation is not None else None
                    ),
                    "host_backed_mamba_missing_device_nodes": (
                        observation.host_backed_mamba_missing_device_nodes
                        if observation is not None else None
                    ),
                    "fits_current_free_lists": (
                        observation.fits_current_free_lists
                        if observation is not None else None
                    ),
                }
                if observation is None:
                    no_step_record["no_live_detail"] = self._missing_opportunity_detail(
                        ticket.key, admission_candidate=False,
                    )
                self._opportunity_writer.record(no_step_record)
                ticket.no_step_recorded = True
            return
        command = self.issue_prefetch_gpu_step(step, source="join_ticket")
        if command is not None:
            ticket.command_id = command
            ticket.issued_nodes += 1
            stage = self._final_stages.get(ticket.join_id)
            if stage is not None and ticket.phase == "provisional":
                stage.issued_nodes = ticket.issued_nodes
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
        now_ms = time.monotonic() * 1000
        features = self._local_frontier_features(
            invocation, context.epoch, now_ms=now_ms
        )
        worker.submit_tool_wait(((key, features, invocation.updated_ts_ms),))
        self.counts["tool_wait_submitted"] += 1

    def _local_frontier_features(
        self, invocation: object, context_epoch: int, *, now_ms: float
    ) -> LocalFrontierFeatures:
        stored = self._context_tokens.get(invocation.context_id)
        prompt, output, stored_child = (
            (stored[1], stored[2], stored[3])
            if stored is not None and stored[0] == context_epoch
            else (0, 0, False)
        )
        active_tools = [
            item for item in self.graph.invocations.values()
            if item.state is InvocationState.WAIT_TOOL
        ]
        family = invocation.active_tool_family or "unknown"
        family_count = sum(
            item.active_tool_family == family for item in active_tools
        )
        (backend, command, observed_command, previous_duration,
         previous_status, project_duration, project_support) = (
            self._tool_metadata.get(
                invocation.invocation_id,
                ("unknown", "unknown", "unknown", None, "", None, 0),
            )
        )
        return LocalFrontierFeatures(
            invocation_id=invocation.invocation_id,
            state=invocation.state.value,
            agent_definition_id=invocation.agent_definition_id,
            boundary_history=tuple(
                self._boundary_history.get(invocation.invocation_id, ())
            ),
            tool_family=family,
            backend_class=backend,
            command_class=command,
            observed_command_class=observed_command,
            previous_same_input_duration_ms=(
                previous_duration if previous_status == "success" else None
            ),
            previous_failed_same_input_duration_ms=(
                previous_duration
                if previous_status == "error"
                and bool(stored_child or invocation.parent_invocation_id)
                and previous_duration is not None and previous_duration > 100
                else None
            ),
            project_class_duration_median_ms=(
                project_duration
                if project_support >= 16 and command == "execute"
                and bool(stored_child or invocation.parent_invocation_id)
                else None
            ),
            generated_tokens=output,
            elapsed_wait_ms=max(
                0.0, now_ms - invocation.active_tool_start_ms
            ) if invocation.active_tool_start_ms is not None else 0.0,
            current_sequence_tokens=prompt + output,
            active_tool_count=len(active_tools),
            backend_pressure=(
                f"active_family:{family_count}"
                if family != "unknown" else "unknown"
            ),
            invocation_elapsed_ms=max(0.0, now_ms - invocation.created_ts_ms),
            state_elapsed_ms=max(0.0, now_ms - invocation.updated_ts_ms),
            llm_round=invocation.llm_round,
            child_count=len(invocation.child_invocation_ids),
            unfinished_child_count=len(invocation.blocking_child_ids),
            is_child=stored_child or invocation.parent_invocation_id is not None,
        )

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
        self.tool_wait_hints[key.context_id] = hint
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

    def _submit_join_wait(
        self, events: tuple[RuntimeEvent, ...], *, join_ids: set[str] | None = None
    ) -> None:
        worker = self._model_worker
        if worker is None or worker.disabled:
            return
        now_ms = time.monotonic() * 1000
        affected = {event.invocation_id for event in events if event.invocation_id}
        affected_joins = (
            set(join_ids) if join_ids is not None
            else {event.join_id for event in events if event.join_id}
        )
        for invocation_id in affected:
            affected_joins.update(self._join_by_invocation.get(invocation_id, ()))
        items = []
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
                    children.append((
                        child_id,
                        self._local_frontier_features(
                            child, child_context.epoch, now_ms=now_ms
                        ),
                        child.updated_ts_ms,
                        child_context.epoch,
                    ))
                if len(children) != len(join.member_invocation_ids - join.completed_member_ids):
                    continue
                items.append((
                    key, parent.updated_ts_ms, join.join_id, join.mode.value,
                    tuple(sorted(join.member_invocation_ids)),
                    tuple(sorted(join.completed_member_ids)), tuple(children),
                ))
                if len(items) == 8:
                    break
            if len(items) == 8:
                break
        if items:
            worker.submit_join_wait(tuple(items))
            self.counts["join_wait_submitted"] += len(items)

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
            if leaves is not None:
                leaves = tuple(
                    (
                        component,
                        tuple(
                            (node_id, normalize_native_creation_time(created))
                            for node_id, created in component_anchors
                        ),
                    )
                    for component, component_anchors in leaves
                )
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if leaves is None or not any(anchors for _, anchors in leaves):
            return None
        return ContextSessionAnchors(
            key=key, component_leaves=leaves,
            captured_monotonic_s=time.monotonic(),
            reusable_input_tokens=(
                tokens[1]
                if (tokens := self._context_tokens.get(context_id)) is not None
                and tokens[0] == key.context_epoch else None
            ),
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

    def inspect_context_h2d_opportunity(
        self, *, context_id: str, context_epoch: int,
        admission_candidate: bool = False,
    ) -> SessionH2DOpportunity | None:
        """Read action-local session/allocator evidence; never dispatch H2D."""
        cache = self._native_cache
        key = self.context_sessions.get(context_id)
        invocation = self.graph.invocations.get(key.invocation_id) if key else None
        if (
            cache is None or key is None
            or key.context_epoch != context_epoch
            or invocation is None
            or invocation.context_id != context_id
            or invocation.workflow_id != key.root_workflow_id
            or invocation.state not in (
                InvocationState.WAIT_TOOL, InvocationState.WAIT_JOIN,
                InvocationState.READY,
            )
            and not (
                admission_candidate
                and invocation.state is InvocationState.RUNNING_LLM
                and self.visible.get(key.request_id) == key
            )
            or self._terminal(key)
        ):
            return None
        anchors = self.snapshot_session_anchors(
            cache, context_id=context_id, context_epoch=context_epoch
        )
        return (
            inspect_session_h2d_opportunity(cache, anchors)
            if anchors is not None else None
        )

    def refreshed_shadow_backup_step(
        self, *, context_id: str | None = None
    ) -> ShadowBackupStep | None:
        """Recheck a tool wait and its native closure at the action safe point."""
        if not self.enable_prepare_host:
            return None
        hint = (self.tool_wait_hints.get(context_id) if context_id is not None
                else self.tool_wait_hint)
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
            self.tool_wait_hints.pop(key.context_id, None)
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
            or self.refreshed_shadow_backup_step(context_id=step.key.context_id) != step
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
        self, *, source: str = "tool_wait", context_id: str | None = None
    ) -> PrefetchLoadStep | None:
        """Revalidate the explicit causal or admission source before native H2D."""
        cache = self._native_cache
        if cache is None:
            return None
        if source == "tool_wait":
            hint = (self.tool_wait_hints.get(context_id) if context_id is not None
                    else self.tool_wait_hint)
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
            hint = self.join_wait_hints.get(ticket.join_id)
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
            or (
                invocation.state.value not in ("ready", "running_llm")
                if source == "admission"
                else invocation.state.value != (
                    "ready" if source == "join_ticket"
                    and ticket.phase == "confirmed"
                    else "wait_join" if source in ("join_wait", "join_ticket")
                    else "wait_tool"
                )
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
        if self.enable_confirmed_join_canary and source != "join_ticket":
            return None
        cache = self._native_cache
        if (
            self.physical_disabled
            or cache is None
            or not isinstance(step, PrefetchLoadStep)
            or self.refreshed_prefetch_gpu_step(
                source=source,
                **({"context_id": step.key.context_id} if source == "tool_wait" else {}),
            ) != step
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
        if self._opportunity_writer is not None:
            tokens = self._context_tokens.get(step.key.context_id)
            self._opportunity_writer.record({
                "event": "prefetch_native_issued",
                "ts_ms": time.time() * 1000,
                "command_id": command_id, "source": source,
                "context_id": step.key.context_id,
                "context_epoch": step.key.context_epoch,
                "node_id": step.node_id, "leaf_node_id": step.leaf_node_id,
                "reusable_input_tokens": (
                    tokens[1] if tokens is not None
                    and tokens[0] == step.key.context_epoch else None
                ),
            })
        return command_id

    def defer_prefill_for_prefetch(self, req: object) -> bool:
        """Hold at most one submitted request in waiting until bounded native H2D ACK.

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
        if lease is not None and (
            self.visible.get(key.request_id) != key
            or self._terminal(key)
            or (
                (current := self.graph.invocations.get(key.invocation_id)) is None
                or current.state.value not in ("ready", "running_llm")
            )
        ):
            self._admission_lease = None
            return False
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
            or invocation.state.value not in ("ready", "running_llm")
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

    def on_native_transfer_commit(
        self, commit: object
    ) -> tuple[PhysicalActionCompleted, ...]:
        """Observe synchronized native ACKs, never infer completion from enqueue."""
        if self.physical_disabled:
            return ()
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
        # A client LLM_SUBMIT can advance the causal epoch before its HTTP request
        # registers a new visible key. Native session generation is the authority
        # for accepting the outstanding old-prefix H2D ACK across that one step.
        cache = self._native_cache
        for context_id, epoch, session_id, generation in self.physical_ledger.pending_h2d_sessions:
            if context_id in live_sessions or live_epochs.get(context_id) != epoch + 1:
                continue
            context = self.graph.contexts.get(context_id)
            workflow = self.graph.workflows.get(context.workflow_id) if context else None
            if workflow is None or workflow.end_ts_ms is not None or cache is None:
                continue
            try:
                anchors = cache.session_refs.snapshot_session_leaf_anchors(
                    session_id, generation, max_leaves=8,
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if anchors is not None:
                live_sessions[context_id] = (session_id, generation)
        try:
            completed = self.physical_ledger.observe(
                commit,
                live_context_epochs=live_epochs,
                live_context_sessions=live_sessions,
            )
        except PhysicalReceiptError as error:
            self.physical_disabled = True
            self.counts["physical_receipt_failed"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "physical_receipt_failure",
                    "ts_ms": time.time() * 1000, "error": str(error),
                    "direction": getattr(commit, "direction", None),
                    "live_context_epochs": live_epochs,
                    "live_context_sessions": live_sessions,
                })
            return ()
        self.completed_physical_actions.extend(completed)
        actual_bytes = getattr(commit, "actual_bytes", None)
        ack_ms = getattr(commit, "submit_to_ack_ms", None)
        if (
            getattr(commit, "status", None) == "completed"
            and getattr(commit, "direction", None) == "h2d"
            and type(actual_bytes) is int and actual_bytes > 0
            and type(ack_ms) in (int, float)
            and math.isfinite(ack_ms) and 0 < ack_ms <= 60_000
        ):
            self._h2d_samples.append((actual_bytes, float(ack_ms)))
            self.counts["h2d_service_sample"] += 1
        self.counts["native_physical_completed"] += len(completed)
        return completed

    def register_visible_request(self, req: object) -> bool:
        key = _request_key(req)
        if key is None or self._terminal(key):
            self.counts["invalid_request_identity"] += 1
            return False
        if key.request_id in self.visible:
            raise ValueError(f"duplicate visible request: {key.request_id}")
        old_hint = self.tool_wait_hints.get(key.context_id)
        if old_hint is not None and old_hint.key != key:
            self.tool_wait_hints.pop(key.context_id)
            self.shadow_candidate = None
            self._context_tokens.pop(key.context_id, None)
        self.join_wait_hints = {
            join_id: hint for join_id, hint in self.join_wait_hints.items()
            if hint.key.context_id != key.context_id or hint.key == key
        }
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
            old_hint = self.tool_wait_hints.get(key.context_id)
            if old_hint is not None and old_hint.key != key:
                self.tool_wait_hints.pop(key.context_id)
                self.shadow_candidate = None
                self._context_tokens.pop(key.context_id, None)
            self.join_wait_hints = {
                join_id: hint for join_id, hint in self.join_wait_hints.items()
                if hint.key.context_id != key.context_id or hint.key == key
            }
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
                self.tool_wait_hints.pop(context_id, None)
                self.join_wait_hints = {
                    join_id: hint for join_id, hint in self.join_wait_hints.items()
                    if hint.key.context_id != context_id
                }
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
        # LLM_SUBMIT marks an invocation RUNNING_LLM before the native waiting
        # request has received its first GPU service. Only inspect bounded
        # candidates supplied by SGLang's waiting queue here.
        for _, req in tagged[:8]:
            key = _request_key(req)
            if (
                key is None
                or key.invocation_id in ready_ranks
                or self.visible.get(key.request_id) != key
                or self.context_sessions.get(key.context_id) != key
                or self._terminal(key)
            ):
                continue
            invocation = self.graph.invocations.get(key.invocation_id)
            context = self.graph.contexts.get(key.context_id)
            if (
                invocation is None
                or invocation.state is not InvocationState.RUNNING_LLM
                or invocation.workflow_id != key.root_workflow_id
                or invocation.context_id != key.context_id
                or context is None
                or context.workflow_id != key.root_workflow_id
                or context.epoch != key.context_epoch
            ):
                continue
            candidate = self.frontier.describe_invocation(key.invocation_id)
            ready_ranks[key.invocation_id] = (
                candidate.score[0], -candidate.unblock_depth,
            )
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
        self._final_priority_promoted = None
        self._final_priority_native_rank = None
        if (
            (
                self.enable_final_stage_priority
                if self.enable_final_stage_priority is not None
                else self.enable_admission_prefetch or self.enable_final_stage_prefetch
            )
            and self._final_priority_normal_admissions >= 4 and ordered
        ):
            for stage in self._final_stages.values():
                if (
                    stage.semantic_only or not self._live_final_stage(stage)
                    or stage.request_id is None
                ):
                    continue
                candidate = next((
                    pair for pair in ordered[:32]
                    if getattr(pair[1], "rid", None) == stage.request_id
                    and self.visible.get(stage.request_id) == _request_key(pair[1])
                ), None)
                if candidate is None or ordered[0] == candidate:
                    continue
                native_rank = ordered.index(candidate)
                ordered.remove(candidate)
                ordered.insert(0, candidate)
                self._final_priority_promoted = stage.request_id
                self._final_priority_native_rank = native_rank
                self.counts["final_priority_ordered"] += 1
                break
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
                if req.rid == self._final_priority_promoted:
                    self._final_priority_normal_admissions = 0
                    self.counts["final_priority_admitted"] += 1
                    stage = self._final_request_stages.get(req.rid)
                    if stage is not None and self._opportunity_writer is not None:
                        self._opportunity_writer.record({
                            "event": "final_request_priority_admitted",
                            "ts_ms": time.time() * 1000,
                            "workflow_id": stage.key.root_workflow_id,
                            "join_id": stage.join_id,
                            "request_id": req.rid,
                            "tagged_displaced": self._final_priority_native_rank,
                        })
                    self._final_priority_promoted = None
                else:
                    self._final_priority_normal_admissions = min(
                        4, self._final_priority_normal_admissions + 1
                    )

    def on_batch_selected(self, batch: object) -> None:
        pass

    def on_batch_completed(self, batch: object) -> None:
        for req in batch.reqs:
            if self._semantic_worker is not None:
                key = self.visible.get(getattr(req, "rid", None))
                if key is not None:
                    self._semantic_keys[key.request_id] = key
                    history = self._semantic_progress.setdefault(
                        key.request_id, deque(maxlen=128),
                    )
                    tokens = len(getattr(req, "output_ids", ()) or ())
                    if not history or tokens > history[-1][1]:
                        history.append((time.monotonic() * 1000, tokens))
                    if req.finished():
                        self._semantic_finished[key.request_id] = (
                            time.monotonic() * 1000, tokens,
                        )
            stage = self._final_request_stages.get(getattr(req, "rid", None))
            if stage is not None:
                key = _request_key(req)
                if (
                    key is not None and key == self.visible.get(key.request_id)
                    and key.invocation_id == stage.child_id
                    and key.context_epoch == stage.child_epoch
                ):
                    now = time.monotonic()
                    tokens = len(getattr(req, "output_ids", ()) or ())
                    if (
                        stage.last_service_at is not None
                        and tokens > stage.generated_tokens
                        and 0 < now - stage.last_service_at <= 0.5
                    ):
                        rate = (tokens - stage.generated_tokens) / (
                            now - stage.last_service_at
                        )
                        stage.tokens_per_second = min(500.0, max(1.0, rate))
                    if tokens > stage.generated_tokens:
                        stage.generated_tokens = tokens
                        stage.last_service_at = now
            if (
                req.rid in self.visible
                and (key := self.visible[req.rid]).session_id is not None
                and key.session_generation is not None
            ):
                self._context_tokens[key.context_id] = (
                    key.context_epoch,
                    len(getattr(req, "origin_input_ids", ()) or ()),
                    len(getattr(req, "output_ids", ()) or ()),
                    req.beliefkv_metadata.get("parent_invocation_id") is not None,
                )
            if req.rid in self.visible and req.finished():
                context_id = self.visible[req.rid].context_id
                key = self.visible[req.rid]
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
                self.tool_wait_hints.pop(context_id, None)
                self.join_wait_hints = {
                    join_id: hint for join_id, hint in self.join_wait_hints.items()
                    if hint.key.context_id != context_id
                }
                self.shadow_candidate = None

    def running_batch_retraction_barrier_required(self, batch: object) -> bool:
        # SGLang uses this drain to reach the same safe point needed for a
        # native JOIN H2D. Request it once per node budget, never per decode tick.
        if self._semantic_worker is not None:
            # Build the read-only intent before deciding whether overlap must drain.
            # Issuance still waits for SGLang's drained safe point and revalidation.
            self._roll_final_stage()
        ticket = self._join_ticket
        if (
            ticket is None
            or not (
                ticket.phase == "confirmed"
                or self._semantic_worker is not None
                and ticket.stage_bound and ticket.phase == "provisional"
            )
            or not self._live_join_ticket()
            or ticket.command_id is not None
            or self.physical_ledger.pending_count
            or ticket.issued_nodes >= (1 if self.enable_confirmed_join_canary else 2)
            or ticket.drained_for_issued_nodes == ticket.issued_nodes
        ):
            return False
        ticket.drained_for_issued_nodes = ticket.issued_nodes
        self.counts["join_overlap_drain_requested"] += 1
        if self.enable_confirmed_join_canary and self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "confirmed_join_overlap_drain_requested",
                "ts_ms": time.time() * 1000.0,
                "join_id": ticket.join_id,
                "workflow_id": ticket.key.root_workflow_id,
                "context_id": ticket.key.context_id,
                "context_epoch": ticket.key.context_epoch,
                "issued_nodes": ticket.issued_nodes,
            })
        return True

    def on_running_batch_retraction_barrier_drained(self, batch: object) -> None:
        self.counts["join_overlap_drain_completed"] += 1
        ticket = self._join_ticket
        if (
            ticket is not None
            and self.enable_confirmed_join_canary
            and self._opportunity_writer is not None
        ):
            self._opportunity_writer.record({
                "event": "confirmed_join_overlap_drain_completed",
                "ts_ms": time.time() * 1000.0,
                "join_id": ticket.join_id,
                "workflow_id": ticket.key.root_workflow_id,
                "context_id": ticket.key.context_id,
                "context_epoch": ticket.key.context_epoch,
                "issued_nodes": ticket.issued_nodes,
                "ticket_live": self._live_join_ticket(),
            })

    def plan_running_batch_retraction(self, batch: object) -> None:
        return None
