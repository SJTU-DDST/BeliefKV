"""Bounded semantic admission and action-local native H2D for FULL/MAMBA.

The SGLang cache remains the physical capacity and transfer authority.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
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
    validate_tool_timing_artifact,
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
    observe_unified_node_closure,
)
from beliefkv.predictor.structured_frontier import LocalFrontierFeatures
from beliefkv.runtime.semantic_report_worker import (
    SEMANTIC_TEXT, SemanticReportInput, SemanticReportReply, SemanticReportWorker,
)
from beliefkv.runtime.clock_evidence import (
    SEMANTIC_FRAME_INTERVAL_MS,
    local_monotonic_clock_domain,
)
from beliefkv.runtime.native_h2d_seed import load_h2d_seed
from beliefkv.runtime.native_transfer_service import (
    NativeServiceSample,
    estimate_native_service,
    load_native_service_seed,
    pool_shape,
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


@dataclass(frozen=True)
class _JoinPrefetchIdentity:
    join_id: str
    workflow_id: str
    invocation_id: str
    context_id: str
    context_epoch: int
    session_id: str | None
    session_generation: int | None

    @classmethod
    def from_parent(
        cls, join_id: str, key: PrefillCandidateKey,
    ) -> _JoinPrefetchIdentity:
        return cls(
            join_id, key.root_workflow_id, key.invocation_id,
            key.context_id, key.context_epoch, key.session_id,
            key.session_generation,
        )


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


@dataclass
class _ToolPrefetchTicket:
    key: PrefillCandidateKey
    revision: float
    command_id: str | None = None
    drained: bool = False


@dataclass(frozen=True)
class _PrefetchServiceLease:
    key: PrefillCandidateKey
    command_id: str
    node_id: int
    creation_time: int | float
    source: str
    wait_revision: float | None
    acknowledged_at: float
    expires_at: float
    pool_bytes: tuple[tuple[str, int], ...]
    lock_params: object | None = None
    protected_bytes: int = 0
    demand_ready: bool = False
    reentry_ready_at: float | None = None


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
        tool_timing_artifact_path: str | None = None,
        tool_timing_sha256: str | None = None,
        enable_tool_prefetch: bool = False,
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
        self._h2d_samples: deque[tuple[int, float]] = deque(maxlen=64)
        self._native_service_samples: deque[NativeServiceSample] = deque(maxlen=256)
        service_path = os.environ.get("BELIEFKV_TRANSFER_SERVICE_SEED")
        service_sha = os.environ.get("BELIEFKV_TRANSFER_SERVICE_SEED_SHA256")
        if bool(service_path) != bool(service_sha):
            raise ValueError("transfer service seed requires a path and SHA256")
        if service_path:
            self._native_service_samples.extend(load_native_service_seed(service_path, service_sha))
        self._prefetch_issue_times: dict[str, float] = {}
        self._prefetch_steps: dict[
            str, tuple[PrefetchLoadStep, str, float | None]
        ] = {}
        self._prefetch_service_leases: dict[str, _PrefetchServiceLease] = {}
        self._prefetch_lock_release_failures: set[str] = set()
        self._prefetch_priority_normal_admissions = 4
        self._prefetch_priority_promoted: str | None = None
        self._prefetch_priority_native_rank: int | None = None
        self._visible_since: dict[str, float] = {}
        lead_setting = float(os.environ.get("BELIEFKV_PREFETCH_LEAD_MS", "1000"))
        if not math.isfinite(lead_setting) or not 100 <= lead_setting <= 1000:
            raise ValueError("prefetch lead must be between 100 and 1000 ms")
        self.prefetch_lead_ms = lead_setting
        self.semantic_work_statistic = os.environ.get(
            "BELIEFKV_SEMANTIC_WORK_STATISTIC", "upper",
        )
        if self.semantic_work_statistic not in ("upper", "center"):
            raise ValueError("semantic work statistic must be upper or center")
        self.eos_protocol_window_ms = float(os.environ.get(
            "BELIEFKV_EOS_PROTOCOL_WINDOW_MS", "50",
        ))
        if not math.isfinite(self.eos_protocol_window_ms) or not 50 <= self.eos_protocol_window_ms <= 500:
            raise ValueError("EOS protocol window must be between 50 and 500 ms")
        seed_path = os.environ.get("BELIEFKV_H2D_SEED")
        seed_sha = os.environ.get("BELIEFKV_H2D_SEED_SHA256")
        if bool(seed_path) != bool(seed_sha):
            raise ValueError("H2D cold-start seed requires path and SHA256")
        seed = load_h2d_seed(seed_path, seed_sha) if seed_path else ()
        self._h2d_samples.extend(seed)
        self._h2d_seed_count = len(seed)
        self._join_prepare_cursor = 0
        self._join_prepare_next_ms = 0.
        self._join_prepare_commands: dict[_JoinPrefetchIdentity, str] = {}
        self._parent_pressure_candidates: dict[int, tuple[PrefillCandidateKey, int | float]] = {}
        self._final_priority_normal_admissions = 4
        self._final_priority_promoted: str | None = None
        self._final_priority_native_rank: int | None = None
        self._join_ticket: _JoinPrefetchTicket | None = None
        self._join_prefetch_issued: Counter[_JoinPrefetchIdentity] = Counter()
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
        self._semantic_body_seen: set[str] = set()
        self._semantic_normal_stops: set[str] = set()
        self._semantic_eos_proofs: dict[str, bool] = {}
        self._semantic_submit_ms: dict[str, float] = {}
        self._semantic_submitted_frames: dict[str, tuple[float, int]] = {}
        self._semantic_target_cache: dict[PrefillCandidateKey, tuple[float, bool]] = {}
        self._semantic_tool_counts: Counter[str] = Counter()
        self._decoded_tool_requests: set[str] = set()
        self._decoded_scan_positions: dict[str, int] = {}
        self._tool_open_token_ids: frozenset[int] = frozenset()
        self._reasoning_close_token_ids: frozenset[int] = frozenset()
        if model_path:
            config = Path(model_path) / "tokenizer_config.json"
            if config.is_file():
                tokens = json.loads(config.read_text()).get("added_tokens_decoder", {})
                self._tool_open_token_ids = frozenset(
                    int(token_id) for token_id, token in tokens.items()
                    if isinstance(token, dict) and token.get("content") in (
                        "<tool_call>", "<|tool_call|>",
                    )
                )
                self._reasoning_close_token_ids = frozenset(
                    int(token_id) for token_id, token in tokens.items()
                    if isinstance(token, dict) and token.get("content") == "</think>"
                )
        self._runtime_state_next_ms = 0.
        self._terminal_cache_diagnostics = os.environ.get(
            "BELIEFKV_TERMINAL_CACHE_DIAGNOSTICS", "0",
        ) == "1"
        self._terminal_cache_watches: OrderedDict[tuple[str, str], dict] = OrderedDict()
        self._terminal_cache_next_ms = 0.
        self.enable_confirmed_join_canary = enable_confirmed_join_canary
        self._admission_lease: _AdmissionPrefetchLease | None = None
        self.shadow_candidate: ActionLocalShadowCandidate | None = None
        self._native_cache: object | None = None
        self._context_tokens: dict[str, tuple[int, int, int, bool]] = {}
        self._boundary_history: dict[str, deque[str]] = {}
        self._tool_metadata: dict[
            str, tuple[str, str, str, float | None, str, float | None, int]
        ] = {}
        self._tool_metadata_by_run: dict[str, dict[str, tuple]] = {}
        self._tool_ticket: _ToolPrefetchTicket | None = None
        self._tool_prefetch_budget: Counter[PrefillCandidateKey] = Counter()
        self._tool_timing_only = False
        self._tool_refresh_next_ms = 0.
        self._tool_prepare_next_ms = 0.
        self._tool_last_queries: dict[PrefillCandidateKey, tuple[float, float]] = {}
        self._tool_opportunity_cache: dict[PrefillCandidateKey, tuple[float, object]] = {}
        tool_setting = os.environ.get("BELIEFKV_ENABLE_TOOL_PREFETCH", "0")
        if tool_setting not in ("0", "1"):
            raise ValueError("tool prefetch setting must be 0 or 1")
        self.enable_tool_prefetch = enable_tool_prefetch or tool_setting == "1"
        tool_timing_artifact_path = (
            tool_timing_artifact_path or os.environ.get("BELIEFKV_TOOL_TIMING_ARTIFACT")
        )
        tool_timing_sha256 = (
            tool_timing_sha256 or os.environ.get("BELIEFKV_TOOL_TIMING_SHA256")
        )
        if bool(tool_timing_artifact_path) != bool(tool_timing_sha256):
            raise ValueError("tool event predictor requires path and SHA256")
        if tool_timing_artifact_path:
            if enable_local_predictor or predictor_sha256 is not None or not model_path:
                raise ValueError("tool-only timing and legacy admission predictors are exclusive")
            validate_tool_timing_artifact(
                tool_timing_artifact_path, expected_sha256=tool_timing_sha256,
                model_path=model_path,
            )
            from beliefkv.runtime.sglang_v0520_predictor_worker import NativePredictorWorker

            self._tool_timing_only = True
            self.predictor_sha256 = tool_timing_sha256
            self._tool_model_arguments = (tool_timing_artifact_path, tool_timing_sha256)
        self._next_wait_refresh_ms = 0.0
        self._refresh_join_next = False
        self._scan_unhinted_next = False
        self._scan_join_next = False
        self._scan_offsets = {"tool_wait": 0, "join_wait": 0}
        self._last_model_signature: tuple[object, ...] | None = None
        self._model_worker = None
        if self._tool_timing_only:
            self._model_worker = NativePredictorWorker(*self._tool_model_arguments)
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
                (
                    remaining if (remaining := hint.remaining_quantile(.1, now_ms=now_ms))
                    is not None else math.inf
                ),
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
        for command in tuple(self._prefetch_service_leases):
            self._release_prefetch_service_lease(command, "shutdown")
        self._prefetch_steps.clear()
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
        if callable(getattr(cache, "_evict_backed_join_parent", None)):
            cache.beliefkv_join_pressure_validator = self._live_parent_pressure_node
            cache.beliefkv_join_pressure_parked = self._on_parent_pressure_parked

    def predictor_fileno(self) -> int | None:
        if self._semantic_worker is not None:
            return self._semantic_worker.fileno()
        if self._model_worker is None or self._model_worker.disabled:
            return None
        return self._model_worker.fileno()

    def predictor_filenos(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys(
            fd for worker in (self._semantic_worker, self._model_worker)
            if worker is not None and not worker.disabled
            and (fd := worker.fileno()) is not None
        ))

    def idle_poll_timeout_ms(self) -> int:
        if self.physical_ledger.pending_count:
            return 10
        if self._prefetch_service_leases:
            return 100
        if self._tool_timing_only and any(
            item.state is InvocationState.WAIT_TOOL for item in self.graph.invocations.values()
        ):
            return 100
        return 1000

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
            for command in tuple(self._prefetch_service_leases):
                self._release_prefetch_service_lease(command, "causal_mirror_discarded")
            self._prefetch_steps.clear()
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
                        identity = str(
                            attrs.get("tool_run_id") or attrs.get("tool_call_id") or "_legacy"
                        )
                        self._tool_metadata_by_run.setdefault(event.invocation_id, {})[
                            identity
                        ] = self._tool_metadata[event.invocation_id]
                    elif event.kind in (
                        RuntimeEventKind.TOOL_END,
                        RuntimeEventKind.RETURN,
                        RuntimeEventKind.INVOCATION_CANCEL,
                    ):
                        if event.kind is RuntimeEventKind.TOOL_END:
                            attrs = event.attributes
                            identity = str(
                                attrs.get("tool_run_id") or attrs.get("tool_call_id") or "_legacy"
                            )
                            pending = self._tool_metadata_by_run.get(event.invocation_id, {})
                            pending.pop(identity, None)
                            if pending:
                                self._tool_metadata[event.invocation_id] = next(iter(pending.values()))
                            else:
                                self._tool_metadata.pop(event.invocation_id, None)
                        else:
                            self._tool_metadata.pop(event.invocation_id, None)
                            self._tool_metadata_by_run.pop(event.invocation_id, None)
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
                        self._track_terminal_cache(key)
                        self._tool_last_queries.pop(key, None)
                        self._tool_opportunity_cache.pop(key, None)
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
                    for key in tuple(self._tool_last_queries):
                        if key.root_workflow_id == event.workflow_id:
                            self._tool_last_queries.pop(key, None)
                    for key in tuple(self._tool_opportunity_cache):
                        if key.root_workflow_id == event.workflow_id:
                            self._tool_opportunity_cache.pop(key, None)
                    self._join_prefetch_issued = Counter({
                        identity: count
                        for identity, count in self._join_prefetch_issued.items()
                        if identity.workflow_id != event.workflow_id
                    })
                    self._tool_prefetch_budget = Counter({
                        key: count for key, count in self._tool_prefetch_budget.items()
                        if key.root_workflow_id != event.workflow_id
                    })
                    if self._tool_ticket is not None and self._tool_ticket.key.root_workflow_id == event.workflow_id:
                        self._tool_ticket = None
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
                if event.kind in (RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END) and context_id is not None:
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
                    if event.kind is RuntimeEventKind.JOIN_TIMEOUT:
                        self._join_prefetch_issued = Counter({
                            identity: count
                            for identity, count in self._join_prefetch_issued.items()
                            if identity.join_id != event.join_id
                        })
                    for join_id, stage in tuple(self._final_stages.items()):
                        if (
                            stage.child_id == event.invocation_id
                            or stage.key.invocation_id == event.invocation_id
                            or stage.join_id == event.join_id
                        ):
                            self._clear_final_stage(join_id)
            self._refresh_prefetch_service_leases()
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
                self._semantic_body_seen.discard(rid)
                self._semantic_normal_stops.discard(rid)
                self._semantic_eos_proofs.pop(rid, None)
                self._semantic_submit_ms.pop(rid, None)
                self._semantic_submitted_frames.pop(rid, None)
                self._decoded_tool_requests.discard(rid)
                self._decoded_scan_positions.pop(rid, None)
                cancel = getattr(self._semantic_worker, "cancel", None)
                if callable(cancel):
                    cancel(rid)

    def _semantic_key_live(self, key: PrefillCandidateKey, now_ms: float) -> bool:
        child = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        ended = self._semantic_finished.get(key.request_id)
        return bool(
            child is not None and not child.state.terminal
            and key.request_id not in self._decoded_tool_requests
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
        if type(text) is str and type(chars) is int and chars > 0 and text.strip():
            self._semantic_body_seen.add(rid)
            if rid in self._semantic_normal_stops:
                self._semantic_eos_proofs[rid] = True
                self._ensure_observed_final_stage(key)
        if type(text) is not str or type(chars) is not int or chars < 32:
            return
        if len(self._semantic_frames) >= 128 and rid not in self._semantic_frames:
            self.counts["semantic_text_capacity"] += 1
            return
        self._semantic_keys[rid] = key
        self._semantic_frames[rid] = event

    def _ensure_observed_final_stage(self, key: PrefillCandidateKey) -> None:
        ended = self._semantic_finished.get(key.request_id)
        now_ms = time.monotonic() * 1000
        if not (
            self._semantic_eos_proofs.get(key.request_id) is True
            and ended is not None and 0 <= now_ms - ended[0] <= self.eos_protocol_window_ms
            and self._semantic_key_live(key, now_ms)
        ):
            return
        parent = self._semantic_parent(key.invocation_id)
        if parent is None:
            return
        join_id, parent_key = parent
        previous = self._final_stages.get(join_id)
        if (
            previous is not None and previous.request_id == key.request_id
            and self._live_final_stage(previous)
        ):
            return
        if previous is not None and previous.request_id is not None:
            self._final_request_stages.pop(previous.request_id, None)
        stage = _ChildFinalStage(
            parent_key, join_id, key.invocation_id, key.context_epoch, ended[1],
            time.monotonic() + self.eos_protocol_window_ms / 1000.,
            request_id=key.request_id, generated_tokens=ended[1],
            semantic_only=True,
            issued_nodes=self._issued_join_nodes(join_id, parent_key),
        )
        self._final_stages[join_id] = stage
        self._final_request_stages[key.request_id] = stage
        self.counts["observed_eos_final_stage_created"] += 1

    def _record_native_final_body(self, req: object, key: PrefillCandidateKey) -> None:
        reason = getattr(req, "finished_reason", None)
        to_json = getattr(reason, "to_json", None)
        info = to_json() if callable(to_json) else {}
        normal = (
            info.get("type") == "stop" and info.get("matched") != "NaN happened"
            and key.request_id not in self._decoded_tool_requests
            and not (getattr(req, "beliefkv_metadata", None) or {}).get("runtime_internal")
        )
        if normal:
            self._semantic_normal_stops.add(key.request_id)
        body = key.request_id in self._semantic_body_seen
        outputs = getattr(req, "output_ids", ()) or ()
        tokenizer = getattr(req, "tokenizer", None)
        if normal and not body and tokenizer is not None:
            close = next((
                index for index in range(len(outputs) - 1, -1, -1)
                if outputs[index] in self._reasoning_close_token_ids
            ), None)
            if close is not None:
                # Inspect only the completed visible suffix, never the reasoning
                # prefix or a future decode. No text/token payload is logged.
                suffix = outputs[max(close + 1, len(outputs) - 64):]
                try:
                    body = bool(tokenizer.decode(suffix, skip_special_tokens=True).strip())
                except (TypeError, ValueError, KeyError, IndexError):
                    self.counts["native_final_body_decode_unavailable"] += 1
        self._semantic_eos_proofs[key.request_id] = bool(normal and body)
        if normal and body:
            self._semantic_body_seen.add(key.request_id)
            self._ensure_observed_final_stage(key)

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
        rates = [(end_tokens - prior[1]) * 1000 / (end_ms - prior[0])]
        # A short decode burst is not the future wall-clock service share.
        for window in (2_000., 5_000.):
            start = next(((ts, tokens) for ts, tokens in progress if ts >= end_ms - window), progress[0])
            if start[0] < end_ms and start[1] < end_tokens:
                rates.append((end_tokens - start[1]) * 1000 / (end_ms - start[0]))
        return min(500., max(1., min(rates)))

    def _semantic_transfer_target_ready(self, child_id: str, now_ms: float) -> bool:
        if not self.enable_final_stage_prefetch or self._native_cache is None:
            return True
        parent = self._semantic_parent(child_id)
        if parent is None:
            return False
        _, key = parent
        cached = self._semantic_target_cache.get(key)
        if cached is not None and now_ms < cached[0]:
            return cached[1]
        observation = self.inspect_context_h2d_opportunity(
            context_id=key.context_id, context_epoch=key.context_epoch,
        )
        ready = observation is not None and observation.step is not None
        self._semantic_target_cache[key] = (now_ms + 100., ready)
        if len(self._semantic_target_cache) > 256:
            self._semantic_target_cache = {
                k: value for k, value in self._semantic_target_cache.items()
                if value[0] > now_ms
            }
        return ready

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
                    "causal_progress_guard_ms": item.causal_progress_guard_ms,
                    "notice_active": item.notice_active,
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
                    issued_nodes=self._issued_join_nodes(join_id, parent_key),
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
            if now_ms - self._semantic_submit_ms.get(rid, 0.) < SEMANTIC_FRAME_INTERVAL_MS:
                continue
            if now_ms - event.ts_ms > 1_500:
                continue
            frame = (event.ts_ms, event.attributes["content_chars"])
            if self._semantic_submitted_frames.get(rid) == frame:
                self.counts["semantic_unchanged_frame_skipped"] += 1
                continue
            if not self._semantic_transfer_target_ready(key.invocation_id, now_ms):
                self.counts["semantic_no_transfer_target_skipped"] += 1
                continue
            progress = self._semantic_progress.get(rid, ())
            domain = local_monotonic_clock_domain()
            guard_ms = (
                0. if domain is not None and event.attributes.get("monotonic_clock_domain") == domain
                else 100.
            )
            # Only completed decode older than the delivered text is an input.
            observed = next((tokens for ts, tokens in reversed(progress)
                             if ts <= event.ts_ms - guard_ms), None)
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
                causal_progress_guard_ms=guard_ms,
            ))
            self._semantic_submit_ms[rid] = now_ms
            self._semantic_submitted_frames[rid] = frame
            self.counts["semantic_input_submitted"] += 1

    def scheduler_step(self, waiting_queue: Sequence[object] = ()) -> None:
        if self.event_server is not None:
            self.event_server.drain(max_messages=128)
        self._refresh_prefetch_service_leases()
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
                                    issued_nodes=self._issued_join_nodes(
                                        hint.join_id, hint.key,
                                    ),
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
        if self._tool_timing_only and now_ms >= self._tool_refresh_next_ms:
            self._tool_refresh_next_ms = now_ms + 100.
            contexts = [
                key.context_id for key in self.context_sessions.values()
                if (inv := self.graph.invocations.get(key.invocation_id)) is not None
                and inv.state is InvocationState.WAIT_TOOL
            ]
            if contexts:
                offset = self._scan_offsets["tool_wait"] % len(contexts)
                for context in (contexts[offset:] + contexts[:offset])[:8]:
                    self._submit_tool_wait(context)
                self._scan_offsets["tool_wait"] = (offset + 8) % len(contexts)
        expired = self.physical_ledger.expire()
        self.counts["physical_expired"] += len(expired)
        for command in expired:
            self._prefetch_issue_times.pop(command, None)
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
        self._sample_terminal_cache(now_ms=now_ms)
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
                "tool_timing_only": self._tool_timing_only,
                "tool_predictor_configured": self._model_worker is not None,
                "tool_predictor_disabled": bool(
                    self._model_worker is not None and self._model_worker.disabled
                ),
                "tool_prefetch": self.enable_tool_prefetch,
                "prefetch_lead_ms": self.prefetch_lead_ms,
                "semantic_work_statistic": self.semantic_work_statistic,
                "eos_protocol_window_ms": self.eos_protocol_window_ms,
                "terminal_cache_diagnostics": self._terminal_cache_diagnostics,
                "h2d_seed_samples": self._h2d_seed_count,
                "h2d_service_samples": len(self._h2d_samples),
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
                snapshot = getattr(
                    cache.session_refs, "snapshot_latest_session_leaf_anchors",
                    cache.session_refs.snapshot_session_leaf_anchors,
                )
                leaves = snapshot(
                    key.session_id, key.session_generation, max_leaves=8
                )
                if leaves is None:
                    reason = getattr(cache.session_refs, "session_anchor_snapshot_reason", None)
                    if callable(reason):
                        return reason(key.session_id, key.session_generation)
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
        mamba_needed = int(
            step.include_mamba and node.mamba_device_present and not node.mamba_host_present
        )
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
        if self._tool_timing_only:
            return
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
                issued_nodes=self._issued_join_nodes(join_id, key),
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
                issued_nodes=self._issued_join_nodes(join_id, key),
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

    def _issued_join_nodes(self, join_id: str, key: PrefillCandidateKey) -> int:
        # Keep the budget through stage expiry and confirmed reentry.
        return self._join_prefetch_issued[
            _JoinPrefetchIdentity.from_parent(join_id, key)
        ]

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
        service_count = max(
            len(self._h2d_samples),
            sum(sample.direction == "h2d" for sample in self._native_service_samples),
        )
        if service_count < 3 or self.physical_ledger.pending_count:
            if service_count < 3:
                self.counts["final_stage_no_h2d_service_evidence"] += 1
            return
        if self._join_ticket is not None and self._live_join_ticket():
            return
        for rid, valid in tuple(self._semantic_eos_proofs.items()):
            if valid and (key := self._semantic_keys.get(rid)) is not None:
                self._ensure_observed_final_stage(key)
        for stage in tuple(self._final_stages.values()):
            if self._semantic_worker is not None and stage.request_id is not None:
                progress = self._semantic_progress.get(stage.request_id, ())
                if progress:
                    stage.generated_tokens = progress[-1][1]
                    stage.tokens_per_second = self._semantic_rate(stage.request_id)
            if (
                not self._live_final_stage(stage) or stage.request_id is None
                or self._issued_join_nodes(stage.join_id, stage.key) >= 2
            ):
                continue
            observed_eos = self._semantic_eos_proofs.get(stage.request_id) is True
            if not observed_eos and (
                stage.generated_tokens < 16 or stage.tokens_per_second is None
            ):
                continue
            child_key = self.visible.get(stage.request_id) or self._semantic_keys.get(stage.request_id)
            if (
                child_key is None or child_key.invocation_id != stage.child_id
                or child_key.context_epoch != stage.child_epoch
            ):
                continue
            forecast = None
            trigger_kind = "estimated_work"
            if self._semantic_worker is not None and observed_eos:
                now_ms = time.monotonic() * 1000
                ended_ms = self._semantic_finished[stage.request_id][0]
                if (
                    not 0 <= now_ms - ended_ms <= self.eos_protocol_window_ms
                    or not self._semantic_key_live(child_key, now_ms)
                ):
                    self.counts["semantic_eos_protocol_window_expired"] += 1
                    continue
                remaining, remaining_ms = 0., 0.
                trigger_kind = "observed_no_tool_eos"
                self.counts["observed_eos_h2d_candidate"] += 1
            elif self._semantic_worker is not None:
                if self._semantic_eos_proofs.get(stage.request_id) is False:
                    self.counts["semantic_finish_without_valid_body_skipped"] += 1
                    continue
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
                if stage.request_id not in self._semantic_finished:
                    # Notice prose can still end in announce_completion_intent.
                    # Unannounced children retain the no-tool native EOS path.
                    if not forecast.observation.notice_active:
                        self.counts["semantic_pre_eos_phase_unconfirmed"] += 1
                        continue
                    work_tokens = (
                        forecast.upper_tokens if self.semantic_work_statistic == "upper"
                        else forecast.middle_tokens
                    )
                    remaining = max(
                        1., work_tokens
                        - max(0, generated - forecast.observation.observed_output_tokens),
                    )
                    self.counts[
                        "semantic_pre_eos_uses_work_upper_bound"
                        if self.semantic_work_statistic == "upper"
                        else "semantic_pre_eos_uses_work_center"
                    ] += 1
                # EOS is known GPU progress, not confirmation that the child RETURNed.
                if stage.request_id in self._semantic_finished:
                    ended_ms = self._semantic_finished[stage.request_id][0]
                    if now_ms - ended_ms > self.eos_protocol_window_ms:
                        self.counts["semantic_eos_protocol_window_expired"] += 1
                        continue
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
            shape = pool_shape(
                observation.required_full_tokens, observation.required_mamba_slots,
            )
            estimate = estimate_native_service(
                self._native_service_samples, required_bytes, shape=shape,
            ) or estimate_native_service(self._h2d_samples, required_bytes)
            if estimate is None:
                self.counts["prefetch_service_size_unsupported"] += 1
                continue
            h2d_ms = estimate.submit_to_ack_p90_ms
            if remaining_ms > self.prefetch_lead_ms:
                if self._semantic_worker is not None:
                    self.counts["semantic_h2d_not_latest_start"] += 1
                continue
            join = self.graph.joins[stage.join_id]
            self._join_ticket = _JoinPrefetchTicket(
                stage.key, stage.join_id, join.mode.value,
                tuple(sorted(join.member_invocation_ids)),
                "provisional", min(stage.expires_at, time.monotonic() + 2.0),
                issued_nodes=self._issued_join_nodes(stage.join_id, stage.key),
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
                    "trigger_kind": trigger_kind,
                    "work_statistic": self.semantic_work_statistic,
                    "forecast_center_tokens": (
                        forecast.middle_tokens if forecast is not None else None
                    ),
                    "forecast_upper_tokens": (
                        forecast.upper_tokens if forecast is not None else None
                    ),
                    "h2d_ms": h2d_ms,
                    "prefetch_lead_ms": self.prefetch_lead_ms,
                    "service_sample_count": estimate.sample_count,
                    "service_support": estimate.support,
                    "required_bytes": required_bytes,
                    "context_id": stage.key.context_id,
                    "context_epoch": stage.key.context_epoch,
                    "child_invocation_id": stage.child_id,
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
                    issued_nodes=self._issued_join_nodes(join_id, key),
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
        ticket.issued_nodes = max(
            ticket.issued_nodes, self._issued_join_nodes(ticket.join_id, ticket.key),
        )
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
            self._join_prefetch_issued[
                _JoinPrefetchIdentity.from_parent(ticket.join_id, ticket.key)
            ] = ticket.issued_nodes
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
        if len(invocation.active_tool_calls) > 1:
            self.counts["tool_wait_parallel_group_pending"] += 1
            return
        metadata = self._tool_metadata.get(invocation.invocation_id)
        if metadata and metadata[1] in ("announce_completion_intent", "ChildCompletion", "WorkflowCompletion"):
            return
        if self._tool_timing_only:
            elapsed = max(0., now_ms - (invocation.active_tool_start_ms or now_ms))
            if elapsed < 50.:
                self.counts["tool_fast_wait_prediction_skipped"] += 1
                return
            previous = self._tool_last_queries.get(key)
            hint = self.tool_wait_hints.get(context_id)
            remaining = 0.
            if hint is not None and hint.live(key, now_ms=now_ms):
                value = hint.remaining_quantile(.5, now_ms=now_ms)
                remaining = value if value is not None else math.inf
            spacing = 1000. if remaining > 10000. else 500. if remaining > 2000. else 150.
            if (
                previous is not None and previous[0] == invocation.updated_ts_ms
                and now_ms - previous[1] < spacing
            ):
                self.counts["tool_unchanged_wait_prediction_skipped"] += 1
                return
        features = self._local_frontier_features(
            invocation, context.epoch, now_ms=now_ms
        )
        worker.submit_tool_wait(((key, features, invocation.updated_ts_ms),))
        self._tool_last_queries[key] = (invocation.updated_ts_ms, now_ms)
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
        self._refresh_prefetch_service_leases(context_id=key.context_id)
        self.counts["tool_wait_accepted"] += 1
        if self._tool_timing_only:
            # Physical ancestry is read at an action safe point, not for every
            # advisory refresh of the same external wait.
            self.counts["tool_hint_physical_inspection_deferred"] += 1
        else:
            self.shadow_candidate = (
                self.capture_shadow_candidate(
                    self._native_cache,
                    context_id=key.context_id,
                    context_epoch=key.context_epoch,
                )
                if self._native_cache is not None else None
            )
            self.counts[
                "tool_wait_shadow_available"
                if self.shadow_candidate is not None else "tool_wait_shadow_unavailable"
            ] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "tool_wait_forecast", "ts_ms": time.time() * 1000,
                "observed_monotonic_ms": hint.issued_monotonic_ms,
                "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
                "context_id": key.context_id, "context_epoch": key.context_epoch,
                "p10_ms": hint.wait_p10_ms, "p50_ms": hint.wait_p50_ms,
                "p90_ms": hint.wait_p90_ms, "release_cdf": hint.release_cdf,
                "conditional_p50_ms": hint.remaining_quantile(
                    .5, now_ms=time.monotonic() * 1000,
                ),
                "timing_policy": "survival_conditioned_cdf_or_unexpired_quantile",
            })

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
        if self._tool_timing_only:
            return
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
            snapshot = getattr(
                cache.session_refs, "snapshot_latest_session_leaf_anchors",
                cache.session_refs.snapshot_session_leaf_anchors,
            )
            leaves = snapshot(
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
                max(0, tokens[1] - 1)
                if (tokens := self._context_tokens.get(context_id)) is not None
                and tokens[0] == key.context_epoch else None
            ),
        )

    def _track_terminal_cache(self, key: PrefillCandidateKey) -> None:
        if not (
            self._terminal_cache_diagnostics and self._native_cache is not None
            and self._opportunity_writer is not None and key.session_id is not None
            and key.session_generation is not None
        ):
            return
        try:
            refs = self._native_cache.session_refs
            snapshot = getattr(refs, "snapshot_latest_session_leaf_anchors", refs.snapshot_session_leaf_anchors)
            anchors = snapshot(key.session_id, key.session_generation, max_leaves=8)
            nodes = sorted({
                (node_id, normalize_native_creation_time(created))
                for _, values in anchors or () for node_id, created in values
            })
        except (AttributeError, KeyError, TypeError, ValueError):
            nodes = []
        scope = (key.root_workflow_id, key.context_id)
        if not nodes:
            self.counts["terminal_cache_anchor_unavailable"] += 1
            self._opportunity_writer.record({
                "event": "terminal_cache_anchor_unavailable", "ts_ms": time.time() * 1000.,
                "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
                "context_id": key.context_id, "session_id": key.session_id,
                "semantics": "missing anchor is unknown, not proof that cache bytes were freed",
            })
            return
        watch = {
            "key": key, "anchors": nodes[:16], "terminated_ms": time.monotonic() * 1000.,
            "sample_count": 0,
        }
        self._terminal_cache_watches[scope] = watch
        self._terminal_cache_watches.move_to_end(scope)
        while len(self._terminal_cache_watches) > 512:
            self._terminal_cache_watches.popitem(last=False)
            self.counts["terminal_cache_watch_capacity_expired"] += 1
        self._record_terminal_cache(watch)

    def _record_terminal_cache(self, watch: dict) -> None:
        key = watch["key"]
        summaries, unavailable, gone = {}, [], []
        for node_id, created in watch["anchors"]:
            try:
                node = self._native_cache.tree_core.node_by_id(node_id)
                if node is None or normalize_native_creation_time(node.creation_time) != created:
                    gone.append(node_id)
                    continue
                observation = observe_unified_node_closure(self._native_cache, node_id, max_nodes=64)
                if not observation.observable:
                    unavailable.append({"node_id": node_id, "reason": observation.reason})
                    continue
                for summary in observation.nodes:
                    summaries[(summary.node_id, summary.creation_time)] = asdict(summary)
            except (AttributeError, KeyError, TypeError, ValueError):
                unavailable.append({"node_id": node_id, "reason": "native node unavailable"})
        self._opportunity_writer.record({
            "event": "terminal_context_cache_sample", "ts_ms": time.time() * 1000.,
            "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
            "context_id": key.context_id, "context_epoch": key.context_epoch,
            "session_id": key.session_id, "sample_index": watch["sample_count"],
            "elapsed_since_terminal_ms": time.monotonic() * 1000. - watch["terminated_ms"],
            "anchor_node_ids": [node for node, _ in watch["anchors"]],
            "nodes": list(summaries.values()), "gone_or_replaced_anchors": gone,
            "unavailable_anchors": unavailable,
            "semantics": (
                "Read-only terminated-context leaf ancestry; ancestors may be shared "
                "and are not exclusive dead bytes. Session close is not physical "
                "reclamation; no cache drop/offload is caused by this observation."
            ),
        })
        watch["sample_count"] += 1

    def _sample_terminal_cache(self, *, now_ms: float) -> None:
        if not self._terminal_cache_diagnostics or now_ms < self._terminal_cache_next_ms:
            return
        self._terminal_cache_next_ms = now_ms + 1000.
        for scope, watch in list(self._terminal_cache_watches.items())[:4]:
            age = now_ms - watch["terminated_ms"]
            if age >= 60_000.:
                self._terminal_cache_watches.pop(scope, None)
                self.counts["terminal_cache_watch_time_expired"] += 1
                continue
            self._record_terminal_cache(watch)
            self._terminal_cache_watches.move_to_end(scope)

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
        self, *, context_id: str | None = None, source: str = "tool_wait"
    ) -> ShadowBackupStep | None:
        """Recheck a tool wait and its native closure at the action safe point."""
        if not self.enable_prepare_host:
            return None
        if source == "join_prepare":
            key = self.context_sessions.get(context_id)
            parent = self.graph.invocations.get(key.invocation_id) if key else None
            if (
                key is None or parent is None or self._terminal(key)
                or parent.state is not InvocationState.WAIT_JOIN
                or self._join_parent_key(parent.join_id) != key
                or parent.join_id in self._noncontinuing_joins
                or parent.join_id in self._final_stages
            ):
                return None
            candidate = self.capture_shadow_candidate(
                self._native_cache, context_id=key.context_id,
                context_epoch=key.context_epoch,
            )
            return next_shadow_backup_step(candidate) if candidate is not None else None
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

    def issue_shadow_backup_step(
        self, step: ShadowBackupStep, *, source: str = "tool_wait",
    ) -> str | None:
        """Submit a revalidated native shadow; return its ID, not ACK credit.

        The scheduler's action policy must first authorize the step. This
        method supplies transaction safety only and is not an action planner.
        """
        cache = self._native_cache
        if (
            self.physical_disabled
            or cache is None
            or not isinstance(step, ShadowBackupStep)
            or self.refreshed_shadow_backup_step(
                context_id=step.key.context_id, source=source,
            ) != step
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
            beliefkv_include_mamba=step.include_mamba,
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
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "prepare_native_issued", "ts_ms": time.time() * 1000,
                "command_id": command_id, "source": source,
                "context_id": step.key.context_id,
                "context_epoch": step.key.context_epoch,
                "include_mamba": step.include_mamba,
                "node_id": step.node_id, "leaf_node_id": step.leaf_node_id,
            })
        return command_id

    def _live_parent_pressure_node(self, node_id: int, creation_time: int | float) -> bool:
        self._refresh_prefetch_service_leases()
        if any(
            lease.node_id == node_id and lease.creation_time == creation_time
            for lease in self._prefetch_service_leases.values()
        ):
            self.counts["prefetch_residency_pressure_protected"] += 1
            return False
        record = self._parent_pressure_candidates.get(node_id)
        if record is None or record[1] != creation_time:
            return False
        key = record[0]
        parent = self.graph.invocations.get(key.invocation_id)
        return bool(
            self.enable_prepare_host and not self.physical_disabled
            and self.context_sessions.get(key.context_id) == key
            and parent is not None and (
                parent.state is InvocationState.WAIT_JOIN
                and self._join_parent_key(parent.join_id) == key
                and parent.join_id not in self._final_stages
                or parent.state is InvocationState.WAIT_TOOL and self._long_tool_wait(key)
            )
            and not self._terminal(key)
        )

    def _on_parent_pressure_parked(self, node_id: int, freed: dict[int, int]) -> None:
        for command, lease in tuple(self._prefetch_service_leases.items()):
            if lease.node_id == node_id:
                self._release_prefetch_service_lease(command, "pressure_parked")
        record = self._parent_pressure_candidates.get(node_id)
        if record is None:
            return
        key = record[0]
        self.counts["parent_pressure_demoted"] += 1
        invocation = self.graph.invocations.get(key.invocation_id)
        source = "tool_wait" if invocation and invocation.state is InvocationState.WAIT_TOOL else "join_wait"
        self.counts[f"{source}_pressure_demoted"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "parent_pressure_demoted", "ts_ms": time.time() * 1000,
                "workflow_id": key.root_workflow_id,
                "context_id": key.context_id, "context_epoch": key.context_epoch,
                "node_id": node_id, "freed_units": freed,
                "source": source, "invocation_id": key.invocation_id,
                "evidence": "real_allocation_shortfall;unlocked_exclusive_session;host_copy_settled",
            })

    def _publish_parent_pressure_candidates(self) -> None:
        cache = self._native_cache
        candidates = []
        for node_id, (_, created) in self._parent_pressure_candidates.items():
            try:
                node = cache.tree_core.node_by_id(node_id)
                state = node.component_data[2]
            except (AttributeError, KeyError, RuntimeError, ValueError):
                continue
            if (
                node.creation_time == created
                and state.value is not None and state.host_value is not None
                and state.lock_ref == 0 and state.session_ref == 1
                and node_id not in getattr(cache, "ongoing_write_through", {})
                and node.write_through_pending_id is None
                and node.load_back_pending_id is None
            ):
                candidates.append((node_id, created))
        cache.beliefkv_join_pressure_candidates = tuple(candidates)

    def dispatch_join_prepare(self, waiting_queue: Sequence[object] = ()) -> None:
        """Back up waiting parents under pressure; never evict at this safe point."""
        cache = self._native_cache
        if not self.enable_prepare_host or cache is None or self.physical_disabled:
            return
        now_ms = time.monotonic() * 1000
        if now_ms < self._join_prepare_next_ms:
            return
        self._join_prepare_next_ms = now_ms + 50.
        self._parent_pressure_candidates = {
            node: record for node, record in self._parent_pressure_candidates.items()
            if self._live_parent_pressure_node(node, record[1])
        }
        self._publish_parent_pressure_candidates()
        if self.physical_ledger.pending_count:
            return
        headroom = observe_static_full_mamba_headroom(cache)
        if not headroom.observable:
            return
        child_waiting = sum(
            bool((key := _request_key(req)) and (
                invocation := self.graph.invocations.get(key.invocation_id)
            ) and invocation.parent_invocation_id is not None)
            for req in waiting_queue
        )
        if (
            headroom.device_mamba_free_slots >= max(32, 4 * child_waiting)
            and headroom.device_full_free_tokens >= max(65536, 8192 * child_waiting)
        ):
            return
        parents = sorted(
            (key for key in self.context_sessions.values()
             if (parent := self.graph.invocations.get(key.invocation_id)) is not None
             and parent.state is InvocationState.WAIT_JOIN),
            key=lambda key: key.context_id,
        )
        if not parents:
            return
        for offset in range(min(len(parents), 8)):
            key = parents[(self._join_prepare_cursor + offset) % len(parents)]
            parent = self.graph.invocations[key.invocation_id]
            identity = _JoinPrefetchIdentity.from_parent(parent.join_id, key)
            prior = self._join_prepare_commands.get(identity)
            if prior is not None and self.physical_ledger.is_pending(prior):
                continue
            step = self.refreshed_shadow_backup_step(
                context_id=key.context_id, source="join_prepare",
            )
            if step is None:
                anchors = self.snapshot_session_anchors(
                    cache, context_id=key.context_id, context_epoch=key.context_epoch,
                )
                candidate = (
                    capture_action_local_shadow(
                        cache, anchors, for_prefetch=True, include_non_actionable=True,
                    ) if anchors is not None else None
                )
                if candidate is not None and anchors.reusable_input_tokens is not None:
                    nodes = {node.node_id: node for node in candidate.nodes}
                    for node in candidate.nodes:
                        if not (node.mamba_device_present and node.mamba_host_present):
                            continue
                        prefix, current = 0, node
                        while current is not None:
                            prefix += current.key_tokens or 0
                            current = nodes.get(current.parent_id)
                        if prefix <= anchors.reusable_input_tokens:
                            self._parent_pressure_candidates[node.node_id] = (
                                key, node.creation_time,
                            )
                continue
            command = self.issue_shadow_backup_step(step, source="join_prepare")
            if command is None:
                continue
            self._join_prepare_commands[identity] = command
            self._parent_pressure_candidates[step.node_id] = (key, step.creation_time)
            self._join_prepare_cursor = (self._join_prepare_cursor + offset + 1) % len(parents)
            self.counts["join_prepare_issued"] += 1
            break
        self._publish_parent_pressure_candidates()

    def _live_tool_hint(self, key: PrefillCandidateKey) -> NativeToolWaitHint | None:
        hint = self.tool_wait_hints.get(key.context_id)
        inv = self.graph.invocations.get(key.invocation_id)
        if (
            hint is not None and hint.key == key
            and hint.live(key, now_ms=time.monotonic() * 1000)
            and self.context_sessions.get(key.context_id) == key
            and inv is not None and inv.state is InvocationState.WAIT_TOOL
            and inv.updated_ts_ms == hint.invocation_revision_ts_ms
            and len(inv.active_tool_calls) <= 1 and not self._terminal(key)
        ):
            return hint
        return None

    def _long_tool_wait(self, key: PrefillCandidateKey) -> bool:
        hint = self._live_tool_hint(key)
        if hint is None:
            return False
        now_ms = time.monotonic() * 1000
        if hint.release_cdf:
            probability = hint.release_probability_within(2000., now_ms=now_ms)
            return probability is not None and probability <= .1
        remaining = hint.remaining_quantile(.1, now_ms=now_ms)
        return remaining is not None and remaining >= 2000.

    def dispatch_tool_prepare(self, waiting_queue: Sequence[object] = ()) -> None:
        """Back long external waits; native allocator alone decides demotion."""
        if not self.enable_prepare_host or self.physical_disabled or self._native_cache is None:
            return
        now_ms = time.monotonic() * 1000
        if now_ms < self._tool_prepare_next_ms or self.physical_ledger.pending_count:
            return
        self._tool_prepare_next_ms = now_ms + 100.
        headroom = observe_static_full_mamba_headroom(self._native_cache)
        if not headroom.observable or (
            headroom.device_full_free_tokens >= max(65536, 8192 * len(waiting_queue))
            and headroom.device_mamba_free_slots >= max(32, 4 * len(waiting_queue))
        ):
            return
        for hint in tuple(self.tool_wait_hints.values()):
            if not self._long_tool_wait(hint.key):
                continue
            step = self.refreshed_shadow_backup_step(context_id=hint.key.context_id)
            if step is not None:
                command = self.issue_shadow_backup_step(step)
                if command is not None:
                    self._parent_pressure_candidates[step.node_id] = (hint.key, step.creation_time)
                    self.counts["tool_prepare_issued"] += 1
                    break
            anchors = self.snapshot_session_anchors(
                self._native_cache, context_id=hint.key.context_id,
                context_epoch=hint.key.context_epoch,
            )
            candidate = (
                capture_action_local_shadow(
                    self._native_cache, anchors, for_prefetch=True, include_non_actionable=True,
                ) if anchors is not None else None
            )
            if candidate is not None and anchors.reusable_input_tokens is not None:
                nodes = {node.node_id: node for node in candidate.nodes}
                for node in candidate.nodes:
                    if not (node.mamba_device_present and node.mamba_host_present):
                        continue
                    prefix, current = 0, node
                    while current is not None:
                        prefix += current.key_tokens or 0
                        current = nodes.get(current.parent_id)
                    if prefix <= anchors.reusable_input_tokens:
                        self._parent_pressure_candidates[node.node_id] = (hint.key, node.creation_time)
        self._publish_parent_pressure_candidates()

    def _roll_tool_prefetch(self) -> None:
        if not self.enable_tool_prefetch or self.physical_disabled:
            return
        self._refresh_prefetch_service_leases()
        ticket = self._tool_ticket
        if ticket is not None:
            if ticket.command_id is not None:
                if self.physical_ledger.is_pending(ticket.command_id):
                    return
                completed = any(
                    action.command_id == ticket.command_id and action.action == "PREFETCH_GPU"
                    for action in self.completed_physical_actions
                )
                self._tool_opportunity_cache.pop(ticket.key, None)
                self._tool_ticket = None
                if not completed:
                    self.counts["tool_prefetch_lost_ack"] += 1
                    return
                self.counts["tool_prefetch_acked"] += 1
                ticket = None
        if ticket is not None:
            hint = self._live_tool_hint(ticket.key)
            if (
                hint is None or hint.invocation_revision_ts_ms != ticket.revision
                or not self._tool_prefetch_ready(hint)
            ):
                self._tool_ticket = None
            else:
                return
        if self.physical_ledger.pending_count:
            return
        now_ms = time.monotonic() * 1000
        for hint in sorted(
            self.tool_wait_hints.values(),
            key=lambda item: (
                remaining if (remaining := item.remaining_quantile(.5, now_ms=now_ms))
                is not None else math.inf
            ),
        ):
            if self._live_tool_hint(hint.key) is None or self._tool_prefetch_budget[hint.key] >= 2:
                continue
            if any(
                lease.key == hint.key
                for lease in self._prefetch_service_leases.values()
            ):
                continue
            if not self._tool_prefetch_ready(hint):
                self.counts["tool_prefetch_not_in_time_window"] += 1
                continue
            cached = self._tool_opportunity_cache.get(hint.key)
            if cached is not None and now_ms < cached[0]:
                opportunity = cached[1]
            else:
                opportunity = self.inspect_context_h2d_opportunity(
                    context_id=hint.key.context_id, context_epoch=hint.key.context_epoch,
                )
                self._tool_opportunity_cache[hint.key] = (now_ms + 100., opportunity)
                if len(self._tool_opportunity_cache) > 256:
                    self._tool_opportunity_cache = {
                        key: value for key, value in self._tool_opportunity_cache.items()
                        if value[0] > now_ms
                    }
            if opportunity is None or opportunity.step is None:
                self.counts["tool_prefetch_no_restore_target"] += 1
                continue
            if opportunity.fits_current_free_lists is not True:
                self.counts["tool_prefetch_no_free_capacity"] += 1
                continue
            if not self._tool_service_supported(opportunity):
                self.counts["tool_prefetch_service_unsupported"] += 1
                continue
            self._tool_ticket = _ToolPrefetchTicket(hint.key, hint.invocation_revision_ts_ms)
            self.counts["tool_prefetch_window_entered"] += 1
            break

    def _tool_prefetch_ready(self, hint: NativeToolWaitHint) -> bool:
        now_ms = time.monotonic() * 1000
        # P50 and parking now refer to the same surviving event distribution.
        remaining = hint.remaining_quantile(.5, now_ms=now_ms)
        return remaining is not None and remaining <= self.prefetch_lead_ms

    def _tool_service_supported(self, opportunity: SessionH2DOpportunity) -> bool:
        try:
            entries = self._native_cache.cache_controller.mem_pool_host.entry_map
            size = (
                opportunity.required_full_tokens * entries["kv"].host_pool.size_per_token
                + opportunity.required_mamba_slots * entries["mamba"].host_pool.size_per_token
            )
        except (AttributeError, KeyError, TypeError):
            return False
        estimate = estimate_native_service(
            self._native_service_samples, size,
            shape=pool_shape(opportunity.required_full_tokens, opportunity.required_mamba_slots),
        ) or estimate_native_service(self._h2d_samples, size)
        return estimate is not None and estimate.submit_to_ack_p90_ms <= self.prefetch_lead_ms

    def dispatch_tool_prefetch(self) -> None:
        self._roll_tool_prefetch()
        ticket = self._tool_ticket
        if ticket is None or self.physical_ledger.pending_count:
            return
        hint = self._live_tool_hint(ticket.key)
        if (
            hint is None or hint.invocation_revision_ts_ms != ticket.revision
            or not self._tool_prefetch_ready(hint)
        ):
            self._tool_ticket = None
            return
        if self._tool_prefetch_budget[ticket.key] >= 2:
            self._tool_ticket = None
            return
        step = self.refreshed_prefetch_gpu_step(source="tool_wait", context_id=ticket.key.context_id)
        if step is None:
            self._tool_ticket = None
            return
        command = self.issue_prefetch_gpu_step(step)
        if command is not None:
            ticket.command_id = command
            ticket.drained = False
            self._tool_prefetch_budget[ticket.key] += 1
            self.counts["tool_prefetch_issued"] += 1

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
        if not isinstance(step, PrefetchLoadStep):
            self.counts["prefetch_step_stale"] += 1
            return None
        if any(
            pending.key == step.key
            and pending.node_id == step.node_id
            and pending.creation_time == step.creation_time
            for pending, _, _ in self._prefetch_steps.values()
        ):
            self.counts["prefetch_duplicate_inflight_suppressed"] += 1
            return None
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
        issue_wall_ms = time.time() * 1000
        registered = False

        def before_enqueue(operation: object) -> bool:
            nonlocal registered
            if self.event_server is not None:
                self.event_server.drain(max_messages=128)
            if self.refreshed_prefetch_gpu_step(
                source=source,
                **({"context_id": step.key.context_id} if source == "tool_wait" else {}),
            ) != step:
                self.counts["prefetch_causal_invalidated_before_enqueue"] += 1
                return False
            try:
                expected = prefetch_expectation_from_native_op(
                    command_id, step, operation, cache.cache_controller
                )
                self.register_physical_action(expected)
            except (PhysicalReceiptError, AttributeError, TypeError, ValueError):
                self.counts["prefetch_reservation_rejected"] += 1
                return False
            registered = True
            invocation = self.graph.invocations.get(step.key.invocation_id)
            self._prefetch_steps[command_id] = (
                step, source,
                invocation.updated_ts_ms if invocation is not None else None,
            )
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
            beliefkv_include_mamba=step.include_mamba,
        )
        if not outcome.issued:
            if registered:
                self.physical_ledger.cancel_unsubmitted(command_id)
                self._prefetch_steps.pop(command_id, None)
            self.counts["prefetch_native_declined"] += 1
            return None
        if not registered or outcome.node_id != step.node_id:
            self.physical_disabled = True
            raise PhysicalReceiptError("native prefetch issued without a matching reservation")
        self.counts["prefetch_native_issued"] += 1
        self._prefetch_issue_times[command_id] = issue_wall_ms
        self.counts["prefetch_immediate_submit"] += int(getattr(outcome, "load_started", False))
        if self._opportunity_writer is not None:
            tokens = self._context_tokens.get(step.key.context_id)
            record = {
                "event": "prefetch_native_issued",
                "ts_ms": issue_wall_ms,
                "recorded_ts_ms": time.time() * 1000,
                "load_started_immediately": getattr(outcome, "load_started", False),
                "command_id": command_id, "source": source,
                "context_id": step.key.context_id,
                "context_epoch": step.key.context_epoch,
                "node_id": step.node_id, "leaf_node_id": step.leaf_node_id,
                "node_creation_time": step.creation_time,
                "include_mamba": step.include_mamba,
                "reusable_input_tokens": (
                    max(0, tokens[1] - 1) if tokens is not None
                    and tokens[0] == step.key.context_epoch else None
                ),
            }
            ticket = self._join_ticket
            if source == "join_ticket" and ticket is not None:
                stage = self._final_stages.get(ticket.join_id)
                record.update({
                    "workflow_id": step.key.root_workflow_id,
                    "join_id": ticket.join_id,
                    "child_invocation_id": stage.child_id if stage else None,
                    "child_request_id": stage.request_id if stage else None,
                    "phase": ticket.phase,
                })
            elif source == "tool_wait":
                hint = self.tool_wait_hints.get(step.key.context_id)
                invocation = self.graph.invocations.get(step.key.invocation_id)
                record.update({
                    "workflow_id": step.key.root_workflow_id,
                    "invocation_id": step.key.invocation_id,
                    "tool_episode_revision_ms": hint.invocation_revision_ts_ms if hint else None,
                    "active_tool_ids": sorted(invocation.active_tool_calls) if invocation else [],
                    "predicted_remaining_ms": (
                        hint.remaining_quantile(.5, now_ms=time.monotonic() * 1000)
                        if hint else None
                    ),
                    "timing_policy": "survival_conditioned_cdf_or_unexpired_quantile",
                })
            self._opportunity_writer.record(record)
        return command_id

    def _release_prefetch_service_lease(
        self, command_id: str, reason: str, *, request: object | None = None,
    ) -> None:
        lease = self._prefetch_service_leases.get(command_id)
        if lease is None or command_id in self._prefetch_lock_release_failures:
            return
        if lease.lock_params is not None:
            try:
                self._native_cache.tree_core.dec_lock_ref(lease.node_id, lease.lock_params)
            except (AttributeError, AssertionError, KeyError, TypeError, ValueError, RuntimeError):
                self.physical_disabled = True
                self._prefetch_lock_release_failures.add(command_id)
                self.counts["prefetch_native_lock_release_failed"] += 1
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "prefetch_native_lock_release_failed",
                        "ts_ms": time.time() * 1000, "command_id": command_id,
                        "node_id": lease.node_id, "reason": reason,
                        "scope": "receipt retained; no unsafe release retry",
                    })
                return
        self._prefetch_service_leases.pop(command_id)
        self.counts[f"prefetch_residency_released:{reason}"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "prefetch_residency_released", "ts_ms": time.time() * 1000,
                "command_id": command_id, "source": lease.source, "reason": reason,
                "context_id": lease.key.context_id, "context_epoch": lease.key.context_epoch,
                "node_id": lease.node_id, "node_creation_time": lease.creation_time,
                "residency_ms": max(0., time.monotonic() - lease.acknowledged_at) * 1000,
                "pool_bytes": dict(lease.pool_bytes),
                "request_id": getattr(request, "rid", None),
                "native_locked": lease.lock_params is not None,
                "protected_bytes": lease.protected_bytes,
                "scope": "native lock released; not reuse proof",
            })

    def _prefetch_lease_invalid_reason(self, lease: _PrefetchServiceLease) -> str | None:
        if self.physical_disabled:
            return "physical_disabled"
        if time.monotonic() >= lease.expires_at:
            return "service_window_expired"
        key = lease.key
        workflow = self.graph.workflows.get(key.root_workflow_id)
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if workflow is None or invocation is None or context is None:
            return "causal_identity_missing"
        if workflow.end_ts_ms is not None or invocation.state.terminal:
            return "terminal"
        if (
            invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or context.workflow_id != key.root_workflow_id
            or context.epoch not in (key.context_epoch, key.context_epoch + 1)
        ):
            return "context_changed"
        current = self.context_sessions.get(key.context_id)
        pending_epoch_handoff = current is None and context.epoch == key.context_epoch + 1
        if (current is None and not pending_epoch_handoff) or (current is not None and (
            current.root_workflow_id != key.root_workflow_id
            or current.invocation_id != key.invocation_id
            or current.context_epoch not in (key.context_epoch, key.context_epoch + 1)
            or (current.session_id, current.session_generation)
            != (key.session_id, key.session_generation)
        )):
            return "session_changed"
        cache = self._native_cache
        try:
            sessions = cache.session_refs
            generations = getattr(sessions, "_session_generations", None)
            if pending_epoch_handoff and (
                generations is None
                or generations.get(key.session_id) != key.session_generation
            ):
                return "session_handoff_unproven"
            if generations is not None and generations.get(key.session_id) != key.session_generation:
                return "session_changed"
            if key.session_id in getattr(sessions, "_closed_session_ids", ()):
                return "session_closed"
            node = cache.tree_core.node_by_id(lease.node_id)
            if node.creation_time != lease.creation_time:
                return "node_generation_changed"
            if any(
                amount > 0 and node.component_data[component].value is None
                for name, amount in lease.pool_bytes
                for component in (0 if name in ("kv", "full") else 2,)
            ):
                return "native_residency_lost"
        except (AttributeError, KeyError, IndexError, RuntimeError, TypeError, ValueError):
            return "native_residency_unobservable"
        if lease.source == "tool_wait" and invocation.state is InvocationState.WAIT_TOOL:
            if (
                context.epoch != key.context_epoch
                or invocation.updated_ts_ms != lease.wait_revision
            ):
                return "wait_episode_changed"
            # A rolling ETA change does not invalidate an already completed
            # transfer for the same wait episode. Its bounded lease still ends.
        return None

    def _refresh_prefetch_service_leases(self, *, context_id: str | None = None) -> None:
        if not self._prefetch_service_leases:
            return
        now = time.monotonic()
        for command, lease in tuple(self._prefetch_service_leases.items()):
            if context_id is not None and lease.key.context_id != context_id:
                continue
            invocation = self.graph.invocations.get(lease.key.invocation_id)
            if (
                not lease.demand_ready and lease.reentry_ready_at is None
                and lease.lock_params is not None
                and now < lease.expires_at
                and invocation is not None
                and invocation.state is InvocationState.READY
                and not invocation.active_tool_calls
                and not invocation.blocking_child_ids
            ):
                # Real completion can precede HTTP submission; do not pin on ETA.
                lease = replace(
                    lease, reentry_ready_at=now,
                    expires_at=min(lease.acknowledged_at + 10., now + 3.),
                )
                self._prefetch_service_leases[command] = lease
                self.counts["prefetch_residency_reentry_ready"] += 1
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "prefetch_reentry_ready",
                        "ts_ms": time.time() * 1000, "command_id": command,
                        "source": lease.source, "context_id": lease.key.context_id,
                        "invocation_id": lease.key.invocation_id,
                        "context_epoch": lease.key.context_epoch,
                        "submission_grace_ms": (lease.expires_at - now) * 1000.,
                    })
            if (
                not lease.demand_ready
                and now < lease.expires_at
                and any(
                    key.context_id == lease.key.context_id
                    and key.invocation_id == lease.key.invocation_id
                    and key.root_workflow_id == lease.key.root_workflow_id
                    and key.session_id == lease.key.session_id
                    and key.session_generation == lease.key.session_generation
                    and key.context_epoch in (
                        lease.key.context_epoch, lease.key.context_epoch + 1,
                    )
                    and key.request_id != lease.key.request_id
                    for key in self.visible.values()
                )
            ):
                lease = replace(
                    lease, demand_ready=True,
                    expires_at=(
                        lease.acknowledged_at + 10.
                        if lease.lock_params is not None else lease.expires_at
                    ),
                )
                self._prefetch_service_leases[command] = lease
                self.counts["prefetch_residency_demand_ready"] += 1
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "prefetch_demand_submitted",
                        "ts_ms": time.time() * 1000, "command_id": command,
                        "source": lease.source, "context_id": lease.key.context_id,
                        "ready_to_submit_ms": (
                            (now - lease.reentry_ready_at) * 1000.
                            if lease.reentry_ready_at is not None else None
                        ),
                        "ack_to_submit_ms": (now - lease.acknowledged_at) * 1000.,
                        "remaining_lease_ms": (lease.expires_at - now) * 1000.,
                    })
            reason = self._prefetch_lease_invalid_reason(lease)
            if reason is not None:
                self._release_prefetch_service_lease(command, reason)

    def _register_prefetch_service_lease(self, action: PhysicalActionCompleted) -> None:
        pending = self._prefetch_steps.pop(action.command_id, None)
        if action.action != "PREFETCH_GPU" or pending is None:
            return
        step, source, revision = pending
        now = time.monotonic()
        lock_params, protected_bytes = None, 0
        cache = self._native_cache
        lease = _PrefetchServiceLease(
            step.key, action.command_id, step.node_id, step.creation_time,
            source, revision, now,
            now + (self.prefetch_lead_ms + 1000.) / 1000., action.pool_bytes,
        )
        reason = self._prefetch_lease_invalid_reason(lease)
        if reason is not None:
            self.counts[f"prefetch_residency_registration_skipped:{reason}"] += 1
            return
        try:
            from beliefkv.runtime.sglang_v0520_observer import observe_unified_node_closure
            observation = observe_unified_node_closure(cache, step.node_id, max_nodes=64)
            entries = cache.host_pool_group.entry_map
            protected_bytes = sum(
                node.full_device_tokens * entries["kv"].host_pool.size_per_token
                for node in observation.nodes
            ) + sum(
                int(node.mamba_device_present) * entries["mamba"].host_pool.size_per_token
                for node in observation.nodes if node.node_id == step.node_id
            )
            if (
                observation.observable and len(self._prefetch_service_leases) < 4
                and protected_bytes + sum(
                    lease.protected_bytes for lease in self._prefetch_service_leases.values()
                ) <= 1024 ** 3
            ):
                try:
                    lock_params = cache.tree_core.inc_lock_ref(step.node_id).to_dec_params()
                except (AttributeError, AssertionError, KeyError, TypeError, ValueError, RuntimeError):
                    self.physical_disabled = True
                    self.counts["prefetch_native_lock_acquire_failed"] += 1
                    return
                if getattr(lock_params, "node_id", None) != step.node_id:
                    self.physical_disabled = True
                    self.counts["prefetch_native_lock_receipt_invalid"] += 1
                    self._prefetch_lock_release_failures.add(action.command_id)
                else:
                    self.counts["prefetch_native_lock_acquired"] += 1
            else:
                protected_bytes = 0
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
            protected_bytes = 0
        lease = replace(lease, lock_params=lock_params, protected_bytes=protected_bytes)
        self._prefetch_service_leases[action.command_id] = lease
        reason = self._prefetch_lease_invalid_reason(lease)
        if reason is not None:
            self._release_prefetch_service_lease(action.command_id, reason)
            self.counts[f"prefetch_residency_registration_skipped:{reason}"] += 1
            return
        self.counts["prefetch_residency_registered"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "prefetch_residency_registered", "ts_ms": time.time() * 1000,
                "command_id": action.command_id, "source": source,
                "context_id": step.key.context_id, "context_epoch": step.key.context_epoch,
                "session_id": step.key.session_id, "session_generation": step.key.session_generation,
                "node_id": step.node_id, "node_creation_time": step.creation_time,
                "pool_bytes": dict(action.pool_bytes),
                "lease_ms": (lease.expires_at - now) * 1000,
                "native_locked": lock_params is not None,
                "protected_bytes": protected_bytes,
                "scope": "bounded native receipt lock; max four leases and one GiB closure",
            })

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
        for context_id, epoch, session_id, generation in self.physical_ledger.pending_transfer_sessions:
            if context_id in live_sessions or live_epochs.get(context_id) != epoch + 1:
                continue
            context = self.graph.contexts.get(context_id)
            workflow = self.graph.workflows.get(context.workflow_id) if context else None
            if workflow is None or workflow.end_ts_ms is not None or cache is None:
                continue
            try:
                snapshot = getattr(
                    cache.session_refs, "snapshot_latest_session_leaf_anchors",
                    cache.session_refs.snapshot_session_leaf_anchors,
                )
                anchors = snapshot(
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
                native_cache=cache,
            )
        except PhysicalReceiptError as error:
            self.physical_disabled = True
            self._prefetch_steps.clear()
            for command in tuple(self._prefetch_service_leases):
                self._release_prefetch_service_lease(command, "physical_receipt_failure")
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
        self._join_prepare_commands = {
            identity: command for identity, command in self._join_prepare_commands.items()
            if self.physical_ledger.is_pending(command)
        }
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
        if (
            getattr(commit, "status", None) == "completed"
            and getattr(commit, "direction", None) in ("h2d", "d2h")
            and type(actual_bytes) is int and actual_bytes > 0
            and type(ack_ms) in (int, float) and math.isfinite(ack_ms)
            and 0 < ack_ms <= 60_000
        ):
            units = dict(getattr(commit, "num_tokens_by_pool", ()) or ())
            full = units.get("kv", units.get("full", 0))
            mamba = units.get("mamba", 0)
            self._native_service_samples.append(NativeServiceSample(
                actual_bytes, float(ack_ms), getattr(commit, "direction"),
                pool_shape(full, mamba), getattr(commit, "enqueue_to_submit_ms", None),
            ))
        for action in completed:
            self._register_prefetch_service_lease(action)
            issued = self._prefetch_issue_times.pop(action.command_id, None)
            submitted = getattr(commit, "submit_ts_ms", None)
            if issued is not None and submitted is not None and self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "prefetch_submit_queue_observed",
                    "ts_ms": time.time() * 1000, "command_id": action.command_id,
                    "issued_ts_ms": issued, "submit_ts_ms": submitted,
                    "issue_to_submit_ms": max(0., submitted - issued),
                })
        self.counts["native_physical_completed"] += len(completed)
        return completed

    def register_visible_request(self, req: object) -> bool:
        key = _request_key(req)
        if key is None or self._terminal(key):
            self.counts["invalid_request_identity"] += 1
            return False
        if key.request_id in self.visible:
            raise ValueError(f"duplicate visible request: {key.request_id}")
        previous = self.context_sessions.get(key.context_id)
        if previous is not None and previous != key:
            self._tool_last_queries.pop(previous, None)
            self._tool_opportunity_cache.pop(previous, None)
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
        self._visible_since[key.request_id] = time.monotonic()
        if key.session_id is not None and key.session_generation is not None:
            self.context_sessions[key.context_id] = key
        else:
            self.context_sessions.pop(key.context_id, None)
        self.semantic_revision += 1
        self._refresh_prefetch_service_leases(context_id=key.context_id)
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
            self._visible_since.pop(request_id, None)
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
            self._visible_since.setdefault(key.request_id, time.monotonic())
            if key.session_id is not None and key.session_generation is not None:
                self.context_sessions[key.context_id] = key
            else:
                self.context_sessions.pop(key.context_id, None)
            self.demand_hints.pop(key.request_id, None)
            self.semantic_revision += 1

    def _forget_session(self, request_id: str) -> None:
        self._visible_since.pop(request_id, None)
        for context_id, key in tuple(self.context_sessions.items()):
            if key.request_id == request_id:
                self._tool_last_queries.pop(key, None)
                self._tool_opportunity_cache.pop(key, None)
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

        def waiting_rank(pair: tuple[int, object]) -> tuple:
            index, request = pair
            since = self._visible_since.get(getattr(request, "rid", ""), now_ms / 1000.)
            if now_ms / 1000. - since >= 10.:
                return (-1, since, index, index)
            return (
                *ranks[index][:2],
                valid_hints[index].next_output_tokens
                if ranks[index][0] < 5
                and hinted[ranks[index][:2]] == members[ranks[index][:2]]
                else index,
                index,
            )

        ordered = sorted(tagged, key=waiting_rank)
        self._final_priority_promoted = None
        self._final_priority_native_rank = None
        self._prefetch_priority_promoted = None
        self._prefetch_priority_native_rank = None
        priority_candidates = []
        if self._prefetch_priority_normal_admissions >= 4 and ordered:
            for candidate in ordered:
                key = _request_key(candidate[1])
                matches = [
                    lease for lease in self._prefetch_service_leases.values()
                    if lease.demand_ready
                    and key is not None and key.request_id != lease.key.request_id
                    and lease.key.context_id == key.context_id
                    and lease.key.invocation_id == key.invocation_id
                    and lease.key.root_workflow_id == key.root_workflow_id
                    and lease.key.session_id == key.session_id
                    and lease.key.session_generation == key.session_generation
                    and key.context_epoch in (lease.key.context_epoch, lease.key.context_epoch + 1)
                ]
                if matches:
                    priority_candidates.append((
                        min(lease.expires_at for lease in matches),
                        0 if any(lease.source == "join_ticket" for lease in matches) else 1,
                        candidate,
                    ))
            if priority_candidates:
                candidate = min(priority_candidates, key=lambda item: item[:2])[2]
                native_rank = ordered.index(candidate)
                # An aged native head keeps its next turn; speculative restores
                # cannot perpetually defer an ordinary request's admission.
                head = _request_key(ordered[0][1])
                priority_now = time.monotonic()
                aged_head = head is not None and (
                    priority_now - self._visible_since.get(head.request_id, priority_now) >= 10.
                )
                if native_rank > 0 and not aged_head:
                    ordered.remove(candidate)
                    ordered.insert(0, candidate)
                    self._prefetch_priority_promoted = candidate[1].rid
                    self._prefetch_priority_native_rank = native_rank
                    self.counts["prefetch_priority_ordered"] += 1
                elif aged_head:
                    self.counts["prefetch_priority_aged_head_kept"] += 1
        if (
            (
                self.enable_final_stage_priority
                if self.enable_final_stage_priority is not None
                else self.enable_admission_prefetch or self.enable_final_stage_prefetch
            )
            and self._final_priority_normal_admissions >= 4
            and self._prefetch_priority_normal_admissions >= 4
            and self._prefetch_priority_promoted is None and ordered
            and time.monotonic() - self._visible_since.get(
                getattr(ordered[0][1], "rid", ""), time.monotonic(),
            ) < 10.
        ):
            for stage in self._final_stages.values():
                if (
                    stage.semantic_only or not self._live_final_stage(stage)
                    or stage.request_id is None
                ):
                    continue
                candidate = next((
                    pair for pair in ordered
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
        if self._tool_timing_only or self._model_worker is None or self._model_worker.disabled:
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
                if req.rid in (self._prefetch_priority_promoted, self._final_priority_promoted):
                    self._prefetch_priority_normal_admissions = 0
                    if req.rid == self._prefetch_priority_promoted:
                        self.counts["prefetch_priority_admitted"] += 1
                        if self._opportunity_writer is not None:
                            self._opportunity_writer.record({
                                "event": "restore_ready_priority_admitted",
                                "ts_ms": time.time() * 1000,
                                "request_id": req.rid,
                                "tagged_displaced": self._prefetch_priority_native_rank,
                                "queue_wait_ms": (
                                    time.monotonic() - self._visible_since.get(req.rid, time.monotonic())
                                ) * 1000.,
                            })
                else:
                    self._prefetch_priority_normal_admissions = min(
                        4, self._prefetch_priority_normal_admissions + 1,
                    )
                if req.rid == self._prefetch_priority_promoted:
                    self._final_priority_normal_admissions = 0
                elif req.rid == self._final_priority_promoted:
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
            elif result == "NO_TOKEN" and self._prefetch_service_leases:
                # Urgent real demand may reclaim the oldest speculative lock.
                oldest = min(self._prefetch_service_leases.values(), key=lambda item: item.acknowledged_at)
                self._release_prefetch_service_lease(oldest.command_id, "allocation_pressure")

    def on_batch_selected(self, batch: object) -> None:
        pass

    def on_batch_completed(self, batch: object) -> None:
        for req in batch.reqs:
            key = _request_key(req)
            if key is not None:
                for command, lease in tuple(self._prefetch_service_leases.items()):
                    target = lease.key
                    if (
                        key.root_workflow_id == target.root_workflow_id
                        and key.invocation_id == target.invocation_id
                        and key.context_id == target.context_id
                        and key.context_epoch in (target.context_epoch, target.context_epoch + 1)
                        and (key.session_id, key.session_generation)
                        == (target.session_id, target.session_generation)
                    ):
                        self._release_prefetch_service_lease(
                            command, "first_gpu_service", request=req,
                        )
            if self._semantic_worker is not None:
                key = self.visible.get(getattr(req, "rid", None))
                if key is not None:
                    self._semantic_keys[key.request_id] = key
                    history = self._semantic_progress.setdefault(
                        key.request_id, deque(maxlen=128),
                    )
                    tokens = len(getattr(req, "output_ids", ()) or ())
                    start = self._decoded_scan_positions.get(key.request_id, 0)
                    outputs = getattr(req, "output_ids", ()) or ()
                    self._decoded_scan_positions[key.request_id] = len(outputs)
                    if (
                        key.request_id not in self._decoded_tool_requests
                        and any(token in self._tool_open_token_ids for token in outputs[start:])
                    ):
                        self._decoded_tool_requests.add(key.request_id)
                        self._semantic_forecasts.pop(key.request_id, None)
                        self._semantic_frames.pop(key.request_id, None)
                        for join_id, active in tuple(self._final_stages.items()):
                            if active.child_id == key.invocation_id:
                                self._clear_final_stage(join_id)
                        self.counts["semantic_native_tool_marker_invalidated"] += 1
                    if not history or tokens > history[-1][1]:
                        history.append((time.monotonic() * 1000, tokens))
                    if req.finished():
                        self._semantic_finished[key.request_id] = (
                            time.monotonic() * 1000, tokens,
                        )
                        self._record_native_final_body(req, key)
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
                self._visible_since.pop(req.rid, None)
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
        self._refresh_prefetch_service_leases()

    def on_abort_request(self, abort: object) -> None:
        removed = [
            rid
            for rid in self.visible
            if getattr(abort, "abort_all", False) or rid.startswith(abort.rid)
        ]
        for rid in removed:
            del self.visible[rid]
            self._visible_since.pop(rid, None)
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
        self._refresh_prefetch_service_leases()

    def running_batch_retraction_barrier_required(self, batch: object) -> bool:
        self._roll_tool_prefetch()
        tool = self._tool_ticket
        if (
            tool is not None and not tool.drained and not self.physical_ledger.pending_count
            and self._live_tool_hint(tool.key) is not None
            and self._tool_prefetch_budget[tool.key] < 2
        ):
            tool.drained = True
            self.counts["tool_overlap_drain_requested"] += 1
            return True
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
