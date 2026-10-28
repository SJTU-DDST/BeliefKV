"""Bounded semantic admission and action-local native H2D for FULL/MAMBA.

The SGLang cache remains the physical capacity and transfer authority.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field, replace
from heapq import nsmallest
from itertools import chain, count, islice
import hashlib
import math
import json
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from beliefkv.control.causal_graph import (
    GraphDelta, InvocationState, RuntimeCausalContextGraph,
)
from beliefkv.core.events import RelationType, RuntimeEventKind, is_unobserved_join_event
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.predictor.composer import observed_boundary_action
from beliefkv.predictor.completion_lead import (
    CompletionLead,
    load_pinned_completion_lead,
)
from beliefkv.runtime.event_channel import (
    CONTROL_TIMING_SAMPLE_EVERY,
    RuntimeEventDatagramServer,
)
from beliefkv.runtime.hotpath_timing import HotpathTiming, timed_runtime
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry
from beliefkv.runtime.sglang_v0520_admission import (
    _request_key,
    NativePrefillPlan,
    PrefillCandidateKey,
    compile_native_prefill_plan,
    select_native_prefill_candidates,
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
    PrefetchCheckpointPlan,
    PrefetchLoadStep,
    SessionH2DOpportunity,
    ShadowBackupStep,
    capture_action_local_shadow,
    has_host_backed_missing_copy,
    inspect_session_residency_hint,
    inspect_session_h2d_opportunity,
    missing_prepare_full_prefix_tokens,
    next_prefetch_gpu_step,
    next_shadow_backup_step,
    plan_prefetch_gpu_steps,
    revalidate_prefetch_subset,
    prefetch_expectation_from_native_op,
    shadow_backup_steps,
    shadow_expectation_from_native_op,
)
from beliefkv.runtime.sglang_v0520_observer import (
    StaticPoolHeadroomObservation,
    normalize_native_creation_time,
    observe_static_full_mamba_headroom,
    observe_unified_node_closure,
)
from beliefkv.predictor.structured_frontier import LocalFrontierFeatures
from beliefkv.predictor.online_workload_timing import (
    OnlineReportWork, OnlineWorkloadToolTiming,
)
from beliefkv.runtime.semantic_report_worker import (
    SEMANTIC_TEXT, SemanticReportInput, SemanticReportReply,
)
from beliefkv.runtime.clock_evidence import (
    SEMANTIC_FRAME_INTERVAL_MS,
    local_monotonic_clock_domain,
)
from beliefkv.runtime.native_h2d_seed import load_h2d_seed
from beliefkv.runtime.native_transfer_service import (
    NativeServiceEstimate,
    NativeServiceSample,
    estimate_larger_full_h2d,
    estimate_native_service,
    load_native_service_seed,
    native_service_planning_ceiling,
    percentile,
    pool_shape,
)
from beliefkv.runtime.native_transfer_policy import (
    PrefetchResidencyBudget,
    SelectiveJoinPrefetchPolicy,
    TransferStartWindow,
    native_residency_budget,
    transfer_start_window,
)
from beliefkv.runtime.tool_continuity import ToolContinuationPolicy
from beliefkv.runtime.tool_residency_grace import ToolResidencyGrace
from beliefkv.runtime.subagent_retention import DormantSubagentRetention
from beliefkv.runtime.predicted_tool_offload import PredictedToolOffload
from beliefkv.runtime.idle_prepare_backend import idle_prepare_backend
from beliefkv.runtime.idle_host_mirror_recycle import IdleHostMirrorRecycle

if TYPE_CHECKING:
    from beliefkv.core.events import RuntimeEvent


TOOL_OFFLOAD_WAIT_MS = 1000.
TOOL_PREFETCH_PLANNING_SLACK_MS = 250.
IDLE_FULL_PREPARE_INTERVAL_MS = 200.
SPAWN_FULL_PREPARE_MAX_BYTES = 4 * 1024**3


@dataclass(frozen=True)
class _NativePrefillQueueInspection:
    owner: object = field(repr=False)
    semantic_revision: int
    graph_version: int
    sessions_revision: int
    entries: tuple[tuple[object, PrefillCandidateKey | None], ...] = field(repr=False)
    terminal_request_ids: tuple[str, ...]


@dataclass
class _AdmissionPrefetchLease:
    key: PrefillCandidateKey
    expires_at: float
    command_id: str | None = None
    issued_nodes: int = 0


@dataclass
class _ExecutionHandoffTicket:
    key: PrefillCandidateKey
    request: object
    expires_at: float
    command_id: str | None = None
    issued_nodes: int = 0
    command_ids: tuple[str, ...] = ()


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
    command_ids: tuple[str, ...] = ()
    work_endpoint_tokens: float | None = None
    client_request_id: str | None = None
    client_submit_ts_ms: float | None = None
    checkpoint_key: PrefillCandidateKey | None = None
    checkpoint_input_tokens: int | None = None
    restoration_complete: bool = False
    report_start_window_ms: float | None = None
    report_service_shape: _WaitPrefetchServiceShape | None = None


@dataclass(frozen=True)
class _CompletionReentryHint:
    key: PrefillCandidateKey
    join_id: str
    child_id: str
    child_epoch: int
    issued_monotonic_ms: float


@dataclass(frozen=True)
class _ChildReportNotice:
    workflow_id: str
    context_id: str
    context_epoch: int
    estimated_tokens: int
    join_id: str
    expires_at: float
    request_id: str | None = None


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


@dataclass(frozen=True)
class _WaitPrefetchServiceShape:
    key: PrefillCandidateKey
    leaf_node_id: int
    leaf_creation_time: int | float
    full_tokens: int
    mamba_slots: int

    @classmethod
    def from_opportunity(
        cls, opportunity: SessionH2DOpportunity,
    ) -> _WaitPrefetchServiceShape | None:
        step = opportunity.step
        full = getattr(opportunity, "required_full_tokens", None)
        mamba = getattr(opportunity, "required_mamba_slots", None)
        if (
            step is None or type(full) is not int or type(mamba) is not int
            or min(full, mamba) < 0 or full + mamba == 0
        ):
            return None
        return cls(
            step.key, step.leaf_node_id, step.leaf_creation_time,
            full, mamba,
        )

    def contains(self, opportunity: SessionH2DOpportunity) -> bool:
        step = opportunity.step
        return bool(
            step is not None and step.key == self.key
            and (step.leaf_node_id, step.leaf_creation_time)
            == (self.leaf_node_id, self.leaf_creation_time)
            and 0 <= opportunity.required_full_tokens <= self.full_tokens
            and 0 <= opportunity.required_mamba_slots <= self.mamba_slots
            and (
                opportunity.required_full_tokens < self.full_tokens
                or opportunity.required_mamba_slots < self.mamba_slots
            )
        )


@dataclass
class _ToolPrefetchTicket:
    key: PrefillCandidateKey
    revision: float
    command_id: str | None = None
    drained: bool = False
    start_window_ms: float | None = None
    command_ids: tuple[str, ...] = ()
    service_shape: _WaitPrefetchServiceShape | None = None


@dataclass(frozen=True, slots=True)
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
    client_submitted_at: float | None = None
    host_lock_params: object | None = None


@dataclass
class _ParentCapacityHandoff:
    key: PrefillCandidateKey
    join_id: str
    created_at: float
    child_ids: set[str] = field(default_factory=set)
    freed_device_units: Counter = field(default_factory=Counter)
    freed_host_units: Counter = field(default_factory=Counter)
    last_return_ts_ms: float = 0.


@dataclass
class _TerminalChildReclaim:
    key: PrefillCandidateKey
    parent_context_id: str | None
    join_id: str | None
    returned_ts_ms: float
    observed_return_wall_ts_ms: float
    retry_anchors: tuple = ()
    invocation_scope: str = "unknown"


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
        self.internal_comparison_mode = os.environ.get(
            "BELIEFKV_INTERNAL_COMPARISON_MODE", "full",
        )
        if self.internal_comparison_mode not in (
            "full", "join_priority_only", "scheduler_only", "kv_only",
        ):
            raise ValueError("unsupported internal runtime comparison mode")
        self._custom_kv_management = self.internal_comparison_mode not in (
            "join_priority_only", "scheduler_only",
        )
        self._join_priority_pending: dict[str, str] = {}
        self._join_priority_promoted: str | None = None
        self._join_priority_normal_admissions = 4
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
        native_order_setting = os.environ.get("BELIEFKV_NATIVE_PREFILL_ORDER", "0")
        if native_order_setting not in ("0", "1"):
            raise ValueError("native prefill order setting must be 0 or 1")
        self._native_prefill_order = native_order_setting == "1"
        join_priority_setting = os.environ.get("BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY", "1")
        if join_priority_setting not in ("0", "1"):
            raise ValueError("JOIN parent priority setting must be 0 or 1")
        self.enable_join_parent_priority = (
            join_priority_setting == "1" and not self._native_prefill_order
            and self.internal_comparison_mode != "kv_only"
        )
        scheduling_setting = os.environ.get("BELIEFKV_PREFILL_SCHEDULING_POLICY", "workflow")
        if scheduling_setting not in ("workflow", "join_priority", "workflow_completion"):
            raise ValueError("prefill scheduling policy must be workflow, join_priority or workflow_completion")
        self.prefill_scheduling_policy = (
            "native" if self._native_prefill_order else
            "join_priority" if self.internal_comparison_mode == "join_priority_only"
            else scheduling_setting
        )
        continuity_setting = os.environ.get("BELIEFKV_ENABLE_TOOL_CONTINUITY", "0")
        if continuity_setting not in ("0", "1"):
            raise ValueError("tool continuity setting must be 0 or 1")
        self._tool_continuity = (
            ToolContinuationPolicy()
            if continuity_setting == "1"
            and self.prefill_scheduling_policy in ("workflow", "join_priority")
            and self.internal_comparison_mode != "kv_only"
            else None
        )
        self._tool_continuity_promoted: str | None = None
        self._tool_continuity_normal_admissions = 4
        grace_ms = float(os.environ.get("BELIEFKV_TOOL_CONTINUITY_GRACE_MS", "0"))
        if not math.isfinite(grace_ms) or not 0 <= grace_ms <= 1000.:
            raise ValueError("tool continuity grace must be in [0, 1000] ms")
        self.tool_continuity_grace_ms = (
            grace_ms if self._custom_kv_management and self._tool_continuity is not None else 0.
        )
        self._tool_residency_grace = (
            ToolResidencyGrace(self.tool_continuity_grace_ms)
            if self.tool_continuity_grace_ms else None
        )
        last_child_setting = os.environ.get("BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY", "0")
        if last_child_setting not in ("0", "1"):
            raise ValueError("JOIN last child priority setting must be 0 or 1")
        self.enable_join_last_child_priority = (
            last_child_setting == "1" and self.prefill_scheduling_policy == "join_priority"
            and self.internal_comparison_mode != "kv_only"
        )
        self._workflow_attained_work_enabled = (
            os.environ.get("BELIEFKV_WORKFLOW_ATTAINED_WORK", "0") == "1"
            and self.prefill_scheduling_policy != "join_priority"
        )
        if self.prefill_scheduling_policy == "workflow_completion":
            self._workflow_attained_work_enabled = True
        self._workflow_attained_output_tokens: dict[str, int] = {}
        self._workflow_work_promoted: str | None = None
        self.predictor_sha256 = predictor_sha256
        self.demand_hints: dict[str, NativeDemandHint] = {}
        self.tool_wait_hints: dict[str, NativeToolWaitHint] = {}
        self.join_wait_hints: dict[str, NativeJoinWaitHint] = {}
        self.completion_lead = completion_lead
        self._completion_hints: dict[str, _CompletionReentryHint] = {}
        self._final_stages: OrderedDict[str, _ChildFinalStage] = OrderedDict()
        self._final_request_stages: dict[str, _ChildFinalStage] = {}
        self._h2d_samples: deque[tuple[int, float]] = deque(maxlen=64)
        self._native_service_samples: deque[NativeServiceSample] = deque(maxlen=256)
        self._h2d_full_service_samples: deque[tuple[float, NativeServiceSample]] = deque(
            maxlen=32,
        )
        self._h2d_service_estimates: dict[tuple[int, str], NativeServiceEstimate | None] = {}
        self._d2h_service_estimates: dict[tuple[int, str], NativeServiceEstimate | None] = {}
        self._residency_ordering_hints: dict[PrefillCandidateKey, tuple[float, dict]] = {}
        self._residency_hint_admissions: dict[str, tuple[int, PrefillCandidateKey, int]] = {}
        service_path = os.environ.get("BELIEFKV_TRANSFER_SERVICE_SEED")
        service_sha = os.environ.get("BELIEFKV_TRANSFER_SERVICE_SEED_SHA256")
        if bool(service_path) != bool(service_sha):
            raise ValueError("transfer service seed requires a path and SHA256")
        if service_path:
            self._native_service_samples.extend(load_native_service_seed(service_path, service_sha))
        self._prefetch_issue_times: dict[str, float] = {}
        self._physical_command_namespace: str | None = None
        self._physical_command_sequence = count(1)
        self._prefetch_steps: dict[
            str, tuple[PrefetchLoadStep, str, float | None]
        ] = {}
        self._prefetch_issue_budgets: dict[str, PrefetchResidencyBudget] = {}
        self._prefetch_service_leases: dict[str, _PrefetchServiceLease] = {}
        protection_setting = os.environ.get("BELIEFKV_PREFETCH_RESIDENCY_PROTECTION", "1")
        if protection_setting not in ("0", "1"):
            raise ValueError("prefetch residency protection must be 0 or 1")
        self.enable_prefetch_residency_protection = (
            protection_setting == "1" and self._custom_kv_management
        )
        self._prefetch_admission_hints: dict[str, PrefillCandidateKey] = {}
        self._tool_prefetch_survival_evidence: OrderedDict[
            PrefillCandidateKey, tuple[NativeToolWaitHint, bool]
        ] = OrderedDict()
        self._prefetch_lock_release_failures: set[str] = set()
        self._prefetch_priority_normal_admissions = 4
        frontier_quota = os.environ.get("BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA", "4")
        if frontier_quota not in ("0", "4"):
            raise ValueError("frontier restore ordinary quota must be 0 or 4")
        self.frontier_restore_ordinary_quota = int(frontier_quota)
        self._resident_priority_normal_admissions = 4
        self._prefetch_priority_promoted: str | None = None
        self._prefetch_priority_native_rank: int | None = None
        self._prefetch_priority_aged_head_bypassed = False
        self._prefetch_priority_complete_ready_quota: int | None = None
        self._prefetch_priority_partial_restore_details: dict | None = None
        self._resident_priority_promoted: str | None = None
        self._resident_priority_details: dict | None = None
        self._native_running_batch: object | None = None
        self._capacity_waiting_queue: Sequence[object] = ()
        self._native_max_running: int | None = None
        self._native_prefill_slots: int | None = None
        self._native_page_size = 1
        self._native_input_reserve = 0
        self._native_chunk_input_reserve: int | None = None
        self._residency_budget = PrefetchResidencyBudget(4, 1024 ** 3)
        self._residency_headroom: StaticPoolHeadroomObservation | None = None
        self._visible_since: dict[str, float] = {}
        lead_setting = float(os.environ.get("BELIEFKV_PREFETCH_LEAD_MS", "1000"))
        if not math.isfinite(lead_setting) or lead_setting < 0:
            raise ValueError("prefetch lead must be finite and nonnegative")
        self.prefetch_lead_ms = lead_setting
        self.tool_prefetch_priority_ms = float(os.environ.get(
            "BELIEFKV_TOOL_PREFETCH_PRIORITY_MS", "0",
        ))
        if not math.isfinite(self.tool_prefetch_priority_ms) or self.tool_prefetch_priority_ms < 0:
            raise ValueError("tool prefetch priority window must be finite and nonnegative")
        self.join_prefetch_min_advance_ms = float(os.environ.get(
            "BELIEFKV_JOIN_PREFETCH_MIN_ADVANCE_MS", "0",
        ))
        self.tool_prefetch_min_advance_ms = float(os.environ.get(
            "BELIEFKV_TOOL_PREFETCH_MIN_ADVANCE_MS", "0",
        ))
        self._prediction_maintenance_interval_ms = float(os.environ.get(
            "BELIEFKV_PREDICTION_MAINTENANCE_INTERVAL_MS", "0",
        ))
        if not all(math.isfinite(value) and value >= 0 for value in (
            self.join_prefetch_min_advance_ms, self.tool_prefetch_min_advance_ms,
            self._prediction_maintenance_interval_ms,
        )):
            raise ValueError("prefetch advance and maintenance intervals must be finite and nonnegative")
        parallel_setting = os.environ.get("BELIEFKV_PARALLEL_WAIT_PREFETCH", "0")
        if parallel_setting not in ("0", "1"):
            raise ValueError("parallel wait prefetch must be 0 or 1")
        self._parallel_wait_prefetch = parallel_setting == "1"
        self._wait_prefetch_contexts = int(os.environ.get("BELIEFKV_WAIT_PREFETCH_CONTEXTS", "1"))
        if not 1 <= self._wait_prefetch_contexts <= 2:
            raise ValueError("wait prefetch contexts must be one or two")
        self._prediction_maintenance_next_ms = 0.
        self._prediction_maintenance_graph_version = -1
        self._wait_prediction_maintenance_deferred = False
        self._lease_refresh_signature = None
        self._lease_refresh_next_ms = 0.
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
        self._prepare_probe_after_ms: dict[PrefillCandidateKey, float] = {}
        self._parent_pressure_candidates: dict[int, tuple[PrefillCandidateKey, int | float]] = {}
        self._parent_pressure_groups: dict[PrefillCandidateKey, set[int]] = {}
        self._parent_pressure_group_source: dict | None = None
        self._parent_pressure_group_size = 0
        self._parent_pressure_positions: dict[int, int] = {}
        self._parent_pressure_next_position = 0
        self._final_priority_normal_admissions = 4
        self._final_priority_promoted: str | None = None
        self._final_priority_native_rank: int | None = None
        self._join_last_child_priority_promoted: str | None = None
        self._join_ticket: _JoinPrefetchTicket | None = None
        self._confirmed_join_tickets: dict[str, _JoinPrefetchTicket] = {}
        self._empty_join_reentry_origins: dict[str, PrefillCandidateKey] = {}
        self._join_inflight_tickets: dict[str, _JoinPrefetchTicket] = {}
        self._join_prefetch_issued: Counter[_JoinPrefetchIdentity] = Counter()
        self.enable_admission_prefetch = enable_admission_prefetch
        self.enable_final_stage_prefetch = enable_final_stage_prefetch
        self.enable_resident_first = os.environ.get(
            "BELIEFKV_ENABLE_RESIDENT_FIRST", "1",
        ) == "1" and self.prefill_scheduling_policy not in ("join_priority", "workflow_completion")
        priority_setting = os.environ.get("BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY")
        self.enable_final_stage_priority = (
            False if self.prefill_scheduling_policy in ("join_priority", "workflow_completion")
            else None if priority_setting is None else priority_setting == "1"
        )
        self.enable_prepare_host = os.environ.get("BELIEFKV_ENABLE_PREPARE_HOST", "1") == "1"
        offload_setting = os.environ.get("BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD", "0")
        if offload_setting not in ("0", "1"):
            raise ValueError("predicted tool offload setting must be 0 or 1")
        self.enable_predicted_tool_offload = (
            self._custom_kv_management and self.enable_prepare_host and offload_setting == "1"
        )
        self._predicted_tool_offload = (
            PredictedToolOffload.from_environment(self)
            if self.enable_predicted_tool_offload else None
        )
        self.tool_offload_policy = (
            os.environ.get("BELIEFKV_TOOL_OFFLOAD_POLICY", "fixed")
            if self.enable_predicted_tool_offload else "off"
        )
        before_admission = os.environ.get("BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION", "0")
        if before_admission not in ("0", "1"):
            raise ValueError("tool offload before admission must be 0 or 1")
        self.tool_offload_before_admission = before_admission == "1"
        if self.tool_offload_before_admission and (
            self.internal_comparison_mode != "full"
            or not self.enable_predicted_tool_offload
            or self.tool_offload_policy != "demand_cost"
        ):
            raise ValueError("tool offload before admission requires Full demand-cost offload")
        layer_handoff = os.environ.get("BELIEFKV_LAYER_HANDOFF", "0")
        if layer_handoff not in ("0", "1"):
            raise ValueError("layer handoff must be 0 or 1")
        self.layer_handoff_enabled = layer_handoff == "1"
        if self.layer_handoff_enabled and (
            not self.tool_offload_before_admission
            or os.environ.get("BELIEFKV_AGENT_ADMISSION") != "thunderagent_reference"
        ):
            raise ValueError("layer handoff requires reference Full pre-admission tool offload")
        free_fence = os.environ.get("BELIEFKV_FREE_PAGE_H2D_FENCE", "0")
        if free_fence not in ("0", "1"):
            raise ValueError("free-page H2D fence must be 0 or 1")
        self.free_page_h2d_fence_requested = free_fence == "1"
        background_setting = os.environ.get("BELIEFKV_ENABLE_BACKGROUND_PREPARE", "1")
        retirement_setting = os.environ.get("BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM", "1")
        if background_setting not in ("0", "1") or retirement_setting not in ("0", "1"):
            raise ValueError("background prepare and child reclaim settings must be 0 or 1")
        self.enable_background_prepare = (
            self._custom_kv_management and self.enable_prepare_host and background_setting == "1"
        )
        self.enable_terminal_child_reclaim = (
            self._custom_kv_management and retirement_setting == "1"
        )
        self.idle_prepare_policy = os.environ.get("BELIEFKV_IDLE_PREPARE_POLICY", "cold")
        if self.idle_prepare_policy not in ("cold", "all"):
            raise ValueError("idle prepare policy must be cold or all")
        mirror_recycle = os.environ.get("BELIEFKV_IDLE_HOST_MIRROR_RECYCLE", "0")
        if mirror_recycle not in ("0", "1"):
            raise ValueError("idle Host mirror recycling must be 0 or 1")
        self.idle_host_mirror_recycle = mirror_recycle == "1"
        if self.idle_host_mirror_recycle and (
            self.internal_comparison_mode != "full"
            or not self.enable_background_prepare or self.idle_prepare_policy != "cold"
        ):
            raise ValueError("idle Host mirror recycling requires Full cold background prepare")
        self._idle_host_mirror_recycler = (
            IdleHostMirrorRecycle(self) if self.idle_host_mirror_recycle else None
        )
        self._idle_backup_queue_scope = False
        self._idle_prepare_direct_nodes = set()
        self.idle_prepare_io_backend = os.environ.get(
            "BELIEFKV_IDLE_PREPARE_IO_BACKEND", "native",
        )
        if self.idle_prepare_io_backend not in ("native", "direct"):
            raise ValueError("idle prepare backend must be native or direct")
        self._idle_backup_queued_requests: dict[PrefillCandidateKey, tuple[int, object]] | None = None
        sampling_setting = os.environ.get("BELIEFKV_H2D_OPPORTUNITY_SAMPLING", "0")
        if sampling_setting not in ("0", "1"):
            raise ValueError("H2D opportunity sampling setting must be 0 or 1")
        self.enable_h2d_opportunity_sampling = sampling_setting == "1"
        self.join_prefetch_trigger = os.environ.get(
            "BELIEFKV_JOIN_PREFETCH_TRIGGER", "observed_eos",
        )
        if self.join_prefetch_trigger not in (
            "observed_eos", "report_service_or_eos", "selective_report_or_eos",
        ):
            raise ValueError("unsupported JOIN prefetch trigger")
        self._selective_join_policy = SelectiveJoinPrefetchPolicy(
            min_full_bytes=int(os.environ.get(
                "BELIEFKV_JOIN_PREDICTION_MIN_FULL_BYTES", str(512 * 1024 ** 2),
            )),
            interval_ms=float(os.environ.get("BELIEFKV_JOIN_PREDICTION_INTERVAL_MS", "200")),
            max_candidates=int(os.environ.get("BELIEFKV_JOIN_PREDICTION_MAX_CANDIDATES", "4")),
        )
        self._join_eos_arrival_pending: OrderedDict[
            str, tuple[str, PrefillCandidateKey, PrefillCandidateKey, float]
        ] = OrderedDict()
        self._join_eos_arrival_samples: deque[float] = deque(maxlen=64)
        self._join_eos_arrival_window_ms = self.eos_protocol_window_ms
        self._online_tool_timing = (
            OnlineWorkloadToolTiming()
            if os.environ.get("BELIEFKV_ONLINE_TOOL_TIMING", "0") == "1" else None
        )
        prior_path = os.environ.get("BELIEFKV_TOOL_TIMING_PRIOR", "")
        prior_sha = os.environ.get("BELIEFKV_TOOL_TIMING_PRIOR_SHA256", "")
        self.tool_timing_prior_sha256 = prior_sha or None
        if bool(prior_path) != bool(prior_sha):
            raise ValueError("tool timing prior requires a path and SHA256")
        if prior_path:
            if self._online_tool_timing is None:
                raise ValueError("tool timing prior requires online timing adaptation")
            from beliefkv.predictor.tool_timing_prior import ToolTimingPrior

            self._online_tool_timing.prior = ToolTimingPrior.load(prior_path, prior_sha)
            self.predictor_sha256 = hashlib.sha256(
                ("hybrid_tool_prior_v1:" + prior_sha).encode(),
            ).hexdigest()
        self._online_tool_queries: dict[
            PrefillCandidateKey, tuple[float, tuple | None, float]
        ] = {}
        self._report_work = OnlineReportWork()
        self._report_sample_next_ms = 0.
        self._report_candidate_next_ms = 0.
        self._borrowable_parent_headroom = os.environ.get(
            "BELIEFKV_BORROWABLE_PARENT_HEADROOM", "0",
        ) == "1"
        self._critical_parent_demand: tuple[PrefillCandidateKey, int, int, float] | None = None
        self._cold_full_backups: dict[int, tuple[int | float, str]] = {}
        self._spawn_full_prepares: OrderedDict[str, PrefillCandidateKey] = OrderedDict()
        self._subagent_retention = (
            DormantSubagentRetention.from_environment() if self._custom_kv_management else None
        )
        self._spawn_prepare_next_ms = 0.
        self._idle_prepare_next_ms = 0.
        self._critical_reclaim_next_ms = 0.
        self._semantic_worker = None
        self._semantic_score_threshold = .5
        self._eos_prefetch_keys: dict[str, PrefillCandidateKey] = {}
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
        self._child_report_notices: dict[str, _ChildReportNotice] = {}
        self._decoded_tool_requests: set[str] = set()
        self._decoded_scan_positions: dict[str, int] = {}
        self._tool_open_token_ids: frozenset[int] = frozenset()
        self._reasoning_open_token_ids: frozenset[int] = frozenset()
        self._reasoning_close_token_ids: frozenset[int] = frozenset()
        self._native_plain_output_template = False
        if model_path:
            config = Path(model_path) / "tokenizer_config.json"
            if config.is_file():
                tokenizer_config = json.loads(config.read_text())
                tokens = tokenizer_config.get("added_tokens_decoder", {})
                template = tokenizer_config.get("chat_template")
                self._native_plain_output_template = bool(
                    isinstance(template, str) and template
                    and "<think>" not in template and "</think>" not in template
                )
                # Either boundary is tool-turn evidence; the model may omit
                # the dedicated opener while still emitting the closer.
                self._tool_open_token_ids = frozenset(
                    int(token_id) for token_id, token in tokens.items()
                    if isinstance(token, dict) and token.get("content") in (
                        "<tool_call>", "</tool_call>", "<|tool_call|>",
                    )
                )
                self._reasoning_close_token_ids = frozenset(
                    int(token_id) for token_id, token in tokens.items()
                    if isinstance(token, dict) and token.get("content") == "</think>"
                )
                self._reasoning_open_token_ids = frozenset(
                    int(token_id) for token_id, token in tokens.items()
                    if isinstance(token, dict) and token.get("content") == "<think>"
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
        self._restore_tiers_supported = False
        self._residency_pool_bytes: tuple[int, int] | None = None
        self._residency_request_counts: tuple[int, int, int | None] | None = None
        self._context_tokens: dict[str, tuple[int, int, int, bool]] = {}
        self._boundary_history: dict[str, deque[str]] = {}
        self._tool_metadata: dict[
            str, tuple[str, str, str, float | None, str, float | None, int]
        ] = {}
        self._tool_metadata_by_run: dict[str, dict[str, tuple]] = {}
        self._tool_ticket: _ToolPrefetchTicket | None = None
        self._tool_inflight_tickets: dict[PrefillCandidateKey, _ToolPrefetchTicket] = {}
        self._tool_prefetch_budget: Counter[PrefillCandidateKey] = Counter()
        self._wait_prefetch_records: OrderedDict[tuple, tuple[float, tuple]] = OrderedDict()
        self._wait_capacity_probe_next_ms = 0.
        self._tool_timing_only = False
        self._tool_refresh_next_ms = 0.
        self._tool_prepare_next_ms = 0.
        self._tool_prepare_cursor = 0
        self._tool_prefetch_next_ms = 0.
        self._tool_last_queries: dict[PrefillCandidateKey, tuple[float, float]] = {}
        self._tool_opportunity_cache: dict[PrefillCandidateKey, tuple[float, object]] = {}
        self._tool_survival_probes: dict[str, tuple[NativeToolWaitHint, float]] = {}
        self._active_tool_census_version: int | None = None
        self._active_tool_census: Counter[str | None] = Counter()
        self._active_tool_census_invocations: dict[str, str | None] = {}
        tool_setting = os.environ.get("BELIEFKV_ENABLE_TOOL_PREFETCH", "0")
        if tool_setting not in ("0", "1"):
            raise ValueError("tool prefetch setting must be 0 or 1")
        tool_prefetch_requested = enable_tool_prefetch or tool_setting == "1"
        self.tool_restore_mode = os.environ.get("BELIEFKV_TOOL_RESTORE_MODE", "speculative")
        if self.tool_restore_mode not in ("speculative", "execution"):
            raise ValueError("tool restore mode must be speculative or execution")
        frontier_setting = os.environ.get("BELIEFKV_ENABLE_FRONTIER_RESTORE", "0")
        if frontier_setting not in ("0", "1"):
            raise ValueError("execution frontier restore setting must be 0 or 1")
        self.enable_frontier_restore = (
            self._custom_kv_management and frontier_setting == "1"
        )
        frontier_reclaim = os.environ.get("BELIEFKV_FRONTIER_COLD_RECLAIM", "0")
        if frontier_reclaim not in ("0", "1"):
            raise ValueError("frontier cold reclaim setting must be 0 or 1")
        self.enable_frontier_cold_reclaim = (
            self.enable_frontier_restore and frontier_reclaim == "1"
        )
        self.frontier_cold_reclaim_policy = os.environ.get(
            "BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY", "waiting",
        )
        if self.frontier_cold_reclaim_policy not in ("waiting", "selected_queue"):
            raise ValueError("frontier cold reclaim policy must be waiting or selected_queue")
        self._frontier_reclaim_after_ms = 0.
        self.enable_tool_prefetch = (
            tool_prefetch_requested and self.tool_restore_mode == "speculative"
        )
        self.restore_tiers = os.environ.get("BELIEFKV_RESTORE_TIERS", "complete")
        if self.restore_tiers not in ("complete", "dependency"):
            raise ValueError("restore tiers must be complete or dependency")
        self._frontier_probe_after_ms: dict[PrefillCandidateKey, float] = {}
        self._frontier_restore_consumer: _ExecutionHandoffTicket | None = None
        self.enable_execution_handoff = os.environ.get(
            "BELIEFKV_ENABLE_EXECUTION_HANDOFF",
            "1" if (
                enable_admission_prefetch or enable_final_stage_prefetch
                or self.enable_tool_prefetch
            ) and not enable_confirmed_join_canary else "0",
        ) == "1"
        self._execution_handoff: _ExecutionHandoffTicket | None = None
        self._execution_handoff_next_ms = 0.
        self._execution_handoff_attempted: set[PrefillCandidateKey] = set()
        self._execution_handoff_capacity_blocked: set[PrefillCandidateKey] = set()
        self._reentry_observations: dict[PrefillCandidateKey, tuple[float, dict | None]] = {}
        self._terminal_child_reclaims: OrderedDict[tuple, _TerminalChildReclaim] = OrderedDict()
        self._terminal_root_contexts: OrderedDict[tuple[str, str], _TerminalChildReclaim] = OrderedDict()
        self._terminal_child_reclaim_next_ms = 0.
        self._parent_capacity_handoffs: dict[str, _ParentCapacityHandoff] = {}
        self._parent_handoff_priority_promoted: str | None = None
        self._residency_scan_cursor = 0
        self._prefill_residency_scan: tuple | None = None
        self._prefill_causal_cache: tuple | None = None
        self._prefill_cycle_active = False
        self.prefetch_issued_callback = None
        self.prefetch_discarded_callback = None
        self._prefetch_first_services: dict[str, tuple[float, str]] = {}
        tool_timing_artifact_path = (
            tool_timing_artifact_path or os.environ.get("BELIEFKV_TOOL_TIMING_ARTIFACT")
        )
        tool_timing_sha256 = (
            tool_timing_sha256 or os.environ.get("BELIEFKV_TOOL_TIMING_SHA256")
        )
        if bool(tool_timing_artifact_path) != bool(tool_timing_sha256):
            raise ValueError("tool event predictor requires path and SHA256")
        if tool_timing_artifact_path:
            if prior_path:
                raise ValueError("legacy tool artifact and domain timing prior are exclusive")
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
        if self._tool_timing_only and self._online_tool_timing is None:
            self._model_worker = NativePredictorWorker(*self._tool_model_arguments)
        if not self._custom_kv_management and (
            enable_local_predictor or enable_admission_prefetch
            or enable_final_stage_prefetch or enable_confirmed_join_canary
            or self.enable_prepare_host or tool_prefetch_requested
            or self.enable_execution_handoff or self.enable_frontier_restore or self._borrowable_parent_headroom
            or self._tool_timing_only or self._online_tool_timing is not None
        ):
            raise ValueError("scheduling-only comparison cannot enable custom KV management")
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
        self._continuing_joins: set[str] = set()
        self.frontier = CausalFrontierScheduler(self.graph)
        from beliefkv.policy.workflow_completion import WorkflowCompletionOrder

        self._completion_order = (
            WorkflowCompletionOrder(self.graph)
            if self.prefill_scheduling_policy == "workflow_completion" else None
        )
        self.visible: dict[str, PrefillCandidateKey] = {}
        self.context_sessions: dict[str, PrefillCandidateKey] = {}
        self._context_sessions_revision = 0
        self._cold_full_owner_index_source = None
        self._cold_full_owner_index_revision = -1
        self._cold_full_owner_index: dict[str, tuple[PrefillCandidateKey, ...]] = {}
        self._wait_context_cache = None
        self._join_parent_cache = None
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
        self._hotpath_timing = HotpathTiming(enabled=bool(
            self._opportunity_writer is not None
            and os.environ.get("BELIEFKV_PROFILE_SHARED_PATH", "0") == "1"
        ))
        self._opportunity_next_ms = 0.0
        self._opportunity_cursor = 0
        paper_policy = os.environ.get("BELIEFKV_PAPER_BASELINE", "")
        self._paper_baseline = None
        self._agent_admission = None
        self._full_prefix_migration = None
        agent_policy = os.environ.get("BELIEFKV_AGENT_ADMISSION", "off")
        if agent_policy not in (
            "off", "working_set", "thunderagent", "thunderagent_retained",
            "thunderagent_workflow", "thunderagent_reference",
        ):
            raise ValueError("unsupported declared agent admission policy")
        if agent_policy in (
            "thunderagent_retained", "thunderagent_workflow", "thunderagent_reference",
        ) and (
            self.enable_frontier_restore or self.enable_execution_handoff
        ):
            raise ValueError("retained agent admission uses native ordinary demand recovery")
        if (
            agent_policy == "thunderagent_workflow"
            and self.prefill_scheduling_policy != "join_priority"
        ):
            raise ValueError("workflow agent admission requires declared JOIN priority ordering")
        self.enable_agent_working_set = agent_policy != "off"
        join_free = os.environ.get("BELIEFKV_AGENT_JOIN_FREE_ADMISSION", "0")
        if join_free not in ("0", "1") or (
            join_free == "1" and agent_policy != "thunderagent_reference"
        ):
            raise ValueError("free-capacity JOIN admission requires the reference Full core")
        self.agent_join_free_admission = join_free == "1"
        continuation_free = os.environ.get("BELIEFKV_AGENT_FREE_CONTINUATION_ADMISSION", "0")
        if continuation_free not in ("0", "1") or (
            continuation_free == "1" and agent_policy != "thunderagent_reference"
        ):
            raise ValueError("free continuation admission requires the reference Full core")
        self.agent_free_continuation_admission = continuation_free == "1"
        sibling_setting = os.environ.get("BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN", "0")
        if sibling_setting not in ("0", "1") or (
            sibling_setting == "1"
            and agent_policy not in ("thunderagent_retained", "thunderagent_reference")
        ):
            raise ValueError("JOIN sibling admission requires retained or reference Full eligibility")
        self.coadmit_join_children = sibling_setting == "1"
        blocked_lending = os.environ.get("BELIEFKV_AGENT_BLOCKED_LENDING", "idle")
        if blocked_lending not in ("idle", "backed_demand", "backed_demand_recall") or (
            blocked_lending != "idle" and agent_policy != "thunderagent_retained"
        ):
            raise ValueError("backed blocked lending requires retained agent admission")
        self.agent_blocked_lending = blocked_lending
        join_admission = os.environ.get("BELIEFKV_AGENT_JOIN_DEPENDENCY_ADMISSION", "0")
        if join_admission not in ("0", "1") or (
            join_admission == "1" and agent_policy != "thunderagent_retained"
        ):
            raise ValueError("JOIN-dependent admission requires retained agent eligibility")
        self.agent_join_dependency_admission = join_admission == "1"
        budget_proof = os.environ.get("BELIEFKV_AGENT_BLOCKED_BUDGET_PROOF", "predicted_pressure")
        if budget_proof not in ("predicted_pressure", "observed_blocked", "observed_demand") or (
            budget_proof != "predicted_pressure" and blocked_lending == "idle"
        ):
            raise ValueError("observed blocked budget proof requires backed lending")
        self.agent_blocked_budget_proof = budget_proof
        lending_selection = os.environ.get("BELIEFKV_AGENT_LENDING_SELECTION", "largest")
        if lending_selection not in ("largest", "fit") or (
            lending_selection != "largest" and (
                agent_policy != "thunderagent_retained" or blocked_lending == "idle"
                or budget_proof != "observed_demand"
            )
        ):
            raise ValueError("fitting lending requires observed-demand retained admission")
        self.agent_lending_selection = lending_selection
        pressure_policy = os.environ.get("BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY", "reference")
        if pressure_policy not in ("reference", "projected_release") or (
            pressure_policy != "reference" and agent_policy != "thunderagent_retained"
        ):
            raise ValueError("projected pressure release requires retained agent eligibility")
        self.agent_pressure_pause_policy = pressure_policy
        recall_selection = os.environ.get("BELIEFKV_AGENT_RECALL_SELECTION", "largest")
        if recall_selection not in ("largest", "cold_fit") or (
            recall_selection != "largest" and blocked_lending != "backed_demand_recall"
        ):
            raise ValueError("recall selection requires recorded blocked lending")
        self.agent_recall_selection = recall_selection
        tool_return = os.environ.get("BELIEFKV_AGENT_TOOL_LOAN_RETURN", "0")
        if tool_return not in ("0", "1") or (
            tool_return == "1" and blocked_lending != "backed_demand_recall"
        ):
            raise ValueError("tool loan return requires recorded blocked lending")
        self.agent_tool_loan_return = tool_return == "1"
        entry_setting = os.environ.get("BELIEFKV_AGENT_WORKFLOW_ENTRY_MIN_READY", "0")
        if (
            not entry_setting.isascii() or not entry_setting.isdecimal()
            or not 0 <= int(entry_setting) <= 256
            or int(entry_setting) and agent_policy != "thunderagent_retained"
        ):
            raise ValueError("workflow entry requires retained agent admission and a bounded count")
        self.agent_workflow_entry_min_ready = int(entry_setting)
        if self.enable_agent_working_set:
            if (
                paper_policy or self.internal_comparison_mode != "full"
                or self.tool_restore_mode != "execution" and agent_policy not in (
                    "thunderagent_retained", "thunderagent_workflow", "thunderagent_reference",
                )
            ):
                raise ValueError("agent working-set admission requires Full execution recovery")
            if agent_policy == "thunderagent_reference":
                from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission

                if self.agent_free_continuation_admission:
                    from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission

                    self._agent_admission = ReferenceFreeContinuationAdmission(self)
                else:
                    self._agent_admission = ReferenceThunderAdmission(self)
            else:
                from beliefkv.runtime.agent_working_set import AgentAdmission

                self._agent_admission = AgentAdmission(
                    self, strategy=agent_policy, coadmit_join_children=self.coadmit_join_children,
                    blocked_lending=blocked_lending,
                    workflow_entry_min_ready=self.agent_workflow_entry_min_ready,
                    tool_loan_return=self.agent_tool_loan_return,
                    recall_selection=self.agent_recall_selection,
                    join_dependency_admission=self.agent_join_dependency_admission,
                    blocked_budget_proof=self.agent_blocked_budget_proof,
                    pressure_pause_policy=self.agent_pressure_pause_policy,
                    lending_selection=self.agent_lending_selection,
                )
        if paper_policy:
            from beliefkv.runtime.paper_baseline import create_adapter

            self._paper_baseline = create_adapter(self, paper_policy)
        prefix_setting = os.environ.get("BELIEFKV_SESSIONLESS_PREFIX_MIGRATION", "0")
        if prefix_setting not in ("0", "1"):
            raise ValueError("sessionless prefix migration must be 0 or 1")
        if prefix_setting == "1":
            if (
                self._agent_admission is None or not self._custom_kv_management or paper_policy
                or agent_policy == "thunderagent_reference"
            ):
                raise ValueError("sessionless prefix migration requires Full agent admission")
            from beliefkv.runtime.full_prefix_migration import FullPrefixMigration

            self._full_prefix_migration = FullPrefixMigration(self)
        if self.tool_offload_before_admission and (
            getattr(self._agent_admission, "strategy", None) != "thunderagent_reference"
        ):
            raise ValueError("tool offload before admission requires reference agent eligibility")
        self.event_server = (
            RuntimeEventDatagramServer(
                event_socket_path, self.on_events, timing=self._hotpath_timing,
            )
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
        self._tool_prefetch_next_ms = 0.
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
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.close()
        if (agents := getattr(self, "_agent_admission", None)) is not None:
            agents.close()
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            paper.close()
        if self._tool_residency_grace is not None:
            self._release_tool_residency_grace("shutdown")
        self._frontier_restore_consumer = None
        for command in tuple(self._prefetch_service_leases):
            self._release_prefetch_service_lease(command, "shutdown")
        self._prefetch_admission_hints.clear()
        self._discard_prefetch_tracking("shutdown")
        self._prefill_residency_scan = None
        self._prefill_causal_cache = None
        self._prefill_cycle_active = False
        self._capacity_waiting_queue = ()
        self._residency_ordering_hints.clear()
        self.counts["residency_ordering_hint_feedback_unserved"] += len(
            self._residency_hint_admissions,
        )
        self._residency_hint_admissions.clear()
        self._discard_join_ticket("shutdown")
        self._confirmed_join_tickets.clear()
        self._empty_join_reentry_origins.clear()
        self._join_inflight_tickets.clear()
        self._tool_inflight_tickets.clear()
        self._final_stages.clear()
        self._final_request_stages.clear()
        self._join_eos_arrival_pending.clear()
        self._terminal_cache_watches.clear()
        self._terminal_child_reclaims.clear()
        self._terminal_root_contexts.clear()
        self._parent_capacity_handoffs.clear()
        self._workflow_attained_output_tokens.clear()
        self._parent_pressure_groups.clear()
        self._parent_pressure_group_source = None
        self._parent_pressure_positions.clear()
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

    def _discard_prefetch_tracking(
        self, reason: str, commands: Sequence[str] | None = None,
    ) -> None:
        for command in tuple(self._prefetch_steps) if commands is None else commands:
            pending = self._prefetch_steps.pop(command, None)
            self._prefetch_issue_budgets.pop(command, None)
            self._prefetch_issue_times.pop(command, None)
            self._prefetch_first_services.pop(command, None)
            if pending is not None and self.prefetch_discarded_callback is not None:
                self.prefetch_discarded_callback(command, reason)

    def _discard_join_ticket(
        self, reason: str, *, ticket: _JoinPrefetchTicket | None = None,
    ) -> None:
        ticket = ticket if ticket is not None else self._join_ticket
        if (
            ticket is not None
            and ticket.phase == "confirmed"
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
        if ticket is not None and self._confirmed_join_tickets.get(ticket.join_id) is ticket:
            del self._confirmed_join_tickets[ticket.join_id]
        if ticket is not None and self._join_inflight_tickets.get(ticket.join_id) is ticket:
            del self._join_inflight_tickets[ticket.join_id]
        if self._join_ticket is ticket:
            self._join_ticket = None

    def _tool_grace_yields_to_join(self) -> bool:
        return bool(
            self._join_priority_pending or self._eos_prefetch_keys
            or self._join_ticket is not None or self._confirmed_join_tickets
            or self._join_inflight_tickets or self._critical_parent_demand is not None
        )

    def _release_tool_residency_grace(self, reason: str) -> PrefillCandidateKey | None:
        key = self._tool_residency_grace.release(reason)
        if key is not None:
            self._residency_ordering_hints.pop(key, None)
            current = self.context_sessions.get(key.context_id)
            if current is not None:
                self._residency_ordering_hints.pop(current, None)
        return key

    def _refresh_tool_residency_grace(self) -> None:
        grace = self._tool_residency_grace
        key = grace.key
        if key is None:
            return
        if self.physical_disabled or self._tool_grace_yields_to_join():
            self._release_tool_residency_grace("critical_or_disabled")
            return
        allocator = getattr(self._native_cache, "token_to_kv_pool_allocator", None)
        available = getattr(allocator, "available_size", None)
        page_size = getattr(allocator, "page_size", None)
        if (
            callable(available) and type(page_size) is int
            and available() < page_size * max(
                1, len(getattr(self._native_running_batch, "reqs", ())),
            )
        ):
            self._release_tool_residency_grace("decode_reserve")
            return
        current = self.context_sessions.get(key.context_id)
        context = self.graph.contexts.get(key.context_id)
        invocation = self.graph.invocations.get(key.invocation_id)
        workflow = self.graph.workflows.get(key.root_workflow_id)
        valid = bool(
            current is not None
            and (current.root_workflow_id, current.invocation_id,
                 current.session_id, current.session_generation)
            == (key.root_workflow_id, key.invocation_id,
                key.session_id, key.session_generation)
            and current.context_epoch in (key.context_epoch, key.context_epoch + 1)
            and context is not None
            and context.epoch in (key.context_epoch, key.context_epoch + 1)
            and invocation is not None and not invocation.state.terminal
            and invocation.workflow_id == key.root_workflow_id
            and invocation.context_id == key.context_id
            and invocation.state is not InvocationState.WAIT_CHILD
            and workflow is not None and workflow.end_ts_ms is None
            and self._tool_continuity.pending_reentry(key)
        )
        if not valid:
            self._release_tool_residency_grace("authority_changed")
        elif time.monotonic() >= grace.pin.expires_at:
            self._release_tool_residency_grace("expired")

    def attach_native_cache(self, cache: object) -> None:
        if self._tool_residency_grace is not None:
            self._release_tool_residency_grace("cache_changed")
        self._native_cache = cache
        if self.layer_handoff_enabled and not callable(
            getattr(cache, "begin_beliefkv_layer_handoff", None)
        ):
            raise ValueError("layer handoff requires the dedicated native engine")
        supports = getattr(cache, "supports_beliefkv_restore_tiers", None)
        self._restore_tiers_supported = (
            self.restore_tiers == "dependency" and callable(supports) and supports() is True
        )
        incremental = getattr(cache, "enable_beliefkv_incremental_prepare", None)
        if self._custom_kv_management and self.enable_prepare_host and not self.physical_disabled and callable(incremental):
            if incremental() is True:
                self.counts["incremental_full_tracking_enabled"] += 1
                cache.beliefkv_idle_prepare_policy = self.idle_prepare_policy
                cache.beliefkv_backup_full_owner_validator = self._live_backup_full_owners
        self._residency_pool_bytes = None
        try:
            entries = cache.host_pool_group.entry_map
            full_unit = entries["kv"].host_pool.size_per_token
            mamba_unit = entries["mamba"].host_pool.size_per_token if "mamba" in entries else 0
            if (
                type(full_unit) is int and full_unit > 0
                and type(mamba_unit) is int and mamba_unit >= 0
            ):
                self._residency_pool_bytes = full_unit, mamba_unit
        except (AttributeError, KeyError, TypeError):
            pass
        if self._custom_kv_management and callable(getattr(cache, "_evict_backed_join_parent", None)):
            cache.beliefkv_join_pressure_validator = self._live_parent_pressure_node
            cache.beliefkv_join_pressure_parked = self._on_parent_pressure_parked
            cache.beliefkv_cold_full_owner_validator = self._live_cold_full_owners
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            paper.attach(cache)
        if self._agent_admission is not None:
            self._agent_admission.attach(cache)
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.attach(cache)

    def attach_native_scheduler(self, scheduler: object) -> None:
        paper = getattr(self, "_paper_baseline", None)
        bind = getattr(paper, "bind_scheduler", None)
        if bind is not None:
            bind(scheduler)

    def _next_physical_command_id(self, kind: str) -> str:
        namespace = self._physical_command_namespace
        if namespace is None:
            namespace = self._physical_command_namespace = uuid4().hex
        # PID separates forked copies; the sequence outlives bounded ACK history.
        return (
            f"beliefkv-{kind}-{namespace}-{os.getpid():x}"
            f"-{next(self._physical_command_sequence):x}"
        )

    def can_prefetch_during_overlap(self) -> bool:
        supports = getattr(self._native_cache, "supports_beliefkv_overlap_prefetch", None)
        return bool(
            not self.enable_confirmed_join_canary
            and callable(supports) and supports()
        )

    def can_defer_waiting_migrations(self) -> bool:
        return not self.enable_execution_handoff and self.can_prefetch_during_overlap()

    @timed_runtime("post_launch_physical")
    def dispatch_after_forward(
        self, waiting_queue: Sequence[object] = (), *, running_batch: object = None,
    ) -> None:
        """Plan immutable waiting contexts after native forward submission."""
        self._capacity_waiting_queue = waiting_queue
        if running_batch is not None:
            self._native_running_batch = running_batch
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.dispatch()
        if self._subagent_retention is not None:
            self._subagent_retention.dispatch(self)
        critical_before = self._critical_parent_demand
        if self.enable_prepare_host and critical_before is not None:
            # ACKed donor capacity is reusable before this iteration's H2D plan.
            self._reclaim_critical_parent_headroom()
        if (
            self._join_ticket is not None or self._confirmed_join_tickets
            or self._join_inflight_tickets
            or (
                (self.enable_final_stage_prefetch or self.enable_admission_prefetch)
                and (
                    self._eos_prefetch_keys or (
                        self._report_prediction_enabled
                        and self._final_request_stages
                        and time.monotonic() * 1000. >= self._report_candidate_next_ms
                    )
                )
            )
        ):
            self.dispatch_join_prefetch()
        if (
            self.enable_prepare_host and self._critical_parent_demand is not None
            and self._critical_parent_demand != critical_before
        ):
            if self._reclaim_critical_parent_headroom():
                # EOS may leave only milliseconds before RETURN. Consume
                # newly earned capacity before tool forecasting or yielding.
                self.counts["critical_capacity_immediate_join_replans"] += 1
                self.dispatch_join_prefetch()
        if self._wait_prediction_maintenance_deferred:
            self._wait_prediction_maintenance_deferred = False
            self._maintain_wait_predictions(time.monotonic() * 1000.)
        if self.enable_frontier_restore:
            self.dispatch_frontier_restore(waiting_queue, running_batch=running_batch)
        if self._tool_ticket is not None or self._tool_inflight_tickets or (
            self.enable_tool_prefetch and self.tool_wait_hints
        ):
            if not (
                self.enable_tool_prefetch and not self.physical_disabled
                and self._tool_ticket is None
                and time.monotonic() * 1000. < self._tool_prefetch_next_ms
            ):
                self.dispatch_tool_prefetch()
        # Earlier handlers can cross a deadline or rearm another source.
        # Read each existing deadline at its original dispatch position.
        if self.enable_prepare_host:
            if self._spawn_full_prepares and time.monotonic() * 1000. >= self._spawn_prepare_next_ms:
                self.dispatch_spawn_prepare()
            publish_pressure = False
            if time.monotonic() * 1000. >= self._join_prepare_next_ms:
                publish_pressure = self.dispatch_join_prepare(
                    waiting_queue, running_batch=running_batch,
                    _defer_pressure_publication=True,
                ) is True
            if time.monotonic() * 1000. >= self._tool_prepare_next_ms:
                tool_publication = self.dispatch_tool_prepare(
                    waiting_queue, running_batch=running_batch,
                    _defer_pressure_publication=True,
                )
                publish_pressure = tool_publication is True or publish_pressure
            if publish_pressure:
                # These D2H selectors do not reclaim device capacity. Publish
                # their final live candidates once, before background backup.
                self._publish_parent_pressure_candidates()
            if time.monotonic() * 1000. >= self._idle_prepare_next_ms:
                self.dispatch_idle_prepare()

    def predictor_fileno(self) -> int | None:
        if self._semantic_worker is not None:
            return self._semantic_worker.fileno()
        if self._model_worker is None or self._model_worker.disabled:
            return None
        return self._model_worker.fileno()

    def predictor_filenos(self) -> tuple[int, ...]:
        if self._semantic_worker is None and self._model_worker is None:
            return ()
        return tuple(dict.fromkeys(
            fd for worker in (self._semantic_worker, self._model_worker)
            if worker is not None and not worker.disabled
            and (fd := worker.fileno()) is not None
        ))

    def idle_poll_timeout_ms(self) -> int:
        if self._full_prefix_migration is not None:
            if self._full_prefix_migration.transfers is not None and (
                self._full_prefix_migration.transfers.receipts.pending_count
            ):
                return 10
            if self._full_prefix_migration.contexts:
                return 200
        if self.physical_ledger.pending_count:
            return 10
        if self._spawn_full_prepares:
            return 50
        if self._terminal_child_reclaims:
            return 50
        if self._subagent_retention is not None and self._subagent_retention.work:
            return 200
        if self._prefetch_service_leases:
            return 100
        if self._tool_timing_only and any(
            item.state is InvocationState.WAIT_TOOL for item in self.graph.invocations.values()
        ):
            return 100
        return 1000

    @timed_runtime("event_apply")
    def on_events(self, events: tuple[RuntimeEvent, ...]) -> None:
        # A provisional callback can arrive behind a confirmed RETURN/epoch
        # advance. It is advisory, so a stale one must not discard the RCCG.
        filtered = []
        submitted_origins = None
        for event in events:
            if is_unobserved_join_event(event):
                self.counts["unobserved_join_event_ignored"] += 1
                continue
            if self.internal_comparison_mode == "join_priority_only" and (
                event.attributes.get(SEMANTIC_TEXT) is True
                or event.kind is RuntimeEventKind.STRUCTURED_ACTION
                and event.attributes.get(CHILD_COMPLETION_INTENT) is True
            ):
                continue
            if (
                (self.enable_admission_prefetch or self.enable_final_stage_prefetch)
                and self._continuing_joins and event.kind is RuntimeEventKind.LLM_SUBMIT
                and not event.attributes.get("runtime_internal")
            ):
                origin = self.context_sessions.get(event.context_id)
                parent = self.graph.invocations.get(event.invocation_id)
                tokens = self._context_tokens.get(event.context_id)
                if (
                    origin is not None and parent is not None
                    and parent.join_id in self._continuing_joins
                    and (
                        parent.state is InvocationState.WAIT_JOIN
                        or parent.join_id in self._confirmed_join_tickets
                        or parent.state is InvocationState.READY
                        and self._empty_join_reentry_origins.get(parent.join_id) == origin
                    )
                    and origin.invocation_id == event.invocation_id
                    and origin.root_workflow_id == event.workflow_id
                    and event.context_epoch == origin.context_epoch + 1
                    and tokens is not None and tokens[0] == origin.context_epoch
                    and type(tokens[1]) is int and tokens[1] > 1
                ):
                    if submitted_origins is None:
                        submitted_origins = {}
                    submitted_origins[event.event_id] = (
                        origin, tokens[1] - 1,
                        self._empty_join_reentry_origins.get(parent.join_id) == origin,
                    )
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
        if submitted_origins is not None:
            advanced_invocations = {
                event.invocation_id for event in events
                if event.kind in (
                    RuntimeEventKind.CONTEXT_COMPACT, RuntimeEventKind.CONTEXT_ADVANCE,
                )
            }
            submitted_origins = {
                event_id: origin for event_id, origin in submitted_origins.items()
                if origin[0].invocation_id not in advanced_invocations
            } or None
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
        graph_version = self.graph.graph_version
        semantic_revision = self.semantic_revision
        sessions_revision = self._context_sessions_revision
        rank_dependencies_unchanged = True
        changed_rank_invocations = set()
        for event in events:
            if event.kind in (
                RuntimeEventKind.STRUCTURED_ACTION,
                RuntimeEventKind.TOOL_WAIT_OBSERVATION,
                RuntimeEventKind.CALL_CENSORED,
                RuntimeEventKind.CONTEXT_ADVANCE,
                RuntimeEventKind.CONTEXT_COMPACT,
            ):
                continue
            if event.kind not in (
                RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.LLM_RESULT,
                RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END,
            ):
                rank_dependencies_unchanged = False
                break
            invocation = self.graph.invocations.get(event.invocation_id)
            if (
                invocation is None or invocation.state is InvocationState.WAIT_CHILD
                or event.kind is RuntimeEventKind.LLM_RESULT
                and bool(event.attributes.get("terminal", False))
            ):
                rank_dependencies_unchanged = False
                break
            changed_rank_invocations.add(event.invocation_id)
        try:
            if self._hotpath_timing.enabled:
                with self._hotpath_timing.measure("graph_apply"):
                    deltas = self.graph.apply_batch(events, atomic=False)
            else:
                deltas = self.graph.apply_batch(events, atomic=False)
        except Exception:
            # A partially applied batch cannot remain a scheduling authority.
            if self._tool_residency_grace is not None:
                self._release_tool_residency_grace("causal_mirror_discarded")
            self.graph = RuntimeCausalContextGraph(strict_timestamps=False)
            self._join_by_invocation.clear()
            self._noncontinuing_joins.clear()
            self._continuing_joins.clear()
            self.frontier = CausalFrontierScheduler(self.graph)
            if self._completion_order is not None:
                self._completion_order = type(self._completion_order)(self.graph)
            self.demand_hints.clear()
            self._last_model_signature = None
            self.context_sessions.clear()
            self._wait_context_cache = None
            self._join_parent_cache = None
            self._terminal_child_reclaims.clear()
            self._join_priority_pending.clear()
            if self._agent_admission is not None:
                self._agent_admission.close()
            if self._tool_continuity is not None:
                self._tool_continuity.clear()
            self._spawn_full_prepares.clear()
            if self._full_prefix_migration is not None:
                for identity in tuple(self._full_prefix_migration.contexts):
                    self._full_prefix_migration.forget(identity)
            if self._subagent_retention is not None:
                # Losing the causal mirror revokes every physical authority.
                self._subagent_retention.states.clear()
                self._subagent_retention.work.clear()
                self._subagent_retention.pending_resumes.clear()
            self._terminal_root_contexts.clear()
            self._parent_capacity_handoffs.clear()
            self.tool_wait_hint = None
            self.join_wait_hint = None
            self._completion_hints.clear()
            for command in tuple(self._prefetch_service_leases):
                self._release_prefetch_service_lease(command, "causal_mirror_discarded")
            self._discard_prefetch_tracking("causal_mirror_discarded")
            self._prefill_causal_cache = None
            self._final_stages.clear()
            self._final_request_stages.clear()
            self._join_eos_arrival_pending.clear()
            self._child_report_notices.clear()
            self._discard_join_ticket("causal_mirror_discarded")
            self._confirmed_join_tickets.clear()
            self._empty_join_reentry_origins.clear()
            self._join_inflight_tickets.clear()
            self._admission_lease = None
            self.shadow_candidate = None
            self._context_tokens.clear()
            self._boundary_history.clear()
            self._tool_metadata.clear()
            self._tool_metadata_by_run.clear()
            self._online_tool_queries.clear()
            self._next_wait_refresh_ms = 0.0
            self._scan_offsets = {"tool_wait": 0, "join_wait": 0}
            self._active_tool_census_version = None
            self._active_tool_census.clear()
            self._active_tool_census_invocations.clear()
            self.semantic_revision += 1
            self.counts["causal_mirror_discarded"] += 1
            raise
        else:
            if self._tool_continuity is not None:
                for event in events:
                    self._tool_continuity.observe(event)
            if self._completion_order is not None:
                for workflow_id in {event.workflow_id for event in events}:
                    self._completion_order.invalidate(workflow_id)
            affected = self._update_active_tool_census(deltas, previous_version=graph_version)
            self._update_wait_context_cache(
                deltas, previous_version=graph_version, affected_invocations=affected,
            )
            tool_wait_refreshes: dict[str, None] | None = None
            refresh_tool_waits = self._custom_kv_management
            tool_started_keys: set[PrefillCandidateKey] | None = None
            terminal_returns = {
                event.invocation_id: event
                for event in events if event.kind is RuntimeEventKind.RETURN
            }
            confirmed_in_batch = (
                {
                    event.join_id for event in events
                    if event.kind is RuntimeEventKind.JOIN_SATISFIED
                } | {
                    join_id for invocation_id in terminal_returns
                    for join_id in self._join_by_invocation.get(invocation_id, ())
                    if (join := self.graph.joins.get(join_id)) is not None and join.satisfied
                }
            ) if submitted_origins is not None else set()
            for event in events:
                if event.kind is RuntimeEventKind.JOIN_CREATE and event.join_id:
                    join = self.graph.joins.get(event.join_id)
                    if join is not None:
                        if event.attributes.get("parent_prefix_continuation") is False:
                            self._noncontinuing_joins.add(join.join_id)
                        elif event.attributes.get("parent_prefix_continuation") is True:
                            self._continuing_joins.add(join.join_id)
                        for member in join.member_invocation_ids:
                            self._join_by_invocation.setdefault(member, set()).add(join.join_id)
                elif event.kind is RuntimeEventKind.JOIN_WAIT and event.join_id and event.invocation_id:
                    self._join_by_invocation.setdefault(event.invocation_id, set()).add(event.join_id)
            if self.enable_join_parent_priority:
                for event, delta in zip(events, deltas):
                    if event.kind is RuntimeEventKind.JOIN_WAIT and event.invocation_id:
                        self._join_priority_pending.pop(event.invocation_id, None)
                    if event.kind in (
                        RuntimeEventKind.RETURN, RuntimeEventKind.JOIN_WAIT,
                        RuntimeEventKind.JOIN_SATISFIED,
                    ) or event.kind is RuntimeEventKind.LLM_RESULT and event.attributes.get("terminal"):
                        for invocation_id in delta.awakened_invocations:
                            invocation = self.graph.invocations.get(invocation_id)
                            join = self.graph.joins.get(invocation.join_id) if invocation else None
                            if join is not None and join.satisfied and invocation_id in join.waiter_invocation_ids:
                                self._join_priority_pending[invocation_id] = join.join_id
                    for invocation_id in delta.completed_invocations:
                        self._join_priority_pending.pop(invocation_id, None)
                if any(event.kind is RuntimeEventKind.WORKFLOW_END for event in events):
                    self._join_priority_pending = {
                        invocation_id: join_id
                        for invocation_id, join_id in self._join_priority_pending.items()
                        if not self.graph.invocations[invocation_id].state.terminal
                        and self.graph.workflows[self.graph.invocations[invocation_id].workflow_id].end_ts_ms is None
                    }
            if self._tool_residency_grace is not None:
                self._refresh_tool_residency_grace()
            for hint in hints:
                if self._terminal(hint.key):
                    self.counts["terminal_prediction_ignored"] += 1
                else:
                    self.demand_hints[hint.key.request_id] = hint
                    self.counts["prediction_accepted"] += 1
            for event in events:
                if self._subagent_retention is not None:
                    if event.kind is RuntimeEventKind.INVOCATION_CREATE:
                        self._subagent_retention.activate(event, runtime=self)
                        self._subagent_retention.observe_delegation(event)
                    elif event.kind is RuntimeEventKind.LLM_SUBMIT:
                        self._subagent_retention.started(event)
                    elif event.kind is RuntimeEventKind.INVOCATION_CANCEL:
                        self._subagent_retention.cancel(event)
                    elif event.kind is RuntimeEventKind.WORKFLOW_END:
                        self._subagent_retention.end(
                            event.workflow_id,
                            labels_complete=event.attributes.get("reuse_labels_complete") is True,
                        )
                    elif event.attributes.get("subagent_context_close") is True:
                        self._subagent_retention.close_context(event)
                if event.kind in (RuntimeEventKind.SPAWN, RuntimeEventKind.CALL):
                    self._queue_spawn_prepare(event)
                elif event.kind is RuntimeEventKind.JOIN_WAIT:
                    self._queue_join_wait_prepare(event)
                if self._online_tool_timing is not None:
                    self._online_tool_timing.observe(event)
                if self._model_worker is not None and event.invocation_id is not None:
                    boundary = observed_boundary_action(event)
                    if boundary is not None:
                        history = self._boundary_history.get(event.invocation_id)
                        if history is None:
                            history = self._boundary_history[event.invocation_id] = deque(maxlen=8)
                        history.append(boundary)
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
                                self._tool_metadata_by_run.pop(event.invocation_id, None)
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
                    if event.kind is RuntimeEventKind.CONTEXT_COMPACT:
                        self._spawn_full_prepares.pop(context_id, None)
                    key = self.context_sessions.get(context_id)
                    if event.kind is RuntimeEventKind.TOOL_START and key is not None:
                        if tool_started_keys is None:
                            tool_started_keys = {key}
                        else:
                            tool_started_keys.add(key)
                    if key is not None and self._terminal(key):
                        self._track_terminal_cache(key)
                        returned = terminal_returns.get(key.invocation_id)
                        if returned is not None:
                            if self._subagent_retention is not None:
                                self._subagent_retention.remember(key, returned)
                            self._retire_terminal_child(key, returned)
                            self._remember_terminal_root(key, returned)
                        invocation = self.graph.invocations.get(key.invocation_id)
                        if invocation is not None and invocation.state.terminal:
                            self._spawn_full_prepares.pop(context_id, None)
                            self._parent_capacity_handoffs.pop(context_id, None)
                        self._tool_last_queries.pop(key, None)
                        self._online_tool_queries.pop(key, None)
                        self._tool_opportunity_cache.pop(key, None)
                        self._tool_prefetch_survival_evidence.pop(key, None)
                        del self.context_sessions[context_id]
                        self._context_session_changed(context_id)
                        self._context_tokens.pop(context_id, None)
                    if event.kind in (
                        RuntimeEventKind.TOOL_END,
                        RuntimeEventKind.RETURN,
                        RuntimeEventKind.INVOCATION_CANCEL,
                        RuntimeEventKind.CONTEXT_ADVANCE,
                    ):
                        self.tool_wait_hints.pop(context_id, None)
                        if key is not None:
                            self._tool_prefetch_survival_evidence.pop(key, None)
                        self.shadow_candidate = None
                if event.kind is RuntimeEventKind.WORKFLOW_END:
                    self._invalidate_join_eos_arrival(event)
                    self._empty_join_reentry_origins = {
                        join_id: key
                        for join_id, key in self._empty_join_reentry_origins.items()
                        if key.root_workflow_id != event.workflow_id
                    }
                    self._spawn_full_prepares = OrderedDict(
                        (context, key) for context, key in self._spawn_full_prepares.items()
                        if key.root_workflow_id != event.workflow_id
                    )
                    self._online_tool_queries = {
                        key: value for key, value in self._online_tool_queries.items()
                        if key.root_workflow_id != event.workflow_id
                    }
                    self._workflow_attained_output_tokens.pop(event.workflow_id, None)
                    self._execution_handoff_capacity_blocked = {
                        key for key in self._execution_handoff_capacity_blocked
                        if key.root_workflow_id != event.workflow_id
                    }
                    self._parent_capacity_handoffs = {
                        context: handoff
                        for context, handoff in self._parent_capacity_handoffs.items()
                        if handoff.key.root_workflow_id != event.workflow_id
                    }
                    self._terminal_child_reclaims = OrderedDict(
                        (identity, reclaim)
                        for identity, reclaim in self._terminal_child_reclaims.items()
                        if reclaim.key.root_workflow_id != event.workflow_id
                        or reclaim.invocation_scope == "workflow_root"
                    )
                    self._retire_ended_workflow_roots(event.workflow_id)
                    self._child_report_notices = {
                        child: notice for child, notice in self._child_report_notices.items()
                        if notice.workflow_id != event.workflow_id
                    }
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
                    self._tool_prefetch_survival_evidence = OrderedDict(
                        (key, evidence)
                        for key, evidence in self._tool_prefetch_survival_evidence.items()
                        if key.root_workflow_id != event.workflow_id
                    )
                    self._prepare_probe_after_ms = {
                        key: when for key, when in self._prepare_probe_after_ms.items()
                        if key.root_workflow_id != event.workflow_id
                    }
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
                    self._continuing_joins = {
                        join_id for join_id in self._continuing_joins
                        if (join := self.graph.joins.get(join_id)) is not None
                        and join.workflow_id != event.workflow_id
                    }
                    for invocation_id in (
                        set(self._boundary_history) | set(self._tool_metadata)
                        | set(self._tool_metadata_by_run)
                    ):
                        invocation = self.graph.invocations.get(invocation_id)
                        if invocation is not None and (
                            invocation.workflow_id == event.workflow_id
                        ):
                            self._boundary_history.pop(invocation_id, None)
                            self._tool_metadata.pop(invocation_id, None)
                            self._tool_metadata_by_run.pop(invocation_id, None)
                    self.context_sessions = {
                        context: key
                        for context, key in self.context_sessions.items()
                        if key.root_workflow_id != event.workflow_id
                    }
                    self._context_sessions_revision += 1
                    self._wait_context_cache = None
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
                if refresh_tool_waits and event.kind in (
                    RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END,
                ) and context_id is not None:
                    if len(events) == 1:
                        self._submit_tool_wait(context_id)
                    else:
                        if tool_wait_refreshes is None:
                            tool_wait_refreshes = {}
                        tool_wait_refreshes[context_id] = None
            if tool_wait_refreshes is not None:
                # Graph state is already final; metadata must finish the same batch.
                for context_id in tool_wait_refreshes:
                    self._submit_tool_wait(context_id)
            report_notices_in_batch: set[str] = set()
            empty_join_restores = None
            for event in events:
                if event.kind is RuntimeEventKind.STRUCTURED_ACTION:
                    if self._observe_child_report_notice(event):
                        report_notices_in_batch.add(event.invocation_id)
                    self._observe_child_completion_intent(event)
                elif event.kind is RuntimeEventKind.LLM_SUBMIT:
                    self._bind_confirmed_join_submit(
                        event,
                        origin=(submitted_origins or {}).get(event.event_id),
                        confirmed_in_batch=confirmed_in_batch,
                    )
                    self._clear_semantic_invocation(event.invocation_id)
                    self._bind_child_report_notice(event)
                    self._bind_final_request(event)
                    notice = self._child_report_notices.get(event.invocation_id)
                    if (
                        event.invocation_id in report_notices_in_batch
                        and notice is not None
                        and notice.request_id == event.attributes.get("request_id")
                    ):
                        self._resume_remaining_child_report_stage(
                            notice.join_id, resumed_after_sibling_return=False,
                        )
                    self._observe_prefetch_client_submit(event)
                elif event.kind is RuntimeEventKind.TOOL_START:
                    self._invalidate_join_eos_arrival(event)
                    self._child_report_notices.pop(event.invocation_id, None)
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
                    if event.kind in (
                        RuntimeEventKind.JOIN_TIMEOUT, RuntimeEventKind.INVOCATION_CANCEL,
                    ):
                        self._invalidate_join_eos_arrival(event)
                    if event.invocation_id is not None:
                        self._child_report_notices.pop(event.invocation_id, None)
                        self._clear_semantic_invocation(event.invocation_id)
                    if empty_join_restores is None:
                        empty_join_restores = set()
                    self._advance_join_ticket(event, empty_restores=empty_join_restores)
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
                elif event.kind in (
                    RuntimeEventKind.CONTEXT_COMPACT, RuntimeEventKind.CONTEXT_ADVANCE,
                ):
                    self._invalidate_join_eos_arrival(event)
                    invocation = self.graph.invocations.get(event.invocation_id)
                    context_id = event.context_id or (
                        invocation.context_id if invocation is not None else None
                    )
                    if context_id is not None:
                        self.physical_ledger.invalidate_context(context_id)
                    self._advance_join_ticket(event)
                    self._child_report_notices.pop(event.invocation_id, None)
                    self._parent_capacity_handoffs.pop(event.context_id, None)
            returned_joins = {
                join_id for event in events if event.kind is RuntimeEventKind.RETURN
                for join_id in self._join_by_invocation.get(event.invocation_id, ())
            }
            for join_id in sorted(returned_joins):
                self._resume_remaining_child_report_stage(join_id)
            if tool_started_keys:
                # The graph already reflects the whole event batch; wait for
                # confirmed ticket/client binding before checking its donors.
                for key in tool_started_keys:
                    if self._critical_tool_donor(key):
                        self._prepare_probe_after_ms.pop(key, None)
                        self._tool_prepare_next_ms = 0.
                        self.counts["critical_tool_boundary_rearmed"] += 1
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
            if rank_dependencies_unchanged:
                self.frontier.retain_observed_state(
                    previous_version=graph_version,
                    invalidated_invocations=changed_rank_invocations,
                )
                self.counts["admission_rank_dependencies_preserved"] += 1
            if (
                sessions_revision == self._context_sessions_revision
                and all(event.kind in (
                    RuntimeEventKind.STRUCTURED_ACTION,
                    RuntimeEventKind.TOOL_WAIT_OBSERVATION,
                    RuntimeEventKind.CALL_CENSORED,
                ) for event in events)
            ):
                # These RCCG handlers only validate identities and record labels.
                # Keep live timing, boundary inputs and residency checks separate.
                cached = self._prefill_causal_cache
                if cached is not None and cached[0][:2] == (
                    semantic_revision, graph_version,
                ):
                    signature = (
                        self.semantic_revision, self.graph.graph_version,
                        *cached[0][2:],
                    )
                    self._prefill_causal_cache = (signature, *cached[1:])
                    self.counts["admission_causal_cache_preserved_observation"] += 1
                parent_cache = self._join_parent_cache
                if parent_cache is not None and parent_cache[0] == (
                    graph_version, sessions_revision,
                ):
                    self._join_parent_cache = (
                        (self.graph.graph_version, sessions_revision), parent_cache[1],
                    )

        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            paper.events(events)
        if self._agent_admission is not None:
            self._agent_admission.events(events)
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.events(events)

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
                self._eos_prefetch_keys.pop(rid, None)
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
            and (
                self.context_sessions.get(key.context_id) == key
                or self._full_prefix_migration is not None
                and self._full_prefix_migration.bound_key(key.context_id) == key
            )
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
            self._child_report_notices.pop(key.invocation_id, None)
            self._clear_semantic_invocation(key.invocation_id)
            # Keep this request's negative evidence through late body/finish
            # frames, and let the normal invocation cleanup retire it.
            self._semantic_keys[rid] = key
            self._decoded_tool_requests.add(rid)
            for join_id, stage in tuple(self._final_stages.items()):
                if stage.child_id == key.invocation_id:
                    self._clear_final_stage(join_id)
            self.counts["semantic_tool_chunk_invalidated"] += 1
            return
        if rid in self._decoded_tool_requests:
            self.counts["semantic_text_after_tool_ignored"] += 1
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
        if self._semantic_submitted_frames.get(rid) == (event.ts_ms, chars):
            self.counts["semantic_unchanged_frame_skipped"] += 1
            return
        if len(self._semantic_frames) >= 128 and rid not in self._semantic_frames:
            self.counts["semantic_text_capacity"] += 1
            return
        self._semantic_keys[rid] = key
        self._semantic_frames[rid] = event

    @property
    def eos_prefetch_evidence_window_ms(self) -> float:
        return max(
            self.eos_protocol_window_ms, min(self.prefetch_lead_ms, 2_000.),
        )

    def _observed_final_eos_live(self, key: PrefillCandidateKey, now_ms: float) -> bool:
        ended = self._semantic_finished.get(key.request_id)
        # EOS remains a completed native fact while causal identity is intact.
        # Bound its use by the existing restore lead and semantic-key lifetime.
        return bool(
            self._semantic_eos_proofs.get(key.request_id) is True
            and ended is not None
            and 0 <= now_ms - ended[0] <= self.eos_prefetch_evidence_window_ms
            and self._semantic_key_live(key, now_ms)
        )

    def _ensure_observed_final_stage(self, key: PrefillCandidateKey) -> None:
        ended = self._semantic_finished.get(key.request_id)
        now_ms = time.monotonic() * 1000
        if not self._observed_final_eos_live(key, now_ms):
            return
        self._eos_prefetch_keys[key.request_id] = key
        parent = self._semantic_parent(key.invocation_id)
        if parent is None:
            return
        join_id, parent_key = parent
        if self._tool_residency_grace is not None:
            self._release_tool_residency_grace("join_eos")
        if self.join_prefetch_trigger == "selective_report_or_eos":
            # Preserve this causal clock pair across child RETURN cleanup.
            pending = self._join_eos_arrival_pending
            previous = pending.get(parent_key.context_id)
            if previous is None or previous[:3] != (join_id, parent_key, key):
                pending[parent_key.context_id] = (join_id, parent_key, key, ended[0])
                pending.move_to_end(parent_key.context_id)
                if len(pending) > 256:
                    pending.popitem(last=False)
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
            (ended[0] + self.eos_prefetch_evidence_window_ms) / 1000.,
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
            markers = self._reasoning_open_token_ids | self._reasoning_close_token_ids
            marker = next((
                index for index in range(len(outputs) - 1, -1, -1)
                if outputs[index] in markers
            ), None)
            body_start = None
            if marker is not None and outputs[marker] in self._reasoning_close_token_ids:
                body_start = marker + 1
            elif marker is None:
                # enable_thinking=False closes reasoning in the immutable input
                # template; its ordinary output need not emit another </think>.
                inputs = getattr(req, "origin_input_ids", ()) or ()
                tail = inputs[-32:]
                prompt_markers = [
                    token for token in tail if token in markers
                ]
                if (
                    len(prompt_markers) >= 2
                    and prompt_markers[-2] in self._reasoning_open_token_ids
                    and prompt_markers[-1] in self._reasoning_close_token_ids
                ):
                    body_start = 0
                elif self._native_plain_output_template and not prompt_markers:
                    # Plain templates can share a vocabulary with thinking models.
                    body_start = 0
            if body_start is not None:
                # Inspect only the completed visible suffix, never the reasoning
                # prefix or a future decode. No text/token payload is logged.
                suffix = outputs[max(body_start, len(outputs) - 64):]
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
            skip_empty_restore=True,
        )
        ready = observation is not None and observation.step is not None
        self._semantic_target_cache[key] = (now_ms + 100., ready)
        if len(self._semantic_target_cache) > 256:
            self._semantic_target_cache = {
                k: value for k, value in self._semantic_target_cache.items()
                if value[0] > now_ms
            }
        return ready

    def _invalidate_wait_transfer_observation(self, key: PrefillCandidateKey) -> None:
        semantic = self._semantic_target_cache.pop(key, None)
        tool = self._tool_opportunity_cache.pop(key, None)
        if semantic is not None or tool is not None:
            self.counts["wait_transfer_observation_invalidated"] += 1
        hint = self.tool_wait_hints.get(key.context_id)
        if hint is not None and hint.key == key:
            self._tool_prefetch_next_ms = 0.

    @timed_runtime("semantic_updates")
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
                progress = self._semantic_progress.get(item.key.request_id, ())
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
                    "estimated_report_tokens": item.estimated_report_tokens,
                    "content_chars": item.content_chars,
                    "prior_tool_calls": item.prior_tool_calls,
                    "prior_model_rounds": item.prior_model_rounds,
                    "current_output_tokens": progress[-1][1] if progress else None,
                    "last_service_age_ms": (
                        now_ms - progress[-1][0] if progress else None
                    ),
                    "sampled_tokens_per_second": self._semantic_rate(item.key.request_id),
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
                self._semantic_frames.pop(rid, None)
                self.counts["semantic_pending_frame_expired"] += 1
                continue
            frame = (event.ts_ms, event.attributes["content_chars"])
            if self._semantic_submitted_frames.get(rid) == frame:
                self._semantic_frames.pop(rid, None)
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
            notice = self._child_report_notices.get(key.invocation_id)
            if notice is not None and (
                notice.workflow_id != key.root_workflow_id
                or notice.context_id != key.context_id
                or notice.context_epoch != key.context_epoch
                or notice.request_id != key.request_id
            ):
                notice = None
            worker.submit(SemanticReportInput(
                key, event.ts_ms, observed, event.attributes["content_chars"],
                event.attributes["content_tail"][-1024:],
                notice is not None,
                notice.estimated_tokens if notice else 0,
                self._semantic_tool_counts[key.invocation_id],
                child.llm_round,
                causal_progress_guard_ms=guard_ms,
            ))
            self._semantic_submit_ms[rid] = now_ms
            self._semantic_submitted_frames[rid] = frame
            self._semantic_frames.pop(rid, None)
            self.counts["semantic_input_submitted"] += 1

    @timed_runtime("scheduler_maintenance")
    def scheduler_step(
        self, waiting_queue: Sequence[object] = (), *, defer_observations: bool = False,
        defer_wait_predictions: bool = False,
    ) -> None:
        self._prefill_cycle_active = True
        self._prefill_residency_scan = None
        if self.event_server is not None:
            if self._hotpath_timing.enabled:
                with self._hotpath_timing.measure("control_drain"):
                    self.event_server.drain(max_messages=128)
            else:
                self.event_server.drain(max_messages=128)
        if self._prefetch_service_leases:
            self._refresh_prefetch_service_leases()
        if self._tool_residency_grace is not None:
            self._refresh_tool_residency_grace()
        now_ms = time.monotonic() * 1000
        if self._terminal_child_reclaims and now_ms >= self._terminal_child_reclaim_next_ms:
            self._retry_terminal_child_reclaims(now_ms=now_ms)
        self._wait_prediction_maintenance_deferred = defer_wait_predictions
        if not defer_wait_predictions:
            self._maintain_wait_predictions(now_ms)
        expired = self.physical_ledger.expire()
        if expired:
            self.counts["physical_expired"] += len(expired)
            self._discard_prefetch_tracking("physical_expired", expired)
            lease = self._admission_lease
            if lease is not None and lease.command_id in expired:
                self._admission_lease = None
                self.counts["admission_prefetch_expired"] += 1
        ticket = self._join_ticket
        if expired and ticket is not None and ticket.command_id in expired:
            ticket.command_id = None
            self.counts["join_prefetch_expired"] += 1
        if ticket is not None and not self._live_join_ticket():
            self._discard_join_ticket(
                "expired" if time.monotonic() >= ticket.expires_at
                else "safe_point_invalidated"
            )
        if not defer_observations:
            self.sample_runtime_observations(waiting_queue, now_ms=now_ms)
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            paper.tick(now=now_ms / 1000.)
        if self._agent_admission is not None:
            self._agent_admission.tick(now=now_ms / 1000.)

    def _maintain_wait_predictions(self, now_ms: float) -> None:
        """Refresh advisory wait forecasts after launch when overlap permits."""
        if (
            self._final_stages or self.tool_wait_hints
            or self.join_wait_hints or self._completion_hints
        ):
            self._maintain_prediction_state(now_ms)
        if self._model_worker is not None:
            hints = self._model_worker.poll()
            for hint in hints:
                if isinstance(hint, NativeToolWaitHint):
                    self._accept_tool_wait(hint)
                    continue
                if isinstance(hint, NativeJoinWaitHint):
                    if self._live_join_hint(hint):
                        self.join_wait_hint = hint
                    self.counts["join_work_forecast_ignored"] += 1
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
                key.context_id for key in self._waiting_context_keys(InvocationState.WAIT_TOOL)
            ]
            if contexts:
                offset = self._scan_offsets["tool_wait"] % len(contexts)
                for context in (contexts[offset:] + contexts[:offset])[:8]:
                    self._submit_tool_wait(context)
                self._scan_offsets["tool_wait"] = (offset + 8) % len(contexts)

    def _maintain_prediction_state(self, now_ms: float) -> None:
        if not (
            self._final_stages or self.tool_wait_hints
            or self.join_wait_hints or self._completion_hints
        ):
            return
        version = self.graph.graph_version
        if (
            version == self._prediction_maintenance_graph_version
            and now_ms < self._prediction_maintenance_next_ms
        ):
            return
        self._prediction_maintenance_graph_version = version
        self._prediction_maintenance_next_ms = (
            now_ms + self._prediction_maintenance_interval_ms
        )
        self.counts["prediction_maintenance_sweeps"] += 1
        # Transfer selection and enqueue still validate every live identity and
        # expiry; this sweep only retires stale prediction bookkeeping.
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
        if expired_join:
            self.counts["join_wait_expired"] += len(expired_join)
        if self._completion_hints:
            self._completion_hints = {
                join_id: hint for join_id, hint in self._completion_hints.items()
                if self._live_completion_hint(hint)
            }

    def sample_runtime_observations(
        self, waiting_queue: Sequence[object] = (), *, now_ms: float | None = None,
    ) -> None:
        """Read diagnostics after launch; idle selection keeps the same cadence."""
        if self._opportunity_writer is None:
            return
        if now_ms is None:
            now_ms = time.monotonic() * 1000
        if self.enable_h2d_opportunity_sampling and now_ms >= self._opportunity_next_ms:
            self._sample_h2d_opportunities(waiting_queue, now_ms=now_ms)
        if (
            self._terminal_cache_diagnostics
            and now_ms >= self._terminal_cache_next_ms
        ):
            self._sample_terminal_cache(now_ms=now_ms)
        if now_ms >= self._runtime_state_next_ms:
            self._runtime_state_next_ms = now_ms + 1_000
            self._record_runtime_state()

    def _record_runtime_state(self, *, final: bool = False) -> None:
        if self._opportunity_writer is not None:
            controller = getattr(self._native_cache, "cache_controller", None)
            fence_counts = getattr(controller, "beliefkv_h2d_fence_counts", None)
            idle_prepare_stats = getattr(
                self._native_cache, "beliefkv_idle_full_prepare_stats", None,
            )
            restore_tier_stats = getattr(
                getattr(self._native_cache, "cache_controller", None),
                "beliefkv_restore_tier_stats", None,
            )
            before_ms = time.monotonic() * 1000.
            wall_ms = time.time() * 1000.
            after_ms = time.monotonic() * 1000.
            self._opportunity_writer.record({
                "event": "admission_runtime_state", "ts_ms": wall_ms,
                "monotonic_ms": (before_ms + after_ms) / 2.,
                "clock_sample_span_ms": after_ms - before_ms,
                "final": final, "counts": dict(self.counts),
                "physical_disabled": self.physical_disabled,
                "internal_comparison_mode": self.internal_comparison_mode,
                "custom_kv_management": self._custom_kv_management,
                "paper_baseline": (
                    paper.status()
                    if (paper := getattr(self, "_paper_baseline", None)) is not None else None
                ),
                "agent_admission": (
                    self._agent_admission.status() if self._agent_admission is not None else None
                ),
                "agent_admission_enabled": self.enable_agent_working_set,
                "agent_join_free_admission": self.agent_join_free_admission,
                "agent_free_continuation_admission": self.agent_free_continuation_admission,
                "full_prefix_migration": (
                    self._full_prefix_migration.status()
                    if self._full_prefix_migration is not None else None
                ),
                "join_parent_priority": self.enable_join_parent_priority,
                "join_last_child_priority": self.enable_join_last_child_priority,
                "prefill_scheduling_policy": self.prefill_scheduling_policy,
                "tool_continuity": self._tool_continuity is not None,
                "tool_continuity_grace_ms": self.tool_continuity_grace_ms,
                "tool_residency_grace": (
                    {
                        "active": self._tool_residency_grace.key is not None,
                        "max_contexts": 1,
                        "revocable_on_capacity_pressure": True,
                        "counts": dict(self._tool_residency_grace.counts),
                    } if self._tool_residency_grace is not None else None
                ),
                "tool_continuity_policy": (
                    {
                        "short_gap_seconds": self._tool_continuity.short_gap_seconds,
                        "max_segment_requests": self._tool_continuity.max_rounds,
                        "max_completed_segment_output_tokens": self._tool_continuity.max_output_tokens,
                        "resident_only": self.prefill_scheduling_policy == "workflow",
                        "aged_bypass_ordinary_quota": 4,
                        "ordinary_admissions_since_priority":
                            self._tool_continuity_normal_admissions,
                        "credit_scope": "root_workflow;earned_once_per_request",
                        "counts": dict(self._tool_continuity.counts),
                        "segments": len(self._tool_continuity.segments),
                        "ready_segments": len(self._tool_continuity.ready),
                    } if self._tool_continuity is not None else None
                ),
                "join_priority_order": "aged_fifo_then_native;independent_four_admission_quota",
                "restore_priority_scope": "same_causal_class;no_aged_head_bypass",
                "prefetch_residency_protection": self.enable_prefetch_residency_protection,
                "prefetch_protection_revocable_on_pressure": True,
                "join_priority_ordinary_quota": 4,
                "join_priority_pending_parents": len(self._join_priority_pending),
                "join_only_priority_ordinary_quota": 4,
                "join_only_priority_allows_aged_head_after_quota": True,
                "join_only_pending_parents": len(self._join_priority_pending),
                "final_stage_prefetch": self.enable_final_stage_prefetch,
                "final_stage_priority": (
                    self.enable_final_stage_priority
                    if self.enable_final_stage_priority is not None
                    else self.enable_admission_prefetch or self.enable_final_stage_prefetch
                ),
                "prepare_host": self.enable_prepare_host,
                "background_prepare": self.enable_background_prepare,
                "idle_host_mirror_recycle": self.idle_host_mirror_recycle,
                "terminal_child_reclaim": self.enable_terminal_child_reclaim,
                "subagent_retention": (
                    self._subagent_retention.describe()
                    if self._subagent_retention is not None else None
                ),
                "h2d_opportunity_sampling": self.enable_h2d_opportunity_sampling,
                "prepare_pool_scope": (
                    "observed_full_prefix_or_session_path"
                    if self._full_prefix_migration is not None else "missing_full_session_path"
                ),
                "spawn_full_prepare": {
                    "pending_contexts": len(self._spawn_full_prepares),
                    "max_batch_bytes": SPAWN_FULL_PREPARE_MAX_BYTES,
                    "max_merged_nodes": 64,
                },
                "idle_full_prepare": {
                    "interval_ms": IDLE_FULL_PREPARE_INTERVAL_MS,
                    "queued_owner_backup": (
                        self.enable_prepare_host and self.enable_background_prepare
                        and self.idle_prepare_policy == "cold"
                    ),
                    "native": dict(idle_prepare_stats) if isinstance(idle_prepare_stats, dict) else None,
                },
                "latest_full_session_branches_retired": getattr(
                    getattr(
                        getattr(self._native_cache, "tree_core", None),
                        "components_by_type", {},
                    ).get(0),
                    "latest_session_branch_retirements", None,
                ),
                "resident_first": self.enable_resident_first,
                "prefill_ordering_mode": (
                    "native" if self._native_prefill_order else "workflow"
                ),
                "workflow_attained_work": self._workflow_attained_work_enabled,
                "workflow_work_metric": "completed_request_output_tokens",
                "workflow_work_accounts": len(self._workflow_attained_output_tokens),
                "workflow_completion_focus": (
                    self._completion_order.focus if self._completion_order is not None else None
                ),
                "workflow_completion_quantum": (
                    self._completion_order.quantum if self._completion_order is not None else None
                ),
                "execution_handoff": self.enable_execution_handoff,
                "overlap_prefetch_supported": self.can_prefetch_during_overlap(),
                "execution_handoff_request_id": (
                    self._execution_handoff.key.request_id
                    if self._execution_handoff is not None else None
                ),
                "tool_timing_only": self._tool_timing_only,
                "tool_predictor_configured": self._model_worker is not None,
                "online_tool_timing": self._online_tool_timing is not None,
                "tool_timing_prior_sha256": (
                    self._online_tool_timing.prior.sha256
                    if self._online_tool_timing is not None
                    and self._online_tool_timing.prior is not None else None
                ),
                "predicted_tool_offload": self.enable_predicted_tool_offload,
                "tool_offload_before_admission": self.tool_offload_before_admission,
                "layer_handoff_enabled": self.layer_handoff_enabled,
                "tool_offload_policy": (
                    type(self._predicted_tool_offload).__name__
                    if self._predicted_tool_offload is not None else None
                ),
                "idle_prepare_io_backend": self.idle_prepare_io_backend,
                "online_tool_timing_revision": (
                    self._online_tool_timing.revision if self._online_tool_timing else None
                ),
                "report_length_calibration_samples": len(self._report_work.ratios),
                "selective_join_prefetch": {
                    **asdict(self._selective_join_policy),
                    "enabled": self.join_prefetch_trigger == "selective_report_or_eos",
                    "max_advance_ms": self.prefetch_lead_ms,
                    "eos_to_native_arrival_window_ms": self._join_eos_arrival_window_ms,
                    "eos_to_native_arrival_samples": len(self._join_eos_arrival_samples),
                    "window_statistic": "max(configured_protocol_floor, online_p10_after_4_samples)",
                    "service_semantics": "controller_submit_to_ack_plus_enqueue; not exposed GPU wait",
                },
                "borrowable_parent_headroom": self._borrowable_parent_headroom,
                "tool_predictor_disabled": bool(
                    self._model_worker is not None and self._model_worker.disabled
                ),
                "tool_prefetch": self.enable_tool_prefetch,
                "tool_restore_mode": self.tool_restore_mode,
                "execution_frontier_restore": self.enable_frontier_restore,
                "frontier_restore_ordinary_quota": self.frontier_restore_ordinary_quota,
                "frontier_cold_reclaim": self.enable_frontier_cold_reclaim,
                "frontier_cold_reclaim_policy": self.frontier_cold_reclaim_policy,
                "frontier_cold_reclaim_max_nodes": (
                    64 if self.frontier_cold_reclaim_policy == "selected_queue" else 16
                ),
                "restore_tiers": self.restore_tiers,
                "restore_tiers_supported": self._restore_tiers_supported,
                "restore_tiers_native": (
                    dict(restore_tier_stats) if isinstance(restore_tier_stats, dict) else {}
                ),
                "native_free_page_h2d_fence": {
                    "requested": self.free_page_h2d_fence_requested,
                    "enabled": getattr(controller, "beliefkv_free_page_fence_enabled", False) is True,
                    "supported": callable(getattr(controller, "beliefkv_record_free_page_boundary", None)),
                    "counts": dict(fence_counts) if isinstance(fence_counts, dict) else {},
                },
                "native_prefetch_batch_dependencies": (
                    {
                        "checks": getattr(
                            self._native_cache, "beliefkv_prefetch_dependency_checks", 0,
                        ),
                        "unrelated_waits_skipped": getattr(
                            self._native_cache, "beliefkv_prefetch_unrelated_waits_skipped", 0,
                        ),
                    }
                    if callable(getattr(
                        self._native_cache, "beliefkv_prefill_load_consumer", None,
                    )) else None
                ),
                "idle_prepare_policy": self.idle_prepare_policy,
                "tool_prefetch_planning_slack_ms": TOOL_PREFETCH_PLANNING_SLACK_MS,
                "prefetch_lead_ms": self.prefetch_lead_ms,
                "tool_prefetch_priority_ms": self.tool_prefetch_priority_ms,
                "join_prefetch_min_advance_ms": self.join_prefetch_min_advance_ms,
                "tool_prefetch_min_advance_ms": self.tool_prefetch_min_advance_ms,
                "parallel_wait_prefetch": self._parallel_wait_prefetch,
                "wait_prefetch_contexts": self._wait_prefetch_contexts,
                "tool_inflight_contexts": len(self._tool_inflight_tickets),
                "join_inflight_contexts": len(self._join_inflight_tickets),
                "prediction_maintenance_interval_ms": self._prediction_maintenance_interval_ms,
                "semantic_work_statistic": self.semantic_work_statistic,
                "final_admission_policy": "remaining_report_work",
                "eos_protocol_window_ms": self.eos_protocol_window_ms,
                "eos_prefetch_evidence_window_ms": self.eos_prefetch_evidence_window_ms,
                "terminal_cache_diagnostics": self._terminal_cache_diagnostics,
                "profile_shared_path": self._hotpath_timing.enabled,
                "h2d_seed_samples": self._h2d_seed_count,
                "h2d_service_samples": len(self._h2d_samples),
                "prefetch_residency_budget": vars(self._residency_budget).copy(),
                "prefetch_request_counts_at_budget": (
                    dict(zip(
                        ("running_requests", "free_request_rows", "request_row_capacity"),
                        self._residency_request_counts,
                    )) if self._residency_request_counts is not None else None
                ),
                "prefetch_residency_headroom": (
                    asdict(self._residency_headroom)
                    if self._residency_headroom is not None else None
                ),
                "prefetch_locked_count": sum(
                    lease.lock_params is not None
                    for lease in self._prefetch_service_leases.values()
                ),
                "prefetch_locked_contexts_by_role": {
                    role: len({
                        lease.key for lease in self._prefetch_service_leases.values()
                        if lease.lock_params is not None and lease.demand_ready == ready
                    })
                    for role, ready in (("demand", True), ("speculative", False))
                },
                "prefetch_protected_bytes": sum(
                    lease.protected_bytes
                    for lease in self._prefetch_service_leases.values()
                ),
                "semantic_worker_configured": self._semantic_worker is not None,
                "join_prefetch_trigger": self.join_prefetch_trigger,
                "semantic_worker_error": (
                    self._semantic_worker.error if self._semantic_worker else ""
                ),
                "shared_path_timing": self._hotpath_timing.snapshot(),
                "control_timing_sample_every": (
                    CONTROL_TIMING_SAMPLE_EVERY
                    if self.event_server is not None and self._hotpath_timing.enabled
                    else None
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

    @timed_runtime("opportunity_sampling")
    def _sample_h2d_opportunities(
        self, waiting_queue: Sequence[object], *, now_ms: float,
    ) -> None:
        writer = self._opportunity_writer
        if (
            not self.enable_h2d_opportunity_sampling
            or writer is None or now_ms < self._opportunity_next_ms
        ):
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
                "join_id": invocation.join_id if source == "join_wait" and invocation else None,
                "active_tool_ids": sorted(invocation.active_tool_calls)
                if source == "tool_wait" and invocation else [],
                "semantic_revision": self.semantic_revision,
            }
            observation = self.inspect_context_h2d_opportunity(
                context_id=key.context_id, context_epoch=key.context_epoch,
                admission_candidate=source == "admission_candidate",
                max_steps=16 if source in ("tool_wait", "join_wait") else 1,
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
                    "planned_nodes": [
                        {"node_id": item.node_id, "creation_time": item.creation_time,
                         "include_mamba": item.include_mamba}
                        for item in observation.steps
                    ],
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
        observed = getattr(h2d, "candidate", None)
        if isinstance(observed, ActionLocalPrefetchCandidate):
            candidate = ActionLocalShadowCandidate(
                anchors=observed.anchors, nodes=observed.nodes,
                missing_full_host_tokens=sum(
                    max(node.full_device_tokens - node.full_host_tokens, 0)
                    for node in observed.nodes
                ),
                missing_mamba_host_nodes=sum(
                    node.mamba_device_present and not node.mamba_host_present
                    for node in observed.nodes
                ),
            )
        elif h2d is not None and h2d.no_step_reason == "closure_unobservable":
            # Both observers use this same closure at the scheduler safe point.
            row["prepare_reason"] = "no_observable_shadow_closure"
            row["prepare_blocked_detail"] = h2d.blocked_detail
            return
        else:
            candidate = self.capture_shadow_candidate(
                cache, context_id=key.context_id, context_epoch=key.context_epoch,
            )
        if not isinstance(candidate, ActionLocalShadowCandidate):
            row["prepare_reason"] = "no_observable_shadow_closure"
            return
        tail_leaves = self._shadow_reclaim_tail_leaves(candidate)
        row["prepare_full_tail_leaf_candidates"] = len(tail_leaves)
        step = next_shadow_backup_step(candidate, reclaim_tail_leaves=tail_leaves)
        if step is None:
            row["prepare_reason"] = "no_eligible_shadow_step"
            return
        node = next(node for node in candidate.nodes if node.node_id == step.node_id)
        full_needed = max(node.full_device_tokens - node.full_host_tokens, 0)
        checkpoint_full_needed = max(full_needed, step.missing_full_prefix_tokens)
        mamba_needed = int(
            step.include_mamba and node.mamba_device_present and not node.mamba_host_present
        )
        headroom = (
            h2d.headroom if h2d is not None else
            observe_static_full_mamba_headroom(cache)
        )
        fits = (
            headroom.host_full_free_tokens >= checkpoint_full_needed
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
            "prepare_required_checkpoint_full_tokens": checkpoint_full_needed,
            "prepare_required_mamba_slots": mamba_needed,
            "prepare_host_full_free_tokens": headroom.host_full_free_tokens,
            "prepare_host_mamba_free_slots": headroom.host_mamba_free_slots,
            "prepare_fits_current_host_free_lists": fits,
        })

    def _join_parent_key(self, join_id: str) -> PrefillCandidateKey | None:
        signature = (self.graph.graph_version, self._context_sessions_revision)
        cached = self._join_parent_cache
        if cached is None or cached[0] != signature:
            cached = (signature, {})
            self._join_parent_cache = cached
        if join_id in cached[1]:
            return cached[1][join_id]
        join = self.graph.joins.get(join_id)
        if join is None or not 0 < len(join.member_invocation_ids) <= 8:
            cached[1][join_id] = None
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
                cached[1][join_id] = key
                return key
        cached[1][join_id] = None
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

    def _invalidate_wait_context_states(self, cached, states: set[InvocationState]) -> None:
        for state in states:
            for key in cached[1].pop(state, ()):
                cached[2].pop(key.invocation_id, None)
                cached[3].pop(key.context_id, None)
        self._wait_context_cache = (
            (self.graph.graph_version, self._context_sessions_revision),
            cached[1], cached[2], cached[3],
        )
        self.counts["wait_context_groups_invalidated"] += len(states)
        if not states:
            self.counts["wait_context_cache_retained"] += 1

    def _update_wait_context_cache(
        self, deltas: Sequence[GraphDelta], *, previous_version: int,
        affected_invocations: set[str] | None = None,
    ) -> None:
        cached = self._wait_context_cache
        if cached is None:
            return
        if cached[0] != (previous_version, self._context_sessions_revision):
            # Changes outside the tracked event path require the original scan.
            self._wait_context_cache = None
            return
        affected = affected_invocations
        if affected is None:
            affected = set()
            for delta in deltas:
                affected.update(delta.awakened_invocations)
                affected.update(delta.parked_invocations)
                affected.update(delta.completed_invocations)
                for context_id in delta.changed_contexts:
                    context = self.graph.contexts.get(context_id)
                    if context is not None:
                        affected.update(context.invocation_ids)
                for invocation_id in delta.completed_invocations:
                    invocation = self.graph.invocations.get(invocation_id)
                    if invocation is not None:
                        parent = invocation.return_target_id or invocation.parent_invocation_id
                        if parent is not None:
                            affected.add(parent)
        states = set()
        for invocation_id in affected:
            previous_state = cached[2].get(invocation_id)
            invocation = self.graph.invocations.get(invocation_id)
            current_state = invocation.state if invocation is not None else None
            if previous_state is current_state:
                continue
            if previous_state in cached[1]:
                states.add(previous_state)
            if current_state in cached[1]:
                states.add(current_state)
        self._invalidate_wait_context_states(cached, states)

    def _context_session_changed(self, context_id: str) -> None:
        previous_revision = self._context_sessions_revision
        self._context_sessions_revision += 1
        cached = self._wait_context_cache
        if cached is None:
            return
        if cached[0] != (self.graph.graph_version, previous_revision):
            self._wait_context_cache = None
            return
        key = self.context_sessions.get(context_id)
        invocation = self.graph.invocations.get(key.invocation_id) if key is not None else None
        states = {
            state for state in (
                cached[3].get(context_id),
                invocation.state if invocation is not None else None,
            ) if state in cached[1]
        }
        self._invalidate_wait_context_states(cached, states)

    def _waiting_context_keys(self, state: InvocationState) -> tuple[PrefillCandidateKey, ...]:
        signature = (self.graph.graph_version, self._context_sessions_revision)
        cached = self._wait_context_cache
        if cached is None or cached[0] != signature:
            cached = (signature, {}, {}, {})
            self._wait_context_cache = cached
        if state not in cached[1]:
            keys = [
                key for key in self.context_sessions.values()
                if (invocation := self.graph.invocations.get(key.invocation_id)) is not None
                and invocation.state is state
            ]
            # Tool refresh keeps session insertion order; PREPARE keeps context order.
            if state is InvocationState.WAIT_JOIN:
                keys.sort(key=lambda key: key.context_id)
            cached[1][state] = tuple(keys)
            for key in keys:
                cached[2][key.invocation_id] = state
                cached[3][key.context_id] = state
            self.counts["wait_context_groups_built"] += 1
        return cached[1][state]

    def _scan_unhinted_wait(self) -> bool:
        """Revisit overflowed or unhinted waits only while the worker is idle."""
        tools = sorted(
            key.context_id for key in self._waiting_context_keys(InvocationState.WAIT_TOOL)
            if key.context_id not in self.tool_wait_hints
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

    def _observe_child_report_notice(self, event: RuntimeEvent) -> bool:
        if (
            event.attributes.get(CHILD_COMPLETION_INTENT) is not True
            or event.attributes.get("child_completion_signal_kind") != "stage"
        ):
            return False
        child = self.graph.invocations.get(event.invocation_id)
        context = self.graph.contexts.get(event.context_id)
        join = self.graph.joins.get(event.join_id)
        estimated = event.attributes.get("estimated_final_report_tokens")
        if not (
            child is not None and not child.state.terminal
            and child.workflow_id == event.workflow_id
            and child.context_id == event.context_id
            and context is not None and type(event.context_epoch) is int
            and 0 <= event.context_epoch <= context.epoch
            and join is not None and not join.satisfied
            and join.workflow_id == event.workflow_id
            and event.invocation_id in join.member_invocation_ids - join.completed_member_ids
            and type(estimated) is int and 1 <= estimated <= 4096
        ):
            return False
        # The validated graph batch may already include the next submit. Bind
        # announcement history in event order, independently of H2D eligibility.
        self._child_report_notices[event.invocation_id] = _ChildReportNotice(
            event.workflow_id, event.context_id, event.context_epoch, estimated,
            event.join_id, time.monotonic() + (
                float(os.environ.get("ACTIVATION_WALL_CLOCK_SECONDS", "14400"))
                if self._report_prediction_enabled else 120.
            ),
            request_id=event.attributes.get("completion_stage_bound_request_id"),
        )
        self.counts["child_report_notice_observed"] += 1
        return True

    def _bind_child_report_notice(self, event: RuntimeEvent) -> None:
        if event.attributes.get("runtime_internal"):
            return
        notice = self._child_report_notices.get(event.invocation_id)
        if notice is None:
            return
        rid = event.attributes.get("request_id")
        if (
            event.workflow_id == notice.workflow_id
            and event.context_id == notice.context_id
            and isinstance(rid, str) and rid
            and notice.request_id is None
            and event.context_epoch == notice.context_epoch + 1
        ):
            self._child_report_notices[event.invocation_id] = replace(
                notice, context_epoch=event.context_epoch, request_id=rid,
            )
        elif (
            event.workflow_id != notice.workflow_id
            or event.context_id != notice.context_id
            or event.context_epoch != notice.context_epoch
            or rid != notice.request_id
        ):
            self._child_report_notices.pop(event.invocation_id, None)

    def _activate_child_report_stage(
        self, key: PrefillCandidateKey, child_id: str, notice: _ChildReportNotice, *,
        resumed_after_sibling_return: bool = False,
    ) -> None:
        self._clear_final_stage(notice.join_id)
        progress = self._semantic_progress.get(notice.request_id, ())
        stage = _ChildFinalStage(
            key, notice.join_id, child_id, notice.context_epoch,
            notice.estimated_tokens, notice.expires_at,
            request_id=notice.request_id,
            generated_tokens=progress[-1][1] if progress else 0,
            tokens_per_second=self._semantic_rate(notice.request_id) if progress else None,
            issued_nodes=self._issued_join_nodes(notice.join_id, key),
        )
        self._final_stages[notice.join_id] = stage
        if notice.request_id is not None:
            self._final_request_stages[notice.request_id] = stage
        self.counts["final_stage_accepted"] += 1
        if resumed_after_sibling_return:
            self.counts["final_stage_resumed_after_sibling_return"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "child_final_stage_accepted",
                "ts_ms": time.time() * 1000.,
                "workflow_id": notice.workflow_id, "join_id": notice.join_id,
                "child_invocation_id": child_id,
                "context_epoch": notice.context_epoch,
                "estimated_final_report_tokens": notice.estimated_tokens,
                "epoch_handoff": notice.request_id is not None,
                "resumed_after_sibling_return": resumed_after_sibling_return,
            })

    def _resume_remaining_child_report_stage(
        self, join_id: str, *, resumed_after_sibling_return: bool = True,
    ) -> None:
        if self.enable_confirmed_join_canary or join_id in self._noncontinuing_joins:
            return
        join = self.graph.joins.get(join_id)
        if join is None or join.satisfied or join.mode.value != "all":
            return
        remaining = join.member_invocation_ids - join.completed_member_ids
        if len(remaining) != 1:
            return
        previous = self._final_stages.get(join_id)
        if previous is not None and self._live_final_stage(previous):
            return
        child_id = next(iter(remaining))
        notice = self._child_report_notices.get(child_id)
        child = self.graph.invocations.get(child_id)
        context = self.graph.contexts.get(child.context_id) if child else None
        key = self._join_parent_key(join_id)
        parent = self.graph.invocations.get(key.invocation_id) if key else None
        if (
            notice is None or notice.join_id != join_id
            or notice.workflow_id != join.workflow_id
            or time.monotonic() >= notice.expires_at
            or child is None or child.state.terminal or child.active_tool_family is not None
            or child.context_id != notice.context_id
            or context is None or context.epoch != notice.context_epoch
            or key is None or parent.state is not InvocationState.WAIT_JOIN
            or parent.join_id != join_id
        ):
            return
        self._activate_child_report_stage(
            key, child_id, notice,
            resumed_after_sibling_return=resumed_after_sibling_return,
        )
        if not resumed_after_sibling_return:
            self.counts["final_stage_resumed_after_report_submit"] += 1

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
            notice = self._child_report_notices.get(child_id)
            if notice is not None:
                self._activate_child_report_stage(key, child_id, notice)
            return
        # A notification can describe intent, but only native EOS permits a
        # provisional JOIN transfer. Confirmed RETURN recovery is independent.
        self.counts["join_intent_waiting_for_native_eos"] += 1
        if self.completion_lead is not None:
            age_ms = time.monotonic() * 1000 - event.ts_ms
            if 0 <= age_ms <= self.completion_lead.p90_ms:
                self._completion_hints[join_id] = _CompletionReentryHint(
                    key, join_id, child_id, context.epoch, event.ts_ms,
                )
                self.counts["join_completion_forecast_accepted"] += 1
            else:
                self.counts["join_completion_forecast_too_late"] += 1

    def _clear_final_stage(self, join_id: str) -> None:
        stage = self._final_stages.pop(join_id, None)
        if stage is not None and stage.request_id is not None:
            self._final_request_stages.pop(stage.request_id, None)
        if (
            self.join_prefetch_trigger == "selective_report_or_eos"
            and stage is not None and self._critical_parent_demand is not None
            and self._critical_parent_demand[0] == stage.key
            and join_id not in self._confirmed_join_tickets
        ):
            self._critical_parent_demand = None
            self.counts["selective_join_capacity_intent_invalidated"] += 1
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

    def _live_final_stage_forecast(
        self, stage: _ChildFinalStage, now_ms: float,
    ) -> SemanticReportReply | None:
        forecast = self._semantic_forecasts.get(stage.request_id)
        if forecast is None:
            return None
        observation = forecast.observation
        key = observation.key
        if (
            forecast.final_score < self._semantic_score_threshold
            or not 0 <= now_ms - observation.observed_ts_ms <= 1_500.
            or key.request_id != stage.request_id
            or key.invocation_id != stage.child_id
            or key.context_epoch != stage.child_epoch
            or not self._semantic_key_live(key, now_ms)
        ):
            return None
        return forecast

    def _remaining_semantic_work(
        self, forecast: SemanticReportReply, generated: int,
    ) -> tuple[float, str] | None:
        advanced = max(0, generated - forecast.observation.observed_output_tokens)
        statistic = self.semantic_work_statistic
        work = forecast.upper_tokens if statistic == "upper" else forecast.middle_tokens
        if work <= advanced:
            if statistic != "center" or forecast.upper_tokens <= advanced:
                return None
            work, statistic = forecast.upper_tokens, "upper_after_center_overrun"
        return work - advanced, statistic

    def _roll_final_stage(self) -> SessionH2DOpportunity | None:
        if (
            self._report_prediction_enabled
            and not self._eos_prefetch_keys
        ):
            return self._roll_report_service_prefetch()
        if not (
            self.enable_final_stage_prefetch or self.enable_admission_prefetch
        ) or self.physical_disabled or not self._eos_prefetch_keys:
            return
        if self._wait_prefetch_transfer_busy():
            return
        now_ms = time.monotonic() * 1000
        ticket = self._join_ticket
        if ticket is not None and self._live_join_ticket():
            if not (
                self.join_prefetch_trigger == "selective_report_or_eos"
                and ticket.phase == "provisional" and ticket.command_id is None
                and ticket.report_start_window_ms is not None
                and any(
                    self._observed_final_eos_live(child, now_ms)
                    for child in self._eos_prefetch_keys.values()
                )
            ):
                return
            if self._join_ticket_has_native_eos(ticket):
                # Same parent: retain its identity and use the EOS enqueue path.
                return
            if (
                self._critical_parent_demand is not None
                and self._critical_parent_demand[0] == ticket.key
            ):
                self._critical_parent_demand = None
            self._discard_join_ticket("observed_eos_preempts_report_planning")
            self.counts["selective_join_yielded_to_eos"] += 1
        for rid, child_key in tuple(self._eos_prefetch_keys.items()):
            if not self._observed_final_eos_live(child_key, now_ms):
                self._eos_prefetch_keys.pop(rid, None)
                continue
            self._ensure_observed_final_stage(child_key)
            stage = self._final_request_stages.get(rid)
            if stage is None:
                continue
            if stage.join_id in self._join_inflight_tickets:
                continue
            if not self._live_final_stage(stage):
                continue
            if (
                child_key is None or child_key.invocation_id != stage.child_id
                or child_key.context_epoch != stage.child_epoch
            ):
                continue
            remaining_ms = 0.
            self.counts["observed_eos_h2d_candidate"] += 1
            observation = self._wait_prefetch_opportunity(
                stage.key, include_checkpoint_plan=self._borrowable_parent_headroom,
            )
            cost_plan = None
            if self._borrowable_parent_headroom and observation is not None:
                cost_plan = self._report_restore_cost_plan(observation)
                if cost_plan.step is not None:
                    budget = self._current_residency_budget(
                        include_evictable=True, headroom=getattr(cost_plan, "headroom", None),
                    )
                    # EOS can follow an exhausted/missing report forecast. Its
                    # safe checkpoint still needs capacity before any enqueue.
                    self._set_critical_parent_demand(
                        stage.key,
                        cost_plan.required_full_tokens + budget.reserve_full_tokens,
                        cost_plan.required_mamba_slots + budget.reserve_mamba_slots,
                        min(
                            stage.expires_at,
                            (
                                self._semantic_finished[rid][0]
                                + self.eos_prefetch_evidence_window_ms
                            ) / 1000.,
                        ),
                    )
                    self.counts["observed_eos_capacity_preparation"] += 1
                    if self._reclaim_critical_parent_headroom(
                        _planned_opportunity=observation,
                    ):
                        observation = self._wait_prefetch_opportunity(
                            stage.key, include_checkpoint_plan=True,
                        )
            if observation is None or observation.step is None:
                capacity_blocked = cost_plan is not None and cost_plan.step is not None
                self.counts[
                    "observed_eos_capacity_blocked" if capacity_blocked
                    else "observed_eos_no_host_target"
                ] += 1
                self._record_wait_prefetch_plan(
                    "join_ticket", stage.key, observation,
                    reason="capacity_budget" if capacity_blocked else "no_restore_plan",
                    remaining_ms=remaining_ms,
                )
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
                    if tokens > 0
                )
            except (AttributeError, KeyError, TypeError):
                continue
            if required_bytes <= 0:
                continue
            # EOS is an observed completion, not an ETA. A cold-start or
            # out-of-range service estimate must not gate its remaining window.
            if not self._prefetch_slot_available(
                stage.key, budget=self._current_residency_budget(
                    include_evictable=True, headroom=getattr(observation, "headroom", None),
                ),
            ):
                self.counts["semantic_h2d_admission_budget_busy"] += 1
                self._record_wait_prefetch_plan(
                    "join_ticket", stage.key, observation,
                    reason="admission_budget_busy", remaining_ms=remaining_ms,
                )
                continue
            self._record_wait_prefetch_plan(
                "join_ticket", stage.key, observation,
                reason="start_window", remaining_ms=remaining_ms,
            )
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
                    "trigger_kind": "observed_no_tool_eos",
                    "native_eos_age_ms": now_ms - self._semantic_finished[rid][0],
                    "h2d_ms": None,
                    "enqueue_to_submit_p90_ms": None,
                    "prediction_early_ms": None,
                    "start_window_ms": None,
                    "prefetch_lead_ms": self.prefetch_lead_ms,
                    "service_sample_count": None,
                    "service_support": "not_required_observed_eos",
                    "required_bytes": required_bytes,
                    "context_id": stage.key.context_id,
                    "context_epoch": stage.key.context_epoch,
                    "child_invocation_id": stage.child_id,
                })
            return observation

    def _report_remaining_ms(self, stage: _ChildFinalStage) -> float | None:
        if (
            not self._live_final_stage(stage) or stage.request_id is None
            or stage.request_id in self._decoded_tool_requests
            or not self._final_stage_service_available(stage)
        ):
            return None
        rate = self._semantic_rate(stage.request_id)
        remaining = self._report_work.remaining(
            expected=stage.expected_tokens, generated=stage.generated_tokens,
        )
        if rate is None or remaining is None:
            return None
        return remaining * 1000. / rate

    @property
    def _report_prediction_enabled(self) -> bool:
        return self.join_prefetch_trigger in ("report_service_or_eos", "selective_report_or_eos")

    def _report_parent_full_upper_bytes(self, key: PrefillCandidateKey) -> int:
        tokens = self._context_tokens.get(key.context_id)
        if tokens is None or tokens[0] != key.context_epoch or tokens[1] <= 1:
            return 0
        try:
            full_unit = self._native_cache.cache_controller.mem_pool_host.entry_map[
                "kv"
            ].host_pool.size_per_token
        except (AttributeError, KeyError, TypeError):
            return 0
        return (tokens[1] - 1) * full_unit if type(full_unit) is int and full_unit > 0 else 0

    def _invalidate_join_eos_arrival(self, event: RuntimeEvent) -> None:
        for context_id, (join_id, parent, child, _) in tuple(
            self._join_eos_arrival_pending.items()
        ):
            if (
                event.kind is RuntimeEventKind.WORKFLOW_END
                and event.workflow_id == parent.root_workflow_id
                or event.kind is RuntimeEventKind.JOIN_TIMEOUT and event.join_id == join_id
                or event.kind in (
                    RuntimeEventKind.TOOL_START, RuntimeEventKind.INVOCATION_CANCEL,
                    RuntimeEventKind.CONTEXT_COMPACT, RuntimeEventKind.CONTEXT_ADVANCE,
                ) and (
                    event.invocation_id in (parent.invocation_id, child.invocation_id)
                    or event.context_id in (parent.context_id, child.context_id)
                )
            ):
                self._join_eos_arrival_pending.pop(context_id, None)

    def _observe_join_eos_arrival(self, key: PrefillCandidateKey, now_ms: float) -> None:
        pending = self._join_eos_arrival_pending.pop(key.context_id, None)
        if pending is None:
            return
        join_id, parent, child, eos_ms = pending
        join = self.graph.joins.get(join_id)
        if not (
            join is not None and join.satisfied and join.mode.value == "all"
            and join.workflow_id == parent.root_workflow_id
            and child.invocation_id in join.completed_member_ids
            and self.graph.invocations.get(child.invocation_id) is not None
            and self.graph.invocations[child.invocation_id].state is InvocationState.DONE
            and key.invocation_id in join.waiter_invocation_ids
            and key.root_workflow_id == parent.root_workflow_id
            and key.invocation_id == parent.invocation_id
            and key.context_epoch == parent.context_epoch + 1
            and (key.session_id, key.session_generation)
            == (parent.session_id, parent.session_generation)
            and key.session_id is not None and 0 <= now_ms - eos_ms <= 10_000.
        ):
            self.counts["join_eos_arrival_identity_rejected"] += 1
            return
        elapsed = now_ms - eos_ms
        self._join_eos_arrival_samples.append(elapsed)
        if len(self._join_eos_arrival_samples) >= 4:
            self._join_eos_arrival_window_ms = max(
                self.eos_protocol_window_ms,
                percentile(self._join_eos_arrival_samples, .1),
            )
        self.counts["join_eos_arrival_observed"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "join_eos_native_arrival", "ts_ms": time.time() * 1000.,
                "join_id": join_id, "workflow_id": key.root_workflow_id,
                "context_id": key.context_id, "context_epoch": key.context_epoch,
                "request_id": key.request_id, "child_request_id": child.request_id,
                "eos_to_native_arrival_ms": elapsed,
                "next_prediction_protocol_window_ms": self._join_eos_arrival_window_ms,
                "scope": "causal no-tool EOS to native arrival; excludes later service queueing",
            })

    def _next_report_stages(self):
        for _ in range(min(self._selective_join_policy.max_candidates, len(self._final_stages))):
            join_id = next(iter(self._final_stages))
            stage = self._final_stages[join_id]
            self._final_stages.move_to_end(join_id)
            yield stage

    def _report_planning_ceiling_ms(self, key: PrefillCandidateKey) -> float | None:
        tokens = self._context_tokens.get(key.context_id)
        if (
            tokens is None or tokens[0] != key.context_epoch
            or type(tokens[1]) is not int or tokens[1] <= 1
        ):
            return None
        try:
            entries = self._native_cache.cache_controller.mem_pool_host.entry_map
            full_unit = entries["kv"].host_pool.size_per_token
            mamba_unit = entries["mamba"].host_pool.size_per_token if "mamba" in entries else 0
        except (AttributeError, KeyError, TypeError):
            return None
        if (
            type(full_unit) is not int or full_unit <= 0
            or type(mamba_unit) is not int or mamba_unit < 0
        ):
            return None
        # Capture permits at most eight leaves. Each selected FULL path fits
        # the safe input and needs at most one state. Count shared paths again
        # for a conservative byte bound, independent of current residency.
        maximum_bytes = 8 * ((tokens[1] - 1) * full_unit + mamba_unit)
        ceiling = native_service_planning_ceiling(chain(
            self._native_service_samples, self._h2d_samples,
            (sample for _, sample in self._h2d_full_service_samples),
        ), maximum_bytes)
        if ceiling is None:
            return None
        service, enqueue = ceiling
        return max(
            self.prefetch_lead_ms,
            service + enqueue + SEMANTIC_FRAME_INTERVAL_MS,
        )

    @timed_runtime("join_report_prefetch")
    def _roll_report_service_prefetch(self) -> SessionH2DOpportunity | None:
        if (
            not (self.enable_final_stage_prefetch or self.enable_admission_prefetch)
            or self.physical_disabled or self._wait_prefetch_transfer_busy()
            or self._join_ticket is not None and self._live_join_ticket()
        ):
            return None
        now_ms = time.monotonic() * 1000.
        if now_ms < self._report_candidate_next_ms:
            return None
        selective = self.join_prefetch_trigger == "selective_report_or_eos"
        policy = self._selective_join_policy
        self._report_candidate_next_ms = now_ms + (
            policy.interval_ms if selective else SEMANTIC_FRAME_INTERVAL_MS
        )
        stages = self._next_report_stages() if selective else tuple(self._final_stages.values())
        for stage in stages:
            if stage.join_id in self._join_inflight_tickets:
                continue
            if selective:
                self.counts["selective_join_candidates_checked"] += 1
                if self._report_parent_full_upper_bytes(stage.key) < policy.min_full_bytes:
                    self.counts["selective_join_small_or_unknown_parent"] += 1
                    continue
            remaining = self._report_remaining_ms(stage)
            if remaining is None:
                continue
            if selective and remaining > 2 * self.prefetch_lead_ms:
                self.counts["report_service_planning_deferred"] += 1
                continue
            if remaining > 2 * self.prefetch_lead_ms:
                ceiling = self._report_planning_ceiling_ms(stage.key)
                if ceiling is not None and remaining > 2 * ceiling:
                    # Neither transfer nor the existing two-window capacity lead
                    # can start yet. Near stages still recapture every live page.
                    self.counts["report_service_planning_deferred"] += 1
                    continue
            if selective:
                self.counts["selective_join_native_plan_checks"] += 1
            observation = self._wait_prefetch_opportunity(
                stage.key, include_checkpoint_plan=True,
            )
            if observation is None:
                if selective:
                    self.counts["selective_join_no_host_restore"] += 1
                continue
            cost_plan = self._report_restore_cost_plan(observation)
            if cost_plan.step is None:
                if selective:
                    self.counts["selective_join_no_host_restore"] += 1
                continue
            missing_full_bytes = None
            if selective:
                entries = self._native_cache.cache_controller.mem_pool_host.entry_map
                missing_full_bytes = (
                    cost_plan.required_full_tokens * entries["kv"].host_pool.size_per_token
                )
                if missing_full_bytes < policy.min_full_bytes:
                    self.counts["selective_join_small_missing_full"] += 1
                    continue
            window = self._h2d_start_window(cost_plan)
            if selective:
                if window is None:
                    # Optional prediction needs a cost comparison. EOS never
                    # depends on this estimator or creates speculative reclaim.
                    self.counts["report_service_transfer_size_unsupported"] += 1
                    continue
                deficit = (
                    window.service_ms + window.enqueue_ms - self._join_eos_arrival_window_ms
                )
                if deficit <= 0 or self.prefetch_lead_ms <= 0:
                    self.counts["selective_join_eos_window_sufficient"] += 1
                    continue
                window = replace(
                    window,
                    horizon_ms=min(self.prefetch_lead_ms, deficit + policy.interval_ms),
                )
                budget = self._current_residency_budget(
                    include_evictable=True, headroom=getattr(cost_plan, "headroom", None),
                )
                if not self._prefetch_slot_available(stage.key, budget=budget):
                    self.counts["selective_join_admission_budget_busy"] += 1
                    continue
            if self._borrowable_parent_headroom and remaining <= 2 * (
                window.horizon_ms if window is not None else self.prefetch_lead_ms
            ):
                if not selective:
                    budget = self._current_residency_budget(
                        include_evictable=True, headroom=getattr(cost_plan, "headroom", None),
                    )
                # Reclaim enough for restoration and its native service reserve;
                # unsupported DMA timing must not prevent safe capacity preparation.
                self._set_critical_parent_demand(
                    stage.key, cost_plan.required_full_tokens + budget.reserve_full_tokens,
                    cost_plan.required_mamba_slots + budget.reserve_mamba_slots,
                    time.monotonic() + max(1., remaining / 1000.),
                )
            if window is None:
                self.counts["report_service_transfer_size_unsupported"] += 1
                continue
            if remaining > window.horizon_ms:
                continue
            if observation.step is None:
                self.counts["report_service_waiting_for_cold_capacity"] += 1
                continue
            if selective and observation.fits_current_free_lists is not True:
                self.counts["report_service_waiting_for_cold_capacity"] += 1
                continue
            join = self.graph.joins[stage.join_id]
            self._join_ticket = _JoinPrefetchTicket(
                stage.key, stage.join_id, join.mode.value,
                tuple(sorted(join.member_invocation_ids)), "provisional",
                min(stage.expires_at, time.monotonic() + max(2., remaining / 1000. + 1.)),
                issued_nodes=self._issued_join_nodes(stage.join_id, stage.key),
                stage_bound=True,
                work_endpoint_tokens=stage.generated_tokens
                + remaining * (self._semantic_rate(stage.request_id) or 0.) / 1000.,
                report_start_window_ms=window.horizon_ms if selective else None,
                report_service_shape=(
                    _WaitPrefetchServiceShape.from_opportunity(cost_plan) if selective else None
                ),
            )
            self.counts["report_service_h2d_candidate"] += 1
            if selective:
                self.counts["selective_join_h2d_candidate"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "final_stage_latest_start", "ts_ms": time.time() * 1000.,
                    "join_id": stage.join_id, "workflow_id": stage.key.root_workflow_id,
                    "context_id": stage.key.context_id, "context_epoch": stage.key.context_epoch,
                    "child_invocation_id": stage.child_id, "child_request_id": stage.request_id,
                    "trigger_kind": "report_observed_service",
                    "selective_report": selective,
                    "generated_tokens": stage.generated_tokens,
                    "expected_tokens": stage.expected_tokens, "remaining_ms": remaining,
                    "h2d_ms": window.service_ms, "start_window_ms": window.horizon_ms,
                    "enqueue_to_submit_p90_ms": window.enqueue_ms,
                    "prediction_early_ms": window.prediction_early_ms,
                    "prefetch_lead_ms": self.prefetch_lead_ms,
                    "service_sample_count": None,
                    "service_support": "current_size_and_pool_estimator",
                    "service_semantics": "controller_submit_to_ack; not exposed GPU wait",
                    "length_calibration_samples": len(self._report_work.ratios),
                    "required_full_tokens": observation.required_full_tokens,
                    "required_mamba_slots": observation.required_mamba_slots,
                    **({
                        "missing_checkpoint_full_bytes": missing_full_bytes,
                        "eos_to_native_arrival_window_ms": self._join_eos_arrival_window_ms,
                        "eos_to_native_arrival_samples": len(self._join_eos_arrival_samples),
                        "estimated_recovery_deficit_ms": deficit,
                        "prediction_policy": asdict(policy),
                    } if selective else {}),
                    "future_queueing_in_input": False,
                })
            return observation
        return None

    @staticmethod
    def _report_restore_cost_plan(opportunity):
        checkpoint_plan = getattr(opportunity, "checkpoint_plan", None)
        if checkpoint_plan is not None:
            return replace(
                opportunity,
                step=checkpoint_plan.steps[0] if checkpoint_plan.steps else None,
                steps=checkpoint_plan.steps,
                required_full_tokens=checkpoint_plan.full_tokens,
                required_mamba_slots=checkpoint_plan.mamba_slots,
            )
        candidate = getattr(opportunity, "candidate", None)
        if candidate is None:
            return opportunity
        steps = plan_prefetch_gpu_steps(candidate)
        if not steps:
            return opportunity
        nodes = {node.node_id: node for node in candidate.nodes}
        # Price the safe checkpoint before applying current free-list budgets.
        # This object is only a cost/capacity request, never an enqueue permit.
        return replace(
            opportunity, step=steps[0], steps=steps,
            required_full_tokens=sum(
                nodes[step.node_id].full_host_tokens
                for step in steps if nodes[step.node_id].full_device_tokens == 0
            ),
            required_mamba_slots=sum(
                step.include_mamba and nodes[step.node_id].mamba_host_present
                and not nodes[step.node_id].mamba_device_present for step in steps
            ),
        )

    def _observe_report_service(self, batch: object) -> None:
        if (
            not self._report_prediction_enabled
            or not self._final_request_stages
        ):
            return
        now_ms = time.monotonic() * 1000.
        if now_ms < self._report_sample_next_ms:
            return
        selective = self.join_prefetch_trigger == "selective_report_or_eos"
        self._report_sample_next_ms = now_ms + (
            self._selective_join_policy.interval_ms if selective else 100.
        )
        for request in batch.reqs:
            rid = request.rid
            stage = self._final_request_stages.get(rid)
            if stage is None or not self._live_final_stage(stage):
                continue
            if selective and (
                self._report_parent_full_upper_bytes(stage.key)
                < self._selective_join_policy.min_full_bytes
            ):
                continue
            outputs = getattr(request, "output_ids", ()) or ()
            scanned = self._decoded_scan_positions.get(rid, 0)
            self._decoded_scan_positions[rid] = len(outputs)
            if any(token in self._tool_open_token_ids for token in outputs[scanned:]):
                self._decoded_tool_requests.add(rid)
                self._clear_final_stage(stage.join_id)
                self.counts["report_service_native_tool_invalidated"] += 1
                continue
            if not outputs:
                continue
            key = self.visible.get(rid)
            if key is None:
                continue
            self._semantic_keys.setdefault(rid, key)
            progress = self._semantic_progress.setdefault(rid, deque(maxlen=64))
            if progress and len(outputs) <= progress[-1][1]:
                continue
            progress.append((now_ms, len(outputs)))
            stage.generated_tokens = len(outputs)
            stage.last_service_at = now_ms / 1000.
            stage.tokens_per_second = self._semantic_rate(rid)

    def _final_stage_service_available(
        self, stage: _ChildFinalStage, *, running_ids: set[str] | None = None,
    ) -> bool:
        batch = self._native_running_batch
        if batch is not None:
            if running_ids is not None:
                if stage.request_id not in running_ids:
                    return False
            elif not any(
                getattr(req, "rid", None) == stage.request_id
                for req in getattr(batch, "reqs", ())
            ):
                return False
        progress = self._semantic_progress.get(stage.request_id, ())
        if progress and time.monotonic() * 1000. - progress[-1][0] > max(
            2 * SEMANTIC_FRAME_INTERVAL_MS, min(250., self.prefetch_lead_ms),
            2 * self._selective_join_policy.interval_ms
            if self.join_prefetch_trigger == "selective_report_or_eos" else 0.,
        ):
            return False
        return True

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

    def _join_restore_known_empty(
        self, key: PrefillCandidateKey, *, input_tokens: int | None = None,
    ) -> bool:
        cache = self._native_cache
        if not callable(getattr(getattr(cache, "tree_core", None), "node_by_id", None)):
            return False
        if input_tokens is None:
            tokens = self._context_tokens.get(key.context_id)
            if tokens is not None and tokens[0] == key.context_epoch:
                input_tokens = max(0, tokens[1] - 1)
        anchors = self._snapshot_key_session_anchors(
            cache, key, reusable_input_tokens=input_tokens,
        )
        return anchors is not None and has_host_backed_missing_copy(
            cache, anchors, input_checkpoint_only=True,
        ) is False

    def _advance_join_ticket(
        self, event: RuntimeEvent, *,
        empty_restores: set[tuple[str, PrefillCandidateKey]] | None = None,
    ) -> None:
        ticket = self._join_ticket
        if ticket is not None and ticket.join_id in self._noncontinuing_joins:
            self._discard_join_ticket("noncontinuing_join")
            ticket = None
        if event.kind in (
            RuntimeEventKind.JOIN_TIMEOUT, RuntimeEventKind.INVOCATION_CANCEL,
            RuntimeEventKind.CONTEXT_COMPACT, RuntimeEventKind.CONTEXT_ADVANCE,
        ):
            if event.kind is RuntimeEventKind.JOIN_TIMEOUT:
                self._empty_join_reentry_origins.pop(event.join_id, None)
            else:
                context = self.graph.contexts.get(event.context_id)
                for invocation_id in (
                    context.invocation_ids if context is not None else (event.invocation_id,)
                ):
                    for join_id in self._join_by_invocation.get(invocation_id, ()):
                        self._empty_join_reentry_origins.pop(join_id, None)
            candidates = list(self._confirmed_join_tickets.values())
            candidates.extend(self._join_inflight_tickets.values())
            if ticket is not None and self._confirmed_join_tickets.get(ticket.join_id) is not ticket:
                if self._join_inflight_tickets.get(ticket.join_id) is not ticket:
                    candidates.append(ticket)
            for pending in candidates:
                if event.kind is RuntimeEventKind.JOIN_TIMEOUT and event.join_id == pending.join_id:
                    self._discard_join_ticket("join_timeout", ticket=pending)
                elif event.kind is RuntimeEventKind.INVOCATION_CANCEL and (
                    event.invocation_id == pending.key.invocation_id
                    or event.invocation_id in pending.member_ids
                ):
                    self._discard_join_ticket("invocation_canceled", ticket=pending)
                elif event.kind in (
                    RuntimeEventKind.CONTEXT_COMPACT, RuntimeEventKind.CONTEXT_ADVANCE,
                ) and (
                    event.context_id == pending.key.context_id
                    or event.invocation_id == pending.key.invocation_id
                ):
                    self._discard_join_ticket("event_invalidated", ticket=pending)
            return
        if (
            self.enable_admission_prefetch or self.enable_final_stage_prefetch
            or self.enable_confirmed_join_canary
        ) and event.kind in (
            RuntimeEventKind.RETURN, RuntimeEventKind.JOIN_SATISFIED,
        ):
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
                    or join.mode.value == "all" and any(
                        self.graph.invocations[member].state is InvocationState.CANCELLED
                        for member in join.member_invocation_ids
                    )
                ):
                    continue
                key = self._join_parent_key(join_id)
                parent = self.graph.invocations.get(key.invocation_id) if key else None
                if (
                    key is None or parent is None or parent.state is not InvocationState.READY
                    or parent.join_id != join_id
                ):
                    continue
                pending = self._confirmed_join_tickets.get(join_id)
                if pending is not None and pending.key == key:
                    continue
                inflight = self._join_inflight_tickets.get(join_id)
                pending = (
                    inflight if inflight is not None and inflight.key == key else
                    ticket if ticket is not None and (ticket.join_id, ticket.key) == (join_id, key)
                    else None
                )
                identity = join_id, key
                if empty_restores is not None and identity in empty_restores:
                    continue
                issued_nodes = self._issued_join_nodes(join_id, key)
                if not (
                    issued_nodes or pending is not None and (
                        pending.issued_nodes or pending.command_id or pending.command_ids
                    )
                ) and self._join_restore_known_empty(key):
                    if pending is not None:
                        self._discard_join_ticket("no_host_backed_missing_copy", ticket=pending)
                        if ticket is pending:
                            ticket = None
                    if empty_restores is not None:
                        empty_restores.add(identity)
                    # Identity only: recheck once on the next client submit.
                    # It holds no pages and is never polled by the selector.
                    self._empty_join_reentry_origins[join_id] = key
                    self.counts["confirmed_join_empty_restore_skipped"] += 1
                    continue
                if pending is None:
                    pending = _JoinPrefetchTicket(
                        key, join_id, join.mode.value,
                        tuple(sorted(join.member_invocation_ids)),
                        "confirmed", 0.,
                        issued_nodes=issued_nodes,
                    )
                # Keep any issued burst and its ACK identity across RETURN.
                pending.phase = "confirmed"
                pending.expires_at = time.monotonic() + 2.0
                if self._join_inflight_tickets.get(join_id) is pending:
                    del self._join_inflight_tickets[join_id]
                self._confirmed_join_tickets[join_id] = pending
                self._empty_join_reentry_origins.pop(join_id, None)
                if self._join_ticket is None:
                    self._join_ticket = pending
                self.counts["join_reentry_confirmed"] += 1
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "confirmed_join_ticket",
                        "ts_ms": time.time() * 1000.0,
                        "join_id": join_id,
                        "workflow_id": key.root_workflow_id,
                        "context_id": key.context_id,
                        "context_epoch": key.context_epoch,
                        "session_id": key.session_id,
                        "session_generation": key.session_generation,
                        "confirmation_event_id": event.event_id,
                        "issued_nodes": pending.issued_nodes,
                        "pre_boundary_prediction": False,
                    })
        if (
            ticket is not None and ticket.phase != "confirmed"
            and event.kind is RuntimeEventKind.RETURN
            and event.invocation_id in ticket.member_ids
        ):
            ticket.phase = "probabilistic"

    def _bind_confirmed_join_submit(
        self, event: RuntimeEvent, *,
        origin: tuple[PrefillCandidateKey, int, bool] | None,
        confirmed_in_batch: set[str],
    ) -> None:
        if (
            not (self.enable_admission_prefetch or self.enable_final_stage_prefetch)
            or not self._continuing_joins or event.attributes.get("runtime_internal")
        ):
            return
        parent = self.graph.invocations.get(event.invocation_id)
        if parent is not None:
            self._empty_join_reentry_origins.pop(parent.join_id, None)
        pending = self._confirmed_join_tickets.get(parent.join_id) if parent else None
        rid = event.attributes.get("request_id")
        if pending is not None and pending.client_request_id is not None:
            if pending.client_request_id != rid or pending.client_submit_ts_ms != event.ts_ms:
                self._discard_join_ticket("client_submit_changed", ticket=pending)
            return
        join = self.graph.joins.get(parent.join_id) if parent else None
        if origin is None or join is None:
            return
        key, input_tokens, confirmed_before_submit = origin
        if (
            join.join_id not in self._continuing_joins or not join.satisfied
            or join.join_id in self._noncontinuing_joins
            or join.workflow_id != key.root_workflow_id
            or key.invocation_id not in join.waiter_invocation_ids
            or join.mode.value == "all" and any(
                self.graph.invocations[member].state is InvocationState.CANCELLED
                for member in join.member_invocation_ids
            )
            or type(rid) is not str or not rid or rid == key.request_id
            or event.workflow_id != key.root_workflow_id
            or event.invocation_id != key.invocation_id
            or event.context_id != key.context_id
            or event.context_epoch != key.context_epoch + 1
            or parent.state is not InvocationState.RUNNING_LLM
            or parent.updated_ts_ms != event.ts_ms
            or parent.active_tool_calls or parent.blocking_child_ids
            or self.context_sessions.get(key.context_id) is not None
            or key.session_id is None or key.session_generation is None
            or type(input_tokens) is not int or input_tokens <= 0
        ):
            if pending is not None:
                self._discard_join_ticket("client_submit_identity_unproven", ticket=pending)
            return
        if pending is not None and pending.key != key:
            return
        issued_nodes = self._issued_join_nodes(join.join_id, key)
        if pending is None:
            if join.join_id not in confirmed_in_batch and not confirmed_before_submit:
                return
            pending = self._join_inflight_tickets.get(join.join_id)
            if pending is not None and pending.key != key:
                pending = None
        if not (
            issued_nodes or pending is not None and (
                pending.issued_nodes or pending.command_id or pending.command_ids
            )
        ) and self._join_restore_known_empty(key, input_tokens=input_tokens):
            if pending is not None:
                self._discard_join_ticket("no_host_backed_missing_copy", ticket=pending)
            self.counts["confirmed_join_empty_restore_skipped"] += 1
            return
        if join.join_id not in self._confirmed_join_tickets:
            if pending is not None:
                del self._join_inflight_tickets[join.join_id]
                pending.phase = "confirmed"
                pending.expires_at = time.monotonic() + 2.
            else:
                pending = _JoinPrefetchTicket(
                    key, join.join_id, join.mode.value, tuple(sorted(join.member_invocation_ids)),
                    "confirmed", time.monotonic() + 2.,
                    issued_nodes=issued_nodes,
                )
            self._confirmed_join_tickets[join.join_id] = pending
            self.counts["join_reentry_confirmed"] += 1
        if pending.phase != "confirmed" or pending.key != key:
            return
        pending.checkpoint_key = key
        pending.checkpoint_input_tokens = input_tokens
        pending.key = replace(key, request_id=rid, context_epoch=event.context_epoch)
        pending.client_request_id = rid
        pending.client_submit_ts_ms = event.ts_ms
        if self._join_ticket is None:
            self._join_ticket = pending
        self.counts["confirmed_join_client_bound"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "confirmed_join_client_bound",
                "ts_ms": time.time() * 1000.,
                "workflow_id": key.root_workflow_id,
                "join_id": pending.join_id,
                "invocation_id": key.invocation_id,
                "context_id": key.context_id,
                "context_epoch": key.context_epoch,
                "target_context_epoch": event.context_epoch,
                "request_id": rid,
                "scope": "old safe input checkpoint; submitted consumer not native-visible",
            })

    def _live_join_ticket(self, ticket: _JoinPrefetchTicket | None = None) -> bool:
        ticket = ticket if ticket is not None else self._join_ticket
        if ticket is None or time.monotonic() >= ticket.expires_at:
            return False
        key = ticket.key
        join = self.graph.joins.get(ticket.join_id)
        parent = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        return bool(
            (
                self.enable_admission_prefetch
                or self.enable_final_stage_prefetch
                and (ticket.stage_bound or ticket.phase == "confirmed")
                or self.enable_confirmed_join_canary and ticket.phase == "confirmed"
            ) and not self.physical_disabled
            and ticket.join_id not in self._noncontinuing_joins
            and join is not None and join.workflow_id == key.root_workflow_id
            and join.mode.value == ticket.join_mode
            and tuple(sorted(join.member_invocation_ids)) == ticket.member_ids
            and key.invocation_id in join.waiter_invocation_ids
            and context is not None and context.epoch == key.context_epoch
            and parent is not None and parent.context_id == key.context_id
            and parent.workflow_id == key.root_workflow_id
            and parent.join_id == ticket.join_id
            and (
                self.context_sessions.get(key.context_id) == key and parent.state is (
                    InvocationState.READY if ticket.phase == "confirmed"
                    else InvocationState.WAIT_JOIN
                ) and ticket.client_request_id is None
                or ticket.phase == "confirmed"
                and ticket.join_id in self._continuing_joins
                and ticket.client_request_id == key.request_id
                and (checkpoint := ticket.checkpoint_key) is not None
                and checkpoint.request_id != key.request_id
                and checkpoint.context_epoch + 1 == key.context_epoch
                and checkpoint.root_workflow_id == key.root_workflow_id
                and checkpoint.invocation_id == key.invocation_id
                and checkpoint.context_id == key.context_id
                and checkpoint.session_id == key.session_id
                and checkpoint.session_generation == key.session_generation
                and type(ticket.checkpoint_input_tokens) is int
                and ticket.checkpoint_input_tokens > 0
                and self.context_sessions.get(key.context_id) is None
                and ticket.client_submit_ts_ms == parent.updated_ts_ms
                and parent.state is InvocationState.RUNNING_LLM
                and not parent.active_tool_calls and not parent.blocking_child_ids
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

    def _pending_confirmed_join_ticket(
        self, context_id: str, context_epoch: int,
    ) -> _JoinPrefetchTicket | None:
        context = self.graph.contexts.get(context_id)
        if context is None:
            return None
        for invocation_id in context.invocation_ids:
            invocation = self.graph.invocations.get(invocation_id)
            ticket = (
                self._confirmed_join_tickets.get(invocation.join_id)
                if invocation is not None else None
            )
            if (
                ticket is not None and ticket.phase == "confirmed"
                and ticket.client_request_id is not None
                and ticket.key.context_id == context_id and ticket.key.context_epoch == context_epoch
                and self._live_join_ticket(ticket)
            ):
                return ticket
        return None

    def _pending_confirmed_join_submit(self, key: PrefillCandidateKey) -> bool:
        ticket = self._pending_confirmed_join_ticket(key.context_id, key.context_epoch)
        return ticket is not None and ticket.key == key

    def _physical_actions_completed(
        self, commands: Sequence[str], *, action: str | None = None,
    ) -> bool:
        remaining = set(commands)
        if not remaining:
            return True
        # A burst is normally reconciled just after its ACK. Do not rebuild
        # the whole bounded history when its newest receipts prove completion.
        for completed in reversed(self.completed_physical_actions):
            if (
                (action is None or completed.action == action)
                and completed.command_id in remaining
            ):
                remaining.remove(completed.command_id)
                if not remaining:
                    return True
        return False

    def _reconcile_join_prefetch_ticket(self, ticket: _JoinPrefetchTicket) -> bool | None:
        commands = ticket.command_ids or (ticket.command_id,)
        if any(self.physical_ledger.is_pending(command) for command in commands):
            return None
        if not self._physical_actions_completed(commands, action="PREFETCH_GPU"):
            self._discard_join_ticket("action_ack_missing", ticket=ticket)
            self.counts["join_prefetch_lost_ack"] += 1
            return False
        ticket.command_id = None
        ticket.command_ids = ()
        self.counts["join_prefetch_acked"] += len(commands)
        return True

    def _select_confirmed_join_ticket(self) -> None:
        if not self._confirmed_join_tickets:
            return
        parallel = self._parallel_wait_prefetch and self.can_prefetch_during_overlap()
        if parallel and self._wait_prefetch_transfer_busy():
            return
        ticket = self._join_ticket
        if ticket is not None and not ticket.restoration_complete and self._live_join_ticket(ticket) and (
            ticket.phase == "confirmed" or ticket.command_id is not None
        ):
            if (
                ticket.phase != "confirmed" or ticket.command_id is None
                or not parallel
            ):
                return
            # The confirmation map owns this burst while the selector services
            # another parent. The ledger still owns its bytes and native nodes.
            self._join_ticket = None
        if ticket is not None and ticket.restoration_complete:
            self._join_ticket = None
        for pending in tuple(self._confirmed_join_tickets.values()):
            if self._live_join_ticket(pending):
                if pending.command_id is not None and (
                    self._reconcile_join_prefetch_ticket(pending) is not True
                ):
                    continue
                if pending.restoration_complete:
                    continue
                self._join_ticket = pending
                return
            else:
                self._discard_join_ticket(
                    "expired" if time.monotonic() >= pending.expires_at
                    else "confirmed_reentry_invalidated",
                    ticket=pending,
                )

    def _yield_confirmed_join_ticket(self) -> None:
        ticket = self._join_ticket
        if (
            ticket is not None and ticket.phase == "confirmed"
            and len(self._confirmed_join_tickets) > 1
        ):
            self._confirmed_join_tickets.pop(ticket.join_id, None)
            self._confirmed_join_tickets[ticket.join_id] = ticket
            self._join_ticket = None

    def _try_eos_after_blocked_confirmed(self, ticket: _JoinPrefetchTicket) -> None:
        if (
            ticket.phase != "confirmed" or ticket.command_id is not None
            or not self._eos_prefetch_keys
            or self._join_ticket is not None and self._join_ticket is not ticket
        ):
            return
        # Keep the confirmed ticket in its map for later demand recovery. It
        # cannot issue now; give an observed EOS one attempt in this safe point.
        self._join_ticket = None
        self.counts["confirmed_join_yielded_to_observed_eos"] += 1
        self.dispatch_join_prefetch(
            _retry_client_submit=False, _prefer_observed_eos=True,
        )

    @timed_runtime("join_prefetch")
    def dispatch_join_prefetch(
        self, *, _retry_client_submit: bool = True, _prefer_observed_eos: bool = False,
    ) -> None:
        """Restore a bounded reusable prefix burst, without granting admission."""
        if self._join_inflight_tickets:
            if self._wait_prefetch_transfer_busy():
                return
            for join_id, pending in tuple(self._join_inflight_tickets.items()):
                if self._reconcile_join_prefetch_ticket(pending) is not None:
                    self._join_inflight_tickets.pop(join_id, None)
        ticket = self._join_ticket
        if (
            ticket is not None and ticket.phase == "provisional"
            and ticket.command_id is not None
            and self._parallel_wait_prefetch and self.can_prefetch_during_overlap()
            and any(self.physical_ledger.is_pending(command) for command in (
                ticket.command_ids or (ticket.command_id,)
            ))
        ):
            self._join_inflight_tickets[ticket.join_id] = ticket
            self._join_ticket = None
        if not _prefer_observed_eos:
            self._select_confirmed_join_ticket()
        observation = self._roll_final_stage()
        ticket = self._join_ticket
        if ticket is None or not self._live_join_ticket():
            if ticket is not None:
                self._discard_join_ticket(
                    "expired" if time.monotonic() >= ticket.expires_at
                    else "dispatch_invalidated"
                )
            return
        if ticket.command_id is not None:
            if self._reconcile_join_prefetch_ticket(ticket) is not True:
                return
        ticket.issued_nodes = max(
            ticket.issued_nodes, self._issued_join_nodes(ticket.join_id, ticket.key),
        )
        if (
            self.enable_confirmed_join_canary and ticket.issued_nodes >= 1
            or self._wait_prefetch_transfer_busy(ticket.key)
        ):
            return
        if ticket.phase != "confirmed" and not (
            self._join_ticket_has_native_eos(ticket)
            or self._join_ticket_has_report_service(ticket)
        ):
            self._discard_join_ticket("native_eos_required")
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
        # Only this synchronous call's plan may be reused after an empty drain.
        if observation is None:
            observation = self._wait_prefetch_opportunity(ticket.key)
        service_shape = (
            _WaitPrefetchServiceShape.from_opportunity(observation)
            if observation is not None else None
        )
        if (
            observation is not None and observation.step is not None
            and callable(getattr(self._native_cache, "prefetch_gpu_session_nodes", None))
            and not self._wait_prefetch_timing_valid(ticket.key, observation, "join_ticket")
        ):
            self._discard_join_ticket("batch_start_window_changed")
            return
        if observation is not None and observation.step is not None:
            if self._agent_admission is not None and not self._agent_admission.claim_restore(
                ticket.key, source="join_ticket",
            ):
                self.counts["agent_restore_deferred_before_reclaim"] += 1
                return
            observation = self._reclaim_wait_prefetch_capacity(
                ticket.key, observation, source="join_ticket",
            )
        step = observation.step if observation is not None else None
        if step is None:
            self.counts["join_prefetch_no_cpu_node"] += 1
            self._record_wait_prefetch_plan(
                "join_ticket", ticket.key, observation, reason="no_restore_plan",
            )
            if (
                self.enable_confirmed_join_canary
                and not ticket.no_step_recorded
                and self._opportunity_writer is not None
            ):
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
            remaining_restore = observation is not None and bool(
                getattr(observation, "host_backed_full_missing_device_tokens", 0)
                or getattr(observation, "host_backed_mamba_missing_device_nodes", 0)
                or getattr(observation, "no_step_reason", None) == "prefetch_capacity_budget"
            )
            if ticket.issued_nodes and not remaining_restore:
                if ticket.client_request_id is not None:
                    # Keep the bound consumer for residency validation until
                    # native arrival, without blocking another JOIN restore.
                    ticket.restoration_complete = True
                    self._join_ticket = None
                else:
                    self._discard_join_ticket("prefix_resident_or_unavailable")
            else:
                self._yield_confirmed_join_ticket()
            if not _prefer_observed_eos:
                self._try_eos_after_blocked_confirmed(ticket)
            return
        if observation.fits_current_free_lists is not True:
            self.counts["semantic_h2d_no_free_capacity"] += 1
            self._record_wait_prefetch_plan(
                "join_ticket", ticket.key, observation, reason="no_cold_capacity",
            )
            self._yield_confirmed_join_ticket()
            if not _prefer_observed_eos:
                self._try_eos_after_blocked_confirmed(ticket)
            return
        selected_key = ticket.key
        commands = self._issue_wait_prefetch_steps(
            selected_key, getattr(observation, "steps", ()) or (step,),
            source="join_ticket", service_shape=service_shape,
            _selection_opportunity=observation,
        )
        if commands:
            ticket.command_id = commands[-1]
            ticket.command_ids = commands
            ticket.issued_nodes += len(commands)
            self._join_prefetch_issued[
                _JoinPrefetchIdentity.from_parent(ticket.join_id, ticket.key)
            ] = ticket.issued_nodes
            stage = self._final_stages.get(ticket.join_id)
            if stage is not None and ticket.phase == "provisional":
                stage.issued_nodes = ticket.issued_nodes
            self.counts[f"join_prefetch_{ticket.phase}_issued"] += len(commands)
        elif (
            _retry_client_submit and self._join_ticket is ticket
            and ticket.phase == "confirmed" and ticket.key != selected_key
            and ticket.checkpoint_key == selected_key and self._live_join_ticket(ticket)
        ):
            # Submission can rebind this ticket during the final event drain.
            # Replan once for the proven consumer; never reuse the old steps.
            self.counts["join_prefetch_client_handoff_replanned"] += 1
            self.dispatch_join_prefetch(_retry_client_submit=False)
        else:
            self._yield_confirmed_join_ticket()
            if not _prefer_observed_eos:
                self._try_eos_after_blocked_confirmed(ticket)

    def _submit_tool_wait(self, context_id: str) -> None:
        if self._online_tool_timing is not None:
            self._submit_online_tool_wait(context_id)
            return
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

    def _submit_online_tool_wait(self, context_id: str) -> None:
        key = self.context_sessions.get(context_id)
        invocation = self.graph.invocations.get(key.invocation_id) if key else None
        if (
            key is None or invocation is None or self._terminal(key)
            or invocation.state is not InvocationState.WAIT_TOOL
            or len(invocation.active_tool_calls) != 1
        ):
            self.tool_wait_hints.pop(context_id, None)
            return
        now_ms = time.monotonic() * 1000.
        revision = self._online_tool_timing.estimate_revision(
            key.root_workflow_id, key.invocation_id,
        )
        previous = self._online_tool_queries.get(key)
        if previous is not None and previous[:2] == (invocation.updated_ts_ms, revision) and now_ms < previous[2]:
            return
        estimate = self._online_tool_timing.estimate(
            key.root_workflow_id, key.invocation_id, now_ms=now_ms,
        )
        spacing = 1000. if estimate is None or estimate.p50_ms > 10_000 else 500. if estimate.p50_ms > 2000 else 150.
        self._online_tool_queries[key] = (invocation.updated_ts_ms, revision, now_ms + spacing)
        if estimate is None:
            self.tool_wait_hints.pop(context_id, None)
            self.counts["online_tool_cold_or_tail_unsupported"] += 1
            return
        hint = NativeToolWaitHint(
            key, estimate.p10_ms, estimate.p50_ms, estimate.p90_ms,
            now_ms, now_ms + max(1000., 2 * spacing),
            self.predictor_sha256, invocation.updated_ts_ms,
            release_cdf=estimate.release_cdf,
            residual_support_detail=f"online_{estimate.scope}_command",
        )
        self.counts["online_tool_supported"] += 1
        self.counts[f"online_tool_{estimate.scope}_samples"] = estimate.samples
        self._accept_tool_wait(hint)

    def _update_active_tool_census(
        self, deltas: Sequence[GraphDelta], *, previous_version: int,
    ) -> set[str] | None:
        if self._active_tool_census_version != previous_version:
            return
        affected = set()
        for delta in deltas:
            affected.update(delta.awakened_invocations)
            affected.update(delta.parked_invocations)
            affected.update(delta.completed_invocations)
            for context_id in delta.changed_contexts:
                context = self.graph.contexts.get(context_id)
                if context is not None:
                    affected.update(context.invocation_ids)
            # A blocked caller can resume WAIT_TOOL without being awakened.
            for invocation_id in delta.completed_invocations:
                invocation = self.graph.invocations.get(invocation_id)
                if invocation is not None:
                    parent_id = invocation.return_target_id or invocation.parent_invocation_id
                    if parent_id is not None:
                        affected.add(parent_id)
        for invocation_id in affected:
            invocation = self.graph.invocations.get(invocation_id)
            family = (
                invocation.active_tool_family
                if invocation is not None and invocation.state is InvocationState.WAIT_TOOL
                else None
            )
            waiting = invocation is not None and invocation.state is InvocationState.WAIT_TOOL
            prior_waiting = invocation_id in self._active_tool_census_invocations
            prior_family = self._active_tool_census_invocations.get(invocation_id)
            if waiting == prior_waiting and family == prior_family:
                continue
            if prior_waiting:
                self._active_tool_census[prior_family] -= 1
                if not self._active_tool_census[prior_family]:
                    del self._active_tool_census[prior_family]
                del self._active_tool_census_invocations[invocation_id]
            if waiting:
                self._active_tool_census[family] += 1
                self._active_tool_census_invocations[invocation_id] = family
        self._active_tool_census_version = self.graph.graph_version
        return affected

    @timed_runtime("tool_features")
    def _local_frontier_features(
        self, invocation: object, context_epoch: int, *, now_ms: float
    ) -> LocalFrontierFeatures:
        stored = self._context_tokens.get(invocation.context_id)
        prompt, output, stored_child = (
            (stored[1], stored[2], stored[3])
            if stored is not None and stored[0] == context_epoch
            else (0, 0, False)
        )
        if self._active_tool_census_version != self.graph.graph_version:
            self._active_tool_census_invocations = {
                item.invocation_id: item.active_tool_family
                for item in self.graph.invocations.values()
                if item.state is InvocationState.WAIT_TOOL
            }
            self._active_tool_census = Counter(self._active_tool_census_invocations.values())
            self._active_tool_census_version = self.graph.graph_version
        family = invocation.active_tool_family or "unknown"
        family_count = self._active_tool_census[family]
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
            active_tool_count=sum(self._active_tool_census.values()),
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
            or self._model_worker is None and self._online_tool_timing is None
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
        if self.tool_wait_hints.get(key.context_id) is not hint:
            self._tool_prefetch_next_ms = 0.
        self.tool_wait_hints[key.context_id] = hint
        self._refresh_prefetch_service_leases(context_id=key.context_id)
        self.counts["tool_wait_accepted"] += 1
        if self._tool_timing_only or self.tool_restore_mode == "execution":
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
                "invocation_revision_ts_ms": hint.invocation_revision_ts_ms,
                "p10_ms": hint.wait_p10_ms, "p50_ms": hint.wait_p50_ms,
                "p90_ms": hint.wait_p90_ms, "release_cdf": hint.release_cdf,
                "tool_offload_wait_ms": TOOL_OFFLOAD_WAIT_MS,
                "residual_duration_mass": hint.residual_duration_mass,
                "residual_support_detail": hint.residual_support_detail,
                "conditional_p50_ms": hint.remaining_quantile(
                    .5, now_ms=time.monotonic() * 1000,
                ),
                "timing_policy": (
                    "survival_conditioned_completed_history"
                    if hint.residual_duration_mass
                    else "survival_conditioned_cdf_or_unexpired_quantile"
                ),
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
        if not self._custom_kv_management:
            raise PhysicalReceiptError("custom KV actions disabled by the scheduling-only comparison")
        context = self.graph.contexts.get(expected.context_id)
        if (
            self.physical_disabled or context is None
            or context.epoch != expected.context_epoch
        ):
            raise PhysicalReceiptError("physical action has no live causal context")
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
        ticket = self._join_ticket
        confirmed_join = (
            not session_waiting and expected.action == "PREFETCH_GPU"
            and ticket is not None and ticket.phase == "confirmed"
            and ticket.key.context_id == expected.context_id
            and ticket.key.context_epoch == expected.context_epoch
            and ticket.key.session_id == expected.session_id
            and ticket.key.session_generation == expected.session_generation
            and self._live_join_ticket(ticket)
        )
        visible = not (session_waiting or confirmed_join) and any(
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
        if not (visible or session_waiting or confirmed_join):
            raise PhysicalReceiptError("physical action has no live causal context")
        self.physical_ledger.register(expected)

    def snapshot_session_anchors(
        self, cache: object, *, context_id: str, context_epoch: int
    ) -> ContextSessionAnchors | None:
        """Request-local lookup for a finished tool-waiting context."""
        key = self.context_sessions.get(context_id)
        pending = (
            self._pending_confirmed_join_ticket(context_id, context_epoch)
            if key is None else None
        )
        if pending is not None:
            key = pending.key
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
        return self._snapshot_key_session_anchors(
            cache, key,
            reusable_input_tokens=(
                pending.checkpoint_input_tokens if pending is not None else (
                    max(0, tokens[1] - 1)
                    if (tokens := self._context_tokens.get(context_id)) is not None
                    and tokens[0] == key.context_epoch else None
                )
            ),
        )

    @staticmethod
    def _snapshot_key_session_anchors(
        cache: object, key: PrefillCandidateKey, *,
        reusable_input_tokens: int | None,
    ) -> ContextSessionAnchors | None:
        if key.session_id is None or key.session_generation is None:
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
            reusable_input_tokens=reusable_input_tokens,
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
        if not self._record_terminal_cache(watch):
            self._terminal_cache_watches.pop(scope, None)
            self.counts["terminal_cache_watch_anchors_gone"] += 1

    def _remember_terminal_root(self, key: PrefillCandidateKey, event: RuntimeEvent) -> None:
        if not self._custom_kv_management:
            return
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            invocation is None or context is None
            or invocation.relation_type is not RelationType.ROOT
            or invocation.parent_invocation_id is not None
            or invocation.state is not InvocationState.DONE
            or invocation.workflow_id != key.root_workflow_id
            or context.workflow_id != key.root_workflow_id
            or context.epoch != key.context_epoch
            or key.session_id is None or key.session_generation is None
        ):
            return
        identity = key.root_workflow_id, key.context_id
        self._terminal_root_contexts[identity] = _TerminalChildReclaim(
            key, None, None, event.ts_ms, time.time() * 1000.,
            invocation_scope="workflow_root",
        )
        self._terminal_root_contexts.move_to_end(identity)
        if len(self._terminal_root_contexts) > 512:
            self._terminal_root_contexts.popitem(last=False)
            self.counts["terminal_root_provenance_expired"] += 1

    def _retire_ended_workflow_roots(self, workflow_id: str) -> None:
        if not self._custom_kv_management:
            return
        workflow = self.graph.workflows.get(workflow_id)
        retire = getattr(self._native_cache, "retire_beliefkv_child_session", None)
        if workflow is None or workflow.end_ts_ms is None or not callable(retire):
            return
        for identity, reclaim in tuple(self._terminal_root_contexts.items()):
            key = reclaim.key
            if key.root_workflow_id != workflow_id:
                continue
            del self._terminal_root_contexts[identity]
            invocation = self.graph.invocations.get(key.invocation_id)
            context = self.graph.contexts.get(key.context_id)
            if (
                invocation is None or invocation.state is not InvocationState.DONE
                or context is None or context.epoch != key.context_epoch
                or context.workflow_id != workflow_id
                or any(
                    other.root_workflow_id != workflow_id
                    and (other.session_id, other.session_generation)
                    == (key.session_id, key.session_generation)
                    and not self._terminal(other)
                    for other in self.context_sessions.values()
                )
            ):
                self.counts["terminal_root_retirement_invalidated"] += 1
                continue
            # Session close alone retains cached KV. Native releases only
            # unreferenced leaves and retries locked/in-flight suffixes.
            self._apply_terminal_child_reclaim(reclaim, retry=False)

    def _retire_terminal_child(self, key: PrefillCandidateKey, event: RuntimeEvent) -> None:
        if not self._custom_kv_management or not self.enable_terminal_child_reclaim:
            return
        child = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        retire = getattr(self._native_cache, "retire_beliefkv_child_session", None)
        if (
            child is None or child.parent_invocation_id is None or context is None
            or (
                (child.persistent or context.persistent)
                and event.attributes.get("context_retired") is not True
            )
            or any(
                invocation_id != key.invocation_id
                and not self.graph.invocations[invocation_id].state.terminal
                for invocation_id in context.invocation_ids
            )
            or child.state is not InvocationState.DONE
            or key.session_id is None or key.session_generation is None
            or not callable(retire)
        ):
            return
        observed_return_wall_ts_ms = time.time() * 1000.
        parent_context, join_id = None, None
        for jid in sorted(self._join_by_invocation.get(child.invocation_id, ())):
            join = self.graph.joins.get(jid)
            parent_key = self._join_parent_key(jid)
            if parent_key is None and join is not None:
                parent = self.graph.invocations.get(child.parent_invocation_id)
                candidate = self.context_sessions.get(parent.context_id) if parent else None
                parent_record = self.graph.contexts.get(parent.context_id) if parent else None
                if (
                    candidate is not None and parent is not None and not parent.state.terminal
                    and parent.invocation_id in join.waiter_invocation_ids
                    and parent_record is not None
                    and parent_record.epoch == candidate.context_epoch + 1
                    and candidate.invocation_id == parent.invocation_id
                    and candidate.session_id is not None and candidate.session_generation is not None
                ):
                    parent_key = candidate
            if (
                join is None or parent_key is None
                or parent_key.invocation_id != child.parent_invocation_id
                or jid in self._noncontinuing_joins
            ):
                continue
            parent_context, join_id = parent_key.context_id, jid
            handoff = self._parent_capacity_handoffs.get(parent_context)
            if handoff is None or (
                handoff.join_id != jid
                or handoff.key.invocation_id != parent_key.invocation_id
                or handoff.key.session_id != parent_key.session_id
                or handoff.key.session_generation != parent_key.session_generation
                or parent_key.context_epoch not in (
                    handoff.key.context_epoch, handoff.key.context_epoch + 1,
                )
            ):
                handoff = _ParentCapacityHandoff(parent_key, jid, time.monotonic())
                self._parent_capacity_handoffs[parent_context] = handoff
            handoff.child_ids.add(child.invocation_id)
            handoff.last_return_ts_ms = max(
                handoff.last_return_ts_ms, observed_return_wall_ts_ms,
            )
            break
        reclaim = _TerminalChildReclaim(
            key, parent_context, join_id, event.ts_ms, observed_return_wall_ts_ms,
            invocation_scope=(
                "runtime_internal" if event.attributes.get("runtime_internal") is True
                else "dynamic_subagent"
                if child.relation_type is RelationType.SPAWN or join_id is not None
                else "other"
            ),
        )
        self._apply_terminal_child_reclaim(reclaim, retry=False)

    @timed_runtime("terminal_child_reclaim")
    def _apply_terminal_child_reclaim(
        self, reclaim: _TerminalChildReclaim, *, retry: bool,
    ) -> None:
        if not self._custom_kv_management:
            return
        key = reclaim.key
        result = self._native_cache.retire_beliefkv_child_session(
            session_id=key.session_id, session_generation=key.session_generation,
            **({"retry_anchors": reclaim.retry_anchors, "max_nodes": 16} if retry else {}),
        )
        device = result["freed_device_units"]
        host = result["freed_host_units"]
        handoff = self._parent_capacity_handoffs.get(reclaim.parent_context_id)
        if handoff is not None and handoff.join_id == reclaim.join_id:
            handoff.freed_device_units.update(device)
            handoff.freed_host_units.update(host)
        counter = "terminal_root" if reclaim.invocation_scope == "workflow_root" else "terminal_child"
        self.counts[f"{counter}_reclaim_calls"] += 1
        self.counts[f"{counter}_discarded_nodes"] += len(result["discarded_nodes"])
        self.counts[f"{counter}_freed_full_tokens"] += device.get(0, 0)
        self.counts[f"{counter}_freed_mamba_slots"] += device.get(2, 0)
        reclaim.retry_anchors = result["retry_anchors"]
        identity = (key.session_id, key.session_generation)
        if reclaim.retry_anchors:
            self._terminal_child_reclaims[identity] = reclaim
            self._terminal_child_reclaims.move_to_end(identity)
            if len(self._terminal_child_reclaims) > 512:
                self._terminal_child_reclaims.popitem(last=False)
                self.counts["terminal_child_reclaim_provenance_expired"] += 1
        else:
            self._terminal_child_reclaims.pop(identity, None)
        if self._opportunity_writer is not None and (not retry or device or host):
            entries = getattr(getattr(self._native_cache, "host_pool_group", None), "entry_map", {})
            bytes_per_unit = {
                int(component): entry.host_pool.size_per_token
                for component, pool in ((0, "kv"), (2, "mamba"))
                if (entry := entries.get(pool)) is not None
            }
            self._opportunity_writer.record({
                "event": (
                    "terminal_workflow_root_cache_reclaimed"
                    if reclaim.invocation_scope == "workflow_root"
                    else "terminal_child_cache_reclaimed"
                ), "ts_ms": time.time() * 1000.,
                "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
                "invocation_scope": reclaim.invocation_scope,
                "context_id": key.context_id, "context_epoch": key.context_epoch,
                "session_id": key.session_id, "session_generation": key.session_generation,
                "parent_context_id": reclaim.parent_context_id, "join_id": reclaim.join_id,
                "child_return_event_ts_ms": reclaim.returned_ts_ms,
                "child_return_observed_wall_ts_ms": reclaim.observed_return_wall_ts_ms,
                "retry": retry,
                "freed_device_units": device, "freed_host_units": host,
                "bytes_per_unit": bytes_per_unit,
                "discarded_nodes": result["discarded_nodes"],
                "pending_anchors": len(reclaim.retry_anchors), "reason": result["reason"],
                "semantics": (
                    "Native allocator release of unreferenced terminal suffixes, no D2H; "
                    "shared ancestors remain. Released capacity is not reserved or proof "
                    "of an exclusive physical-page handoff to the parent."
                ),
            })
        if device:
            self._reentry_observations.clear()
            self._residency_ordering_hints.clear()
            self._execution_handoff_next_ms = 0.
            if handoff is not None and handoff.join_id == reclaim.join_id:
                self._invalidate_wait_transfer_observation(handoff.key)
                self._rearm_parent_capacity_restore(handoff)

    def _rearm_parent_capacity_restore(self, handoff: _ParentCapacityHandoff) -> None:
        for key in tuple(self._execution_handoff_capacity_blocked):
            if (
                key.context_id != handoff.key.context_id
                or self.visible.get(key.request_id) != key
                or self.context_sessions.get(key.context_id) != key
                or self._live_parent_capacity_handoff(key) is not handoff
            ):
                continue
            self._execution_handoff_capacity_blocked.discard(key)
            self._execution_handoff_attempted.discard(key)
            self.counts["parent_capacity_handoff_restore_rearmed"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "parent_capacity_handoff_restore_rearmed",
                    "ts_ms": time.time() * 1000.,
                    "workflow_id": key.root_workflow_id,
                    "context_id": key.context_id, "context_epoch": key.context_epoch,
                    "request_id": key.request_id, "join_id": handoff.join_id,
                    "reason": "linked_terminal_child_device_capacity_released",
                    "scope": "retry capacity-blocked demand restore; not predictive H2D",
                })

    def _retry_terminal_child_reclaims(self, *, now_ms: float) -> None:
        self._terminal_child_reclaim_next_ms = now_ms + 50.
        for identity in tuple(self._terminal_child_reclaims)[:4]:
            reclaim = self._terminal_child_reclaims.get(identity)
            if reclaim is not None:
                self._apply_terminal_child_reclaim(reclaim, retry=True)

    def _live_parent_capacity_handoff(
        self, key: PrefillCandidateKey,
    ) -> _ParentCapacityHandoff | None:
        handoff = self._parent_capacity_handoffs.get(key.context_id)
        if handoff is None:
            return None
        target = handoff.key
        join = self.graph.joins.get(handoff.join_id)
        parent = self.graph.invocations.get(key.invocation_id)
        valid = (
            key.root_workflow_id == target.root_workflow_id
            and key.invocation_id == target.invocation_id
            and key.context_epoch in (target.context_epoch, target.context_epoch + 1)
            and (key.session_id, key.session_generation)
            == (target.session_id, target.session_generation)
            and parent is not None and not self._terminal(key)
            and join is not None and join.workflow_id == key.root_workflow_id
            and key.invocation_id in join.waiter_invocation_ids
            and parent.join_id in (None, handoff.join_id)
        )
        if not valid:
            self._parent_capacity_handoffs.pop(key.context_id, None)
            return None
        return handoff if join.satisfied else None

    def _observe_parent_handoff_service(self, req: object) -> None:
        key = _request_key(req)
        if key is None or self.visible.get(key.request_id) != key:
            return
        handoff = self._live_parent_capacity_handoff(key) if key is not None else None
        if handoff is None:
            return
        self._parent_capacity_handoffs.pop(key.context_id, None)
        self.counts["parent_capacity_handoff_first_service"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "parent_capacity_handoff_first_service",
                "ts_ms": time.time() * 1000., "workflow_id": key.root_workflow_id,
                "invocation_id": key.invocation_id, "context_id": key.context_id,
                "context_epoch": key.context_epoch, "request_id": key.request_id,
                "join_id": handoff.join_id, "child_ids": sorted(handoff.child_ids),
                "last_observed_child_return_ts_ms": handoff.last_return_ts_ms,
                "freed_device_units": dict(handoff.freed_device_units),
                "freed_host_units": dict(handoff.freed_host_units),
                "request_wait_ms": (
                    time.monotonic() - self._visible_since.get(key.request_id, time.monotonic())
                ) * 1000.,
                "semantics": (
                    "First observed completed parent GPU batch after JOIN, not kernel start; "
                    "RETURN observation uses scheduler wall time, not the client's event clock; "
                    "linked capacity release "
                    "is separate from predictive H2D ACK/FULL reuse and JCT benefit."
                ),
            })

    def _record_terminal_cache(self, watch: dict) -> bool:
        key = watch["key"]
        multiple_anchors = len(watch["anchors"]) > 1
        summaries, unavailable, gone = {}, [], []
        for node_id, created in watch["anchors"]:
            try:
                try:
                    node = self._native_cache.tree_core.node_by_id(node_id)
                except KeyError:
                    gone.append(node_id)
                    continue
                if node is None or normalize_native_creation_time(node.creation_time) != created:
                    gone.append(node_id)
                    continue
                observation = observe_unified_node_closure(self._native_cache, node_id, max_nodes=64)
                if not observation.observable:
                    unavailable.append({"node_id": node_id, "reason": observation.reason})
                    continue
                for summary in observation.nodes:
                    summaries[(summary.node_id, summary.creation_time)] = (
                        summary if multiple_anchors else summary.to_record()
                    )
            except (AttributeError, KeyError, TypeError, ValueError):
                unavailable.append({"node_id": node_id, "reason": "native node unavailable"})
        self._opportunity_writer.record({
            "event": "terminal_context_cache_sample", "ts_ms": time.time() * 1000.,
            "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
            "context_id": key.context_id, "context_epoch": key.context_epoch,
            "session_id": key.session_id, "sample_index": watch["sample_count"],
            "elapsed_since_terminal_ms": time.monotonic() * 1000. - watch["terminated_ms"],
            "anchor_node_ids": [node for node, _ in watch["anchors"]],
            "nodes": (
                [summary.to_record() for summary in summaries.values()]
                if multiple_anchors else list(summaries.values())
            ),
            "gone_or_replaced_anchors": gone,
            "unavailable_anchors": unavailable,
            "semantics": (
                "Read-only terminated-context leaf ancestry; ancestors may be shared "
                "and are not exclusive dead bytes. Session close is not physical "
                "reclamation; no cache drop/offload is caused by this observation."
            ),
        })
        watch["sample_count"] += 1
        if gone:
            watch["anchors"] = [
                anchor for anchor in watch["anchors"] if anchor[0] not in gone
            ]
        return bool(watch["anchors"])

    @timed_runtime("terminal_sampling")
    def _sample_terminal_cache(self, *, now_ms: float) -> None:
        if (
            not self._terminal_cache_diagnostics
            or self._opportunity_writer is None
            or now_ms < self._terminal_cache_next_ms
        ):
            return
        self._terminal_cache_next_ms = now_ms + 1000.
        for scope, watch in list(self._terminal_cache_watches.items())[:4]:
            age = now_ms - watch["terminated_ms"]
            if age >= 60_000.:
                self._terminal_cache_watches.pop(scope, None)
                self.counts["terminal_cache_watch_time_expired"] += 1
                continue
            if self._record_terminal_cache(watch):
                self._terminal_cache_watches.move_to_end(scope)
            else:
                self._terminal_cache_watches.pop(scope, None)
                self.counts["terminal_cache_watch_anchors_gone"] += 1

    def capture_shadow_candidate(
        self, cache: object, *, context_id: str, context_epoch: int,
        for_prefetch: bool = False,
        include_non_actionable: bool = False,
        host_full_free_tokens: int | None = None,
        _skip_complete_backup: bool = False,
    ) -> ActionLocalShadowCandidate | ActionLocalPrefetchCandidate | None:
        anchors = self.snapshot_session_anchors(
            cache, context_id=context_id, context_epoch=context_epoch
        )
        if anchors is None:
            return None
        if (
            _skip_complete_backup and not for_prefetch and include_non_actionable
            and cache is self._native_cache and self.enable_prepare_host
            # A dirty leaf will need ordinary planning. The index only skips
            # this optional probe; native capture still validates the copies.
            and not (
                (dirty := getattr(cache, "_beliefkv_unbacked_full", None))
                and any(
                    node_id in dirty
                    for node_id, _ in dict(anchors.component_leaves).get(0, ())
                )
            )
        ):
            backed = []
            tails = self._reclaimable_session_full_leaves(anchors)
            needed = missing_prepare_full_prefix_tokens(
                cache, anchors, complete_backed_nodes=backed,
                reclaim_tail_leaves=tails,
                reclaim_device_leaves=tails if dirty else None,
            )
            if needed is not None:
                if backed:
                    self._pressure_candidate_groups()
                for node_id, created in backed:
                    previous = self._parent_pressure_candidates.get(node_id)
                    if previous is None or previous[0] is not anchors.key or previous[1] != created:
                        if node_id in tails and previous != (anchors.key, created):
                            node, prefix = cache.tree_core.node_by_id(node_id), 0
                            while node is not None:
                                prefix += len(node.key)
                                node = node.parent
                            if prefix > anchors.reusable_input_tokens:
                                self.counts["prepare_backed_full_tail_registered"] += 1
                        self._remember_parent_pressure_node(node_id, anchors.key, created)
                self.counts[
                    "prepare_fully_backed_capture_skipped" if needed == 0
                    else "prepare_empty_private_full_capture_skipped"
                ] += 1
                return None
        if (
            not for_prefetch and type(host_full_free_tokens) is int
            and host_full_free_tokens >= 0
            and type(anchors.reusable_input_tokens) is int
            and host_full_free_tokens < anchors.reusable_input_tokens
            and not self._resident_parent_prepare_supported(cache)
        ):
            backed = [] if (
                cache is self._native_cache and self.enable_prepare_host
                and include_non_actionable
            ) else None
            needed = missing_prepare_full_prefix_tokens(cache, anchors, backed_nodes=backed)
            if needed is not None and needed > host_full_free_tokens:
                # New backups may not fit while settled cold copies can still
                # release HBM. Publication and native reclaim validate them live.
                for node_id, created in backed or ():
                    self._remember_parent_pressure_node(node_id, anchors.key, created)
                if backed:
                    self.counts["prepare_backed_budget_registration"] += len(backed)
                self.counts["prepare_prefix_budget_rejected_early"] += 1
                return None
        return capture_action_local_shadow(
            cache, anchors, for_prefetch=for_prefetch,
            include_non_actionable=include_non_actionable,
        )

    def inspect_context_h2d_opportunity(
        self, *, context_id: str, context_epoch: int,
        admission_candidate: bool = False,
        max_steps: int = 1,
        fit_current_capacity: bool = False,
        skip_empty_restore: bool = False,
        include_checkpoint_plan: bool = False,
        _empty_restore_cache: tuple[object, dict[int, object]] | None = None,
    ) -> SessionH2DOpportunity | None:
        """Read action-local session/allocator evidence; never dispatch H2D."""
        cache = self._native_cache
        key = self.context_sessions.get(context_id)
        if key is None and (
            pending := self._pending_confirmed_join_ticket(context_id, context_epoch)
        ) is not None:
            key = pending.key
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
            and not self._pending_confirmed_join_submit(key)
            or self._terminal(key)
        ):
            return None
        anchors = self.snapshot_session_anchors(
            cache, context_id=context_id, context_epoch=context_epoch
        )
        return (
            inspect_session_h2d_opportunity(
                cache, anchors, max_steps=max_steps,
                fit_current_capacity=fit_current_capacity,
                skip_empty_restore=skip_empty_restore,
                include_checkpoint_plan=include_checkpoint_plan,
                _empty_restore_cache=_empty_restore_cache,
            )
            if anchors is not None else None
        )

    def _wait_prefetch_opportunity(
        self, key: PrefillCandidateKey,
        *, max_steps: int | None = None, fit_current_capacity: bool = False,
        include_checkpoint_plan: bool = False, apply_capacity_budget: bool = True,
        admission_candidate: bool = False,
        _empty_restore_cache: tuple[object, dict[int, object]] | None = None,
    ) -> SessionH2DOpportunity | None:
        batched = callable(getattr(self._native_cache, "prefetch_gpu_session_nodes", None))
        slots = self.physical_ledger.max_pending - self.physical_ledger.pending_count
        if slots < 1:
            return None
        opportunity = self.inspect_context_h2d_opportunity(
            context_id=key.context_id, context_epoch=key.context_epoch,
            max_steps=(
                slots if max_steps is None else min(max_steps, slots)
            ) if batched else 1,
            # Apply both pool budgets together below; a missing state slot
            # need not discard an otherwise fitting FULL checkpoint extent.
            fit_current_capacity=False,
            skip_empty_restore=True,
            include_checkpoint_plan=include_checkpoint_plan,
            **({"admission_candidate": True} if admission_candidate else {}),
            **(
                {"_empty_restore_cache": _empty_restore_cache}
                if _empty_restore_cache is not None else {}
            ),
        )
        if getattr(opportunity, "no_step_reason", None) == "no_host_backed_missing_copy":
            self.counts["wait_prefetch_empty_restore_shortcut"] += 1
        if opportunity is None or opportunity.step is None or not apply_capacity_budget:
            return opportunity
        return self._budget_wait_prefetch_opportunity(
            key, opportunity, fit_current_capacity=fit_current_capacity, batched=batched,
        )

    def _budget_wait_prefetch_opportunity(
        self, key: PrefillCandidateKey, opportunity: SessionH2DOpportunity, *,
        fit_current_capacity: bool, batched: bool,
        budget: PrefetchResidencyBudget | None = None,
    ) -> SessionH2DOpportunity:
        candidate = getattr(opportunity, "candidate", None)
        if candidate is None or not opportunity.headroom.observable:
            return opportunity
        if budget is None:
            budget = self._current_residency_budget(
                include_evictable=True, headroom=opportunity.headroom,
            )
        headroom = opportunity.headroom
        full_limit = max(
            0, headroom.device_full_free_tokens
            + (0 if fit_current_capacity else budget.evictable_full_tokens)
            - budget.reserve_full_tokens,
        )
        mamba_limit = max(
            0, headroom.device_mamba_free_slots
            + (0 if fit_current_capacity else budget.evictable_mamba_slots)
            - budget.reserve_mamba_slots,
        )
        entries = self._native_cache.cache_controller.mem_pool_host.entry_map
        full_unit = entries["kv"].host_pool.size_per_token
        mamba_unit = entries["mamba"].host_pool.size_per_token if "mamba" in entries else 0
        byte_limit = self._prefetch_byte_allowance(budget)
        nodes = {node.node_id: node for node in candidate.nodes}
        selected, full, mamba, deferred_mamba = [], 0, 0, 0
        partial_step, partial_tokens = None, 0
        blocked = None
        leaf = (opportunity.step.leaf_node_id, opportunity.step.leaf_creation_time)
        for step in opportunity.steps:
            node = nodes[step.node_id]
            missing_full = (
                node.full_host_tokens if node.full_device_tokens == 0 else 0
            )
            missing_mamba = int(
                step.include_mamba and node.mamba_host_present
                and not node.mamba_device_present
            )
            next_full, next_mamba = full + missing_full, mamba + missing_mamba
            if (step.leaf_node_id, step.leaf_creation_time) != leaf:
                blocked = "prefetch_checkpoint_identity_changed"
                break
            if (
                next_full > full_limit
                or next_full * full_unit + mamba * mamba_unit > byte_limit
            ):
                blocked = (
                    "prefetch_full_capacity_budget" if next_full > full_limit
                    else "prefetch_byte_capacity_budget"
                )
                splitter = getattr(
                    self._native_cache, "split_beliefkv_prefetch_full_prefix", None,
                )
                page_size = max(1, self._native_page_size)
                available = max(0, min(
                    full_limit - full,
                    (byte_limit - full * full_unit - mamba * mamba_unit) // full_unit,
                ))
                available = available // page_size * page_size
                if batched and callable(splitter) and 0 < available < missing_full:
                    # This is still a plan. Split only after the live timing and
                    # reclaim checks, then recapture the native fragment identity.
                    partial_step, partial_tokens = step, available
                    selected.append(replace(step, include_mamba=False))
                    full += available
                    deferred_mamba += missing_mamba
                break
            if (
                next_mamba > mamba_limit
                or next_full * full_unit + next_mamba * mamba_unit > byte_limit
            ):
                if not batched or not missing_full or not missing_mamba:
                    blocked = (
                        "prefetch_mamba_capacity_budget" if next_mamba > mamba_limit
                        else "prefetch_byte_capacity_budget"
                    )
                    break
                step = replace(step, include_mamba=False)
                next_mamba = mamba
                deferred_mamba += missing_mamba
            selected.append(step)
            full, mamba = next_full, next_mamba
        capacity_budget = {
            "source": budget.source,
            "device_full_free_tokens": headroom.device_full_free_tokens,
            "device_mamba_free_slots": headroom.device_mamba_free_slots,
            "evictable_full_tokens": 0 if fit_current_capacity else budget.evictable_full_tokens,
            "evictable_mamba_slots": 0 if fit_current_capacity else budget.evictable_mamba_slots,
            "reserve_full_tokens": budget.reserve_full_tokens,
            "reserve_mamba_slots": budget.reserve_mamba_slots,
            "full_limit_tokens": full_limit, "mamba_limit_slots": mamba_limit,
            "remaining_byte_limit": byte_limit,
            "requested_full_tokens": opportunity.required_full_tokens,
            "requested_mamba_slots": opportunity.required_mamba_slots,
            "partial_full_tokens": partial_tokens,
        } if self._opportunity_writer is not None else None
        if not selected:
            now_ms = time.monotonic() * 1000.
            waiting = getattr(
                self._native_cache, "beliefkv_join_pressure_candidates_by_component", {},
            ).get(0, ())
            excluded = {node.node_id for node in candidate.nodes}
            # Native budgets and the published eight donors can omit referenced
            # backed FULL. The native index is only a probe hint; reclaim must
            # still earn fresh capacity through its live ownership checks.
            can_probe = bool(
                not fit_current_capacity and batched
                and blocked == "prefetch_full_capacity_budget"
                and callable(getattr(
                    self._native_cache, "reclaim_beliefkv_handoff_capacity", None,
                ))
                and now_ms >= self._wait_capacity_probe_next_ms
                and (
                    any(
                        node_id not in excluded
                        and self._live_parent_pressure_node(node_id, created)
                        for node_id, created in waiting[:8]
                    )
                    or (
                        type(backed := getattr(
                            getattr(self._native_cache, "tree_core", None),
                            "full_host_duplicates", None,
                        )) is dict
                        and any(node_id not in excluded for node_id in backed)
                    )
                )
            )
            if can_probe:
                first = opportunity.steps[0]
                missing = nodes[first.node_id].full_host_tokens
                probe_tokens = min(missing, byte_limit // full_unit)
                page_size = max(1, self._native_page_size)
                probe_tokens = probe_tokens // page_size * page_size
                if (
                    probe_tokens == 0 and budget.source == "native_next_prefill"
                    and missing >= page_size
                ):
                    # Backed waiting pages may be absent from both native
                    # budgets. Probe one page; reclaim must earn a fresh budget.
                    probe_tokens = page_size
                if probe_tokens > 0:
                    first = replace(first, include_mamba=False)
                    return replace(
                        opportunity, step=first, steps=(first,),
                        required_full_tokens=probe_tokens, required_mamba_slots=0,
                        deferred_mamba_slots=int(opportunity.steps[0].include_mamba),
                        partial_full_step=first if probe_tokens < missing else None,
                        partial_full_tokens=probe_tokens if probe_tokens < missing else 0,
                        capacity_reclaim_probe=True, fits_current_free_lists=False,
                        capacity_budget=capacity_budget,
                        blocked_detail=blocked,
                        no_step_reason="prefetch_capacity_reclaim_probe",
                    )
            self.counts["wait_prefetch_capacity_budget"] += 1
            return replace(
                opportunity, step=None, steps=(), required_full_tokens=0,
                required_mamba_slots=0, fits_current_free_lists=None,
                no_step_reason="prefetch_capacity_budget", deferred_mamba_slots=0,
                blocked_detail=blocked, capacity_budget=capacity_budget,
            )
        return replace(
            opportunity, step=selected[0], steps=tuple(selected),
            required_full_tokens=full, required_mamba_slots=mamba,
            deferred_mamba_slots=deferred_mamba,
            partial_full_step=partial_step, partial_full_tokens=partial_tokens,
            capacity_budget=capacity_budget,
            fits_current_free_lists=(
                partial_step is None
                and full <= max(0, headroom.device_full_free_tokens - budget.reserve_full_tokens)
                and mamba <= max(
                    0, headroom.device_mamba_free_slots - budget.reserve_mamba_slots,
                )
            ),
        )

    def _record_wait_prefetch_plan(
        self, source: str, key: PrefillCandidateKey,
        opportunity: SessionH2DOpportunity | None, *,
        reason: str, remaining_ms: float | None = None,
        window: TransferStartWindow | None = None,
        join_id_override: str | None = None,
    ) -> None:
        writer = self._opportunity_writer
        if writer is None:
            return
        invocation = self.graph.invocations.get(key.invocation_id)
        ticket = self._join_ticket
        join_id = join_id_override or (
            ticket.join_id if source == "join_ticket" and ticket is not None
            else getattr(invocation, "join_id", None) if source == "join_ticket"
            else None
        )
        tools = tuple(sorted(getattr(invocation, "active_tool_calls", ())))
        identity = (source, key.context_id, key.context_epoch, join_id, tools)
        step = getattr(opportunity, "step", None)
        steps = getattr(opportunity, "steps", ()) or ((step,) if step else ())
        full = getattr(opportunity, "required_full_tokens", None)
        mamba = getattr(opportunity, "required_mamba_slots", None)
        missing_full = getattr(opportunity, "host_backed_full_missing_device_tokens", None)
        missing_mamba = getattr(opportunity, "host_backed_mamba_missing_device_nodes", None)
        unbacked_full = getattr(opportunity, "unbacked_full_nodes", None)
        unbacked_mamba = getattr(opportunity, "unbacked_mamba_leaves", None)
        observed_candidate = getattr(opportunity, "candidate", None)
        host_only_mamba = (
            sum(node.mamba_host_present and not node.mamba_device_present
                for node in observed_candidate.nodes)
            if observed_candidate is not None else None
        )
        fingerprint = (
            reason, getattr(opportunity, "no_step_reason", None),
            getattr(opportunity, "blocked_detail", None), full, mamba,
            tuple((item.node_id, item.creation_time, item.include_mamba) for item in steps),
            getattr(opportunity, "partial_full_tokens", 0),
            getattr(opportunity, "capacity_reclaim_probe", False),
            missing_full, missing_mamba, unbacked_full, unbacked_mamba,
            host_only_mamba,
        )
        now = time.monotonic()
        previous = self._wait_prefetch_records.get(identity)
        if reason not in ("submitted", "submitted_partial") and (
            previous is not None and previous[1] == fingerprint
        ) and (
            not self.enable_h2d_opportunity_sampling or now - previous[0] < .5
        ):
            return
        self._wait_prefetch_records[identity] = (now, fingerprint)
        self._wait_prefetch_records.move_to_end(identity)
        if len(self._wait_prefetch_records) > 512:
            self._wait_prefetch_records.popitem(last=False)
        unplanned_full = (
            max(0, missing_full - full)
            if type(missing_full) is int and type(full) is int else None
        )
        # Covering observed FULL does not prove state readiness, completed DMA,
        # or that the next request reuses this checkpoint.
        restore_scope = (
            "unobservable" if unplanned_full is None
            else "no_full_payload" if full == 0
            else "partial_observed_host_backed_full" if unplanned_full > 0
            else "all_observed_host_backed_full"
        )
        entries = getattr(
            getattr(getattr(self._native_cache, "cache_controller", None), "mem_pool_host", None),
            "entry_map", {},
        )
        planned_bytes = {
            pool: amount * entry.host_pool.size_per_token
            for pool, amount in (("kv", full), ("mamba", mamba))
            if type(amount) is int and (entry := entries.get(pool)) is not None
        }
        writer.record({
            "event": "wait_prefetch_plan", "ts_ms": time.time() * 1000.,
            "source": source, "workflow_id": key.root_workflow_id,
            "invocation_id": key.invocation_id, "context_id": key.context_id,
            "context_epoch": key.context_epoch, "request_id": key.request_id,
            **({
                "confirmed_parent_request_id": ticket.client_request_id,
                "checkpoint_context_epoch": ticket.checkpoint_key.context_epoch,
                "target_context_epoch": key.context_epoch,
            } if source == "join_ticket" and ticket is not None
                 and ticket.client_request_id is not None else {}),
            "session_id": key.session_id, "session_generation": key.session_generation,
            "join_id": join_id, "active_tool_ids": tools, "reason": reason,
            "no_step_reason": getattr(opportunity, "no_step_reason", None),
            "blocked_detail": getattr(opportunity, "blocked_detail", None),
            "reusable_input_tokens": getattr(
                getattr(opportunity, "anchors", None), "reusable_input_tokens", None,
            ),
            "planned_full_tokens": full, "planned_mamba_slots": mamba,
            "host_backed_full_missing_device_tokens": missing_full,
            "host_backed_mamba_missing_device_nodes": missing_mamba,
            "host_only_mamba_nodes_in_observed_closure": host_only_mamba,
            "mamba_missing_device_nodes_scope": "FULL-resident, separately loadable state",
            "unbacked_full_nodes": unbacked_full,
            "unbacked_mamba_leaves": unbacked_mamba,
            "unplanned_host_backed_full_tokens": unplanned_full,
            "restore_batch_scope": restore_scope,
            "deferred_mamba_slots": getattr(opportunity, "deferred_mamba_slots", 0),
            "planned_pool_bytes": planned_bytes,
            "capacity_budget": getattr(opportunity, "capacity_budget", None),
            "partial_full_tokens": getattr(opportunity, "partial_full_tokens", 0),
            "capacity_reclaim_probe": getattr(opportunity, "capacity_reclaim_probe", False),
            "capacity_reclaim": getattr(opportunity, "capacity_reclaim", None),
            "planned_nodes": [
                {"node_id": item.node_id, "creation_time": item.creation_time,
                 "include_mamba": item.include_mamba} for item in steps
            ],
            "fits_current_free_lists": getattr(opportunity, "fits_current_free_lists", None),
            "restore_frontier": {
                "budget": vars(self._residency_budget).copy(),
                "locked_contexts_by_role": {
                    role: len({
                        lease.key for lease in self._prefetch_service_leases.values()
                        if lease.lock_params is not None and lease.demand_ready == ready
                    })
                    for role, ready in (("demand", True), ("speculative", False))
                },
            } if reason == "admission_budget_busy" else None,
            "predicted_remaining_ms": remaining_ms,
            "start_window_ms": window.horizon_ms if window else None,
            "h2d_p90_ms": window.service_ms if window else None,
            **({
                "h2d_service_ceiling_bytes": window.service_ceiling_bytes,
                "h2d_service_ceiling_shape": window.service_ceiling_shape,
            } if window is not None and window.service_ceiling_bytes is not None else {}),
            "prediction_early_ms": window.prediction_early_ms if window else None,
            "planning_slack_ms": window.planning_slack_ms if window else None,
            "scope": "bounded reusable checkpoint plan; not a reservation or oracle",
        })

    def _join_ticket_has_native_eos(self, ticket: _JoinPrefetchTicket) -> bool:
        stage = self._final_stages.get(ticket.join_id)
        child_key = (
            self._eos_prefetch_keys.get(stage.request_id)
            if stage is not None and ticket.stage_bound and ticket.key == stage.key else None
        )
        return child_key is not None and self._observed_final_eos_live(
            child_key, time.monotonic() * 1000.,
        )

    def _join_ticket_has_report_service(self, ticket: _JoinPrefetchTicket) -> bool:
        if (
            not self._report_prediction_enabled
            or not ticket.stage_bound or ticket.work_endpoint_tokens is None
        ):
            return False
        stage = self._final_stages.get(ticket.join_id)
        return bool(
            stage is not None and stage.key == ticket.key
            and self._report_remaining_ms(stage) is not None
        )

    def _wait_prefetch_timing_valid(
        self, key: PrefillCandidateKey, opportunity: SessionH2DOpportunity, source: str,
        *, service_shape: _WaitPrefetchServiceShape | None = None,
    ) -> bool:
        ticket = self._join_ticket if source == "join_ticket" else self._tool_ticket
        if source == "join_ticket" and ticket is not None and ticket.phase == "confirmed":
            return True
        if source == "join_ticket":
            native_eos = ticket is not None and self._join_ticket_has_native_eos(ticket)
            if (
                ticket is None or ticket.key != key
                or not (
                    native_eos or self.join_prefetch_trigger == "selective_report_or_eos"
                    or self._join_ticket_has_report_service(ticket)
                )
            ):
                self.counts["join_prefetch_requires_native_eos"] += 1
                return False
            if native_eos:
                return True
            if self.join_prefetch_trigger == "selective_report_or_eos":
                stage = self._final_stages.get(ticket.join_id)
                remaining = self._report_remaining_ms(stage) if stage is not None else None
                selected = ticket.report_service_shape
                valid = bool(
                    ticket.stage_bound and ticket.work_endpoint_tokens is not None
                    and ticket.report_start_window_ms is not None
                    and stage is not None and stage.key == key
                    and selected is not None
                    and (
                        _WaitPrefetchServiceShape.from_opportunity(opportunity) == selected
                        or selected.contains(opportunity)
                    )
                    and remaining is not None
                    and remaining <= min(self.prefetch_lead_ms, ticket.report_start_window_ms)
                )
                if not valid:
                    self.counts["selective_join_enqueue_prediction_changed"] += 1
                return valid
        window = (
            self._h2d_start_window(opportunity, service_shape=service_shape)
            if service_shape is not None else self._h2d_start_window(opportunity)
        )
        remaining = None
        if window is not None and source == "tool_wait":
            hint = self._live_tool_hint(key)
            if hint is not None:
                remaining = self._tool_prefetch_remaining(
                    hint, .5, now_ms=time.monotonic() * 1000.,
                )
        elif window is not None and source == "join_ticket":
            stage = self._final_stages.get(ticket.join_id) if ticket else None
            remaining = (
                self._report_remaining_ms(stage)
                if ticket is not None and ticket.work_endpoint_tokens is not None
                and stage is not None and not self._join_ticket_has_native_eos(ticket)
                else 0.
            )
        valid = window is not None and remaining is not None and remaining <= window.horizon_ms
        if not valid:
            self.counts["wait_prefetch_batch_timing_changed"] += 1
            self._record_wait_prefetch_plan(
                source, key, opportunity, reason=(
                    "service_unsupported" if window is None
                    else "service_progress_unavailable" if remaining is None
                    else "before_start_window"
                ), remaining_ms=remaining, window=window,
            )
        elif window.service_ceiling_bytes is not None:
            self._record_wait_prefetch_plan(
                source, key, opportunity, reason="supported_selected_payload_subset",
                remaining_ms=remaining, window=window,
            )
        return valid

    def _reclaim_wait_prefetch_capacity(
        self, key: PrefillCandidateKey, opportunity: SessionH2DOpportunity, *,
        source: str, admission_candidate: bool = False,
        allow_reclaim: bool = True, max_reclaim_nodes: int | None = None,
        minimum_full_free_tokens: int = 0,
    ) -> SessionH2DOpportunity | None:
        if (
            opportunity.fits_current_free_lists is True
            and (
                not minimum_full_free_tokens
                or opportunity.headroom.device_full_free_tokens >= minimum_full_free_tokens
            )
        ):
            return opportunity
        reclaim = getattr(self._native_cache, "reclaim_beliefkv_handoff_capacity", None)
        candidate = getattr(opportunity, "candidate", None)
        if candidate is None:
            return opportunity
        splitter = getattr(
            self._native_cache, "split_beliefkv_prefetch_full_prefix", None,
        )
        if not callable(reclaim) and not (
            opportunity.partial_full_step is not None and callable(splitter)
        ):
            return opportunity
        ticket = self._join_ticket if source == "join_ticket" else None
        budget = None
        if (
            ticket is not None and ticket.key == key
            and ticket.phase == "provisional" and not ticket.issued_nodes
            and callable(getattr(self._native_cache, "prefetch_gpu_session_nodes", None))
            and self._join_ticket_has_native_eos(ticket)
        ):
            budget = self._current_residency_budget(
                include_evictable=True, headroom=opportunity.headroom,
            )
            if opportunity.headroom.device_full_free_tokens > budget.reserve_full_tokens:
                # Submit complete extents that already fit before scanning cold
                # donors. Existing post-enqueue reclaim prepares the missing tail.
                free_prefix = self._budget_wait_prefetch_opportunity(
                    key, opportunity, fit_current_capacity=True, batched=True, budget=budget,
                )
                free_prefix = revalidate_prefetch_subset(free_prefix, opportunity.steps)
                if free_prefix is not None and (
                    0 < free_prefix.required_full_tokens < opportunity.required_full_tokens
                ):
                    self.counts["observed_eos_free_prefix_before_reclaim"] += 1
                    if free_prefix.capacity_budget is not None:
                        free_prefix = replace(
                            free_prefix, capacity_budget={
                                **free_prefix.capacity_budget,
                                "partial_full_tokens": 0,
                                "capacity_refresh_source": "free_eos_prefix_before_reclaim",
                            },
                        )
                    return free_prefix
        excluded = tuple(node.node_id for node in candidate.nodes)
        if budget is None and allow_reclaim:
            budget = self._current_residency_budget(include_evictable=True)
        freed, diagnosis = {}, None
        collect_diagnostics = (
            self._opportunity_writer is not None
            and (
                self.enable_h2d_opportunity_sampling
                or self._borrowable_parent_headroom and not admission_candidate
            )
        )
        if callable(reclaim) and allow_reclaim:
            if opportunity.capacity_reclaim_probe:
                self._wait_capacity_probe_next_ms = (
                    time.monotonic() * 1000. + SEMANTIC_FRAME_INTERVAL_MS
                )
                self.counts["wait_prefetch_backed_capacity_probe"] += 1
            self._publish_parent_pressure_candidates(exclude_node_ids=excluded)
            with (
                nullcontext() if source == "tool_wait"
                else self._queued_full_donor_scope(key)
            ):
                freed = reclaim(
                    full_tokens=max(
                        opportunity.required_full_tokens + budget.reserve_full_tokens,
                        minimum_full_free_tokens,
                    ),
                    mamba_slots=opportunity.required_mamba_slots + budget.reserve_mamba_slots,
                    exclude_node_ids=excluded,
                    **({"collect_diagnostics": True} if collect_diagnostics else {}),
                    **({"max_nodes": max_reclaim_nodes} if max_reclaim_nodes is not None else {}),
                )
            observed = (
                getattr(self._native_cache, "beliefkv_handoff_last_capacity", None)
                if collect_diagnostics else None
            )
            if isinstance(observed, dict):
                checks = []
                for check in observed.get("waiting_candidate_checks", ()):
                    owner = self._parent_pressure_candidates.get(check["node_id"])
                    invocation = (
                        self.graph.invocations.get(owner[0].invocation_id) if owner else None
                    )
                    checks.append({
                        **check,
                        "context_id": owner[0].context_id if owner else None,
                        "invocation_state": invocation.state.value if invocation else None,
                    })
                diagnosis = {**observed, "waiting_candidate_checks": checks}
        if freed:
            self._invalidate_causal_full_donor_observations(freed)
            self.counts[f"{source}_cold_reclaimed"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "wait_prefetch_capacity_reclaimed",
                    "ts_ms": time.time() * 1000., "source": source,
                    "workflow_id": key.root_workflow_id, "context_id": key.context_id,
                    "context_epoch": key.context_epoch, "freed_units": freed,
                    "victims": getattr(self._native_cache, "beliefkv_handoff_last_victims", ()),
                    "capacity_reclaim_probe": opportunity.capacity_reclaim_probe,
                    "capacity_reclaim": diagnosis,
                })
        if admission_candidate and allow_reclaim and not freed:
            self.counts["frontier_restore_no_cold_victim"] += 1
            return None
        headroom = None
        if minimum_full_free_tokens:
            headroom = observe_static_full_mamba_headroom(self._native_cache)
            if (
                not headroom.observable
                or headroom.device_full_free_tokens < minimum_full_free_tokens
            ):
                return None
        if (
            freed and opportunity.partial_full_step is None
            and not opportunity.capacity_reclaim_probe
            and (
                headroom := headroom or observe_static_full_mamba_headroom(self._native_cache)
            ).observable
        ):
            budget = self._current_residency_budget(
                include_evictable=True, headroom=headroom,
            )
            allowance = self._prefetch_byte_allowance(budget)
            entries = self._native_cache.cache_controller.mem_pool_host.entry_map
            planned_bytes = (
                opportunity.required_full_tokens * entries["kv"].host_pool.size_per_token
                + (opportunity.required_mamba_slots * entries["mamba"].host_pool.size_per_token
                   if opportunity.required_mamba_slots else 0)
            )
            if (
                opportunity.required_full_tokens + budget.reserve_full_tokens
                <= headroom.device_full_free_tokens
                and opportunity.required_mamba_slots + budget.reserve_mamba_slots
                <= headroom.device_mamba_free_slots
                and planned_bytes <= allowance
            ):
                # Keep the pre-reclaim selection for planning, but recapture
                # its target before enqueue even if the causal drain is empty.
                self.counts["wait_prefetch_reclaim_plan_reused"] += 1
                capacity = opportunity.capacity_budget
                if capacity is not None:
                    capacity = {
                        **capacity, "source": budget.source,
                        "device_full_free_tokens": headroom.device_full_free_tokens,
                        "device_mamba_free_slots": headroom.device_mamba_free_slots,
                        "evictable_full_tokens": 0, "evictable_mamba_slots": 0,
                        "reserve_full_tokens": budget.reserve_full_tokens,
                        "reserve_mamba_slots": budget.reserve_mamba_slots,
                        "full_limit_tokens": max(
                            0, headroom.device_full_free_tokens - budget.reserve_full_tokens,
                        ),
                        "mamba_limit_slots": max(
                            0, headroom.device_mamba_free_slots - budget.reserve_mamba_slots,
                        ),
                        "remaining_byte_limit": allowance,
                        "capacity_refresh_source": "post_reclaim_free_lists",
                    }
                return replace(
                    opportunity, headroom=headroom, fits_current_free_lists=True,
                    capacity_budget=capacity, capacity_reclaim=diagnosis,
                    enqueue_refresh_required=True,
                )
        # Bounded reclaim need not fit the whole checkpoint. Restore the
        # continuous prefix that fits now and leave the tail for a later load.
        refresh_arguments = {"admission_candidate": True} if admission_candidate else {}
        refreshed = (
            self._wait_prefetch_opportunity(key, fit_current_capacity=True, **refresh_arguments)
            if allow_reclaim else opportunity
        )
        if refreshed is None:
            return None
        partial = refreshed.partial_full_step
        if partial is not None and callable(splitter):
            fragment = splitter(
                session_id=key.session_id, session_generation=key.session_generation,
                leaf_node_id=partial.leaf_node_id,
                leaf_creation_time=partial.leaf_creation_time,
                node_id=partial.node_id, node_creation_time=partial.creation_time,
                prefix_tokens=refreshed.partial_full_tokens,
            )
            if fragment is not None:
                self.counts["wait_prefetch_full_extent_split"] += 1
                self.counts["wait_prefetch_full_extent_split_tokens"] += (
                    refreshed.partial_full_tokens
                )
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "wait_prefetch_full_extent_split",
                        "ts_ms": time.time() * 1000., "source": source,
                        "workflow_id": key.root_workflow_id,
                        "invocation_id": key.invocation_id,
                        "context_id": key.context_id, "context_epoch": key.context_epoch,
                        "request_id": key.request_id,
                        "session_id": key.session_id,
                        "session_generation": key.session_generation,
                        "leaf_node_id": partial.leaf_node_id,
                        "leaf_creation_time": partial.leaf_creation_time,
                        "suffix_node_id": partial.node_id,
                        "suffix_creation_time": partial.creation_time,
                        "fragment_node_id": fragment[0],
                        "fragment_creation_time": fragment[1],
                        "fragment_full_tokens": refreshed.partial_full_tokens,
                        "host_backed_full_missing_device_tokens":
                            refreshed.host_backed_full_missing_device_tokens,
                        "scope": "native tree split only; no DMA or execution readiness",
                    })
                refreshed = self._wait_prefetch_opportunity(
                    key, fit_current_capacity=True, **refresh_arguments,
                )
            else:
                self.counts["wait_prefetch_full_extent_split_declined"] += 1
                refreshed = replace(
                    refreshed, step=None, steps=(), required_full_tokens=0,
                    required_mamba_slots=0, partial_full_step=None,
                    partial_full_tokens=0, fits_current_free_lists=None,
                    no_step_reason="prefetch_partial_extent_unavailable",
                )
        return (
            replace(refreshed, capacity_reclaim=diagnosis)
            if refreshed is not None and diagnosis is not None else refreshed
        )

    @timed_runtime("wait_prefetch_tail_reclaim")
    def _reclaim_wait_prefetch_tail_capacity(
        self, key: PrefillCandidateKey, opportunity: SessionH2DOpportunity,
        issued_steps: tuple[PrefetchLoadStep, ...], *, source: str,
        commands: tuple[str, ...],
    ) -> None:
        candidate = getattr(opportunity, "candidate", None)
        reclaim = getattr(self._native_cache, "reclaim_beliefkv_handoff_capacity", None)
        if (
            candidate is None or not issued_steps or not callable(reclaim)
            or not self._live_wait_prefetch(key, source)
            or not any(self.physical_ledger.is_pending(command) for command in commands)
        ):
            return
        if (
            issued_steps == opportunity.steps
            and opportunity.required_full_tokens == candidate.missing_full_device_tokens
        ):
            # Every missing FULL token in this snapshot is already submitted.
            # This says nothing about DMA completion, state or request readiness.
            return
        sent = {(step.node_id, step.creation_time) for step in issued_steps}
        checkpoint = (
            issued_steps[0].leaf_node_id, issued_steps[0].leaf_creation_time,
        )
        nodes = {node.node_id: node for node in candidate.nodes}
        # Use the pre-enqueue safe input plan, not total closure bytes: the
        # latter can include generated output that the next request cannot use.
        checkpoint_plan = opportunity.checkpoint_plan
        planned = (
            checkpoint_plan.steps if checkpoint_plan is not None
            else plan_prefetch_gpu_steps(candidate)
        )
        tail_tokens = sum(
            nodes[step.node_id].full_host_tokens
            for step in planned
            if (step.node_id, step.creation_time) not in sent
            and (step.leaf_node_id, step.leaf_creation_time) == checkpoint
            and nodes[step.node_id].full_device_tokens == 0
        )
        if not tail_tokens:
            return
        budget = self._current_residency_budget(include_evictable=True)
        entries = self._native_cache.cache_controller.mem_pool_host.entry_map
        full_unit = entries["kv"].host_pool.size_per_token
        allowance = self._prefetch_byte_allowance(budget)
        target_tokens = min(tail_tokens, allowance // full_unit)
        page_size = max(1, self._native_page_size)
        target_tokens = target_tokens // page_size * page_size
        if not target_tokens:
            return
        excluded = tuple(nodes)
        self._publish_parent_pressure_candidates(exclude_node_ids=excluded)
        self.counts["wait_prefetch_tail_capacity_checks"] += 1
        collect_diagnostics = (
            self._opportunity_writer is not None and self.enable_h2d_opportunity_sampling
        )
        with (
            nullcontext() if source == "tool_wait"
            else self._queued_full_donor_scope(key)
        ):
            freed = reclaim(
                full_tokens=target_tokens + budget.reserve_full_tokens,
                mamba_slots=budget.reserve_mamba_slots,
                exclude_node_ids=excluded,
                **({"collect_diagnostics": True} if collect_diagnostics else {}),
            )
        if freed:
            self._invalidate_causal_full_donor_observations(freed)
            self.counts["wait_prefetch_tail_cold_reclaimed"] += 1
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "wait_prefetch_tail_capacity_reclaimed",
                "ts_ms": time.time() * 1000., "source": source,
                "workflow_id": key.root_workflow_id, "context_id": key.context_id,
                "context_epoch": key.context_epoch, "session_id": key.session_id,
                "session_generation": key.session_generation,
                "pending_command_ids": commands,
                "safe_input_tail_full_tokens": tail_tokens,
                "budgeted_tail_full_tokens": target_tokens,
                "reserve_full_tokens": budget.reserve_full_tokens,
                "freed_units": freed,
                "capacity_reclaim": getattr(
                    self._native_cache, "beliefkv_handoff_last_capacity", None,
                ) if collect_diagnostics else None,
                "scope": (
                    "One bounded cold-capacity pass after native enqueue. No "
                    "DMA wait, tail enqueue, admission or execution readiness."
                ),
            })

    def refreshed_shadow_backup_step(
        self, *, context_id: str | None = None, source: str = "tool_wait",
        host_full_free_tokens: int | None = None,
    ) -> ShadowBackupStep | None:
        """Recheck a waiting context and native closure at the action safe point."""
        selection = self._refreshed_shadow_backup_selection(
            context_id=context_id, source=source,
            host_full_free_tokens=host_full_free_tokens,
        )
        return selection[0] if selection is not None else None

    def _refreshed_shadow_backup_selection(
        self, *, context_id: str | None = None, source: str = "tool_wait",
        host_full_free_tokens: int | None = None,
    ) -> tuple[ShadowBackupStep, ActionLocalShadowCandidate] | None:
        candidate = self._refreshed_shadow_backup_candidate(
            context_id=context_id, source=source,
            host_full_free_tokens=host_full_free_tokens,
        )
        if candidate is not None:
            tail_leaves = self._shadow_reclaim_tail_leaves(candidate)
            step = next_shadow_backup_step(
                candidate, reclaim_tail_leaves=tail_leaves,
                reclaim_device_leaves=(
                    tail_leaves if self._resident_parent_prepare_supported(
                        self._native_cache,
                    ) else None
                ),
            )
            # Settled cold copies remain reclaimable while other extents still
            # need PREPARE; publication rechecks native locks and ownership.
            self._register_backed_pressure_nodes(
                candidate.anchors.key, candidate=candidate,
                reclaim_tail_leaves=tail_leaves,
            )
            if step is not None:
                return step, candidate
        return None

    def _refreshed_shadow_backup_candidate(
        self, *, context_id: str | None = None, source: str = "tool_wait",
        host_full_free_tokens: int | None = None,
    ) -> ActionLocalShadowCandidate | None:
        if not self.enable_prepare_host:
            return None
        dirty = getattr(self._native_cache, "_beliefkv_unbacked_full", None)
        complete_check = (
            {"_skip_complete_backup": True}
            if isinstance(dirty, dict)
            and self._resident_parent_prepare_supported(self._native_cache)
            else {}
        )
        if source == "join_prepare":
            key = self.context_sessions.get(context_id)
            parent = self.graph.invocations.get(key.invocation_id) if key else None
            if (
                key is None or parent is None or self._terminal(key)
                or parent.state is not InvocationState.WAIT_JOIN
                or self._join_parent_key(parent.join_id) != key
                or parent.join_id in self._noncontinuing_joins
            ):
                return None
            ticket = self._join_ticket
            if ticket is not None and ticket.key == key and self._live_join_ticket():
                self.counts["prepare_join_restore_window_protected"] += 1
                return None
            return self.capture_shadow_candidate(
                self._native_cache, context_id=key.context_id,
                context_epoch=key.context_epoch,
                include_non_actionable=True,
                host_full_free_tokens=host_full_free_tokens,
                **complete_check,
            )
        cache = self._native_cache
        key = self.context_sessions.get(context_id)
        if cache is not None and key is not None and self._critical_tool_donor(key):
            return self.capture_shadow_candidate(
                cache, context_id=key.context_id, context_epoch=key.context_epoch,
                include_non_actionable=True,
                host_full_free_tokens=host_full_free_tokens,
                **complete_check,
            )
        hint = (self.tool_wait_hints.get(context_id) if context_id is not None
                else self.tool_wait_hint)
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
            cache, context_id=key.context_id, context_epoch=key.context_epoch,
            include_non_actionable=True,
            host_full_free_tokens=host_full_free_tokens,
            **complete_check,
        )
        self.shadow_candidate = candidate
        return candidate

    def issue_shadow_backup_step(
        self, step: ShadowBackupStep, *, source: str = "tool_wait",
        _authorization: Callable[[], bool] | None = None,
    ) -> str | None:
        """Submit a revalidated native shadow; return its ID, not ACK credit.

        The scheduler's action policy must first authorize the step. This
        method supplies transaction safety only and is not an action planner.
        """
        cache = self._native_cache
        if (
            self.physical_disabled
            or cache is None
            or _authorization is not None and not _authorization()
            or self.physical_ledger.pending_action_count("PREPARE_HOST")
            or not isinstance(step, ShadowBackupStep)
            or self.refreshed_shadow_backup_step(
                context_id=step.key.context_id, source=source,
            ) != step
        ):
            self.counts["shadow_step_stale"] += 1
            return None
        command_id = self._next_physical_command_id("shadow")
        registered = False

        def before_enqueue(operation: object) -> bool:
            nonlocal registered
            if _authorization is not None and not _authorization():
                self.counts["shadow_authorization_changed"] += 1
                return False
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
                "backup_scope": "missing_full_extent",
                "node_id": step.node_id, "leaf_node_id": step.leaf_node_id,
                "node_creation_time": step.creation_time,
                "leaf_creation_time": step.leaf_creation_time,
            })
        return command_id

    def issue_shadow_backup_steps(
        self, step: ShadowBackupStep, *, source: str = "tool_wait",
        _selection_candidate: ActionLocalShadowCandidate | None = None,
        _authorization: Callable[[], bool] | None = None,
    ) -> tuple[tuple[ShadowBackupStep, str], ...]:
        """Reserve one FULL prefix burst; each extent retains its own ACK."""
        cache = self._native_cache
        burst = getattr(cache, "prepare_host_session_nodes", None)
        if not callable(burst):
            command = self.issue_shadow_backup_step(
                step, source=source,
                **({"_authorization": _authorization} if _authorization is not None else {}),
            )
            return ((step, command),) if command is not None else ()
        deliveries = (
            self.event_server.drain(max_messages=128)
            if self.event_server is not None else ()
        )
        if (
            self.physical_disabled
            or cache is not self._native_cache
            or _authorization is not None and not _authorization()
            or self.physical_ledger.pending_action_count("PREPARE_HOST")
            or not isinstance(step, ShadowBackupStep)
        ):
            self.counts["shadow_step_stale"] += 1
            return ()
        # Only the first native attempt in this synchronous dispatch may reuse
        # its selection. Control delivery or an earlier attempt invalidates it.
        if (
            type(deliveries) is tuple and not deliveries
            and isinstance(_selection_candidate, ActionLocalShadowCandidate)
            and _selection_candidate.anchors.key == step.key
        ):
            candidate = _selection_candidate
            self.counts["prepare_selection_closure_reused"] += 1
        else:
            candidate = self._refreshed_shadow_backup_candidate(
                context_id=step.key.context_id, source=source,
            )
        if candidate is not None:
            tail_leaves = self._shadow_reclaim_tail_leaves(candidate)
            steps = shadow_backup_steps(
                candidate, reclaim_tail_leaves=tail_leaves,
                reclaim_device_leaves=(
                    tail_leaves if self._resident_parent_prepare_supported(cache) else None
                ),
            )
        else:
            steps = ()
        if not steps or steps[0] != step:
            self.counts["shadow_step_stale"] += 1
            return ()
        commands = tuple(self._next_physical_command_id("shadow") for _ in steps)
        planned = dict(zip(commands, steps))
        registered = set()

        def before_enqueue(operation: object) -> bool:
            command = getattr(operation, "beliefkv_command_id", None)
            planned_step = planned.get(command)
            if (
                planned_step is None or not self._live_parent_pressure_key(step.key)
                or _authorization is not None and not _authorization()
            ):
                self.counts["shadow_causal_invalidated_before_enqueue"] += 1
                return False
            try:
                expected = shadow_expectation_from_native_op(
                    command, planned_step, operation, cache.cache_controller,
                )
                self.register_physical_action(expected)
            except (PhysicalReceiptError, AttributeError, TypeError, ValueError):
                self.counts["shadow_reservation_rejected"] += 1
                return False
            registered.add(command)
            return True

        native_args = dict(
            session_id=step.key.session_id,
            session_generation=step.key.session_generation,
            leaf_node_id=steps[0].leaf_node_id,
            leaf_creation_time=steps[0].leaf_creation_time,
            beliefkv_before_enqueue=before_enqueue,
        )
        if len(steps) == 1:
            outcome = cache.prepare_host_shadow(
                **native_args, node_id=step.node_id,
                node_creation_time=step.creation_time,
                beliefkv_command_id=commands[0], beliefkv_include_mamba=False,
            )
            outcomes = (outcome,) if outcome.issued else ()
        else:
            outcomes = burst(
                **native_args, nodes=tuple(
                    (item.node_id, item.creation_time, command)
                    for item, command in zip(steps, commands)
                ),
            )
        issued = []
        for item, command, outcome in zip(steps, commands, outcomes):
            if not outcome.issued or outcome.node_id != item.node_id or command not in registered:
                self.physical_disabled = True
                raise PhysicalReceiptError("native shadow burst has no matching reservation")
            issued.append((item, command))
            self.counts["shadow_native_issued"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "prepare_native_issued", "ts_ms": time.time() * 1000,
                    "command_id": command, "source": source,
                    "context_id": item.key.context_id,
                    "context_epoch": item.key.context_epoch,
                    "include_mamba": False, "backup_scope": "missing_full_extent",
                    "node_id": item.node_id, "leaf_node_id": item.leaf_node_id,
                    "node_creation_time": item.creation_time,
                    "leaf_creation_time": item.leaf_creation_time,
                    "burst_command_ids": commands[:len(outcomes)],
                })
        for command in registered.difference(command for _, command in issued):
            self.physical_ledger.cancel_unsubmitted(command)
        if issued:
            self.counts["shadow_bursts"] += 1
            self.counts["shadow_burst_nodes"] += len(issued)
        return tuple(issued)

    def _live_parent_pressure_node(self, node_id: int, creation_time: int | float) -> bool:
        record = self._parent_pressure_candidates.get(node_id)
        if record is None or record[1] != creation_time:
            return False
        try:
            node = self._native_cache.tree_core.node_by_id(node_id)
            if node.creation_time != creation_time:
                return False
            owners = node.component_data[0].session_ids
            if owners is not None and record[0].session_id not in owners:
                return False
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
            return False
        return self._live_parent_pressure_key(record[0])

    def _agent_observed_blocked_budget_key(self, key: PrefillCandidateKey) -> bool:
        invocation = self.graph.invocations.get(key.invocation_id)
        if (
            self.physical_disabled or not self._custom_kv_management
            or self.context_sessions.get(key.context_id) != key or self._terminal(key)
            or invocation is None or invocation.context_id != key.context_id
            or invocation.workflow_id != key.root_workflow_id
        ):
            return False
        if invocation.state is InvocationState.WAIT_TOOL:
            return bool(invocation.active_tool_calls)
        if invocation.state is InvocationState.WAIT_CHILD:
            return bool(invocation.blocking_child_ids)
        join = self.graph.joins.get(invocation.join_id)
        return bool(
            invocation.state is InvocationState.WAIT_JOIN and join is not None
            and not join.satisfied and join.workflow_id == invocation.workflow_id
            and key.invocation_id in join.waiter_invocation_ids
        )

    @timed_runtime("agent_blocked_backing_probe")
    def _agent_blocked_prefix_backed(
        self, key: PrefillCandidateKey, *, observed_blocked: bool = False,
    ) -> bool:
        if not (
            self._agent_observed_blocked_budget_key(key) if observed_blocked
            else self._live_parent_pressure_key(key)
        ):
            return False
        anchors = self.snapshot_session_anchors(
            self._native_cache, context_id=key.context_id, context_epoch=key.context_epoch,
        )
        if anchors is None or type(anchors.reusable_input_tokens) is not int:
            return False
        candidate = capture_action_local_shadow(
            self._native_cache, anchors, max_nodes=64, include_non_actionable=True,
        )
        if candidate is None or candidate.missing_full_host_tokens:
            return False
        self.counts["agent_blocked_backing_probe_nodes"] += len(candidate.nodes)
        nodes = {node.node_id: node for node in candidate.nodes}
        ongoing = getattr(self._native_cache, "ongoing_write_through", {})
        for node in candidate.nodes:
            if (
                type(node.key_tokens) is not int or node.key_tokens < 0
                or node.full_host_tokens != node.key_tokens
                or node.node_id in ongoing
                or node.pending_write_id is not None or node.pending_load_id is not None
            ):
                return False
        for leaf, _ in dict(anchors.component_leaves).get(0, ()):
            covered, node = 0, nodes.get(leaf)
            while node is not None:
                covered += node.key_tokens
                node = nodes.get(node.parent_id)
            if covered >= anchors.reusable_input_tokens > 0:
                return True
        return False

    def _live_parent_pressure_key(self, key: PrefillCandidateKey) -> bool:
        parent = self.graph.invocations.get(key.invocation_id)
        ticket = self._join_ticket
        return bool(
            self.enable_prepare_host and not self.physical_disabled
            and self.context_sessions.get(key.context_id) == key
            and parent is not None and (
                parent.state is InvocationState.WAIT_JOIN
                and self._join_parent_key(parent.join_id) == key
                # A report notice can precede its child's service by seconds.
                # Protect the actual restore window, not the whole report phase.
                and not (
                    ticket is not None and ticket.key == key and self._live_join_ticket()
                    or (
                        (demand := self._critical_parent_demand) is not None
                        and demand[0].invocation_id == key.invocation_id
                        and (live := self._live_critical_parent_demand()) is not None
                        and live[0] == key
                    )
                )
                and not self._terminal(key)
                # Both long-wait paths validate this tool owner as nonterminal.
                or parent.state is InvocationState.WAIT_TOOL and self._long_tool_wait(key)
            )
        )

    def _full_owner_index(self) -> dict[str, tuple[PrefillCandidateKey, ...]]:
        if (
            self._cold_full_owner_index_source is not self.context_sessions
            or self._cold_full_owner_index_revision != self._context_sessions_revision
        ):
            indexed: dict[str, list[PrefillCandidateKey]] = {}
            for key in self.context_sessions.values():
                if key.session_id is not None:
                    indexed.setdefault(key.session_id, []).append(key)
            self._cold_full_owner_index = {
                session: tuple(keys) for session, keys in indexed.items()
            }
            self._cold_full_owner_index_source = self.context_sessions
            self._cold_full_owner_index_revision = self._context_sessions_revision
            self.counts["cold_full_owner_index_built"] += 1
        return self._cold_full_owner_index

    def _live_cold_full_owners(
        self, owners: tuple[tuple[str, int], ...], *,
        queued_requests: dict[PrefillCandidateKey, object] | None = None,
    ) -> bool:
        """Authorize FULL-only reclaim only while every native owner is waiting."""
        if not self.enable_prepare_host or self.physical_disabled or not owners:
            return False
        index = self._full_owner_index()
        for session, generation in owners:
            keys = index.get(session)
            if (
                type(session) is not str or type(generation) is not int or not keys
                or any(
                    key.session_generation != generation
                    or not (
                        self._live_parent_pressure_key(key)
                        or queued_requests is not None
                        and (request := queued_requests.get(key)) is not None
                        and self._live_queued_full_request(key, request)
                    )
                    for key in keys
                )
            ):
                return False
        return True

    def _live_backup_full_owners(self, owners: tuple[tuple[str, int], ...]) -> bool:
        """Back blocked or proven unstarted owners; no reclaim authority."""
        if not self.enable_prepare_host or self.physical_disabled or not owners:
            return False
        # Reuse the session index maintained for safe donor checks. No graph
        # traversal, prefix planning or tool-duration prediction per dirty node.
        index = self._full_owner_index()
        for session, generation in owners:
            keys = index.get(session)
            if type(session) is not str or type(generation) is not int or not keys or any(
                key.session_generation != generation
                or (inv := self.graph.invocations.get(key.invocation_id)) is None
                or inv.context_id != key.context_id or inv.workflow_id != key.root_workflow_id
                or (context := self.graph.contexts.get(key.context_id)) is None
                or context.epoch != key.context_epoch
                or context.workflow_id != key.root_workflow_id
                or not (
                    inv.state in (
                        InvocationState.WAIT_JOIN, InvocationState.WAIT_CHILD,
                        InvocationState.WAIT_TOOL,
                    )
                    or inv.state is InvocationState.RUNNING_LLM
                    and self._idle_backup_queued_owner(key)
                )
                or self._terminal(key)
                for key in keys
            ):
                return False
        return True

    def _idle_backup_queued_owner(self, key: PrefillCandidateKey) -> bool:
        if not self._idle_backup_queue_scope:
            return False
        if self._idle_backup_queued_requests is None:
            self._idle_backup_queued_requests = {
                candidate: (position, request)
                for position, request in enumerate(islice(self._capacity_waiting_queue, 512))
                if (candidate := self.visible.get(getattr(request, "rid", None))) is not None
            }
            self.counts["idle_full_prepare_queued_index_builds"] += 1
        entry = self._idle_backup_queued_requests.get(key)
        if entry is None:
            return False
        position, request = entry
        if not self._unallocated_waiting_full_request(key, request):
            return False
        queue = self._capacity_waiting_queue
        return (
            type(queue) is list and position < len(queue) and queue[position] is request
        ) or any(
            waiting is request for waiting in islice(queue, 512)
        )

    def _unallocated_waiting_full_request(
        self, key: PrefillCandidateKey, request: object,
    ) -> bool:
        """Native request has not entered service or acquired mutable KV."""
        kv = getattr(request, "kv", None)
        if (
            kv is None or getattr(kv, "req_pool_idx", False) is not None
            or any(
                type(value := getattr(kv, name, None)) is not int or value != 0
                for name in ("kv_allocated_len", "kv_committed_len")
            )
            or getattr(request, "is_retracted", True) is not False
            or getattr(request, "retracted_stain", True) is not False
            or (outputs := getattr(request, "output_ids", None)) is None or len(outputs)
            or not callable(getattr(request, "finished", None)) or request.finished()
            or self._executable_waiting_key(request) != key
            or any(active is request for active in getattr(self._native_running_batch, "reqs", ()))
        ):
            return False
        return self.graph.invocations[key.invocation_id].state is InvocationState.RUNNING_LLM

    def _live_queued_full_request(
        self, key: PrefillCandidateKey, request: object, *,
        _queue_tail_member: bool = False,
    ) -> bool:
        budget = self._residency_budget
        if (
            budget.source != "native_next_prefill"
            or type(budget.request_slots) is not int or budget.request_slots <= 0
            or not self._unallocated_waiting_full_request(key, request)
            or self._ready_join_parent(request, key)
            or self._last_join_child_has_waiting_parent(key)
            or not _queue_tail_member and not any(
                waiting is request for waiting in islice(
                    self._capacity_waiting_queue, 1, 512,
                )
            )
        ):
            return False
        return self.graph.invocations[key.invocation_id].state is InvocationState.RUNNING_LLM

    @contextmanager
    def _queued_full_donor_scope(self, key: PrefillCandidateKey):
        """Lend backed queue tails only to the live selected capacity transaction."""
        cache = self._native_cache
        original = getattr(cache, "beliefkv_cold_full_owner_validator", None)
        demand = (
            self._live_critical_parent_demand() if self._capacity_waiting_queue else None
        )
        budget = self._residency_budget
        selected = self._execution_handoff
        selected_authority = bool(
            self.enable_frontier_cold_reclaim
            and self.frontier_cold_reclaim_policy == "selected_queue"
            and selected is not None and selected.key == key
            and self._live_selected_queue_reclaim(selected)
        )
        requests = {}
        if (
            (demand is not None and demand[0] == key or selected_authority)
            and getattr(original, "__self__", None) is self
            and getattr(original, "__func__", None) is NativeAdmissionRuntime._live_cold_full_owners
            and budget.source == "native_next_prefill"
            and type(budget.request_slots) is int and budget.request_slots > 0
        ):
            # Free request rows are an admission ceiling, not a selected batch.
            # Retain the immediate queue head; owner checks protect JOIN dependencies.
            for request in islice(
                self._capacity_waiting_queue, 1, 17 if selected_authority else 512,
            ):
                candidate = self.visible.get(getattr(request, "rid", None))
                if (
                    candidate is not None and candidate.invocation_id != key.invocation_id
                    # This iterator proves membership only for initial collection.
                    # Each native owner check revalidates the live queue below.
                    and self._live_queued_full_request(
                        candidate, request, _queue_tail_member=True,
                    )
                ):
                    requests[candidate] = request
        if requests:
            if selected_authority:
                self.counts["frontier_restore_queued_donor_transactions"] += 1
                self.counts["frontier_restore_queued_donor_candidates"] += len(requests)

            def validate(owners):
                authorized = (
                    self._live_selected_queue_reclaim(selected) if selected_authority
                    else self._live_critical_parent_demand() == demand
                )
                return (
                    self._live_cold_full_owners(owners, queued_requests=requests)
                    if authorized else original(owners)
                )

            cache.beliefkv_cold_full_owner_validator = validate
        try:
            yield len(requests)
        finally:
            if requests:
                cache.beliefkv_cold_full_owner_validator = original

    def _live_selected_queue_reclaim(self, ticket: _ExecutionHandoffTicket) -> bool:
        return bool(
            self._execution_handoff is ticket
            and self._live_execution_handoff(ticket)
            and self._unallocated_waiting_full_request(ticket.key, ticket.request)
            and any(
                request is ticket.request
                for request in islice(self._capacity_waiting_queue, 512)
            )
        )

    def _pressure_candidate_groups(self) -> dict[PrefillCandidateKey, set[int]]:
        candidates = self._parent_pressure_candidates
        if (
            self._parent_pressure_group_source is not candidates
            or self._parent_pressure_group_size != len(candidates)
        ):
            groups: dict[PrefillCandidateKey, set[int]] = {}
            for node_id, (key, _) in candidates.items():
                groups.setdefault(key, set()).add(node_id)
            self._parent_pressure_groups = groups
            self._parent_pressure_positions = {
                node_id: index for index, node_id in enumerate(candidates)
            }
            self._parent_pressure_next_position = len(candidates)
            self._parent_pressure_group_source = candidates
            self._parent_pressure_group_size = len(candidates)
        return self._parent_pressure_groups

    def _remember_parent_pressure_node(
        self, node_id: int, key: PrefillCandidateKey, created: int | float,
    ) -> None:
        groups = self._pressure_candidate_groups()
        previous = self._parent_pressure_candidates.get(node_id)
        if previous is None or previous[0] != key:
            if previous is not None:
                old_members = groups[previous[0]]
                old_members.remove(node_id)
                if not old_members:
                    del groups[previous[0]]
            groups.setdefault(key, set()).add(node_id)
        if previous is None:
            self._parent_pressure_positions[node_id] = self._parent_pressure_next_position
            self._parent_pressure_next_position += 1
        self._parent_pressure_candidates[node_id] = (key, created)
        self._parent_pressure_group_size = len(self._parent_pressure_candidates)

    @timed_runtime("prepare_candidate_maintenance")
    def _prune_parent_pressure_candidates(self) -> None:
        if not self._parent_pressure_candidates:
            return
        groups = self._pressure_candidate_groups()
        self._refresh_prefetch_service_leases()
        for key, members in tuple(groups.items()):
            if not self._live_parent_pressure_key(key):
                for node_id in members:
                    del self._parent_pressure_candidates[node_id]
                    del self._parent_pressure_positions[node_id]
                del groups[key]
        self._parent_pressure_group_size = len(self._parent_pressure_candidates)

    def _on_parent_pressure_parked(self, node_id: int, freed: dict[int, int]) -> None:
        for command, lease in tuple(self._prefetch_service_leases.items()):
            if lease.node_id == node_id:
                self._release_prefetch_service_lease(command, "pressure_parked")
        record = self._parent_pressure_candidates.get(node_id)
        if record is None:
            return
        key = record[0]
        if any(freed.values()):
            self._invalidate_wait_transfer_observation(key)
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

    def _invalidate_causal_full_donor_observations(self, freed: dict[int, int]) -> None:
        """Reuse the owner index already validated by this native reclaim."""
        if freed.get(0, 0) <= 0 or not (
            self._tool_opportunity_cache or self._semantic_target_cache or self.tool_wait_hints
            or self._residency_ordering_hints or self._reentry_observations
        ) or (
            self._cold_full_owner_index_source is not self.context_sessions
            or self._cold_full_owner_index_revision != self._context_sessions_revision
        ):
            return
        cache = self._native_cache
        invalidated = set()
        for victim in getattr(cache, "beliefkv_handoff_last_victims", ()):
            try:
                if (
                    victim.get("scope") != "causal_waiting_full"
                    or victim.get("freed_units", {}).get(0, 0) <= 0
                ):
                    continue
                node = cache.tree_core.node_by_id(victim["node_id"])
                full = node.component_data[0]
                if (
                    node.creation_time != victim["creation_time"]
                    or full.value is not None or full.host_value is None
                ):
                    continue
                sessions = cache.session_refs
                owners = victim.get("full_session_owners")
                if owners is None:
                    owners = tuple(
                        (session, sessions._session_generations.get(session))
                        for session in full.session_ids or ()
                    )
                for session, observed_generation in owners:
                    generation = sessions._session_generations.get(session)
                    if (
                        session in sessions._closed_session_ids
                        or generation is None or generation != observed_generation
                    ):
                        continue
                    for key in self._cold_full_owner_index.get(session, ()):
                        if (
                            key not in invalidated and key.session_generation == generation
                            and self.context_sessions.get(key.context_id) == key
                        ):
                            invalidated.add(key)
                            self._invalidate_wait_transfer_observation(key)
                            self._residency_ordering_hints.pop(key, None)
                            self._reentry_observations.pop(key, None)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
                continue

    @timed_runtime("prepare_candidate_publication")
    def _publish_parent_pressure_candidates(
        self, *, exclude_node_ids: tuple[int, ...] = (),
    ) -> None:
        cache = self._native_cache
        by_component = {0: [], 2: []}
        excluded = frozenset(exclude_node_ids)
        # Both native consumers use these pool tables; their generic fallback
        # is unreachable after this publication supplies both component keys.
        limit = 8
        full_left = mamba_left = limit
        tree = getattr(cache, "tree_core", None)
        device_leaves = getattr(tree, "evictable_device_leaves", ())
        indexed = False
        full_ids: set[int] = set()
        mamba_ids: set[int] = set()
        entries = iter(self._parent_pressure_candidates.items())
        head = tuple(islice(entries, 2 * limit))

        def remaining_entries():
            nonlocal indexed, full_ids, mamba_ids
            lrus = getattr(tree, "lru_lists", None)
            mamba_lru = getattr(lrus.get(2), "cache", None) if isinstance(lrus, dict) else None
            if isinstance(device_leaves, set) and isinstance(mamba_lru, dict):
                # Inspect only the sparse pool members when sorting those IDs
                # costs less than walking the remaining owner-node table.
                visited = {node_id for node_id, _ in head}
                if len(head) == 2 * limit:
                    if (
                        full_left
                        and len(device_leaves) > 8 * len(self._parent_pressure_candidates)
                    ):
                        # Candidate lookups cost more than leaf membership checks;
                        # use them only for a substantially smaller waiting index.
                        full_ids = set()
                        remaining = self._parent_pressure_candidates.keys() - visited - excluded
                        for candidate_id in remaining:
                            try:
                                candidate = tree.node_by_id(candidate_id)
                            except (AttributeError, KeyError, RuntimeError, ValueError):
                                continue
                            if candidate.id == candidate_id and candidate in device_leaves:
                                full_ids.add(candidate_id)
                        self.counts["prepare_publication_candidate_index"] += 1
                    else:
                        full_ids = {
                            node.id for node in device_leaves
                            if (
                                node.id in self._parent_pressure_candidates
                                and node.id not in visited and node.id not in excluded
                            )
                        } if full_left else set()
                    mamba_ids = (
                        self._parent_pressure_candidates.keys() & mamba_lru.keys()
                    ) - visited - excluded if mamba_left else set()
                    indexed = True
                    pool_ids = full_ids | mamba_ids
                    if (
                        len(pool_ids) * max(1, (len(pool_ids) - 1).bit_length())
                        < len(self._parent_pressure_candidates) - len(head)
                    ):
                        self._pressure_candidate_groups()
                        for node_id in sorted(
                            pool_ids, key=self._parent_pressure_positions.__getitem__,
                        ):
                            yield node_id, self._parent_pressure_candidates[node_id]
                        return
            yield from entries

        for node_id, (_, created) in chain(head, remaining_entries()):
            if indexed:
                if not (
                    full_left and full_ids or mamba_left and mamba_ids
                ):
                    break
                if not (
                    full_left and node_id in full_ids or mamba_left and node_id in mamba_ids
                ):
                    continue
                full_ids.discard(node_id)
                mamba_ids.discard(node_id)
            if node_id in excluded:
                continue
            try:
                node = cache.tree_core.node_by_id(node_id)
                full = node.component_data[0]
                state = node.component_data[2]
            except (AttributeError, KeyError, RuntimeError, ValueError):
                continue
            if (
                node.creation_time == created
                and (
                    full.value is not None and full.host_value is not None
                    and full.lock_ref == 0 and full.session_ref == 1
                    or state.value is not None and state.host_value is not None
                    and state.lock_ref == 0 and state.session_ref == 1
                )
                and node_id not in getattr(cache, "ongoing_write_through", {})
                and node.write_through_pending_id is None
                and node.load_back_pending_id is None
            ):
                if (
                    full_left
                    and node in device_leaves and node.backuped
                    and full.value is not None and full.host_value is not None
                    and full.lock_ref == 0 and full.session_ref == 1
                    and all(
                        data.lock_ref == 0 and data.session_ref <= 1
                        and (
                            data.value is None or data.host_value is not None
                            or component == 2
                        )
                        for component, data in (
                            node.component_data.items()
                            if isinstance(node.component_data, dict)
                            else enumerate(node.component_data)
                        )
                    )
                ):
                    by_component[0].append((node_id, created))
                    full_left -= 1
                if (
                    mamba_left
                    and state.value is not None and state.host_value is not None
                    and state.lock_ref == 0 and state.session_ref == 1
                ):
                    by_component[2].append((node_id, created))
                    mamba_left -= 1
                if not full_left and not mamba_left:
                    break
        cache.beliefkv_join_pressure_candidates = ()
        cache.beliefkv_join_pressure_candidates_by_component = {
            component: tuple(items) for component, items in by_component.items()
        }

    @timed_runtime("prepare_backed_registration")
    def _register_backed_pressure_nodes(
        self, key: PrefillCandidateKey,
        *, candidate: ActionLocalShadowCandidate | ActionLocalPrefetchCandidate | None = None,
        reclaim_tail_leaves: frozenset[int] | None = None,
    ) -> None:
        if candidate is None:
            cache = self._native_cache
            anchors = self.snapshot_session_anchors(
                cache, context_id=key.context_id, context_epoch=key.context_epoch,
            )
            candidate = (
                capture_action_local_shadow(
                    cache, anchors, for_prefetch=True, include_non_actionable=True,
                ) if anchors is not None else None
            )
        else:
            anchors = candidate.anchors
        if candidate is None or anchors.reusable_input_tokens is None:
            return
        nodes = None
        if reclaim_tail_leaves is None:
            reclaim_tail_leaves = self._shadow_reclaim_tail_leaves(candidate)
        prefix_lengths: dict[int, int] = {}
        existing = self._parent_pressure_candidates or None
        unchanged_index_checked = False
        for node in candidate.nodes:
            if not (
                node.full_device_tokens and node.full_host_tokens
                or node.mamba_device_present and node.mamba_host_present
            ):
                continue
            previous = existing.get(node.node_id) if existing is not None else None
            if (
                previous is not None and previous[0] is key
                and previous[1] == node.creation_time
            ):
                # Registration only adds candidates. Publication and reclaim
                # still validate live ownership, locks and settled copies.
                if not unchanged_index_checked:
                    self._pressure_candidate_groups()
                    unchanged_index_checked = True
                continue
            if nodes is None:
                nodes = {item.node_id: item for item in candidate.nodes}
            chain, current = [], node
            while current is not None and current.node_id not in prefix_lengths:
                chain.append(current)
                current = nodes.get(current.parent_id)
            prefix = prefix_lengths.get(current.node_id, 0) if current is not None else 0
            for ancestor in reversed(chain):
                prefix += ancestor.key_tokens or 0
                prefix_lengths[ancestor.node_id] = prefix
            if (
                prefix_lengths[node.node_id] <= anchors.reusable_input_tokens
                or node.node_id in reclaim_tail_leaves
            ):
                if (
                    node.node_id in reclaim_tail_leaves
                    and prefix_lengths[node.node_id] > anchors.reusable_input_tokens
                    and self._parent_pressure_candidates.get(node.node_id)
                    != (key, node.creation_time)
                ):
                    self.counts["prepare_backed_full_tail_registered"] += 1
                self._remember_parent_pressure_node(node.node_id, key, node.creation_time)

    @staticmethod
    def _resident_parent_prepare_supported(cache: object) -> bool:
        supported = getattr(cache, "supports_beliefkv_resident_parent_prepare", None)
        return callable(supported) and supported() is True

    def _shadow_reclaim_tail_leaves(
        self, candidate: ActionLocalShadowCandidate | ActionLocalPrefetchCandidate,
    ) -> frozenset[int]:
        """Prove current private device leaves; H2D keeps its input checkpoint."""
        return self._reclaimable_session_full_leaves(candidate.anchors)

    def _reclaimable_session_full_leaves(
        self, anchors: ContextSessionAnchors,
    ) -> frozenset[int]:
        key, cache = anchors.key, self._native_cache
        if not self._live_parent_pressure_key(key):
            return frozenset()
        tree = getattr(cache, "tree_core", None)
        device_leaves = getattr(tree, "evictable_device_leaves", ())
        resident_parents = self._resident_parent_prepare_supported(cache)
        eligible = set()
        for node_id, created in dict(anchors.component_leaves).get(0, ()):
            try:
                node = tree.node_by_id(node_id)
                if node.id != node_id or node.creation_time != created:
                    continue
                if resident_parents:
                    visited = set()
                    while node.component_data[0].value is None:
                        if (
                            node.id in visited
                            or node.component_data[0].host_value is None
                            or node.write_through_pending_id is not None
                            or node.load_back_pending_id is not None
                        ):
                            break
                        visited.add(node.id)
                        parent = node.parent
                        if parent is None or tree.node_by_id(parent.id) is not parent:
                            break
                        node = parent
                    else:
                        node_id, created = node.id, node.creation_time
                full = node.component_data[0]
                if (
                    node.id == node_id and node.creation_time == created
                    and node in device_leaves
                    and full.value is not None and full.session_ref == 1
                    and full.session_ids == {key.session_id}
                    and node.write_through_pending_id is None
                    and node.load_back_pending_id is None
                    and node_id not in getattr(cache, "ongoing_write_through", {})
                    and all(
                        data.lock_ref == 0 and data.host_lock_ref == 0
                        and data.session_ref <= 1
                        for data in (
                            node.component_data.values()
                            if isinstance(node.component_data, dict)
                            else node.component_data
                        )
                    )
                ):
                    eligible.add(node_id)
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
        return frozenset(eligible)

    def _prepare_step_rank(
        self, step: ShadowBackupStep, headroom: object,
        *, full_pressure: bool, mamba_pressure: bool,
    ) -> tuple[float, float] | None:
        """Rank reclaimable pressured pools; do not make a native reservation."""
        cache = self._native_cache
        try:
            node = cache.tree_core.node_by_id(step.node_id)
            full, state = node.component_data[0], node.component_data[2]
            full_units = len(full.value) if full.value is not None and full.host_value is None else 0
            checkpoint_full_units = max(full_units, step.missing_full_prefix_tokens)
            mamba_units = int(
                step.include_mamba and state.value is not None and state.host_value is None
            )
            if (
                checkpoint_full_units > headroom.host_full_free_tokens
                or mamba_units > headroom.host_mamba_free_slots
            ):
                self.counts["prepare_candidate_no_host_capacity"] += 1
                if full_units <= headroom.host_full_free_tokens < checkpoint_full_units:
                    self.counts["prepare_checkpoint_no_host_capacity"] += 1
                return None
            entries = cache.host_pool_group.entry_map
            full_unit = entries["kv"].host_pool.size_per_token
            mamba_unit = entries["mamba"].host_pool.size_per_token if "mamba" in entries else 0
            transfer_bytes = full_units * full_unit + mamba_units * mamba_unit
            if transfer_bytes <= 0:
                return None
            reclaim_bytes = (
                (len(full.value) * full_unit if full.value is not None else 0)
                * bool(full_pressure and full.lock_ref == 0 and full.session_ref == 1)
                + int(state.value is not None) * mamba_unit
                * bool(
                    mamba_pressure and state.lock_ref == 0 and state.session_ref == 1
                    and (state.host_value is not None or step.include_mamba)
                )
            )
            # A critical restore needs capacity regardless of this donor's
            # forecast. Native locks and its current tool wait still govern it.
            hint = (
                None if self._critical_tool_donor(step.key)
                else self._live_tool_hint(step.key)
            )
            if hint is not None:
                service = self._estimate_d2h_service(
                    transfer_bytes, shape=pool_shape(full_units, mamba_units),
                )
                if service is not None:
                    remaining = hint.remaining_quantile(.1, now_ms=time.monotonic() * 1000.)
                    ready_ms = service.submit_to_ack_p90_ms + (
                        service.enqueue_to_submit_p90_ms or 0.
                    )
                    restore_ms = self.prefetch_lead_ms
                    if remaining is not None and service.enqueue_to_submit_p90_ms is not None:
                        restore = self._estimate_h2d_service(
                            transfer_bytes, shape=pool_shape(full_units, mamba_units),
                        )
                        if restore is not None and restore.enqueue_to_submit_p90_ms is not None:
                            # A maximum prediction lead is not a mandatory restore
                            # duration. Budget the measured round trip and cadence.
                            restore_ms = (
                                restore.submit_to_ack_p90_ms
                                + restore.enqueue_to_submit_p90_ms
                                + SEMANTIC_FRAME_INTERVAL_MS
                            )
                            self.counts["prepare_round_trip_service_used"] += 1
                    if remaining is not None and remaining < ready_ms + restore_ms:
                        self.counts["prepare_candidate_short_wait_window"] += 1
                        return None
                    if (
                        remaining is not None
                        and remaining < ready_ms + self.prefetch_lead_ms
                    ):
                        self.counts["prepare_round_trip_window_relaxed"] += 1
            # Ancestor backup can unlock a later exclusive checkpoint; retain
            # it behind candidates that can directly release pressured bytes.
            return (-float(reclaim_bytes), float(transfer_bytes))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
            return (0., 0.)

    def _record_prepare_selection(
        self, step: ShadowBackupStep, *, command_id: str, source: str,
        rank: tuple[float, float], scanned: int,
        burst_command_ids: tuple[str, ...] = (),
    ) -> None:
        if self._opportunity_writer is not None:
            demand = (
                self._live_critical_parent_demand()
                if source == "tool_wait" and self._critical_tool_donor(step.key)
                else None
            )
            self._opportunity_writer.record({
                "event": "prepare_candidate_selected", "ts_ms": time.time() * 1000.,
                "command_id": command_id,
                "source": source, "context_id": step.key.context_id,
                "context_epoch": step.key.context_epoch, "node_id": step.node_id,
                "missing_full_prefix_tokens": step.missing_full_prefix_tokens,
                "reclaimable_pressured_bytes": -rank[0],
                "transfer_bytes": rank[1], "scanned_candidates": scanned,
                "burst_command_ids": burst_command_ids or (command_id,),
                "estimate_scope": "selected_first_extent",
                "selection_reason": (
                    "critical_parent_tool_wait" if demand is not None
                    else "waiting_parent" if source == "join_prepare"
                    else "predicted_long_tool_wait"
                ),
                "beneficiary_context_id": demand[0].context_id if demand is not None else None,
            })

    def _prepare_pressure(
        self, waiting_queue: Sequence[object], running_batch: object, headroom: object,
    ) -> tuple[bool, bool]:
        running = len(getattr(running_batch, "reqs", ()))
        slots = min(len(waiting_queue), 8)
        if self._native_max_running is not None:
            slots = min(slots, max(1, self._native_max_running - running))
        demand = self._live_critical_parent_demand()
        if not slots and not running and demand is None:
            self.counts["prepare_no_service_demand"] += 1
            return False, False
        # Reserve a next-prefill window and one decode page per active request.
        # An occupied cache alone is not competing demand, nor free capacity.
        full_reserve = self._native_page_size * running + 8192 * slots
        mamba_reserve = (
            4 * slots if self._residency_pool_bytes is None or self._residency_pool_bytes[1] else 0
        )
        if demand is not None:
            full_reserve = max(full_reserve, demand[1])
            mamba_reserve = max(mamba_reserve, demand[2])
        return (
            headroom.device_full_free_tokens < full_reserve,
            headroom.device_mamba_free_slots < mamba_reserve,
        )

    def _live_critical_parent_demand(self):
        demand = self._critical_parent_demand
        if demand is None:
            return None
        key, _, _, expires = demand
        parent = self.graph.invocations.get(key.invocation_id)
        if time.monotonic() >= expires or parent is None:
            self._critical_parent_demand = None
            return None
        if self._agent_admission is not None and not self._agent_admission.holds_restore(key):
            self._critical_parent_demand = None
            return None
        if (
            parent.state is InvocationState.WAIT_JOIN
            and self.context_sessions.get(key.context_id) == key
            and not self._terminal(key)
        ):
            return demand
        ticket = self._confirmed_join_tickets.get(parent.join_id)
        if (
            ticket is not None and ticket.phase == "confirmed"
            and (ticket.key == key or ticket.checkpoint_key == key)
            and self._live_join_ticket(ticket)
        ):
            # RETURN and client submission may precede native arrival. Preserve
            # capacity work only through that ticket's proven checkpoint handoff.
            if ticket.key != key:
                self.counts["critical_parent_demand_client_bound"] += 1
            demand = (ticket.key, demand[1], demand[2], min(expires, ticket.expires_at))
            self._critical_parent_demand = demand
            return demand
        self._critical_parent_demand = None
        return None

    def _set_critical_parent_demand(
        self, key: PrefillCandidateKey, full_tokens: int, mamba_slots: int,
        expires: float,
    ) -> None:
        if self._agent_admission is not None and not self._agent_admission.claim_restore(
            key, source="join_ticket",
        ):
            self.counts["agent_restore_deferred_before_reclaim"] += 1
            return
        if self._tool_residency_grace is not None:
            self._release_tool_residency_grace("critical_parent")
        prior = self._live_critical_parent_demand()
        self._critical_parent_demand = (key, full_tokens, mamba_slots, expires)
        if (
            prior is None or prior[0] != key
            or full_tokens > prior[1] or mamba_slots > prior[2]
        ):
            # Rearm once when competing demand appears, not on every report tick.
            self._tool_prepare_next_ms = self._critical_reclaim_next_ms = 0.
            for donor in self._waiting_context_keys(InvocationState.WAIT_TOOL):
                if self._critical_tool_donor(donor):
                    self._prepare_probe_after_ms.pop(donor, None)

    def _critical_tool_donor(self, key: PrefillCandidateKey) -> bool:
        demand = self._live_critical_parent_demand()
        if demand is None:
            return False
        invocation = self.graph.invocations.get(key.invocation_id)
        if (
            self.context_sessions.get(key.context_id) != key
            or invocation is None or invocation.state is not InvocationState.WAIT_TOOL
            or not invocation.active_tool_calls or self._terminal(key)
        ):
            return False
        return demand[0] != key

    def _reclaim_critical_parent_headroom(
        self, *, _planned_opportunity: SessionH2DOpportunity | None = None,
    ) -> bool:
        if not self.enable_prepare_host or self.physical_disabled:
            return False
        demand = self._live_critical_parent_demand()
        if demand is None:
            return False
        now_ms = time.monotonic() * 1000.
        if now_ms < self._critical_reclaim_next_ms:
            return False
        self._critical_reclaim_next_ms = now_ms + SEMANTIC_FRAME_INTERVAL_MS
        key, full_tokens, mamba_slots, _ = demand
        headroom = observe_static_full_mamba_headroom(self._native_cache)
        if not headroom.observable:
            return False
        if (
            headroom.device_full_free_tokens >= full_tokens
            and headroom.device_mamba_free_slots >= mamba_slots
        ):
            return True
        reclaim = getattr(self._native_cache, "reclaim_beliefkv_handoff_capacity", None)
        if not callable(reclaim):
            return False
        if (
            isinstance(_planned_opportunity, SessionH2DOpportunity)
            and _planned_opportunity.anchors.key == key
            and _planned_opportunity.headroom == headroom
        ):
            opportunity = _planned_opportunity
        else:
            opportunity = self._wait_prefetch_opportunity(
                key, include_checkpoint_plan=True, apply_capacity_budget=False,
            )
        candidate = getattr(opportunity, "candidate", None)
        if candidate is None:
            return False
        checkpoint = getattr(opportunity, "checkpoint_plan", None)
        if isinstance(checkpoint, PrefetchCheckpointPlan):
            budget = self._current_residency_budget(
                include_evictable=True, headroom=opportunity.headroom,
            )
            if budget.source == "native_next_prefill":
                targets = (
                    checkpoint.full_tokens + budget.reserve_full_tokens,
                    checkpoint.mamba_slots + budget.reserve_mamba_slots,
                )
                if targets != (full_tokens, mamba_slots):
                    if self._live_critical_parent_demand() != demand:
                        return False
                    full_tokens, mamba_slots = targets
                    demand = self._critical_parent_demand = (
                        key, full_tokens, mamba_slots, demand[3],
                    )
                    self.counts["critical_parent_capacity_target_refreshed"] += 1
                if (
                    opportunity.headroom.device_full_free_tokens >= full_tokens
                    and opportunity.headroom.device_mamba_free_slots >= mamba_slots
                ):
                    self.counts["critical_parent_capacity_refresh_satisfied"] += 1
                    return True
        excluded = tuple(node.node_id for node in candidate.nodes)
        # Free only our optional post-ACK locks after observing a real shortage.
        # Native reclaim still validates references, copies and in-flight DMA.
        for command, lease in tuple(self._prefetch_service_leases.items()):
            if lease.key.context_id != key.context_id and lease.lock_params is not None:
                self._release_prefetch_service_lease(
                    command, "critical_parent_capacity", keep_tracking=True,
                )
                if self.physical_disabled:
                    return False
        self._publish_parent_pressure_candidates(exclude_node_ids=excluded)
        prepare = getattr(self._native_cache, "prepare_beliefkv_cold_full_capacity", None)
        with self._queued_full_donor_scope(key) as queued_candidates:
            # Native transactions retain all reference, lock, Host and DMA guards.
            freed = reclaim(
                full_tokens=full_tokens, mamba_slots=mamba_slots,
                exclude_node_ids=excluded, collect_diagnostics=True,
                **({"collect_cold_full_candidates": True} if (
                    callable(prepare) and self.enable_prepare_host and not self.physical_disabled
                ) else {}),
            )
            self.counts["critical_parent_headroom_checks"] += 1
            self.counts["critical_queued_full_donor_checks"] += bool(queued_candidates)
            if any(freed.values()):
                self._invalidate_causal_full_donor_observations(freed)
                self._invalidate_wait_transfer_observation(key)
                self._report_candidate_next_ms = self._tool_prefetch_next_ms = 0.
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "critical_parent_headroom", "ts_ms": time.time() * 1000.,
                    "workflow_id": key.root_workflow_id, "context_id": key.context_id,
                    "full_target_tokens": full_tokens, "mamba_target_slots": mamba_slots,
                    "freed_units": freed, "queued_full_donor_candidates": queued_candidates,
                    "victims": getattr(self._native_cache, "beliefkv_handoff_last_victims", ()),
                    "capacity": getattr(self._native_cache, "beliefkv_handoff_last_capacity", None),
                    "active_llm_preempted": False,
                })
            capacity = getattr(self._native_cache, "beliefkv_handoff_last_capacity", None)
            if (
                self.enable_prepare_host and not self.physical_disabled
                and isinstance(capacity, dict)
                and capacity.get("full_target_satisfied") is False
                and not capacity.get("transaction_truncated")
                and callable(prepare)
                and not self.physical_ledger.pending_action_count("PREPARE_HOST")
                and self._live_critical_parent_demand() == demand
            ):
                if capacity.get("native_full_scan_truncated"):
                    if not getattr(
                        self._native_cache, "beliefkv_handoff_cold_full_candidates", (),
                    ):
                        return any(freed.values())
                    backed = getattr(
                        getattr(self._native_cache, "tree_core", None),
                        "full_host_duplicates", None,
                    )
                    if (
                        type(backed) is not dict or len(backed) > len(excluded)
                    ):
                        return any(freed.values())
                    excluded_ids = set(excluded)
                    if any(node_id not in excluded_ids for node_id in backed):
                        return any(freed.values())
                    # The native index rules out a backed alternative outside the
                    # target. The unfinished walk need not delay a read-only copy.
                    self.counts["critical_no_backed_full_copy_scan_shortcut"] += 1
                issued = prepare(full_tokens=full_tokens, exclude_node_ids=excluded)
                if issued:
                    self._cold_full_backups.update({
                        node_id: (created, "critical") for node_id, created, _ in issued
                    })
                    self.counts["critical_cold_full_backup_issued"] += len(issued)
                    if self._opportunity_writer is not None:
                        self._opportunity_writer.record({
                            "event": "critical_parent_cold_full_prepare",
                            "ts_ms": time.time() * 1000.,
                            "workflow_id": key.root_workflow_id,
                            "context_id": key.context_id, "context_epoch": key.context_epoch,
                            "native_nodes": issued, "include_mamba": False,
                            "before_scan_complete": bool(capacity.get("native_full_scan_truncated")),
                            "scope": "native asynchronous FULL backup; no capacity released yet",
                        })
            return any(freed.values())

    def _queue_spawn_prepare(self, event: RuntimeEvent) -> None:
        if (
            not self.enable_prepare_host or not self.enable_background_prepare or self.physical_disabled
            or event.attributes.get("runtime_internal")
        ):
            return
        parent = self.graph.invocations.get(event.invocation_id)
        child = self.graph.invocations.get(event.target_invocation_id)
        key = self.context_sessions.get(parent.context_id) if parent else None
        workflow = self.graph.workflows.get(event.workflow_id)
        if (
            key is None or child is None or child.parent_invocation_id != key.invocation_id
            or key.root_workflow_id != event.workflow_id
            or child.workflow_id != event.workflow_id
            or event.context_id is not None and event.context_id != key.context_id
            or event.context_epoch is not None and event.context_epoch != key.context_epoch
            or key.session_id is None or key.session_generation is None
            or parent.state.terminal or workflow is None or workflow.end_ts_ms is not None
        ):
            self.counts["spawn_full_prepare_unbound"] += 1
            return
        self._enqueue_delegator_prepare(key)

    def _queue_join_wait_prepare(self, event: RuntimeEvent) -> None:
        if (
            not self.enable_prepare_host or not self.enable_background_prepare
            or self.physical_disabled or event.attributes.get("runtime_internal")
            or is_unobserved_join_event(event)
        ):
            return
        parent = self.graph.invocations.get(event.invocation_id)
        join = self.graph.joins.get(event.join_id)
        if (
            parent is None or parent.state is not InvocationState.WAIT_JOIN
            or join is None or join.satisfied or parent.join_id != join.join_id
            or parent.invocation_id not in join.waiter_invocation_ids
            or parent.workflow_id != event.workflow_id or join.workflow_id != event.workflow_id
        ):
            self.counts["join_wait_full_prepare_not_blocked"] += 1
            return
        key = self.context_sessions.get(parent.context_id)
        workflow = self.graph.workflows.get(event.workflow_id)
        if (
            key is None or key.invocation_id != parent.invocation_id
            or key.root_workflow_id != event.workflow_id
            or event.context_id is not None and event.context_id != key.context_id
            or event.context_epoch is not None and event.context_epoch != key.context_epoch
            or key.session_id is None or key.session_generation is None
            or workflow is None or workflow.end_ts_ms is not None
        ):
            self.counts["join_wait_full_prepare_unbound"] += 1
            return
        # Background SPAWN need not park its parent. Refresh the incremental
        # checkpoint when the observed JOIN actually parks that continuation.
        self._enqueue_delegator_prepare(key)
        self.counts["join_wait_full_prepare_queued"] += 1

    def _enqueue_delegator_prepare(self, key: PrefillCandidateKey) -> None:
        if self._spawn_full_prepares.get(key.context_id) == key:
            self.counts["spawn_full_prepare_coalesced"] += 1
            return
        self._spawn_full_prepares[key.context_id] = key
        self._spawn_prepare_next_ms = 0.
        self.counts["spawn_full_prepare_queued"] += 1

    def dispatch_spawn_prepare(self) -> None:
        """Back a known delegator's published FULL without waiting for pressure."""
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.dispatch()
        if (
            not self.enable_prepare_host or not self.enable_background_prepare
            or self.physical_disabled or not self._spawn_full_prepares
        ):
            return
        now_ms = time.monotonic() * 1000.
        if now_ms < self._spawn_prepare_next_ms:
            return
        self._spawn_prepare_next_ms = now_ms + IDLE_FULL_PREPARE_INTERVAL_MS
        self.counts["spawn_full_prepare_checks"] += 1
        if self._live_critical_parent_demand() is not None:
            self.counts["spawn_full_prepare_blocked_critical"] += 1
            return
        if self.physical_ledger.pending_action_count("PREPARE_HOST"):
            self.counts["spawn_full_prepare_blocked_prepare"] += 1
            return
        prepare = getattr(self._native_cache, "prepare_beliefkv_spawn_full", None)
        if not callable(prepare):
            self.counts["spawn_full_prepare_unsupported"] += len(self._spawn_full_prepares)
            self._spawn_full_prepares.clear()
            return
        for context_id, key in tuple(islice(self._spawn_full_prepares.items(), 4)):
            current = self.context_sessions.get(context_id, key)
            invocation = self.graph.invocations.get(key.invocation_id)
            workflow = self.graph.workflows.get(key.root_workflow_id)
            context = self.graph.contexts.get(context_id)
            if (
                invocation is None or invocation.state.terminal
                or workflow is None or workflow.end_ts_ms is not None
                or context is None or current.context_epoch > context.epoch
                or current.context_epoch < key.context_epoch
                or (
                    current.root_workflow_id, current.invocation_id,
                    current.session_id, current.session_generation,
                ) != (
                    key.root_workflow_id, key.invocation_id,
                    key.session_id, key.session_generation,
                )
            ):
                self._spawn_full_prepares.pop(context_id, None)
                self.counts["spawn_full_prepare_stale"] += 1
                continue
            # Backups follow an immutable native path, not a consumer request ID.
            # The native generation check also covers the HTTP rebind gap.
            if current != key:
                key = current
                self._spawn_full_prepares[context_id] = key
            result = prepare(
                session_id=key.session_id, session_generation=key.session_generation,
                max_batch_bytes=SPAWN_FULL_PREPARE_MAX_BYTES,
            )
            issued = result["issued"]
            if result["complete"]:
                self._spawn_full_prepares.pop(context_id, None)
                self.counts["spawn_full_prepare_settled_contexts"] += 1
            elif result["reason"] in (
                "stale_session", "unsupported_cache", "host_extent_mismatch",
            ):
                self._spawn_full_prepares.pop(context_id, None)
                self.counts[f"spawn_full_prepare_retired:{result['reason']}"] += 1
            else:
                self._spawn_full_prepares.move_to_end(context_id)
            if issued:
                self._cold_full_backups.update({
                    node: (created, "spawn") for node, created, _ in issued
                })
                for node, created, _ in issued:
                    self._remember_parent_pressure_node(node, key, created)
                self.counts["spawn_full_prepare_issued_batches"] += 1
                self.counts["spawn_full_prepare_issued_tokens"] += sum(item[2] for item in issued)
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "spawn_full_prepare", "ts_ms": time.time() * 1000.,
                        "trigger_kind": (
                            "blocked_join_wait" if invocation.state is InvocationState.WAIT_JOIN
                            else "delegation_backup"
                        ),
                        "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
                        "context_id": key.context_id, "context_epoch": key.context_epoch,
                        "session_id": key.session_id, "session_generation": key.session_generation,
                        "native_nodes": issued, "include_mamba": False,
                        "max_batch_bytes": SPAWN_FULL_PREPARE_MAX_BYTES,
                        "max_merged_nodes": result["max_merged_nodes"],
                        "scope": "delegator incremental FULL backup; no eligibility change or device capacity released",
                    })
                break
            self.counts[f"spawn_full_prepare_no_issue:{result['reason']}"] += 1
            if result["reason"] == "d2h_busy":
                break

    def dispatch_idle_prepare(self) -> None:
        """Background FULL copies yield to all queued restoration/capacity work."""
        cache = self._native_cache
        prepare = getattr(cache, "prepare_beliefkv_idle_full", None)
        if (
            not self.enable_prepare_host or not self.enable_background_prepare or self.physical_disabled
            or not callable(prepare)
        ):
            return
        now_ms = time.monotonic() * 1000.
        if now_ms < self._idle_prepare_next_ms:
            return
        self._idle_prepare_next_ms = now_ms + IDLE_FULL_PREPARE_INTERVAL_MS
        self.counts["idle_full_prepare_checks"] += 1
        dirty = getattr(cache, "_beliefkv_unbacked_full", None)
        if isinstance(dirty, dict):
            self.counts["idle_full_prepare_dirty_nodes_last"] = len(dirty)
            self.counts["idle_full_prepare_checks_with_dirty_index"] += bool(dirty)
            if not dirty:
                self.counts["idle_full_prepare_empty_dirty_checks"] += 1
                return
        if self._live_critical_parent_demand() is not None:
            self.counts["idle_full_prepare_blocked_critical_demand"] += 1
            self.counts["idle_full_prepare_blocked_critical_with_dirty_index"] += bool(dirty)
            return
        if self.physical_ledger.pending_count:
            self.counts["idle_full_prepare_blocked_physical_transaction"] += 1
            self.counts["idle_full_prepare_blocked_transaction_with_dirty_index"] += bool(dirty)
            if self.physical_ledger.pending_action_count("PREPARE_HOST"):
                self.counts["idle_full_prepare_blocked_prepare_transaction"] += 1
            if self.physical_ledger.pending_action_count("PREFETCH_GPU"):
                self.counts["idle_full_prepare_blocked_prefetch_transaction"] += 1
            return
        self.counts["idle_full_prepare_native_calls"] += 1
        previous_scope, previous_requests = (
            self._idle_backup_queue_scope, self._idle_backup_queued_requests,
        )
        self._idle_backup_queue_scope = self.idle_prepare_policy == "cold"
        self._idle_backup_queued_requests = None
        try:
            with idle_prepare_backend(
                cache, self.idle_prepare_io_backend, self.counts,
            ) as direct:
                issued = (
                    self._idle_host_mirror_recycler.prepare()
                    if self._idle_host_mirror_recycler is not None else None
                )
                if issued is None:
                    issued = prepare()
        finally:
            self._idle_backup_queue_scope, self._idle_backup_queued_requests = (
                previous_scope, previous_requests,
            )
        if issued:
            if direct:
                self._idle_prepare_direct_nodes.update(item[0] for item in issued)
            self.counts["idle_full_prepare_issued_batches"] += 1
            self._cold_full_backups.update({
                node_id: (created, "idle") for node_id, created, _ in issued
            })
            self.counts["idle_cold_full_backup_issued"] += len(issued)
            self.counts["idle_cold_full_backup_tokens"] += sum(item[2] for item in issued)
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "idle_cold_full_prepare", "ts_ms": time.time() * 1000.,
                    "native_nodes": issued, "include_mamba": False,
                    "scope": "one bounded asynchronous FULL batch; no capacity released yet",
                })
        else:
            self.counts["idle_full_prepare_empty_native_calls"] += 1

    def _prepare_probe_due(self, key: PrefillCandidateKey, now_ms: float) -> bool:
        if len(self._prepare_probe_after_ms) > 512:
            self._prepare_probe_after_ms = {
                item: when for item, when in self._prepare_probe_after_ms.items()
                if when > now_ms and self.context_sessions.get(item.context_id) == item
            }
        if now_ms >= self._prepare_probe_after_ms.get(key, 0.):
            return True
        self.counts["prepare_probe_backoff"] += 1
        return False

    @timed_runtime("join_prepare")
    def dispatch_join_prepare(
        self, waiting_queue: Sequence[object] = (), *, running_batch: object = None,
        _defer_pressure_publication: bool = False,
    ) -> bool | None:
        """Back waiting parents; return True only for deferred publication."""
        cache = self._native_cache
        if not self.enable_prepare_host or cache is None or self.physical_disabled:
            return
        now_ms = time.monotonic() * 1000
        if now_ms < self._join_prepare_next_ms:
            return
        self._join_prepare_next_ms = now_ms + 50.
        if self.physical_ledger.pending_action_count("PREPARE_HOST"):
            return
        parents = self._waiting_context_keys(InvocationState.WAIT_JOIN)
        if not parents and not self._parent_pressure_candidates:
            return
        headroom = observe_static_full_mamba_headroom(cache)
        if not headroom.observable:
            return
        full_pressure, mamba_pressure = self._prepare_pressure(
            waiting_queue, running_batch, headroom,
        )
        if not (mamba_pressure or full_pressure):
            return
        self._prune_parent_pressure_candidates()
        if not full_pressure:
            self.counts["prepare_mamba_pressure_only"] += 1
            if _defer_pressure_publication:
                return True
            self._publish_parent_pressure_candidates()
            return
        if not parents:
            if _defer_pressure_publication:
                return True
            self._publish_parent_pressure_candidates()
            return
        candidates = []
        selected_candidate = selected_order = None
        for offset in range(min(len(parents), 8)):
            key = parents[(self._join_prepare_cursor + offset) % len(parents)]
            if not self._prepare_probe_due(key, now_ms):
                continue
            parent = self.graph.invocations[key.invocation_id]
            identity = _JoinPrefetchIdentity.from_parent(parent.join_id, key)
            prior = self._join_prepare_commands.get(identity)
            if prior is not None and self.physical_ledger.is_pending(prior):
                continue
            selection = self._refreshed_shadow_backup_selection(
                context_id=key.context_id, source="join_prepare",
                host_full_free_tokens=headroom.host_full_free_tokens,
            )
            if selection is None:
                self._prepare_probe_after_ms[key] = now_ms + 1000.
                continue
            step, candidate = selection
            rank = self._prepare_step_rank(
                step, headroom, full_pressure=full_pressure, mamba_pressure=mamba_pressure,
            )
            if rank is not None:
                candidates.append((rank, offset, identity, step))
                if selected_order is None or (rank, offset) < selected_order:
                    selected_order, selected_candidate = (rank, offset), candidate
            else:
                self._prepare_probe_after_ms[key] = now_ms + 1000.
        self._join_prepare_cursor = (self._join_prepare_cursor + 8) % len(parents)
        first_attempt = True
        for rank, offset, identity, step in sorted(candidates, key=lambda item: item[:2]):
            issued = self.issue_shadow_backup_steps(
                step, source="join_prepare",
                _selection_candidate=selected_candidate if first_attempt else None,
            )
            first_attempt = False
            if not issued:
                self._prepare_probe_after_ms[step.key] = now_ms + 250.
                continue
            self._join_prepare_commands[identity] = issued[-1][1]
            for item, command in issued:
                self._remember_parent_pressure_node(item.node_id, item.key, item.creation_time)
            self.counts["join_prepare_issued"] += len(issued)
            self._record_prepare_selection(
                step, command_id=issued[0][1], source="join_prepare",
                rank=rank, scanned=len(candidates),
                burst_command_ids=tuple(command for _, command in issued),
            )
            break
        # Native PREPARE cannot reclaim memory; publish once after its lock changes.
        if _defer_pressure_publication:
            return True
        self._publish_parent_pressure_candidates()

    def _live_tool_hint(
        self, key: PrefillCandidateKey, *, now_ms: float | None = None,
    ) -> NativeToolWaitHint | None:
        hint = self.tool_wait_hints.get(key.context_id)
        inv = self.graph.invocations.get(key.invocation_id)
        if (
            hint is not None and hint.key == key
            and hint.live(
                key, now_ms=time.monotonic() * 1000 if now_ms is None else now_ms,
            )
            and self.context_sessions.get(key.context_id) == key
            and inv is not None and inv.state is InvocationState.WAIT_TOOL
            and inv.updated_ts_ms == hint.invocation_revision_ts_ms
            and len(inv.active_tool_calls) <= 1 and not self._terminal(key)
        ):
            return hint
        return None

    def _long_tool_wait(self, key: PrefillCandidateKey) -> bool:
        if self._critical_tool_donor(key):
            return True
        return self._predicted_long_tool_wait(key)

    def _predicted_long_tool_wait(self, key: PrefillCandidateKey) -> bool:
        """Prediction only; critical JOIN demand does not establish tool length."""
        hint = self._live_tool_hint(key)
        if hint is None:
            return False
        now_ms = time.monotonic() * 1000.
        if hint.release_cdf:
            probability = hint.release_probability_within(TOOL_OFFLOAD_WAIT_MS, now_ms=now_ms)
            return probability is not None and probability <= .1
        remaining = hint.remaining_quantile(.1, now_ms=now_ms)
        return remaining is not None and remaining >= TOOL_OFFLOAD_WAIT_MS

    @timed_runtime("tool_prepare")
    def dispatch_tool_prepare(
        self, waiting_queue: Sequence[object] = (), *, running_batch: object = None,
        _defer_pressure_publication: bool = False,
    ) -> bool | None:
        """Back tool waits; return True only for deferred publication."""
        if self._predicted_tool_offload is not None:
            self._predicted_tool_offload.dispatch()
        if not self.enable_prepare_host or self.physical_disabled or self._native_cache is None:
            return
        now_ms = time.monotonic() * 1000
        if now_ms < self._tool_prepare_next_ms or self.physical_ledger.pending_action_count("PREPARE_HOST"):
            return
        self._tool_prepare_next_ms = now_ms + 100.
        demand = self._live_critical_parent_demand()
        if not self.tool_wait_hints and demand is None:
            return
        headroom = observe_static_full_mamba_headroom(self._native_cache)
        if not headroom.observable:
            return
        full_pressure, mamba_pressure = self._prepare_pressure(
            waiting_queue, running_batch, headroom,
        )
        if not full_pressure:
            if mamba_pressure:
                self.counts["prepare_mamba_pressure_only"] += 1
            return
        keys = {hint.key for hint in self.tool_wait_hints.values()}
        if demand is not None:
            keys.update(self._waiting_context_keys(InvocationState.WAIT_TOOL))
        keys = sorted(keys, key=lambda item: item.context_id)
        if not keys:
            return
        candidates = []
        selected_candidate = selected_order = None
        for offset in range(min(8, len(keys))):
            key = keys[(self._tool_prepare_cursor + offset) % len(keys)]
            if not self._prepare_probe_due(key, now_ms):
                continue
            if not self._long_tool_wait(key):
                continue
            selection = self._refreshed_shadow_backup_selection(
                context_id=key.context_id,
                host_full_free_tokens=headroom.host_full_free_tokens,
            )
            if selection is not None:
                step, candidate = selection
                rank = self._prepare_step_rank(
                    step, headroom,
                    full_pressure=full_pressure, mamba_pressure=mamba_pressure,
                )
                if rank is not None:
                    candidates.append((rank, offset, step))
                    if selected_order is None or (rank, offset) < selected_order:
                        selected_order, selected_candidate = (rank, offset), candidate
                else:
                    self._prepare_probe_after_ms[key] = now_ms + 1000.
            else:
                self._prepare_probe_after_ms[key] = now_ms + 1000.
        self._tool_prepare_cursor = (self._tool_prepare_cursor + 8) % len(keys)
        first_attempt = True
        for rank, offset, step in sorted(candidates, key=lambda item: item[:2]):
            issued = self.issue_shadow_backup_steps(
                step, _selection_candidate=selected_candidate if first_attempt else None,
            )
            first_attempt = False
            if issued:
                for item, command in issued:
                    self._remember_parent_pressure_node(
                        item.node_id, item.key, item.creation_time,
                    )
                self.counts["tool_prepare_issued"] += len(issued)
                self._record_prepare_selection(
                    step, command_id=issued[0][1], source="tool_wait",
                    rank=rank, scanned=len(candidates),
                    burst_command_ids=tuple(command for _, command in issued),
                )
                break
            self._prepare_probe_after_ms[step.key] = now_ms + 250.
        if _defer_pressure_publication:
            return True
        self._publish_parent_pressure_candidates()

    def _reconcile_tool_prefetch_ticket(self, ticket: _ToolPrefetchTicket) -> bool | None:
        commands = ticket.command_ids or (ticket.command_id,)
        if any(self.physical_ledger.is_pending(command) for command in commands):
            return None
        self._tool_opportunity_cache.pop(ticket.key, None)
        self._tool_prefetch_next_ms = 0.
        if not self._physical_actions_completed(commands, action="PREFETCH_GPU"):
            self.counts["tool_prefetch_lost_ack"] += 1
            return False
        self.counts["tool_prefetch_acked"] += len(commands)
        return True

    def _roll_tool_prefetch(self) -> SessionH2DOpportunity | None:
        if not self.enable_tool_prefetch or self.physical_disabled:
            return
        for key, pending in tuple(self._tool_inflight_tickets.items()):
            if self._reconcile_tool_prefetch_ticket(pending) is not None:
                del self._tool_inflight_tickets[key]
        ticket = self._tool_ticket
        if ticket is not None:
            if ticket.command_id is not None:
                reconciled = self._reconcile_tool_prefetch_ticket(ticket)
                if reconciled is None:
                    if not self._parallel_wait_prefetch or not self.can_prefetch_during_overlap():
                        return
                    # Retain ACK reconciliation without serializing a different
                    # context. The ledger still owns all bytes and native nodes.
                    self._tool_inflight_tickets[ticket.key] = ticket
                self._tool_ticket = None
                self._tool_prefetch_next_ms = 0.
                if reconciled is False:
                    return
                ticket = None
        if ticket is not None:
            hint = self._live_tool_hint(ticket.key)
            if (
                hint is None or hint.invocation_revision_ts_ms != ticket.revision
                or not self._tool_prefetch_ready(
                    hint, horizon_ms=ticket.start_window_ms,
                )
            ):
                self._tool_ticket = None
            else:
                return
        now_ms = time.monotonic() * 1000
        if now_ms < self._tool_prefetch_next_ms:
            return
        if self._wait_prefetch_transfer_busy():
            return
        self._tool_prefetch_next_ms = now_ms + SEMANTIC_FRAME_INTERVAL_MS
        self.counts["tool_prefetch_scans"] += 1
        budget = None
        budget_headroom = None
        candidates = []
        outside_window = deferred = 0
        next_possible_window_ms = math.inf
        inflight_contexts = self.physical_ledger.pending_h2d_contexts
        for hint in self.tool_wait_hints.values():
            if self._agent_admission is not None and self._agent_admission.strategy in (
                "thunderagent_retained", "thunderagent_workflow", "thunderagent_reference",
            ) and not self._agent_admission.holds_restore(hint.key, source="tool_wait"):
                self.counts["tool_prefetch_outside_retained_eligibility"] += 1
                continue
            if hint.key in self._tool_inflight_tickets or (
                hint.key.context_id, hint.key.session_id, hint.key.session_generation,
            ) in inflight_contexts:
                continue
            if self._live_tool_hint(hint.key, now_ms=now_ms) is None:
                self._tool_survival_probes.pop(hint.key.context_id, None)
                continue
            use_probe = bool(hint.release_cdf or hint.residual_duration_mass)
            if use_probe:
                probe = self._tool_survival_probes.get(hint.key.context_id)
                if probe is not None and probe[0] is hint and now_ms < probe[1]:
                    deferred += 1
                    outside_window += 1
                    next_possible_window_ms = min(next_possible_window_ms, probe[1])
                    continue
            remaining = self._tool_prefetch_remaining(hint, .5, now_ms=now_ms)
            if remaining is None or remaining > self.prefetch_lead_ms:
                deadline = hint.next_prefetch_check_ms(
                    self.prefetch_lead_ms, now_ms=now_ms, remaining_ms=remaining,
                )
                next_possible_window_ms = min(next_possible_window_ms, deadline)
                if use_probe:
                    self._tool_survival_probes[hint.key.context_id] = (hint, deadline)
                outside_window += 1
                continue
            if self._tool_survival_probes:
                self._tool_survival_probes.pop(hint.key.context_id, None)
            candidates.append((hint, remaining))
        if outside_window:
            self.counts["tool_prefetch_not_in_time_window"] += outside_window
        if deferred:
            self.counts["tool_prefetch_survival_deferred"] += deferred
        if len(self._tool_survival_probes) > 512:
            self._tool_survival_probes = {
                context: probe for context, probe in self._tool_survival_probes.items()
                if self.tool_wait_hints.get(context) is probe[0] and now_ms < probe[1]
            }
        if not candidates:
            # No live hint can enter earlier. New forecasts and credited D2H
            # already rearm this deadline; pending tickets are handled above.
            if (
                math.isfinite(next_possible_window_ms)
                and next_possible_window_ms > now_ms
                and next_possible_window_ms != self._tool_prefetch_next_ms
            ):
                self.counts[
                    "tool_prefetch_scan_deadline_coalesced"
                    if next_possible_window_ms > self._tool_prefetch_next_ms
                    else "tool_prefetch_scan_deadline_advanced"
                ] += 1
                self._tool_prefetch_next_ms = next_possible_window_ms
            return
        # Scheduler/event/batch maintenance already expires leases. Revalidate
        # them here only when this scan will use restoration capacity.
        self._refresh_prefetch_service_leases()
        if self.physical_disabled:
            return
        # Share proofs only during read-only selection; reclaim below clears them.
        empty_restore_cache = (
            (getattr(self._native_cache, "tree_core", None), {})
            if len(candidates) > 1 else None
        )
        for hint, remaining in sorted(candidates, key=lambda item: item[1]):
            if self._live_tool_hint(hint.key) is None:
                continue
            cached = self._tool_opportunity_cache.get(hint.key)
            from_cache = cached is not None and now_ms < cached[0]
            # A still-valid empty observation needs no restoration budget.
            # Keep the admission gate before fresh, potentially long planning.
            if from_cache and (cached[1] is None or cached[1].step is None):
                self.counts["tool_prefetch_no_restore_target"] += 1
                self._record_wait_prefetch_plan(
                    "tool_wait", hint.key, cached[1], reason="no_restore_plan",
                )
                continue
            if budget is None:
                budget = self._current_residency_budget(include_evictable=True)
                budget_headroom = self._residency_headroom
            if not self._prefetch_slot_available(hint.key, budget=budget):
                self.counts["tool_prefetch_admission_budget_busy"] += 1
                continue
            if from_cache:
                opportunity = cached[1]
            else:
                opportunity = self._wait_prefetch_opportunity(
                    hint.key, apply_capacity_budget=False,
                    _empty_restore_cache=empty_restore_cache,
                )
                if opportunity is not None and opportunity.step is not None:
                    # Selection is read-only; reuse this call's budget only
                    # while its native free-list observation still matches.
                    opportunity = self._budget_wait_prefetch_opportunity(
                        hint.key, opportunity, fit_current_capacity=False,
                        batched=callable(getattr(
                            self._native_cache, "prefetch_gpu_session_nodes", None,
                        )),
                        budget=budget if (
                            budget.source == "native_next_prefill"
                            and getattr(opportunity, "headroom", None) == budget_headroom
                        ) else None,
                    )
                self._tool_opportunity_cache[hint.key] = (now_ms + 100., opportunity)
                if len(self._tool_opportunity_cache) > 256:
                    self._tool_opportunity_cache = {
                        key: value for key, value in self._tool_opportunity_cache.items()
                        if value[0] > now_ms
                    }
            if opportunity is None or opportunity.step is None:
                self.counts["tool_prefetch_no_restore_target"] += 1
                self._record_wait_prefetch_plan(
                    "tool_wait", hint.key, opportunity, reason="no_restore_plan",
                )
                continue
            window = self._h2d_start_window(opportunity)
            if window is None:
                self.counts["tool_prefetch_service_unsupported"] += 1
                self._record_wait_prefetch_plan(
                    "tool_wait", hint.key, opportunity, reason="service_unsupported",
                )
                continue
            horizon = window.horizon_ms
            if remaining > horizon:
                self.counts["tool_prefetch_not_latest_start"] += 1
                self._record_wait_prefetch_plan(
                    "tool_wait", hint.key, opportunity,
                    reason="before_start_window", window=window,
                    remaining_ms=remaining,
                )
                continue
            service_shape = _WaitPrefetchServiceShape.from_opportunity(opportunity)
            if opportunity.fits_current_free_lists is not True:
                if empty_restore_cache is not None:
                    empty_restore_cache[1].clear()
                budget = None
                opportunity = self._reclaim_wait_prefetch_capacity(
                    hint.key, opportunity, source="tool_wait",
                )
                from_cache = False
                if opportunity is None or opportunity.step is None:
                    self.counts["tool_prefetch_no_restore_target"] += 1
                    self._record_wait_prefetch_plan(
                        "tool_wait", hint.key, opportunity,
                        reason="no_restore_plan", window=window,
                        remaining_ms=remaining,
                    )
                    continue
                if opportunity.fits_current_free_lists is not True:
                    self.counts["tool_prefetch_no_free_capacity"] += 1
                    self._record_wait_prefetch_plan(
                        "tool_wait", hint.key, opportunity,
                        reason="no_cold_capacity", window=window,
                        remaining_ms=remaining,
                    )
                    continue
                window = (
                    self._h2d_start_window(opportunity, service_shape=service_shape)
                    if service_shape is not None else self._h2d_start_window(opportunity)
                )
                remaining = self._tool_prefetch_remaining(
                    hint, .5, now_ms=time.monotonic() * 1000.,
                )
                if window is None or remaining is None or remaining > window.horizon_ms:
                    self.counts["tool_prefetch_reclaimed_plan_timing_changed"] += 1
                    self._record_wait_prefetch_plan(
                        "tool_wait", hint.key, opportunity,
                        reason="service_unsupported" if window is None else "before_start_window",
                        window=window, remaining_ms=remaining,
                    )
                    continue
                horizon = window.horizon_ms
            self._record_wait_prefetch_plan(
                "tool_wait", hint.key, opportunity, reason="start_window",
                window=window, remaining_ms=remaining,
            )
            self._tool_ticket = _ToolPrefetchTicket(
                hint.key, hint.invocation_revision_ts_ms, start_window_ms=horizon,
                service_shape=service_shape,
            )
            self.counts["tool_prefetch_window_entered"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "tool_prefetch_latest_start", "ts_ms": time.time() * 1000.,
                    "context_id": hint.key.context_id,
                    "context_epoch": hint.key.context_epoch,
                    "wait_revision_ts_ms": hint.invocation_revision_ts_ms,
                    "remaining_p50_ms": remaining,
                    "start_window_ms": horizon,
                    "h2d_ms": window.service_ms,
                    "enqueue_to_submit_p90_ms": window.enqueue_ms,
                    "prediction_early_ms": window.prediction_early_ms,
                    "planning_slack_ms": window.planning_slack_ms,
                })
            return None if from_cache else opportunity

    def _tool_prefetch_ready(
        self, hint: NativeToolWaitHint, *, horizon_ms: float | None = None,
    ) -> bool:
        now_ms = time.monotonic() * 1000
        # P50 and parking now refer to the same surviving event distribution.
        remaining = self._tool_prefetch_remaining(hint, .5, now_ms=now_ms)
        return remaining is not None and remaining <= (
            self.prefetch_lead_ms if horizon_ms is None else horizon_ms
        )

    def _tool_prefetch_remaining(
        self, hint: NativeToolWaitHint, quantile: float, *, now_ms: float,
    ) -> float | None:
        if (
            self._online_tool_timing is not None
            and hint.residual_support_detail in (
                "online_workflow_command", "online_run_command",
            )
            and not self._online_tool_timing.prefetch_supported(
                hint.key.root_workflow_id, hint.key.invocation_id,
                now_ms=now_ms,
                scope=hint.residual_support_detail.removeprefix("online_").removesuffix("_command"),
            )
        ):
            self.counts["tool_prefetch_conditional_tail_unsupported"] += 1
            return None
        remaining = hint.remaining_quantile(quantile, now_ms=now_ms)
        evidence = self._tool_prefetch_survival_evidence.get(hint.key)
        if evidence is None:
            return remaining
        anchor, overrun = evidence
        if anchor.invocation_revision_ts_ms != hint.invocation_revision_ts_ms:
            self._tool_prefetch_survival_evidence.pop(hint.key, None)
            self.counts["tool_prefetch_survival_episode_changed"] += 1
            return remaining
        if not overrun or remaining is None:
            return remaining
        # The expired hint is statistical evidence, not action authorization.
        # Current live identity/timing checks still control every native enqueue.
        surviving = anchor.remaining_quantile(quantile, now_ms=now_ms)
        if surviving is None:
            self.counts["tool_prefetch_survival_tail_unsupported"] += 1
            return None
        if surviving > remaining:
            self.counts["tool_prefetch_survival_remaining_extended"] += 1
        return max(remaining, surviving)

    def _remember_tool_prefetch_survival(self, hint: NativeToolWaitHint) -> None:
        if not hint.release_cdf and not hint.residual_duration_mass:
            return
        previous = self._tool_prefetch_survival_evidence.get(hint.key)
        if (
            previous is None
            or previous[0].invocation_revision_ts_ms != hint.invocation_revision_ts_ms
        ):
            self._tool_prefetch_survival_evidence[hint.key] = (hint, False)
            self._tool_prefetch_survival_evidence.move_to_end(hint.key)
            if len(self._tool_prefetch_survival_evidence) > 512:
                self._tool_prefetch_survival_evidence.popitem(last=False)

    def _estimate_d2h_service(
        self, size_bytes: int, *, shape: str,
    ) -> NativeServiceEstimate | None:
        identity = (size_bytes, shape)
        cache = self._d2h_service_estimates
        if identity in cache:
            self.counts["d2h_service_estimate_reused"] += 1
            return cache[identity]
        estimate = estimate_native_service(
            self._native_service_samples, size_bytes, direction="d2h", shape=shape,
        )
        if len(cache) >= 256:
            cache.clear()
        cache[identity] = estimate
        self.counts["d2h_service_estimate_computed"] += 1
        return estimate

    def _estimate_h2d_service(
        self, size_bytes: int, *, shape: str,
    ) -> NativeServiceEstimate | None:
        # Rare FULL-only H2D evidence must not disappear behind D2H/hybrid ACKs.
        # Expire its cached fallback even if no further receipts arrive.
        recent_full = self._h2d_full_service_samples
        if shape == "full" and recent_full:
            now = time.monotonic()
            expired = 0
            while recent_full and now - recent_full[0][0] > 300.:
                recent_full.popleft()
                expired += 1
            if expired:
                self._h2d_service_estimates.clear()
                self.counts["h2d_full_service_samples_expired"] += expired
        identity = (size_bytes, shape)
        cache = self._h2d_service_estimates
        if identity in cache:
            self.counts["h2d_service_estimate_reused"] += 1
            return cache[identity]
        estimate = estimate_native_service(
            self._native_service_samples, size_bytes, shape=shape,
        ) or estimate_native_service(self._h2d_samples, size_bytes)
        if estimate is None and shape == "full" and len(recent_full) >= 3:
            estimate = estimate_native_service(
                (sample for _, sample in recent_full), size_bytes, shape="full",
            )
            if estimate is None:
                estimate = estimate_larger_full_h2d(
                    (sample for _, sample in recent_full), size_bytes,
                )
                if estimate is not None:
                    self.counts["h2d_larger_full_payload_supported"] += 1
            self.counts["h2d_full_service_fallback_computed"] += 1
            if estimate is not None:
                self.counts["h2d_full_service_fallback_supported"] += 1
        if len(cache) >= 256:
            cache.clear()
        cache[identity] = estimate
        self.counts["h2d_service_estimate_computed"] += 1
        return estimate

    def _h2d_start_window(
        self, opportunity: SessionH2DOpportunity,
        *, service_shape: _WaitPrefetchServiceShape | None = None,
    ) -> TransferStartWindow | None:
        try:
            entries = self._native_cache.cache_controller.mem_pool_host.entry_map
            size = (
                opportunity.required_full_tokens * entries["kv"].host_pool.size_per_token
                + (opportunity.required_mamba_slots * entries["mamba"].host_pool.size_per_token
                   if opportunity.required_mamba_slots else 0)
            )
        except (AttributeError, KeyError, TypeError):
            return None
        estimate = self._estimate_h2d_service(
            size, shape=pool_shape(
                opportunity.required_full_tokens, opportunity.required_mamba_slots,
            ),
        )
        ceiling_bytes, ceiling_shape = None, None
        if estimate is None and service_shape is not None and service_shape.contains(opportunity):
            # Reclaim can shrink a selected burst below measured size support.
            # Retain the current cost of its larger same-checkpoint payload,
            # without scaling polling overhead or caching a physical plan.
            ceiling_bytes = (
                service_shape.full_tokens * entries["kv"].host_pool.size_per_token
                + (service_shape.mamba_slots * entries["mamba"].host_pool.size_per_token
                   if service_shape.mamba_slots else 0)
            )
            ceiling_shape = pool_shape(service_shape.full_tokens, service_shape.mamba_slots)
            estimate = self._estimate_h2d_service(ceiling_bytes, shape=ceiling_shape)
            self.counts["wait_prefetch_subset_service_queries"] += 1
            if estimate is not None:
                self.counts["wait_prefetch_subset_service_supported"] += 1
        if estimate is None:
            return None
        prediction_early_ms = 0.
        hint = self._live_tool_hint(opportunity.step.key) if opportunity.step is not None else None
        if hint is not None:
            now_ms = time.monotonic() * 1000.
            lower = self._tool_prefetch_remaining(hint, .1, now_ms=now_ms)
            center = self._tool_prefetch_remaining(hint, .5, now_ms=now_ms)
            if lower is not None and center is not None:
                prediction_early_ms = max(0., center - lower)
        window = transfer_start_window(
            estimate, max_lead_ms=(
                max(
                    self.prefetch_lead_ms,
                    estimate.submit_to_ack_p90_ms
                    + (estimate.enqueue_to_submit_p90_ms or 0.)
                    + SEMANTIC_FRAME_INTERVAL_MS + prediction_early_ms,
                )
                if self.join_prefetch_trigger == "report_service_or_eos"
                else self.prefetch_lead_ms
            ),
            observation_spacing_ms=SEMANTIC_FRAME_INTERVAL_MS,
            prediction_early_ms=prediction_early_ms,
            # A narrow tool distribution can still overestimate the last few
            # hundred milliseconds. Keep this runtime allowance separate from it.
            planning_slack_ms=TOOL_PREFETCH_PLANNING_SLACK_MS if hint is not None else 0.,
            minimum_advance_ms=(
                self.tool_prefetch_min_advance_ms if hint is not None
                else self.join_prefetch_min_advance_ms
            ),
        )
        return (
            replace(
                window, service_ceiling_bytes=ceiling_bytes,
                service_ceiling_shape=ceiling_shape,
            )
            if window is not None and ceiling_bytes is not None else window
        )

    @timed_runtime("tool_prefetch")
    def dispatch_tool_prefetch(self) -> None:
        opportunity = self._roll_tool_prefetch()
        ticket = self._tool_ticket
        if ticket is None or self._wait_prefetch_transfer_busy(ticket.key):
            return
        hint = self._live_tool_hint(ticket.key)
        if (
            hint is None or hint.invocation_revision_ts_ms != ticket.revision
            or not self._tool_prefetch_ready(
                hint, horizon_ms=ticket.start_window_ms,
            )
        ):
            self._tool_ticket = None
            return
        # Cached plans are recaptured here before the synchronous enqueue.
        if opportunity is None:
            opportunity = self._wait_prefetch_opportunity(ticket.key)
        if (
            opportunity is not None and opportunity.step is not None
            and callable(getattr(self._native_cache, "prefetch_gpu_session_nodes", None))
            and not self._wait_prefetch_timing_valid(
                ticket.key, opportunity, "tool_wait",
                service_shape=ticket.service_shape,
            )
        ):
            self._tool_ticket = None
            return
        if opportunity is not None and opportunity.step is not None:
            if ticket.service_shape is None:
                ticket.service_shape = _WaitPrefetchServiceShape.from_opportunity(opportunity)
            opportunity = self._reclaim_wait_prefetch_capacity(
                ticket.key, opportunity, source="tool_wait",
            )
        step = opportunity.step if opportunity is not None else None
        if step is None:
            self._tool_ticket = None
            return
        if opportunity.fits_current_free_lists is not True:
            self.counts["tool_prefetch_no_free_capacity"] += 1
            self._record_wait_prefetch_plan(
                "tool_wait", ticket.key, opportunity, reason="no_cold_capacity",
            )
            self._tool_ticket = None
            return
        commands = self._issue_wait_prefetch_steps(
            ticket.key, getattr(opportunity, "steps", ()) or (step,),
            source="tool_wait", service_shape=ticket.service_shape,
            _selection_opportunity=opportunity,
        )
        if commands:
            self._remember_tool_prefetch_survival(hint)
            ticket.command_id = commands[-1]
            ticket.command_ids = commands
            ticket.drained = False
            self._tool_prefetch_budget[ticket.key] += len(commands)
            self.counts["tool_prefetch_issued"] += len(commands)

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
        elif source == "execution_handoff":
            ticket = self._execution_handoff
            if ticket is None or not self._live_execution_handoff(ticket):
                return None
            anchors = self._request_reentry_anchors(ticket.request)
            candidate = (
                capture_action_local_shadow(cache, anchors, for_prefetch=True)
                if anchors is not None else None
            )
            return next_prefetch_gpu_step(candidate) if candidate is not None else None
        else:
            return None
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            not valid_source
            or self.context_sessions.get(key.context_id) != key and not (
                source == "join_ticket" and self._pending_confirmed_join_submit(key)
            )
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
                    (
                        "running_llm" if ticket.client_request_id is not None else "ready"
                    ) if source == "join_ticket" and ticket.phase == "confirmed"
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
            or self._wait_prefetch_transfer_busy(step.key)
            or not isinstance(step, PrefetchLoadStep)
            or self.refreshed_prefetch_gpu_step(
                source=source,
                **({"context_id": step.key.context_id} if source == "tool_wait" else {}),
            ) != step
        ):
            self.counts["prefetch_step_stale"] += 1
            return None
        if self._agent_admission is not None and not self._agent_admission.claim_restore(
            step.key, source=source,
        ):
            self.counts["agent_restore_deferred"] += 1
            return None
        command_id = self._next_physical_command_id("prefetch")
        issue_wall_ms = time.time() * 1000
        registered = False

        def before_enqueue(operation: object) -> bool:
            nonlocal registered
            if self.event_server is not None:
                self.event_server.drain(max_messages=128)
            if self._agent_admission is not None and not self._agent_admission.holds_restore(
                step.key, source=source,
            ):
                self.counts["agent_restore_invalidated_before_enqueue"] += 1
                return False
            if self.refreshed_prefetch_gpu_step(
                source=source,
                **({"context_id": step.key.context_id} if source == "tool_wait" else {}),
            ) != step:
                self.counts["prefetch_causal_invalidated_before_enqueue"] += 1
                return False
            issue_budget = self._current_residency_budget(
                include_evictable=source == "execution_handoff",
            )
            if source != "execution_handoff":
                if not self._prefetch_slot_available(step.key, budget=issue_budget):
                    self.counts["prefetch_slot_lost_before_enqueue"] += 1
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
            self._prefetch_issue_budgets[command_id] = issue_budget
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
                self._prefetch_issue_budgets.pop(command_id, None)
            self.counts["prefetch_native_declined"] += 1
            return None
        if not registered or outcome.node_id != step.node_id:
            self.physical_disabled = True
            raise PhysicalReceiptError("native prefetch issued without a matching reservation")
        self._record_prefetch_issued(command_id, step, source, issue_wall_ms, outcome)
        return command_id

    @contextmanager
    def _prefetch_issue_observation_scope(self):
        """The issue-record loop does not deliver events or mutate the native tree."""
        observer = getattr(self.prefetch_issued_callback, "__self__", None)
        scope = getattr(observer, "_prefetch_issuance_scope", None)
        if callable(scope):
            with scope():
                yield
        else:
            yield

    def _record_prefetch_issued(
        self, command_id: str, step: PrefetchLoadStep, source: str,
        issue_wall_ms: float, outcome: object,
    ) -> None:
        self.counts["prefetch_native_issued"] += 1
        self._prefetch_issue_times[command_id] = issue_wall_ms
        self._reentry_observations.pop(step.key, None)
        self._residency_ordering_hints.pop(step.key, None)
        self.counts["prefetch_immediate_submit"] += int(getattr(outcome, "load_started", False))
        if self.prefetch_issued_callback is not None:
            expected = self.physical_ledger.pending_expectation(command_id)
            if expected is not None:
                # Native H2D registration freezes one child with canonical pool bytes.
                child = expected.children[0]
                self.prefetch_issued_callback(PhysicalActionCompleted(
                    command_id, expected.action, expected.context_id, expected.context_epoch,
                    child.published_node_ids, child.pool_bytes, child.num_bytes, source,
                ))
        if self._opportunity_writer is not None:
            tokens = self._context_tokens.get(step.key.context_id)
            record = {
                "event": "prefetch_native_issued",
                "ts_ms": issue_wall_ms,
                "recorded_ts_ms": time.time() * 1000,
                "load_started_immediately": getattr(outcome, "load_started", False),
                "command_id": command_id, "source": source,
                "workflow_id": step.key.root_workflow_id,
                "invocation_id": step.key.invocation_id,
                "request_id": step.key.request_id,
                "session_id": step.key.session_id,
                "session_generation": step.key.session_generation,
                "context_id": step.key.context_id,
                "context_epoch": step.key.context_epoch,
                "node_id": step.node_id, "leaf_node_id": step.leaf_node_id,
                "node_creation_time": step.creation_time,
                "include_mamba": step.include_mamba,
                "residency_issue_budget": (
                    # Frozen budget fields are scalars; no recursive copy is needed.
                    vars(self._prefetch_issue_budgets[command_id]).copy()
                    if command_id in self._prefetch_issue_budgets else None
                ),
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
                    "trigger_kind": (
                        "confirmed_join" if ticket.phase == "confirmed"
                        else "observed_no_tool_eos" if self._join_ticket_has_native_eos(ticket)
                        else "selective_report_service"
                        if ticket.report_start_window_ms is not None else "report_observed_service"
                    ),
                    "report_start_window_ms": ticket.report_start_window_ms,
                })
                if ticket.phase == "confirmed":
                    record.update({
                        "timing_policy": (
                            "confirmed_join_client_bound_before_native_visibility"
                            if ticket.client_request_id is not None
                            else "confirmed_join_before_parent_request"
                        ),
                        **({
                            "confirmed_parent_request_id": ticket.client_request_id,
                            "target_context_epoch": step.key.context_epoch,
                            "checkpoint_context_epoch": ticket.checkpoint_key.context_epoch,
                            "reusable_input_tokens": ticket.checkpoint_input_tokens,
                        } if ticket.client_request_id is not None else {}),
                        "pre_boundary_prediction": False,
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
            elif source == "execution_handoff":
                record.update({
                    "workflow_id": step.key.root_workflow_id,
                    "request_id": step.key.request_id,
                    "invocation_id": step.key.invocation_id,
                    "timing_policy": "submitted_request_before_first_gpu_service",
                    "pre_boundary_prediction": False,
                })
            self._opportunity_writer.record(record)

    def _wait_prefetch_transfer_busy(self, key: PrefillCandidateKey | None = None) -> bool:
        if not self._parallel_wait_prefetch or not self.can_prefetch_during_overlap():
            return bool(self.physical_ledger.pending_action_count("PREFETCH_GPU"))
        if self.physical_ledger.pending_count >= self.physical_ledger.max_pending:
            return True
        sessions = self.physical_ledger.pending_h2d_contexts
        if len(sessions) >= self._wait_prefetch_contexts:
            return True
        return key is not None and (
            key.context_id, key.session_id, key.session_generation,
        ) in sessions

    def _prefetch_byte_allowance(self, budget: PrefetchResidencyBudget) -> int:
        occupied = sum(
            lease.protected_bytes for lease in self._prefetch_service_leases.values()
        )
        if self._parallel_wait_prefetch and budget.source != "native_next_prefill":
            # Native free lists already exclude pending allocations. The legacy
            # fixed byte allowance has no such accounting.
            occupied += self.physical_ledger.pending_action_bytes("PREFETCH_GPU")
        return max(0, budget.byte_limit - occupied)

    def _live_wait_prefetch(self, key: PrefillCandidateKey, source: str) -> bool:
        if source == "join_ticket":
            ticket = self._join_ticket
            if ticket is None or ticket.key != key:
                return False
            if self.context_sessions.get(key.context_id) == key:
                return self._live_join_ticket(ticket)
            pending = self._pending_confirmed_join_ticket(key.context_id, key.context_epoch)
            # The lookup just validated this exact ticket, within this call.
            return (
                pending is not None and pending.key == key
                and (pending is ticket or self._live_join_ticket(ticket))
            )
        context = self.graph.contexts.get(key.context_id)
        if self.physical_disabled or context is None or context.epoch != key.context_epoch or (
            self.context_sessions.get(key.context_id) != key
        ):
            return False
        if source == "tool_wait":
            hint = self._live_tool_hint(key)
            ticket = self._tool_ticket
            return bool(
                hint is not None and ticket is not None and ticket.key == key
                and ticket.revision == hint.invocation_revision_ts_ms
            )
        return False

    @timed_runtime("wait_prefetch_enqueue")
    def _issue_wait_prefetch_steps(
        self, key: PrefillCandidateKey, steps: tuple[PrefetchLoadStep, ...], *,
        source: str,
        service_shape: _WaitPrefetchServiceShape | None = None,
        _selection_opportunity: SessionH2DOpportunity | None = None,
    ) -> tuple[str, ...]:
        cache = self._native_cache
        if not callable(getattr(cache, "prefetch_gpu_session_nodes", None)):
            command = self.issue_prefetch_gpu_step(steps[0], source=source) if steps else None
            return (command,) if command is not None else ()
        join_id = (
            getattr(self._join_ticket, "join_id", None)
            if source == "join_ticket" and self._opportunity_writer is not None else None
        )
        deliveries = (
            self.event_server.drain(max_messages=128)
            if self.event_server is not None else ()
        )
        if not steps:
            return ()
        if not self._live_wait_prefetch(key, source):
            self._record_wait_prefetch_plan(
                source, key, None, reason="enqueue_causal_changed",
                join_id_override=join_id,
            )
            return ()
        if self._wait_prefetch_transfer_busy(key):
            self._record_wait_prefetch_plan(
                source, key, None, reason="enqueue_transfer_busy",
                join_id_override=join_id,
            )
            return ()
        if cache is not self._native_cache:
            self._record_wait_prefetch_plan(
                source, key, None, reason="enqueue_cache_changed",
                join_id_override=join_id,
            )
            return ()
        if any(step.key != key for step in steps) or len({
            (step.leaf_node_id, step.leaf_creation_time) for step in steps
        }) != 1:
            self._record_wait_prefetch_plan(
                source, key, None, reason="enqueue_identity_changed",
                join_id_override=join_id,
            )
            return ()
        # No scheduler/cache mutation can interleave with this synchronous
        # selection except event delivery. Older or uncertain plans are recaptured.
        selected_count = len(steps)
        if (
            type(deliveries) is tuple and not deliveries
            and isinstance(_selection_opportunity, SessionH2DOpportunity)
            and _selection_opportunity.anchors.key == key
            and _selection_opportunity.steps == steps
            and _selection_opportunity.fits_current_free_lists is True
            and _selection_opportunity.partial_full_step is None
            and not _selection_opportunity.enqueue_refresh_required
        ):
            fresh = _selection_opportunity
            self.counts["wait_prefetch_selection_plan_reused"] += 1
        else:
            fresh = self._wait_prefetch_opportunity(
                key, max_steps=len(steps), fit_current_capacity=True,
            )
        if (
            fresh is not None and (
                fresh.steps != steps or fresh.partial_full_step is not None
            )
        ):
            subset = revalidate_prefetch_subset(fresh, steps)
            if subset is not None:
                fresh = subset
                steps = fresh.steps
                self.counts["prefetch_burst_subset_revalidated"] += 1
                self._record_wait_prefetch_plan(
                    source, key, fresh, reason="enqueue_selected_subset",
                    join_id_override=join_id,
                )
        if (
            fresh is None or fresh.fits_current_free_lists is not True or not fresh.steps
            or fresh.partial_full_step is not None or fresh.steps != steps
        ):
            self.counts["prefetch_burst_plan_changed"] += 1
            self._record_wait_prefetch_plan(
                source, key, fresh, reason="enqueue_plan_changed",
                join_id_override=join_id,
            )
            return ()
        if not self._wait_prefetch_timing_valid(
            key, fresh, source, service_shape=service_shape,
        ):
            if source == "join_ticket":
                self._discard_join_ticket("enqueue_start_window_changed")
            return ()
        issue_budget = self._current_residency_budget(
            include_evictable=True, headroom=getattr(fresh, "headroom", None),
        )
        if not self._prefetch_slot_available(key, budget=issue_budget):
            self.counts["prefetch_slot_lost_before_enqueue"] += 1
            self._record_wait_prefetch_plan(
                source, key, fresh, reason="enqueue_slot_changed",
                join_id_override=join_id,
            )
            return ()
        if self._agent_admission is not None and not self._agent_admission.claim_restore(
            key, source=source,
        ):
            self.counts["agent_restore_deferred"] += 1
            return ()
        byte_allowance = self._prefetch_byte_allowance(issue_budget)
        commands = tuple(self._next_physical_command_id("prefetch") for _ in steps)
        planned = dict(zip(commands, steps))
        registered: set[str] = set()
        reserved_bytes = 0
        issued_at = time.time() * 1000.

        def before_enqueue(operation: object) -> bool:
            nonlocal reserved_bytes
            command = getattr(operation, "beliefkv_command_id", None)
            step = planned.get(command)
            if step is None:
                self.counts["prefetch_causal_invalidated_before_enqueue"] += 1
                return False
            try:
                expected = prefetch_expectation_from_native_op(
                    command, step, operation, cache.cache_controller,
                )
                size = expected.children[0].num_bytes
                if reserved_bytes + size > byte_allowance:
                    self.counts["prefetch_burst_byte_budget_lost"] += 1
                    return False
                # The source proof includes registration's live context/session
                # check. Validate once, immediately before reserving this node.
                if not self._live_wait_prefetch(key, source):
                    self.counts["prefetch_causal_invalidated_before_enqueue"] += 1
                    return False
                if self._agent_admission is not None and not self._agent_admission.holds_restore(
                    key, source=source,
                ):
                    self.counts["agent_restore_invalidated_before_enqueue"] += 1
                    return False
                self.physical_ledger.register(expected)
            except (PhysicalReceiptError, AttributeError, TypeError, ValueError):
                self.counts["prefetch_reservation_rejected"] += 1
                return False
            reserved_bytes += size
            registered.add(command)
            invocation = self.graph.invocations.get(key.invocation_id)
            self._prefetch_steps[command] = (
                step, source,
                invocation.updated_ts_ms if invocation is not None else None,
            )
            self._prefetch_issue_budgets[command] = issue_budget
            return True

        outcomes = cache.prefetch_gpu_session_nodes(
            session_id=key.session_id, session_generation=key.session_generation,
            leaf_node_id=steps[0].leaf_node_id,
            leaf_creation_time=steps[0].leaf_creation_time,
            nodes=tuple(
                (step.node_id, step.creation_time, step.include_mamba, command)
                for command, step in zip(commands, steps)
            ),
            beliefkv_before_enqueue=before_enqueue,
            **self._restore_tier_arguments("eager"),
        )
        issued = []
        # Small bursts do not amortize a shared ancestry index.
        with (
            self._prefetch_issue_observation_scope() if len(outcomes) > 16 else nullcontext()
        ):
            for command, step, outcome in zip(commands, steps, outcomes):
                if not outcome.issued or outcome.node_id != step.node_id or command not in registered:
                    self.physical_disabled = True
                    raise PhysicalReceiptError("native wait burst has no matching reservation")
                issued.append(command)
                self._record_prefetch_issued(command, step, source, issued_at, outcome)
        for command in registered.difference(issued):
            self.physical_ledger.cancel_unsubmitted(command)
            self._prefetch_steps.pop(command, None)
            self._prefetch_issue_budgets.pop(command, None)
        if issued:
            self.counts[f"{source}_bursts"] += 1
            self.counts[f"{source}_burst_nodes"] += len(issued)
            self._record_wait_prefetch_plan(
                source, key, fresh,
                reason="submitted" if len(issued) == selected_count else "submitted_partial",
            )
            self._reclaim_wait_prefetch_tail_capacity(
                key, fresh, steps[:len(issued)], source=source,
                commands=tuple(issued),
            )
        return tuple(issued)

    @timed_runtime("handoff_enqueue")
    def _issue_handoff_prefetch_steps(
        self, ticket: _ExecutionHandoffTicket, steps: tuple[PrefetchLoadStep, ...],
    ) -> tuple[str, ...]:
        cache = self._native_cache
        if self.event_server is not None:
            self.event_server.drain(max_messages=128)
        if (
            not steps or not self._live_execution_handoff(ticket)
            or self.physical_ledger.pending_action_count("PREFETCH_GPU")
            or any(step.key != ticket.key for step in steps)
            or len({(step.leaf_node_id, step.leaf_creation_time) for step in steps}) != 1
        ):
            return ()
        if self._agent_admission is not None and not self._agent_admission.claim_restore(
            ticket.key, source="execution_handoff",
        ):
            self.counts["agent_restore_deferred"] += 1
            return ()
        issue_budget = self._current_residency_budget(include_evictable=True)
        commands = tuple(self._next_physical_command_id("prefetch") for _ in steps)
        planned = dict(zip(commands, steps))
        registered: set[str] = set()
        issued_at = time.time() * 1000.

        def before_enqueue(operation: object) -> bool:
            command = getattr(operation, "beliefkv_command_id", None)
            step = planned.get(command)
            if step is None or not self._live_execution_handoff(ticket):
                self.counts["prefetch_causal_invalidated_before_enqueue"] += 1
                return False
            if self._agent_admission is not None and not self._agent_admission.holds_restore(ticket.key):
                self.counts["agent_restore_invalidated_before_enqueue"] += 1
                return False
            try:
                expected = prefetch_expectation_from_native_op(
                    command, step, operation, cache.cache_controller,
                )
                self.register_physical_action(expected)
            except (PhysicalReceiptError, AttributeError, TypeError, ValueError):
                self.counts["prefetch_reservation_rejected"] += 1
                return False
            registered.add(command)
            invocation = self.graph.invocations.get(step.key.invocation_id)
            self._prefetch_steps[command] = (
                step, "execution_handoff",
                invocation.updated_ts_ms if invocation is not None else None,
            )
            self._prefetch_issue_budgets[command] = issue_budget
            return True

        outcomes = cache.prefetch_gpu_session_nodes(
            session_id=ticket.key.session_id,
            session_generation=ticket.key.session_generation,
            leaf_node_id=steps[0].leaf_node_id,
            leaf_creation_time=steps[0].leaf_creation_time,
            nodes=tuple(
                (step.node_id, step.creation_time, step.include_mamba, command)
                for command, step in zip(commands, steps)
            ),
            beliefkv_before_enqueue=before_enqueue,
            **self._restore_tier_arguments("pipeline"),
        )
        issued = []
        with (
            self._prefetch_issue_observation_scope() if len(outcomes) > 16 else nullcontext()
        ):
            for command, step, outcome in zip(commands, steps, outcomes):
                if not outcome.issued or outcome.node_id != step.node_id or command not in registered:
                    self.physical_disabled = True
                    raise PhysicalReceiptError("native handoff burst has no matching reservation")
                issued.append(command)
                self._record_prefetch_issued(command, step, "execution_handoff", issued_at, outcome)
        for command in registered.difference(issued):
            self.physical_ledger.cancel_unsubmitted(command)
            self._prefetch_steps.pop(command, None)
            self._prefetch_issue_budgets.pop(command, None)
        if issued:
            self.counts["execution_handoff_bursts"] += 1
            self.counts["execution_handoff_burst_nodes"] += len(issued)
        return tuple(issued)

    def _release_prefetch_service_lease(
        self, command_id: str, reason: str, *, request: object | None = None,
        keep_tracking: bool = False,
    ) -> None:
        lease = self._prefetch_service_leases.get(command_id)
        if lease is None or command_id in self._prefetch_lock_release_failures:
            return
        if keep_tracking and lease.lock_params is None:
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
            self._prefetch_service_leases[command_id] = replace(
                lease, lock_params=None, protected_bytes=0,
            )
        if keep_tracking:
            self._lease_refresh_signature = None
            self.counts[f"prefetch_protection_revoked:{reason}"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "prefetch_protection_revoked",
                    "ts_ms": time.time() * 1000., "command_id": command_id,
                    "source": lease.source, "reason": reason,
                    "context_id": lease.key.context_id,
                    "context_epoch": lease.key.context_epoch,
                    "node_id": lease.node_id, "node_creation_time": lease.creation_time,
                    "protected_bytes": lease.protected_bytes,
                    "host_native_locked": lease.host_lock_params is not None,
                    "scope": "own optional device lock released; tracking retained; not freed bytes",
                })
            return
        if lease.host_lock_params is not None:
            # Record the successful device unlock before any later operation
            # can fail; a retained Host receipt must never unlock it twice.
            self._prefetch_service_leases[command_id] = replace(
                lease, lock_params=None, protected_bytes=0,
            )
            try:
                invocation = self.graph.invocations.get(lease.key.invocation_id)
                reclaim = getattr(
                    self._native_cache, "reclaim_beliefkv_expired_tool_prefetch", None,
                )
                if (
                    reason == "service_window_expired" and lease.source == "tool_wait"
                    and not lease.demand_ready and callable(reclaim)
                    and not self.physical_disabled
                    and self.context_sessions.get(lease.key.context_id) == lease.key
                    and invocation is not None and invocation.state is InvocationState.WAIT_TOOL
                    and invocation.active_tool_calls
                    and invocation.updated_ts_ms == lease.wait_revision
                    and not self._terminal(lease.key)
                ):
                    amounts = dict(lease.pool_bytes)
                    self.counts["tool_prefetch_expiry_reclaim_calls"] += 1
                    freed = reclaim(
                        lease.node_id, lease.creation_time,
                        session_id=lease.key.session_id,
                        session_generation=lease.key.session_generation,
                        include_full=amounts.get("kv", amounts.get("full", 0)) > 0,
                        include_mamba=amounts.get("mamba", 0) > 0,
                    )
                    if any(freed.values()):
                        self._invalidate_wait_transfer_observation(lease.key)
                        self.counts["tool_prefetch_expiry_reclaimed_nodes"] += 1
                        self.counts["tool_prefetch_expiry_freed_full_tokens"] += freed.get(0, 0)
                        self.counts["tool_prefetch_expiry_freed_mamba_slots"] += freed.get(2, 0)
                        if self._opportunity_writer is not None:
                            self._opportunity_writer.record({
                                "event": "tool_prefetch_expiry_reclaimed",
                                "ts_ms": time.time() * 1000., "command_id": command_id,
                                "context_id": lease.key.context_id,
                                "context_epoch": lease.key.context_epoch,
                                "node_id": lease.node_id, "freed_units": freed,
                                "scope": "same active tool; retained Host copy; no D2H",
                            })
                self._native_cache.tree_core.dec_host_lock_ref(
                    lease.node_id, lease.host_lock_params,
                )
                self.counts["tool_prefetch_host_lock_released"] += 1
            except (AttributeError, AssertionError, KeyError, TypeError, ValueError, RuntimeError):
                self.physical_disabled = True
                self._prefetch_lock_release_failures.add(command_id)
                self.counts["tool_prefetch_host_release_failed"] += 1
                return
        self._prefetch_service_leases.pop(command_id)
        self._lease_refresh_signature = None
        if (
            reason == "service_window_expired" and lease.demand_ready
            and lease.lock_params is not None and lease.protected_bytes > 0
            and lease.source in ("join_ticket", "tool_wait")
            and not (lease.source == "tool_wait" and self.tool_prefetch_priority_ms > 0)
        ):
            current = self.context_sessions.get(lease.key.context_id)
            if (
                current is not None and self.visible.get(current.request_id) == current
                and current.root_workflow_id == lease.key.root_workflow_id
                and current.invocation_id == lease.key.invocation_id
                and current.attempt_id == lease.key.attempt_id
                and current.session_id == lease.key.session_id
                and current.session_generation == lease.key.session_generation
                and current.context_epoch in (
                    lease.key.context_epoch, lease.key.context_epoch + 1,
                )
                and (
                    current.request_id != lease.key.request_id
                    or lease.source == "join_ticket" and current == lease.key
                )
                and not self._terminal(current)
            ):
                # Release physical protection on time. Only the submitted
                # consumer's identity survives until a real admission attempt.
                self._prefetch_admission_hints[current.request_id] = current
                self.counts["prefetch_admission_hint_retained"] += 1
        evidence = (
            self._tool_prefetch_survival_evidence.get(lease.key)
            if reason == "service_window_expired" and lease.source == "tool_wait"
            else None
        )
        if evidence is not None and not evidence[1]:
            anchor = evidence[0]
            invocation = self.graph.invocations.get(lease.key.invocation_id)
            if (
                invocation is not None and invocation.state is InvocationState.WAIT_TOOL
                and invocation.active_tool_calls
                and self.context_sessions.get(lease.key.context_id) == lease.key
                and lease.wait_revision == anchor.invocation_revision_ts_ms
                and invocation.updated_ts_ms == anchor.invocation_revision_ts_ms
            ):
                self._tool_prefetch_survival_evidence[lease.key] = (anchor, True)
                self._tool_survival_probes.pop(lease.key.context_id, None)
                self._tool_prefetch_next_ms = 0.
                self.counts["tool_prefetch_survival_overrun"] += 1
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "tool_prefetch_survival_overrun",
                        "ts_ms": time.time() * 1000., "command_id": command_id,
                        "context_id": lease.key.context_id,
                        "context_epoch": lease.key.context_epoch,
                        "invocation_revision_ts_ms": anchor.invocation_revision_ts_ms,
                        "forecast_age_ms": (
                            time.monotonic() * 1000. - anchor.issued_monotonic_ms
                        ),
                        "scope": "same active tool survived speculative protection; not completion",
                    })
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
                "host_native_locked": lease.host_lock_params is not None,
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
            or (
                context.epoch != key.context_epoch
                and context.epoch != key.context_epoch + 1
            )
        ):
            return "context_changed"
        current = self.context_sessions.get(key.context_id)
        pending_epoch_handoff = current is None and context.epoch == key.context_epoch + 1
        pending_client_handoff = (
            current is None and not pending_epoch_handoff and lease.source == "join_ticket"
            and self._pending_confirmed_join_submit(key)
        )
        if (
            current is None and not pending_epoch_handoff and not pending_client_handoff
        ) or (current is not None and (
            current.root_workflow_id != key.root_workflow_id
            or current.invocation_id != key.invocation_id
            or (
                current.context_epoch != key.context_epoch
                and current.context_epoch != key.context_epoch + 1
            )
            or current.session_id != key.session_id
            or current.session_generation != key.session_generation
        )):
            return "session_changed"
        if not lease.demand_ready:
            for child_id in invocation.blocking_child_ids:
                child = self.graph.invocations.get(child_id)
                if (
                    child is not None
                    and child.agent_definition_id == "context-summarizer"
                    and child.relation_type is RelationType.CALL
                    and child.parent_invocation_id == key.invocation_id
                    and not child.state.terminal
                ):
                    # The next request is a summary, not this checkpoint's consumer.
                    return "context_summary_started"
        cache = self._native_cache
        try:
            sessions = cache.session_refs
            generations = getattr(sessions, "_session_generations", None)
            if (pending_epoch_handoff or pending_client_handoff) and (
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
            for name, amount in lease.pool_bytes:
                if amount > 0 and node.component_data[
                    0 if name in ("kv", "full") else 2
                ].value is None:
                    return "native_residency_lost"
        except (AttributeError, KeyError, IndexError, RuntimeError, TypeError, ValueError):
            return "native_residency_unobservable"
        if lease.source == "tool_wait" and invocation.state is InvocationState.WAIT_TOOL:
            if context.epoch != key.context_epoch:
                return "wait_episode_changed"
            # Sequential or parallel tools from one model request change the
            # revision without changing this immutable checkpoint's consumer.
        return None

    def _observe_prefetch_client_submit(self, event: RuntimeEvent) -> None:
        if not self._prefetch_service_leases or event.attributes.get("runtime_internal"):
            return
        rid = event.attributes.get("request_id")
        if type(rid) is not str or not rid:
            return
        invocation = self.graph.invocations.get(event.invocation_id)
        if (
            invocation is None or invocation.state is not InvocationState.RUNNING_LLM
            or invocation.active_tool_calls or invocation.blocking_child_ids
        ):
            return
        now = time.monotonic()
        for command, lease in tuple(self._prefetch_service_leases.items()):
            key = lease.key
            if (
                lease.demand_ready or lease.client_submitted_at is not None
                or lease.lock_params is None or now >= lease.expires_at
                or event.workflow_id != key.root_workflow_id
                or event.invocation_id != key.invocation_id
                or event.context_id != key.context_id
                or event.context_epoch != key.context_epoch + 1
                or rid == key.request_id
                or self._prefetch_lease_invalid_reason(lease) is not None
            ):
                continue
            # Client submission precedes native visibility and is not admission.
            deadline = self._prefetch_service_deadline(lease, lease.acknowledged_at + 10.)
            self._prefetch_service_leases[command] = replace(
                lease, client_submitted_at=now, expires_at=deadline,
            )
            self.counts["prefetch_residency_client_submitted"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "prefetch_client_submitted",
                    "ts_ms": time.time() * 1000., "command_id": command,
                    "source": lease.source, "context_id": key.context_id,
                    "context_epoch": key.context_epoch,
                    "request_id": rid, "ack_to_client_submit_ms": (
                        now - lease.acknowledged_at
                    ) * 1000.,
                    "remaining_lease_ms": (deadline - now) * 1000.,
                    "scope": "existing locked restore retained; no native request or admission",
                })

    def _prefetch_service_deadline(self, lease: _PrefetchServiceLease, proposed: float) -> float:
        if lease.source == "tool_wait" and self.tool_prefetch_priority_ms > 0:
            return min(proposed, lease.acknowledged_at + self.tool_prefetch_priority_ms / 1000.)
        return proposed

    def _refresh_prefetch_service_leases(self, *, context_id: str | None = None) -> None:
        if not self._prefetch_service_leases:
            return
        now = time.monotonic()
        signature = (
            self.graph.graph_version, self.semantic_revision,
            self._context_sessions_revision, len(self._prefetch_service_leases),
        )
        if (
            context_id is None and self._prediction_maintenance_interval_ms > 0
            and signature == self._lease_refresh_signature
            and now * 1000. < self._lease_refresh_next_ms
        ):
            return
        leases = tuple(self._prefetch_service_leases.items())
        visible_by_context = None
        submitted_consumer = None
        for command, lease in leases:
            if context_id is not None and lease.key.context_id != context_id:
                continue
            invocation = self.graph.invocations.get(lease.key.invocation_id)
            if (
                not lease.demand_ready and lease.reentry_ready_at is None
                and lease.client_submitted_at is None
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
                    expires_at=self._prefetch_service_deadline(
                        lease, min(lease.acknowledged_at + 10., now + 3.),
                    ),
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
            if not lease.demand_ready and now < lease.expires_at and self.visible:
                if len(leases) > 1 and visible_by_context is None:
                    # A prefix burst has several receipts for the same next consumer.
                    visible_by_context = {
                        item.key.context_id: [] for _, item in leases
                        if context_id is None or item.key.context_id == context_id
                    }
                    for key in self.visible.values():
                        if key.context_id in visible_by_context:
                            visible_by_context[key.context_id].append(key)
                keys = (
                    visible_by_context.get(lease.key.context_id, ())
                    if visible_by_context is not None else self.visible.values()
                )
                # Consecutive receipts sharing a frozen key reuse this scan's match.
                submitted = lease.key is submitted_consumer
                # A client-bound JOIN step already names its eventual consumer.
                if not submitted and keys and any(
                    key.context_id == lease.key.context_id
                    and key.invocation_id == lease.key.invocation_id
                    and key.root_workflow_id == lease.key.root_workflow_id
                    and key.session_id == lease.key.session_id
                    and key.session_generation == lease.key.session_generation
                    and key.context_epoch in (
                        lease.key.context_epoch, lease.key.context_epoch + 1,
                    )
                    and (
                        key.request_id != lease.key.request_id
                        or lease.source == "join_ticket" and key == lease.key
                        and self.context_sessions.get(key.context_id) == key
                        and invocation is not None
                        and invocation.state is InvocationState.RUNNING_LLM
                        and not invocation.active_tool_calls
                        and not invocation.blocking_child_ids
                    )
                    for key in keys
                ):
                    submitted = True
                    submitted_consumer = lease.key
                if submitted:
                    lease = replace(
                        lease, demand_ready=True,
                        expires_at=self._prefetch_service_deadline(lease, (
                            lease.acknowledged_at + 10.
                            if lease.lock_params is not None else lease.expires_at
                        )),
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
        self._lease_refresh_signature = signature if context_id is None else None
        self._lease_refresh_next_ms = min(
            now * 1000. + self._prediction_maintenance_interval_ms,
            min(
                (lease.expires_at * 1000. for lease in self._prefetch_service_leases.values()),
                default=now * 1000.,
            ),
        )

    def _register_prefetch_service_lease(
        self, action: PhysicalActionCompleted, *,
        _locked_prefixes: dict[str, set[tuple[int, int | float]]] | None = None,
    ) -> None:
        pending = self._prefetch_steps.pop(action.command_id, None)
        issue_budget = self._prefetch_issue_budgets.pop(action.command_id, None)
        if action.action != "PREFETCH_GPU" or pending is None:
            return
        service = self._prefetch_first_services.pop(action.command_id, None)
        if service is not None:
            self.counts["prefetch_service_preceded_ledger_ack"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "prefetch_service_before_ledger_ack",
                    "ts_ms": time.time() * 1000., "command_id": action.command_id,
                    "request_id": service[1], "first_service_ts_ms": service[0],
                    "scope": "service observed; reuse requires native identity evidence",
                })
            return
        step, source, revision = pending
        self._reentry_observations.pop(step.key, None)
        self._residency_ordering_hints.pop(step.key, None)
        now = time.monotonic()
        lock_params, protected_bytes = None, 0
        cache = self._native_cache
        lease = _PrefetchServiceLease(
            step.key, action.command_id, step.node_id, step.creation_time,
            source, revision, now,
            now + (
                3. if source == "execution_handoff"
                else self.tool_prefetch_priority_ms / 1000.
                if source == "tool_wait" and self.tool_prefetch_priority_ms > 0
                else (self.prefetch_lead_ms + 1000.) / 1000.
            ), action.pool_bytes,
            demand_ready=source == "execution_handoff",
        )
        reason = self._prefetch_lease_invalid_reason(lease)
        if reason is not None:
            self.counts[f"prefetch_residency_registration_skipped:{reason}"] += 1
            return
        if not self.enable_prefetch_residency_protection:
            self._prefetch_service_leases[action.command_id] = lease
            self._lease_refresh_signature = None
            self.counts["prefetch_residency_registered"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "prefetch_residency_registered", "ts_ms": time.time() * 1000.,
                    "command_id": action.command_id, "source": source,
                    "context_id": step.key.context_id, "context_epoch": step.key.context_epoch,
                    "session_id": step.key.session_id,
                    "session_generation": step.key.session_generation,
                    "node_id": step.node_id, "node_creation_time": step.creation_time,
                    "pool_bytes": dict(action.pool_bytes),
                    "lease_ms": (lease.expires_at - now) * 1000.,
                    "native_locked": False, "host_native_locked": False,
                    "protected_bytes": 0, "protection_reason": "disabled",
                    "protected_by_command_id": None, "budget": None,
                    "residency_issue_budget": (
                        vars(issue_budget).copy() if issue_budget is not None else None
                    ),
                    "protection_request_slots": 0, "protection_slot_role": None,
                    "scope": "first-service tracking only; no extra post-ACK lock",
                })
            return
        budget = self._residency_budget
        protection_slots = budget.request_slots
        protection_reason = "native_protection_unobservable"
        protected_by_command_id = None
        try:
            already_protected = False
            for existing in self._prefetch_service_leases.values():
                if existing.key != step.key or existing.lock_params is None:
                    continue
                if (existing.node_id, existing.creation_time) == (
                    step.node_id, step.creation_time,
                ):
                    already_protected = True
                    protected_by_command_id = existing.command_id
                    break
                if _locked_prefixes is not None and (
                    prefix := _locked_prefixes.get(existing.command_id)
                ) is not None:
                    if (step.node_id, step.creation_time) in prefix:
                        already_protected = True
                        protected_by_command_id = existing.command_id
                        break
                    continue
                current = cache.tree_core.node_by_id(existing.node_id)
                visited = set()
                while current is not None and current.id not in visited:
                    visited.add(current.id)
                    if current.id == step.node_id and current.creation_time == step.creation_time:
                        already_protected = True
                        protected_by_command_id = existing.command_id
                        break
                    current = current.parent
                if already_protected:
                    break
            observation = None
            superseded = []
            if not already_protected:
                # Existing native locks cover their ancestors; only a new or
                # deeper lock needs a fresh closure and byte-budget calculation.
                from beliefkv.runtime.sglang_v0520_observer import observe_unified_node_closure
                observation = observe_unified_node_closure(cache, step.node_id)
                entries = cache.host_pool_group.entry_map
                protected_bytes = sum(
                    node.full_device_tokens * entries["kv"].host_pool.size_per_token
                    for node in observation.nodes
                ) + sum(
                    int(node.mamba_device_present) * entries["mamba"].host_pool.size_per_token
                    for node in observation.nodes
                    if node.node_id == step.node_id and node.mamba_device_present
                )
                ancestors = {
                    (node.node_id, node.creation_time) for node in observation.nodes
                }
                superseded = [
                    existing for existing in self._prefetch_service_leases.values()
                    if existing.key == step.key and existing.lock_params is not None
                    and (existing.node_id, existing.creation_time) in ancestors
                ]
            if not already_protected:
                budget = self._current_residency_budget(
                    include_evictable=True,
                )
                protection_slots = budget.request_slots
                if (
                    issue_budget is not None
                    and issue_budget.source == budget.source == "native_next_prefill"
                ):
                    # A completed restore uses its issued protection allowance,
                    # while bytes and actual request admission remain live.
                    protection_slots = max(protection_slots, issue_budget.request_slots)
                protection_slot_available = self._prefetch_slot_available(
                    step.key, budget=replace(budget, request_slots=protection_slots),
                    demand_ready=lease.demand_ready,
                )
                protection_bytes_fit = (
                    protected_bytes - sum(item.protected_bytes for item in superseded)
                    <= self._prefetch_byte_allowance(budget)
                )
            if already_protected:
                protection_reason = "covered_by_existing_lock"
            elif (
                observation.observable
                and protection_slots > 0
                and protection_slot_available and protection_bytes_fit
            ):
                try:
                    lock_params = cache.tree_core.inc_lock_ref(step.node_id).to_dec_params()
                except (AttributeError, AssertionError, KeyError, TypeError, ValueError, RuntimeError):
                    self.physical_disabled = True
                    self.counts["prefetch_native_lock_acquire_failed"] += 1
                    return
                if getattr(lock_params, "node_id", None) != step.node_id:
                    self.physical_disabled = True
                    protection_reason = "native_lock_receipt_invalid"
                    self.counts["prefetch_native_lock_receipt_invalid"] += 1
                    self._prefetch_lock_release_failures.add(action.command_id)
                else:
                    self.counts["prefetch_native_lock_acquired"] += 1
                    protection_reason = "native_locked"
                    # Acquire the descendant first, then relinquish ancestor
                    # locks. Keep every node's receipt and first-use tracking.
                    for prior in superseded:
                        try:
                            cache.tree_core.dec_lock_ref(prior.node_id, prior.lock_params)
                        except (AttributeError, AssertionError, KeyError, TypeError, ValueError, RuntimeError):
                            self.physical_disabled = True
                            self._prefetch_lock_release_failures.add(prior.command_id)
                            self.counts["prefetch_native_lock_release_failed"] += 1
                            break
                        self._prefetch_service_leases[prior.command_id] = replace(
                            prior, lock_params=None, protected_bytes=0,
                        )
                        self.counts["prefetch_prefix_protection_coalesced"] += 1
                        if self._opportunity_writer is not None:
                            self._opportunity_writer.record({
                                "event": "prefetch_prefix_protection_coalesced",
                                "ts_ms": time.time() * 1000.,
                                "command_id": prior.command_id,
                                "protected_by_command_id": action.command_id,
                                "context_id": step.key.context_id,
                                "context_epoch": step.key.context_epoch,
                            })
                    if protection_slots > budget.request_slots:
                        self.counts["prefetch_issued_slot_protection_preserved"] += 1
            else:
                protection_reason = (
                    "closure_unobservable" if not observation.observable
                    else "no_frontier_slot" if protection_slots < 1
                    else "frontier_slot_occupied" if not protection_slot_available
                    else "byte_budget_exceeded"
                )
                protected_bytes = 0
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
            protected_bytes = 0
        lease = replace(lease, lock_params=lock_params, protected_bytes=protected_bytes)
        self._prefetch_service_leases[action.command_id] = lease
        self._lease_refresh_signature = None
        retain_host = getattr(cache, "retain_beliefkv_prefetch_host", None)
        if (
            source == "tool_wait" and callable(retain_host)
            and not self.physical_disabled
            and (lock_params is not None or protected_by_command_id is not None)
        ):
            amounts = dict(action.pool_bytes)
            try:
                host_lock_params = retain_host(
                    step.node_id, step.creation_time,
                    include_full=amounts.get("kv", amounts.get("full", 0)) > 0,
                    include_mamba=amounts.get("mamba", 0) > 0,
                )
                if host_lock_params is not None:
                    lease = replace(lease, host_lock_params=host_lock_params)
                    self._prefetch_service_leases[action.command_id] = lease
                    if getattr(host_lock_params, "node_id", None) != step.node_id:
                        raise ValueError("prefetch Host receipt names another node")
                    self.counts["tool_prefetch_host_lock_acquired"] += 1
                else:
                    self.counts["tool_prefetch_host_copy_unavailable"] += 1
            except (AttributeError, AssertionError, KeyError, TypeError, ValueError, RuntimeError):
                self.physical_disabled = True
                self._prefetch_lock_release_failures.add(action.command_id)
                self.counts["tool_prefetch_host_lock_acquire_failed"] += 1
                return
        reason = self._prefetch_lease_invalid_reason(lease)
        if reason is not None:
            self._release_prefetch_service_lease(action.command_id, reason)
            self.counts[f"prefetch_residency_registration_skipped:{reason}"] += 1
            return
        if lock_params is not None and _locked_prefixes is not None:
            _locked_prefixes[action.command_id] = ancestors
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
                "host_native_locked": lease.host_lock_params is not None,
                "protected_bytes": protected_bytes,
                "protection_reason": protection_reason,
                "protected_by_command_id": protected_by_command_id,
                "budget": None if already_protected else vars(budget).copy(),
                "residency_issue_budget": vars(issue_budget).copy() if issue_budget is not None else None,
                "protection_request_slots": None if already_protected else protection_slots,
                "protection_slot_role": (
                    None if already_protected else
                    ("demand" if lease.demand_ready else "speculative")
                    if budget.source == "native_next_prefill" else "combined"
                ),
                "scope": "bounded restore protection; native admission remains authoritative",
            })

    def _observe_native_admission_capacity(
        self, *, running_batch: object, adder: object,
    ) -> None:
        self._native_running_batch = running_batch
        if adder is None:
            return
        maximum = getattr(adder, "max_running_requests", None)
        if type(maximum) is not int or maximum <= 0:
            return
        # Restore budget is for the next batch; this adder's selections expire
        # with its current prefill and must not block subsequent decode overlap.
        limits = [
            value for name in ("max_prefill_bs", "prefill_max_requests")
            if type(value := getattr(adder, name, None)) is int and value > 0
        ]
        self._native_max_running = maximum
        self._native_prefill_slots = max(0, min(limits, default=maximum))
        self._native_page_size = max(1, int(getattr(adder, "page_size", 1)))
        self._native_input_reserve = max(
            0, int(getattr(adder, "rem_input_tokens", 0)),
        )
        chunk_tokens = getattr(adder, "rem_chunk_tokens", None)
        if type(chunk_tokens) is int and getattr(adder, "dllm_config", None) is None:
            if chunk_tokens > 0:
                self._native_chunk_input_reserve = max(
                    self._native_chunk_input_reserve or 0, chunk_tokens,
                )
            # Reserve the observed chunk bound for the next batch, even when
            # the current continuation has exhausted its remaining budget.
            if self._native_chunk_input_reserve is not None:
                self._native_input_reserve = min(
                    self._native_input_reserve, self._native_chunk_input_reserve,
                )

    def _current_residency_budget(
        self, *, include_evictable: bool = False,
        headroom: StaticPoolHeadroomObservation | None = None,
    ) -> PrefetchResidencyBudget:
        self._residency_request_counts = None
        if self._native_max_running is None or self._native_prefill_slots is None:
            return self._residency_budget
        cache = self._native_cache
        # An opportunity observation is read-only and belongs to this same
        # synchronous planning call; reuse its free lists, not an older budget.
        if headroom is None:
            headroom = observe_static_full_mamba_headroom(cache)
        self._residency_headroom = headroom
        try:
            rows = cache.req_to_token_pool.available_size()
            entries = cache.host_pool_group.entry_map
            full_unit = entries["kv"].host_pool.size_per_token
            mamba_unit = entries["mamba"].host_pool.size_per_token if "mamba" in entries else 0
            if (
                not headroom.observable or type(rows) is not int or rows < 0
                or type(full_unit) is not int or full_unit <= 0
                or type(mamba_unit) is not int or mamba_unit < 0
            ):
                self._residency_budget = PrefetchResidencyBudget(4, 1024 ** 3)
                return self._residency_budget
            running = len(getattr(self._native_running_batch, "reqs", ()))
            row_capacity = getattr(cache.req_to_token_pool, "size", None)
            occupied_rows = (
                row_capacity - rows
                if type(row_capacity) is int and 0 <= rows <= row_capacity else None
            )
            self._residency_request_counts = (
                running, rows, row_capacity if type(row_capacity) is int else None,
            )
            full_evictable, mamba_evictable = 0, 0
            if include_evictable:
                # Demand handoff pins existing pages for the next native batch;
                # it does not allocate these bytes a second time.
                try:
                    full_count = cache.full_evictable_size()
                    mamba_count = cache.mamba_evictable_size() if mamba_unit else 0
                    if (
                        type(full_count) is int and type(mamba_count) is int
                        and 0 <= full_count <= cache.token_to_kv_pool_allocator.size
                        and (
                            mamba_unit == 0 and mamba_count == 0
                            or 0 <= mamba_count <= cache.req_to_token_pool.mamba_pool.size
                        )
                    ):
                        full_evictable, mamba_evictable = full_count, mamba_count
                except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
                    pass
            self._residency_budget = native_residency_budget(
                running_requests=running,
                max_running_requests=self._native_max_running,
                available_request_rows=rows,
                prefill_slots=self._native_prefill_slots,
                page_size=self._native_page_size,
                input_token_reserve=self._native_input_reserve,
                full_free_tokens=headroom.device_full_free_tokens,
                mamba_free_slots=headroom.device_mamba_free_slots,
                full_bytes_per_token=full_unit,
                mamba_bytes_per_slot=mamba_unit,
                protected_bytes=sum(
                    lease.protected_bytes
                    for lease in self._prefetch_service_leases.values()
                ),
                evictable_full_tokens=full_evictable,
                evictable_mamba_slots=mamba_evictable,
                restore_frontier_slots=(
                    self._wait_prefetch_contexts if self._parallel_wait_prefetch else 1
                ),
                occupied_request_rows=occupied_rows,
            )
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
            self._residency_budget = PrefetchResidencyBudget(4, 1024 ** 3)
        return self._residency_budget

    def _prefetch_slot_available(
        self, key: PrefillCandidateKey, *, budget: PrefetchResidencyBudget | None = None,
        demand_ready: bool = False,
    ) -> bool:
        if budget is None:
            budget = self._current_residency_budget()
        locked = [
            lease for lease in self._prefetch_service_leases.values()
            if lease.lock_params is not None
        ]
        if budget.request_slots < 1:
            return False
        if any(lease.key == key for lease in locked):
            return True
        # Demand restoration feeds the next native batch. Speculative cache
        # restoration can overlap it without allocating a request row; both
        # remain subject to the same live byte and native allocator budgets.
        occupied = {
            lease.key for lease in locked
            if budget.source != "native_next_prefill"
            or lease.demand_ready == demand_ready
        }
        if self._parallel_wait_prefetch:
            sessions = {
                (item.context_id, item.session_id, item.session_generation) for item in occupied
            }
            sessions.update(
                self.physical_ledger.pending_h2d_contexts
            )
            return (
                key.context_id, key.session_id, key.session_generation,
            ) in sessions or len(sessions) < min(
                budget.request_slots, self._wait_prefetch_contexts,
            )
        return key in occupied or len(occupied) < budget.request_slots

    def _executable_waiting_key(
        self, req: object, *, key: PrefillCandidateKey | None = None,
    ) -> PrefillCandidateKey | None:
        # Supplied keys come from the same synchronous planning call.
        if key is None:
            key = _request_key(req, known_keys=self.visible)
        if (
            key is None or self.visible.get(key.request_id) != key
            or self.context_sessions.get(key.context_id) != key
            or key.session_id is None or key.session_generation is None
            or self._terminal(key)
        ):
            return None
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            invocation is None or context is None
            or invocation.state not in (InvocationState.READY, InvocationState.RUNNING_LLM)
            or invocation.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or context.workflow_id != key.root_workflow_id
            or context.epoch != key.context_epoch
            or invocation.active_tool_calls or invocation.blocking_child_ids
        ):
            return None
        return key

    @timed_runtime("reentry_inspection")
    def _read_request_reentry(
        self, req: object, *, refresh: bool = False,
        key: PrefillCandidateKey | None = None,
    ) -> dict | None:
        key = self._executable_waiting_key(req, key=key)
        cache = self._native_cache
        if key is None or cache is None:
            return None
        now_ms = time.monotonic() * 1000.
        cached = self._reentry_observations.get(key)
        if not refresh and cached is not None and cached[0] > now_ms:
            return cached[1]
        inspect = getattr(cache, "inspect_beliefkv_reentry", None)
        if not callable(inspect):
            return None
        try:
            observation = inspect(req)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
            observation = None
            self.counts["reentry_observation_unavailable"] += 1
        self._reentry_observations[key] = (now_ms + 50., observation)
        if len(self._reentry_observations) > 512:
            self._reentry_observations = {
                item: value for item, value in self._reentry_observations.items()
                if value[0] > now_ms and self.visible.get(item.request_id) == item
            }
        return observation

    @timed_runtime("residency_hint")
    def _read_request_residency_hint(
        self, req: object, *, key: PrefillCandidateKey,
    ) -> dict | None:
        key = self._executable_waiting_key(req, key=key)
        if key is None or self._native_cache is None:
            return None
        now_ms = time.monotonic() * 1000.
        cached = self._residency_ordering_hints.get(key)
        if cached is not None and cached[0] > now_ms:
            self.counts["residency_ordering_hint_reused"] += 1
            return cached[1]
        observation = inspect_session_residency_hint(self._native_cache, req)
        if observation is None:
            self.counts["residency_ordering_hint_unavailable"] += 1
            return self._read_request_reentry(req, key=key)
        self._residency_ordering_hints[key] = (now_ms + 50., observation)
        self.counts["residency_ordering_hint_read"] += 1
        if len(self._residency_ordering_hints) > 512:
            self._residency_ordering_hints = {
                item: value for item, value in self._residency_ordering_hints.items()
                if value[0] > now_ms and self.visible.get(item.request_id) == item
            }
        return observation

    def _request_reentry_anchors(self, req: object) -> ContextSessionAnchors | None:
        key = _request_key(req, known_keys=self.visible)
        observation = self._read_request_reentry(req, key=key)
        if key is None or observation is None:
            return None
        try:
            leaves = tuple(
                (
                    component,
                    tuple(
                        (node_id, normalize_native_creation_time(created))
                        for node_id, created in component_leaves
                    ),
                )
                for component, component_leaves in observation["component_leaves"]
            )
        except (KeyError, TypeError, ValueError):
            return None
        return ContextSessionAnchors(
            key, leaves, time.monotonic(),
            reusable_input_tokens=observation["reusable_input_tokens"],
        )

    def _live_execution_handoff(self, ticket: _ExecutionHandoffTicket) -> bool:
        return bool(
            (self.enable_execution_handoff or self.enable_frontier_restore) and not self.physical_disabled
            and not (self.enable_frontier_restore and (
                self._join_ticket is not None or self._confirmed_join_tickets
                or self._eos_prefetch_keys
                or self._critical_parent_demand is not None
            ))
            and time.monotonic() < ticket.expires_at
            and self._executable_waiting_key(ticket.request) == ticket.key
            and (self._agent_admission is None or self._agent_admission.allows(ticket.key))
        )

    def _restore_tier_arguments(self, tier: str) -> dict:
        return (
            {"restore_tier": tier}
            if self.restore_tiers == "dependency" and self._restore_tiers_supported
            else {}
        )

    def _live_frontier_restore_consumer(self) -> _ExecutionHandoffTicket | None:
        ticket = self._frontier_restore_consumer
        if ticket is not None and not self._live_execution_handoff(ticket):
            self._frontier_restore_consumer = None
            self.counts["frontier_restore_selection_released"] += 1
            return None
        return ticket

    def _frontier_probes_backed_off(
        self, waiting_queue: Sequence[object], *, now_ms: float,
    ) -> bool:
        inspected = 0
        for request in waiting_queue:
            if getattr(request, "beliefkv_metadata", None) is None:
                continue
            if inspected == 512:
                break
            key = _request_key(request, known_keys=self.visible)
            if (
                key is None or self.visible.get(key.request_id) != key
                or now_ms >= self._frontier_probe_after_ms.get(key, 0.)
            ):
                return False
            inspected += 1
        return True

    def _frontier_cold_reclaim_available(self, now_ms: float) -> bool:
        if (
            not self.enable_frontier_cold_reclaim
            or now_ms < self._frontier_reclaim_after_ms
        ):
            return False
        backed = getattr(
            getattr(self._native_cache, "tree_core", None), "full_host_duplicates", None,
        )
        return bool(
            type(backed) is dict and backed
            and callable(getattr(
                self._native_cache, "reclaim_beliefkv_handoff_capacity", None,
            ))
        )

    def _fit_selected_frontier_restore(
        self, key: PrefillCandidateKey, opportunity: SessionH2DOpportunity, *,
        budget: PrefetchResidencyBudget, now_ms: float,
    ) -> SessionH2DOpportunity | None:
        free = self._budget_wait_prefetch_opportunity(
            key, opportunity, fit_current_capacity=True, batched=True, budget=budget,
        )
        selected_queue = self.frontier_cold_reclaim_policy == "selected_queue"
        checkpoint = opportunity.checkpoint_plan
        minimum_full_free_tokens = (
            checkpoint.full_tokens + budget.reserve_full_tokens
            if selected_queue and checkpoint is not None else 0
        )
        if selected_queue and checkpoint is None:
            self.counts["frontier_restore_complete_plan_unavailable"] += 1
            return None
        if free.steps and (
            not selected_queue
            or opportunity.headroom.device_full_free_tokens >= minimum_full_free_tokens
        ):
            # A first oversized extent can still supply a native prefix fragment.
            return self._reclaim_wait_prefetch_capacity(
                key, free, source="execution_handoff",
                admission_candidate=True, allow_reclaim=False,
            )
        if not self._frontier_cold_reclaim_available(now_ms):
            return None
        self._frontier_reclaim_after_ms = now_ms + 200.
        planned = self._budget_wait_prefetch_opportunity(
            key, opportunity, fit_current_capacity=False, batched=True,
        )
        if not planned.steps:
            return None
        self.counts["frontier_restore_cold_capacity_checks"] += 1
        fitted = self._reclaim_wait_prefetch_capacity(
            key, planned, source="execution_handoff",
            admission_candidate=True,
            # One GiB-scale checkpoint can span more than sixteen native extents.
            max_reclaim_nodes=64 if selected_queue else 16,
            minimum_full_free_tokens=minimum_full_free_tokens,
        )
        if selected_queue and (
            fitted is None
            or fitted.headroom.device_full_free_tokens < minimum_full_free_tokens
        ):
            # A fitting extent alone does not cover native's eventual input
            # allocation. Native still owns the full admission/compute decision.
            self.counts["frontier_restore_complete_capacity_unavailable"] += 1
            return None
        return fitted

    @timed_runtime("frontier_restore")
    def dispatch_frontier_restore(
        self, waiting_queue: Sequence[object], *, running_batch: object,
    ) -> None:
        """Issue once at an executable frontier; never hold admission for a ticket."""
        if (
            not self.enable_frontier_restore or self.physical_disabled or not waiting_queue
            or self._native_cache is None or self._execution_handoff is not None
            or self._join_ticket is not None or self._confirmed_join_tickets
            or self._eos_prefetch_keys
            or self._critical_parent_demand is not None
            or self.physical_ledger.pending_action_count("PREFETCH_GPU")
        ):
            return
        if self._live_frontier_restore_consumer() is not None:
            self.counts["frontier_restore_selection_pending"] += 1
            return
        if self._prefetch_priority_normal_admissions < self.frontier_restore_ordinary_quota:
            return
        now_ms = time.monotonic() * 1000.
        if now_ms < self._execution_handoff_next_ms:
            return
        self._execution_handoff_next_ms = now_ms + 50.
        self._observe_native_admission_capacity(running_batch=running_batch, adder=None)
        budget = self._current_residency_budget()
        slots = budget.request_slots
        if slots < 1:
            self.counts["frontier_restore_no_request_slot"] += 1
            return
        if budget.byte_limit <= 0 and not self._frontier_cold_reclaim_available(now_ms):
            self.counts["frontier_restore_no_free_byte_budget"] += 1
            return
        if self._frontier_probe_after_ms and self._frontier_probes_backed_off(
            waiting_queue, now_ms=now_ms,
        ):
            self.counts["frontier_restore_backoff_skipped_plans"] += 1
            return
        if not callable(getattr(self._native_cache, "prefetch_gpu_session_nodes", None)):
            return
        available_commands = self.physical_ledger.max_pending - self.physical_ledger.pending_count
        if available_commands < 1:
            return
        plan = self.plan_native_prefill(waiting_queue, running_batch=running_batch, adder=None)
        by_id = {getattr(request, "rid", None): request for request in waiting_queue}
        if len(self._frontier_probe_after_ms) > 512:
            self._frontier_probe_after_ms = {
                key: until for key, until in self._frontier_probe_after_ms.items()
                if until > now_ms and key.request_id in by_id
                and self.visible.get(key.request_id) == key
            }
        frontier = plan.prioritized[:slots]
        selected_head_id = frontier[0].request_id if frontier else None
        if len(plan.prioritized) != len(waiting_queue):
            selection = select_native_prefill_candidates(
                waiting_queue, plan=plan, current_semantic_revision=self.semantic_revision,
            )
            frontier_ids = {
                getattr(request, "rid", None)
                for request in selection.candidates[:slots]
            }
            frontier = tuple(
                key for key in plan.prioritized if key.request_id in frontier_ids
            )
            selected_head_id = (
                getattr(selection.candidates[0], "rid", None)
                if selection.candidates else None
            )
        if self.enable_frontier_cold_reclaim:
            frontier = tuple(
                key for key in frontier[:1] if key.request_id == selected_head_id
            )
        probes = 0
        for key in frontier:
            if now_ms < self._frontier_probe_after_ms.get(key, 0.):
                continue
            # Backed-off heads do not consume this pass's inspection budget.
            if probes == 2:
                break
            probes += 1
            self._frontier_probe_after_ms[key] = now_ms + 200.
            request = by_id.get(key.request_id)
            if request is None or self._executable_waiting_key(request) != key:
                continue
            snapshot = getattr(
                getattr(self._native_cache, "session_refs", None),
                "snapshot_latest_session_leaf_anchors", None,
            )
            leaves = (
                snapshot(key.session_id, key.session_generation, max_leaves=8)
                if callable(snapshot) else None
            )
            if leaves is None:
                # New contexts may share global cache, but native matching can
                # handle them without a second long-input prefix search here.
                # Missing anchors can still be published while this request waits.
                continue
            if has_host_backed_missing_copy(self._native_cache, ContextSessionAnchors(
                key, leaves, now_ms / 1000.,
            )) is False:
                # All anchored states, including historical ones, have no
                # recoverable missing copy. Unknown metadata still falls through.
                self.counts["frontier_restore_empty_metadata_checks"] += 1
                continue
            observed = self._read_request_reentry(request, key=key)
            if observed is None or not (
                observed["missing_full_tokens"] or observed["missing_mamba_slots"]
            ):
                # Queued resident pages may be evicted without changing the key.
                continue
            anchors = self._request_reentry_anchors(request)
            if anchors is None:
                continue
            opportunity = inspect_session_h2d_opportunity(
                self._native_cache, anchors,
                max_steps=min(64, available_commands),
                fit_current_capacity=not self.enable_frontier_cold_reclaim,
                **(
                    {"include_checkpoint_plan": True}
                    if self.frontier_cold_reclaim_policy == "selected_queue" else {}
                ),
            )
            ticket = _ExecutionHandoffTicket(key, request, time.monotonic() + 2.)
            self._execution_handoff = ticket
            try:
                if (
                    self.enable_frontier_cold_reclaim
                    and opportunity is not None and opportunity.steps
                ):
                    opportunity = self._fit_selected_frontier_restore(
                        key, opportunity, budget=budget, now_ms=now_ms,
                    )
                if (
                    opportunity is None or not opportunity.steps
                    or opportunity.fits_current_free_lists is not True
                ):
                    self.counts["frontier_restore_native_fallback"] += 1
                    continue
                commands = self._issue_handoff_prefetch_steps(ticket, opportunity.steps)
            finally:
                self._execution_handoff = None
            if commands:
                ticket.expires_at = time.monotonic() + 1.5
                ticket.command_ids = commands
                self._frontier_restore_consumer = ticket
                self._frontier_probe_after_ms[key] = float("inf")
                self.counts["frontier_restore_issued"] += len(commands)
                self.counts["frontier_restore_beneficiaries"] += 1
                return

    def _clear_execution_handoff(self, reason: str) -> None:
        ticket = self._execution_handoff
        if ticket is None:
            return
        self.counts[f"execution_handoff_closed:{reason}"] += 1
        if reason == "no_cold_capacity":
            self._execution_handoff_capacity_blocked.add(ticket.key)
        else:
            self._execution_handoff_capacity_blocked.discard(ticket.key)
        if self._opportunity_writer is not None:
            self._opportunity_writer.record({
                "event": "execution_handoff_closed", "ts_ms": time.time() * 1000.,
                "request_id": ticket.key.request_id, "context_id": ticket.key.context_id,
                "reason": reason, "issued_nodes": ticket.issued_nodes,
            })
        self._execution_handoff = None

    def _handoff_candidate_keys(
        self, waiting_queue: Sequence[object],
    ) -> tuple[tuple[int, PrefillCandidateKey | None], ...]:
        candidates = []
        for index, request in enumerate(waiting_queue):
            if getattr(request, "beliefkv_metadata", None) is None:
                continue
            if len(candidates) == 512:
                break
            candidates.append((index, _request_key(request, known_keys=self.visible)))
        return tuple(candidates)

    def _handoff_candidates_already_attempted(
        self, waiting_queue: Sequence[object], *,
        candidates: tuple[tuple[int, PrefillCandidateKey | None], ...] | None = None,
    ) -> bool:
        """Skip planning only when its entire tagged candidate set is exhausted."""
        if candidates is None:
            candidates = self._handoff_candidate_keys(waiting_queue)
        return all(
            key is not None and key in self._execution_handoff_attempted
            for _, key in candidates
        )

    def _aged_handoff_frontier_already_attempted(
        self, waiting_queue: Sequence[object], *, frontier_slots: int, now_ms: float,
        candidates: tuple[tuple[int, PrefillCandidateKey | None], ...] | None = None,
    ) -> bool:
        """New requests cannot displace an exhausted aged FIFO frontier."""
        cutoff = now_ms / 1000. - 10.
        restore_contexts = {
            lease.key.context_id for lease in self._prefetch_service_leases.values()
            if lease.demand_ready
        }
        aged = []
        if candidates is None:
            candidates = self._handoff_candidate_keys(waiting_queue)
        for index, key in candidates:
            if key is None or self.visible.get(key.request_id) != key:
                return False
            attempted = key in self._execution_handoff_attempted
            if not attempted and (
                key.context_id in restore_contexts
                or self.enable_resident_first
                and key.context_id in self._parent_capacity_handoffs
                and self._live_parent_capacity_handoff(key) is not None
            ):
                return False
            since = self._visible_since.get(key.request_id, now_ms / 1000.)
            if since <= cutoff:
                aged.append((since, index, key))
        if len(aged) < frontier_slots:
            return False
        return all(
            key in self._execution_handoff_attempted and not self._terminal(key)
            for _, _, key in nsmallest(frontier_slots, aged, key=lambda item: item[:2])
        )

    @timed_runtime("execution_handoff")
    def dispatch_execution_handoff(
        self, waiting_queue: Sequence[object], *, running_batch: object,
    ) -> None:
        """Restore one imminent queued beneficiary while other GPU work continues."""
        if self.enable_frontier_restore:
            self.dispatch_frontier_restore(waiting_queue, running_batch=running_batch)
            return
        if not self.enable_execution_handoff or self.physical_disabled or self._native_cache is None:
            return
        ticket = self._execution_handoff
        if ticket is not None:
            if not self._live_execution_handoff(ticket) or not any(
                req is ticket.request for req in waiting_queue
            ):
                self._clear_execution_handoff("expired_or_request_changed")
                ticket = None
            elif ticket.command_id is not None:
                commands = ticket.command_ids or (ticket.command_id,)
                if any(self.physical_ledger.is_pending(command) for command in commands):
                    return
                if not self._physical_actions_completed(commands):
                    self._clear_execution_handoff("ack_unavailable")
                    return
                ticket.command_id = None
                ticket.command_ids = ()
                self.counts["execution_handoff_acked"] += len(commands)
        now_ms = time.monotonic() * 1000.
        if ticket is None and now_ms < self._execution_handoff_next_ms:
            return
        if self.physical_ledger.pending_action_count("PREFETCH_GPU"):
            return
        if self.physical_ledger.pending_count >= self.physical_ledger.max_pending:
            return
        if ticket is None:
            self._execution_handoff_next_ms = now_ms + 50.
            if not waiting_queue:
                self.counts["execution_handoff_no_waiting_requests"] += 1
                return
            self._execution_handoff_attempted = {
                key for key in self._execution_handoff_attempted
                if self.visible.get(key.request_id) == key
            }
            self._execution_handoff_capacity_blocked.intersection_update(
                self._execution_handoff_attempted,
            )
            self._observe_native_admission_capacity(running_batch=running_batch, adder=None)
            candidates = self._handoff_candidate_keys(waiting_queue)
            if self._handoff_candidates_already_attempted(
                waiting_queue, candidates=candidates,
            ):
                self.counts["execution_handoff_candidates_already_attempted"] += 1
                return
            frontier_slots = min(16, self._current_residency_budget().request_slots)
            if frontier_slots < 1:
                self.counts["execution_handoff_no_frontier_slots"] += 1
                return
            if self._aged_handoff_frontier_already_attempted(
                waiting_queue, frontier_slots=frontier_slots, now_ms=now_ms,
                candidates=candidates,
            ):
                self.counts["execution_handoff_aged_frontier_already_attempted"] += 1
                return
            plan = self.plan_native_prefill(
                waiting_queue, running_batch=running_batch, adder=None,
            )
            by_id = {getattr(req, "rid", None): req for req in waiting_queue}
            imminent = plan.prioritized[:frontier_slots]
            if self._parent_capacity_handoffs and self._prefetch_priority_normal_admissions >= 4:
                imminent = sorted(
                    imminent,
                    key=lambda key: self._live_parent_capacity_handoff(key) is None,
                )
                # A Host-only parent otherwise waits for aging before reaching
                # this frontier. Restore one unlocked parent into the next
                # available slot; native dependencies still govern admission.
                checked = 0
                lookahead = (
                    () if any(self._live_parent_capacity_handoff(key) for key in imminent)
                    else plan.prioritized
                )
                for key in lookahead:
                    if (
                        key in imminent or key in self._execution_handoff_attempted
                        or key.context_id not in self._parent_capacity_handoffs
                        or self._live_parent_capacity_handoff(key) is None
                    ):
                        continue
                    checked += 1
                    request = by_id[key.request_id]
                    anchors = self._request_reentry_anchors(request)
                    batched = callable(getattr(self._native_cache, "prefetch_gpu_session_nodes", None))
                    opportunity = (
                        inspect_session_h2d_opportunity(
                            self._native_cache, anchors,
                            max_steps=min(
                                16, self.physical_ledger.max_pending
                                - self.physical_ledger.pending_count,
                            ) if batched else 1,
                            fit_current_capacity=batched,
                        )
                        if anchors is not None else None
                    )
                    if (
                        opportunity is not None and opportunity.step is not None
                        and opportunity.fits_current_free_lists is True
                    ):
                        imminent = [key, *imminent[:frontier_slots - 1]]
                        self.counts["parent_capacity_handoff_restore_lookahead"] += 1
                        if self._opportunity_writer is not None:
                            self._opportunity_writer.record({
                                "event": "parent_capacity_handoff_restore_lookahead",
                                "ts_ms": time.time() * 1000.,
                                "workflow_id": key.root_workflow_id,
                                "invocation_id": key.invocation_id,
                                "request_id": key.request_id, "context_id": key.context_id,
                                "context_epoch": key.context_epoch,
                                "join_id": self._parent_capacity_handoffs[key.context_id].join_id,
                                "frontier_slots": frontier_slots,
                                "queue_wait_ms": (
                                    time.monotonic() - self._visible_since.get(
                                        key.request_id, time.monotonic(),
                                    )
                                ) * 1000.,
                                "semantics": (
                                    "One submitted JOIN-unlocked parent restore after "
                                    "ordinary quota, fitting current free lists; "
                                    "native data dependencies still govern admission."
                                ),
                            })
                        break
                    if checked == 8:
                        break
            for key in imminent:
                request = by_id[key.request_id]
                if key in self._execution_handoff_attempted:
                    continue
                observation = self._read_request_reentry(request)
                if observation is None or not (
                    observation["missing_full_tokens"] or observation["missing_mamba_slots"]
                ):
                    continue
                ticket = _ExecutionHandoffTicket(key, request, time.monotonic() + 2.)
                self._execution_handoff = ticket
                self._execution_handoff_attempted.add(key)
                self.counts["execution_handoff_selected"] += 1
                if self._live_parent_capacity_handoff(key) is not None:
                    self.counts["parent_capacity_handoff_restore_selected"] += 1
                if self._opportunity_writer is not None:
                    self._opportunity_writer.record({
                        "event": "execution_handoff_selected", "ts_ms": time.time() * 1000.,
                        "request_id": key.request_id, "context_id": key.context_id,
                        "context_epoch": key.context_epoch,
                        "session_id": key.session_id, "session_generation": key.session_generation,
                        "semantic_revision": plan.semantic_revision,
                        "checkpoint_tokens": observation["checkpoint_tokens"],
                        "missing_full_tokens": observation["missing_full_tokens"],
                        "missing_mamba_slots": observation["missing_mamba_slots"],
                    })
                break
        if ticket is None:
            return
        anchors = self._request_reentry_anchors(ticket.request)
        batched = callable(getattr(self._native_cache, "prefetch_gpu_session_nodes", None))
        max_steps = min(
            16,
            self.physical_ledger.max_pending - self.physical_ledger.pending_count,
        ) if batched else 1
        if max_steps < 1:
            return
        opportunity = (
            inspect_session_h2d_opportunity(
                self._native_cache, anchors, max_steps=max_steps,
                fit_current_capacity=batched,
            )
            if anchors is not None else None
        )
        if opportunity is None or opportunity.step is None:
            detail = (
                "reentry_anchors_unavailable" if opportunity is None
                else opportunity.no_step_reason or "no_host_backed_step"
            )
            self.counts[f"execution_handoff_no_step:{detail}"] += 1
            if self._opportunity_writer is not None:
                self._opportunity_writer.record({
                    "event": "execution_handoff_no_step", "ts_ms": time.time() * 1000.,
                    "request_id": ticket.key.request_id,
                    "context_id": ticket.key.context_id,
                    "reason": detail,
                    "blocked_detail": (
                        opportunity.blocked_detail if opportunity is not None else None
                    ),
                })
            self._clear_execution_handoff("resident_or_unavailable")
            return
        if opportunity.fits_current_free_lists is not True:
            reclaim = getattr(self._native_cache, "reclaim_beliefkv_handoff_capacity", None)
            if callable(reclaim):
                candidate = getattr(opportunity, "candidate", None)
                excluded = (
                    tuple(node.node_id for node in candidate.nodes)
                    if candidate is not None else ()
                )
                self._publish_parent_pressure_candidates(exclude_node_ids=excluded)
                freed = reclaim(
                    full_tokens=opportunity.required_full_tokens,
                    mamba_slots=opportunity.required_mamba_slots,
                    exclude_node_ids=excluded,
                )
                if freed:
                    self.counts["execution_handoff_cold_reclaimed"] += 1
                    if self._opportunity_writer is not None:
                        self._opportunity_writer.record({
                            "event": "execution_handoff_capacity_reclaimed",
                            "ts_ms": time.time() * 1000., "request_id": ticket.key.request_id,
                            "context_id": ticket.key.context_id, "freed_units": freed,
                            "victims": getattr(self._native_cache, "beliefkv_handoff_last_victims", ()),
                            "evidence": "idle_unlocked_host_ack_settled;actual_h2d_shortfall",
                        })
                opportunity = inspect_session_h2d_opportunity(
                    self._native_cache, anchors, max_steps=max_steps,
                    fit_current_capacity=batched,
                )
            if opportunity.fits_current_free_lists is not True:
                self.counts["execution_handoff_no_cold_capacity"] += 1
                self._clear_execution_handoff("no_cold_capacity")
                return
        commands = (
            self._issue_handoff_prefetch_steps(ticket, opportunity.steps)
            if batched else (
                (command,) if (command := self.issue_prefetch_gpu_step(
                    opportunity.step, source="execution_handoff",
                )) is not None else ()
            )
        )
        if commands:
            ticket.command_id = commands[-1]
            ticket.command_ids = commands
            ticket.issued_nodes += len(commands)
            self.counts["execution_handoff_issued"] += len(commands)
        else:
            self._clear_execution_handoff("native_declined")

    def defer_prefill_for_prefetch(self, req: object) -> bool:
        """Use the native FULL pipeline; defer only unavailable state or legacy loads.

        This runs after the native slot test but before prefix match or running
        admission. A failed/expired step falls back to ordinary PrefillAdder.
        """
        if self.physical_disabled:
            return False
        if (
            not self._prefetch_steps and self._execution_handoff is None
            and not self.enable_admission_prefetch
        ):
            return False
        key = _request_key(req, known_keys=self.visible)
        if key is None:
            return False
        pending_commands = ()
        if self._prefetch_steps and (
            key.session_id is None or key.session_generation is None
            or (key.context_id, key.session_id, key.session_generation)
            in self.physical_ledger.pending_h2d_contexts
        ):
            pending_commands = tuple(
                command for command, (step, source, _) in self._prefetch_steps.items()
                if step.key.context_id == key.context_id
                and step.key.invocation_id == key.invocation_id
                and step.key.root_workflow_id == key.root_workflow_id
                and (step.key.session_id, step.key.session_generation)
                == (key.session_id, key.session_generation)
                and key.context_epoch in (step.key.context_epoch, step.key.context_epoch + 1)
                and self.physical_ledger.is_pending(command)
            )
        if pending_commands:
            can_admit = getattr(self._native_cache, "beliefkv_prefetch_can_admit", None)
            if callable(can_admit):
                ready = can_admit(pending_commands)
                self.counts[
                    "prefetch_native_pipeline_admission" if ready
                    else "prefetch_waiting_state"
                ] += 1
                return not ready
            if self.enable_execution_handoff and any(
                self._prefetch_steps[command][1] == "execution_handoff"
                for command in pending_commands
            ):
                self.counts["execution_handoff_waiting_state_or_legacy_ack"] += 1
                return True
        ticket = self._execution_handoff
        if (
            ticket is not None and ticket.key == key
            and self._live_execution_handoff(ticket) and ticket.issued_nodes > 0
            and not self.can_prefetch_during_overlap()
        ):
            observation = self._read_request_reentry(req)
            if observation is not None and (
                observation["missing_full_tokens"] or observation["missing_mamba_slots"]
            ):
                self.counts["execution_handoff_restoring_prefix"] += 1
                return True
        if not self.enable_admission_prefetch:
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
            and not self._physical_actions_completed(
                (lease.command_id,), action="PREFETCH_GPU",
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
            if self._physical_actions_completed((lease.command_id,), action="PREFETCH_GPU"):
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

    def _fail_native_transfer_receipt(self, commit, error, live_epochs, live_sessions):
        self.physical_disabled = True
        self._discard_prefetch_tracking("physical_receipt_failure")
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

    def on_native_transfer_commit(
        self, commit: object
    ) -> tuple[PhysicalActionCompleted, ...]:
        """Observe synchronized native ACKs, never infer completion from enqueue."""
        if self._full_prefix_migration is not None:
            try:
                handled = self._full_prefix_migration.transfer_completed(commit)
            except PhysicalReceiptError as error:
                self._fail_native_transfer_receipt(commit, error, {}, {})
                return ()
            if handled is True:
                self.counts["full_prefix_native_commits_completed"] += 1
                return ()
        paper = getattr(self, "_paper_baseline", None)
        callback = getattr(paper, "transfer_completed", None)
        if callback is not None:
            try:
                handled = callback(commit)
            except PhysicalReceiptError as error:
                self._fail_native_transfer_receipt(commit, error, {}, {})
                return ()
            if handled is True:
                self.counts["paper_native_commits_completed"] += 1
                return ()
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
        # HTTP registration can lag both an old-epoch transfer and a confirmed
        # JOIN transfer already bound to its next consumer's epoch.
        cache = self._native_cache
        for context_id, epoch, session_id, generation in self.physical_ledger.pending_transfer_sessions:
            if context_id in live_sessions:
                continue
            if live_epochs.get(context_id) == epoch:
                ticket = self._pending_confirmed_join_ticket(context_id, epoch)
                if ticket is None or (
                    ticket.key.session_id, ticket.key.session_generation,
                ) != (session_id, generation):
                    continue
            elif live_epochs.get(context_id) != epoch + 1:
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
            self._fail_native_transfer_receipt(commit, error, live_epochs, live_sessions)
            return ()
        completed = tuple(
            replace(action, source=self._prefetch_steps[action.command_id][1])
            if action.command_id in self._prefetch_steps else action
            for action in completed
        )
        has_prefetch = any(action.action == "PREFETCH_GPU" for action in completed)
        if has_prefetch:
            self._execution_handoff_next_ms = 0.
        self.completed_physical_actions.extend(completed)
        self._join_prepare_commands = {
            identity: command for identity, command in self._join_prepare_commands.items()
            if self.physical_ledger.is_pending(command)
        }
        actual_bytes = getattr(commit, "actual_bytes", None)
        ack_ms = getattr(commit, "submit_to_ack_ms", None)
        direct_d2h = (
            getattr(commit, "direction", None) == "d2h"
            and bool(self._idle_prepare_direct_nodes.intersection(
                getattr(commit, "node_ids", ()),
            ))
        )
        if direct_d2h:
            self._idle_prepare_direct_nodes.difference_update(getattr(commit, "node_ids", ()))
            self.counts["idle_prepare_direct_acks"] += 1
        if (
            getattr(commit, "status", None) == "completed"
            and getattr(commit, "direction", None) == "h2d"
            and type(actual_bytes) is int and actual_bytes > 0
            and type(ack_ms) in (int, float)
            and math.isfinite(ack_ms) and 0 < ack_ms <= 60_000
        ):
            self._h2d_samples.append((actual_bytes, float(ack_ms)))
            self._h2d_service_estimates.clear()
            self.counts["h2d_service_sample"] += 1
        if (
            getattr(commit, "status", None) == "completed"
            and getattr(commit, "direction", None) in ("h2d", "d2h")
            and not direct_d2h
            and type(actual_bytes) is int and actual_bytes > 0
            and type(ack_ms) in (int, float) and math.isfinite(ack_ms)
            and 0 < ack_ms <= 60_000
        ):
            units = dict(getattr(commit, "num_tokens_by_pool", ()) or ())
            full = units.get("kv", units.get("full", 0))
            mamba = units.get("mamba", 0)
            sample = NativeServiceSample(
                actual_bytes, float(ack_ms), getattr(commit, "direction"),
                pool_shape(full, mamba), getattr(commit, "enqueue_to_submit_ms", None),
            )
            history = self._native_service_samples
            evicted = history[0] if history and len(history) == history.maxlen else None
            history.append(sample)
            if sample.direction == "h2d" and sample.shape == "full":
                self._h2d_full_service_samples.append((time.monotonic(), sample))
            # Estimators filter by direction. Preserve the other cache unless
            # this append also evicts its evidence from the shared FIFO.
            if sample.direction == "d2h" or evicted is not None and evicted.direction == "d2h":
                self._d2h_service_estimates.clear()
            elif self._d2h_service_estimates:
                self.counts["d2h_service_cache_retained_after_h2d"] += 1
            if sample.direction == "h2d" or evicted is not None and evicted.direction == "h2d":
                self._h2d_service_estimates.clear()
            elif self._h2d_service_estimates:
                self.counts["h2d_service_cache_retained_after_d2h"] += 1
        if self._cold_full_backups and (
            getattr(commit, "direction", None) == "d2h"
            and getattr(commit, "status", None) == "completed"
        ):
            settled = False
            for node_id in getattr(commit, "node_ids", ()):
                pending = self._cold_full_backups.pop(node_id, None)
                if pending is None:
                    continue
                created, source = pending
                try:
                    node = cache.tree_core.node_by_id(node_id)
                    valid = bool(
                        node.creation_time == created
                        and node.component_data[0].host_value is not None
                        and node.write_through_pending_id is None
                        and node_id not in cache.ongoing_write_through
                    )
                    settled |= valid
                    if valid:
                        self.counts[f"{source}_cold_full_backup_acked"] += 1
                        if source == "spawn":
                            self._spawn_prepare_next_ms = 0.
                except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
                    pass
            if settled and self._live_critical_parent_demand() is not None:
                self._critical_reclaim_next_ms = self._report_candidate_next_ms = 0.
        # A burst's deepest node locks its FULL ancestry once; registering its
        # ancestors first would consume the budget without protecting the state.
        locked_prefixes = {} if has_prefetch and len(completed) > 1 else None
        critical_rearmed = False
        for action in reversed(completed):
            if (
                action.action == "PREPARE_HOST"
                and (key := self.context_sessions.get(action.context_id)) is not None
                and key.context_epoch in (action.context_epoch, action.context_epoch + 1)
            ):
                self._invalidate_wait_transfer_observation(key)
                if self._predicted_tool_offload is not None:
                    self._predicted_tool_offload.next_ms = 0.
                if self._critical_tool_donor(key):
                    self._critical_reclaim_next_ms = 0.
            if (
                not critical_rearmed and self.enable_prepare_host
                and action.action == "PREFETCH_GPU"
                and (critical := self._critical_parent_demand) is not None
                and critical[0].context_id == action.context_id
                and (pending := self._prefetch_steps.get(action.command_id)) is not None
                and pending[1] == "join_ticket"
                and (critical := self._live_critical_parent_demand()) is not None
            ):
                origin = pending[0].key
                parent = self.graph.invocations.get(critical[0].invocation_id)
                ticket = self._confirmed_join_tickets.get(parent.join_id) if parent else None
                if origin == critical[0] or (
                    ticket is not None and ticket.key == critical[0]
                    and ticket.checkpoint_key == origin
                ):
                    self._critical_reclaim_next_ms = 0.
                    critical_rearmed = True
                    self.counts["critical_parent_capacity_rearmed_after_h2d"] += 1
            self._register_prefetch_service_lease(action, _locked_prefixes=locked_prefixes)
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
            self._online_tool_queries.pop(previous, None)
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
        if self._join_eos_arrival_pending:
            self._observe_join_eos_arrival(key, self._visible_since[key.request_id] * 1000.)
        if key.session_id is not None and key.session_generation is not None:
            self.context_sessions[key.context_id] = key
        else:
            self.context_sessions.pop(key.context_id, None)
        self._context_session_changed(key.context_id)
        self.semantic_revision += 1
        self._execution_handoff_next_ms = 0.
        self._refresh_prefetch_service_leases(context_id=key.context_id)
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            paper.registered(req, key)
        if self._agent_admission is not None:
            self._agent_admission.registered(req, key)
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.registered(req, key)
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
            if (key := _request_key(req, known_keys=self.visible)) is not None
            and key.request_id in self.visible
            and self._terminal(key)
        )

    def inspect_native_prefill_queue(
        self, native_order: Sequence[object],
    ) -> _NativePrefillQueueInspection:
        """Share parsed identities with the immediately following advisory plan."""
        revision = (
            self.semantic_revision, self.graph.graph_version,
            self._context_sessions_revision,
        )
        entries, terminal = [], []
        for req in native_order:
            key = _request_key(req, known_keys=self.visible)
            entries.append((req, key))
            if (
                key is not None and key.request_id in self.visible
                and self._terminal(key)
            ):
                terminal.append(key.request_id)
        return _NativePrefillQueueInspection(
            self, *revision, tuple(entries), tuple(terminal),
        )

    def retire_terminal_request(self, request_id: str) -> None:
        if request_id in self.visible:
            if self._agent_admission is not None:
                self._agent_admission.forget(
                    self.visible[request_id].invocation_id, now=time.monotonic(),
                )
            if self._tool_continuity is not None:
                key = self.visible[request_id]
                self._tool_continuity.discard(key.root_workflow_id, key.invocation_id)
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
            previous = self.context_sessions.get(key.context_id)
            if previous is not None and previous != key:
                self._online_tool_queries.pop(previous, None)
            if is_retracted and self._tool_continuity is not None:
                self._tool_continuity.retracted(key)
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
            self._context_session_changed(key.context_id)
            self.demand_hints.pop(key.request_id, None)
            self.semantic_revision += 1
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            requeued = getattr(paper, "requeued", None)
            if requeued is not None:
                requeued(requests, is_retracted=is_retracted)
        if self._agent_admission is not None:
            self._agent_admission.requeued(requests, is_retracted=is_retracted)
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.requeued(requests)

    def _forget_session(self, request_id: str) -> None:
        self._visible_since.pop(request_id, None)
        self._prefetch_admission_hints.pop(request_id, None)
        for context_id, key in tuple(self.context_sessions.items()):
            if key.request_id == request_id:
                self._tool_last_queries.pop(key, None)
                self._online_tool_queries.pop(key, None)
                self._tool_opportunity_cache.pop(key, None)
                del self.context_sessions[context_id]
                self._context_session_changed(context_id)
                self._context_tokens.pop(context_id, None)
                self.tool_wait_hints.pop(context_id, None)
                self.join_wait_hints = {
                    join_id: hint for join_id, hint in self.join_wait_hints.items()
                    if hint.key.context_id != context_id
                }
                self.shadow_candidate = None

    def _causal_rank(
        self,
        key: PrefillCandidateKey | None,
        index: int,
        ready_ranks: dict[str, tuple[int, int]],
    ) -> tuple[int, int, int]:
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

    def _prefill_causal_ranks(
        self, tagged: list[tuple[int, object]], keys: dict[int, PrefillCandidateKey | None],
    ) -> dict[int, tuple[int, int, int]]:
        signature = (
            self.semantic_revision, self.graph.graph_version,
            tuple((index, id(req), keys[index]) for index, req in tagged),
        )
        cached = self._prefill_causal_cache
        if self._prefill_cycle_active and cached is not None and cached[0] == signature:
            self.counts["admission_causal_cache_hits"] += 1
            self._submit_local_predictions(tagged, cached[1])
            return cached[2]
        ready_ranks = {}
        # LLM_SUBMIT marks an invocation RUNNING_LLM before the native waiting
        # request has received its first GPU service. Only inspect bounded
        # candidates supplied by SGLang's waiting queue here.
        for index, req in tagged:
            key = keys[index]
            if (
                key is None
                or key.invocation_id in ready_ranks
                or self.visible.get(key.request_id) != key
            ):
                continue
            workflow = self.graph.workflows.get(key.root_workflow_id)
            if workflow is None or workflow.end_ts_ms is not None:
                continue
            invocation = self.graph.invocations.get(key.invocation_id)
            context = self.graph.contexts.get(key.context_id)
            if (
                invocation is None
                or invocation.state not in (InvocationState.READY, InvocationState.RUNNING_LLM)
                or (
                    invocation.state is InvocationState.RUNNING_LLM
                    and self.context_sessions.get(key.context_id) != key
                )
                or invocation.workflow_id != key.root_workflow_id
                or invocation.context_id != key.context_id
                or context is None
                or context.workflow_id != key.root_workflow_id
                or context.epoch != key.context_epoch
            ):
                continue
            ready_ranks[key.invocation_id] = self.frontier.admission_rank(key.invocation_id)
        self._submit_local_predictions(tagged, ready_ranks)
        ranks = {
            index: self._causal_rank(keys[index], index, ready_ranks)
            for index, req in tagged
        }
        if self._prefill_cycle_active:
            self._prefill_causal_cache = (signature, ready_ranks, ranks, None)
        return ranks

    def _resident_prefill_scan(
        self, ordered: list[tuple[int, object]],
        keys: dict[int, PrefillCandidateKey | None],
    ) -> list[tuple[int, object]]:
        budget = self._current_residency_budget()
        width = (
            min(8, budget.request_slots)
            if budget.source == "native_next_prefill" else 8
        )
        signature = (
            self.semantic_revision, self.graph.graph_version, width,
            tuple((index, id(req), keys[index]) for index, req in ordered) if width else (),
        )
        cached = self._prefill_residency_scan
        if self._prefill_cycle_active and cached is not None and cached[0] == signature:
            self.counts["admission_residency_scan_reused"] += 1
            return cached[1]
        count = len(ordered)
        # Bound the fixed head by imminent slots, but retain eight rotating
        # tail probes so fewer slots do not slow cache-locality discovery.
        # The selected restore still reads its own complete native checkpoint.
        scan = ordered[:width]
        if width == 8:
            scan += [
                ordered[position]
                for offset in range(min(8, count))
                if (position := (self._residency_scan_cursor + offset) % count) >= 8
            ]
            self._residency_scan_cursor = (self._residency_scan_cursor + 8) % count
        elif width and count > width:
            tail_count = count - width
            start = (max(width, self._residency_scan_cursor) - width) % tail_count
            scan += [
                ordered[width + (start + offset) % tail_count]
                for offset in range(min(8, tail_count))
            ]
            self._residency_scan_cursor = width + (start + 8) % tail_count
        elif width:
            self._residency_scan_cursor = 0
        self.counts["admission_residency_scan_targets"] += len(scan)
        if width < 8:
            self.counts["admission_residency_scan_slot_bounded"] += 1
        if self._prefill_cycle_active:
            self._prefill_residency_scan = (signature, scan)
        return scan

    def _final_admission_work(
        self, stage: _ChildFinalStage, key: PrefillCandidateKey,
        request: object, *, now_ms: float,
    ) -> tuple[float | None, str]:
        generated = max(stage.generated_tokens, len(getattr(request, "output_ids", ()) or ()))
        progress = self._semantic_progress.get(key.request_id, ())
        if progress and progress[-1][0] <= now_ms:
            generated = max(generated, progress[-1][1])
        forecast = self._semantic_forecasts.get(key.request_id)
        if (
            forecast is not None
            and forecast.observation.key == key
            and forecast.observation.notice_active
            and forecast.final_score >= self._semantic_score_threshold
            and 0 <= now_ms - forecast.observation.observed_ts_ms <= 1_500.
            and forecast.observation.observed_output_tokens <= generated
        ):
            advanced = generated - forecast.observation.observed_output_tokens
            endpoint = (
                forecast.upper_tokens if self.semantic_work_statistic == "upper"
                else forecast.middle_tokens
            )
            if endpoint <= advanced and self.semantic_work_statistic == "center":
                endpoint = forecast.upper_tokens
            if math.isfinite(endpoint) and endpoint > advanced:
                return endpoint - advanced, "semantic_work"
        # An exhausted endpoint is unknown, not evidence of one remaining token.
        remaining = stage.expected_tokens - generated
        if remaining > 0:
            return float(remaining), "notice_budget"
        return None, "unknown"

    def _request_restore_cost(
        self, request: object, observation: dict | None,
    ) -> tuple[int, int] | None:
        if observation is None or self._residency_pool_bytes is None:
            return None
        values = tuple(observation.get(name) for name in (
            "checkpoint_tokens", "missing_full_tokens", "missing_mamba_slots",
        ))
        if any(type(value) is not int or value < 0 for value in values):
            return None
        checkpoint, full, mamba = values
        inputs = getattr(request, "origin_input_ids", ())
        if not 0 < checkpoint < len(inputs):
            return None
        new_input = len(inputs) - checkpoint
        chunk = self._native_chunk_input_reserve
        if type(chunk) is int and chunk > 0:
            new_input = min(new_input, chunk)
        full_unit, mamba_unit = self._residency_pool_bytes
        return full * full_unit + mamba * mamba_unit, new_input

    def _resident_aged_bypass(
        self, ordered: list[tuple[int, object]],
        keys: dict[int, PrefillCandidateKey | None],
        ranks: dict[int, tuple[int, int, int]],
    ) -> tuple[tuple[int, object], dict] | None:
        if self._residency_pool_bytes is None:
            return None
        normal_admissions = min(
            self._prefetch_priority_normal_admissions,
            self._resident_priority_normal_admissions,
        )
        budget = None
        if normal_admissions < 4:
            budget = self._current_residency_budget()
            if (
                normal_admissions == 0 or budget.source != "native_next_prefill"
                or budget.request_slots <= 0
            ):
                return None
        now_ms = time.monotonic() * 1000.

        def observation(request: object, key: PrefillCandidateKey) -> dict | None:
            if budget is None:
                return self._read_request_residency_hint(request, key=key)
            # Earlier locality turns reuse discovery already paid for in
            # this frontier; no extra ancestry scans to hunt for a hit.
            cached = self._residency_ordering_hints.get(key)
            if cached is None or cached[0] <= now_ms:
                cached = self._reentry_observations.get(key)
            return cached[1] if cached is not None and cached[0] > now_ms else None

        head_index, head = ordered[0]
        head_key = keys[head_index]
        if head_key is None:
            return None
        head_observation = observation(head, head_key)
        head_cost = self._request_restore_cost(head, head_observation)
        if (
            head_cost is None and budget is None
            or head_cost is not None and head_cost[0] == 0
        ):
            return None
        candidates = []
        for pair in self._resident_prefill_scan(ordered, keys):
            index, request = pair
            key = keys[index]
            if (
                index == head_index or key is None
                or ranks[index][:2] > ranks[head_index][:2]
            ):
                continue
            hint = observation(request, key)
            candidate_cost = self._request_restore_cost(request, hint)
            if (
                candidate_cost is None or hint["missing_full_tokens"] != 0
                or head_cost is not None and (
                    candidate_cost[0] >= head_cost[0]
                    or candidate_cost[1] > head_cost[1]
                )
            ):
                continue
            candidates.append((candidate_cost, index, pair))
        if not candidates:
            return None
        selected_cost, _, selected = min(candidates, key=lambda item: item[:2])
        ordinary_quota = 4
        if budget is not None:
            ordinary_quota = max(1, min(4, math.ceil(
                max(0, budget.request_slots - len(candidates)) / len(candidates),
            )))
            if normal_admissions < ordinary_quota:
                return None
            # Aged heads need not retain a discovery hint. A known warm
            # candidate earns only this comparison, not an admission bypass.
            self.counts["resident_aged_native_quota_inspected"] += 1
            selected_observation = self._read_request_reentry(
                selected[1], key=keys[selected[0]], refresh=True,
            )
            selected_cost = self._request_restore_cost(selected[1], selected_observation)
            head_observation = self._read_request_reentry(head, key=head_key, refresh=True)
            head_cost = self._request_restore_cost(head, head_observation)
            if (
                selected_cost is None or head_cost is None
                or selected_observation["missing_full_tokens"] != 0
                or selected_cost[0] >= head_cost[0]
                or selected_cost[1] > head_cost[1]
            ):
                return None
        return selected, {
            "head_request_id": head.rid,
            "head_restore_bytes": head_cost[0],
            "selected_restore_bytes": selected_cost[0],
            "head_next_input_tokens": head_cost[1],
            "selected_next_input_tokens": selected_cost[1],
            "ordinary_admission_quota": ordinary_quota,
            "native_prefill_slots": budget.request_slots if budget is not None else None,
            "resident_candidates": len(candidates),
            "evidence": (
                "fresh_native_reentry;native_admission_remains"
                if budget is not None
                else "advisory_session_hint;native_input_and_capacity_checks_remain"
            ),
        }

    def _native_prefill_input_budget_exhausted(
        self, native_order: Sequence[object], *, adder: object,
        tagged_count: int | None = None,
    ) -> bool:
        if tagged_count is not None and tagged_count < 2:
            return False
        if not (
            self._tool_timing_only and not self._native_prefill_order
            and type(remaining := getattr(adder, "rem_chunk_tokens", None)) is int
            and remaining == 0
            and getattr(adder, "dllm_config", None) is None
            and getattr(adder, "can_run_list", None)
        ):
            return False
        try:
            if tagged_count is None:
                tagged_count = sum(
                    getattr(req, "beliefkv_metadata", None) is not None
                    for req in native_order
                )
            # Native prefix matching leaves at least one input token.
            return tagged_count >= 2 and all(
                len(getattr(req, "origin_input_ids", ())) > 0
                for req in native_order
            )
        except (AttributeError, TypeError):
            return False

    def skip_exhausted_native_prefill(
        self, native_order: Sequence[object], *, running_batch: object, adder: object,
    ) -> bool:
        if not self._native_prefill_input_budget_exhausted(native_order, adder=adder):
            return False
        self._observe_native_admission_capacity(running_batch=running_batch, adder=adder)
        self.counts["drained_chunk_prefill_skipped"] += 1
        return True

    def _ready_join_parent(self, request: object, key: PrefillCandidateKey | None) -> bool:
        if key is None:
            return False
        join = self.graph.joins.get(self._join_priority_pending.get(key.invocation_id))
        return (
            join is not None and join.satisfied
            and key.invocation_id in join.waiter_invocation_ids
            and self._executable_waiting_key(request, key=key) == key
        )

    def _join_parent_priority_candidate(
        self, ordered: list[tuple[int, object]],
        keys: dict[int, PrefillCandidateKey | None],
    ) -> tuple[int, object] | None:
        for candidate in ordered:
            if self._ready_join_parent(candidate[1], keys[candidate[0]]):
                return candidate
        return None

    def _last_join_child_has_waiting_parent(self, key: PrefillCandidateKey) -> bool:
        for join_id in self._join_by_invocation.get(key.invocation_id, ()):
            join = self.graph.joins.get(join_id)
            if (
                join is None or join.satisfied or join.workflow_id != key.root_workflow_id
                or join.member_invocation_ids - join.completed_member_ids != {key.invocation_id}
            ):
                continue
            for waiter_id in join.waiter_invocation_ids:
                parent = self.graph.invocations.get(waiter_id)
                if (
                    parent is not None and parent.state is InvocationState.WAIT_JOIN
                    and parent.join_id == join_id and not parent.active_tool_calls
                    and parent.workflow_id == key.root_workflow_id
                ):
                    return True
        return False

    @timed_runtime("admission_plan")
    def plan_native_prefill(
        self, native_order: Sequence[object], *, running_batch: object, adder: object,
        _queue_inspection: _NativePrefillQueueInspection | None = None,
    ) -> NativePrefillPlan:
        plan = self._build_native_prefill_plan(
            native_order, running_batch=running_batch, adder=adder,
            _queue_inspection=_queue_inspection,
        )
        if (
            not self.tool_offload_before_admission or not plan.prioritized
            or not self.tool_wait_hints or self.physical_disabled
        ):
            return plan
        if (
            getattr(adder, "rem_chunk_tokens", None) == 0
            or type(remaining := getattr(adder, "rem_input_tokens", None)) is int
            and remaining <= 0
            or type(maximum := getattr(adder, "max_running_requests", None)) is int
            and len(getattr(running_batch, "reqs", ())) + len(
                getattr(adder, "can_run_list", ())
            ) >= maximum
        ):
            return plan
        selected = len(getattr(adder, "can_run_list", ()))
        if any(
            type(limit := getattr(adder, name, None)) is int and limit > 0
            and selected >= limit
            for name in ("max_prefill_bs", "prefill_max_requests")
        ):
            return plan
        from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates

        selection = select_native_prefill_candidates(
            native_order, plan=plan, current_semantic_revision=self.semantic_revision,
        )
        self._predicted_tool_offload.before_admission(
            selection.candidates, running_batch=running_batch, adder=adder,
        )
        if self.semantic_revision != plan.semantic_revision:
            # Tool return/cancellation may arrive while the donor is rechecked.
            self.counts["tool_offload_admission_event_replans"] += 1
            plan = self._build_native_prefill_plan(
                native_order, running_batch=running_batch, adder=adder,
            )
        return plan

    def _build_native_prefill_plan(
        self, native_order: Sequence[object], *, running_batch: object, adder: object,
        _queue_inspection: _NativePrefillQueueInspection | None = None,
    ) -> NativePrefillPlan:
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            return paper.plan(native_order, running_batch=running_batch, adder=adder)
        if self._agent_admission is not None:
            self._agent_admission.tick()
            permitted = [
                request for request in native_order
                if getattr(request, "beliefkv_metadata", None) is None
                or self._agent_admission.allows(self.visible.get(getattr(request, "rid", None)))
            ]
            if len(permitted) != len(native_order):
                self.counts["agent_admission_deferred"] += len(native_order) - len(permitted)
                native_order, _queue_inspection = permitted, None
        self._workflow_work_promoted = None
        self._tool_continuity_promoted = None
        self._observe_native_admission_capacity(running_batch=running_batch, adder=adder)
        if self._tool_residency_grace is not None:
            self._refresh_tool_residency_grace()
            if not getattr(running_batch, "reqs", ()):
                self._release_tool_residency_grace("idle_gpu")
        # Reuse causal classification only; residency, hint expiry and aging
        # remain live because native allocation may change between the two calls.
        tagged = []
        for index, req in enumerate(native_order):
            if getattr(req, "beliefkv_metadata", None) is None:
                continue
            if len(tagged) == 512:
                break
            tagged.append((index, req))
        if len(native_order) > 512:
            self.counts["candidate_bound"] += 1
        inspected = None
        if (
            isinstance(_queue_inspection, _NativePrefillQueueInspection)
            and _queue_inspection.owner is self
            and not _queue_inspection.terminal_request_ids
            and (
                _queue_inspection.semantic_revision, _queue_inspection.graph_version,
                _queue_inspection.sessions_revision,
            ) == (
                self.semantic_revision, self.graph.graph_version,
                self._context_sessions_revision,
            )
            and len(_queue_inspection.entries) == len(native_order)
            and all(
                req is entry[0]
                for req, entry in zip(native_order, _queue_inspection.entries)
            )
        ):
            inspected = _queue_inspection.entries
        # This call-local reuse is advisory. The final terminal check below and
        # the selector's current request/session identity check remain live.
        if inspected is None:
            keys = {
                index: _request_key(req, known_keys=self.visible)
                for index, req in tagged
            }
        else:
            keys = {
                index: (
                    inspected[index][1]
                    if inspected[index][1] is not None
                    else _request_key(req, known_keys=self.visible)
                )
                for index, req in tagged
            }
        self._final_priority_promoted = None
        self._final_priority_native_rank = None
        self._join_last_child_priority_promoted = None
        self._join_priority_promoted = None
        self._prefetch_priority_promoted = None
        self._parent_handoff_priority_promoted = None
        self._prefetch_priority_native_rank = None
        self._prefetch_priority_aged_head_bypassed = False
        self._prefetch_priority_complete_ready_quota = None
        self._prefetch_priority_partial_restore_details = None
        self._resident_priority_promoted = None
        self._resident_priority_details = None
        if self.prefill_scheduling_policy in ("join_priority", "workflow_completion") or (
            self.enable_join_parent_priority and self._join_priority_pending
            and self._join_priority_normal_admissions >= 4
        ):
            ordered = [
                (index, request) for index, request in tagged
                if (key := keys[index]) is not None
                and self.visible.get(key.request_id) == key and not self._terminal(key)
            ]
            now = time.monotonic()
            visible_since = self._visible_since.get
            ordered.sort(key=lambda pair: (
                (0, since, pair[0])
                if now - (since := visible_since(pair[1].rid, now)) >= 10.
                else (1, 0., pair[0])
            ))
            candidate = None
            critical_head = False
            if (
                self.enable_join_parent_priority and self._join_priority_pending
                and ordered and self._join_priority_normal_admissions >= 4
            ):
                candidate = self._join_parent_priority_candidate(ordered, keys)
                critical_head = candidate is not None
                if candidate is not None and candidate != ordered[0]:
                    ordered.remove(candidate)
                    ordered.insert(0, candidate)
                    self._join_priority_promoted = candidate[1].rid
                    prefix = (
                        "join_only" if self.internal_comparison_mode == "join_priority_only"
                        else "join_parent"
                    )
                    self.counts[f"{prefix}_priority_ordered"] += 1
                    if now - self._visible_since.get(ordered[1][1].rid, now) >= 10.:
                        self.counts[f"{prefix}_priority_aged_head_bypass"] += 1
            if (
                self.enable_join_last_child_priority and candidate is None
                and len(ordered) > 1 and self._join_priority_normal_admissions >= 4
            ):
                for pair in ordered:
                    key = keys[pair[0]]
                    if (
                        self._last_join_child_has_waiting_parent(key)
                        and self._executable_waiting_key(pair[1], key=key) == key
                    ):
                        critical_head = True
                        # Parent and sole-child boosts spend the same bounded turn.
                        if pair != ordered[0]:
                            if now - self._visible_since.get(ordered[0][1].rid, now) >= 10.:
                                self.counts["join_last_child_priority_aged_head_bypass"] += 1
                            ordered.remove(pair)
                            ordered.insert(0, pair)
                            self._join_last_child_priority_promoted = pair[1].rid
                            self.counts["join_last_child_priority_ordered"] += 1
                        break
            if self._completion_order is not None and candidate is None and all(
                keys[index].invocation_id in self.graph.invocations for index, _ in ordered
            ):
                ordered = self._completion_order.order(
                    ordered, keys, attained=self._workflow_attained_output_tokens,
                    critical=lambda key: self.frontier.admission_rank(key.invocation_id)[0] == 0,
                )
            if self.prefill_scheduling_policy in ("join_priority", "workflow_completion") or candidate is not None:
                if self.prefill_scheduling_policy != "workflow":
                    self._order_tool_continuation(
                        ordered, keys, now=now, protected_head=critical_head,
                    )
                if self._agent_admission is not None:
                    self._agent_admission.order_ready(
                        ordered, keys, now=now, protected_head=critical_head,
                    )
                return compile_native_prefill_plan(
                    [keys[index] for index, _ in ordered],
                    semantic_revision=self.semantic_revision,
                )
        drained_chunk = self._native_prefill_input_budget_exhausted(
            native_order, adder=adder, tagged_count=len(tagged),
        )
        if (
            self._native_prefill_order
            or self._tool_timing_only and len(tagged) < 2
            or drained_chunk
        ):
            # Tool-only inference has no local output-demand submission. Ranking
            # cannot help a single candidate or an input-budget-exhausted pass.
            self.counts[
                "native_prefill_order_plans" if self._native_prefill_order
                else "drained_chunk_prefill_plans" if drained_chunk
                else "noncompeting_prefill_plans"
            ] += 1
            return compile_native_prefill_plan(
                [
                    key for index, _ in tagged
                    if (key := keys[index]) is not None
                    and self.visible.get(key.request_id) == key
                    and not self._terminal(key)
                ],
                semantic_revision=self.semantic_revision,
            )
        # Reuse only versioned causal ranks across unchanged scheduler iterations.
        ranks = self._prefill_causal_ranks(tagged, keys)
        now_ms = time.monotonic() * 1000
        length_ranks: dict[int, int] = {}
        if self.demand_hints:
            valid_hints: dict[int, NativeDemandHint] = {}
            for index, req in tagged:
                key = keys[index]
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
            if valid_hints:
                members = Counter(rank[:2] for rank in ranks.values())
                hinted = Counter(ranks[index][:2] for index in valid_hints)
                length_ranks = {
                    index: hint.next_output_tokens
                    for index, hint in valid_hints.items()
                    if ranks[index][0] < 5
                    and hinted[ranks[index][:2]] == members[ranks[index][:2]]
                }

        work_baseline_ranks: dict[int, tuple] | None = (
            {} if self._workflow_attained_work_enabled else None
        )
        last_join_children = []
        def waiting_rank(pair: tuple[int, object]) -> tuple:
            index, request = pair
            if ranks[index][0] == 0:
                last_join_children.append(pair)
            since = self._visible_since.get(getattr(request, "rid", ""), now_ms / 1000.)
            if now_ms / 1000. - since >= 10.:
                return (-1, since, index, index)
            rank = (
                *ranks[index][:2],
                length_ranks.get(index, index),
                index,
            )
            if self._workflow_attained_work_enabled and not self.enable_resident_first:
                work_baseline_ranks[index] = rank
                key = keys[index]
                work = self._workflow_attained_output_tokens.get(
                    key.root_workflow_id, 0,
                ) if key is not None else 0
                return (
                    *rank[:2], work, *rank[2:],
                )
            return rank

        waiting_ranks = {pair[0]: waiting_rank(pair) for pair in tagged}
        ordered = sorted(tagged, key=lambda pair: waiting_ranks[pair[0]])
        residency_observations = {}
        if (
            self.enable_resident_first and ordered
            and waiting_ranks[ordered[-1][0]][0] != -1
        ):
            same_group = (
                waiting_ranks[ordered[0][0]][:2]
                == waiting_ranks[ordered[-1][0]][:2]
            )
            residency_groups = None
            if not same_group:
                # Cache causal group sizes; exclude aged members only for this plan.
                cached = self._prefill_causal_cache if self._prefill_cycle_active else None
                residency_groups = (
                    cached[3] if cached is not None and cached[2] is ranks else None
                )
                if residency_groups is None:
                    residency_groups = Counter(rank[:2] for rank in ranks.values())
                    if cached is not None and cached[2] is ranks:
                        self._prefill_causal_cache = (*cached[:3], residency_groups)
                if waiting_ranks[ordered[0][0]][0] == -1:
                    residency_groups = residency_groups.copy()
                    for index, _ in ordered:
                        if waiting_ranks[index][0] != -1:
                            break
                        residency_groups[ranks[index][:2]] -= 1
            competing_group = len(ordered) > 1
            # Handoff and admission share inspection targets, not live decisions.
            for index, req in self._resident_prefill_scan(ordered, keys):
                # Aging uses arrival order; a selected handoff reads its own
                # checkpoint when restoration is actually needed.
                rank = waiting_ranks[index]
                if rank[0] != -1 and (
                    competing_group if residency_groups is None
                    else residency_groups[rank[:2]] > 1
                ):
                    key = keys[index]
                    if key is not None:
                        residency_observations[index] = self._read_request_residency_hint(
                            req, key=key,
                        )

            def residency_rank(pair: tuple[int, object]) -> tuple:
                rank = waiting_ranks[pair[0]]
                if rank[0] == -1:
                    return rank[:2] + (0, 0) + rank[2:]
                key = keys[pair[0]]
                observation = residency_observations.get(pair[0])
                if observation is None:
                    cached = self._residency_ordering_hints.get(key)
                    if cached is None or cached[0] <= now_ms:
                        cached = self._reentry_observations.get(key)
                    observation = cached[1] if cached is not None and cached[0] > now_ms else None
                if observation is not None:
                    residency_observations[pair[0]] = observation
                resident = (
                    0 if observation is not None
                    and not observation["missing_full_tokens"]
                    and not observation["missing_mamba_slots"]
                    else 1 if observation is not None
                    and observation["device_checkpoint_tokens"] > 0
                    else 2
                )
                restore_bytes = 0
                if observation is not None and self._residency_pool_bytes is not None:
                    full_unit, mamba_unit = self._residency_pool_bytes
                    restore_bytes = (
                        observation["missing_full_tokens"] * full_unit
                        + observation["missing_mamba_slots"] * mamba_unit
                    )
                    # A host-only state can leave a large FULL prefix resident.
                    # Compare missing bytes instead of requiring an older
                    # complete device checkpoint for all partial hits.
                    resident = int(restore_bytes > 0)
                tail = rank[2:]
                if self._workflow_attained_work_enabled:
                    work_baseline_ranks[pair[0]] = (
                        rank[:2] + (resident, restore_bytes) + tail
                    )
                    work = self._workflow_attained_output_tokens.get(
                        key.root_workflow_id, 0,
                    ) if key is not None else 0
                    tail = (work, *tail)
                return rank[:2] + (resident, restore_bytes) + tail

            resident_ordered = sorted(ordered, key=residency_rank)
            if resident_ordered != ordered:
                self.counts["resident_first_ordered"] += 1
            ordered = resident_ordered
        work_selected = None
        if (
            work_baseline_ranks and ordered
            and waiting_ranks[ordered[0][0]][0] != -1
            and min(work_baseline_ranks, key=work_baseline_ranks.__getitem__) != ordered[0][0]
        ):
            work_selected = ordered[0][1].rid
        priority_candidates = []
        same_class = (
            ranks[ordered[0][0]][:2]
            if ordered and waiting_ranks[ordered[0][0]][0] != -1 else None
        )
        selected_frontier = self._live_frontier_restore_consumer()
        if selected_frontier is not None and same_class is not None and not any(
            self._ready_join_parent(request, keys[index]) for index, request in ordered
        ):
            candidate = next((
                pair for pair in ordered
                if keys[pair[0]] == selected_frontier.key
                and ranks[pair[0]][:2] == same_class
            ), None)
            if candidate is not None:
                priority_candidates.append((
                    time.monotonic(), 0, candidate, "frontier",
                ))
        if (
            self._prefetch_service_leases and same_class is not None
            and self._prefetch_priority_normal_admissions >= 4
        ):
            priority_now = time.monotonic()
            restores_by_context: dict[str, list[_PrefetchServiceLease]] = {}
            for lease in self._prefetch_service_leases.values():
                if lease.demand_ready and priority_now < lease.expires_at:
                    restores_by_context.setdefault(lease.key.context_id, []).append(lease)
            for candidate in ordered:
                if ranks[candidate[0]][:2] != same_class:
                    continue
                key = keys[candidate[0]]
                if key is None:
                    continue
                matches = [
                    lease for lease in restores_by_context.get(key.context_id, ())
                    if (
                        key.request_id != lease.key.request_id
                        or lease.source == "execution_handoff"
                        or lease.source == "join_ticket" and key == lease.key
                    )
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
                        candidate, "prefetch",
                    ))
        if (
            self.enable_resident_first and same_class is not None
            and self._parent_capacity_handoffs
            and self._prefetch_priority_normal_admissions >= 4
        ):
            inspected = 0
            for candidate in ordered:
                if ranks[candidate[0]][:2] != same_class:
                    continue
                key = keys[candidate[0]]
                handoff = self._live_parent_capacity_handoff(key) if key is not None else None
                if handoff is None:
                    continue
                observation = self._read_request_reentry(candidate[1], key=key)
                inspected += 1
                if observation is not None and not (
                    observation["missing_full_tokens"] or observation["missing_mamba_slots"]
                ):
                    priority_candidates.append((
                        handoff.created_at + 10., 2, candidate, "parent_handoff",
                    ))
                if inspected == 8:
                    break
        if (
            self._prefetch_admission_hints and same_class is not None
            and self._prefetch_priority_normal_admissions >= 4
            and not priority_candidates
        ):
            inspected = 0
            for candidate in ordered:
                if ranks[candidate[0]][:2] != same_class:
                    continue
                rid = candidate[1].rid
                hint = self._prefetch_admission_hints.get(rid)
                if hint is None:
                    continue
                key = keys[candidate[0]]
                if key != hint or self._executable_waiting_key(candidate[1], key=key) is None:
                    self._prefetch_admission_hints.pop(rid, None)
                    continue
                observation = self._read_request_reentry(candidate[1], key=key, refresh=True)
                inspected += 1
                if (
                    observation is not None
                    and type(observation.get("device_checkpoint_tokens")) is int
                    and observation["device_checkpoint_tokens"] > 0
                    and type(observation.get("missing_full_tokens")) is int
                    and observation["missing_full_tokens"] == 0
                    and type(observation.get("missing_mamba_slots")) is int
                    and observation["missing_mamba_slots"] == 0
                ):
                    # Within this causal class, a live restore precedes a hint.
                    # The hint adds neither protection nor another restoration.
                    priority_candidates.append((math.inf, 4, candidate, "prefetch_advisory"))
                    break
                else:
                    self._prefetch_admission_hints.pop(rid, None)
                    self.counts["prefetch_admission_hint_not_ready"] += 1
                if inspected == 8:
                    break
        if priority_candidates:
            chosen = min(priority_candidates, key=lambda item: item[:2])
            source = chosen[3]
            candidate = chosen[2]
            native_rank = ordered.index(candidate)
            if native_rank > 0:
                ordered.remove(candidate)
                ordered.insert(0, candidate)
                if source == "parent_handoff":
                    self._parent_handoff_priority_promoted = candidate[1].rid
                    self.counts["parent_handoff_priority_ordered"] += 1
                else:
                    self._prefetch_priority_promoted = candidate[1].rid
                    self.counts["prefetch_priority_ordered"] += 1
                self._prefetch_priority_native_rank = native_rank
        if (
            (
                self.enable_final_stage_priority
                if self.enable_final_stage_priority is not None
                else self.enable_admission_prefetch or self.enable_final_stage_prefetch
            )
            and self._final_stages
            and self._final_priority_normal_admissions >= 4
            and self._prefetch_priority_normal_admissions >= 4
            and self._prefetch_priority_promoted is None
            and self._parent_handoff_priority_promoted is None and ordered
            and time.monotonic() - self._visible_since.get(
                getattr(ordered[0][1], "rid", ""), time.monotonic(),
            ) < 10.
        ):
            final_candidates = []
            queued = {
                getattr(pair[1], "rid", None): (position, pair)
                for position, pair in enumerate(ordered)
            }
            for stage in self._final_stages.values():
                if (
                    stage.semantic_only or not self._live_final_stage(stage)
                    or stage.request_id is None
                ):
                    continue
                queued_candidate = queued.get(stage.request_id)
                if queued_candidate is None:
                    continue
                native_rank, candidate = queued_candidate
                key = keys[candidate[0]]
                if (
                    key is None or self.visible.get(stage.request_id) != key
                    or key.invocation_id != stage.child_id
                    or key.context_epoch != stage.child_epoch
                ):
                    continue
                remaining, source = self._final_admission_work(
                    stage, key, candidate[1], now_ms=now_ms,
                )
                final_candidates.append((
                    remaining is None, remaining or 0.,
                    native_rank, candidate, source,
                ))
            if final_candidates:
                self.counts["final_priority_candidates_inspected"] += len(final_candidates)
                chosen = min(final_candidates, key=lambda item: item[:3])
                candidate = chosen[3]
                self.counts[f"final_priority_selected_{chosen[4]}"] += 1
                native_rank = chosen[2]
                if native_rank == 0:
                    self.counts["final_priority_best_already_first"] += 1
                else:
                    ordered.remove(candidate)
                    ordered.insert(0, candidate)
                    self._final_priority_promoted = candidate[1].rid
                    self._final_priority_native_rank = native_rank
                    self.counts["final_priority_ordered"] += 1
        if (
            (
                self.enable_final_stage_priority
                if self.enable_final_stage_priority is not None
                else self.enable_admission_prefetch or self.enable_final_stage_prefetch
            )
            and last_join_children and len(ordered) > 1
            and self._final_priority_normal_admissions >= 4
            and self._prefetch_priority_normal_admissions >= 4
            and self._prefetch_priority_promoted is None
            and self._parent_handoff_priority_promoted is None
            and self._final_priority_promoted is None
            and ranks[ordered[0][0]][0] != 0
        ):
            # Reuse the cached causal classification: class zero means this
            # child alone still blocks an observed JOIN with actual waiters.
            for candidate in ordered[1:]:
                index, request = candidate
                if (
                    ranks[index][0] != 0 or keys[index] is None
                    or self._executable_waiting_key(request, key=keys[index]) is None
                    or not self._last_join_child_has_waiting_parent(keys[index])
                ):
                    continue
                self._final_priority_native_rank = ordered.index(candidate)
                if waiting_ranks[ordered[0][0]][0] == -1:
                    self.counts["join_last_child_priority_aged_head_bypass"] += 1
                ordered.remove(candidate)
                ordered.insert(0, candidate)
                self._final_priority_promoted = request.rid
                self._join_last_child_priority_promoted = request.rid
                self.counts["join_last_child_priority_ordered"] += 1
                break
        if (
            self.enable_resident_first and len(ordered) > 1
            and min(
                self._prefetch_priority_normal_admissions,
                self._resident_priority_normal_admissions,
            ) >= 1
            and self._prefetch_priority_promoted is None
            and self._parent_handoff_priority_promoted is None
            and self._join_priority_promoted is None
            and self._final_priority_promoted is None
            and waiting_ranks[ordered[0][0]][0] == -1
        ):
            selected = self._resident_aged_bypass(ordered, keys, ranks)
            if selected is not None:
                candidate, details = selected
                details["tagged_displaced"] = ordered.index(candidate)
                ordered.remove(candidate)
                ordered.insert(0, candidate)
                self._resident_priority_promoted = candidate[1].rid
                self._resident_priority_details = details
                self.counts["resident_aged_priority_ordered"] += 1
                if details["ordinary_admission_quota"] < 4:
                    self.counts["resident_aged_native_quota_ordered"] += 1
        if self._tool_continuity is not None:
            self._order_tool_continuation(
                ordered, keys, now=now_ms / 1000.,
                protected_head=any((
                    self._prefetch_priority_promoted, self._parent_handoff_priority_promoted,
                    self._join_priority_promoted, self._final_priority_promoted,
                    self._resident_priority_promoted,
                )),
                causal_ranks=ranks, residency_observations=residency_observations,
            )
        plan = compile_native_prefill_plan(
            [
                key for index, _ in ordered
                if (key := keys[index]) is not None
                and self.visible.get(key.request_id) == key
                and not self._terminal(key)
            ],
            semantic_revision=self.semantic_revision,
        )
        if plan.prioritized and plan.prioritized[0].request_id == work_selected:
            self._workflow_work_promoted = work_selected
            self.counts["workflow_work_head_ordered"] += 1
        return plan

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

    def _order_tool_continuation(
        self, ordered: list[tuple[int, object]],
        keys: dict[int, PrefillCandidateKey | None], *,
        now: float, protected_head: bool,
        causal_ranks: dict[int, tuple[int, int, int]] | None = None,
        residency_observations: dict[int, dict | None] | None = None,
    ) -> None:
        policy = self._tool_continuity
        if policy is None or not policy.ready or len(ordered) < 2:
            return
        if causal_ranks is not None and (protected_head or not self.enable_resident_first):
            return
        floor = int(protected_head)
        selected = None
        for position, (index, req) in enumerate(ordered):
            if position < floor:
                continue
            key = keys[index]
            # Keep locality boosts below every ready JOIN, even during cooldown.
            if self._join_priority_pending and self._ready_join_parent(req, key):
                floor = position + 1
                selected = None
                continue
            if (
                position <= floor or key is None
                or (key.root_workflow_id, key.invocation_id) not in policy.ready
            ):
                continue
            arrival = self._visible_since.get(req.rid, now)
            if now - arrival >= 10.:
                continue
            inherited = policy.inherited_arrival(key, arrival=arrival)
            if inherited is not None and self._executable_waiting_key(req, key=key) == key:
                if causal_ranks is not None:
                    if causal_ranks[index][:2] != causal_ranks[ordered[0][0]][:2]:
                        continue
                    # Reuse resident-first observations; never plan another restore.
                    observation = (residency_observations or {}).get(index)
                    if (
                        observation is None
                        or not observation.get("device_checkpoint_tokens", 0)
                        or observation.get("missing_full_tokens") != 0
                        or observation.get("missing_mamba_slots") != 0
                    ):
                        self.counts["tool_continuity_not_resident"] += 1
                        continue
                choice = inherited, position
                if selected is None or choice < selected:
                    selected = choice
        if selected is None:
            return
        inherited, position = selected
        target = floor
        allows_aged = self._tool_continuity_normal_admissions >= 4
        for preceding in range(floor, position):
            arrival = self._visible_since.get(ordered[preceding][1].rid, now)
            if arrival <= inherited or now - arrival >= 10. and not allows_aged:
                target = preceding + 1
        if target < position:
            aged_bypassed = any(
                now - self._visible_since.get(ordered[index][1].rid, now) >= 10.
                for index in range(target, position)
            )
            pair = ordered.pop(position)
            ordered.insert(target, pair)
            self._tool_continuity_promoted = pair[1].rid
            self.counts["tool_continuity_priority_ordered"] += 1
            self.counts["tool_continuity_priority_aged_bypass_ordered"] += int(aged_bypassed)

    def on_prefill_selection(self, rejected: tuple[tuple[str, str], ...]) -> None:
        self.counts.update(reason for _, reason in rejected)

    def allow_prefill_capacity_bypass(
        self, req: object, *, native_budget_available: bool, bypassed: int,
    ) -> bool:
        if (
            self._native_prefill_order
            or not self.enable_resident_first or not native_budget_available
            or bypassed >= 8 or self._executable_waiting_key(req) is None
            or time.monotonic() - self._visible_since.get(req.rid, time.monotonic()) >= 10.
        ):
            return False
        self.counts["prefill_capacity_bypassed"] += 1
        return True

    def allow_native_prefill_candidate(
        self, req: object, *, running_batch: object, adder: object
    ) -> bool:
        paper = getattr(self, "_paper_baseline", None)
        callback = getattr(paper, "allow_candidate", None)
        return callback is None or callback(
            req, running_batch=running_batch, adder=adder,
        ) is True

    def on_prefill_candidate_result(
        self, req: object, *, admitted: bool, result: str
    ) -> bool:
        """Report whether rejected admission released optional device locks."""
        if admitted and self._agent_admission is not None:
            self._agent_admission.admitted(req)
        if admitted and self._full_prefix_migration is not None:
            self._full_prefix_migration.admitted(req)
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            callback = getattr(paper, "candidate_result", None)
            if callback is not None:
                return callback(req, admitted=admitted, result=result)
        grace = self._tool_residency_grace
        if grace is not None and grace.key is not None:
            key = self.visible.get(getattr(req, "rid", None))
            if admitted and key is not None and (
                key.root_workflow_id, key.invocation_id, key.context_id,
                key.session_id, key.session_generation,
            ) == (
                grace.key.root_workflow_id, grace.key.invocation_id, grace.key.context_id,
                grace.key.session_id, grace.key.session_generation,
            ):
                self._release_tool_residency_grace("native_admitted")
            elif not admitted and result == "NO_TOKEN":
                released = self._release_tool_residency_grace("allocation_pressure") is not None
                if getattr(req, "beliefkv_metadata", None) is not None:
                    self.counts["native_NO_TOKEN"] += 1
                return released
        if getattr(req, "beliefkv_metadata", None) is not None:
            self.counts["native_admitted" if admitted else f"native_{result}"] += 1
            if admitted and self._tool_continuity is not None:
                key = self.visible.get(req.rid)
                if key is not None:
                    promoted = req.rid == self._tool_continuity_promoted
                    new_admission = self._tool_continuity.admitted(
                        key, arrival=self._visible_since.get(req.rid, time.monotonic()),
                        promoted=promoted,
                    )
                    if new_admission:
                        self._tool_continuity_normal_admissions = (
                            0 if promoted else min(4, self._tool_continuity_normal_admissions + 1)
                        )
                    if promoted:
                        self._tool_continuity_promoted = None
            if admitted and (
                self.enable_join_parent_priority or self.enable_join_last_child_priority
            ) and (
                self._join_priority_pending or self._join_priority_normal_admissions < 4
                or self._join_priority_promoted is not None
                or self._join_last_child_priority_promoted is not None
            ):
                parent_promoted = req.rid == self._join_priority_promoted
                child_promoted = (
                    self.enable_join_last_child_priority
                    and req.rid == self._join_last_child_priority_promoted
                )
                promoted = parent_promoted or child_promoted
                self._join_priority_normal_admissions = (
                    0 if promoted else min(4, self._join_priority_normal_admissions + 1)
                )
                key = self.visible.get(req.rid)
                if key is not None:
                    self._join_priority_pending.pop(key.invocation_id, None)
                if parent_promoted:
                    self.counts[
                        "join_only_priority_admitted"
                        if self.internal_comparison_mode == "join_priority_only"
                        else "join_parent_priority_admitted"
                    ] += 1
                if child_promoted:
                    self.counts["join_last_child_priority_admitted"] += 1
                    self._join_last_child_priority_promoted = None
            if self._prefetch_admission_hints.pop(req.rid, None) is not None:
                self.counts[
                    "prefetch_admission_hint_admitted" if admitted
                    else "prefetch_admission_hint_declined"
                ] += 1
            if admitted:
                if self._completion_order is not None and (key := self.visible.get(req.rid)) is not None:
                    self._completion_order.admitted(
                        key.root_workflow_id, req.rid,
                        priority_override=req.rid == self._join_priority_promoted,
                    )
                if req.rid == self._workflow_work_promoted:
                    self.counts["workflow_work_head_admitted"] += 1
                    self._workflow_work_promoted = None
                key = self.visible.get(req.rid)
                hinted = (
                    None if self._native_prefill_order
                    else self._residency_ordering_hints.get(key)
                )
                if hinted is not None and hinted[0] > time.monotonic() * 1000.:
                    self.counts["residency_ordering_hint_admitted"] += 1
                    # Native fills the tier breakdown in prepare_for_extend,
                    # after this admission callback.
                    if (
                        getattr(req, "_cache_breakdown_computed", None) is False
                        and not getattr(req, "retracted_stain", False)
                        and key is not None
                    ):
                        self._residency_hint_admissions.setdefault(
                            req.rid, (id(req), key, hinted[1]["device_checkpoint_tokens"]),
                        )
                        self.counts["residency_ordering_hint_feedback_deferred"] += 1
                    else:
                        self.counts["residency_ordering_hint_native_unknown"] += 1
                self.demand_hints.pop(req.rid, None)
                resident_priority = req.rid == self._resident_priority_promoted
                native_quota_resident = bool(
                    resident_priority and self._resident_priority_details is not None
                    and self._resident_priority_details["ordinary_admission_quota"] < 4
                )
                self._resident_priority_normal_admissions = (
                    0 if resident_priority else min(
                        4, self._resident_priority_normal_admissions + 1,
                    )
                )
                if req.rid in (
                    self._prefetch_priority_promoted, self._final_priority_promoted,
                    self._parent_handoff_priority_promoted, self._resident_priority_promoted,
                    self._join_priority_promoted,
                ):
                    # Locality turns keep accruing the ordinary quota for
                    # restore/final-stage priorities, so those can still run.
                    self._prefetch_priority_normal_admissions = (
                        min(4, self._prefetch_priority_normal_admissions + 1)
                        if native_quota_resident else 0
                    )
                    if req.rid == self._resident_priority_promoted:
                        self.counts["resident_aged_priority_admitted"] += 1
                        if native_quota_resident:
                            self.counts["resident_aged_native_quota_admitted"] += 1
                        if self._opportunity_writer is not None:
                            self._opportunity_writer.record({
                                "event": "resident_aged_priority_admitted",
                                "ts_ms": time.time() * 1000.,
                                "request_id": req.rid,
                                **(self._resident_priority_details or {}),
                            })
                    if req.rid == self._parent_handoff_priority_promoted:
                        self.counts["parent_handoff_priority_admitted"] += 1
                    if req.rid == self._join_priority_promoted:
                        self._join_priority_promoted = None
                    if req.rid == self._prefetch_priority_promoted:
                        self.counts["prefetch_priority_admitted"] += 1
                        if self._prefetch_priority_complete_ready_quota is not None:
                            self.counts["prefetch_priority_complete_ready_admitted"] += 1
                        if self._prefetch_priority_partial_restore_details is not None:
                            self.counts["prefetch_priority_partial_restore_admitted"] += 1
                        if self._prefetch_priority_aged_head_bypassed:
                            self.counts["prefetch_priority_aged_head_bypass_admitted"] += 1
                        if self._opportunity_writer is not None:
                            self._opportunity_writer.record({
                                "event": "restore_ready_priority_admitted",
                                "ts_ms": time.time() * 1000,
                                "request_id": req.rid,
                                "tagged_displaced": self._prefetch_priority_native_rank,
                                "aged_head_bounded_bypass": self._prefetch_priority_aged_head_bypassed,
                                "complete_checkpoint_ordinary_quota":
                                    self._prefetch_priority_complete_ready_quota,
                                "partial_checkpoint_cost_comparison":
                                    self._prefetch_priority_partial_restore_details,
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
                    if req.rid == self._join_last_child_priority_promoted:
                        self.counts["join_last_child_priority_admitted"] += 1
                        self._join_last_child_priority_promoted = None
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
                locked = [
                    lease for lease in self._prefetch_service_leases.values()
                    if lease.lock_params is not None
                ]
                if locked:
                    key = _request_key(req)
                    victim = min(locked, key=lambda lease: (
                        bool(key is not None and lease.key.context_id == key.context_id),
                        2 if lease.demand_ready else 1 if lease.reentry_ready_at is not None else 0,
                        lease.acknowledged_at,
                    ))
                    # Drop the target's entire protection group, including
                    # receipts covered by its deepest lock.
                    victims = [
                        lease for lease in self._prefetch_service_leases.values()
                        if lease.key == victim.key
                    ]
                    for lease in victims:
                        self._release_prefetch_service_lease(
                            lease.command_id, "allocation_pressure", keep_tracking=True,
                        )
                    remaining = self._prefetch_service_leases.get(victim.command_id)
                    return bool(
                        not self.physical_disabled and remaining is not None
                        and remaining.lock_params is None
                    )
        return False

    def on_batch_selected(self, batch: object) -> None:
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            selected = getattr(paper, "selected", None)
            if selected is not None:
                selected(batch)
        if self._agent_admission is not None:
            self._agent_admission.selected(batch)
        ticket = self._frontier_restore_consumer
        if ticket is None or batch is None:
            return
        if any(
            request is ticket.request and self.visible.get(request.rid) == ticket.key
            for request in getattr(batch, "reqs", ())
        ):
            self._frontier_restore_consumer = None
            self._prefetch_priority_normal_admissions = 0
            self.counts["frontier_restore_batch_selected"] += 1

    @timed_runtime("batch_completed")
    def on_batch_completed(self, batch: object) -> tuple[str, ...]:
        """Observe the original batch and share its completed request IDs."""
        if (
            self._report_prediction_enabled
            and self._final_request_stages
        ):
            self._observe_report_service(batch)
        completed_ids: list[str] | None = None
        pending_by_context: dict[str, list[tuple[str, PrefetchLoadStep]]] | None = None
        if self._prefetch_steps:
            for command, (step, _, _) in self._prefetch_steps.items():
                if command not in self._prefetch_first_services:
                    if pending_by_context is None:
                        pending_by_context = {}
                    pending_by_context.setdefault(step.key.context_id, []).append((command, step))
        lease_contexts = (
            {lease.key.context_id for lease in self._prefetch_service_leases.values()}
            if self._prefetch_service_leases else ()
        )
        observe_service = bool(
            pending_by_context or lease_contexts or self._parent_capacity_handoffs
            or self._residency_hint_admissions
        )
        only_completed = self._tool_timing_only or not self._custom_kv_management or (
            not self._report_prediction_enabled
            and self._model_worker is None and self._semantic_worker is None
        )
        grace_has_active_peer = False
        if self._tool_residency_grace is not None:
            self._refresh_tool_residency_grace()
            grace_has_active_peer = any(not req.finished() for req in batch.reqs)
            if not grace_has_active_peer:
                self._release_tool_residency_grace("idle_gpu")
        for req in batch.reqs:
            finished = req.finished()
            # EOS and tool-wait policies consume final counts, not decode progress.
            if only_completed and not finished and not observe_service:
                continue
            rid = req.rid
            if finished:
                if self._completion_order is not None:
                    self._completion_order.completed(req.rid)
                if completed_ids is None:
                    completed_ids = []
                completed_ids.append(rid)
            visible_key = self.visible.get(rid)
            if self._residency_hint_admissions and (
                feedback := self._residency_hint_admissions.pop(rid, None)
            ) is not None:
                instance, key, expected = feedback
                actual = getattr(req, "cached_tokens_device", None)
                if (
                    id(req) == instance and visible_key == key
                    and getattr(req, "_cache_breakdown_computed", None) is True
                    and not getattr(req, "retracted_stain", False)
                    and type(actual) is int
                ):
                    self.counts["residency_ordering_hint_native_observed"] += 1
                    self.counts["residency_ordering_hint_shorter_native_prefix"] += int(
                        actual < expected,
                    )
                else:
                    self.counts["residency_ordering_hint_native_unknown"] += 1
            if self._parent_capacity_handoffs:
                if visible_key is not None and visible_key.context_id in self._parent_capacity_handoffs:
                    self._observe_parent_handoff_service(req)
            if pending_by_context:
                key = visible_key
                for command, step in pending_by_context.get(
                    key.context_id if key is not None else "", (),
                ):
                    if (
                        key is not None and key.invocation_id == step.key.invocation_id
                        and key.root_workflow_id == step.key.root_workflow_id
                        and key.context_epoch in (step.key.context_epoch, step.key.context_epoch + 1)
                        and (key.session_id, key.session_generation)
                        == (step.key.session_id, step.key.session_generation)
                    ):
                        self._prefetch_first_services.setdefault(
                            command, (time.time() * 1000., req.rid),
                        )
            if lease_contexts and self._prefetch_service_leases:
                metadata = getattr(req, "beliefkv_metadata", None)
                context_id = metadata.get("context_id") if isinstance(metadata, dict) else None
                if (
                    isinstance(context_id, str) and context_id in lease_contexts
                    and (key := _request_key(req, known_keys=self.visible)) is not None
                ):
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
            if (
                (self.enable_final_stage_prefetch or self.enable_admission_prefetch)
                and visible_key is not None and finished
            ):
                key = visible_key
                metadata = getattr(req, "beliefkv_metadata", None)
                if (
                    key is not None and isinstance(metadata, dict)
                    and metadata.get("parent_invocation_id", False) is None
                ):
                    invocation = self.graph.invocations.get(key.invocation_id)
                    if (
                        invocation is not None
                        and invocation.relation_type is RelationType.ROOT
                        and invocation.parent_invocation_id is None
                        and invocation.workflow_id == key.root_workflow_id
                        and invocation.context_id == key.context_id
                    ):
                        key = None
                if key is not None:
                    self._semantic_keys[key.request_id] = key
                    outputs = getattr(req, "output_ids", ()) or ()
                    if any(token in self._tool_open_token_ids for token in outputs):
                        self._child_report_notices.pop(key.invocation_id, None)
                        self._decoded_tool_requests.add(key.request_id)
                        self._semantic_forecasts.pop(key.request_id, None)
                        self._semantic_frames.pop(key.request_id, None)
                        for join_id, active in tuple(self._final_stages.items()):
                            if active.child_id == key.invocation_id:
                                self._clear_final_stage(join_id)
                        self.counts["semantic_native_tool_marker_invalidated"] += 1
                    self._semantic_finished[key.request_id] = (
                        time.monotonic() * 1000, len(outputs),
                    )
                    report_stage = self._final_request_stages.get(key.request_id)
                    self._record_native_final_body(req, key)
                    if (
                        self._report_prediction_enabled
                        and report_stage is not None
                        and self._semantic_eos_proofs.get(key.request_id) is True
                    ):
                        self._report_work.complete(
                            expected=report_stage.expected_tokens, generated=len(outputs),
                        )
                        self.counts["report_length_online_calibrated"] += 1
            if (
                visible_key is not None
                and (key := visible_key).session_id is not None
                and key.session_generation is not None
                and (finished or not only_completed)
            ):
                self._context_tokens[key.context_id] = (
                    key.context_epoch,
                    len(getattr(req, "origin_input_ids", ()) or ()),
                    len(getattr(req, "output_ids", ()) or ()),
                    req.beliefkv_metadata.get("parent_invocation_id") is not None,
                )
            if visible_key is not None and finished:
                context_id = visible_key.context_id
                key = visible_key
                if self._tool_continuity is not None:
                    self._tool_continuity.completed(
                        key, now=time.monotonic(),
                        output_tokens=len(getattr(req, "output_ids", ()) or ()),
                    )
                    if (
                        grace_has_active_peer and self._tool_residency_grace.key is None
                        and self._tool_continuity.pending_reentry(key)
                        and not self.physical_disabled and not self._terminal(key)
                        and not self._tool_grace_yields_to_join()
                        and any(
                            token in self._tool_open_token_ids
                            for token in (getattr(req, "output_ids", ()) or ())
                        )
                    ):
                        self._tool_residency_grace.retain(
                            self._native_cache, key, now=time.monotonic(),
                        )
                if self._workflow_attained_work_enabled:
                    workflow = self.graph.workflows.get(key.root_workflow_id)
                    if workflow is not None and workflow.end_ts_ms is None:
                        tokens = len(getattr(req, "output_ids", ()) or ())
                        self._workflow_attained_output_tokens[key.root_workflow_id] = (
                            self._workflow_attained_output_tokens.get(key.root_workflow_id, 0)
                            + tokens
                        )
                        self.counts["workflow_completed_output_tokens_charged"] += tokens
                del self.visible[rid]
                self._prefetch_admission_hints.pop(rid, None)
                self._visible_since.pop(rid, None)
                self.demand_hints.pop(rid, None)
                session_key = self.context_sessions.get(context_id)
                if (
                    session_key is not None
                    and session_key.request_id == rid
                    and self._terminal(session_key)
                ):
                    self._forget_session(rid)
                    self._context_tokens.pop(context_id, None)
                self.semantic_revision += 1
        if self._prefetch_service_leases:
            self._refresh_prefetch_service_leases()
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            paper.completed(batch, completed_ids or ())
        if self._agent_admission is not None:
            self._agent_admission.completed(batch, completed_ids or ())
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.completed(batch, completed_ids or ())
        return tuple(completed_ids) if completed_ids is not None else ()

    def on_abort_request(self, abort: object) -> None:
        if self._full_prefix_migration is not None:
            self._full_prefix_migration.aborted(
                abort.rid, all_requests=getattr(abort, "abort_all", False),
            )
        if self._agent_admission is not None:
            self._agent_admission.aborted(abort.rid, all_requests=getattr(abort, "abort_all", False))
        if (paper := getattr(self, "_paper_baseline", None)) is not None:
            paper.aborted(abort.rid, all_requests=getattr(abort, "abort_all", False))
        grace = self._tool_residency_grace
        if grace is not None and grace.key is not None and (
            getattr(abort, "abort_all", False) or grace.key.request_id.startswith(abort.rid)
        ):
            self._release_tool_residency_grace("abort")
        removed = [
            rid
            for rid in self.visible
            if getattr(abort, "abort_all", False) or rid.startswith(abort.rid)
        ]
        for rid in removed:
            if self._tool_continuity is not None:
                key = self.visible[rid]
                self._tool_continuity.discard(key.root_workflow_id, key.invocation_id)
            if self._residency_hint_admissions.pop(rid, None) is not None:
                self.counts["residency_ordering_hint_feedback_unserved"] += 1
            del self.visible[rid]
            self._visible_since.pop(rid, None)
            self.demand_hints.pop(rid, None)
            self._forget_session(rid)
            self.semantic_revision += 1
        for context_id, key in tuple(self.context_sessions.items()):
            if getattr(abort, "abort_all", False) or key.request_id.startswith(abort.rid):
                if self._tool_continuity is not None:
                    self._tool_continuity.discard(key.root_workflow_id, key.invocation_id)
                del self.context_sessions[context_id]
                self._context_session_changed(context_id)
                self._context_tokens.pop(context_id, None)
                self.tool_wait_hints.pop(context_id, None)
                self.join_wait_hints = {
                    join_id: hint for join_id, hint in self.join_wait_hints.items()
                    if hint.key.context_id != context_id
                }
                self.shadow_candidate = None
        self._refresh_prefetch_service_leases()

    def running_batch_retraction_barrier_required(self, batch: object) -> bool:
        if self.can_prefetch_during_overlap():
            # The native adapter allocates free pages, preserves its load fence,
            # and carries the H2D consumer event into later prefill batches.
            return False
        self._roll_tool_prefetch()
        tool = self._tool_ticket
        if (
            tool is not None and not tool.drained
            and not self.physical_ledger.pending_action_count("PREFETCH_GPU")
            and self._live_tool_hint(tool.key) is not None
        ):
            tool.drained = True
            self.counts["tool_overlap_drain_requested"] += 1
            return True
        # SGLang uses this drain to reach the same safe point needed for a
        # native JOIN H2D. Request it once per burst, never per decode tick.
        if self._eos_prefetch_keys or (
            self._report_prediction_enabled
            and self._final_request_stages
        ):
            # Build the read-only intent before deciding whether overlap must drain.
            # Issuance still waits for SGLang's drained safe point and revalidation.
            self._roll_final_stage()
        ticket = self._join_ticket
        if (
            ticket is None
            or not (
                ticket.phase == "confirmed"
                or ticket.stage_bound and ticket.phase == "provisional"
            )
            or not self._live_join_ticket()
            or ticket.command_id is not None
            or self.physical_ledger.pending_action_count("PREFETCH_GPU")
            or self.enable_confirmed_join_canary and ticket.issued_nodes >= 1
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

    def select_native_decode_victim(self, batch: object, indices: tuple[int, ...]) -> int | None:
        paper = getattr(self, "_paper_baseline", None)
        callback = getattr(paper, "decode_victim", None)
        return callback(batch, indices) if callback is not None else None

    def before_native_request_release(self, req: object, *, reason: str) -> None:
        paper = getattr(self, "_paper_baseline", None)
        callback = getattr(paper, "before_release", None)
        if callback is not None:
            callback(req, reason=reason)

    def on_native_decode_capacity_pressure(self, batch: object) -> bool:
        paper = getattr(self, "_paper_baseline", None)
        callback = getattr(paper, "decode_pressure", None)
        return callback is not None and callback(batch) is True
