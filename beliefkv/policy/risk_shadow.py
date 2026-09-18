from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, replace
import hashlib
import math
import time
from typing import Any, Callable, Mapping

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.policy.joint_scheduler import JointPlan
from beliefkv.policy.predictive_joint import (
    PackageScenarioEvaluation,
    PrepareRecourseDiagnostic,
    PredictiveActionKind,
    PredictiveActionPackage,
    ProjectedReclaimRequirement,
    ScenarioCost,
    PackageRiskSummary,
    ScenarioRiskDecision,
    ScenarioRiskPlanner,
    ScenarioRiskPlannerConfig,
)
from beliefkv.policy.predictive_timeline import (
    CandidatePhysicalPlan,
    CandidateTimelineEvaluator,
    PhysicalizedInvocationDemand,
    ScheduledBatchQuantum,
    ScheduledRequestQuantum,
    ScheduledTransfer,
    TimedScenario,
    evaluate_belief_timelines,
)
from beliefkv.policy.reference import PolicyInput
from beliefkv.policy.service_curve import TransferServiceCurve
from beliefkv.runtime.protocol import TransferDirection
from beliefkv.predictor.frontier_belief import (
    BeliefScopeBuilder,
    BeliefScopeConfig,
    DemandPhase,
    DependencyMode,
    FrontierBeliefSnapshot,
    PredictiveEvidenceReadSet,
    ScenarioProjection,
)
from beliefkv.predictor.hardware_service import GPUServiceCurveModel
from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    FrontierScenarioComposer,
    LocalFrontierFeatures,
    LocalFrontierPrediction,
)


@dataclass(frozen=True)
class PredictiveRiskShadowConfig:
    particle_count: int = 128
    top_k: int = 4
    max_candidates: int = 2
    minimum_calibration_coverage: float = 0.9
    service_quantile: float = 0.9
    kv_bytes_per_token: int = 57_344
    transfer_p95_safety_factor: float = 1.25
    online_overlay_enabled: bool = False
    transfer_commit_guard_ms: float = 25.0
    scheduled_service_lead_ms: float = 100.0
    minimum_causal_slack_probability: float = 0.9
    minimum_prefetch_slack_probability: float = 0.5

    def __post_init__(self) -> None:
        if min(self.particle_count, self.top_k, self.max_candidates) <= 0:
            raise ValueError("predictive risk shadow limits must be positive")
        if self.top_k > self.particle_count:
            raise ValueError("predictive risk top_k cannot exceed particle count")
        if not 0 <= self.minimum_calibration_coverage <= 1:
            raise ValueError("minimum calibration coverage must be in [0, 1]")
        if self.kv_bytes_per_token <= 0:
            raise ValueError("KV bytes per token must be positive")
        if (
            not math.isfinite(self.transfer_p95_safety_factor)
            or self.transfer_p95_safety_factor < 1.0
        ):
            raise ValueError("transfer p95 safety factor must be at least one")
        if (
            not math.isfinite(self.transfer_commit_guard_ms)
            or self.transfer_commit_guard_ms < 0
        ):
            raise ValueError("transfer commit guard must be finite and non-negative")
        if (
            not math.isfinite(self.scheduled_service_lead_ms)
            or self.scheduled_service_lead_ms < 0
        ):
            raise ValueError("scheduled service lead must be finite and non-negative")
        if not 0 <= self.minimum_causal_slack_probability <= 1:
            raise ValueError(
                "minimum causal slack probability must be in [0, 1]"
            )
        if not 0 <= self.minimum_prefetch_slack_probability <= 1:
            raise ValueError(
                "minimum prefetch slack probability must be in [0, 1]"
            )


@dataclass(frozen=True)
class _ActionTimingEvidence:
    semantics: str
    required_wait_ms: float
    causal_slack_probability: float
    conservative_remaining_window_ms: float
    calibration_brier_skill: float | None = None
    calibration_balanced_accuracy: float | None = None
    decision_threshold: float | None = None
    raw_decision_threshold: float | None = None
    precision_at_decision_threshold: float | None = None
    recall_at_decision_threshold: float | None = None
    informative: bool = True


def _minimum_action_timing_probability(
    action: PredictiveActionKind,
    timing: _ActionTimingEvidence,
    config: PredictiveRiskShadowConfig,
) -> float:
    """Use the calibrated operating point whenever the action head has one."""

    if timing.decision_threshold is not None:
        return timing.decision_threshold
    if action in {
        PredictiveActionKind.PREFETCH_GPU,
        PredictiveActionKind.PARTIAL_PREFETCH_GPU,
        PredictiveActionKind.RECLAIM_AND_PREFETCH,
    }:
        # RCCG-composed JOIN/child timing has no independently calibrated
        # classifier threshold. Its release probability already weights the
        # same scenarios used for expected benefit, CVaR, HBM chance, and
        # deterministic feasibility. Applying the legacy 0.5 threshold again
        # rejects low-probability/high-value prefetches after they pass the
        # complete risk objective. Calibrated WAIT_TOOL heads still return
        # above and retain their artifact-defined operating point.
        if timing.semantics == "release_within_transfer":
            return 0.0
        return config.minimum_prefetch_slack_probability
    return config.minimum_causal_slack_probability


def _transfer_deadline_and_slack(
    pressure_ms: float | None,
    reentry_ms: float | None,
    *,
    transfer_ms: float,
    guard_ms: float,
) -> tuple[float | None, float | None]:
    if pressure_ms is None or reentry_ms is None:
        return None, None
    deadline = min(pressure_ms, reentry_ms)
    return deadline, deadline - transfer_ms - guard_ms


def _prepare_beneficiary_feasibility_reasons(
    package: PredictiveActionPackage,
    *,
    transfer_ready_ms: float,
) -> tuple[str, ...]:
    if package.action != PredictiveActionKind.PREPARE_HOST:
        return ()
    reasons: list[str] = []
    if (
        package.predicted_block_time_ms is None
        or package.predicted_block_time_ms <= transfer_ready_ms
    ):
        reasons.append("beneficiary_blocks_before_prepare_complete")
    if package.predicted_deficit_bytes > package.victim_reclaim_bytes:
        reasons.append("victim_reclaim_below_beneficiary_deficit")
    return tuple(reasons)


@dataclass(frozen=True)
class PredictiveIntent:
    """Semantic prediction output; Radix extents are resolved at a safe point."""

    intent_id: str
    source_joint_plan_id: str
    source_snapshot_id: str
    package_id: str
    model_version: str
    action: PredictiveActionKind
    invocation_id: str
    expected_invocation_state: str
    context_id: str
    context_epoch: int
    generated_ts_ms: float
    remaining_window_low_ms: float
    transfer_p95_ms: float
    target_bytes_hint: int
    min_reclaimable_bytes: int
    max_cross_context_bytes: int
    max_copy_bytes: int
    causal_certificate: Mapping[str, object]
    required_prediction_heads: tuple[str, ...]
    prediction_head_support: tuple[tuple[str, str], ...]
    calibration_coverage: float
    future_hbm_feasibility_probability: float
    expected_benefit_ms: float
    shape_fingerprint: str
    predicted_extent_count: int
    maximum_transfer_ms: float
    maximum_stall_ms: float
    morphology_slack_ms: float
    causal_slack_probability: float = 0.0
    timing_semantics: str = "action_default"
    beneficiary_request_id: str | None = None
    beneficiary_invocation_id: str | None = None
    beneficiary_context_id: str | None = None
    beneficiary_context_epoch: int | None = None
    beneficiary_startup_bytes: int = 0
    beneficiary_growth_bytes: int = 0
    predicted_block_time_ms: float | None = None
    predicted_deficit_bytes: int = 0
    causal_package_generation: str | None = None
    target_reentry_context_epoch: int | None = None
    victim_invocation_id: str | None = None
    expected_victim_invocation_state: str | None = None
    victim_context_id: str | None = None
    victim_context_epoch: int | None = None
    victim_generation_fingerprint: str | None = None
    victim_reclaim_bytes: int = 0
    evidence_kind: str = "model_prediction"
    execution_order_request_ids: tuple[str, ...] = ()
    admit_request_ids: tuple[str, ...] = ()
    execution_request_evidence: tuple[tuple[str, str, str, int], ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.intent_id,
            self.source_joint_plan_id,
            self.source_snapshot_id,
            self.package_id,
            self.model_version,
            self.invocation_id,
            self.expected_invocation_state,
            self.context_id,
            self.shape_fingerprint,
        )
        if any(not item for item in required):
            raise ValueError("predictive intent identity is required")
        if not self.evidence_kind:
            raise ValueError("predictive intent evidence kind is required")
        object.__setattr__(self, "action", PredictiveActionKind(self.action))
        if self.action not in {
            PredictiveActionKind.SCHEDULE,
            PredictiveActionKind.PREPARE_HOST,
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            raise ValueError("unsupported online predictive intent")
        expected_timing = {
            PredictiveActionKind.SCHEDULE: "execution_window",
            PredictiveActionKind.PREPARE_HOST: "release_after_transfer",
            PredictiveActionKind.PREFETCH_GPU: "release_within_transfer",
            PredictiveActionKind.PARTIAL_PREFETCH_GPU: "release_within_transfer",
            PredictiveActionKind.RECLAIM_AND_PREFETCH: "release_within_transfer",
        }[self.action]
        if self.timing_semantics == "action_default":
            object.__setattr__(self, "timing_semantics", expected_timing)
        elif self.timing_semantics != expected_timing:
            raise ValueError("predictive intent timing semantics do not match action")
        if self.context_epoch < 0 or self.target_bytes_hint < 0:
            raise ValueError("predictive intent context/byte values are invalid")
        if min(
            self.min_reclaimable_bytes,
            self.max_cross_context_bytes,
            self.max_copy_bytes,
        ) < 0:
            raise ValueError("predictive intent resource envelope is invalid")
        if self.action == PredictiveActionKind.SCHEDULE:
            if any(
                value != 0
                for value in (
                    self.target_bytes_hint,
                    self.min_reclaimable_bytes,
                    self.max_cross_context_bytes,
                    self.max_copy_bytes,
                    self.predicted_extent_count,
                    self.maximum_transfer_ms,
                    self.maximum_stall_ms,
                    self.morphology_slack_ms,
                )
            ):
                raise ValueError("schedule intent cannot carry physical resources")
        else:
            if self.target_bytes_hint <= 0 or self.max_copy_bytes <= 0:
                raise ValueError("transfer intent requires a positive copy envelope")
            if self.target_bytes_hint > self.max_copy_bytes:
                raise ValueError(
                    "predictive byte hint exceeds the certified copy bound"
                )
            if self.max_cross_context_bytes > self.max_copy_bytes:
                raise ValueError(
                    "predictive cross-context bound exceeds copy bound"
                )
        if not self.causal_certificate:
            raise ValueError("predictive intent requires causal evidence")
        finite = (
            self.generated_ts_ms,
            self.remaining_window_low_ms,
            self.transfer_p95_ms,
            self.calibration_coverage,
            self.future_hbm_feasibility_probability,
            self.expected_benefit_ms,
            self.maximum_transfer_ms,
            self.maximum_stall_ms,
            self.morphology_slack_ms,
            self.causal_slack_probability,
        )
        if any(not math.isfinite(item) for item in finite):
            raise ValueError("predictive intent values must be finite")
        if min(self.generated_ts_ms, self.remaining_window_low_ms, self.transfer_p95_ms) < 0:
            raise ValueError("predictive intent timing must be non-negative")
        if min(
            self.predicted_extent_count,
            self.maximum_transfer_ms,
            self.maximum_stall_ms,
            self.morphology_slack_ms,
        ) < 0:
            raise ValueError("predictive morphology envelope must be non-negative")
        if self.action == PredictiveActionKind.PREPARE_HOST:
            if self.predicted_extent_count <= 0:
                raise ValueError("prepare intent requires a physical extent shape")
            if self.maximum_transfer_ms < self.transfer_p95_ms:
                raise ValueError("prepare transfer envelope is below its estimate")
            beneficiary_ids = (
                self.beneficiary_request_id,
                self.beneficiary_invocation_id,
                self.beneficiary_context_id,
                self.causal_package_generation,
            )
            if any(not value for value in beneficiary_ids):
                raise ValueError("prepare intent requires beneficiary evidence")
            if self.beneficiary_context_epoch is None:
                raise ValueError("prepare intent requires beneficiary context epoch")
            if (
                self.beneficiary_context_epoch < 0
                or self.beneficiary_startup_bytes
                + self.beneficiary_growth_bytes
                <= 0
                or self.predicted_deficit_bytes <= 0
                or self.predicted_block_time_ms is None
                or not math.isfinite(self.predicted_block_time_ms)
                or self.predicted_block_time_ms < 0
            ):
                raise ValueError("prepare intent beneficiary evidence is invalid")
        if self.action in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            if (
                self.target_reentry_context_epoch is None
                or self.target_reentry_context_epoch < self.context_epoch
            ):
                raise ValueError("prefetch intent requires a target reentry epoch")
        if self.action == PredictiveActionKind.RECLAIM_AND_PREFETCH:
            victim_identity = (
                self.victim_invocation_id,
                self.expected_victim_invocation_state,
                self.victim_context_id,
                self.victim_context_epoch,
                self.victim_generation_fingerprint,
            )
            if any(item is None for item in victim_identity):
                raise ValueError("funded prefetch requires victim evidence")
            if (
                self.victim_context_id == self.context_id
                or self.victim_context_epoch is None
                or self.victim_context_epoch < 0
                or self.victim_reclaim_bytes <= 0
            ):
                raise ValueError("funded prefetch victim evidence is invalid")
        elif any(
            item is not None
            for item in (
                self.victim_invocation_id,
                self.expected_victim_invocation_state,
                self.victim_context_id,
                self.victim_context_epoch,
                self.victim_generation_fingerprint,
            )
        ) or self.victim_reclaim_bytes:
            raise ValueError("non-swap intent cannot carry victim evidence")
        execution_order = tuple(self.execution_order_request_ids)
        admit_requests = tuple(self.admit_request_ids)
        request_evidence = tuple(self.execution_request_evidence)
        if (
            any(not request_id for request_id in execution_order)
            or len(execution_order) != len(set(execution_order))
        ):
            raise ValueError("predictive execution order must contain unique IDs")
        if (
            any(not request_id for request_id in admit_requests)
            or len(admit_requests) != len(set(admit_requests))
            or not set(admit_requests).issubset(execution_order)
        ):
            raise ValueError("predictive admission set is invalid")
        evidence_ids = tuple(item[0] for item in request_evidence)
        if (
            len(evidence_ids) != len(set(evidence_ids))
            or set(evidence_ids) != set(execution_order)
            or any(
                len(item) != 4
                or not item[0]
                or not item[1]
                or not item[2]
                or not isinstance(item[3], int)
                or isinstance(item[3], bool)
                or item[3] < 0
                for item in request_evidence
            )
        ):
            raise ValueError("predictive execution evidence is invalid")
        if self.action == PredictiveActionKind.SCHEDULE:
            beneficiary = (
                self.beneficiary_request_id,
                self.beneficiary_invocation_id,
                self.beneficiary_context_id,
                self.beneficiary_context_epoch,
            )
            if any(item is None for item in beneficiary) or not execution_order:
                raise ValueError("schedule intent requires beneficiary evidence")
            if execution_order[0] != self.beneficiary_request_id:
                raise ValueError("schedule beneficiary must lead execution order")
        object.__setattr__(self, "execution_order_request_ids", execution_order)
        object.__setattr__(self, "admit_request_ids", admit_requests)
        object.__setattr__(self, "execution_request_evidence", request_evidence)
        if not 0 <= self.calibration_coverage <= 1:
            raise ValueError("predictive intent calibration must be in [0, 1]")
        if not 0 <= self.future_hbm_feasibility_probability <= 1:
            raise ValueError("predictive HBM feasibility must be in [0, 1]")
        if not 0 <= self.causal_slack_probability <= 1:
            raise ValueError("predictive causal slack probability must be in [0, 1]")
        heads = tuple(sorted(set(self.required_prediction_heads)))
        support = tuple(sorted(set(self.prediction_head_support)))
        if not heads or any(not name for name in heads):
            raise ValueError("predictive intent requires action-specific heads")
        if any(not name or not level for name, level in support):
            raise ValueError("predictive intent head support is invalid")
        if not set(heads).issubset({name for name, _level in support}):
            raise ValueError("predictive intent is missing required head support")
        object.__setattr__(self, "required_prediction_heads", heads)
        object.__setattr__(self, "prediction_head_support", support)

    def to_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "source_joint_plan_id": self.source_joint_plan_id,
            "source_snapshot_id": self.source_snapshot_id,
            "package_id": self.package_id,
            "model_version": self.model_version,
            "action": self.action.value,
            "invocation_id": self.invocation_id,
            "expected_invocation_state": self.expected_invocation_state,
            "context_id": self.context_id,
            "context_epoch": self.context_epoch,
            "generated_ts_ms": self.generated_ts_ms,
            "remaining_window_low_ms": self.remaining_window_low_ms,
            "transfer_p95_ms": self.transfer_p95_ms,
            "target_bytes_hint": self.target_bytes_hint,
            "min_reclaimable_bytes": self.min_reclaimable_bytes,
            "max_cross_context_bytes": self.max_cross_context_bytes,
            "max_copy_bytes": self.max_copy_bytes,
            "causal_certificate": dict(self.causal_certificate),
            "required_prediction_heads": list(self.required_prediction_heads),
            "prediction_head_support": [list(item) for item in self.prediction_head_support],
            "calibration_coverage": self.calibration_coverage,
            "future_hbm_feasibility_probability": self.future_hbm_feasibility_probability,
            "expected_benefit_ms": self.expected_benefit_ms,
            "shape_fingerprint": self.shape_fingerprint,
            "predicted_extent_count": self.predicted_extent_count,
            "maximum_transfer_ms": self.maximum_transfer_ms,
            "maximum_stall_ms": self.maximum_stall_ms,
            "morphology_slack_ms": self.morphology_slack_ms,
            "causal_slack_probability": self.causal_slack_probability,
            "timing_semantics": self.timing_semantics,
            "beneficiary_request_id": self.beneficiary_request_id,
            "beneficiary_invocation_id": self.beneficiary_invocation_id,
            "beneficiary_context_id": self.beneficiary_context_id,
            "beneficiary_context_epoch": self.beneficiary_context_epoch,
            "beneficiary_startup_bytes": self.beneficiary_startup_bytes,
            "beneficiary_growth_bytes": self.beneficiary_growth_bytes,
            "predicted_block_time_ms": self.predicted_block_time_ms,
            "predicted_deficit_bytes": self.predicted_deficit_bytes,
            "causal_package_generation": self.causal_package_generation,
            "target_reentry_context_epoch": self.target_reentry_context_epoch,
            "victim_invocation_id": self.victim_invocation_id,
            "expected_victim_invocation_state": (
                self.expected_victim_invocation_state
            ),
            "victim_context_id": self.victim_context_id,
            "victim_context_epoch": self.victim_context_epoch,
            "victim_generation_fingerprint": self.victim_generation_fingerprint,
            "victim_reclaim_bytes": self.victim_reclaim_bytes,
            "evidence_kind": self.evidence_kind,
            "execution_order_request_ids": list(
                self.execution_order_request_ids
            ),
            "admit_request_ids": list(self.admit_request_ids),
            "execution_request_evidence": [
                list(item) for item in self.execution_request_evidence
            ],
        }


@dataclass(frozen=True)
class PredictiveRiskShadowResult:
    status: str
    source_joint_plan_id: str
    source_snapshot_id: str
    belief_id: str | None
    model_version: str | None
    support_level: str
    calibration_coverage: float
    other_probability_mass: float
    target_invocation_id: str | None
    target_context_id: str | None
    selected_package_id: str
    selected_action: str
    candidate_count: int
    candidate_summaries: tuple[Mapping[str, object], ...]
    ood_reasons: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    planning_ms: float
    scenario_count: int
    belief_compose_ms: float = 0.0
    candidate_generation_ms: float = 0.0
    deterministic_preflight_ms: float = 0.0
    scenario_risk_ms: float = 0.0
    service_cache_hits: int = 0
    service_cache_misses: int = 0
    belief_cache_hit: bool = False
    local_particle_cache_hits: int = 0
    local_particle_cache_misses: int = 0
    predictive_intent: PredictiveIntent | None = None
    decision_authority: str = "read_only_shadow"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "source_joint_plan_id": self.source_joint_plan_id,
            "source_snapshot_id": self.source_snapshot_id,
            "belief_id": self.belief_id,
            "model_version": self.model_version,
            "support_level": self.support_level,
            "calibration_coverage": self.calibration_coverage,
            "other_probability_mass": self.other_probability_mass,
            "target_invocation_id": self.target_invocation_id,
            "target_context_id": self.target_context_id,
            "selected_package_id": self.selected_package_id,
            "selected_action": self.selected_action,
            "candidate_count": self.candidate_count,
            "candidate_summaries": [dict(item) for item in self.candidate_summaries],
            "ood_reasons": list(self.ood_reasons),
            "blocked_reasons": list(self.blocked_reasons),
            "planning_ms": self.planning_ms,
            "scenario_count": self.scenario_count,
            "belief_compose_ms": self.belief_compose_ms,
            "candidate_generation_ms": self.candidate_generation_ms,
            "deterministic_preflight_ms": self.deterministic_preflight_ms,
            "scenario_risk_ms": self.scenario_risk_ms,
            "service_cache_hits": self.service_cache_hits,
            "service_cache_misses": self.service_cache_misses,
            "belief_cache_hit": self.belief_cache_hit,
            "local_particle_cache_hits": self.local_particle_cache_hits,
            "local_particle_cache_misses": self.local_particle_cache_misses,
            "predictive_intent": (
                self.predictive_intent.to_dict()
                if self.predictive_intent is not None
                else None
            ),
            "prediction_used": False,
            "predictive_intent_available": self.predictive_intent is not None,
            "decision_authority": self.decision_authority,
        }


@dataclass(frozen=True)
class PrefetchTarget:
    invocation_id: str
    context_id: str
    state: str
    missing_gpu_bytes: int


@dataclass(frozen=True)
class PrepareHostVictim:
    invocation_id: str
    context_id: str
    state: str
    shadow_bytes: int
    reclaimable_bytes: int


@dataclass(frozen=True)
class ReclaimReadyVictim:
    invocation_id: str
    context_id: str
    context_epoch: int
    state: str
    reclaimable_bytes: int
    generation_fingerprint: str


@dataclass(frozen=True)
class _PrepareShadowProjection:
    """Snapshot-side approximation of one live SHADOW_CONTEXT closure."""

    root_extent_id: str
    closure_extent_ids: tuple[str, ...]
    copy_bytes: int
    extent_count: int
    shape_fingerprint: str
    exclusive_copy_bytes: int
    cross_context_copy_bytes: int


@dataclass(frozen=True)
class _PrefetchPrefixProjection:
    """Largest ancestor-closed H2D prefix within the current byte budget."""

    target_extent_id: str
    closure_extent_ids: tuple[str, ...]
    copy_bytes: int
    extent_count: int
    shape_fingerprint: str
    cross_context_copy_bytes: int


@dataclass(frozen=True)
class _TransferDurationEvidence:
    duration_ms: float
    source: str
    service_epoch: str
    nearest_bucket_distance: int | None = None
    sample_count: int = 0
    size_coverage_bytes: tuple[int, int] | None = None
    extent_count_coverage: tuple[int, int] | None = None
    shape_bucket_distance: int | None = None
    shape_supported: bool = False
    estimated_unhidden_stall_p90_ms: float | None = None


@dataclass(frozen=True)
class _TransferInterferenceEvidence:
    interference_ms: float
    source: str
    service_epoch: str
    interference_to_transfer_ratio: float


def _prepare_shadow_projection(
    bundles: tuple[Any, ...],
    context_id: str,
    *,
    extent_index: Mapping[str, Any] | None = None,
) -> _PrepareShadowProjection | None:
    """Select a closure-complete D2H shadow rooted in target-private KV.

    Snapshot bundles are single Radix extents. ``descendant_closure`` only says
    that an extent cannot be evicted alone; it does not block a non-destructive
    shadow when all GPU-resident descendants are copied in the same bundle.
    Every other blocker remains a hard constraint and is checked over the
    complete closure, matching the live PhysicalBundleBuilder.
    """

    by_extent = extent_index or {
        bundle.extent_ids[0]: bundle
        for bundle in bundles
        if len(bundle.extent_ids) == 1
    }
    candidates: list[_PrepareShadowProjection] = []
    for root_extent_id, root in by_extent.items():
        if (
            root.owner_context_ids != (context_id,)
            or root.scope != "exclusive_suffix"
            or root.gpu_bytes <= root.cpu_bytes
        ):
            continue
        closure: dict[str, Any] = {}
        stack = [root_extent_id]
        valid = True
        while stack:
            extent_id = stack.pop()
            if extent_id in closure:
                valid = False
                break
            bundle = by_extent.get(extent_id)
            if bundle is None:
                valid = False
                break
            if bundle.gpu_bytes <= 0:
                continue
            hard_blockers = set(bundle.blocker_codes) - {"descendant_closure"}
            if hard_blockers or bundle.locked_bytes:
                valid = False
                break
            closure[extent_id] = bundle
            for child_extent_id in bundle.child_extent_ids:
                if child_extent_id not in by_extent:
                    valid = False
                    break
                stack.append(child_extent_id)
            if not valid:
                break
        if not valid or not closure:
            continue
        copy_bytes = sum(
            max(0, bundle.gpu_bytes - bundle.cpu_bytes)
            for bundle in closure.values()
        )
        exclusive_copy_bytes = sum(
            max(0, bundle.gpu_bytes - bundle.cpu_bytes)
            for bundle in closure.values()
            if bundle.owner_context_ids == (context_id,)
        )
        if copy_bytes <= 0 or exclusive_copy_bytes <= 0:
            continue
        transfer_extents = {
            extent_id: bundle
            for extent_id, bundle in closure.items()
            if bundle.gpu_bytes > bundle.cpu_bytes
        }
        candidates.append(
            _PrepareShadowProjection(
                root_extent_id=root_extent_id,
                closure_extent_ids=tuple(sorted(closure)),
                copy_bytes=copy_bytes,
                extent_count=len(transfer_extents),
                shape_fingerprint=hashlib.blake2b(
                    repr(
                        tuple(
                            sorted(
                                (
                                    extent_id,
                                    bundle.generation_fingerprint,
                                    bundle.gpu_bytes,
                                    bundle.cpu_bytes,
                                )
                                for extent_id, bundle in transfer_extents.items()
                            )
                        )
                    ).encode("utf-8"),
                    digest_size=12,
                    person=b"bkv-shape",
                ).hexdigest(),
                exclusive_copy_bytes=exclusive_copy_bytes,
                cross_context_copy_bytes=copy_bytes - exclusive_copy_bytes,
            )
        )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            -item.exclusive_copy_bytes,
            item.copy_bytes,
            item.cross_context_copy_bytes,
            item.root_extent_id,
        ),
    )


def _prefetch_prefix_projection(
    bundles: tuple[Any, ...],
    context_id: str,
    byte_budget: int,
    *,
    extent_index: Mapping[str, Any] | None = None,
) -> _PrefetchPrefixProjection | None:
    """Select the largest live-equivalent ancestor closure under ``byte_budget``."""

    if byte_budget <= 0:
        return None
    by_extent = extent_index or {
        bundle.extent_ids[0]: bundle
        for bundle in bundles
        if len(bundle.extent_ids) == 1
    }
    candidates: list[_PrefetchPrefixProjection] = []
    for target_extent_id, target in by_extent.items():
        if (
            context_id not in target.owner_context_ids
            or target.cpu_bytes <= target.gpu_bytes
        ):
            continue
        closure: dict[str, Any] = {}
        seen: set[str] = set()
        node = target
        valid = True
        while node.cpu_bytes > node.gpu_bytes:
            extent_id = node.extent_ids[0]
            if extent_id in seen:
                valid = False
                break
            seen.add(extent_id)
            if not node.actionable or node.locked_bytes or node.blocker_codes:
                valid = False
                break
            closure[extent_id] = node
            if node.parent_extent_id is None:
                break
            parent = by_extent.get(node.parent_extent_id)
            if parent is None:
                valid = False
                break
            if parent.cpu_bytes <= parent.gpu_bytes:
                break
            node = parent
        if not valid or not closure:
            continue
        copy_bytes = sum(
            max(0, bundle.cpu_bytes - bundle.gpu_bytes)
            for bundle in closure.values()
        )
        if copy_bytes <= 0 or copy_bytes > byte_budget:
            continue
        cross_context_copy_bytes = sum(
            max(0, bundle.cpu_bytes - bundle.gpu_bytes)
            for bundle in closure.values()
            if any(owner != context_id for owner in bundle.owner_context_ids)
        )
        candidates.append(
            _PrefetchPrefixProjection(
                target_extent_id=target_extent_id,
                closure_extent_ids=tuple(sorted(closure)),
                copy_bytes=copy_bytes,
                extent_count=len(closure),
                shape_fingerprint=hashlib.blake2b(
                    repr(
                        tuple(
                            sorted(
                                (
                                    extent_id,
                                    bundle.generation_fingerprint,
                                    bundle.gpu_bytes,
                                    bundle.cpu_bytes,
                                )
                                for extent_id, bundle in closure.items()
                            )
                        )
                    ).encode("utf-8"),
                    digest_size=12,
                    person=b"bkv-h2d-shape",
                ).hexdigest(),
                cross_context_copy_bytes=cross_context_copy_bytes,
            )
        )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            -item.copy_bytes,
            item.cross_context_copy_bytes,
            item.target_extent_id,
        ),
    )


def _action_local_physical_overlay(
    policy_input: PolicyInput,
) -> dict[str, Mapping[str, object]]:
    metadata = policy_input.optional_metadata.get(
        "beliefkv_action_local_physical_overlay"
    )
    payload = metadata.value if metadata is not None else None
    rows = payload.get("overlays", ()) if isinstance(payload, Mapping) else ()
    return {
        str(row.get("context_id")): row
        for row in rows
        if isinstance(row, Mapping) and row.get("context_id")
    }



@dataclass(frozen=True)
class PredictiveEligibility:
    source_snapshot_id: str
    prefetch_targets: tuple[PrefetchTarget, ...]
    prepare_host_victims: tuple[PrepareHostVictim, ...]
    probe_ms: float
    reclaim_ready_victims: tuple[ReclaimReadyVictim, ...] = ()
    trigger_signature: tuple[object, ...] = ()
    belief_signature: tuple[object, ...] = ()

    @property
    def has_candidate(self) -> bool:
        return bool(
            self.prefetch_targets
            or self.prepare_host_victims
            or self.reclaim_ready_victims
        )


class PredictiveEligibilityIndex:
    """Cheap action-specific gate evaluated before belief composition."""

    _WAIT_STATES = {
        InvocationState.WAIT_TOOL.value,
        InvocationState.WAIT_CHILD.value,
        InvocationState.WAIT_JOIN.value,
        InvocationState.WAIT_MESSAGE.value,
    }
    _SERVICE_STATES = {
        InvocationState.READY.value,
        InvocationState.RUNNING_LLM.value,
    }

    def __init__(self) -> None:
        self._cache_key: tuple[object, ...] | None = None
        self._cached: PredictiveEligibility | None = None
        self._last_hbm_bucket: int | None = None
        self._last_host_bucket: int | None = None

    def probe(
        self,
        policy_input: PolicyInput,
        *,
        graph_state: Mapping[str, object] | None = None,
    ) -> PredictiveEligibility:
        started_ns = time.perf_counter_ns()
        prediction_metadata = policy_input.optional_metadata.get(
            "frontier_predictions"
        )
        model_metadata = policy_input.optional_metadata.get(
            "frontier_prediction_model_version"
        )
        overlay_metadata = policy_input.optional_metadata.get(
            "beliefkv_action_local_physical_overlay"
        )
        cache_key = (
            policy_input.physical_kv.snapshot_id,
            policy_input.runtime_graph.graph_version,
            id(prediction_metadata.value) if prediction_metadata is not None else 0,
            str(model_metadata.value) if model_metadata is not None else "",
            id(overlay_metadata.value) if overlay_metadata is not None else 0,
        )
        if (
            graph_state is None
            and cache_key == self._cache_key
            and self._cached is not None
        ):
            return PredictiveEligibility(
                source_snapshot_id=self._cached.source_snapshot_id,
                prefetch_targets=self._cached.prefetch_targets,
                prepare_host_victims=self._cached.prepare_host_victims,
                probe_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
                reclaim_ready_victims=self._cached.reclaim_ready_victims,
                trigger_signature=self._cached.trigger_signature,
                belief_signature=self._cached.belief_signature,
            )

        graph_state_view = graph_state or policy_input.runtime_graph.state
        nested = graph_state_view.get("rccg")
        if isinstance(nested, Mapping):
            graph_state_view = nested
        invocations = graph_state_view.get("invocations", {})
        if not isinstance(invocations, Mapping):
            invocations = {}
        risk_trigger_metadata = policy_input.optional_metadata.get(
            "beliefkv_predictive_risk_trigger"
        )
        risk_trigger_payload = (
            risk_trigger_metadata.value
            if risk_trigger_metadata is not None
            and isinstance(risk_trigger_metadata.value, Mapping)
            else {}
        )
        explicit_reentry_rank: dict[str, int] = {}
        scheduled_service_rank: dict[str, int] = {}
        for event in risk_trigger_payload.get("events", ()):
            if (
                isinstance(event, (list, tuple))
                and len(event) >= 4
                and str(event[0]) == "reentry"
            ):
                invocation_id = str(event[2])
                if invocation_id and invocation_id not in explicit_reentry_rank:
                    explicit_reentry_rank[invocation_id] = len(
                        explicit_reentry_rank
                    )
                if (
                    str(event[1]) == "scheduled_service"
                    and invocation_id
                    and invocation_id not in scheduled_service_rank
                ):
                    scheduled_service_rank[invocation_id] = len(
                        scheduled_service_rank
                    )
        ranked_reentry_targets = scheduled_service_rank or explicit_reentry_rank
        preferred_reentry_rank = dict(
            tuple(ranked_reentry_targets.items())[:1]
        )
        invocation_by_context: dict[str, list[tuple[str, str, float]]] = {}
        for invocation_id, raw in invocations.items():
            if not isinstance(raw, Mapping):
                continue
            invocation_state = str(raw.get("state") or "")
            if invocation_state in {
                InvocationState.DONE.value,
                InvocationState.CANCELLED.value,
            }:
                continue
            context_id = str(raw.get("context_id") or "")
            if context_id:
                invocation_by_context.setdefault(context_id, []).append(
                    (
                        str(invocation_id),
                        invocation_state,
                        float(raw.get("updated_ts_ms") or 0.0),
                    )
                )

        bundles_by_context: dict[str, list[Any]] = {}
        for bundle in policy_input.physical_kv.bundles:
            for context_id in bundle.owner_context_ids:
                bundles_by_context.setdefault(context_id, []).append(bundle)
        extent_index = {
            bundle.extent_ids[0]: bundle
            for bundle in policy_input.physical_kv.bundles
            if len(bundle.extent_ids) == 1
        }
        overlay_by_context = _action_local_physical_overlay(policy_input)
        overlay_metadata = policy_input.optional_metadata.get(
            "beliefkv_action_local_physical_overlay"
        )
        overlay_payload = (
            overlay_metadata.value
            if overlay_metadata is not None
            and isinstance(overlay_metadata.value, Mapping)
            else {}
        )

        prefetch: list[PrefetchTarget] = []
        victims: list[PrepareHostVictim] = []
        reclaim_ready: list[ReclaimReadyVictim] = []
        prefetch_priority = {
            InvocationState.WAIT_JOIN.value: 0,
            InvocationState.WAIT_CHILD.value: 1,
            InvocationState.WAIT_TOOL.value: 2,
            InvocationState.WAIT_MESSAGE.value: 3,
            InvocationState.READY.value: 4,
            InvocationState.RUNNING_LLM.value: 5,
        }
        for context_id, overlay in overlay_by_context.items():
            owners = invocation_by_context.get(context_id, ())
            if not owners:
                continue
            selected_invocation = min(
                owners,
                key=lambda item: (
                    0 if item[0] in preferred_reentry_rank else 1,
                    preferred_reentry_rank.get(item[0], 1 << 30),
                    prefetch_priority.get(item[1], 10), item[2], item[0]
                ),
            )
            if int(overlay.get("h2d_copy_bytes", 0)) > 0:
                prefetch.append(
                    PrefetchTarget(
                        invocation_id=selected_invocation[0],
                        context_id=context_id,
                        state=selected_invocation[1],
                        missing_gpu_bytes=int(overlay["h2d_copy_bytes"]),
                    )
                )
                continue
            if (
                selected_invocation[1] in self._WAIT_STATES
                and int(overlay.get("d2h_copy_bytes", 0)) > 0
                and int(overlay.get("exclusive_reclaimable_bytes", 0)) > 0
                and int(overlay.get("extent_count", 0)) > 0
                and int(overlay.get("locked_bytes", 0)) == 0
                and not bool(overlay.get("native_loading", False))
                and not tuple(overlay.get("blocker_codes", ()))
            ):
                victims.append(
                    PrepareHostVictim(
                        invocation_id=selected_invocation[0],
                        context_id=context_id,
                        state=selected_invocation[1],
                        shadow_bytes=int(overlay["d2h_copy_bytes"]),
                        reclaimable_bytes=int(
                            overlay["exclusive_reclaimable_bytes"]
                        ),
                    )
                )
            elif (
                selected_invocation[1] in self._WAIT_STATES
                and int(overlay.get("d2h_copy_bytes", 0)) == 0
                and int(overlay.get("exclusive_reclaimable_bytes", 0)) > 0
                and int(overlay.get("locked_bytes", 0)) == 0
                and not bool(overlay.get("native_loading", False))
                and not tuple(overlay.get("blocker_codes", ()))
            ):
                reclaim_ready.append(
                    ReclaimReadyVictim(
                        invocation_id=selected_invocation[0],
                        context_id=context_id,
                        context_epoch=int(overlay.get("context_epoch", 0)),
                        state=selected_invocation[1],
                        reclaimable_bytes=int(
                            overlay["exclusive_reclaimable_bytes"]
                        ),
                        generation_fingerprint=str(
                            overlay.get("generation_fingerprint") or ""
                        ),
                    )
                )

        for context_id, bundles in bundles_by_context.items():
            owners = invocation_by_context.get(context_id, ())
            if not owners:
                continue
            selected_invocation = min(
                owners,
                key=lambda item: (
                    0 if item[0] in preferred_reentry_rank else 1,
                    preferred_reentry_rank.get(item[0], 1 << 30),
                    prefetch_priority.get(item[1], 10),
                    item[2],
                    item[0],
                ),
            )
            missing_gpu = sum(max(0, item.cpu_bytes - item.gpu_bytes) for item in bundles)
            if missing_gpu > 0:
                prefetch.append(
                    PrefetchTarget(
                        invocation_id=selected_invocation[0],
                        context_id=context_id,
                        state=selected_invocation[1],
                        missing_gpu_bytes=missing_gpu,
                    )
                )
            if context_id in overlay_by_context:
                continue
            projection = _prepare_shadow_projection(
                policy_input.physical_kv.bundles,
                context_id,
                extent_index=extent_index,
            )
            if selected_invocation[1] in self._WAIT_STATES and projection is not None:
                victims.append(
                    PrepareHostVictim(
                        invocation_id=selected_invocation[0],
                        context_id=context_id,
                        state=selected_invocation[1],
                        shadow_bytes=projection.copy_bytes,
                        reclaimable_bytes=projection.exclusive_copy_bytes,
                    )
                )

        prefetch.sort(
            key=lambda item: (
                0 if item.invocation_id in preferred_reentry_rank else 1,
                preferred_reentry_rank.get(item.invocation_id, 1 << 30),
                prefetch_priority.get(item.state, 10),
                item.missing_gpu_bytes,
                item.context_id,
            )
        )
        if preferred_reentry_rank:
            prefetch = [
                item
                for item in prefetch
                if item.invocation_id in preferred_reentry_rank
            ]
        victims.sort(key=lambda item: (-item.reclaimable_bytes, item.context_id))
        reclaim_ready.sort(
            key=lambda item: (-item.reclaimable_bytes, item.context_id)
        )
        overlay_device_available = overlay_payload.get("device_available_bytes")
        hbm_free = (
            max(0, int(overlay_device_available))
            if isinstance(overlay_device_available, int)
            and not isinstance(overlay_device_available, bool)
            else max(
                0,
                policy_input.resources.hbm_capacity_bytes
                - policy_input.resources.policy_hbm_used_bytes
                - policy_input.resources.hbm_reserved_bytes,
            )
        )
        host_free = policy_input.resources.host_free_bytes
        hbm_bucket = self._hysteretic_bucket(
            hbm_free,
            previous=self._last_hbm_bucket,
        )
        host_bucket = self._hysteretic_bucket(
            host_free,
            previous=self._last_host_bucket,
        )
        self._last_hbm_bucket = hbm_bucket
        self._last_host_bucket = host_bucket
        candidate_invocation_ids = tuple(
            sorted(
                {
                    item.invocation_id
                    for item in (*prefetch, *victims, *reclaim_ready)
                }
            )
        )
        prediction_signature = self._prediction_signature(
            policy_input,
            candidate_invocation_ids,
        )
        causal_signature = self._causal_signature(
            graph_state_view,
            candidate_invocation_ids,
        )
        candidate_context_ids = {
            item.context_id for item in (*prefetch, *victims, *reclaim_ready)
        }
        contexts = graph_state_view.get("contexts", {})
        if not isinstance(contexts, Mapping):
            contexts = {}
        physical_signature = tuple(
            (
                context_id,
                str(overlay_by_context[context_id].get("generation_fingerprint")),
                str(overlay_by_context[context_id].get("shape_fingerprint")),
                int(overlay_by_context[context_id].get("d2h_copy_bytes", 0))
                // (64 << 20),
                int(
                    overlay_by_context[context_id].get(
                        "exclusive_reclaimable_bytes", 0
                    )
                )
                // (64 << 20),
            )
            if context_id in overlay_by_context
            else self._action_shape_signature(
                context_id, bundles_by_context.get(context_id, ()), contexts
            )
            for context_id in sorted(candidate_context_ids)
        )
        result = PredictiveEligibility(
            source_snapshot_id=policy_input.snapshot_id,
            prefetch_targets=tuple(prefetch),
            prepare_host_victims=tuple(victims),
            probe_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
            reclaim_ready_victims=tuple(reclaim_ready),
            trigger_signature=(
                tuple(
                    (
                        item.context_id,
                        item.state,
                        item.missing_gpu_bytes // (64 << 20),
                    )
                    for item in prefetch
                ),
                tuple(
                    (
                        item.context_id,
                        item.state,
                        item.reclaimable_bytes // (64 << 20),
                    )
                    for item in victims
                ),
                tuple(
                    (
                        item.context_id,
                        item.state,
                        item.reclaimable_bytes // (64 << 20),
                        item.generation_fingerprint,
                    )
                    for item in reclaim_ready
                ),
                hbm_bucket,
                host_bucket,
                prediction_signature,
                causal_signature,
                physical_signature,
            ),
            belief_signature=(prediction_signature, causal_signature),
        )
        if graph_state is None:
            self._cache_key = cache_key
            self._cached = result
        return result

    @staticmethod
    def _action_shape_signature(
        context_id: str,
        bundles: Iterable[Any],
        contexts: Mapping[str, object],
    ) -> tuple[object, ...]:
        """Stable package shape; exact generations remain commit-time evidence."""

        items = tuple(bundles)
        raw_context = contexts.get(context_id)
        context_epoch = (
            int(raw_context.get("epoch") or 0)
            if isinstance(raw_context, Mapping)
            else 0
        )
        blocker_codes = tuple(
            sorted(
                {
                    str(code)
                    for bundle in items
                    for code in bundle.blocker_codes
                }
            )
        )
        return (
            context_id,
            context_epoch,
            max(0, len(items).bit_length() - 1),
            sum(max(0, bundle.gpu_bytes) for bundle in items) // (64 << 20),
            sum(max(0, bundle.cpu_bytes) for bundle in items) // (64 << 20),
            sum(max(0, bundle.locked_bytes) for bundle in items) // (64 << 20),
            bool(items) and all(bundle.actionable for bundle in items),
            blocker_codes,
        )

    @staticmethod
    def _hysteretic_bucket(
        value: int,
        *,
        previous: int | None,
        width: int = 64 << 20,
    ) -> int:
        current = max(0, value) // width
        if previous is None or current == previous:
            return current
        hysteresis = width // 4
        if current > previous and value < (previous + 1) * width + hysteresis:
            return previous
        if current < previous and value >= previous * width - hysteresis:
            return previous
        return current

    @classmethod
    def _prediction_signature(
        cls,
        policy_input: PolicyInput,
        invocation_ids: tuple[str, ...],
    ) -> tuple[object, ...]:
        metadata = policy_input.optional_metadata.get("frontier_predictions")
        payload = metadata.value if metadata is not None else {}
        if not isinstance(payload, Mapping):
            payload = {}
        model = policy_input.optional_metadata.get(
            "frontier_prediction_model_version"
        )
        return (
            str(model.value) if model is not None else "unavailable",
            tuple(
                cls._local_prediction_bucket(invocation_id, payload.get(invocation_id))
                for invocation_id in invocation_ids
            ),
        )

    @staticmethod
    def _causal_signature(
        graph_state: Mapping[str, object],
        invocation_ids: tuple[str, ...],
    ) -> tuple[object, ...]:
        invocations = graph_state.get("invocations", {})
        joins = graph_state.get("joins", {})
        if not isinstance(invocations, Mapping):
            invocations = {}
        if not isinstance(joins, Mapping):
            joins = {}
        invocation_values = []
        join_ids: set[str] = set()
        for invocation_id in invocation_ids:
            raw = invocations.get(invocation_id)
            if not isinstance(raw, Mapping):
                invocation_values.append((invocation_id, "missing"))
                continue
            join_id = str(raw.get("join_id") or "")
            if join_id:
                join_ids.add(join_id)
            invocation_values.append(
                (
                    invocation_id,
                    str(raw.get("state") or ""),
                    join_id,
                    int(raw.get("llm_round") or 0),
                    int(raw.get("pending_messages") or 0),
                    str(raw.get("active_tool_family") or ""),
                    tuple(sorted(str(item) for item in raw.get("children", ()))),
                    tuple(
                        sorted(
                            str(item)
                            for item in raw.get("blocking_children", ())
                        )
                    ),
                )
            )
        join_values = []
        for join_id in sorted(join_ids):
            raw = joins.get(join_id)
            if not isinstance(raw, Mapping):
                join_values.append((join_id, "missing"))
                continue
            join_values.append(
                (
                    join_id,
                    str(raw.get("mode") or ""),
                    bool(raw.get("satisfied")),
                    tuple(
                        sorted(str(item) for item in raw.get("completed", ()))
                    ),
                )
            )
        return (tuple(invocation_values), tuple(join_values))

    @classmethod
    def _local_prediction_bucket(
        cls,
        invocation_id: str,
        raw: object,
    ) -> tuple[object, ...]:
        if not isinstance(raw, Mapping):
            return (invocation_id, "unavailable")
        boundary = raw.get("boundary_distribution", {})
        if isinstance(boundary, Mapping) and boundary:
            name, probability = max(
                ((str(key), float(value)) for key, value in boundary.items()),
                key=lambda item: (item[1], item[0]),
            )
            boundary_bucket: tuple[object, ...] = (
                name,
                int(probability * 20),
            )
        else:
            boundary_bucket = ("unavailable", 0)
        return (
            invocation_id,
            str(raw.get("support_level") or "unavailable"),
            int(float(raw.get("calibration_coverage") or 0.0) * 20),
            int(raw.get("current_sequence_tokens") or 0) // 512,
            cls._distribution_bucket(raw.get("remaining_decode_tokens"), 16),
            cls._distribution_bucket(
                (
                    raw.get("wait_belief", {}).get("residual_duration")
                    if isinstance(raw.get("wait_belief"), Mapping)
                    else None
                )
                or raw.get("remaining_external_wait"),
                0,
            ),
            cls._distribution_bucket(raw.get("prompt_growth_tokens"), 16),
            cls._distribution_bucket(raw.get("next_output_tokens"), 16),
            boundary_bucket,
            tuple(sorted(str(item) for item in raw.get("ood_reasons", ()))),
        )

    @staticmethod
    def _distribution_bucket(raw: object, linear_width: int) -> int:
        if not isinstance(raw, Mapping):
            return 0
        values = tuple(float(item) for item in raw.get("values", ()))
        probabilities = tuple(
            float(item) for item in raw.get("probability_mass", ())
        )
        if not values or len(values) != len(probabilities):
            return 0
        cumulative = 0.0
        median = values[-1]
        for value, probability in zip(values, probabilities):
            cumulative += probability
            if cumulative >= 0.5:
                median = value
                break
        if linear_width > 0:
            return int(median) // linear_width
        return int(math.log2(max(0.0, median) + 1.0))


@dataclass(frozen=True)
class PredictiveActionCertificate:
    package_id: str
    action: str
    source_snapshot_id: str
    target_context_id: str | None
    context_epochs: tuple[tuple[str, int], ...]
    invocation_evidence: tuple[tuple[str, str, float, str | None], ...]
    join_evidence: tuple[tuple[str, str, bool, tuple[str, ...]], ...]
    communication_evidence: tuple[tuple[str, str, int, float], ...]
    bundle_evidence: tuple[tuple[str, str, int, int], ...]
    required_hbm_free_bytes: int
    required_host_free_bytes: int
    transfer_epoch: int
    transfer_service_evidence: tuple[float, float, float]
    model_version: str

    def causal_dict(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "action": self.action,
            "source_snapshot_id": self.source_snapshot_id,
            "target_context_id": self.target_context_id,
            "context_epochs": [list(item) for item in self.context_epochs],
            "invocation_evidence": [list(item) for item in self.invocation_evidence],
            "join_evidence": [
                [join_id, mode, satisfied, list(completed)]
                for join_id, mode, satisfied, completed in self.join_evidence
            ],
            "communication_evidence": [
                list(item) for item in self.communication_evidence
            ],
            "required_hbm_free_bytes": self.required_hbm_free_bytes,
            "required_host_free_bytes": self.required_host_free_bytes,
            "transfer_epoch": self.transfer_epoch,
            "transfer_service_evidence": list(
                self.transfer_service_evidence
            ),
            "model_version": self.model_version,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "action": self.action,
            "source_snapshot_id": self.source_snapshot_id,
            "target_context_id": self.target_context_id,
            "context_epochs": [list(item) for item in self.context_epochs],
            "invocation_evidence": [list(item) for item in self.invocation_evidence],
            "join_evidence": [
                [join_id, mode, satisfied, list(completed)]
                for join_id, mode, satisfied, completed in self.join_evidence
            ],
            "communication_evidence": [
                list(item) for item in self.communication_evidence
            ],
            "bundle_evidence": [list(item) for item in self.bundle_evidence],
            "required_hbm_free_bytes": self.required_hbm_free_bytes,
            "required_host_free_bytes": self.required_host_free_bytes,
            "transfer_epoch": self.transfer_epoch,
            "transfer_service_evidence": list(
                self.transfer_service_evidence
            ),
            "model_version": self.model_version,
        }


def validate_predictive_causal_certificate(
    raw: Mapping[str, object],
    graph_state: Mapping[str, object] | RuntimeCausalContextGraph,
    *,
    current_model_version: str,
) -> tuple[str, ...]:
    """Validate action-specific causal evidence without binding Radix extents."""

    reasons: list[str] = []

    def read(value: object, name: str, default: object = None) -> object:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)

    def enum_value(value: object) -> object:
        return getattr(value, "value", value)

    state: object = graph_state
    nested = read(state, "rccg")
    if isinstance(nested, Mapping):
        state = nested
    contexts = read(state, "contexts", {})
    invocations = read(state, "invocations", {})
    joins = read(state, "joins", {})
    if not all(isinstance(item, Mapping) for item in (contexts, invocations, joins)):
        return ("invalid_current_graph_snapshot",)
    for item in raw.get("context_epochs", ()):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            reasons.append("invalid_context_readset")
            continue
        context_id, epoch = str(item[0]), int(item[1])
        current_context = contexts.get(context_id)
        if (
            current_context is None
            or int(read(current_context, "epoch", -1)) != epoch
        ):
            reasons.append(f"context_epoch:{context_id}")
    for item in raw.get("invocation_evidence", ()):
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            reasons.append("invalid_invocation_readset")
            continue
        invocation_id, expected_state, updated_ts, join_id = item
        current_invocation = invocations.get(str(invocation_id))
        if current_invocation is None:
            reasons.append(f"invocation_missing:{invocation_id}")
            continue
        if str(enum_value(read(current_invocation, "state"))) != str(
            expected_state
        ):
            reasons.append(f"invocation_state:{invocation_id}")
        if float(read(current_invocation, "updated_ts_ms", -1.0)) != float(updated_ts):
            reasons.append(f"invocation_revision:{invocation_id}")
        if read(current_invocation, "join_id") != join_id:
            reasons.append(f"invocation_join:{invocation_id}")
    for item in raw.get("join_evidence", ()):
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            reasons.append("invalid_join_readset")
            continue
        join_id, mode, satisfied, completed = item
        current_join = joins.get(str(join_id))
        if current_join is None:
            reasons.append(f"join_missing:{join_id}")
            continue
        current_completed = read(
            current_join,
            "completed",
            read(current_join, "completed_member_ids", ()),
        )
        if (
            str(enum_value(read(current_join, "mode"))) != str(mode)
            or bool(read(current_join, "satisfied", False)) != bool(satisfied)
            or tuple(sorted(str(value) for value in current_completed))
            != tuple(sorted(str(value) for value in completed))
        ):
            reasons.append(f"join_revision:{join_id}")
    raw_edges = read(state, "communication_edges", ())
    if isinstance(state, RuntimeCausalContextGraph):
        current_edges = state.communication_edges
    else:
        edge_values = (
            raw_edges.values() if isinstance(raw_edges, Mapping) else raw_edges
        )
        current_edges = {
            (
                str(read(item, "source_invocation_id")),
                str(read(item, "target_invocation_id")),
            ): item
            for item in edge_values
        }
    for item in raw.get("communication_evidence", ()):
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            reasons.append("invalid_communication_readset")
            continue
        source, target, count, last_ts = item
        current_edge = current_edges.get((str(source), str(target)))
        if current_edge is None:
            reasons.append(f"communication_missing:{source}:{target}")
        elif (
            int(read(current_edge, "count", -1)) != int(count)
            or float(read(current_edge, "last_ts_ms", -1.0)) != float(last_ts)
        ):
            reasons.append(f"communication_revision:{source}:{target}")
    if current_model_version != str(raw.get("model_version") or ""):
        reasons.append("belief_model_version")
    return tuple(sorted(set(reasons)))


def validate_predictive_certificate(
    raw: Mapping[str, object],
    current: PolicyInput,
    *,
    current_transfer_epoch: int | None = None,
) -> tuple[str, ...]:
    """Validate causal and physical dependencies read by one action package."""

    model_metadata = current.optional_metadata.get(
        "frontier_prediction_model_version"
    )
    current_model = str(model_metadata.value) if model_metadata is not None else ""
    reasons = list(
        validate_predictive_causal_certificate(
            raw,
            current.runtime_graph.state,
            current_model_version=current_model,
        )
    )
    current_bundles = {
        item.bundle_id: item for item in current.physical_kv.bundles
    }
    for item in raw.get("bundle_evidence", ()):
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            reasons.append("invalid_bundle_readset")
            continue
        bundle_id, generation, gpu_bytes, cpu_bytes = item
        bundle = current_bundles.get(str(bundle_id))
        if bundle is None:
            reasons.append(f"bundle_missing:{bundle_id}")
        elif (
            bundle.generation_fingerprint != str(generation)
            or bundle.gpu_bytes != int(gpu_bytes)
            or bundle.cpu_bytes != int(cpu_bytes)
        ):
            reasons.append(f"bundle_generation:{bundle_id}")
    available = max(
        0,
        current.resources.hbm_capacity_bytes
        - current.resources.policy_hbm_used_bytes
        - current.resources.hbm_reserved_bytes,
    )
    if available < int(raw.get("required_hbm_free_bytes", 0)):
        reasons.append("hbm_capacity_floor")
    if current.resources.host_free_bytes < int(
        raw.get("required_host_free_bytes", 0)
    ):
        reasons.append("host_capacity_floor")
    action = str(raw.get("action") or "")
    if (
        action == PredictiveActionKind.RECLAIM_AND_PREFETCH.value
        and current_transfer_epoch is not None
        and current_transfer_epoch != int(raw.get("transfer_epoch", -1))
    ):
        reasons.append("transfer_epoch")
    service_evidence = raw.get("transfer_service_evidence", ())
    current_service = (
        current.resources.h2d_service_bytes_per_ms,
        current.resources.d2h_service_bytes_per_ms,
        current.resources.transfer_setup_p50_ms,
    )
    if (
        not isinstance(service_evidence, (list, tuple))
        or len(service_evidence) != 3
        or tuple(float(item) for item in service_evidence) != current_service
    ):
        reasons.append("transfer_service_curve")
    return tuple(sorted(set(reasons)))


class PredictiveRiskShadowObserver:
    """Evaluate P6 packages off the scheduler path without applying actions."""

    def __init__(
        self,
        service_model: GPUServiceCurveModel,
        config: PredictiveRiskShadowConfig | None = None,
        *,
        frontier_model: FrontierBeliefModel | None = None,
    ) -> None:
        self.config = config or PredictiveRiskShadowConfig()
        self.frontier_model = frontier_model
        self.eligibility_index = PredictiveEligibilityIndex()
        self.scope_builder = BeliefScopeBuilder(
            BeliefScopeConfig(
                max_atomic_groups=self.config.max_candidates,
                max_total_model_cost=max(32, self.config.max_candidates * 4),
            )
        )
        self.composer = FrontierScenarioComposer(
            particle_count=self.config.particle_count,
            top_k=self.config.top_k,
        )
        self.timeline = CandidateTimelineEvaluator(
            service_model,
            service_quantile=self.config.service_quantile,
        )
        self.risk_planner = ScenarioRiskPlanner(
            ScenarioRiskPlannerConfig(
                risk_budget_ms=10.0,
                minimum_future_feasibility_probability=0.95,
            )
        )
        self._belief_cache: OrderedDict[
            str, tuple[tuple[Any, ...], ...]
        ] = OrderedDict()
        self._belief_cache_entries = 128

    @staticmethod
    def _belief_scope_seed_ids(
        graph: RuntimeCausalContextGraph,
        *,
        required: tuple[str, ...],
        optional: tuple[str, ...] = (),
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        required_ids = tuple(dict.fromkeys(required))
        missing = tuple(
            invocation_id
            for invocation_id in required_ids
            if invocation_id not in graph.invocations
        )
        if missing:
            return (), missing
        return (
            tuple(
                dict.fromkeys(
                    (
                        *required_ids,
                        *(
                            item for item in optional if item in graph.invocations
                        ),
                    )
                )
            ),
            (),
        )

    def evaluate(
        self,
        policy_input: PolicyInput,
        *,
        graph: RuntimeCausalContextGraph,
        source_plan: JointPlan,
        evidence_read_set: PredictiveEvidenceReadSet,
        eligibility: PredictiveEligibility | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PredictiveRiskShadowResult:
        started_ns = time.perf_counter_ns()
        if eligibility is None:
            runtime_state = policy_input.runtime_graph.state
            nested = runtime_state.get("rccg")
            if isinstance(nested, Mapping):
                runtime_state = nested
            runtime_invocations = runtime_state.get("invocations", {})
            eligibility = self.eligibility_index.probe(
                policy_input,
                graph_state=(
                    graph.snapshot()
                    if not runtime_invocations and graph.invocations
                    else None
                ),
            )
        scheduling_candidates = self._scheduling_candidates(
            policy_input,
            source_plan,
            limit=max(2, min(4, self.config.max_candidates)),
        )
        if not eligibility.has_candidate and len(scheduling_candidates) < 2:
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=None,
                reasons=("no_action_specific_candidate",),
            )
        projected_requirement = self._projected_reclaim_requirement(
            policy_input,
            source_plan,
        )
        if (
            projected_requirement is None
            and not eligibility.prefetch_targets
            and not scheduling_candidates
        ):
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=None,
                reasons=("no_predictive_joint_candidate",),
            )
        if cancel_check is not None and cancel_check():
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=None,
                reasons=("cancelled_superseded",),
            )
        prediction_metadata = policy_input.optional_metadata.get(
            "frontier_predictions"
        )
        feature_metadata = policy_input.optional_metadata.get("frontier_features")
        model_metadata = policy_input.optional_metadata.get(
            "frontier_prediction_model_version"
        )
        model_version = (
            str(model_metadata.value)
            if model_metadata is not None
            else (
                str(self.frontier_model.model_version)
                if self.frontier_model is not None
                else None
            )
        )
        prediction_payload = (
            prediction_metadata.value
            if prediction_metadata is not None
            and isinstance(prediction_metadata.value, Mapping)
            else {}
        )
        feature_payload = (
            feature_metadata.value
            if feature_metadata is not None
            and isinstance(feature_metadata.value, Mapping)
            else {}
        )
        if not prediction_payload and (
            self.frontier_model is None or not feature_payload
        ):
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=model_version,
                reasons=("frontier_inputs_unavailable",),
            )

        primary = (
            eligibility.prepare_host_victims[0]
            if eligibility.prepare_host_victims
            else eligibility.prefetch_targets[0]
            if eligibility.prefetch_targets
            else scheduling_candidates[0]
        )
        request_by_id = {
            request.request_id: request
            for request in policy_input.runnable_frontier
        }
        slot_witness = next(
            (
                request_by_id[request_id].invocation_id
                for request_id in source_plan.execution.ordered_request_ids
                if request_id in request_by_id
            ),
            None,
        )
        seed_ids, missing_required = self._belief_scope_seed_ids(
            graph,
            required=tuple(
                dict.fromkeys(
                    (
                        *(
                            (projected_requirement.beneficiary_invocation_id,)
                            if projected_requirement is not None
                            else ()
                        ),
                        *(
                            item.invocation_id
                            for item in scheduling_candidates
                        ),
                        *(
                            item.invocation_id
                            for item in eligibility.prepare_host_victims[:2]
                        ),
                        *(
                            item.invocation_id
                            for item in eligibility.prefetch_targets[:4]
                        ),
                    )
                )
            ),
            optional=((slot_witness,) if slot_witness is not None else ()),
        )
        if missing_required:
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=model_version,
                reasons=("belief_scope_required_invocation_missing",),
            )
        # The current predictive actions never change active ownership. Physical
        # blockers therefore remain deterministic commit constraints instead of
        # semantic scope dependencies. Predictive retraction must add its own
        # candidate-specific blocker closure when that action is introduced.
        scope = self.scope_builder.build(graph, seed_ids)
        if not scope.invocation_ids:
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=model_version,
                reasons=("belief_scope_fully_residual",),
            )
        try:
            predictions = {
                invocation_id: LocalFrontierPrediction.from_dict(
                    prediction_payload[invocation_id]
                )
                for invocation_id in scope.invocation_ids
                if isinstance(prediction_payload.get(invocation_id), Mapping)
            }
        except (KeyError, TypeError, ValueError) as error:
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=model_version,
                reasons=(f"invalid_prediction_payload:{type(error).__name__}",),
            )
        missing = sorted(set(scope.invocation_ids).difference(predictions))
        if missing and self.frontier_model is not None:
            try:
                for invocation_id in missing:
                    raw_features = feature_payload.get(invocation_id)
                    if not isinstance(raw_features, Mapping):
                        continue
                    features = LocalFrontierFeatures.from_dict(raw_features)
                    if features.invocation_id != invocation_id:
                        raise ValueError("frontier feature identity mismatch")
                    predictions[invocation_id] = self.frontier_model.predict(features)
            except (KeyError, TypeError, ValueError) as error:
                return self._skipped(
                    policy_input,
                    source_plan,
                    started_ns,
                    model_version=model_version,
                    reasons=(
                        f"invalid_frontier_feature_payload:{type(error).__name__}",
                    ),
                )
            except Exception as error:
                return self._skipped(
                    policy_input,
                    source_plan,
                    started_ns,
                    model_version=model_version,
                    reasons=(
                        f"candidate_local_prediction_failed:{type(error).__name__}",
                    ),
                )
        missing = sorted(set(scope.invocation_ids).difference(predictions))
        if missing:
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=model_version,
                reasons=("closure_prediction_incomplete",),
            )
        predictive_execution_order = self._predictive_execution_order(
            scheduling_candidates, predictions
        )
        scheduling_beneficiary = next(
            (
                item
                for request_id in predictive_execution_order
                for item in scheduling_candidates
                if item.request_id == request_id
            ),
            None,
        )
        belief_started_ns = time.perf_counter_ns()
        particle_hits_before, particle_misses_before, _ = (
            self.composer.local_particle_cache_stats()
        )
        semantic_key = self._semantic_belief_key(
            graph,
            scope,
            predictions,
            model_version=model_version or "unavailable",
        )
        semantic_seed_payload = (model_version or "unavailable").encode()
        semantic_seed = int.from_bytes(
            hashlib.blake2b(
                semantic_seed_payload,
                digest_size=8,
                person=b"bkv-risk",
            ).digest(),
            "big",
        )
        cached_particles = self._belief_cache.get(semantic_key)
        belief_cache_hit = cached_particles is not None
        if belief_cache_hit:
            particles = cached_particles
            self._belief_cache.move_to_end(semantic_key)
        else:
            try:
                particles = self.composer.sample_particles(
                    graph=graph,
                    scope=scope,
                    local_predictions=predictions,
                    seed=semantic_seed,
                )
            except (KeyError, TypeError, ValueError) as error:
                return self._skipped(
                    policy_input,
                    source_plan,
                    started_ns,
                    model_version=model_version,
                    reasons=(f"belief_compose_failed:{type(error).__name__}",),
                )
            self._belief_cache[semantic_key] = particles
            self._belief_cache.move_to_end(semantic_key)
            while len(self._belief_cache) > self._belief_cache_entries:
                self._belief_cache.popitem(last=False)

        projected_beliefs: dict[
            tuple[ScenarioProjection, str], Any
        ] = {}

        def projected_belief(
            projection: ScenarioProjection,
            invocation_id: str,
        ) -> Any:
            key = (projection, invocation_id)
            cached = projected_beliefs.get(key)
            if cached is not None:
                return cached
            value = self.composer.reduce_particles(
                particles=particles,
                scope=scope,
                local_predictions=predictions,
                generated_ts_ms=policy_input.resources.ts_ms,
                evidence_read_set=evidence_read_set,
                projection=projection,
                target_invocation_id=invocation_id,
            )
            projected_beliefs[key] = value
            return value

        default_projection = ScenarioProjection.FULL
        belief = projected_belief(default_projection, primary.invocation_id)
        belief_compose_ms = (
            time.perf_counter_ns() - belief_started_ns
        ) / 1_000_000.0

        if cancel_check is not None and cancel_check():
            return self._skipped(
                policy_input,
                source_plan,
                started_ns,
                model_version=model_version,
                reasons=("cancelled_superseded",),
            )
        target_invocation_id = primary.invocation_id
        target_context_id = primary.context_id
        candidate_started_ns = time.perf_counter_ns()
        packages = self._candidate_packages(
            policy_input,
            source_plan,
            eligibility,
            allowed_invocation_ids=frozenset(scope.invocation_ids),
            projected_requirement=projected_requirement,
            predictive_execution_order=predictive_execution_order,
            scheduling_beneficiary=scheduling_beneficiary,
        )
        baseline = packages[0]
        blocked: list[str] = []
        candidates: list[PredictiveActionPackage] = []
        support_by_package: dict[str, tuple[tuple[str, str], ...]] = {}
        for package in packages[1:]:
            supported, support, reasons = self._package_prediction_support(
                package,
                eligibility=eligibility,
                predictions=predictions,
            )
            support_by_package[package.package_id] = support
            if supported:
                candidates.append(package)
            else:
                blocked.extend(
                    f"{package.action.value}:{reason}" for reason in reasons
                )

        physicalizer = _OnlineCandidatePhysicalizer(
            policy_input,
            graph,
            source_plan,
            target_invocation_id=target_invocation_id,
            target_context_id=target_context_id,
            belief_scope_invocation_ids=scope.invocation_ids,
            packages={item.package_id: item for item in packages},
            kv_bytes_per_token=self.config.kv_bytes_per_token,
        )
        candidate_generation_ms = (
            time.perf_counter_ns() - candidate_started_ns
        ) / 1_000_000.0
        preflight_started_ns = time.perf_counter_ns()
        feasibility = {
            package.package_id: physicalizer.package_feasible(package)
            for package in candidates
        }
        deterministic_preflight_ms = (
            time.perf_counter_ns() - preflight_started_ns
        ) / 1_000_000.0
        risk_started_ns = time.perf_counter_ns()
        service_hits_before, service_misses_before, _ = (
            self.timeline.service_cache_stats()
        )
        summaries: list[PackageRiskSummary] = []
        projection_by_package: dict[str, str] = {}
        belief_by_package: dict[str, Any] = {}
        timing_by_package: dict[str, _ActionTimingEvidence] = {}
        selected_package_id = baseline.package_id
        selected_benefit_ms = 0.0
        evaluation_by_package: dict[str, PackageScenarioEvaluation] = {}
        resolved_package_by_id: dict[str, PredictiveActionPackage] = {
            baseline.package_id: baseline
        }
        baseline_by_projection: dict[
            tuple[ScenarioProjection, str, str],
            tuple[
                PackageScenarioEvaluation,
                Mapping[str, TimedScenario],
                Mapping[str, TimedScenario],
            ],
        ] = {}
        for package in candidates:
            if cancel_check is not None and cancel_check():
                return self._skipped(
                    policy_input,
                    source_plan,
                    started_ns,
                    model_version=model_version,
                    reasons=("cancelled_superseded",),
                )
            package_invocation_id = self._package_invocation_id(
                package,
                eligibility=eligibility,
            )
            projection = (
                ScenarioProjection.PREPARE_HOST
                if package.action == PredictiveActionKind.PREPARE_HOST
                else ScenarioProjection.PREFETCH
                if package.action
                in {
                    PredictiveActionKind.PREFETCH_GPU,
                    PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                    PredictiveActionKind.RECLAIM_AND_PREFETCH,
                }
                else ScenarioProjection.FULL
            )
            candidate_belief = projected_belief(
                projection,
                package_invocation_id,
            )
            belief_by_package[package.package_id] = candidate_belief
            projection_by_package[package.package_id] = projection.value
            if not feasibility[package.package_id]:
                summaries.append(
                    PackageRiskSummary(
                        package_id=package.package_id,
                        expected_benefit_ms=0.0,
                        expected_recourse_credit_ms=0.0,
                        cvar_regret_ms=0.0,
                        future_feasibility_probability=0.0,
                        future_hbm_feasibility_probability=0.0,
                        worst_future_hbm_peak_bytes=0,
                        worst_future_hbm_overflow_bytes=0,
                        eligible=False,
                        reasons=("deterministic_hard_constraint",),
                    )
                )
                continue
            reactive_baseline = self._reactive_baseline_package(
                baseline, package, source_plan
            )
            physicalizer.register_package(reactive_baseline)
            baseline_key = (
                projection,
                package_invocation_id,
                reactive_baseline.package_id,
            )
            baseline_result = baseline_by_projection.get(baseline_key)
            if baseline_result is None:
                try:
                    baseline_result = self._evaluate_package(
                        candidate_belief,
                        reactive_baseline,
                        physicalizer,
                        package_invocation_id,
                        cancel_check=cancel_check,
                    )
                except RuntimeError:
                    if cancel_check is None or not cancel_check():
                        raise
                    return self._skipped(
                        policy_input,
                        source_plan,
                        started_ns,
                        model_version=model_version,
                        reasons=("cancelled_superseded",),
                    )
                baseline_by_projection[baseline_key] = baseline_result
            (
                baseline_evaluation,
                baseline_timelines,
                conservative_baseline_timelines,
            ) = baseline_result
            try:
                candidate_evaluation, _, _ = self._evaluate_package(
                    candidate_belief,
                    package,
                    physicalizer,
                    package_invocation_id,
                    cancel_check=cancel_check,
                    baseline_timelines=baseline_timelines,
                    conservative_baseline_timelines=(
                        conservative_baseline_timelines
                    ),
                )
            except RuntimeError:
                if cancel_check is None or not cancel_check():
                    raise
                return self._skipped(
                    policy_input,
                    source_plan,
                    started_ns,
                    model_version=model_version,
                    reasons=("cancelled_superseded",),
                )
            candidate_decision = self.risk_planner.select(
                candidate_belief,
                baseline_evaluation,
                (candidate_evaluation,),
            )
            summary = candidate_decision.summaries[0]
            evaluation_by_package[package.package_id] = candidate_evaluation
            resolved_package = candidate_evaluation.package
            resolved_package_by_id[package.package_id] = resolved_package
            if package.action in {
                PredictiveActionKind.PREPARE_HOST,
                PredictiveActionKind.PREFETCH_GPU,
                PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                PredictiveActionKind.RECLAIM_AND_PREFETCH,
            }:
                timing = self._action_timing_evidence(
                    resolved_package,
                    belief=candidate_belief,
                    evaluation=candidate_evaluation,
                    eligibility=eligibility,
                    predictions=predictions,
                    physicalizer=physicalizer,
                )
                if timing is None:
                    summary = replace(
                        summary,
                        eligible=False,
                        reasons=tuple(summary.reasons)
                        + ("action_timing_unavailable",),
                    )
                else:
                    timing_by_package[package.package_id] = timing
                    minimum_probability = _minimum_action_timing_probability(
                        package.action,
                        timing,
                        self.config,
                    )
                    if not timing.informative:
                        summary = replace(
                            summary,
                            eligible=False,
                            reasons=tuple(summary.reasons)
                            + ("action_timing_not_better_than_prior",),
                        )
                    elif timing.causal_slack_probability < minimum_probability:
                        summary = replace(
                            summary,
                            eligible=False,
                            reasons=tuple(summary.reasons)
                            + ("insufficient_causal_slack_probability",),
                        )
                    if package.action == PredictiveActionKind.PREPARE_HOST:
                        transfer_ready_ms = (
                            physicalizer.package_transfer_duration_ms(
                                resolved_package
                            )
                            * self.config.transfer_p95_safety_factor
                            + self.config.transfer_commit_guard_ms
                        )
                        feasibility_reasons = (
                            _prepare_beneficiary_feasibility_reasons(
                                resolved_package,
                                transfer_ready_ms=transfer_ready_ms,
                            )
                        )
                        if feasibility_reasons:
                            summary = replace(
                                summary,
                                eligible=False,
                                reasons=tuple(summary.reasons)
                                + feasibility_reasons,
                            )
            summaries.append(summary)
            if summary.eligible and summary.expected_benefit_ms > selected_benefit_ms:
                selected_benefit_ms = summary.expected_benefit_ms
                selected_package_id = package.package_id
        decision = ScenarioRiskDecision(
            selected_package_id=selected_package_id,
            baseline_package_id=baseline.package_id,
            summaries=tuple(summaries),
        )
        if selected_package_id in belief_by_package:
            belief = belief_by_package[selected_package_id]
        package_by_id = {item.package_id: item for item in packages}
        package_by_id.update(resolved_package_by_id)
        action_by_package = {
            item.package_id: item.action.value for item in packages
        }
        summary_by_package = {
            item.package_id: item for item in decision.summaries
        }
        certificates = {
            package.package_id: physicalizer.certificate(
                package,
                evidence_read_set=evidence_read_set,
                model_version=model_version or "unavailable",
            )
            for package in candidates
        }
        predictive_intent = self._predictive_intent(
            package_by_id.get(decision.selected_package_id),
            summary_by_package.get(decision.selected_package_id),
            belief=belief,
            evaluation=evaluation_by_package.get(decision.selected_package_id),
            policy_input=policy_input,
            graph=graph,
            eligibility=eligibility,
            predictions=predictions,
            physicalizer=physicalizer,
            support=support_by_package.get(decision.selected_package_id, ()),
            certificate=certificates.get(decision.selected_package_id),
            timing_evidence=timing_by_package.get(
                decision.selected_package_id
            ),
        )
        return PredictiveRiskShadowResult(
            status="evaluated",
            source_joint_plan_id=source_plan.plan_id,
            source_snapshot_id=policy_input.snapshot_id,
            belief_id=belief.belief_id,
            model_version=model_version,
            support_level=belief.support_level,
            calibration_coverage=belief.calibration_coverage,
            other_probability_mass=belief.other_probability_mass,
            target_invocation_id=target_invocation_id,
            target_context_id=target_context_id,
            selected_package_id=decision.selected_package_id,
            selected_action=action_by_package[decision.selected_package_id],
            candidate_count=len(candidates),
            candidate_summaries=self._summary_payload(
                decision,
                actions=action_by_package,
                packages=package_by_id,
                support_by_package=support_by_package,
                projection_by_package=projection_by_package,
                timing_by_package=timing_by_package,
                certificates={
                    package_id: certificate.to_dict()
                    for package_id, certificate in certificates.items()
                },
            ),
            ood_reasons=belief.ood_reasons,
            blocked_reasons=tuple(sorted(set(blocked))),
            planning_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
            scenario_count=len(belief.scenarios),
            belief_compose_ms=belief_compose_ms,
            candidate_generation_ms=candidate_generation_ms,
            deterministic_preflight_ms=deterministic_preflight_ms,
            scenario_risk_ms=(
                time.perf_counter_ns() - risk_started_ns
            )
            / 1_000_000.0,
            service_cache_hits=(
                self.timeline.service_cache_hits - service_hits_before
            ),
            service_cache_misses=(
                self.timeline.service_cache_misses - service_misses_before
            ),
            belief_cache_hit=belief_cache_hit,
            local_particle_cache_hits=(
                self.composer.local_particle_cache_hits
                - particle_hits_before
            ),
            local_particle_cache_misses=(
                self.composer.local_particle_cache_misses
                - particle_misses_before
            ),
            predictive_intent=predictive_intent,
            decision_authority=(
                "semantic_joint_overlay"
                if self.config.online_overlay_enabled
                else "read_only_shadow"
            ),
        )

    def _package_prediction_support(
        self,
        package: PredictiveActionPackage,
        *,
        eligibility: PredictiveEligibility,
        predictions: Mapping[str, LocalFrontierPrediction],
    ) -> tuple[bool, tuple[tuple[str, str], ...], tuple[str, ...]]:
        """Check only prediction heads that can change this package."""

        if package.action == PredictiveActionKind.SCHEDULE:
            prediction = predictions.get(package.beneficiary_invocation_id or "")
            if prediction is None:
                return False, (), ("local_prediction_missing",)
            if (
                prediction.calibration_coverage
                < self.config.minimum_calibration_coverage
            ):
                return False, (), ("calibration_coverage",)
            demand_head = (
                "remaining_decode_demand"
                if prediction.remaining_decode_tokens.values
                else "next_output_demand"
            )
            support = ((demand_head, prediction.support_for(demand_head)),)
            unavailable = tuple(
                f"{name}_unavailable"
                for name, level in support
                if level == "unavailable"
            )
            return not unavailable, support, unavailable
        if package.action == PredictiveActionKind.PREPARE_HOST:
            context_id = package.victim_context_ids[0]
            candidate = next(
                (
                    item
                    for item in eligibility.prepare_host_victims
                    if item.context_id == context_id
                ),
                None,
            )
        elif package.action in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            context_id = package.target_context_id or ""
            candidate = next(
                (
                    item
                    for item in eligibility.prefetch_targets
                    if item.context_id == context_id
                ),
                None,
            )
        else:
            return False, (), ("unsupported_predictive_action",)
        if candidate is None:
            return False, (), ("action_candidate_missing",)
        prediction = predictions.get(candidate.invocation_id)
        if prediction is None:
            return False, (), ("local_prediction_missing",)
        if candidate.state not in (
            PredictiveEligibilityIndex._WAIT_STATES
            | PredictiveEligibilityIndex._SERVICE_STATES
        ):
            return False, (), ("target_not_parked",)
        if prediction.calibration_coverage < self.config.minimum_calibration_coverage:
            return False, (), ("calibration_coverage",)

        support: list[tuple[str, str]] = []
        if candidate.state in PredictiveEligibilityIndex._SERVICE_STATES:
            demand_head = (
                "remaining_decode_demand"
                if prediction.remaining_decode_tokens.values
                else "next_output_demand"
            )
            support.append(
                (demand_head, prediction.support_for(demand_head))
            )
        else:
            wait_head, wait_level = self._wait_head_support(
                prediction, candidate.state
            )
            support.append((wait_head, wait_level))
        if package.action != PredictiveActionKind.PREPARE_HOST:
            support.append(
                (
                    "future_kv_growth",
                    self._distribution_head_support(
                        prediction,
                        distribution=prediction.prompt_growth_tokens,
                        interval_name="prompt_growth_tokens",
                    ),
                )
            )
        unavailable = tuple(
            f"{name}_unavailable"
            for name, level in support
            if level == "unavailable"
        )
        return not unavailable, tuple(support), unavailable

    @staticmethod
    def _wait_head_support(
        prediction: LocalFrontierPrediction,
        state: str,
    ) -> tuple[str, str]:
        if state == InvocationState.WAIT_TOOL.value:
            level = prediction.support_for("tool_wait")
            if not prediction.wait_belief.available:
                level = "unavailable"
            return "tool_wait_slack", level
        if state in {
            InvocationState.WAIT_JOIN.value,
            InvocationState.WAIT_CHILD.value,
        }:
            return (
                "join_dependency_slack",
                prediction.support_for("join_dependency"),
            )
        if state == InvocationState.WAIT_MESSAGE.value:
            return (
                "message_dependency_slack",
                prediction.support_for("message_dependency"),
            )
        return "wait_slack", "unavailable"

    @staticmethod
    def _package_invocation_id(
        package: PredictiveActionPackage,
        *,
        eligibility: PredictiveEligibility,
    ) -> str:
        if package.action == PredictiveActionKind.SCHEDULE:
            if package.beneficiary_invocation_id is None:
                raise ValueError("schedule package has no beneficiary invocation")
            return package.beneficiary_invocation_id
        context_id = (
            package.victim_context_ids[0]
            if package.action == PredictiveActionKind.PREPARE_HOST
            else package.target_context_id or ""
        )
        candidates = (
            eligibility.prepare_host_victims
            if package.action == PredictiveActionKind.PREPARE_HOST
            else eligibility.prefetch_targets
        )
        matched = next(
            (item for item in candidates if item.context_id == context_id),
            None,
        )
        if matched is None:
            raise ValueError("predictive package has no matching semantic target")
        return matched.invocation_id

    @staticmethod
    def _distribution_head_support(
        prediction: LocalFrontierPrediction,
        *,
        distribution: Any,
        interval_name: str,
    ) -> str:
        if not distribution.values or distribution.support <= 0:
            return "unavailable"
        if prediction.support_level == "exact":
            return "exact"
        if interval_name in prediction.calibrated_intervals:
            return "calibrated_backoff"
        return "unavailable"

    def _action_timing_evidence(
        self,
        package: PredictiveActionPackage,
        *,
        belief: FrontierBeliefSnapshot,
        evaluation: PackageScenarioEvaluation,
        eligibility: PredictiveEligibility,
        predictions: Mapping[str, LocalFrontierPrediction],
        physicalizer: _OnlineCandidatePhysicalizer,
    ) -> _ActionTimingEvidence | None:
        if package.action == PredictiveActionKind.PREPARE_HOST:
            candidates = eligibility.prepare_host_victims
            context_id = package.victim_context_ids[0]
            release_within = False
            semantics = "release_after_transfer"
        elif package.action in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            candidates = eligibility.prefetch_targets
            context_id = package.target_context_id or ""
            release_within = True
            semantics = "release_within_transfer"
        else:
            return None
        candidate = next(
            (item for item in candidates if item.context_id == context_id),
            None,
        )
        if candidate is None:
            return None
        prediction = predictions.get(candidate.invocation_id)
        if prediction is None:
            return None
        transfer_p95_ms = (
            physicalizer.package_transfer_duration_ms(package)
            * self.config.transfer_p95_safety_factor
        )
        required_wait_ms = transfer_p95_ms + self.config.transfer_commit_guard_ms
        if candidate.state in PredictiveEligibilityIndex._SERVICE_STATES:
            timing_probability = 1.0
            remaining_window_ms = (
                required_wait_ms + self.config.scheduled_service_lead_ms
            )
            calibration_brier_skill = None
            calibration_balanced_accuracy = None
            decision_threshold = None
            raw_decision_threshold = None
            precision_at_decision_threshold = None
            recall_at_decision_threshold = None
            informative = True
        elif candidate.state == InvocationState.WAIT_TOOL.value:
            timing = prediction.action_timing(
                (
                    "prefetch_gpu"
                    if release_within
                    else "prepare_host"
                ),
                required_wait_ms,
            )
            if timing is None:
                return None
            remaining_bound = prediction.wait_belief.conservative_wait_ms(
                max(0.01, min(0.99, timing.raw_decision_threshold))
                if release_within
                else 0.05
            )
            if remaining_bound is None:
                return None
            timing_probability = timing.favorable_probability
            remaining_window_ms = max(0.0, remaining_bound)
            calibration_brier_skill = timing.calibration_brier_skill
            calibration_balanced_accuracy = (
                timing.calibration_balanced_accuracy
            )
            decision_threshold = timing.decision_threshold
            raw_decision_threshold = timing.raw_decision_threshold
            precision_at_decision_threshold = (
                timing.precision_at_decision_threshold
            )
            recall_at_decision_threshold = timing.recall_at_decision_threshold
            informative = timing.informative
        else:
            dependency_timing = self._dependency_release_timing(
                belief,
                evaluation,
                invocation_id=candidate.invocation_id,
                required_wait_ms=required_wait_ms,
                release_within=release_within,
            )
            if dependency_timing is None:
                return None
            timing_probability, remaining_window_ms = dependency_timing
            calibration_brier_skill = None
            calibration_balanced_accuracy = None
            decision_threshold = None
            raw_decision_threshold = None
            precision_at_decision_threshold = None
            recall_at_decision_threshold = None
            informative = True
        return _ActionTimingEvidence(
            semantics=semantics,
            required_wait_ms=required_wait_ms,
            causal_slack_probability=timing_probability,
            conservative_remaining_window_ms=remaining_window_ms,
            calibration_brier_skill=calibration_brier_skill,
            calibration_balanced_accuracy=calibration_balanced_accuracy,
            decision_threshold=decision_threshold,
            raw_decision_threshold=raw_decision_threshold,
            precision_at_decision_threshold=precision_at_decision_threshold,
            recall_at_decision_threshold=recall_at_decision_threshold,
            informative=informative,
        )

    def _predictive_intent(
        self,
        package: PredictiveActionPackage | None,
        summary: Any,
        *,
        belief: FrontierBeliefSnapshot,
        evaluation: PackageScenarioEvaluation | None,
        policy_input: PolicyInput,
        graph: RuntimeCausalContextGraph,
        eligibility: PredictiveEligibility,
        predictions: Mapping[str, LocalFrontierPrediction],
        physicalizer: "_OnlineCandidatePhysicalizer",
        support: tuple[tuple[str, str], ...],
        certificate: PredictiveActionCertificate | None,
        timing_evidence: _ActionTimingEvidence | None,
    ) -> PredictiveIntent | None:
        if (
            package is None
            or summary is None
            or not summary.eligible
            or evaluation is None
            or certificate is None
        ):
            return None

        runnable_by_id = {
            request.request_id: request
            for request in policy_input.runnable_frontier
        }
        beneficiary = runnable_by_id.get(package.beneficiary_request_id or "")
        if (
            beneficiary is None
            and package.action
            not in {
                PredictiveActionKind.PREFETCH_GPU,
                PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            }
        ):
            return None
        beneficiary_invocation = (
            graph.invocations.get(beneficiary.invocation_id)
            if beneficiary is not None
            else None
        )
        if beneficiary is not None and beneficiary_invocation is None:
            return None

        if package.action == PredictiveActionKind.SCHEDULE:
            assert beneficiary is not None
            assert beneficiary_invocation is not None
            candidate_invocation_id = beneficiary.invocation_id
            candidate_state = beneficiary_invocation.state.value
            context_id = beneficiary.context_id
            target_bytes = 0
            shape_fingerprint = "not_applicable"
            predicted_extent_count = 0
            maximum_stall_ms = 0.0
            morphology_slack_ms = 0.0
            transfer_p95_ms = 0.0
            maximum_transfer_ms = 0.0
            timing_probability = 1.0
            remaining_window_ms = 1000.0
            timing_semantics = "execution_window"
            min_reclaimable = max_cross_context = max_copy = 0
            required_heads = tuple(name for name, _level in support)
        elif package.action == PredictiveActionKind.PREPARE_HOST:
            context_id = package.victim_context_ids[0]
            candidate = next(
                (
                    item
                    for item in eligibility.prepare_host_victims
                    if item.context_id == context_id
                ),
                None,
            )
            projection = physicalizer.prepare_projection(package)
            transfer_evidence = physicalizer.prepare_shadow_transfer_evidence(
                package
            )
            interference_evidence = physicalizer.prepare_interference_evidence(
                package, transfer_evidence
            )
            positive_slacks = tuple(
                diagnostic.morphology_slack_ms
                for diagnostic in summary.recourse_diagnostics
                if diagnostic.recourse_failure_reason in {
                    "eligible",
                    "eligible_partial",
                }
                and diagnostic.morphology_slack_ms is not None
                and diagnostic.morphology_slack_ms > 0
            )
            if (
                candidate is None
                or projection is None
                or not transfer_evidence.shape_supported
                or not positive_slacks
                or timing_evidence is None
                or package.predicted_block_time_ms is None
            ):
                return None
            candidate_invocation_id = candidate.invocation_id
            candidate_state = candidate.state
            target_bytes = projection.copy_bytes
            shape_fingerprint = projection.shape_fingerprint
            predicted_extent_count = projection.extent_count
            maximum_stall_ms = max(
                interference_evidence.interference_ms * 1.10,
                interference_evidence.interference_ms + 1.0,
            )
            morphology_slack_ms = min(positive_slacks)
            transfer_p95_ms = (
                timing_evidence.required_wait_ms
                - self.config.transfer_commit_guard_ms
            )
            maximum_transfer_ms = max(
                transfer_p95_ms * 1.10, transfer_p95_ms + 1.0
            )
            timing_probability = timing_evidence.causal_slack_probability
            remaining_window_ms = (
                timing_evidence.conservative_remaining_window_ms
            )
            timing_semantics = timing_evidence.semantics
            min_reclaimable, max_cross_context, max_copy = (
                physicalizer.intent_resource_envelope(package)
            )
            required_heads = tuple(name for name, _level in support)
        elif package.action in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            context_id = package.target_context_id or ""
            candidate = next(
                (
                    item
                    for item in eligibility.prefetch_targets
                    if item.context_id == context_id
                ),
                None,
            )
            if candidate is None or timing_evidence is None:
                return None
            candidate_invocation_id = candidate.invocation_id
            candidate_state = candidate.state
            target_bytes = physicalizer._target_restore_bytes(package)
            prefix_projection = (
                physicalizer.prefetch_prefix_projection(package)
                if package.action
                in {
                    PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                    PredictiveActionKind.RECLAIM_AND_PREFETCH,
                }
                else None
            )
            if (
                package.action
                in {
                    PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                    PredictiveActionKind.RECLAIM_AND_PREFETCH,
                }
                and prefix_projection is None
            ):
                return None
            shape_fingerprint = (
                prefix_projection.shape_fingerprint
                if prefix_projection is not None
                else "prefetch-not-shape-certified"
            )
            predicted_extent_count = (
                prefix_projection.extent_count
                if prefix_projection is not None
                else 0
            )
            maximum_stall_ms = 0.0
            morphology_slack_ms = 0.0
            transfer_p95_ms = (
                timing_evidence.required_wait_ms
                - self.config.transfer_commit_guard_ms
            )
            maximum_transfer_ms = max(
                transfer_p95_ms * 1.10, transfer_p95_ms + 1.0
            )
            timing_probability = timing_evidence.causal_slack_probability
            remaining_window_ms = (
                timing_evidence.conservative_remaining_window_ms
            )
            timing_semantics = timing_evidence.semantics
            min_reclaimable, max_cross_context, max_copy = (
                physicalizer.intent_resource_envelope(package)
            )
            required_heads = tuple(name for name, _level in support)
        else:
            return None

        if context_id not in graph.contexts:
            return None
        prediction = predictions.get(candidate_invocation_id)
        if prediction is None or (
            package.action != PredictiveActionKind.SCHEDULE and max_copy <= 0
        ):
            return None
        execution_evidence = tuple(
            (
                request_id,
                runnable_by_id[request_id].invocation_id,
                runnable_by_id[request_id].context_id,
                runnable_by_id[request_id].context_epoch,
            )
            for request_id in package.execution_order_request_ids
            if request_id in runnable_by_id
        )
        if len(execution_evidence) != len(package.execution_order_request_ids):
            return None
        identity = (
            f"{package.package_id}|{context_id}|{graph.contexts[context_id].epoch}|"
            f"{policy_input.resources.ts_ms:.3f}"
        )
        digest = hashlib.blake2b(
            identity.encode(), digest_size=12, person=b"bkv-intent"
        ).hexdigest()
        return PredictiveIntent(
            intent_id=f"predictive-intent-{digest}",
            source_joint_plan_id=package.source_joint_plan_id or "unavailable",
            source_snapshot_id=policy_input.snapshot_id,
            package_id=package.package_id,
            model_version=(
                str(
                    policy_input.optional_metadata[
                        "frontier_prediction_model_version"
                    ].value
                )
                if "frontier_prediction_model_version"
                in policy_input.optional_metadata
                else "unavailable"
            ),
            action=package.action,
            invocation_id=candidate_invocation_id,
            expected_invocation_state=candidate_state,
            context_id=context_id,
            context_epoch=graph.contexts[context_id].epoch,
            generated_ts_ms=policy_input.resources.ts_ms,
            remaining_window_low_ms=remaining_window_ms,
            transfer_p95_ms=transfer_p95_ms,
            target_bytes_hint=target_bytes,
            min_reclaimable_bytes=min_reclaimable,
            max_cross_context_bytes=max_cross_context,
            max_copy_bytes=max_copy,
            causal_certificate=certificate.causal_dict(),
            required_prediction_heads=required_heads,
            prediction_head_support=support,
            calibration_coverage=prediction.calibration_coverage,
            future_hbm_feasibility_probability=(
                summary.future_hbm_feasibility_probability
            ),
            expected_benefit_ms=summary.expected_benefit_ms,
            shape_fingerprint=shape_fingerprint,
            predicted_extent_count=predicted_extent_count,
            maximum_transfer_ms=maximum_transfer_ms,
            maximum_stall_ms=maximum_stall_ms,
            morphology_slack_ms=morphology_slack_ms,
            causal_slack_probability=timing_probability,
            timing_semantics=timing_semantics,
            evidence_kind=(
                "bounded_seed_scheduled_service"
                if candidate_state
                in PredictiveEligibilityIndex._SERVICE_STATES
                else "model_prediction"
            ),
            beneficiary_request_id=(
                beneficiary.request_id if beneficiary is not None else None
            ),
            beneficiary_invocation_id=(
                beneficiary.invocation_id
                if beneficiary is not None
                else candidate_invocation_id
            ),
            beneficiary_context_id=(
                beneficiary.context_id
                if beneficiary is not None
                else context_id
            ),
            beneficiary_context_epoch=(
                beneficiary.context_epoch
                if beneficiary is not None
                else graph.contexts[context_id].epoch
            ),
            beneficiary_startup_bytes=package.beneficiary_startup_bytes,
            beneficiary_growth_bytes=package.beneficiary_growth_bytes,
            predicted_block_time_ms=package.predicted_block_time_ms,
            predicted_deficit_bytes=package.predicted_deficit_bytes,
            causal_package_generation=package.causal_package_generation,
            target_reentry_context_epoch=(
                beneficiary.context_epoch
                if package.action
                in {
                    PredictiveActionKind.PREFETCH_GPU,
                    PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                    PredictiveActionKind.RECLAIM_AND_PREFETCH,
                }
                and beneficiary is not None
                else graph.contexts[context_id].epoch + 1
                if package.action
                in {
                    PredictiveActionKind.PREFETCH_GPU,
                    PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                    PredictiveActionKind.RECLAIM_AND_PREFETCH,
                }
                else None
            ),
            execution_order_request_ids=package.execution_order_request_ids,
            admit_request_ids=package.admit_request_ids,
            execution_request_evidence=execution_evidence,
            victim_context_id=(
                package.victim_context_ids[0]
                if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                else None
            ),
            victim_invocation_id=(
                next(
                    item.invocation_id
                    for item in eligibility.reclaim_ready_victims
                    if item.context_id == package.victim_context_ids[0]
                )
                if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                else None
            ),
            expected_victim_invocation_state=(
                next(
                    item.state
                    for item in eligibility.reclaim_ready_victims
                    if item.context_id == package.victim_context_ids[0]
                )
                if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                else None
            ),
            victim_context_epoch=(
                graph.contexts[package.victim_context_ids[0]].epoch
                if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                else None
            ),
            victim_generation_fingerprint=(
                next(
                    item.generation_fingerprint
                    for item in eligibility.reclaim_ready_victims
                    if item.context_id == package.victim_context_ids[0]
                )
                if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                else None
            ),
            victim_reclaim_bytes=(
                package.victim_reclaim_bytes
                if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                else 0
            ),
        )

    @staticmethod
    def _dependency_release_timing(
        belief: FrontierBeliefSnapshot,
        evaluation: PackageScenarioEvaluation,
        *,
        invocation_id: str,
        required_wait_ms: float,
        release_within: bool,
        conservative_quantile: float = 0.05,
    ) -> tuple[float, float] | None:
        """Return action-specific RCCG release probability and time bound."""

        weighted_offsets: list[tuple[float, float]] = []
        finite_evidence = False
        if belief.other_probability_mass > 0:
            # OTHER cannot support either action. Immediate release is
            # conservative for PREPARE; infinity is conservative for PREFETCH.
            weighted_offsets.append(
                (
                    math.inf if release_within else 0.0,
                    belief.other_probability_mass,
                )
            )
        releases = evaluation.dependency_release_offsets_by_scenario
        for scenario in belief.scenarios:
            offset = releases.get(scenario.scenario_id, {}).get(invocation_id)
            if offset is None or not math.isfinite(offset) or offset < 0:
                weighted_offsets.append((0.0, scenario.probability_mass))
                continue
            finite_evidence = True
            weighted_offsets.append((float(offset), scenario.probability_mass))
        if not finite_evidence or not weighted_offsets:
            return None
        timing_probability = sum(
            probability
            for offset, probability in weighted_offsets
            if (
                offset <= required_wait_ms
                if release_within
                else offset > required_wait_ms
            )
        )
        quantile = 1.0 - conservative_quantile if release_within else conservative_quantile
        threshold = max(0.0, min(1.0, quantile))
        cumulative = 0.0
        conservative = 0.0
        for offset, probability in sorted(weighted_offsets):
            cumulative += probability
            conservative = offset
            if cumulative + 1e-12 >= threshold:
                break
        if not math.isfinite(conservative):
            return None
        return (
            max(0.0, min(1.0, timing_probability)),
            max(0.0, conservative),
        )

    @staticmethod
    def _semantic_belief_key(
        graph: RuntimeCausalContextGraph,
        scope: Any,
        predictions: Mapping[str, LocalFrontierPrediction],
        *,
        model_version: str,
    ) -> str:
        invocation_ids = set(scope.invocation_ids)
        join_ids = {
            join_id
            for atom in scope.included_atoms
            for join_id in atom.join_ids
        }
        payload = (
            model_version,
            tuple(atom.atom_id for atom in scope.included_atoms),
            tuple(atom.atom_id for atom in scope.other_atoms),
            tuple(
                (
                    invocation_id,
                    FrontierScenarioComposer._local_particle_key(
                        graph,
                        invocation_id,
                        predictions[invocation_id],
                        seed=0,
                    )[2:],
                )
                for invocation_id in sorted(invocation_ids)
            ),
            tuple(
                (join_id, repr(graph.join_snapshot(join_id)))
                for join_id in sorted(join_ids)
            ),
            tuple(
                (
                    source,
                    target,
                    edge.count,
                    edge.last_ts_ms,
                )
                for (source, target), edge in sorted(
                    graph.communication_edges.items()
                )
                if source in invocation_ids and target in invocation_ids
            ),
        )
        return hashlib.blake2b(
            repr(payload).encode(),
            digest_size=16,
            person=b"bkv-semantic",
        ).hexdigest()

    def _evaluate_package(
        self,
        belief: Any,
        package: PredictiveActionPackage,
        physicalizer: "_OnlineCandidatePhysicalizer",
        target_invocation_id: str,
        cancel_check: Callable[[], bool] | None = None,
        baseline_timelines: Mapping[str, TimedScenario] | None = None,
        conservative_baseline_timelines: (
            Mapping[str, TimedScenario] | None
        ) = None,
    ) -> tuple[
        PackageScenarioEvaluation,
        Mapping[str, TimedScenario],
        Mapping[str, TimedScenario],
    ]:
        if (
            package.action == PredictiveActionKind.PREPARE_HOST
            and baseline_timelines is not None
        ):
            timelines = self._prepare_action_delta_timelines(
                package,
                baseline_timelines,
                physicalizer=physicalizer,
            )
        else:
            timelines = evaluate_belief_timelines(
                belief,
                package_id=package.package_id,
                physical_snapshot=physicalizer.policy_input.physical_kv,
                physicalizer=physicalizer,
                evaluator=self.timeline,
                cancel_check=cancel_check,
            )
        has_conservative_outcomes = any(
            item.conservative_outcomes for item in belief.scenarios
        )
        if (
            has_conservative_outcomes
            and package.action == PredictiveActionKind.PREPARE_HOST
            and conservative_baseline_timelines is not None
        ):
            conservative_timelines = self._prepare_action_delta_timelines(
                package,
                conservative_baseline_timelines,
                physicalizer=physicalizer,
            )
        elif has_conservative_outcomes:
            conservative_timelines = evaluate_belief_timelines(
                belief,
                package_id=package.package_id,
                physical_snapshot=physicalizer.policy_input.physical_kv,
                physicalizer=physicalizer,
                evaluator=self.timeline,
                cancel_check=cancel_check,
                conservative=True,
            )
        else:
            conservative_timelines = timelines
        projected_blocks = tuple(
            (
                timeline.projected_beneficiary_block_offset_ms,
                timeline.projected_beneficiary_deficit_bytes,
            )
            for timeline in conservative_timelines.values()
            if timeline.projected_beneficiary_block_offset_ms is not None
        )
        if package.beneficiary_request_id is not None and projected_blocks:
            package = replace(
                package,
                predicted_block_time_ms=min(
                    float(block_time)
                    for block_time, _deficit in projected_blocks
                    if block_time is not None
                ),
                predicted_deficit_bytes=max(
                    deficit for _block_time, deficit in projected_blocks
                ),
            )
        # HBM risk has its own chance constraint. Counting the same overflow as
        # generic future infeasibility would reject a candidate twice and make
        # the two gates impossible to attribute independently.
        timelines = self._without_hbm_from_future_feasibility(timelines)
        conservative_timelines = (
            self._without_hbm_from_future_feasibility(conservative_timelines)
            if has_conservative_outcomes
            else timelines
        )
        if package.action in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            restore_ms = physicalizer.package_transfer_duration_ms(package)
            other_unlock = 0.0
            other_pcie = restore_ms
        elif package.action == PredictiveActionKind.PREPARE_HOST:
            restore_ms = physicalizer.target_restore_duration_ms
            other_unlock = restore_ms
            other_pcie = physicalizer.package_transfer_duration_ms(package)
        else:
            restore_ms = physicalizer.target_restore_duration_ms
            other_unlock = restore_ms
            other_pcie = restore_ms
        evaluation = PackageScenarioEvaluation.from_timed_scenarios(
            package,
            timelines,
            unlock_invocation_ids=(target_invocation_id,),
            other_cost=ScenarioCost(
                action_unlock_delay_ms=other_unlock,
                workflow_service_lag_ms=0.0,
                residual_pcie_time_ms=other_pcie,
                deterministic_feasible=physicalizer.package_feasible(package),
                future_feasible=True,
                liveness_path_proven=True,
            ),
        )
        if conservative_timelines is not timelines:
            conservative_evaluation = PackageScenarioEvaluation.from_timed_scenarios(
                package,
                conservative_timelines,
                unlock_invocation_ids=(target_invocation_id,),
                other_cost=evaluation.other_cost,
            )
            evaluation = replace(
                evaluation,
                costs_by_scenario={
                    scenario_id: replace(
                        evaluation.costs_by_scenario[scenario_id],
                        future_hbm_peak_bytes=(
                            conservative_evaluation.costs_by_scenario[
                                scenario_id
                            ].future_hbm_peak_bytes
                        ),
                        future_hbm_overflow_bytes=(
                            conservative_evaluation.costs_by_scenario[
                                scenario_id
                            ].future_hbm_overflow_bytes
                        ),
                        future_hbm_feasible=(
                            conservative_evaluation.costs_by_scenario[
                                scenario_id
                            ].future_hbm_feasible
                        ),
                        deterministic_feasible=(
                            conservative_evaluation.costs_by_scenario[
                                scenario_id
                            ].deterministic_feasible
                        ),
                        future_feasible=(
                            conservative_evaluation.costs_by_scenario[
                                scenario_id
                            ].future_feasible
                        ),
                        liveness_path_proven=(
                            conservative_evaluation.costs_by_scenario[
                                scenario_id
                            ].liveness_path_proven
                        ),
                    )
                    for scenario_id in evaluation.costs_by_scenario
                },
            )
        if package.action == PredictiveActionKind.PREPARE_HOST:
            evaluation = self._apply_prepare_recourse(
                package,
                evaluation,
                timelines=timelines,
                conservative_timelines=conservative_timelines,
                physicalizer=physicalizer,
            )
        return evaluation, timelines, conservative_timelines

    @staticmethod
    def _prepare_action_delta_timelines(
        package: PredictiveActionPackage,
        baseline_timelines: Mapping[str, TimedScenario],
        *,
        physicalizer: "_OnlineCandidatePhysicalizer",
    ) -> dict[str, TimedScenario]:
        """Apply PREPARE's transfer-only delta to an unchanged GPU timeline."""

        if (
            package.action != PredictiveActionKind.PREPARE_HOST
            or len(package.victim_context_ids) != 1
        ):
            raise ValueError("PREPARE delta requires exactly one victim")
        context_id = package.victim_context_ids[0]
        duration_ms = physicalizer.prepare_shadow_transfer_evidence(
            package
        ).duration_ms
        transfer_id = f"{package.package_id}:d2h:{context_id}"
        deterministic_feasible = physicalizer.package_feasible(package)
        return {
            scenario_id: replace(
                timeline,
                package_id=package.package_id,
                transfer_completion_offsets_ms={
                    **timeline.transfer_completion_offsets_ms,
                    transfer_id: duration_ms,
                },
                pcie_busy_ms=timeline.pcie_busy_ms + duration_ms,
                deterministic_feasible=deterministic_feasible,
                liveness_path_proven=True,
            )
            for scenario_id, timeline in baseline_timelines.items()
        }

    @staticmethod
    def _without_hbm_from_future_feasibility(
        timelines: Mapping[str, TimedScenario],
    ) -> dict[str, TimedScenario]:
        return {
            scenario_id: replace(
                timeline,
                future_feasible=not any(
                    not reason.startswith("future_hbm_overflow:")
                    for reason in timeline.failure_reasons
                ),
            )
            for scenario_id, timeline in timelines.items()
        }

    def _apply_prepare_recourse(
        self,
        package: PredictiveActionPackage,
        evaluation: PackageScenarioEvaluation,
        *,
        timelines: Mapping[str, TimedScenario],
        conservative_timelines: Mapping[str, TimedScenario],
        physicalizer: "_OnlineCandidatePhysicalizer",
    ) -> PackageScenarioEvaluation:
        context_id = package.victim_context_ids[0]
        victim_invocation_id = physicalizer.invocation_for_context(context_id)
        shadow_bytes = physicalizer.prepare_shadow_bytes(package)
        reclaimable_bytes = physicalizer.prepare_reclaimable_bytes(package)
        cross_context_bytes = physicalizer.prepare_cross_context_bytes(package)
        transfer_evidence = physicalizer.prepare_shadow_transfer_evidence(package)
        projection = physicalizer.prepare_projection(package)
        # Compatibility diagnostics retain the historical field names, but all
        # online decisions now consume the same extent-aware transfer evidence.
        byte_only_transfer_ms = transfer_evidence.duration_ms
        interference_evidence = physicalizer.prepare_interference_evidence(
            package,
            transfer_evidence,
        )
        proactive_interference_ms = interference_evidence.interference_ms
        transfer_id = f"{package.package_id}:d2h:{context_id}"
        updated: dict[str, ScenarioCost] = {}
        diagnostics: dict[str, PrepareRecourseDiagnostic] = {}
        for scenario_id, cost in evaluation.costs_by_scenario.items():
            timeline = timelines[scenario_id]
            conservative = conservative_timelines[scenario_id]
            shadow_completion = timeline.transfer_completion_offsets_ms.get(
                transfer_id
            )
            pressure = timeline.projected_beneficiary_block_offset_ms
            reentry = timeline.dependency_release_offsets_ms.get(
                victim_invocation_id
            )
            if reentry is None:
                reentry = next(
                    (
                        item.completion_offset_ms
                        for item in timeline.invocation_outcomes
                        if item.invocation_id == victim_invocation_id
                    ),
                    None,
                )
            recourse_credit_ms = 0.0
            host_residency_ms = 0.0
            pressure_deficit_bytes = (
                timeline.projected_beneficiary_deficit_bytes
            )
            baseline_reactive_d2h_ms = transfer_evidence.duration_ms
            failure_reason = "eligible"
            if shadow_completion is not None and reentry is not None:
                host_residency_ms = max(0.0, reentry - shadow_completion)
            morphology_deadline, morphology_slack = (
                _transfer_deadline_and_slack(
                    pressure,
                    reentry,
                    transfer_ms=transfer_evidence.duration_ms,
                    guard_ms=self.config.transfer_commit_guard_ms,
                )
            )
            if not transfer_evidence.shape_supported:
                failure_reason = "shape_unsupported"
            elif shadow_completion is None:
                failure_reason = "shadow_completion_unavailable"
            elif pressure is None:
                failure_reason = "projected_beneficiary_hbm_block_unavailable"
            elif reentry is None:
                failure_reason = "parent_reentry_unavailable"
            elif shadow_completion > pressure:
                failure_reason = "shadow_completes_after_pressure"
            elif pressure >= reentry:
                failure_reason = "pressure_not_before_parent_reentry"
            elif morphology_slack is None or morphology_slack <= 0:
                failure_reason = "morphology_window_miss"
            elif reclaimable_bytes <= 0:
                failure_reason = "insufficient_exclusive_reclaim"
            elif baseline_reactive_d2h_ms is None:
                failure_reason = "reactive_d2h_unavailable"
            else:
                recourse_credit_ms = baseline_reactive_d2h_ms
                if reclaimable_bytes < pressure_deficit_bytes:
                    failure_reason = "eligible_partial"

            conservative_shadow = (
                conservative.transfer_completion_offsets_ms.get(transfer_id)
            )
            conservative_pressure = (
                conservative.projected_beneficiary_block_offset_ms
            )
            conservative_deficit = (
                conservative.projected_beneficiary_deficit_bytes
            )
            conservative_reentry = conservative.dependency_release_offsets_ms.get(
                victim_invocation_id
            )
            if conservative_reentry is None:
                conservative_reentry = next(
                    (
                        item.completion_offset_ms
                        for item in conservative.invocation_outcomes
                        if item.invocation_id == victim_invocation_id
                    ),
                    None,
                )
            conservative_deadline, conservative_slack = (
                _transfer_deadline_and_slack(
                    conservative_pressure,
                    conservative_reentry,
                    transfer_ms=transfer_evidence.duration_ms,
                    guard_ms=self.config.transfer_commit_guard_ms,
                )
            )
            recourse_feasible = (
                transfer_evidence.shape_supported
                and conservative_shadow is not None
                and conservative_slack is not None
                and conservative_slack > 0
                and conservative_pressure is not None
                and conservative_reentry is not None
                and conservative_shadow <= conservative_pressure < conservative_reentry
                and reclaimable_bytes
                >= conservative_deficit
            )
            overflow = cost.future_hbm_overflow_bytes
            if recourse_feasible:
                overflow = max(0, overflow - reclaimable_bytes)
            updated[scenario_id] = replace(
                cost,
                residual_host_time_byte_ms=(
                    shadow_bytes * host_residency_ms
                ),
                terminal_debt_ms=(
                    cost.terminal_debt_ms + proactive_interference_ms
                ),
                recourse_credit_ms=recourse_credit_ms,
                future_hbm_overflow_bytes=overflow,
                future_hbm_feasible=overflow == 0,
            )
            diagnostics[scenario_id] = PrepareRecourseDiagnostic(
                scenario_id=scenario_id,
                probability_mass=0.0,
                shadow_completion_ms=shadow_completion,
                first_pressure_ms=pressure,
                pressure_deficit_bytes=pressure_deficit_bytes,
                parent_reentry_ms=reentry,
                exclusive_reclaimable_bytes=reclaimable_bytes,
                full_closure_copy_bytes=shadow_bytes,
                cross_context_copy_bytes=cross_context_bytes,
                baseline_reactive_d2h_ms=baseline_reactive_d2h_ms,
                proactive_interference_ms=proactive_interference_ms,
                transfer_duration_source=transfer_evidence.source,
                transfer_service_epoch=transfer_evidence.service_epoch,
                interference_source=interference_evidence.source,
                interference_service_epoch=interference_evidence.service_epoch,
                interference_to_transfer_ratio=(
                    interference_evidence.interference_to_transfer_ratio
                ),
                transfer_nearest_bucket_distance=(
                    transfer_evidence.nearest_bucket_distance
                ),
                transfer_sample_count=transfer_evidence.sample_count,
                transfer_size_coverage_bytes=(
                    transfer_evidence.size_coverage_bytes
                ),
                transfer_extent_count_coverage=(
                    transfer_evidence.extent_count_coverage
                ),
                transfer_shape_bucket_distance=(
                    transfer_evidence.shape_bucket_distance
                ),
                transfer_shape_supported=transfer_evidence.shape_supported,
                predicted_extent_count=(
                    projection.extent_count if projection is not None else 0
                ),
                shape_fingerprint=(
                    projection.shape_fingerprint
                    if projection is not None
                    else "unavailable"
                ),
                byte_only_transfer_ms=byte_only_transfer_ms,
                shape_aware_transfer_p90_ms=transfer_evidence.duration_ms,
                shape_aware_stall_p90_ms=proactive_interference_ms,
                morphology_deadline_ms=morphology_deadline,
                morphology_slack_ms=morphology_slack,
                conservative_morphology_deadline_ms=conservative_deadline,
                conservative_morphology_slack_ms=conservative_slack,
                morphology_debt_ms=transfer_evidence.duration_ms,
                morphology_penalty_ms=(
                    transfer_evidence.duration_ms - byte_only_transfer_ms
                ),
                reactive_victim_model="beneficiary_bound_same_closure",
                recourse_credit_ms=recourse_credit_ms,
                recourse_failure_reason=failure_reason,
            )
        return replace(
            evaluation,
            costs_by_scenario=updated,
            recourse_diagnostics_by_scenario=diagnostics,
        )


    @staticmethod
    def _scheduling_candidates(
        policy_input: PolicyInput,
        source_plan: JointPlan,
        *,
        limit: int,
    ) -> tuple[RunnableInvocation, ...]:
        requests = {
            item.request_id: item for item in policy_input.runnable_frontier
        }
        admissions = {
            item.request_id: item
            for item in getattr(source_plan, "admissions", ())
        }
        ordered_ids = tuple(
            dict.fromkeys(
                (
                    *source_plan.candidate_order_request_ids,
                    *source_plan.execution.ordered_request_ids,
                    *admissions,
                )
            )
        )
        candidates = tuple(
            requests[request_id]
            for request_id in ordered_ids
            if request_id in requests
            and request_id in admissions
            and requests[request_id].causal_class.startswith(
                ("engine_waiting:", "engine_running:")
            )
            and admissions[request_id].action.value
            in {"admit", "defer"}
        )
        return candidates[: max(1, limit)]


    @staticmethod
    def _predictive_execution_order(
        candidates: tuple[RunnableInvocation, ...],
        predictions: Mapping[str, LocalFrontierPrediction],
    ) -> tuple[str, ...]:
        source_rank = {
            request.request_id: index
            for index, request in enumerate(candidates)
        }

        def priority(
            request: RunnableInvocation,
        ) -> tuple[float, float, float, int]:
            prediction = predictions.get(request.invocation_id)
            if prediction is None:
                return (
                    1.0,
                    float("inf"),
                    float("inf"),
                    source_rank[request.request_id],
                )
            running = request.causal_class.startswith("engine_running:")
            demand_head = (
                "remaining_decode_demand" if running else "next_output_demand"
            )
            demand_distribution = (
                prediction.remaining_decode_tokens
                if running
                else prediction.next_output_tokens
            )
            if (
                prediction.support_for(demand_head) == "unavailable"
                or not demand_distribution.values
            ):
                return (
                    1.0,
                    float("inf"),
                    float("inf"),
                    source_rank[request.request_id],
                )
            output_tokens = demand_distribution.quantile(0.5)
            demand_tokens = max(
                1.0,
                float(request.remaining_prefill_tokens)
                + output_tokens,
            )
            hbm_demand = float(
                (request.admission_startup_bytes or request.startup_bytes)
                + (request.admission_growth_bytes or 0)
            )
            return (
                0.0,
                demand_tokens,
                hbm_demand,
                source_rank[request.request_id],
            )

        return tuple(
            item.request_id for item in sorted(candidates, key=priority)
        )


    @staticmethod
    def _reactive_baseline_package(
        baseline: PredictiveActionPackage,
        candidate: PredictiveActionPackage,
        source_plan: JointPlan,
    ) -> PredictiveActionPackage:
        if candidate.action == PredictiveActionKind.SCHEDULE:
            return baseline
        source_order = tuple(
            getattr(
                getattr(source_plan, "execution", None),
                "ordered_request_ids",
                (),
            )
        )
        beneficiary_key = candidate.beneficiary_request_id or "none"
        return replace(
            baseline,
            package_id=(
                f"{baseline.package_id}:reactive:"
                f"{candidate.action.value}:{beneficiary_key}"
            ),
            beneficiary_request_id=candidate.beneficiary_request_id,
            beneficiary_startup_bytes=candidate.beneficiary_startup_bytes,
            beneficiary_growth_bytes=candidate.beneficiary_growth_bytes,
            predicted_block_time_ms=candidate.predicted_block_time_ms,
            predicted_deficit_bytes=candidate.predicted_deficit_bytes,
            causal_package_generation=candidate.causal_package_generation,
            execution_order_request_ids=source_order,
            beneficiary_invocation_id=candidate.beneficiary_invocation_id,
            beneficiary_context_id=candidate.beneficiary_context_id,
            beneficiary_context_epoch=candidate.beneficiary_context_epoch,
        )

    @staticmethod
    def _move_request_first(
        request_id: str, request_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        return (request_id, *(item for item in request_ids if item != request_id))

    def _candidate_packages(
        self,
        policy_input: PolicyInput,
        source_plan: JointPlan,
        eligibility: PredictiveEligibility,
        *,
        allowed_invocation_ids: frozenset[str] | None = None,
        projected_requirement: ProjectedReclaimRequirement | None = None,
        predictive_execution_order: tuple[str, ...] = (),
        scheduling_beneficiary: RunnableInvocation | None = None,
    ) -> tuple[PredictiveActionPackage, ...]:
        source_plan_id = (
            projected_requirement.source_joint_plan_id
            if projected_requirement is not None
            else source_plan.plan_id
        )
        admissions = {
            item.request_id: item
            for item in getattr(source_plan, "admissions", ())
        }

        def admission_for(request_id: str) -> tuple[str, ...]:
            admission = admissions.get(request_id)
            return (
                (request_id,)
                if admission is not None and admission.action.value == "defer"
                else ()
            )

        packages = [
            PredictiveActionPackage(
                package_id=f"{source_plan_id}:a0",
                action=PredictiveActionKind.OBSERVED_BASELINE,
                source_joint_plan_id=source_plan_id,
            )
        ]
        if scheduling_beneficiary is not None and predictive_execution_order:
            request = scheduling_beneficiary
            packages.append(
                PredictiveActionPackage(
                    package_id=f"{source_plan_id}:schedule:{request.request_id}",
                    action=PredictiveActionKind.SCHEDULE,
                    context_ids=(request.context_id,),
                    source_joint_plan_id=source_plan_id,
                    beneficiary_request_id=request.request_id,
                    beneficiary_startup_bytes=(
                        request.admission_startup_bytes or request.startup_bytes
                    ),
                    beneficiary_growth_bytes=(
                        request.admission_growth_bytes or 0
                    ),
                    causal_package_generation=(
                        f"{request.request_id}:{request.context_id}:"
                        f"c{request.context_epoch}:"
                        f"{request.admission_startup_bytes or request.startup_bytes}:"
                        f"{request.admission_growth_bytes or 0}"
                    ),
                    execution_order_request_ids=predictive_execution_order,
                    admit_request_ids=admission_for(request.request_id),
                    beneficiary_invocation_id=request.invocation_id,
                    beneficiary_context_id=request.context_id,
                    beneficiary_context_epoch=request.context_epoch,
                )
            )

        if projected_requirement is not None:
            source_execution_order = tuple(
                getattr(
                    getattr(source_plan, "execution", None),
                    "ordered_request_ids",
                    (),
                )
            )
            active_request_ids = frozenset(source_execution_order)
            prepare_order = tuple(
                request_id
                for request_id in predictive_execution_order
                if request_id in active_request_ids
            ) or source_execution_order
            victims = [
                item
                for item in eligibility.prepare_host_victims
                if (
                    item.invocation_id
                    != projected_requirement.beneficiary_invocation_id
                    and item.context_id
                    != projected_requirement.beneficiary_context_id
                    and (
                        allowed_invocation_ids is None
                        or item.invocation_id in allowed_invocation_ids
                    )
                )
            ][:2]
            packages.extend(
                PredictiveActionPackage(
                    package_id=f"{source_plan_id}:prepare:{victim.context_id}",
                    action=PredictiveActionKind.PREPARE_HOST,
                    context_ids=(victim.context_id,),
                    victim_context_ids=(victim.context_id,),
                    source_joint_plan_id=source_plan_id,
                    beneficiary_request_id=(
                        projected_requirement.beneficiary_request_id
                    ),
                    beneficiary_startup_bytes=(
                        projected_requirement.required_startup_bytes
                    ),
                    beneficiary_growth_bytes=(
                        projected_requirement.required_growth_bytes
                    ),
                    predicted_block_time_ms=(
                        projected_requirement.predicted_block_time_ms
                    ),
                    predicted_deficit_bytes=(
                        projected_requirement.predicted_deficit_bytes
                    ),
                    victim_reclaim_bytes=victim.reclaimable_bytes,
                    causal_package_generation=(
                        projected_requirement.causal_package_generation
                    ),
                    execution_order_request_ids=prepare_order,
                    admit_request_ids=(),
                    beneficiary_invocation_id=(
                        projected_requirement.beneficiary_invocation_id
                    ),
                    beneficiary_context_id=(
                        projected_requirement.beneficiary_context_id
                    ),
                    beneficiary_context_epoch=(
                        projected_requirement.beneficiary_context_epoch
                    ),
                )
                for victim in victims
            )

        request_by_invocation = {
            item.invocation_id: item for item in policy_input.runnable_frontier
        }
        action_local_overlay = _action_local_physical_overlay(policy_input)
        overlay_metadata = policy_input.optional_metadata.get(
            "beliefkv_action_local_physical_overlay"
        )
        overlay_payload = (
            overlay_metadata.value
            if overlay_metadata is not None
            and isinstance(overlay_metadata.value, Mapping)
            else {}
        )
        overlay_device_available = overlay_payload.get("device_available_bytes")
        prefetch_byte_budget = (
            max(0, int(overlay_device_available))
            if isinstance(overlay_device_available, int)
            and not isinstance(overlay_device_available, bool)
            else max(
                0,
                policy_input.resources.hbm_capacity_bytes
                - policy_input.resources.policy_hbm_used_bytes
                - policy_input.resources.hbm_reserved_bytes,
            )
        )
        for target in eligibility.prefetch_targets[:4]:
            request = request_by_invocation.get(target.invocation_id)
            reclaim_victim = next(
                (
                    item
                    for item in eligibility.reclaim_ready_victims
                    if item.context_id != target.context_id
                    and item.invocation_id != target.invocation_id
                    and (
                        allowed_invocation_ids is None
                        or item.invocation_id in allowed_invocation_ids
                    )
                ),
                None,
            )
            target_overlay = action_local_overlay.get(target.context_id, {})
            victim_overlay = (
                action_local_overlay.get(reclaim_victim.context_id, {})
                if reclaim_victim is not None
                else {}
            )
            physical_reclaim_pair = bool(
                int(target_overlay.get("h2d_copy_bytes", 0)) > 0
                and victim_overlay.get("evidence_kind")
                == "commit_ready_summary"
                and int(
                    victim_overlay.get("exclusive_reclaimable_bytes", 0)
                ) > 0
            )
            funded_budget = prefetch_byte_budget
            if physical_reclaim_pair and reclaim_victim is not None:
                funded_budget += reclaim_victim.reclaimable_bytes
                action = PredictiveActionKind.RECLAIM_AND_PREFETCH
            elif (
                target.missing_gpu_bytes > prefetch_byte_budget
                and reclaim_victim is not None
            ):
                funded_budget += reclaim_victim.reclaimable_bytes
                action = PredictiveActionKind.RECLAIM_AND_PREFETCH
            elif prefetch_byte_budget > 0:
                action = (
                    PredictiveActionKind.PARTIAL_PREFETCH_GPU
                    if target.missing_gpu_bytes > prefetch_byte_budget
                    else PredictiveActionKind.PREFETCH_GPU
                )
            else:
                continue
            bounded_prefetch = action in {
                PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                PredictiveActionKind.RECLAIM_AND_PREFETCH,
            }
            if request is None:
                prefetch_order = ()
            else:
                source_execution_order = tuple(
                    getattr(
                        getattr(source_plan, "execution", None),
                        "ordered_request_ids",
                        (),
                    )
                )
                active_request_ids = frozenset(source_execution_order)
                prefetch_order = tuple(
                    request_id
                    for request_id in predictive_execution_order
                    if request_id in active_request_ids
                ) or source_execution_order
            packages.append(
                PredictiveActionPackage(
                    package_id=(
                        f"{source_plan_id}:reclaim-prefetch:"
                        f"{reclaim_victim.context_id}:{target.context_id}"
                        if action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                        else f"{source_plan_id}:partial-prefetch:{target.context_id}"
                        if bounded_prefetch
                        else f"{source_plan_id}:prefetch:{target.context_id}"
                    ),
                    action=action,
                    context_ids=(
                        (target.context_id, reclaim_victim.context_id)
                        if action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                        and reclaim_victim is not None
                        else (target.context_id,)
                    ),
                    target_context_id=target.context_id,
                    victim_context_ids=(
                        (reclaim_victim.context_id,)
                        if action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                        and reclaim_victim is not None
                        else ()
                    ),
                    source_joint_plan_id=source_plan_id,
                    byte_budget=funded_budget if bounded_prefetch else None,
                    beneficiary_request_id=(
                        request.request_id if request is not None else None
                    ),
                    causal_package_generation=(
                        f"{request.request_id}:{request.context_id}:"
                        f"c{request.context_epoch}:"
                        f"{request.admission_startup_bytes or request.startup_bytes}:"
                        f"{request.admission_growth_bytes or 0}"
                        if request is not None
                        else None
                    ),
                    execution_order_request_ids=prefetch_order,
                    admit_request_ids=(),
                    beneficiary_invocation_id=(
                        request.invocation_id if request is not None else None
                    ),
                    beneficiary_context_id=(
                        request.context_id if request is not None else None
                    ),
                    beneficiary_context_epoch=(
                        request.context_epoch if request is not None else None
                    ),
                    predicted_deficit_bytes=max(
                        0, target.missing_gpu_bytes - prefetch_byte_budget
                    ),
                    victim_reclaim_bytes=(
                        reclaim_victim.reclaimable_bytes
                        if reclaim_victim is not None
                        and action == PredictiveActionKind.RECLAIM_AND_PREFETCH
                        else 0
                    ),
                )
            )
        return tuple(packages)

    @staticmethod
    def _projected_reclaim_requirement(
        policy_input: PolicyInput,
        source_plan: JointPlan,
    ) -> ProjectedReclaimRequirement | None:
        state = policy_input.runtime_graph.state
        control = state.get("control", {})
        reclaim_state = (
            control.get("reclaim_requirements", {})
            if isinstance(control, Mapping)
            else {}
        )
        observed_ids = {
            str(item.get("beneficiary_request_id"))
            for item in (
                reclaim_state.get("requirements", ())
                if isinstance(reclaim_state, Mapping)
                else ()
            )
            if isinstance(item, Mapping) and item.get("beneficiary_request_id")
        }
        admissions = {
            item.request_id: item
            for item in getattr(source_plan, "admissions", ())
        }
        requests = {
            item.request_id: item for item in policy_input.runnable_frontier
        }
        hint_metadata = policy_input.optional_metadata.get(
            "beliefkv_observed_seed_beneficiary"
        )
        hint = (
            hint_metadata.value
            if hint_metadata is not None
            and isinstance(hint_metadata.value, Mapping)
            else None
        )
        overlay_metadata = policy_input.optional_metadata.get(
            "beliefkv_action_local_physical_overlay"
        )
        overlay_is_authoritative = overlay_metadata is not None
        overlay_batch = (
            overlay_metadata.value
            if overlay_metadata is not None
            and isinstance(overlay_metadata.value, Mapping)
            else {}
        )
        opportunity = overlay_batch.get("opportunity", {})

        def opportunity_prediction(request_id: str) -> tuple[float | None, int]:
            if (
                not isinstance(opportunity, Mapping)
                or str(opportunity.get("beneficiary_request_id") or "") != request_id
            ):
                return None, 0
            block_time = opportunity.get("predicted_block_time_ms")
            deficit = opportunity.get("predicted_deficit_bytes")
            if (
                not isinstance(deficit, int)
                or isinstance(deficit, bool)
                or deficit <= 0
            ):
                return None, 0
            if block_time is None:
                return None, deficit
            if (
                not isinstance(block_time, (int, float))
                or isinstance(block_time, bool)
                or not math.isfinite(float(block_time))
                or float(block_time) < 0
            ):
                return None, 0
            return float(block_time), deficit

        if hint is not None:
            request_id = str(hint.get("request_id") or "")
            request = requests.get(request_id)
            if (
                request is not None
                and request_id not in observed_ids
                and request.causal_class.startswith("engine_waiting:")
                and request.admission_startup_bytes is not None
                and request.admission_growth_bytes is not None
                and request.invocation_id == hint.get("invocation_id")
                and request.context_id == hint.get("context_id")
                and request.context_epoch == hint.get("context_epoch")
                and request.admission_startup_bytes == hint.get("startup_bytes")
                and request.admission_growth_bytes == hint.get("growth_bytes")
                and request.admission_startup_bytes
                + request.admission_growth_bytes
                > 0
            ):
                bundles = tuple(
                    bundle
                    for bundle in policy_input.physical_kv.bundles
                    if request.context_id in bundle.owner_context_ids
                )
                if not any(
                    bundle.cpu_bytes > bundle.gpu_bytes for bundle in bundles
                ):
                    block_time, deficit_bytes = opportunity_prediction(
                        request.request_id
                    )
                    if overlay_is_authoritative and deficit_bytes <= 0:
                        return None
                    return ProjectedReclaimRequirement(
                        beneficiary_request_id=request.request_id,
                        beneficiary_invocation_id=request.invocation_id,
                        beneficiary_context_id=request.context_id,
                        beneficiary_context_epoch=request.context_epoch,
                        required_startup_bytes=request.admission_startup_bytes,
                        required_growth_bytes=request.admission_growth_bytes,
                        predicted_block_time_ms=block_time,
                        predicted_deficit_bytes=deficit_bytes,
                        source_joint_plan_id=source_plan.plan_id,
                        causal_package_generation=(
                            f"{request.request_id}:{request.context_id}:"
                            f"c{request.context_epoch}:"
                            f"{request.admission_startup_bytes}:"
                            f"{request.admission_growth_bytes}"
                        ),
                    )
        order = (
            (source_plan.projected_beneficiary_request_id,)
            if source_plan.projected_beneficiary_request_id is not None
            else source_plan.candidate_order_request_ids or tuple(admissions)
        )
        for request_id in order:
            admission = admissions.get(request_id)
            request = requests.get(request_id)
            if (
                admission is None
                or admission.action.value != "defer"
                or request is None
                or request_id in observed_ids
                or not request.causal_class.startswith("engine_waiting:")
                or request.admission_startup_bytes is None
                or request.admission_growth_bytes is None
            ):
                continue
            demand = (
                request.admission_startup_bytes
                + request.admission_growth_bytes
            )
            if demand <= 0:
                continue
            bundles = tuple(
                bundle
                for bundle in policy_input.physical_kv.bundles
                if request.context_id in bundle.owner_context_ids
            )
            if any(bundle.cpu_bytes > bundle.gpu_bytes for bundle in bundles):
                continue
            block_time, deficit_bytes = opportunity_prediction(request.request_id)
            if overlay_is_authoritative and deficit_bytes <= 0:
                return None
            return ProjectedReclaimRequirement(
                beneficiary_request_id=request.request_id,
                beneficiary_invocation_id=request.invocation_id,
                beneficiary_context_id=request.context_id,
                beneficiary_context_epoch=request.context_epoch,
                required_startup_bytes=request.admission_startup_bytes,
                required_growth_bytes=request.admission_growth_bytes,
                predicted_block_time_ms=block_time,
                predicted_deficit_bytes=deficit_bytes,
                source_joint_plan_id=source_plan.plan_id,
                causal_package_generation=(
                    f"{request.request_id}:{request.context_id}:"
                    f"c{request.context_epoch}:"
                    f"{request.admission_startup_bytes}:"
                    f"{request.admission_growth_bytes}"
                ),
            )
        return None

    @staticmethod
    def _summary_payload(
        decision: ScenarioRiskDecision,
        *,
        actions: Mapping[str, str],
        packages: Mapping[str, PredictiveActionPackage],
        support_by_package: Mapping[str, tuple[tuple[str, str], ...]],
        projection_by_package: Mapping[str, str],
        timing_by_package: Mapping[str, _ActionTimingEvidence],
        certificates: Mapping[str, Mapping[str, object]],
    ) -> tuple[Mapping[str, object], ...]:
        return tuple(
            {
                "package_id": item.package_id,
                "action": actions.get(item.package_id, "unknown"),
                "beneficiary_request_id": (
                    packages[item.package_id].beneficiary_request_id
                    if item.package_id in packages
                    else None
                ),
                "execution_order_request_ids": (
                    list(packages[item.package_id].execution_order_request_ids)
                    if item.package_id in packages
                    else []
                ),
                "admit_request_ids": (
                    list(packages[item.package_id].admit_request_ids)
                    if item.package_id in packages
                    else []
                ),
                "beneficiary_startup_bytes": (
                    packages[item.package_id].beneficiary_startup_bytes
                    if item.package_id in packages
                    else 0
                ),
                "beneficiary_growth_bytes": (
                    packages[item.package_id].beneficiary_growth_bytes
                    if item.package_id in packages
                    else 0
                ),
                "predicted_block_time_ms": (
                    packages[item.package_id].predicted_block_time_ms
                    if item.package_id in packages
                    else None
                ),
                "predicted_deficit_bytes": (
                    packages[item.package_id].predicted_deficit_bytes
                    if item.package_id in packages
                    else 0
                ),
                "scenario_projection": projection_by_package.get(
                    item.package_id, "unknown"
                ),
                "prediction_head_support": [
                    list(value)
                    for value in support_by_package.get(item.package_id, ())
                ],
                "timing_semantics": (
                    timing_by_package[item.package_id].semantics
                    if item.package_id in timing_by_package
                    else None
                ),
                "required_wait_ms": (
                    timing_by_package[item.package_id].required_wait_ms
                    if item.package_id in timing_by_package
                    else None
                ),
                "causal_slack_probability": (
                    timing_by_package[item.package_id].causal_slack_probability
                    if item.package_id in timing_by_package
                    else None
                ),
                "action_timing_brier_skill": (
                    timing_by_package[
                        item.package_id
                    ].calibration_brier_skill
                    if item.package_id in timing_by_package
                    else None
                ),
                "action_timing_balanced_accuracy": (
                    timing_by_package[
                        item.package_id
                    ].calibration_balanced_accuracy
                    if item.package_id in timing_by_package
                    else None
                ),
                "action_timing_decision_threshold": (
                    timing_by_package[item.package_id].decision_threshold
                    if item.package_id in timing_by_package
                    else None
                ),
                "action_timing_raw_decision_threshold": (
                    timing_by_package[item.package_id].raw_decision_threshold
                    if item.package_id in timing_by_package
                    else None
                ),
                "action_timing_precision_at_threshold": (
                    timing_by_package[
                        item.package_id
                    ].precision_at_decision_threshold
                    if item.package_id in timing_by_package
                    else None
                ),
                "action_timing_recall_at_threshold": (
                    timing_by_package[
                        item.package_id
                    ].recall_at_decision_threshold
                    if item.package_id in timing_by_package
                    else None
                ),
                "conservative_remaining_window_ms": (
                    timing_by_package[
                        item.package_id
                    ].conservative_remaining_window_ms
                    if item.package_id in timing_by_package
                    else None
                ),
                "expected_benefit_ms": item.expected_benefit_ms,
                "expected_recourse_credit_ms": (
                    item.expected_recourse_credit_ms
                ),
                "prepare_recourse_scenarios": [
                    diagnostic.to_dict()
                    for diagnostic in item.recourse_diagnostics
                ],
                "prepare_recourse_failure_counts": dict(
                    sorted(
                        Counter(
                            diagnostic.recourse_failure_reason
                            for diagnostic in item.recourse_diagnostics
                        ).items()
                    )
                ),
                "morphology_shape_supported": (
                    all(
                        diagnostic.transfer_shape_supported
                        for diagnostic in item.recourse_diagnostics
                    )
                    if item.recourse_diagnostics
                    else None
                ),
                "morphology_predicted_extent_count": (
                    max(
                        diagnostic.predicted_extent_count
                        for diagnostic in item.recourse_diagnostics
                    )
                    if item.recourse_diagnostics
                    else None
                ),
                "morphology_shape_fingerprint": (
                    item.recourse_diagnostics[0].shape_fingerprint
                    if item.recourse_diagnostics
                    else None
                ),
                "morphology_byte_only_transfer_ms": (
                    max(
                        diagnostic.byte_only_transfer_ms
                        for diagnostic in item.recourse_diagnostics
                    )
                    if item.recourse_diagnostics
                    else None
                ),
                "morphology_shape_aware_transfer_p90_ms": (
                    max(
                        diagnostic.shape_aware_transfer_p90_ms
                        for diagnostic in item.recourse_diagnostics
                    )
                    if item.recourse_diagnostics
                    else None
                ),
                "morphology_debt_ms": (
                    max(
                        diagnostic.morphology_debt_ms
                        for diagnostic in item.recourse_diagnostics
                    )
                    if item.recourse_diagnostics
                    else None
                ),
                "morphology_penalty_ms": (
                    max(
                        diagnostic.morphology_penalty_ms
                        for diagnostic in item.recourse_diagnostics
                    )
                    if item.recourse_diagnostics
                    else None
                ),
                "morphology_min_positive_slack_ms": (
                    min(
                        (
                            diagnostic.morphology_slack_ms
                            for diagnostic in item.recourse_diagnostics
                            if diagnostic.morphology_slack_ms is not None
                            and diagnostic.morphology_slack_ms > 0
                        ),
                        default=None,
                    )
                ),
                "morphology_window_miss_count": sum(
                    diagnostic.recourse_failure_reason
                    == "morphology_window_miss"
                    for diagnostic in item.recourse_diagnostics
                ),
                "cvar_regret_ms": item.cvar_regret_ms,
                "future_feasibility_probability": (
                    item.future_feasibility_probability
                ),
                "future_hbm_feasibility_probability": (
                    item.future_hbm_feasibility_probability
                ),
                "worst_future_hbm_peak_bytes": item.worst_future_hbm_peak_bytes,
                "worst_future_hbm_overflow_bytes": (
                    item.worst_future_hbm_overflow_bytes
                ),
                "eligible": item.eligible,
                "reasons": list(item.reasons),
                "action_certificate": certificates.get(item.package_id),
            }
            for item in decision.summaries
        )

    def _skipped(
        self,
        policy_input: PolicyInput,
        source_plan: JointPlan,
        started_ns: int,
        *,
        model_version: str | None,
        reasons: tuple[str, ...],
        belief_id: str | None = None,
        support_level: str = "unavailable",
        calibration_coverage: float = 0.0,
        other_probability_mass: float = 1.0,
        ood_reasons: tuple[str, ...] = (),
        scenario_count: int = 0,
    ) -> PredictiveRiskShadowResult:
        return PredictiveRiskShadowResult(
            status="skipped",
            source_joint_plan_id=source_plan.plan_id,
            source_snapshot_id=policy_input.snapshot_id,
            belief_id=belief_id,
            model_version=model_version,
            support_level=support_level,
            calibration_coverage=calibration_coverage,
            other_probability_mass=other_probability_mass,
            target_invocation_id=None,
            target_context_id=None,
            selected_package_id=f"{source_plan.plan_id}:a0",
            selected_action=PredictiveActionKind.OBSERVED_BASELINE.value,
            candidate_count=0,
            candidate_summaries=(),
            ood_reasons=ood_reasons,
            blocked_reasons=tuple(sorted(set(reasons))),
            planning_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
            scenario_count=scenario_count,
            decision_authority=(
                "semantic_joint_overlay"
                if self.config.online_overlay_enabled
                else "read_only_shadow"
            ),
        )


class _OnlineCandidatePhysicalizer:
    def __init__(
        self,
        policy_input: PolicyInput,
        graph: RuntimeCausalContextGraph,
        source_plan: JointPlan,
        *,
        target_invocation_id: str,
        target_context_id: str,
        belief_scope_invocation_ids: tuple[str, ...],
        packages: Mapping[str, PredictiveActionPackage],
        kv_bytes_per_token: int,
    ) -> None:
        self.policy_input = policy_input
        self.graph = graph
        self.source_plan = source_plan
        self.target_invocation_id = target_invocation_id
        self.target_context_id = target_context_id
        self.belief_scope_invocation_ids = belief_scope_invocation_ids
        self.packages = dict(packages)
        self.kv_bytes_per_token = kv_bytes_per_token
        self._context_bytes = self._context_byte_summary(policy_input)
        self._context_bundles = {
            context_id: tuple(
                bundle
                for bundle in policy_input.physical_kv.bundles
                if context_id in bundle.owner_context_ids
            )
            for context_id in {
                context_id
                for bundle in policy_input.physical_kv.bundles
                for context_id in bundle.owner_context_ids
            }
        }
        self._prepare_projections: dict[str, _PrepareShadowProjection | None] = {}
        self._prefetch_projections: dict[
            tuple[str, int], _PrefetchPrefixProjection | None
        ] = {}
        self._extent_index = {
            bundle.extent_ids[0]: bundle
            for bundle in policy_input.physical_kv.bundles
            if len(bundle.extent_ids) == 1
        }
        self._overlay_by_context = _action_local_physical_overlay(policy_input)

    def register_package(self, package: PredictiveActionPackage) -> None:
        self.packages[package.package_id] = package

    @property
    def target_restore_duration_ms(self) -> float:
        restore_bytes = self._context_bytes.get(self.target_context_id, (0, 0, 0))[1]
        return self._transfer_duration_ms(
            restore_bytes,
            direction="h2d",
            context_id=self.target_context_id,
            extent_count=self._target_restore_extent_count(self.target_context_id),
        )

    def package_transfer_duration_ms(
        self, package: PredictiveActionPackage
    ) -> float:
        target_bytes = self._target_restore_bytes(package)
        target_extent_count = self._target_restore_extent_count(
            package.target_context_id
        )
        if package.action in {
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            projection = self.prefetch_prefix_projection(package)
            target_extent_count = (
                projection.extent_count if projection is not None else 0
            )
        return self._transfer_duration_ms(
            target_bytes,
            direction="h2d",
            context_id=package.target_context_id,
            extent_count=target_extent_count,
        ) + sum(
            self._victim_d2h_duration_ms(package, context_id)
            for context_id in package.victim_context_ids
        )

    def _target_restore_extent_count(self, context_id: str | None) -> int:
        if not context_id:
            return 0
        overlay = self._overlay_by_context.get(context_id)
        if overlay is not None and int(overlay.get("h2d_copy_bytes", 0)) > 0:
            return max(1, int(overlay.get("extent_count", 0)))
        return sum(
            1
            for bundle in self._context_bundles.get(context_id, ())
            if bundle.cpu_bytes > bundle.gpu_bytes
        )

    def prepare_shadow_bytes(self, package: PredictiveActionPackage) -> int:
        return sum(
            self._prepare_projection(context_id).copy_bytes
            for context_id in package.victim_context_ids
            if self._prepare_projection(context_id) is not None
        )

    def prepare_projection(
        self,
        package: PredictiveActionPackage,
    ) -> _PrepareShadowProjection | None:
        if len(package.victim_context_ids) != 1:
            return None
        return self._prepare_projection(package.victim_context_ids[0])

    def prefetch_prefix_projection(
        self,
        package: PredictiveActionPackage,
    ) -> _PrefetchPrefixProjection | None:
        if package.target_context_id is None:
            return None
        budget = int(package.byte_budget or 0)
        key = (package.target_context_id, budget)
        if key not in self._prefetch_projections:
            overlay = self._overlay_by_context.get(package.target_context_id)
            overlay_copy_bytes = (
                int(overlay.get("h2d_copy_bytes", 0))
                if overlay is not None
                else 0
            )
            if (
                overlay_copy_bytes > 0
                and overlay_copy_bytes <= budget
                and self._target_actionable(package.target_context_id)
            ):
                generation = str(
                    overlay.get("generation_fingerprint") or ""
                )
                extent_id = (
                    f"overlay:{package.target_context_id}:{generation}"
                )
                self._prefetch_projections[key] = _PrefetchPrefixProjection(
                    target_extent_id=extent_id,
                    closure_extent_ids=(extent_id,),
                    copy_bytes=overlay_copy_bytes,
                    extent_count=max(1, int(overlay.get("extent_count", 0))),
                    shape_fingerprint=str(
                        overlay.get("shape_fingerprint") or ""
                    ),
                    cross_context_copy_bytes=min(
                        overlay_copy_bytes,
                        int(overlay.get("cross_context_bytes", 0)),
                    ),
                )
            else:
                self._prefetch_projections[key] = _prefetch_prefix_projection(
                    self.policy_input.physical_kv.bundles,
                    package.target_context_id,
                    budget,
                    extent_index=self._extent_index,
                )
        return self._prefetch_projections[key]

    def prepare_byte_only_duration_ms(
        self,
        package: PredictiveActionPackage,
    ) -> float:
        size_bytes = self.prepare_shadow_bytes(package)
        resources = self.policy_input.resources
        return (
            resources.transfer_setup_p50_ms
            + size_bytes / max(1.0, resources.d2h_service_bytes_per_ms)
        )

    def prepare_reclaimable_bytes(self, package: PredictiveActionPackage) -> int:
        return sum(
            self._prepare_projection(context_id).exclusive_copy_bytes
            for context_id in package.victim_context_ids
            if self._prepare_projection(context_id) is not None
        )

    def prepare_cross_context_bytes(
        self,
        package: PredictiveActionPackage,
    ) -> int:
        return sum(
            self._prepare_projection(context_id).cross_context_copy_bytes
            for context_id in package.victim_context_ids
            if self._prepare_projection(context_id) is not None
        )

    def intent_resource_envelope(
        self,
        package: PredictiveActionPackage,
    ) -> tuple[int, int, int]:
        """Return the semantic benefit envelope for safe-point rematerialization."""

        if package.action == PredictiveActionKind.PREPARE_HOST:
            return (
                self.prepare_reclaimable_bytes(package),
                self.prepare_cross_context_bytes(package),
                self.prepare_shadow_bytes(package),
            )
        if package.action in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            copy_bytes = self._target_restore_bytes(package)
            context_id = package.target_context_id or ""
            projection = (
                self.prefetch_prefix_projection(package)
                if package.action
                in {
                    PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                    PredictiveActionKind.RECLAIM_AND_PREFETCH,
                }
                else None
            )
            cross_context_bytes = (
                projection.cross_context_copy_bytes
                if projection is not None
                else sum(
                    max(0, bundle.cpu_bytes - bundle.gpu_bytes)
                    for bundle in self._context_bundles.get(context_id, ())
                    if any(
                        owner_context_id != context_id
                        for owner_context_id in bundle.owner_context_ids
                    )
                )
            )
            return 0, min(copy_bytes, cross_context_bytes), copy_bytes
        return 0, 0, 0

    def prepare_shadow_duration_ms(
        self, package: PredictiveActionPackage
    ) -> float:
        return self.prepare_shadow_transfer_evidence(package).duration_ms

    def prepare_shadow_transfer_evidence(
        self,
        package: PredictiveActionPackage,
    ) -> _TransferDurationEvidence:
        estimates = tuple(
            self._transfer_duration_evidence(
                projection.copy_bytes,
                direction="d2h",
                context_id=context_id,
                extent_count=projection.extent_count,
            )
            for context_id in package.victim_context_ids
            if (projection := self._prepare_projection(context_id)) is not None
        )
        if not estimates:
            return _TransferDurationEvidence(
                0.0,
                "unavailable",
                self.policy_input.snapshot_id,
            )
        sources = sorted({item.source for item in estimates})
        epochs = sorted({item.service_epoch for item in estimates})
        distances = [
            item.nearest_bucket_distance
            for item in estimates
            if item.nearest_bucket_distance is not None
        ]
        coverages = [
            item.size_coverage_bytes
            for item in estimates
            if item.size_coverage_bytes is not None
        ]
        extent_coverages = [
            item.extent_count_coverage
            for item in estimates
            if item.extent_count_coverage is not None
        ]
        shape_distances = [
            item.shape_bucket_distance
            for item in estimates
            if item.shape_bucket_distance is not None
        ]
        stalls = [
            item.estimated_unhidden_stall_p90_ms
            for item in estimates
            if item.estimated_unhidden_stall_p90_ms is not None
        ]
        return _TransferDurationEvidence(
            duration_ms=sum(item.duration_ms for item in estimates),
            source=sources[0] if len(sources) == 1 else "mixed:" + ",".join(sources),
            service_epoch=epochs[0] if len(epochs) == 1 else "mixed:" + ",".join(epochs),
            nearest_bucket_distance=max(distances) if distances else None,
            sample_count=min(item.sample_count for item in estimates),
            size_coverage_bytes=(
                (
                    min(item[0] for item in coverages),
                    max(item[1] for item in coverages),
                )
                if coverages
                else None
            ),
            extent_count_coverage=(
                (
                    min(item[0] for item in extent_coverages),
                    max(item[1] for item in extent_coverages),
                )
                if extent_coverages
                else None
            ),
            shape_bucket_distance=(
                max(shape_distances) if shape_distances else None
            ),
            shape_supported=all(item.shape_supported for item in estimates),
            estimated_unhidden_stall_p90_ms=max(stalls) if stalls else None,
        )

    def prepare_interference_evidence(
        self,
        package: PredictiveActionPackage,
        transfer: _TransferDurationEvidence,
    ) -> _TransferInterferenceEvidence:
        metadata = self.policy_input.optional_metadata.get(
            "beliefkv_transfer_interference_policy"
        )
        payload = metadata.value if metadata is not None else None
        if isinstance(payload, Mapping) and payload.get("mode") == "stall_fraction":
            fraction = float(payload.get("stall_fraction") or 0.0)
            if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
                raise ValueError("transfer stall fraction must be in [0, 1]")
            return _TransferInterferenceEvidence(
                interference_ms=transfer.duration_ms * fraction,
                source="stall_fraction_sensitivity",
                service_epoch=str(
                    payload.get("service_epoch") or transfer.service_epoch
                ),
                interference_to_transfer_ratio=fraction,
            )
        if transfer.estimated_unhidden_stall_p90_ms is not None:
            interference_ms = transfer.estimated_unhidden_stall_p90_ms
            return _TransferInterferenceEvidence(
                interference_ms=interference_ms,
                source="shape_service_curve_stall_p90",
                service_epoch=transfer.service_epoch,
                interference_to_transfer_ratio=(
                    interference_ms / transfer.duration_ms
                    if transfer.duration_ms > 0
                    else 0.0
                ),
            )
        shadow_bytes = self.prepare_shadow_bytes(package)
        interference_ms = (
            shadow_bytes * self.policy_input.resources.unhidden_stall_per_byte
        )
        resource_metadata = self.policy_input.optional_metadata.get(
            "beliefkv_resource_observation"
        )
        resource_payload = (
            resource_metadata.value if resource_metadata is not None else None
        )
        source = (
            str(resource_payload.get("unhidden_stall_source"))
            if isinstance(resource_payload, Mapping)
            and resource_payload.get("unhidden_stall_source")
            else "resource_snapshot_unhidden_stall_per_byte"
        )
        return _TransferInterferenceEvidence(
            interference_ms=interference_ms,
            source=source,
            service_epoch=self.policy_input.snapshot_id,
            interference_to_transfer_ratio=(
                interference_ms / transfer.duration_ms
                if transfer.duration_ms > 0
                else 0.0
            ),
        )

    def invocation_for_context(self, context_id: str) -> str:
        candidates = sorted(
            (
                invocation.invocation_id
                for invocation in self.graph.invocations.values()
                if invocation.context_id == context_id
            ),
            key=lambda invocation_id: (
                self.graph.invocations[invocation_id].updated_ts_ms,
                invocation_id,
            ),
            reverse=True,
        )
        if not candidates:
            raise KeyError(f"context has no invocation: {context_id}")
        return candidates[0]

    def package_feasible(self, package: PredictiveActionPackage) -> bool:
        if package.action in {
            PredictiveActionKind.OBSERVED_BASELINE,
            PredictiveActionKind.SCHEDULE,
        }:
            return True
        available = max(
            0,
            self.policy_input.resources.hbm_capacity_bytes
            - self.policy_input.resources.policy_hbm_used_bytes
            - self.policy_input.resources.hbm_reserved_bytes,
        )
        target_bytes = self._target_restore_bytes(package)
        if package.action in {
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            if self.prefetch_prefix_projection(package) is None:
                return False
        elif package.action == PredictiveActionKind.PREFETCH_GPU:
            if package.target_context_id is None or not self._target_actionable(
                package.target_context_id
            ):
                return False
        if package.action == PredictiveActionKind.PREPARE_HOST:
            projections = tuple(
                self._prepare_projection(context_id)
                for context_id in package.victim_context_ids
            )
            victim_shadow_bytes = sum(
                projection.copy_bytes
                for projection in projections
                if projection is not None
            )
            return bool(projections) and all(
                projection is not None for projection in projections
            ) and victim_shadow_bytes <= self.policy_input.resources.host_free_bytes

        victim_shadow_bytes = sum(
            self._private_missing_cpu_bytes(context_id)
            for context_id in package.victim_context_ids
        )
        victim_reclaim_bytes = sum(
            self._private_reclaimable_bytes(context_id)
            for context_id in package.victim_context_ids
        )
        if package.victim_context_ids and (
            not all(
                self._victim_actionable(context_id)
                for context_id in package.victim_context_ids
            )
            or victim_shadow_bytes > self.policy_input.resources.host_free_bytes
        ):
            return False
        if (
            package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
            and victim_shadow_bytes != 0
        ):
            return False
        if package.action == PredictiveActionKind.PREFETCH_GPU:
            return target_bytes > 0 and target_bytes <= available
        if package.action == PredictiveActionKind.PARTIAL_PREFETCH_GPU:
            return (
                target_bytes > 0
                and target_bytes <= int(package.byte_budget or 0)
                and target_bytes <= available
            )
        if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH:
            return (
                target_bytes > 0
                and victim_reclaim_bytes > 0
                and target_bytes <= available + victim_reclaim_bytes
            )
        return False

    def certificate(
        self,
        package: PredictiveActionPackage,
        *,
        evidence_read_set: PredictiveEvidenceReadSet,
        model_version: str,
    ) -> PredictiveActionCertificate:
        """Capture only the semantic and physical evidence read by a package."""

        package_context_ids = set(package.context_ids)
        execution_request_ids = set(package.execution_order_request_ids)
        package_context_ids.update(
            request.context_id
            for request in self.policy_input.runnable_frontier
            if request.request_id in execution_request_ids
        )
        beneficiary = next(
            (
                request
                for request in self.policy_input.runnable_frontier
                if request.request_id == package.beneficiary_request_id
            ),
            None,
        )
        if beneficiary is not None:
            package_context_ids.add(beneficiary.context_id)

        invocation_ids = {
            invocation.invocation_id
            for invocation in self.graph.invocations.values()
            if invocation.context_id in package_context_ids
        }
        join_ids: set[str] = set()
        pending = list(invocation_ids)
        while pending:
            invocation_id = pending.pop()
            invocation = self.graph.invocations.get(invocation_id)
            if invocation is None:
                continue
            dependencies = set(invocation.blocking_child_ids)
            if invocation.join_id is not None:
                join_ids.add(invocation.join_id)
                join = self.graph.joins.get(invocation.join_id)
                if join is not None:
                    dependencies.update(join.member_invocation_ids)
                    dependencies.update(join.waiter_invocation_ids)
            for dependency_id in dependencies:
                if (
                    dependency_id in self.graph.invocations
                    and dependency_id not in invocation_ids
                ):
                    invocation_ids.add(dependency_id)
                    pending.append(dependency_id)

        context_ids = {
            self.graph.invocations[invocation_id].context_id
            for invocation_id in invocation_ids
            if invocation_id in self.graph.invocations
        }

        context_epochs = tuple(
            sorted(
                (context_id, self.graph.contexts[context_id].epoch)
                for context_id in context_ids
                if context_id in self.graph.contexts
            )
        )
        invocation_evidence = tuple(
            (
                invocation_id,
                self.graph.invocations[invocation_id].state.value,
                self.graph.invocations[invocation_id].updated_ts_ms,
                self.graph.invocations[invocation_id].join_id,
            )
            for invocation_id in sorted(invocation_ids)
            if invocation_id in self.graph.invocations
        )
        join_evidence = tuple(
            (
                join_id,
                self.graph.joins[join_id].mode.value,
                self.graph.joins[join_id].satisfied,
                tuple(sorted(self.graph.joins[join_id].completed_member_ids)),
            )
            for join_id in sorted(join_ids)
            if join_id in self.graph.joins
        )
        communication_evidence = tuple(
            (
                source,
                target,
                edge.count,
                edge.last_ts_ms,
            )
            for (source, target), edge in sorted(
                self.graph.communication_edges.items()
            )
            if source in invocation_ids and target in invocation_ids
        )
        bundle_evidence = tuple(
            (
                bundle.bundle_id,
                bundle.generation_fingerprint,
                bundle.gpu_bytes,
                bundle.cpu_bytes,
            )
            for bundle in sorted(
                (
                    bundle
                    for bundle in self.policy_input.physical_kv.bundles
                    if package_context_ids.intersection(bundle.owner_context_ids)
                ),
                key=lambda item: item.bundle_id,
            )
        )
        target_bytes = self._target_restore_bytes(package)
        reclaimed_bytes = (
            sum(
                self._private_reclaimable_bytes(context_id)
                for context_id in package.victim_context_ids
            )
            if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
            else 0
        )
        host_bytes = sum(
            self._victim_d2h_bytes(package, context_id)
            for context_id in package.victim_context_ids
        )
        return PredictiveActionCertificate(
            package_id=package.package_id,
            action=package.action.value,
            source_snapshot_id=self.policy_input.snapshot_id,
            target_context_id=(
                package.target_context_id
                or (
                    package.victim_context_ids[0]
                    if len(package.victim_context_ids) == 1
                    else None
                )
            ),
            context_epochs=context_epochs,
            invocation_evidence=invocation_evidence,
            join_evidence=join_evidence,
            communication_evidence=communication_evidence,
            bundle_evidence=bundle_evidence,
            required_hbm_free_bytes=max(0, target_bytes - reclaimed_bytes),
            required_host_free_bytes=host_bytes,
            transfer_epoch=evidence_read_set.transfer_epoch,
            transfer_service_evidence=(
                self.policy_input.resources.h2d_service_bytes_per_ms,
                self.policy_input.resources.d2h_service_bytes_per_ms,
                self.policy_input.resources.transfer_setup_p50_ms,
            ),
            model_version=model_version,
        )

    def physicalize(
        self,
        scenario: Any,
        *,
        package_id: str,
        physical_snapshot: object,
    ) -> CandidatePhysicalPlan:
        del physical_snapshot
        package = self.packages[package_id]
        context_by_invocation = {
            invocation_id: self.graph.invocations[invocation_id].context_id
            for invocation_id in (item.invocation_id for item in scenario.outcomes)
            if invocation_id in self.graph.invocations
        }
        demands: list[PhysicalizedInvocationDemand] = []
        beneficiary_request = next(
            (
                request
                for request in self.policy_input.runnable_frontier
                if request.request_id == package.beneficiary_request_id
            ),
            None,
        )
        for outcome in scenario.outcomes:
            uncached_prefill = (
                outcome.prompt_growth_tokens
                if outcome.phase in {DemandPhase.PREFILL, DemandPhase.EXTERNAL}
                else 0
            )
            decode = (
                outcome.remaining_decode_tokens
                if outcome.phase == DemandPhase.DECODE
                else outcome.next_output_tokens
            )
            if (
                beneficiary_request is not None
                and outcome.invocation_id == beneficiary_request.invocation_id
            ):
                uncached_prefill = max(
                    uncached_prefill,
                    beneficiary_request.remaining_prefill_tokens,
                )
                predicted_decode = (
                    beneficiary_request.predicted_remaining_decode_tokens
                    if beneficiary_request.predicted_remaining_decode_tokens is not None
                    else beneficiary_request.remaining_output_tokens
                )
                decode = max(decode, int(math.ceil(predicted_decode)))
            demands.append(
                PhysicalizedInvocationDemand(
                    invocation_id=outcome.invocation_id,
                    uncached_prefill_tokens=uncached_prefill,
                    remaining_decode_tokens=decode,
                    current_sequence_tokens=outcome.current_sequence_tokens,
                )
            )

        transfers: list[ScheduledTransfer] = []
        transfer_by_context: dict[str, str] = {}
        for context_id in package.victim_context_ids:
            d2h_bytes = self._victim_d2h_bytes(package, context_id)
            if d2h_bytes <= 0:
                if package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH:
                    reclaim_bytes = self._private_reclaimable_bytes(context_id)
                    transfers.append(
                        ScheduledTransfer(
                            f"{package_id}:commit:{context_id}",
                            0.0,
                            0.0,
                            0.0,
                            hbm_delta_bytes_on_completion=-reclaim_bytes,
                        )
                    )
                    victim_invocation_id = self.invocation_for_context(context_id)
                    overlay = self._overlay_by_context.get(context_id, {})
                    restore_duration = self._transfer_duration_ms(
                        reclaim_bytes,
                        direction="h2d",
                        context_id=context_id,
                        extent_count=max(
                            1, int(overlay.get("extent_count", 0))
                        ),
                    )
                    restore_id = f"{package_id}:restore:{context_id}"
                    transfers.append(
                        ScheduledTransfer(
                            restore_id,
                            0.0,
                            restore_duration,
                            restore_duration,
                            hbm_delta_bytes_on_completion=reclaim_bytes,
                            ready_after_dependency_release_ids=(
                                victim_invocation_id,
                            ),
                        )
                    )
                    transfer_by_context[context_id] = restore_id
                continue
            duration = self._transfer_duration_ms(
                d2h_bytes,
                direction="d2h",
                context_id=context_id,
                extent_count=(
                    projection.extent_count
                    if (projection := self._prepare_projection(context_id)) is not None
                    else None
                ),
            )
            committed = package.action == PredictiveActionKind.RECLAIM_AND_PREFETCH
            transfers.append(
                ScheduledTransfer(
                    f"{package_id}:d2h:{context_id}",
                    0.0,
                    duration,
                    duration,
                    hbm_delta_bytes_on_completion=(
                        -self._private_reclaimable_bytes(context_id)
                        if committed
                        else 0
                    ),
                )
            )

        target_context_id = package.target_context_id or self.target_context_id
        target_restore_bytes = self._target_restore_bytes(package)
        if (
            package.action in {
                PredictiveActionKind.OBSERVED_BASELINE,
                PredictiveActionKind.PREPARE_HOST,
            }
            and target_restore_bytes == 0
        ):
            target_context_id = self.target_context_id
            target_restore_bytes = self._context_restore_bytes(
                target_context_id
            )
        if target_restore_bytes > 0:
            transfer_id = f"{package_id}:h2d:{target_context_id}"
            target_extent_count = self._target_restore_extent_count(
                target_context_id
            )
            if package.action in {
                PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                PredictiveActionKind.RECLAIM_AND_PREFETCH,
            }:
                projection = self.prefetch_prefix_projection(package)
                target_extent_count = (
                    projection.extent_count if projection is not None else 0
                )
            duration = self._transfer_duration_ms(
                target_restore_bytes,
                direction="h2d",
                context_id=target_context_id,
                extent_count=target_extent_count,
            )
            target_outcome = next(
                item
                for item in scenario.outcomes
                if item.invocation_id == self.target_invocation_id
            )
            speculative = package.action in {
                PredictiveActionKind.PREFETCH_GPU,
                PredictiveActionKind.PARTIAL_PREFETCH_GPU,
                PredictiveActionKind.RECLAIM_AND_PREFETCH,
            }
            transfers.append(
                ScheduledTransfer(
                    transfer_id,
                    0.0,
                    duration,
                    duration,
                    hbm_delta_bytes_on_completion=target_restore_bytes,
                    ready_after_dependency_release_ids=(
                        ()
                        if speculative
                        else (target_outcome.invocation_id,)
                        if target_outcome.dependency_mode != DependencyMode.NONE
                        else ()
                    ),
                )
            )
            transfer_by_context[target_context_id] = transfer_id

        outcome_by_id = {item.invocation_id: item for item in scenario.outcomes}
        order = self._topological_order(outcome_by_id, package)
        batches: list[ScheduledBatchQuantum] = []
        demand_by_id = {item.invocation_id: item for item in demands}
        for invocation_id in order:
            demand = demand_by_id[invocation_id]
            transfer_ids = ()
            context_id = context_by_invocation.get(invocation_id)
            if context_id in transfer_by_context:
                transfer_ids = (transfer_by_context[context_id],)
            if demand.uncached_prefill_tokens:
                batches.append(
                    ScheduledBatchQuantum(
                        batch_id=f"{package_id}:prefill:{invocation_id}",
                        phase=DemandPhase.PREFILL,
                        requests=(
                            ScheduledRequestQuantum(
                                invocation_id,
                                demand.uncached_prefill_tokens,
                                demand.current_sequence_tokens,
                            ),
                        ),
                        chunk_position="first",
                        ready_after_transfer_ids=transfer_ids,
                    )
                )
                transfer_ids = ()
            if demand.remaining_decode_tokens:
                batches.append(
                    ScheduledBatchQuantum(
                        batch_id=f"{package_id}:decode:{invocation_id}",
                        phase=DemandPhase.DECODE,
                        requests=(
                            ScheduledRequestQuantum(
                                invocation_id,
                                demand.remaining_decode_tokens,
                                demand.current_sequence_tokens
                                + demand.uncached_prefill_tokens,
                            ),
                        ),
                        ready_after_transfer_ids=transfer_ids,
                    )
                )

        deterministic = self.package_feasible(package)
        modeled_reservation = min(
            self.policy_input.resources.hbm_reserved_bytes,
            sum(
                request.startup_bytes
                for request in self.policy_input.runnable_frontier
                if request.invocation_id in demand_by_id
                and request.causal_class.startswith("reserved_admission:")
            ),
        )
        return CandidatePhysicalPlan(
            package_id=package_id,
            physical_snapshot_id=self.policy_input.physical_kv.snapshot_id,
            physical_snapshot_revision=self.policy_input.physical_kv.allocator_version,
            invocation_demands=tuple(demands),
            batches=tuple(batches),
            transfers=tuple(transfers),
            hbm_capacity_bytes=self.policy_input.resources.hbm_capacity_bytes,
            initial_hbm_used_bytes=self.policy_input.resources.policy_hbm_used_bytes,
            initial_hbm_reserved_bytes=(
                self.policy_input.resources.hbm_reserved_bytes
            ),
            modeled_growth_reservation_bytes=modeled_reservation,
            kv_bytes_per_token=self.kv_bytes_per_token,
            projected_beneficiary_request_id=(
                beneficiary_request.request_id
                if beneficiary_request is not None
                else None
            ),
            projected_beneficiary_invocation_id=(
                beneficiary_request.invocation_id
                if beneficiary_request is not None
                else None
            ),
            projected_beneficiary_startup_bytes=package.beneficiary_startup_bytes,
            projected_beneficiary_growth_bytes=package.beneficiary_growth_bytes,
            projected_beneficiary_block_offset_ms=(
                package.predicted_block_time_ms
            ),
            projected_beneficiary_deficit_bytes=(
                package.predicted_deficit_bytes
            ),
            deterministic_feasible=deterministic,
            liveness_path_proven=True,
        )

    def _target_restore_bytes(self, package: PredictiveActionPackage) -> int:
        if package.target_context_id is None:
            return 0
        missing = self._context_restore_bytes(package.target_context_id)
        if package.action in {
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            projection = self.prefetch_prefix_projection(package)
            return projection.copy_bytes if projection is not None else 0
        return missing

    def _context_restore_bytes(self, context_id: str) -> int:
        overlay = self._overlay_by_context.get(context_id)
        if overlay is not None:
            return max(0, int(overlay.get("h2d_copy_bytes", 0)))
        return self._context_bytes.get(context_id, (0, 0, 0))[1]

    def _target_actionable(self, context_id: str) -> bool:
        overlay = self._overlay_by_context.get(context_id)
        if overlay is not None:
            return (
                int(overlay.get("h2d_copy_bytes", 0)) > 0
                and int(overlay.get("locked_bytes", 0)) == 0
                and not bool(overlay.get("native_loading", False))
                and not tuple(overlay.get("blocker_codes", ()))
            )
        required = tuple(
            bundle
            for bundle in self._context_bundles.get(context_id, ())
            if bundle.cpu_bytes > bundle.gpu_bytes
        )
        return bool(required) and all(
            bundle.actionable and not bundle.locked_bytes and not bundle.blocker_codes
            for bundle in required
        )

    def _private_victim_bundles(self, context_id: str) -> tuple[Any, ...]:
        return tuple(
            bundle
            for bundle in self._context_bundles.get(context_id, ())
            if bundle.owner_context_ids == (context_id,)
            and bundle.scope == "exclusive_suffix"
            and bundle.gpu_bytes > bundle.cpu_bytes
        )

    def _victim_actionable(self, context_id: str) -> bool:
        overlay = self._overlay_by_context.get(context_id)
        if overlay is not None:
            return (
                int(overlay.get("d2h_copy_bytes", 0)) == 0
                and int(overlay.get("exclusive_reclaimable_bytes", 0)) > 0
                and int(overlay.get("locked_bytes", 0)) == 0
                and not bool(overlay.get("native_loading", False))
                and not tuple(overlay.get("blocker_codes", ()))
            )
        required = self._private_victim_bundles(context_id)
        return bool(required) and all(
            bundle.actionable and not bundle.locked_bytes and not bundle.blocker_codes
            for bundle in required
        )

    def _private_missing_cpu_bytes(self, context_id: str) -> int:
        overlay = self._overlay_by_context.get(context_id)
        if overlay is not None:
            return int(overlay.get("d2h_copy_bytes", 0))
        return sum(
            max(0, bundle.gpu_bytes - bundle.cpu_bytes)
            for bundle in self._private_victim_bundles(context_id)
        )

    def _private_reclaimable_bytes(self, context_id: str) -> int:
        overlay = self._overlay_by_context.get(context_id)
        if overlay is not None:
            return int(overlay.get("exclusive_reclaimable_bytes", 0))
        return sum(
            bundle.marginal_reclaimable_bytes
            for bundle in self._private_victim_bundles(context_id)
        )

    def _prepare_projection(
        self,
        context_id: str,
    ) -> _PrepareShadowProjection | None:
        if context_id not in self._prepare_projections:
            overlay = self._overlay_by_context.get(context_id)
            if overlay is not None:
                generation = str(overlay.get("generation_fingerprint") or "")
                root_extent_id = f"overlay:{context_id}:{generation}"
                self._prepare_projections[context_id] = (
                    _PrepareShadowProjection(
                        root_extent_id=root_extent_id,
                        closure_extent_ids=(root_extent_id,),
                        copy_bytes=int(overlay.get("d2h_copy_bytes", 0)),
                        extent_count=int(overlay.get("extent_count", 0)),
                        shape_fingerprint=str(
                            overlay.get("shape_fingerprint") or ""
                        ),
                        exclusive_copy_bytes=int(
                            overlay.get("exclusive_reclaimable_bytes", 0)
                        ),
                        cross_context_copy_bytes=int(
                            overlay.get("cross_context_bytes", 0)
                        ),
                    )
                    if generation
                    and int(overlay.get("d2h_copy_bytes", 0)) > 0
                    and int(overlay.get("exclusive_reclaimable_bytes", 0)) > 0
                    else None
                )
            else:
                self._prepare_projections[context_id] = (
                    _prepare_shadow_projection(
                        self.policy_input.physical_kv.bundles,
                        context_id,
                        extent_index=self._extent_index,
                    )
                )
        return self._prepare_projections[context_id]

    def _victim_d2h_bytes(
        self,
        package: PredictiveActionPackage,
        context_id: str,
    ) -> int:
        if package.action == PredictiveActionKind.PREPARE_HOST:
            projection = self._prepare_projection(context_id)
            return projection.copy_bytes if projection is not None else 0
        return self._private_missing_cpu_bytes(context_id)

    def _victim_d2h_duration_ms(
        self,
        package: PredictiveActionPackage,
        context_id: str,
    ) -> float:
        projection = (
            self._prepare_projection(context_id)
            if package.action == PredictiveActionKind.PREPARE_HOST
            else None
        )
        return self._transfer_duration_ms(
            self._victim_d2h_bytes(package, context_id),
            direction="d2h",
            context_id=context_id,
            extent_count=projection.extent_count if projection is not None else None,
        )

    def snapshot_consistent_reactive_victim(
        self,
        pressure_deficit_bytes: int,
    ) -> tuple[str | None, float | None]:
        """Project the current observed victim order onto a future pressure point.

        This is a conservative snapshot approximation. It does not claim that
        the future observed JointPlan will retain the same ordering or select a
        single victim.
        """

        if pressure_deficit_bytes <= 0:
            return None, None
        options: list[tuple[float, int, str, int]] = []
        candidate_contexts = (
            self._context_bundles.keys() | self._overlay_by_context.keys()
        )
        for context_id in candidate_contexts:
            projection = self._prepare_projection(context_id)
            if (
                projection is None
                or projection.exclusive_copy_bytes < pressure_deficit_bytes
                or projection.copy_bytes
                > self.policy_input.resources.host_free_bytes
            ):
                continue
            states = {
                invocation.state.value
                for invocation in self.graph.invocations.values()
                if invocation.context_id == context_id
            }
            if not states or not states.issubset(PredictiveEligibilityIndex._WAIT_STATES):
                continue
            last_access_ms = max(
                (
                    bundle.last_access_ms
                    for bundle in self._context_bundles.get(context_id, ())
                ),
                default=float(
                    self._overlay_by_context.get(context_id, {}).get(
                        "captured_ts_ms", 0.0
                    )
                ),
            )
            options.append(
                (
                    last_access_ms,
                    -projection.exclusive_copy_bytes,
                    context_id,
                    projection.copy_bytes,
                )
            )
        if not options:
            return None, None
        _last_access, _negative_reclaim, context_id, copy_bytes = min(options)
        return context_id, self._transfer_duration_ms(
            copy_bytes,
            direction="d2h",
            context_id=context_id,
            extent_count=self._prepare_projection(context_id).extent_count,
        )

    def _topological_order(
        self,
        outcomes: Mapping[str, Any],
        package: PredictiveActionPackage,
    ) -> tuple[str, ...]:
        preferred_order = package.execution_order_request_ids
        execution_order = tuple(
            dict.fromkeys(
                (*preferred_order, *self.source_plan.execution.ordered_request_ids)
            )
        )
        candidate_order = tuple(
            request_id
            for request_id in self.source_plan.candidate_order_request_ids
            if request_id not in execution_order
        )
        request_order = {
            request_id: index
            for index, request_id in enumerate(
                (*execution_order, *candidate_order)
            )
        }
        request_by_invocation = {
            item.invocation_id: item.request_id
            for item in self.policy_input.runnable_frontier
        }
        remaining = set(outcomes)
        ordered: list[str] = []
        while remaining:
            ready = [
                invocation_id
                for invocation_id in remaining
                if set(outcomes[invocation_id].dependency_invocation_ids).issubset(
                    set(ordered)
                )
            ]
            if not ready:
                ready = list(remaining)
            ready.sort(
                key=lambda item: (
                    request_order.get(request_by_invocation.get(item, ""), 1 << 30),
                    item,
                )
            )
            selected = ready[0]
            ordered.append(selected)
            remaining.remove(selected)
        return tuple(ordered)

    @staticmethod
    def _context_byte_summary(
        policy_input: PolicyInput,
    ) -> Mapping[str, tuple[int, int, int]]:
        values: dict[str, list[int]] = {}
        for bundle in policy_input.physical_kv.bundles:
            for context_id in bundle.owner_context_ids:
                item = values.setdefault(context_id, [0, 0, 0])
                item[0] += bundle.gpu_bytes
                item[1] += max(0, bundle.cpu_bytes - bundle.gpu_bytes)
                item[2] += max(0, bundle.gpu_bytes - bundle.cpu_bytes)
        return {key: tuple(value) for key, value in values.items()}

    def _transfer_duration_ms(
        self,
        size_bytes: int,
        *,
        direction: str,
        context_id: str | None = None,
        extent_count: int | None = None,
    ) -> float:
        return self._transfer_duration_evidence(
            size_bytes,
            direction=direction,
            context_id=context_id,
            extent_count=extent_count,
        ).duration_ms

    def _transfer_duration_evidence(
        self,
        size_bytes: int,
        *,
        direction: str,
        context_id: str | None = None,
        extent_count: int | None = None,
    ) -> _TransferDurationEvidence:
        if size_bytes <= 0:
            return _TransferDurationEvidence(
                0.0,
                "zero_bytes",
                self.policy_input.snapshot_id,
            )
        compact_metadata = self.policy_input.optional_metadata.get(
            "beliefkv_transfer_service_curve_snapshot"
        )
        compact_snapshot = (
            compact_metadata.value if compact_metadata is not None else None
        )
        if (
            direction in {"d2h", "h2d"}
            and extent_count is not None
            and extent_count > 0
            and isinstance(compact_snapshot, Mapping)
        ):
            transfer_direction = (
                TransferDirection.D2H
                if direction == "d2h"
                else TransferDirection.H2D
            )
            estimate = TransferServiceCurve.estimate_snapshot(
                compact_snapshot,
                transfer_direction,
                size_bytes,
                page_count=extent_count,
                command_kind=(
                    "offload_context" if direction == "d2h" else "prefetch_context"
                ),
                host_copy_state="missing" if direction == "d2h" else "present",
                pinned_host=True,
            )
            if direction == "h2d" and not estimate.shape_supported:
                estimate = TransferServiceCurve.estimate_snapshot_direction_envelope(
                    compact_snapshot,
                    transfer_direction,
                    size_bytes,
                    command_kind="prefetch_context",
                    host_copy_state="present",
                    pinned_host=True,
                )
            return _TransferDurationEvidence(
                duration_ms=estimate.estimated_completion_p90_ms,
                source=estimate.source,
                service_epoch=str(
                    compact_snapshot.get("warm_start_hardware_key")
                    or self.policy_input.snapshot_id
                ),
                nearest_bucket_distance=estimate.nearest_bucket_distance,
                sample_count=estimate.sample_count,
                size_coverage_bytes=estimate.size_coverage_bytes,
                extent_count_coverage=estimate.extent_count_coverage,
                shape_bucket_distance=estimate.shape_bucket_distance,
                shape_supported=estimate.shape_supported,
                estimated_unhidden_stall_p90_ms=(
                    estimate.estimated_unhidden_stall_p90_ms
                ),
            )
        metadata = self.policy_input.optional_metadata.get(
            "beliefkv_transfer_service_estimates"
        )
        payload = metadata.value if metadata is not None else None
        if isinstance(payload, Mapping) and context_id:
            contexts = payload.get("contexts")
            context = (
                contexts.get(context_id)
                if isinstance(contexts, Mapping)
                else None
            )
            estimate = (
                context.get(direction)
                if isinstance(context, Mapping)
                else None
            )
            if isinstance(estimate, Mapping):
                rate = float(estimate.get("effective_bytes_per_ms_p10") or 0.0)
                setup = float(estimate.get("setup_p90_ms") or 0.0)
                fixed = float(estimate.get("fixed_overhead_p90_ms") or 0.0)
                floor = float(estimate.get("callback_floor_p90_ms") or 0.0)
                if rate > 0:
                    coverage_raw = estimate.get("size_coverage_bytes")
                    coverage = (
                        tuple(int(item) for item in coverage_raw)
                        if isinstance(coverage_raw, (list, tuple))
                        and len(coverage_raw) == 2
                        else None
                    )
                    return _TransferDurationEvidence(
                        duration_ms=max(
                            floor,
                            setup + fixed + size_bytes / rate,
                        ),
                        source=str(estimate.get("source") or "context_estimate"),
                        service_epoch=str(
                            estimate.get("service_epoch")
                            or payload.get("hardware_key")
                            or self.policy_input.snapshot_id
                        ),
                        nearest_bucket_distance=(
                            int(estimate["nearest_bucket_distance"])
                            if estimate.get("nearest_bucket_distance") is not None
                            else None
                        ),
                        sample_count=int(estimate.get("sample_count") or 0),
                        size_coverage_bytes=coverage,
                    )
        resources = self.policy_input.resources
        rate = (
            resources.h2d_service_bytes_per_ms
            if direction == "h2d"
            else resources.d2h_service_bytes_per_ms
        )
        return _TransferDurationEvidence(
            duration_ms=(
                resources.transfer_setup_p50_ms + size_bytes / max(1.0, rate)
            ),
            source="resource_snapshot_service_rate",
            service_epoch=self.policy_input.snapshot_id,
        )
