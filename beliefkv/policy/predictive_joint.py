from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math
from typing import Mapping

from beliefkv.policy.online_joint import ActionGroup
from beliefkv.policy.predictive_timeline import TimedScenario
from beliefkv.predictor.frontier_belief import FrontierBeliefSnapshot


class PredictiveActionKind(str, Enum):
    OBSERVED_BASELINE = "observed_baseline"
    SCHEDULE = "schedule"
    PREPARE_HOST = "prepare_host"
    PREFETCH_GPU = "prefetch_gpu"
    RECLAIM_AND_PREFETCH = "reclaim_and_prefetch"
    PARTIAL_PREFETCH_GPU = "partial_prefetch_gpu"

@dataclass(frozen=True)
class ProjectedReclaimRequirement:
    """Observed-seed request expected to hit an HBM admission deficit."""

    beneficiary_request_id: str
    beneficiary_invocation_id: str
    beneficiary_context_id: str
    beneficiary_context_epoch: int
    required_startup_bytes: int
    required_growth_bytes: int
    source_joint_plan_id: str
    causal_package_generation: str
    predicted_block_time_ms: float | None = None
    predicted_deficit_bytes: int = 0

    def __post_init__(self) -> None:
        identities = (
            self.beneficiary_request_id,
            self.beneficiary_invocation_id,
            self.beneficiary_context_id,
            self.source_joint_plan_id,
            self.causal_package_generation,
        )
        if any(not value for value in identities):
            raise ValueError("projected reclaim identity is required")
        if min(
            self.beneficiary_context_epoch,
            self.required_startup_bytes,
            self.required_growth_bytes,
            self.predicted_deficit_bytes,
        ) < 0:
            raise ValueError("projected reclaim counters must be non-negative")
        if (
            self.predicted_block_time_ms is not None
            and (
                not math.isfinite(self.predicted_block_time_ms)
                or self.predicted_block_time_ms < 0
            )
        ):
            raise ValueError(
                "projected reclaim block time must be finite and non-negative"
            )

    @property
    def required_fragment_bytes(self) -> int:
        return self.required_startup_bytes + self.required_growth_bytes

    def with_prediction(
        self,
        *,
        block_time_ms: float,
        deficit_bytes: int,
    ) -> "ProjectedReclaimRequirement":
        return replace(
            self,
            predicted_block_time_ms=block_time_ms,
            predicted_deficit_bytes=deficit_bytes,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class BeneficiaryOpportunityProbe:
    """Cheap safe-point classification before physical victim capture."""

    beneficiary_request_id: str
    beneficiary_context_id: str
    beneficiary_context_epoch: int
    required_bytes: int
    hbm_available_bytes: int
    hbm_risk_margin_bytes: int
    projected_running_growth_bytes: int
    projected_hbm_available_bytes: int
    predicted_block_time_ms: float | None
    predicted_deficit_bytes: int
    running_request_count: int
    max_running_requests: int
    beneficiary_slot_blocked: bool
    beneficiary_hbm_blocked: bool
    beneficiary_slot_then_hbm_blocked: bool
    hbm_opportunity_possible: bool
    captured_ts_ms: float
    immediate_admission_fit: bool = False
    future_growth_bytes: int = 0
    future_growth_deficit_bytes: int = 0
    block_time_source: str = "safe_point_immediate_only"

    def __post_init__(self) -> None:
        if not self.beneficiary_request_id or not self.beneficiary_context_id:
            raise ValueError("beneficiary opportunity identity is required")
        if min(
            self.beneficiary_context_epoch,
            self.required_bytes,
            self.hbm_available_bytes,
            self.hbm_risk_margin_bytes,
            self.projected_running_growth_bytes,
            self.projected_hbm_available_bytes,
            self.predicted_deficit_bytes,
            self.running_request_count,
            self.max_running_requests,
            self.captured_ts_ms,
            self.future_growth_bytes,
            self.future_growth_deficit_bytes,
        ) < 0:
            raise ValueError("beneficiary opportunity values must be non-negative")
        if not self.block_time_source:
            raise ValueError("beneficiary block-time source is required")
        if (
            self.predicted_block_time_ms is not None
            and (
                not math.isfinite(self.predicted_block_time_ms)
                or self.predicted_block_time_ms < 0
            )
        ):
            raise ValueError("beneficiary block time must be finite and non-negative")
        projected_blocked = (
            self.predicted_deficit_bytes > 0
            or self.future_growth_deficit_bytes > 0
        )
        if (
            self.beneficiary_slot_then_hbm_blocked
            != (
                self.beneficiary_slot_blocked
                and projected_blocked
            )
        ):
            raise ValueError("slot-then-HBM classification is inconsistent")
        expected_possible = projected_blocked
        if self.hbm_opportunity_possible != expected_possible:
            raise ValueError("HBM opportunity classification is inconsistent")
        if (
            self.predicted_block_time_ms is not None
            and self.predicted_deficit_bytes <= 0
        ):
            raise ValueError("beneficiary block time requires a positive deficit")

    @property
    def classification(self) -> str:
        if self.beneficiary_slot_then_hbm_blocked:
            return "slot_then_hbm_blocked"
        if self.beneficiary_hbm_blocked:
            return "hbm_blocked"
        if self.beneficiary_slot_blocked:
            return (
                "slot_with_near_hbm_risk"
                if self.hbm_opportunity_possible
                else "slot_only"
            )
        return (
            "near_hbm_risk"
            if self.hbm_opportunity_possible
            else "capacity_available"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        } | {"classification": self.classification}


@dataclass(frozen=True)
class ActionLocalPhysicalOverlay:
    """Compact PREPARE estimate; commit always rematerializes the live bundle."""

    context_id: str
    context_epoch: int
    context_revision: int
    page_revision: int
    topology_revision: int
    generation_fingerprint: str
    shape_fingerprint: str
    exclusive_reclaimable_bytes: int
    d2h_copy_bytes: int
    extent_count: int
    cross_context_bytes: int
    locked_bytes: int
    owner_context_ids: tuple[str, ...]
    blocker_codes: tuple[str, ...]
    native_loading: bool
    captured_ts_ms: float
    h2d_copy_bytes: int = 0
    evidence_kind: str = "exact_preview"

    def __post_init__(self) -> None:
        if not self.context_id or not self.generation_fingerprint:
            raise ValueError("action-local overlay identity is required")
        if not self.shape_fingerprint:
            raise ValueError("action-local overlay shape is required")
        if self.evidence_kind not in {
            "exact_preview",
            "context_summary_upper_bound",
            "commit_ready_summary",
            "prefetch_target_preview",
        }:
            raise ValueError("unsupported action-local overlay evidence kind")
        if min(
            self.context_epoch,
            self.context_revision,
            self.page_revision,
            self.topology_revision,
            self.exclusive_reclaimable_bytes,
            self.d2h_copy_bytes,
            self.h2d_copy_bytes,
            self.extent_count,
            self.cross_context_bytes,
            self.locked_bytes,
            self.captured_ts_ms,
        ) < 0:
            raise ValueError("action-local overlay values must be non-negative")
        if (
            self.d2h_copy_bytes > 0 or self.h2d_copy_bytes > 0
        ) and self.extent_count <= 0:
            raise ValueError("non-empty overlay requires an extent count")
        if len(set(self.owner_context_ids)) != len(self.owner_context_ids):
            raise ValueError("overlay owners must be unique")
        if len(set(self.blocker_codes)) != len(self.blocker_codes):
            raise ValueError("overlay blockers must be unique")
        object.__setattr__(
            self, "owner_context_ids", tuple(sorted(self.owner_context_ids))
        )
        object.__setattr__(
            self, "blocker_codes", tuple(sorted(self.blocker_codes))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class PredictiveActionPackage:
    package_id: str
    action: PredictiveActionKind
    context_ids: tuple[str, ...] = ()
    source_joint_plan_id: str | None = None
    target_context_id: str | None = None
    victim_context_ids: tuple[str, ...] = ()
    byte_budget: int | None = None
    beneficiary_request_id: str | None = None
    beneficiary_startup_bytes: int = 0
    beneficiary_growth_bytes: int = 0
    predicted_block_time_ms: float | None = None
    predicted_deficit_bytes: int = 0
    victim_reclaim_bytes: int = 0
    causal_package_generation: str | None = None
    execution_order_request_ids: tuple[str, ...] = ()
    admit_request_ids: tuple[str, ...] = ()
    beneficiary_invocation_id: str | None = None
    beneficiary_context_id: str | None = None
    beneficiary_context_epoch: int | None = None
    deferred_physical_commit: bool = False

    def __post_init__(self) -> None:
        if not self.package_id:
            raise ValueError("predictive action package ID is required")
        object.__setattr__(self, "action", PredictiveActionKind(self.action))
        if not isinstance(self.deferred_physical_commit, bool):
            raise ValueError("deferred physical commit must be boolean")
        if self.deferred_physical_commit and self.action not in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
        }:
            raise ValueError("only prefetch packages may defer physical commit")
        contexts = tuple(sorted(set(self.context_ids)))
        if any(not item for item in contexts):
            raise ValueError("predictive package context IDs must be non-empty")
        object.__setattr__(self, "context_ids", contexts)
        if self.action != PredictiveActionKind.OBSERVED_BASELINE and not contexts:
            raise ValueError("predictive action package requires a context")
        execution_order = tuple(self.execution_order_request_ids)
        admit_requests = tuple(self.admit_request_ids)
        if (
            any(not item for item in execution_order)
            or len(execution_order) != len(set(execution_order))
        ):
            raise ValueError("predictive execution order must contain unique IDs")
        if (
            any(not item for item in admit_requests)
            or len(admit_requests) != len(set(admit_requests))
        ):
            raise ValueError("predictive admission set must contain unique IDs")
        if not set(admit_requests).issubset(execution_order):
            raise ValueError("predictive admissions must belong to execution order")
        object.__setattr__(self, "execution_order_request_ids", execution_order)
        object.__setattr__(self, "admit_request_ids", admit_requests)
        if min(
            self.beneficiary_startup_bytes,
            self.beneficiary_growth_bytes,
            self.predicted_deficit_bytes,
            self.victim_reclaim_bytes,
        ) < 0:
            raise ValueError("predictive package byte values must be non-negative")
        if (
            self.predicted_block_time_ms is not None
            and (
                not math.isfinite(self.predicted_block_time_ms)
                or self.predicted_block_time_ms < 0
            )
        ):
            raise ValueError(
                "predictive package block time must be finite and non-negative"
            )
        beneficiary_fields = (
            self.beneficiary_startup_bytes,
            self.beneficiary_growth_bytes,
        )
        if self.beneficiary_request_id is None and any(beneficiary_fields):
            raise ValueError("beneficiary bytes require a beneficiary identity")
        beneficiary_identity = (
            self.beneficiary_invocation_id,
            self.beneficiary_context_id,
            self.beneficiary_context_epoch,
        )
        if self.beneficiary_request_id is None and any(
            item is not None for item in beneficiary_identity
        ):
            raise ValueError("beneficiary identity requires a request")
        if any(item is not None for item in beneficiary_identity):
            if self.beneficiary_request_id is None or any(
                item is None for item in beneficiary_identity
            ):
                raise ValueError("predictive beneficiary identity is incomplete")
            if (
                self.beneficiary_context_epoch is not None
                and self.beneficiary_context_epoch < 0
            ):
                raise ValueError("beneficiary context epoch must be non-negative")
        if self.causal_package_generation is not None and not self.causal_package_generation:
            raise ValueError("causal package generation must be non-empty")
        target = self.target_context_id
        victims = tuple(sorted(set(self.victim_context_ids)))
        if self.action in {
            PredictiveActionKind.PREFETCH_GPU,
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
        }:
            target = target or contexts[0]
        elif self.action == PredictiveActionKind.PREPARE_HOST:
            victims = victims or contexts
        elif self.action == PredictiveActionKind.RECLAIM_AND_PREFETCH:
            target = target or contexts[0]
            victims = victims or tuple(item for item in contexts if item != target)
            if not victims:
                raise ValueError("joint reclaim/prefetch requires a victim set")
        if self.action == PredictiveActionKind.PREPARE_HOST:
            if self.beneficiary_request_id is None:
                raise ValueError("prepare package requires a projected beneficiary")
            if self.causal_package_generation is None:
                raise ValueError("prepare package requires a causal generation")
            if self.beneficiary_startup_bytes + self.beneficiary_growth_bytes <= 0:
                raise ValueError("prepare package requires beneficiary demand")
        if self.action == PredictiveActionKind.SCHEDULE:
            if self.beneficiary_request_id is None or not execution_order:
                raise ValueError("schedule package requires a beneficiary and order")
            if execution_order[0] != self.beneficiary_request_id:
                raise ValueError(
                    "schedule beneficiary must lead the execution order"
                )
        if target is not None and not target:
            raise ValueError("predictive target context must be non-empty")
        if any(not item for item in victims):
            raise ValueError("predictive victim contexts must be non-empty")
        if target is not None and target in victims:
            raise ValueError("predictive target cannot also be a victim")
        if self.action in {
            PredictiveActionKind.PARTIAL_PREFETCH_GPU,
            PredictiveActionKind.RECLAIM_AND_PREFETCH,
        }:
            if self.byte_budget is None or self.byte_budget <= 0:
                raise ValueError("funded prefetch requires a positive byte budget")
        elif self.byte_budget is not None:
            raise ValueError("byte budget is only valid for bounded prefetch")
        if self.action == PredictiveActionKind.RECLAIM_AND_PREFETCH:
            if len(victims) != 1:
                raise ValueError("funded prefetch requires exactly one victim")
            if self.victim_reclaim_bytes <= 0:
                raise ValueError("funded prefetch requires reclaimable victim bytes")
        object.__setattr__(self, "target_context_id", target)
        object.__setattr__(self, "victim_context_ids", victims)


@dataclass(frozen=True)
class ScenarioCost:
    """One finite-horizon evaluation with non-overlapping accounting terms."""

    action_unlock_delay_ms: float
    workflow_service_lag_ms: float
    residual_hbm_time_byte_ms: float = 0.0
    residual_host_time_byte_ms: float = 0.0
    residual_pcie_time_ms: float = 0.0
    terminal_debt_ms: float = 0.0
    recourse_credit_ms: float = 0.0
    future_hbm_peak_bytes: int = 0
    future_hbm_overflow_bytes: int = 0
    future_hbm_feasible: bool = True
    deterministic_feasible: bool = True
    future_feasible: bool = True
    liveness_path_proven: bool = True

    def __post_init__(self) -> None:
        values = (
            self.action_unlock_delay_ms,
            self.workflow_service_lag_ms,
            self.residual_hbm_time_byte_ms,
            self.residual_host_time_byte_ms,
            self.residual_pcie_time_ms,
            self.terminal_debt_ms,
            self.recourse_credit_ms,
        )
        if any(not math.isfinite(item) or item < 0 for item in values):
            raise ValueError("scenario cost terms must be finite and non-negative")
        if min(self.future_hbm_peak_bytes, self.future_hbm_overflow_bytes) < 0:
            raise ValueError("future HBM metrics must be non-negative")

    def loss(
        self,
        *,
        hbm_shadow_price_ms_per_byte_ms: float,
        host_shadow_price_ms_per_byte_ms: float,
        pcie_shadow_price: float,
    ) -> float:
        return (
            self.action_unlock_delay_ms
            + self.workflow_service_lag_ms
            + self.terminal_debt_ms
            + self.residual_hbm_time_byte_ms
            * hbm_shadow_price_ms_per_byte_ms
            + self.residual_host_time_byte_ms
            * host_shadow_price_ms_per_byte_ms
            + self.residual_pcie_time_ms * pcie_shadow_price
            - self.recourse_credit_ms
        )


@dataclass(frozen=True)
class PrepareRecourseDiagnostic:
    scenario_id: str
    probability_mass: float
    shadow_completion_ms: float | None
    first_pressure_ms: float | None
    pressure_deficit_bytes: int
    parent_reentry_ms: float | None
    exclusive_reclaimable_bytes: int
    full_closure_copy_bytes: int
    cross_context_copy_bytes: int
    baseline_reactive_d2h_ms: float | None
    proactive_interference_ms: float
    transfer_duration_source: str
    transfer_service_epoch: str
    interference_source: str
    interference_service_epoch: str
    interference_to_transfer_ratio: float
    transfer_nearest_bucket_distance: int | None
    transfer_sample_count: int
    transfer_size_coverage_bytes: tuple[int, int] | None
    transfer_extent_count_coverage: tuple[int, int] | None
    transfer_shape_bucket_distance: int | None
    transfer_shape_supported: bool
    predicted_extent_count: int
    shape_fingerprint: str
    byte_only_transfer_ms: float
    shape_aware_transfer_p90_ms: float
    shape_aware_stall_p90_ms: float
    morphology_deadline_ms: float | None
    morphology_slack_ms: float | None
    conservative_morphology_deadline_ms: float | None
    conservative_morphology_slack_ms: float | None
    morphology_debt_ms: float
    morphology_penalty_ms: float
    reactive_victim_model: str
    recourse_credit_ms: float
    recourse_failure_reason: str

    def __post_init__(self) -> None:
        required = (
            self.scenario_id,
            self.recourse_failure_reason,
            self.transfer_duration_source,
            self.transfer_service_epoch,
            self.interference_source,
            self.interference_service_epoch,
            self.reactive_victim_model,
            self.shape_fingerprint,
        )
        if any(not item for item in required):
            raise ValueError("prepare recourse diagnostic identity is required")
        if not 0.0 <= self.probability_mass <= 1.0:
            raise ValueError("recourse probability must be in [0, 1]")
        optional_times = (
            self.shadow_completion_ms,
            self.first_pressure_ms,
            self.parent_reentry_ms,
            self.baseline_reactive_d2h_ms,
        )
        if any(
            item is not None and (not math.isfinite(item) or item < 0)
            for item in optional_times
        ):
            raise ValueError("recourse timestamps must be finite and non-negative")
        values = (
            self.pressure_deficit_bytes,
            self.exclusive_reclaimable_bytes,
            self.full_closure_copy_bytes,
            self.cross_context_copy_bytes,
            self.proactive_interference_ms,
            self.interference_to_transfer_ratio,
            self.transfer_sample_count,
            self.recourse_credit_ms,
            self.predicted_extent_count,
            self.byte_only_transfer_ms,
            self.shape_aware_transfer_p90_ms,
            self.shape_aware_stall_p90_ms,
        )
        if any(not math.isfinite(float(item)) or item < 0 for item in values):
            raise ValueError("recourse values must be finite and non-negative")
        if (
            self.transfer_nearest_bucket_distance is not None
            and self.transfer_nearest_bucket_distance < 0
        ):
            raise ValueError("transfer bucket distance must be non-negative")
        if self.transfer_size_coverage_bytes is not None:
            low, high = self.transfer_size_coverage_bytes
            if low < 0 or high < low:
                raise ValueError("transfer size coverage is invalid")
        if self.transfer_extent_count_coverage is not None:
            low, high = self.transfer_extent_count_coverage
            if low < 0 or high < low:
                raise ValueError("transfer extent-count coverage is invalid")
        if (
            self.transfer_shape_bucket_distance is not None
            and self.transfer_shape_bucket_distance < 0
        ):
            raise ValueError("transfer shape-bucket distance must be non-negative")
        for item in (
            self.morphology_deadline_ms,
            self.morphology_slack_ms,
            self.conservative_morphology_deadline_ms,
            self.conservative_morphology_slack_ms,
        ):
            if item is not None and not math.isfinite(item):
                raise ValueError("morphology timing must be finite")
        if self.morphology_debt_ms < 0 or not math.isfinite(
            self.morphology_debt_ms
        ):
            raise ValueError("morphology debt must be finite and non-negative")
        if not math.isfinite(self.morphology_penalty_ms):
            raise ValueError("morphology penalty must be finite")

    def to_dict(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class PackageScenarioEvaluation:
    package: PredictiveActionPackage
    costs_by_scenario: Mapping[str, ScenarioCost]
    other_cost: ScenarioCost
    recourse_diagnostics_by_scenario: Mapping[
        str, PrepareRecourseDiagnostic
    ] = field(default_factory=dict)
    dependency_release_offsets_by_scenario: Mapping[
        str, Mapping[str, float]
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(not scenario_id for scenario_id in self.costs_by_scenario):
            raise ValueError("scenario evaluation IDs must be non-empty")
        if not set(self.recourse_diagnostics_by_scenario).issubset(
            self.costs_by_scenario
        ):
            raise ValueError("recourse diagnostics reference unknown scenarios")
        if not set(self.dependency_release_offsets_by_scenario).issubset(
            self.costs_by_scenario
        ):
            raise ValueError("dependency releases reference unknown scenarios")

    @classmethod
    def from_timed_scenarios(
        cls,
        package: PredictiveActionPackage,
        timelines: Mapping[str, TimedScenario],
        *,
        unlock_invocation_ids: tuple[str, ...],
        service_lag_invocation_ids: tuple[str, ...] = (),
        other_cost: ScenarioCost,
    ) -> "PackageScenarioEvaluation":
        if not unlock_invocation_ids:
            raise ValueError("timed evaluation requires an action-unlock target")
        return cls(
            package=package,
            costs_by_scenario={
                scenario_id: _scenario_cost_from_timeline(
                    timeline,
                    unlock_invocation_ids=unlock_invocation_ids,
                    service_lag_invocation_ids=service_lag_invocation_ids,
                )
                for scenario_id, timeline in timelines.items()
            },
            other_cost=other_cost,
            dependency_release_offsets_by_scenario={
                scenario_id: dict(timeline.dependency_release_offsets_ms)
                for scenario_id, timeline in timelines.items()
            },
        )


@dataclass(frozen=True)
class ScenarioRiskPlannerConfig:
    benefit_margin_ms: float = 0.0
    cvar_alpha: float = 0.9
    risk_budget_ms: float = 10.0
    minimum_future_feasibility_probability: float = 0.95
    hbm_shadow_price_ms_per_byte_ms: float = 0.0
    host_shadow_price_ms_per_byte_ms: float = 0.0
    pcie_shadow_price: float = 0.0

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.benefit_margin_ms)
            or not math.isfinite(self.risk_budget_ms)
            or min(self.benefit_margin_ms, self.risk_budget_ms) < 0
        ):
            raise ValueError("risk planner margins must be finite and non-negative")
        if not 0 < self.cvar_alpha < 1:
            raise ValueError("CVaR alpha must be in (0, 1)")
        if not 0 <= self.minimum_future_feasibility_probability <= 1:
            raise ValueError("future feasibility probability must be in [0, 1]")
        if min(
            self.hbm_shadow_price_ms_per_byte_ms,
            self.host_shadow_price_ms_per_byte_ms,
            self.pcie_shadow_price,
        ) < 0:
            raise ValueError("resource shadow prices must be non-negative")


@dataclass(frozen=True)
class PackageRiskSummary:
    package_id: str
    expected_benefit_ms: float
    expected_recourse_credit_ms: float
    cvar_regret_ms: float
    future_feasibility_probability: float
    future_hbm_feasibility_probability: float
    worst_future_hbm_peak_bytes: int
    worst_future_hbm_overflow_bytes: int
    eligible: bool
    reasons: tuple[str, ...]
    recourse_diagnostics: tuple[PrepareRecourseDiagnostic, ...] = ()


@dataclass(frozen=True)
class ScenarioRiskDecision:
    selected_package_id: str
    baseline_package_id: str
    summaries: tuple[PackageRiskSummary, ...]


@dataclass(frozen=True)
class RiskPlanningTelemetry:
    candidate_generation_ms: float
    scenario_evaluation_ms: tuple[float, ...]
    full_plan_ms: float
    publish_age_ms: float = 0.0
    safe_point_validation_ms: float = 0.0
    rollout_cache_hits: int = 0
    rollout_cache_misses: int = 0

    def __post_init__(self) -> None:
        timings = (
            self.candidate_generation_ms,
            self.full_plan_ms,
            self.publish_age_ms,
            self.safe_point_validation_ms,
            *self.scenario_evaluation_ms,
        )
        if any(not math.isfinite(item) or item < 0 for item in timings):
            raise ValueError("risk planning timings must be finite and non-negative")
        if min(self.rollout_cache_hits, self.rollout_cache_misses) < 0:
            raise ValueError("rollout cache counts must be non-negative")


@dataclass(frozen=True)
class PredictivePlanEnvelope:
    """Atomic P6 publication; actions remain independently grouped."""

    envelope_id: str
    belief: FrontierBeliefSnapshot
    source_joint_plan_id: str
    selected_package_id: str
    action_groups: tuple[ActionGroup, ...]
    generated_ts_ms: float
    telemetry: RiskPlanningTelemetry

    def __post_init__(self) -> None:
        if (
            not self.envelope_id
            or not self.source_joint_plan_id
            or not self.selected_package_id
            or self.generated_ts_ms < 0
        ):
            raise ValueError("predictive plan envelope identity/time is invalid")
        group_ids = [item.group_id for item in self.action_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("predictive action group IDs must be unique")
        expected_evidence = {
            ("belief_id", self.belief.belief_id),
            ("model_version", self.belief.evidence_read_set.model_version),
        }
        for group in self.action_groups:
            if not expected_evidence.issubset(set(group.evidence_read_set)):
                raise ValueError(
                    "predictive action group does not reference envelope belief/model"
                )


class ScenarioRiskPlanner:
    """Select a bounded P6 package without changing the P5 execution path."""

    def __init__(self, config: ScenarioRiskPlannerConfig | None = None) -> None:
        self.config = config or ScenarioRiskPlannerConfig()

    def select(
        self,
        belief: FrontierBeliefSnapshot,
        baseline: PackageScenarioEvaluation,
        candidates: tuple[PackageScenarioEvaluation, ...],
    ) -> ScenarioRiskDecision:
        if baseline.package.action != PredictiveActionKind.OBSERVED_BASELINE:
            raise ValueError("risk planning requires the observed A0 baseline")
        expected_ids = {item.scenario_id for item in belief.scenarios}
        self._validate_scenario_coverage(baseline, expected_ids)
        summaries: list[PackageRiskSummary] = []
        best_package_id = baseline.package.package_id
        best_benefit = 0.0
        for candidate in candidates:
            if candidate.package.action == PredictiveActionKind.OBSERVED_BASELINE:
                raise ValueError("candidate cannot duplicate observed baseline")
            self._validate_scenario_coverage(candidate, expected_ids)
            summary = self._summarize(belief, baseline, candidate)
            summaries.append(summary)
            if summary.eligible and summary.expected_benefit_ms > best_benefit:
                best_benefit = summary.expected_benefit_ms
                best_package_id = candidate.package.package_id
        return ScenarioRiskDecision(
            selected_package_id=best_package_id,
            baseline_package_id=baseline.package.package_id,
            summaries=tuple(summaries),
        )

    @staticmethod
    def _validate_scenario_coverage(
        evaluation: PackageScenarioEvaluation,
        expected_ids: set[str],
    ) -> None:
        if set(evaluation.costs_by_scenario) != expected_ids:
            raise ValueError("package evaluation must cover every global scenario")

    def _summarize(
        self,
        belief: FrontierBeliefSnapshot,
        baseline: PackageScenarioEvaluation,
        candidate: PackageScenarioEvaluation,
    ) -> PackageRiskSummary:
        weighted_regrets: list[tuple[float, float]] = []
        expected_benefit = 0.0
        expected_recourse_credit = 0.0
        feasible_probability = 0.0
        hbm_feasible_probability = 0.0
        worst_hbm_peak = 0
        worst_hbm_overflow = 0
        reasons: list[str] = []
        all_deterministic = True
        all_liveness = True
        for scenario in belief.scenarios:
            base_cost = baseline.costs_by_scenario[scenario.scenario_id]
            candidate_cost = candidate.costs_by_scenario[scenario.scenario_id]
            base_loss = self._loss(base_cost)
            candidate_loss = self._loss(candidate_cost)
            probability = scenario.probability_mass
            expected_benefit += probability * (base_loss - candidate_loss)
            expected_recourse_credit += (
                probability * candidate_cost.recourse_credit_ms
            )
            weighted_regrets.append((max(0.0, candidate_loss - base_loss), probability))
            all_deterministic &= candidate_cost.deterministic_feasible
            all_liveness &= candidate_cost.liveness_path_proven
            if candidate_cost.future_feasible:
                feasible_probability += probability
            if candidate_cost.future_hbm_feasible:
                hbm_feasible_probability += probability
            worst_hbm_peak = max(
                worst_hbm_peak, candidate_cost.future_hbm_peak_bytes
            )
            worst_hbm_overflow = max(
                worst_hbm_overflow, candidate_cost.future_hbm_overflow_bytes
            )

        other_probability = belief.other_probability_mass
        base_other_loss = self._loss(baseline.other_cost)
        candidate_other_loss = self._loss(candidate.other_cost)
        expected_benefit += other_probability * (
            base_other_loss - candidate_other_loss
        )
        expected_recourse_credit += (
            other_probability * candidate.other_cost.recourse_credit_ms
        )
        weighted_regrets.append(
            (max(0.0, candidate_other_loss - base_other_loss), other_probability)
        )
        all_deterministic &= candidate.other_cost.deterministic_feasible
        all_liveness &= candidate.other_cost.liveness_path_proven
        if candidate.other_cost.future_feasible:
            feasible_probability += other_probability
        if candidate.other_cost.future_hbm_feasible:
            hbm_feasible_probability += other_probability
        worst_hbm_peak = max(
            worst_hbm_peak, candidate.other_cost.future_hbm_peak_bytes
        )
        worst_hbm_overflow = max(
            worst_hbm_overflow, candidate.other_cost.future_hbm_overflow_bytes
        )

        if not all_deterministic:
            reasons.append("deterministic_hard_constraint")
        if not all_liveness:
            reasons.append("restore_liveness_path_unproven")
        prepare_diagnostics = tuple(
            candidate.recourse_diagnostics_by_scenario.values()
        )
        if (
            candidate.package.action == PredictiveActionKind.PREPARE_HOST
            and prepare_diagnostics
        ):
            probability_by_scenario = {
                scenario.scenario_id: scenario.probability_mass
                for scenario in belief.scenarios
            }
            expected_stall = sum(
                probability_by_scenario.get(diagnostic.scenario_id, 0.0)
                * diagnostic.shape_aware_stall_p90_ms
                for diagnostic in prepare_diagnostics
            )
            pressure_diagnostics = tuple(
                diagnostic
                for diagnostic in prepare_diagnostics
                if diagnostic.first_pressure_ms is not None
            )
            if not all(
                diagnostic.transfer_shape_supported
                for diagnostic in prepare_diagnostics
            ):
                reasons.append("shape_unsupported")
            if pressure_diagnostics and not any(
                diagnostic.morphology_slack_ms is not None
                and diagnostic.morphology_slack_ms > 0
                for diagnostic in pressure_diagnostics
            ):
                reasons.append("morphology_window_miss")
            if expected_recourse_credit <= expected_stall:
                reasons.append("insufficient_recourse_after_stall")
        if (
            candidate.package.action != PredictiveActionKind.PREPARE_HOST
            and belief.other_probability_mass > 0
            and not belief.other_policy.finite_risk_bound
        ):
            reasons.append("other_has_no_finite_risk_bound")
        if expected_benefit <= self.config.benefit_margin_ms:
            reasons.append("insufficient_expected_benefit")
        cvar = self._weighted_cvar(weighted_regrets, self.config.cvar_alpha)
        if cvar >= self.config.risk_budget_ms:
            reasons.append("cvar_risk_budget")
        if feasible_probability < self.config.minimum_future_feasibility_probability:
            reasons.append("future_chance_constraint")
        # PREPARE_HOST only creates a CPU shadow and retains the GPU copy. A
        # baseline future-HBM overflow is therefore diagnostic for recourse,
        # not a safety failure of the non-destructive prepare itself. Actions
        # that add or rearrange GPU residency must still satisfy this gate.
        if (
            candidate.package.action != PredictiveActionKind.PREPARE_HOST
            and hbm_feasible_probability
            < self.config.minimum_future_feasibility_probability
        ):
            reasons.append("future_hbm_chance_constraint")
        probability_by_scenario = {
            scenario.scenario_id: scenario.probability_mass
            for scenario in belief.scenarios
        }
        recourse_diagnostics = tuple(
            replace(
                diagnostic,
                probability_mass=probability_by_scenario.get(scenario_id, 0.0),
            )
            for scenario_id, diagnostic in sorted(
                candidate.recourse_diagnostics_by_scenario.items()
            )
        )
        return PackageRiskSummary(
            package_id=candidate.package.package_id,
            expected_benefit_ms=expected_benefit,
            expected_recourse_credit_ms=expected_recourse_credit,
            cvar_regret_ms=cvar,
            future_feasibility_probability=feasible_probability,
            future_hbm_feasibility_probability=hbm_feasible_probability,
            worst_future_hbm_peak_bytes=worst_hbm_peak,
            worst_future_hbm_overflow_bytes=worst_hbm_overflow,
            eligible=not reasons,
            reasons=tuple(reasons),
            recourse_diagnostics=recourse_diagnostics,
        )

    def _loss(self, cost: ScenarioCost) -> float:
        return cost.loss(
            hbm_shadow_price_ms_per_byte_ms=(
                self.config.hbm_shadow_price_ms_per_byte_ms
            ),
            host_shadow_price_ms_per_byte_ms=(
                self.config.host_shadow_price_ms_per_byte_ms
            ),
            pcie_shadow_price=self.config.pcie_shadow_price,
        )

    @staticmethod
    def _weighted_cvar(
        weighted_values: list[tuple[float, float]], alpha: float
    ) -> float:
        tail_mass = 1.0 - alpha
        if tail_mass <= 0:
            return max((value for value, _ in weighted_values), default=0.0)
        remaining = tail_mass
        tail_sum = 0.0
        for value, probability in sorted(weighted_values, reverse=True):
            if probability <= 0:
                continue
            consumed = min(remaining, probability)
            tail_sum += value * consumed
            remaining -= consumed
            if remaining <= 1e-12:
                break
        return tail_sum / max(tail_mass - remaining, 1e-12)


def _scenario_cost_from_timeline(
    timeline: TimedScenario,
    *,
    unlock_invocation_ids: tuple[str, ...],
    service_lag_invocation_ids: tuple[str, ...],
) -> ScenarioCost:
    completion = {
        item.invocation_id: item.completion_offset_ms
        for item in timeline.invocation_outcomes
    }
    unlock_values = [completion.get(item) for item in unlock_invocation_ids]
    unlock_resolved = all(item is not None for item in unlock_values)
    lag_values = [
        completion.get(item)
        for item in service_lag_invocation_ids
        if completion.get(item) is not None
    ]
    return ScenarioCost(
        action_unlock_delay_ms=(
            max(float(item) for item in unlock_values if item is not None)
            if unlock_resolved
            else 0.0
        ),
        workflow_service_lag_ms=(
            max(float(item) for item in lag_values) if lag_values else 0.0
        ),
        residual_hbm_time_byte_ms=timeline.residual_hbm_time_byte_ms,
        residual_pcie_time_ms=timeline.pcie_busy_ms,
        future_hbm_peak_bytes=timeline.future_hbm_peak_bytes,
        future_hbm_overflow_bytes=timeline.future_hbm_overflow_bytes,
        future_hbm_feasible=timeline.future_hbm_feasible,
        deterministic_feasible=(
            timeline.deterministic_feasible and unlock_resolved
        ),
        future_feasible=timeline.future_feasible and unlock_resolved,
        liveness_path_proven=timeline.liveness_path_proven,
    )
