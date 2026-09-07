from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.control.data_consumers import ObservedDataConsumerIndex
from beliefkv.core.config import BeliefKVConfig
from beliefkv.core.events import RuntimeEvent
from beliefkv.policy.admission import AdmissionController
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.policy.joint_scheduler import JointPlan, ObservedJointPlanner
from beliefkv.policy.leases import CausalLeaseProjector
from beliefkv.policy.predictive_joint import (
    ActionLocalPhysicalOverlay,
    BeneficiaryOpportunityProbe,
)
from beliefkv.policy.risk_shadow import (
    PredictiveEligibility,
    PredictiveRiskShadowObserver,
    PredictiveRiskShadowResult,
)
from beliefkv.policy.reference import (
    CapabilityReport,
    MetadataSource,
    MetadataValue,
    PolicyInput,
    RunnableInvocation,
    RuntimeGraphSnapshot,
)
from beliefkv.policy.reference.snapshot_builder import (
    PolicyInputSnapshotBuilder,
    SnapshotBuildStats,
)
from beliefkv.policy.resource_snapshot import RuntimeResourceObservation
from beliefkv.policy.service_curve import TransferServiceCurve
from beliefkv.policy.transfer_cost import PCIeCostModel
from beliefkv.policy.workflow_fairness import WorkflowFairScheduler
from beliefkv.runtime.page_index import (
    ContextPageReplica,
    PageIndexReplicaDelta,
    PageOwnershipIndex,
    PhysicalPageReplica,
    PhysicalPageStateReplica,
)
from beliefkv.runtime.protocol import PageHandle, TransferTelemetry
from beliefkv.predictor.frontier_belief import PredictiveEvidenceReadSet
from beliefkv.predictor.structured_frontier import LocalFrontierFeatures


class JointPlanProducer(Protocol):
    def plan(self, policy_input: PolicyInput) -> JointPlan:
        ...


class JointShadowWorkClass(str, Enum):
    """Cost classes carried by one safe-point publication."""

    SEMANTIC_DELTA = "semantic_delta"
    JOINT_REPLAN = "joint_replan"
    RISK_EVAL = "risk_eval"


@dataclass(frozen=True)
class WorkflowFairnessReplica:
    workflow_id: str
    weight: float
    attained_service_ms: float
    virtual_runtime_ms: float
    dispatch_count: int


@dataclass(frozen=True)
class FrontierFeatureSource:
    """Small scheduler-to-worker replica for candidate-local prediction."""

    invocation_id: str
    boundary_history: tuple[str, ...] = ()
    context_tokens: int = 0
    generated_tokens: int = 0
    backend_class: str = "unknown"
    command_class: str = "unknown"

    def __post_init__(self) -> None:
        if not self.invocation_id:
            raise ValueError("frontier feature source requires an invocation id")
        if self.context_tokens < 0 or self.generated_tokens < 0:
            raise ValueError("frontier feature source token counts must be non-negative")

    def materialize(
        self,
        invocation: object,
        *,
        now_ms: float,
    ) -> LocalFrontierFeatures:
        state = getattr(invocation, "state")
        tool_family = getattr(invocation, "active_tool_family", None) or "unknown"
        tool_start_ms = getattr(invocation, "active_tool_start_ms", None)
        elapsed_wait_ms = 0.0
        if state == InvocationState.WAIT_TOOL and tool_start_ms is not None:
            elapsed_wait_ms = max(0.0, now_ms - float(tool_start_ms))
        return LocalFrontierFeatures(
            invocation_id=self.invocation_id,
            state=state.value,
            agent_definition_id=str(
                getattr(invocation, "agent_definition_id", "unknown") or "unknown"
            ),
            boundary_history=self.boundary_history,
            tool_family=str(tool_family),
            backend_class=self.backend_class,
            command_class=self.command_class,
            generated_tokens=self.generated_tokens,
            elapsed_wait_ms=elapsed_wait_ms,
            current_sequence_tokens=self.context_tokens,
        )


@dataclass(frozen=True)
class ObservedSeedBeneficiaryHint:
    """One deferred request selected by the latest bounded observed seed."""

    plan_id: str
    request_id: str
    invocation_id: str
    context_id: str
    context_epoch: int
    startup_bytes: int
    growth_bytes: int
    seed_generation: int = 0
    created_ts_ms: float = 0.0
    published_ts_ms: float | None = None

    def __post_init__(self) -> None:
        if not all(
            (self.plan_id, self.request_id, self.invocation_id, self.context_id)
        ):
            raise ValueError("observed seed beneficiary identity must be non-empty")
        if min(
            self.context_epoch,
            self.startup_bytes,
            self.growth_bytes,
            self.seed_generation,
            self.created_ts_ms,
        ) < 0:
            raise ValueError("observed seed beneficiary values must be non-negative")
        if self.published_ts_ms is not None and self.published_ts_ms < 0:
            raise ValueError("observed seed beneficiary publish time must be non-negative")

    @property
    def signature(self) -> tuple[object, ...]:
        return (
            self.request_id,
            self.context_id,
            self.context_epoch,
            self.startup_bytes,
            self.growth_bytes,
            self.seed_generation,
        )

    @property
    def risk_signature(self) -> tuple[object, ...]:
        """Material action inputs, excluding the bounded-seed revision."""

        return (
            self.request_id,
            self.context_id,
            self.context_epoch,
            self.startup_bytes,
            self.growth_bytes,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "request_id": self.request_id,
            "invocation_id": self.invocation_id,
            "context_id": self.context_id,
            "context_epoch": self.context_epoch,
            "startup_bytes": self.startup_bytes,
            "seed_generation": self.seed_generation,
            "created_ts_ms": self.created_ts_ms,
            "published_ts_ms": self.published_ts_ms,
            "growth_bytes": self.growth_bytes,
        }


@dataclass(frozen=True)
class ActionLocalPhysicalOverlayBatch:
    """Latest bounded live physical evidence for one beneficiary risk key."""

    beneficiary_risk_signature: tuple[object, ...]
    opportunity: BeneficiaryOpportunityProbe
    overlays: tuple[ActionLocalPhysicalOverlay, ...] = ()
    selection_reason: str | None = None
    capture_ms: float = 0.0

    def __post_init__(self) -> None:
        if not self.beneficiary_risk_signature:
            raise ValueError("overlay batch requires a beneficiary signature")
        if self.beneficiary_risk_signature[0] != (
            self.opportunity.beneficiary_request_id
        ):
            raise ValueError("overlay batch beneficiary does not match its probe")
        if len(self.overlays) > 2:
            raise ValueError("overlay batch supports at most two victims")
        context_ids = tuple(item.context_id for item in self.overlays)
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("overlay victim contexts must be unique")
        if self.capture_ms < 0:
            raise ValueError("overlay capture time must be non-negative")
        if self.selection_reason is not None and not self.selection_reason:
            raise ValueError("overlay selection reason must be non-empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "beneficiary_risk_signature": list(
                self.beneficiary_risk_signature
            ),
            "opportunity": self.opportunity.to_dict(),
            "overlays": [item.to_dict() for item in self.overlays],
            "selection_reason": self.selection_reason,
            "capture_ms": self.capture_ms,
        }


@dataclass(frozen=True)
class JointShadowStateStamp:
    graph_version: int
    consumer_version: int
    event_sequence: int
    page_revision: int
    topology_revision: int
    fairness_revision: int
    transfer_epoch: int
    runnable_signature: tuple[tuple[object, ...], ...]
    hbm_used_bytes: int
    host_free_bytes: int
    obligation_revision: int = 0
    lease_revision: int = 0
    grace_revision: int = 0
    parser_frontier_revision: int = 0
    admission_revision: int = 0


@dataclass(frozen=True)
class JointShadowDelta:
    """Immutable safe-point publication consumed only by the shadow worker."""

    event_from_sequence: int
    event_to_sequence: int
    runtime_events: tuple[RuntimeEvent, ...]
    page_delta: PageIndexReplicaDelta
    observation: RuntimeResourceObservation
    runnable_frontier: tuple[RunnableInvocation, ...]
    fairness_accounts: tuple[WorkflowFairnessReplica, ...]
    external_workflow_charges: tuple[tuple[str, float], ...]
    control_state: Mapping[str, object]
    transfer_telemetry: tuple[TransferTelemetry, ...]
    capabilities: CapabilityReport
    stamp: JointShadowStateStamp
    trigger: str
    captured_monotonic_ms: float
    planning_requested: bool = True
    risk_evaluation_requested: bool = False
    risk_trigger_signature: tuple[tuple[str, str, str, int], ...] = ()
    observed_seed_beneficiary: ObservedSeedBeneficiaryHint | None = None
    action_local_overlay_batch: (
        ActionLocalPhysicalOverlayBatch | None
    ) = None
    action_local_overlay_replaced: bool = False
    source_page_revision: int | None = None
    source_topology_revision: int | None = None
    frontier_predictions: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    frontier_features: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    frontier_feature_sources: tuple[FrontierFeatureSource, ...] = ()
    removed_frontier_invocation_ids: frozenset[str] = frozenset()
    frontier_model_version: str | None = None

    def __post_init__(self) -> None:
        if self.event_to_sequence < self.event_from_sequence:
            raise ValueError("shadow event sequence cannot move backwards")
        if not self.trigger:
            raise ValueError("shadow delta trigger must be non-empty")
        if self.captured_monotonic_ms < 0:
            raise ValueError("shadow capture time must be non-negative")
        object.__setattr__(
            self,
            "frontier_predictions",
            MappingProxyType(
                {
                    str(invocation_id): dict(prediction)
                    for invocation_id, prediction in sorted(
                        self.frontier_predictions.items()
                    )
                }
            ),
        )
        object.__setattr__(
            self,
            "frontier_features",
            MappingProxyType(
                {
                    str(invocation_id): dict(features)
                    for invocation_id, features in sorted(
                        self.frontier_features.items()
                    )
                }
            ),
        )
        source_ids = tuple(item.invocation_id for item in self.frontier_feature_sources)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("frontier feature source ids must be unique")
        object.__setattr__(
            self,
            "frontier_feature_sources",
            tuple(self.frontier_feature_sources),
        )
        object.__setattr__(
            self,
            "removed_frontier_invocation_ids",
            frozenset(str(item) for item in self.removed_frontier_invocation_ids),
        )
        if self.frontier_model_version is not None and not self.frontier_model_version:
            raise ValueError("frontier model version must be non-empty")
        for risk_class, event_kind, invocation_id, context_epoch in (
            self.risk_trigger_signature
        ):
            if risk_class not in {"prepare", "reentry"}:
                raise ValueError("unknown predictive risk trigger class")
            if not event_kind or not invocation_id or context_epoch < 0:
                raise ValueError("invalid predictive risk trigger identity")
        if self.source_page_revision is not None and self.source_page_revision < 0:
            raise ValueError("source page revision must be non-negative")
        if (
            self.source_topology_revision is not None
            and self.source_topology_revision < 0
        ):
            raise ValueError("source topology revision must be non-negative")


    @property
    def work_classes(self) -> frozenset[JointShadowWorkClass]:
        result = {JointShadowWorkClass.SEMANTIC_DELTA}
        if self.planning_requested:
            result.add(JointShadowWorkClass.JOINT_REPLAN)
        if self.risk_evaluation_requested:
            result.add(JointShadowWorkClass.RISK_EVAL)
        return frozenset(result)


def coalesce_joint_shadow_deltas(
    deltas: tuple[JointShadowDelta, ...],
) -> JointShadowDelta:
    """Collapse contiguous publications to their final physical replica state.

    RCCG events and transfer telemetry remain lossless. Page/context replacement
    records use last-write-wins semantics, so the worker does not replay every
    intermediate allocator quantum before building one latest-wins snapshot.
    """

    if not deltas:
        raise ValueError("at least one shadow delta is required")
    if len(deltas) == 1:
        return deltas[0]

    first = deltas[0]
    event_cursor = first.event_from_sequence
    page_cursor = first.page_delta.from_revision
    events: list[RuntimeEvent] = []
    telemetry: list[TransferTelemetry] = []
    pages: dict[PageHandle, PhysicalPageReplica] = {}
    page_states: dict[PageHandle, PhysicalPageStateReplica] = {}
    contexts: dict[str, ContextPageReplica] = {}
    changed_handles: set[PageHandle] = set()
    changed_context_ids: set[str] = set()
    components: set[str] = set()
    full_rebuild = False
    frontier_predictions: dict[str, Mapping[str, object]] = {}
    frontier_features: dict[str, Mapping[str, object]] = {}
    frontier_feature_sources: dict[str, FrontierFeatureSource] = {}
    removed_frontier_ids: set[str] = set()
    risk_triggers: set[tuple[str, str, str, int]] = set()

    for delta in deltas:
        if delta.event_from_sequence != event_cursor:
            raise RuntimeError("cannot coalesce shadow deltas with an RCCG gap")
        if delta.page_delta.from_revision != page_cursor:
            raise RuntimeError("cannot coalesce shadow deltas with a page gap")
        event_cursor = delta.event_to_sequence
        page_cursor = delta.page_delta.to_revision
        events.extend(delta.runtime_events)
        telemetry.extend(delta.transfer_telemetry)

        page_delta = delta.page_delta
        if page_delta.full_rebuild_required:
            full_rebuild = True
            pages = {item.handle: item for item in page_delta.pages}
            page_states = {
                item.handle: item for item in page_delta.page_states
            }
            contexts = {item.context_id: item for item in page_delta.contexts}
            changed_handles = set(page_delta.changed_handles)
            changed_context_ids = set(page_delta.changed_context_ids)
        else:
            for item in page_delta.pages:
                pages[item.handle] = item
                page_states.pop(item.handle, None)
            for item in page_delta.page_states:
                page_states[item.handle] = item
            for context_id in page_delta.changed_context_ids:
                contexts.pop(context_id, None)
            for item in page_delta.contexts:
                contexts[item.context_id] = item
            changed_handles.update(page_delta.changed_handles)
            changed_context_ids.update(page_delta.changed_context_ids)
        components.update(page_delta.components)
        for invocation_id in delta.removed_frontier_invocation_ids:
            frontier_predictions.pop(invocation_id, None)
            frontier_features.pop(invocation_id, None)
            frontier_feature_sources.pop(invocation_id, None)
            removed_frontier_ids.add(invocation_id)
        for invocation_id, prediction in delta.frontier_predictions.items():
            removed_frontier_ids.discard(invocation_id)
            frontier_predictions[invocation_id] = prediction
        for invocation_id, features in delta.frontier_features.items():
            removed_frontier_ids.discard(invocation_id)
            frontier_features[invocation_id] = features
        for source in delta.frontier_feature_sources:
            removed_frontier_ids.discard(source.invocation_id)
            frontier_feature_sources[source.invocation_id] = source
        risk_triggers.update(delta.risk_trigger_signature)

    last = deltas[-1]
    page_delta = PageIndexReplicaDelta(
        from_revision=first.page_delta.from_revision,
        to_revision=last.page_delta.to_revision,
        topology_revision=last.page_delta.topology_revision,
        pages=tuple(pages[handle] for handle in sorted(pages)),
        page_states=tuple(
            page_states[handle] for handle in sorted(page_states)
        ),
        contexts=tuple(contexts[key] for key in sorted(contexts)),
        changed_handles=frozenset(changed_handles),
        changed_context_ids=frozenset(changed_context_ids),
        components=frozenset(components),
        full_rebuild_required=full_rebuild,
    )
    overlay_update = next(
        (
            item
            for item in reversed(deltas)
            if item.action_local_overlay_replaced
        ),
        None,
    )
    return JointShadowDelta(
        event_from_sequence=first.event_from_sequence,
        event_to_sequence=last.event_to_sequence,
        runtime_events=tuple(events),
        page_delta=page_delta,
        observation=last.observation,
        runnable_frontier=last.runnable_frontier,
        fairness_accounts=last.fairness_accounts,
        external_workflow_charges=last.external_workflow_charges,
        control_state=last.control_state,
        transfer_telemetry=tuple(telemetry),
        capabilities=last.capabilities,
        stamp=last.stamp,
        trigger=last.trigger,
        captured_monotonic_ms=last.captured_monotonic_ms,
        planning_requested=any(
            item.planning_requested for item in deltas
        ),
        risk_evaluation_requested=any(
            item.risk_evaluation_requested for item in deltas
        ),
        risk_trigger_signature=tuple(sorted(risk_triggers)),
        observed_seed_beneficiary=last.observed_seed_beneficiary,
        action_local_overlay_batch=(
            overlay_update.action_local_overlay_batch
            if overlay_update is not None
            else None
        ),
        action_local_overlay_replaced=overlay_update is not None,
        frontier_predictions=frontier_predictions,
        source_page_revision=last.source_page_revision,
        source_topology_revision=last.source_topology_revision,
        frontier_features=frontier_features,
        frontier_feature_sources=tuple(frontier_feature_sources.values()),
        removed_frontier_invocation_ids=frozenset(removed_frontier_ids),
        frontier_model_version=last.frontier_model_version,
    )


@dataclass(frozen=True)
class JointShadowSubmission:
    sequence: int
    snapshot_id: str
    submitted_monotonic_ms: float
    enqueue_ms: float
    replaced_sequence: int | None


@dataclass(frozen=True)
class JointShadowResult:
    sequence: int
    snapshot_id: str
    submitted_monotonic_ms: float
    started_monotonic_ms: float
    completed_monotonic_ms: float
    plan: JointPlan | None
    error: str | None
    policy_input: PolicyInput | None = None
    snapshot_build_ms: float = 0.0
    snapshot_delta_apply_ms: float = 0.0
    snapshot_materialize_ms: float = 0.0
    state_stamp: JointShadowStateStamp | None = None
    trigger: str = "legacy_policy_input"
    trigger_interval_ms: float | None = None
    planning_budget_ms: float | None = None
    predictive_shadow: PredictiveRiskShadowResult | None = None
    predictive_shadow_error: str | None = None
    predictive_shadow_compute_ms: float = 0.0
    risk_evaluation_requested: bool = False
    planning_attempted: bool = True
    risk_funnel_reason: str | None = None

    @property
    def queue_wait_ms(self) -> float:
        return max(0.0, self.started_monotonic_ms - self.submitted_monotonic_ms)

    @property
    def compute_ms(self) -> float:
        return max(0.0, self.completed_monotonic_ms - self.started_monotonic_ms)


@dataclass(frozen=True)
class JointShadowWorkerStats:
    submitted_count: int
    started_count: int
    completed_count: int
    apply_only_count: int
    failed_count: int
    dropped_pending_count: int
    coalesced_pending_count: int
    superseded_result_count: int
    pending_count: int
    busy: bool
    latest_published_sequence: int

    def to_dict(self) -> dict[str, int | bool]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class PredictiveRiskSubmission:
    sequence: int
    source_joint_sequence: int
    source_snapshot_id: str
    submitted_monotonic_ms: float
    enqueue_ms: float
    enqueued: bool
    replaced_sequence: int | None
    suppression_reason: str | None = None


@dataclass(frozen=True)
class PredictiveRiskWorkerResult:
    sequence: int
    source_joint_sequence: int
    source_snapshot_id: str
    submitted_monotonic_ms: float
    started_monotonic_ms: float
    completed_monotonic_ms: float
    policy_input: PolicyInput
    shadow: PredictiveRiskShadowResult | None
    error: str | None
    eligibility_ms: float
    eligibility: PredictiveEligibility | None = None
    suppression_reason: str | None = None
    counterfactual_shadow: PredictiveRiskShadowResult | None = None
    counterfactual_error: str | None = None

    @property
    def queue_wait_ms(self) -> float:
        return max(0.0, self.started_monotonic_ms - self.submitted_monotonic_ms)

    @property
    def compute_ms(self) -> float:
        return max(0.0, self.completed_monotonic_ms - self.started_monotonic_ms)


@dataclass(frozen=True)
class _WorkItem:
    sequence: int
    submitted_monotonic_ms: float
    policy_input: PolicyInput | None = None
    deltas: tuple[JointShadowDelta, ...] = ()


@dataclass(frozen=True)
class _PredictiveWorkItem:
    sequence: int
    source_joint_sequence: int
    submitted_monotonic_ms: float
    policy_input: PolicyInput
    source_plan: JointPlan
    state_stamp: JointShadowStateStamp


class IncrementalPolicyInputAssembler:
    """Worker-owned RCCG/page mirrors and PolicyInput builder."""

    def __init__(self, config: BeliefKVConfig) -> None:
        self.config = config
        self.graph = RuntimeCausalContextGraph()
        self.data_consumers = ObservedDataConsumerIndex(self.graph)
        self.page_index = PageOwnershipIndex()
        fairness = WorkflowFairScheduler()
        frontier = CausalFrontierScheduler(self.graph)
        self.admission = AdmissionController(
            self.page_index,
            fairness,
            frontier,
            reserve_hbm_bytes=0,
        )
        self.leases = CausalLeaseProjector(self.graph)
        self.service_curve = TransferServiceCurve(
            PCIeCostModel(
                bandwidth_gbps=config.pcie_bandwidth_gbps,
                overhead_ms=config.transfer_overhead_ms,
            ),
            window=config.service_curve_window,
            min_samples=config.service_curve_min_samples,
        )
        if config.transfer_service_model_path:
            self.service_curve.warm_start(
                Path(config.transfer_service_model_path),
                expected_hardware_key=config.transfer_service_hardware_key,
            )
            self.service_curve.validate_warm_start_contract()
        self.transfer_service_contract = self.service_curve.warm_start_contract()
        self.builder = PolicyInputSnapshotBuilder(
            self.graph,
            self.data_consumers,
            self.page_index,
            self.admission,
            self.leases,
            self.service_curve,
        )
        self._event_sequence = 0
        self._latest: JointShadowDelta | None = None
        self._frontier_predictions: dict[str, Mapping[str, object]] = {}
        self._frontier_features: dict[str, Mapping[str, object]] = {}
        self._frontier_feature_sources: dict[str, FrontierFeatureSource] = {}
        self._healthy = True
        self._telemetry: deque[TransferTelemetry] = deque(
            maxlen=max(256, config.service_curve_window)
        )
        self._physical_mirror_observed_ts_ms: float | None = None
        self._action_local_overlay_batch: (
            ActionLocalPhysicalOverlayBatch | None
        ) = None

    @property
    def last_stats(self) -> SnapshotBuildStats | None:
        return self.builder.last_stats

    def apply(self, delta: JointShadowDelta) -> None:
        if not self._healthy:
            raise RuntimeError("shadow mirror was discarded after an apply failure")
        if delta.event_from_sequence != self._event_sequence:
            raise RuntimeError(
                "shadow RCCG event gap: "
                f"{delta.event_from_sequence} != {self._event_sequence}"
            )
        if delta.page_delta.full_rebuild_required and self.page_index.revision != 0:
            raise RuntimeError("shadow page journal gap requires fail-closed restart")
        try:
            physical_mirror_changed = bool(
                delta.page_delta.full_rebuild_required
                or delta.page_delta.to_revision != delta.page_delta.from_revision
                or delta.page_delta.pages
                or delta.page_delta.page_states
                or delta.page_delta.contexts
            )
            if delta.runtime_events:
                # These events were committed atomically at the scheduler safe
                # point. The worker mirror is disposable, so rollback must not
                # deep-copy the complete RCCG for every incremental batch.
                self.graph.apply_batch(delta.runtime_events, atomic=False)
                self.data_consumers.apply_batch(delta.runtime_events, atomic=False)
            self._event_sequence = delta.event_to_sequence
            self.page_index.apply_replica_delta(
                delta.page_delta,
                full_validation=False,
                validate_delta=not self.config.performance_mode,
            )
            for telemetry in delta.transfer_telemetry:
                self.service_curve.observe(telemetry)
                self._telemetry.append(telemetry)
            for invocation_id in delta.removed_frontier_invocation_ids:
                self._frontier_predictions.pop(invocation_id, None)
                self._frontier_features.pop(invocation_id, None)
                self._frontier_feature_sources.pop(invocation_id, None)
            self._frontier_predictions.update(delta.frontier_predictions)
            self._frontier_features.update(delta.frontier_features)
            if delta.action_local_overlay_replaced:
                self._action_local_overlay_batch = (
                    delta.action_local_overlay_batch
                )
            for source in delta.frontier_feature_sources:
                self._frontier_feature_sources[source.invocation_id] = source
            if self.graph.graph_version != delta.stamp.graph_version:
                raise RuntimeError(
                    "shadow graph version diverged from safe-point publication"
                )
            if self.data_consumers.version != delta.stamp.consumer_version:
                raise RuntimeError(
                    "shadow consumer version diverged from safe-point publication"
                )
            if physical_mirror_changed or self._physical_mirror_observed_ts_ms is None:
                self._physical_mirror_observed_ts_ms = delta.observation.ts_ms
            self._latest = delta
        except Exception:
            self._healthy = False
            self._latest = None
            raise

    def build(self) -> PolicyInput:
        delta = self._latest
        if delta is None:
            raise RuntimeError("shadow assembler has no safe-point state")
        charges = self.page_index.workflow_gpu_charges()
        for workflow_id, amount in delta.external_workflow_charges:
            charges[workflow_id] = charges.get(workflow_id, 0.0) + amount
        fairness_state = {
            "accounts": {
                item.workflow_id: {
                    "weight": item.weight,
                    "attained_service_ms": item.attained_service_ms,
                    "virtual_runtime_ms": item.virtual_runtime_ms,
                    "dispatch_count": item.dispatch_count,
                }
                for item in delta.fairness_accounts
            },
            "revision": delta.stamp.fairness_revision,
        }
        policy_input = self.builder.build(
            delta.observation,
            additional_runnable=delta.runnable_frontier,
            workflow_memory_charges=charges,
            workflow_fairness_state=fairness_state,
            control_state=delta.control_state,
            transfer_telemetry=tuple(self._telemetry),
            include_transfer_estimates=False,
            physical_summary_only=(
                self.config.performance_mode
                or self.config.predictive_risk_shadow_enabled
                or self.config.predictive_joint_overlay_enabled
            ),
            capabilities=delta.capabilities,
        )
        predictive_compact = (
            self.config.predictive_risk_shadow_enabled
            or self.config.predictive_joint_overlay_enabled
        )
        if self._frontier_predictions and not predictive_compact:
            metadata = dict(policy_input.optional_metadata)
            metadata["frontier_predictions"] = MetadataValue(
                source=MetadataSource.PREDICTED,
                value=dict(self._frontier_predictions),
                producer="frontier_belief_mvp",
            )
            policy_input = replace(
                policy_input,
                optional_metadata=metadata,
            )
        if self._frontier_features and not predictive_compact:
            metadata = dict(policy_input.optional_metadata)
            metadata["frontier_features"] = MetadataValue(
                source=MetadataSource.OBSERVED,
                value=dict(self._frontier_features),
                producer="frontier_online_feature_snapshot",
            )
            policy_input = replace(
                policy_input,
                optional_metadata=metadata,
            )
        if delta.frontier_model_version:
            metadata = dict(policy_input.optional_metadata)
            metadata["frontier_prediction_model_version"] = MetadataValue(
                source=MetadataSource.PREDICTED,
                value=delta.frontier_model_version,
                producer="frontier_belief_model",
            )
            policy_input = replace(policy_input, optional_metadata=metadata)
        return policy_input

    def refresh_predictive_semantics(
        self,
        policy_input: PolicyInput,
        *,
        risk_trigger_signature: tuple[tuple[str, str, str, int], ...],
    ) -> PolicyInput:
        """Refresh only action-relevant semantics around a cached observed seed."""

        delta = self._latest
        if delta is None:
            raise RuntimeError("shadow assembler has no safe-point state")
        snapshot_id = (
            f"{policy_input.snapshot_id}-risk-{delta.event_to_sequence:08d}"
        )
        graph_state = dict(policy_input.runtime_graph.state)
        base_rccg = graph_state.get("rccg", {})
        if not isinstance(base_rccg, Mapping):
            base_rccg = {}
        rccg = dict(base_rccg)
        invocations = dict(
            base_rccg.get("invocations", {})
            if isinstance(base_rccg.get("invocations", {}), Mapping)
            else {}
        )
        contexts = dict(
            base_rccg.get("contexts", {})
            if isinstance(base_rccg.get("contexts", {}), Mapping)
            else {}
        )
        joins = dict(
            base_rccg.get("joins", {})
            if isinstance(base_rccg.get("joins", {}), Mapping)
            else {}
        )
        closure_ids = {
            invocation_id
            for _risk_class, _event_kind, invocation_id, _context_epoch
            in risk_trigger_signature
        }
        pending = list(closure_ids)
        while pending:
            invocation_id = pending.pop()
            invocation = self.graph.invocations.get(invocation_id)
            if invocation is None:
                invocations.pop(invocation_id, None)
                continue
            invocations[invocation_id] = self.graph.invocation_snapshot(
                invocation_id
            )
            contexts[invocation.context_id] = self.graph.context_snapshot(
                invocation.context_id
            )
            related = {
                invocation.parent_invocation_id,
                invocation.return_target_id,
                *invocation.child_invocation_ids,
                *invocation.blocking_child_ids,
            }
            if invocation.join_id:
                join = self.graph.joins.get(invocation.join_id)
                if join is not None:
                    joins[invocation.join_id] = self.graph.join_snapshot(
                        invocation.join_id
                    )
                    related.update(join.member_invocation_ids)
                    related.update(join.waiter_invocation_ids)
            for related_id in related:
                if related_id and related_id not in closure_ids:
                    closure_ids.add(related_id)
                    pending.append(related_id)
        rccg["graph_version"] = self.graph.graph_version
        rccg["invocations"] = invocations
        rccg["contexts"] = contexts
        rccg["joins"] = joins
        graph_state["rccg"] = rccg
        graph_state["control"] = dict(delta.control_state)
        resources = policy_input.resources
        hbm_used_bytes = min(
            delta.observation.hbm_used_bytes,
            max(
                0,
                delta.observation.hbm_capacity_bytes
                - resources.hbm_reserved_bytes,
            ),
        )
        metadata = dict(policy_input.optional_metadata)
        metadata["beliefkv_predictive_risk_trigger"] = MetadataValue(
            source=MetadataSource.OBSERVED,
            value={
                "events": risk_trigger_signature,
                "event_sequence": delta.event_to_sequence,
                "closure_invocation_ids": tuple(sorted(closure_ids)),
            },
            producer="event_driven_risk_trigger",
        )
        if delta.observed_seed_beneficiary is None:
            metadata.pop("beliefkv_observed_seed_beneficiary", None)
        else:
            metadata["beliefkv_observed_seed_beneficiary"] = MetadataValue(
                source=MetadataSource.OBSERVED,
                value=delta.observed_seed_beneficiary.to_dict(),
                producer="bounded_observed_seed",
            )
        overlay_batch = self._action_local_overlay_batch
        if (
            delta.observed_seed_beneficiary is not None
            and overlay_batch is not None
            and overlay_batch.beneficiary_risk_signature
            == delta.observed_seed_beneficiary.risk_signature
        ):
            metadata["beliefkv_action_local_physical_overlay"] = MetadataValue(
                source=MetadataSource.OBSERVED,
                value=overlay_batch.to_dict(),
                producer="safe_point_action_local_overlay",
            )
        else:
            metadata.pop("beliefkv_action_local_physical_overlay", None)
        return replace(
            policy_input,
            runtime_graph=RuntimeGraphSnapshot(
                snapshot_id=snapshot_id,
                graph_version=self.graph.graph_version,
                observed_ts_ms=delta.observation.ts_ms,
                state=graph_state,
            ),
            physical_kv=replace(
                policy_input.physical_kv,
                snapshot_id=snapshot_id,
                gpu_bytes=delta.observation.hbm_used_bytes,
                cpu_bytes=delta.observation.host_used_bytes,
            ),
            resources=replace(
                resources,
                snapshot_id=snapshot_id,
                ts_ms=delta.observation.ts_ms,
                hbm_capacity_bytes=delta.observation.hbm_capacity_bytes,
                hbm_used_bytes=hbm_used_bytes,
                host_free_bytes=delta.observation.host_free_bytes,
                urgent_d2h_bytes=delta.observation.urgent_d2h_bytes,
                urgent_h2d_bytes=delta.observation.urgent_h2d_bytes,
                pcie_utilization=(
                    delta.observation.pcie_utilization
                    if delta.observation.pcie_utilization is not None
                    else resources.pcie_utilization
                ),
                gpu_compute_utilization=(
                    delta.observation.gpu_compute_utilization
                    if delta.observation.gpu_compute_utilization is not None
                    else resources.gpu_compute_utilization
                ),
            ),
            runnable_frontier=delta.runnable_frontier,
            optional_metadata=metadata,
        )

    def _refresh_candidate_graph_closure(
        self,
        policy_input: PolicyInput,
        seed_invocation_ids: tuple[str, ...],
    ) -> PolicyInput:
        graph_state = dict(policy_input.runtime_graph.state)
        base_rccg = graph_state.get("rccg", {})
        if not isinstance(base_rccg, Mapping):
            base_rccg = {}
        rccg = dict(base_rccg)
        invocations = dict(
            base_rccg.get("invocations", {})
            if isinstance(base_rccg.get("invocations", {}), Mapping)
            else {}
        )
        contexts = dict(
            base_rccg.get("contexts", {})
            if isinstance(base_rccg.get("contexts", {}), Mapping)
            else {}
        )
        joins = dict(
            base_rccg.get("joins", {})
            if isinstance(base_rccg.get("joins", {}), Mapping)
            else {}
        )
        closure_ids = set(seed_invocation_ids)
        pending = list(closure_ids)
        while pending:
            invocation_id = pending.pop()
            invocation = self.graph.invocations.get(invocation_id)
            if invocation is None:
                invocations.pop(invocation_id, None)
                continue
            invocations[invocation_id] = self.graph.invocation_snapshot(
                invocation_id
            )
            contexts[invocation.context_id] = self.graph.context_snapshot(
                invocation.context_id
            )
            related = {
                invocation.parent_invocation_id,
                invocation.return_target_id,
                *invocation.child_invocation_ids,
                *invocation.blocking_child_ids,
            }
            if invocation.join_id:
                join = self.graph.joins.get(invocation.join_id)
                if join is not None:
                    joins[invocation.join_id] = self.graph.join_snapshot(
                        invocation.join_id
                    )
                    related.update(join.member_invocation_ids)
                    related.update(join.waiter_invocation_ids)
            for related_id in related:
                if related_id and related_id not in closure_ids:
                    closure_ids.add(related_id)
                    pending.append(related_id)
        rccg["graph_version"] = self.graph.graph_version
        rccg["invocations"] = invocations
        rccg["contexts"] = contexts
        rccg["joins"] = joins
        graph_state["rccg"] = rccg
        return replace(
            policy_input,
            runtime_graph=replace(
                policy_input.runtime_graph,
                graph_version=self.graph.graph_version,
                state=graph_state,
            ),
        )

    def materialize_predictive_candidates(
        self,
        policy_input: PolicyInput,
        source_plan: JointPlan,
        *,
        max_victims: int = 2,
    ) -> tuple[PolicyInput, bool, str | None]:
        """Attach a bounded physical overlay for one projected beneficiary."""

        seed_hint_metadata = policy_input.optional_metadata.get(
            "beliefkv_observed_seed_beneficiary"
        )
        seed_hint = (
            seed_hint_metadata.value
            if seed_hint_metadata is not None
            and isinstance(seed_hint_metadata.value, Mapping)
            else {}
        )
        beneficiary_request_id = str(seed_hint.get("request_id") or "") or (
            source_plan.projected_beneficiary_request_id
        )
        request_by_id = {
            request.request_id: request
            for request in policy_input.runnable_frontier
        }
        beneficiary = (
            request_by_id.get(beneficiary_request_id)
            if beneficiary_request_id is not None
            else None
        )
        if beneficiary is None:
            return policy_input, False, "no_beneficiary_hint"
        overlay_metadata = policy_input.optional_metadata.get(
            "beliefkv_action_local_physical_overlay"
        )
        overlay_batch = (
            overlay_metadata.value
            if overlay_metadata is not None
            and isinstance(overlay_metadata.value, Mapping)
            else {}
        )
        raw_overlays = overlay_batch.get("overlays", ())
        overlay_rows = tuple(
            item for item in raw_overlays if isinstance(item, Mapping)
        )
        opportunity = overlay_batch.get("opportunity", {})
        if isinstance(opportunity, Mapping) and not bool(
            opportunity.get("hbm_opportunity_possible", True)
        ):
            return (
                policy_input,
                False,
                (
                    "beneficiary_slot_only"
                    if opportunity.get("beneficiary_slot_blocked")
                    else "beneficiary_capacity_available"
                ),
            )
        overlay_selection_reason = str(
            overlay_batch.get("selection_reason") or ""
        ) or None
        trigger_metadata = policy_input.optional_metadata.get(
            "beliefkv_predictive_risk_trigger"
        )
        trigger_value = (
            trigger_metadata.value
            if trigger_metadata is not None
            and isinstance(trigger_metadata.value, Mapping)
            else {}
        )
        raw_trigger_events = trigger_value.get("events", ())
        trigger_events = tuple(
            tuple(item)
            for item in raw_trigger_events
            if isinstance(item, (tuple, list)) and len(item) == 4
        )
        summary_metadata = policy_input.optional_metadata.get(
            "beliefkv_context_physical_summaries"
        )
        summaries = (
            summary_metadata.value
            if summary_metadata is not None
            and isinstance(summary_metadata.value, Mapping)
            else {}
        )
        graph_state = policy_input.runtime_graph.state
        nested = graph_state.get("rccg")
        if isinstance(nested, Mapping):
            graph_state = nested
        invocations = graph_state.get("invocations", {})
        if not isinstance(invocations, Mapping):
            invocations = {}
        wait_states = {
            InvocationState.WAIT_TOOL.value,
            InvocationState.WAIT_CHILD.value,
            InvocationState.WAIT_JOIN.value,
            InvocationState.WAIT_MESSAGE.value,
        }
        parked_contexts = {
            str(raw.get("context_id"))
            for raw in invocations.values()
            if isinstance(raw, Mapping)
            and str(raw.get("state") or "") in wait_states
            and raw.get("context_id")
        }
        trigger_contexts: dict[str, str] = {}
        for raw in trigger_events:
            risk_class, _event_kind, invocation_id, _context_epoch = raw
            invocation = invocations.get(str(invocation_id))
            if not isinstance(invocation, Mapping):
                continue
            context_id = str(invocation.get("context_id") or "")
            if context_id:
                trigger_contexts[context_id] = str(risk_class)
        ranked_victims: list[tuple[int, int, float, str]] = []
        if overlay_rows:
            victim_context_ids = tuple(
                str(raw.get("context_id"))
                for raw in overlay_rows[:max_victims]
                if raw.get("context_id")
            )
        else:
            for context_id in parked_contexts:
                if context_id == beneficiary.context_id:
                    continue
                raw = summaries.get(context_id)
                if not isinstance(raw, Mapping):
                    continue
                reclaimable = int(
                    raw.get("exclusive_reclaimable_upper_bound_bytes", 0)
                )
                if reclaimable <= 0:
                    continue
                ranked_victims.append(
                    (
                        -reclaimable,
                        int(raw.get("locked_bytes", 0)),
                        float(raw.get("last_access_ms", 0.0)),
                        context_id,
                    )
                )
            triggered_victims = tuple(
                sorted(
                    context_id
                    for context_id, risk_class in trigger_contexts.items()
                    if risk_class == "prepare"
                    and context_id in parked_contexts
                )
            )
            victim_context_ids = tuple(
                dict.fromkeys(
                    (
                        *triggered_victims,
                        *(item[3] for item in sorted(ranked_victims)),
                    )
                )
            )[:max_victims]
        reentry_context_ids = tuple(
            sorted(
                context_id
                for context_id, risk_class in trigger_contexts.items()
                if risk_class == "reentry"
            )
        )
        if not victim_context_ids and not reentry_context_ids:
            reason = overlay_selection_reason
            if reason is None:
                reason = (
                    "no_victim_context_selected"
                    if not parked_contexts
                    else "victim_zero_reclaimable_bytes"
                )
            return policy_input, False, reason
        context_ids = tuple(
            dict.fromkeys(
                (
                    *((beneficiary.context_id,) if beneficiary is not None else ()),
                    *reentry_context_ids,
                    *victim_context_ids,
                )
            )
        )
        candidate_seed_ids = {beneficiary.invocation_id}
        for context_id in context_ids:
            context = self.graph.contexts.get(context_id)
            if context is not None:
                candidate_seed_ids.update(context.invocation_ids)
        slot_witness = next(
            (
                request_by_id[request_id].invocation_id
                for request_id in source_plan.execution.ordered_request_ids
                if request_id in request_by_id
            ),
            None,
        )
        if slot_witness is not None:
            candidate_seed_ids.add(slot_witness)
        policy_input = self._refresh_candidate_graph_closure(
            policy_input,
            tuple(sorted(candidate_seed_ids)),
        )
        graph_state = policy_input.runtime_graph.state
        nested = graph_state.get("rccg")
        if isinstance(nested, Mapping):
            graph_state = nested
        invocations = graph_state.get("invocations", {})
        if not isinstance(invocations, Mapping):
            invocations = {}
        delta = self._latest
        if delta is None:
            return policy_input, False, "candidate_physicalization_failed"
        physical_context_ids = (
            reentry_context_ids if overlay_rows else context_ids
        )
        bundles = (
            self.builder.targeted_context_bundles(
                physical_context_ids,
                now_ms=delta.observation.ts_ms,
            )
            if physical_context_ids
            else ()
        )
        if physical_context_ids and not bundles:
            source_revision = (
                delta.source_page_revision
                if delta.source_page_revision is not None
                else delta.stamp.page_revision
            )
            reason = (
                "victim_bundle_generation_stale"
                if source_revision > self.page_index.revision
                else "victim_missing_from_physical_mirror"
            )
            return policy_input, False, reason
        metadata = dict(policy_input.optional_metadata)
        if bundles:
            metadata["beliefkv_transfer_service_estimates"] = MetadataValue(
                source=MetadataSource.OBSERVED,
                value=self.builder.targeted_transfer_service_estimates(
                    bundles,
                    delta.observation,
                ),
                producer="candidate_local_transfer_service_curve",
            )
        overlay_by_context = {
            str(raw.get("context_id")): raw
            for raw in overlay_rows
            if raw.get("context_id")
        }
        if overlay_by_context:
            victim_generations = tuple(
                sorted(
                    (
                        context_id,
                        (
                            str(
                                overlay_by_context[context_id].get(
                                    "generation_fingerprint"
                                )
                            ),
                        ),
                    )
                    for context_id in victim_context_ids
                )
            )
            physical_revision = max(
                int(raw.get("page_revision", 0))
                for raw in overlay_by_context.values()
            )
            topology_revision = max(
                int(raw.get("topology_revision", 0))
                for raw in overlay_by_context.values()
            )
            physical_age_ms = max(
                0.0,
                delta.observation.ts_ms
                - min(
                    float(raw.get("captured_ts_ms", delta.observation.ts_ms))
                    for raw in overlay_by_context.values()
                ),
            )
            source_revision = physical_revision
        else:
            victim_generations = tuple(
                sorted(
                    (
                        context_id,
                        tuple(
                            sorted(
                                bundle.generation_fingerprint
                                for bundle in bundles
                                if context_id in bundle.owner_context_ids
                            )
                        ),
                    )
                    for context_id in victim_context_ids
                )
            )
            physical_revision = self.page_index.revision
            topology_revision = delta.stamp.topology_revision
            source_revision = (
                delta.source_page_revision
                if delta.source_page_revision is not None
                else delta.stamp.page_revision
            )
            physical_age_ms = max(
                0.0,
                delta.observation.ts_ms
                - float(
                    self._physical_mirror_observed_ts_ms
                    if self._physical_mirror_observed_ts_ms is not None
                    else delta.observation.ts_ms
                ),
            )
        metadata["beliefkv_predictive_candidate_scope"] = MetadataValue(
            source=MetadataSource.OBSERVED,
            value={
                "beneficiary_request_id": (
                    beneficiary.request_id if beneficiary is not None else None
                ),
                "beneficiary_context_id": (
                    beneficiary.context_id if beneficiary is not None else None
                ),
                "bounded_seed_plan_id": seed_hint.get("plan_id"),
                "victim_context_ids": victim_context_ids,
                "reentry_context_ids": reentry_context_ids,
                "victim_generations": victim_generations,
                "page_revision": physical_revision,
                "topology_revision": topology_revision,
                "physical_mirror_page_revision": physical_revision,
                "source_page_revision": source_revision,
                "page_revision_lag": max(0, source_revision - physical_revision),
                "physical_mirror_age_ms": physical_age_ms,
                "physical_source": (
                    "action_local_overlay"
                    if overlay_by_context
                    else "worker_page_mirror"
                ),
                "overlay_capture_ms": float(overlay_batch.get("capture_ms", 0.0)),
                "beneficiary_opportunity": (
                    dict(opportunity) if isinstance(opportunity, Mapping) else {}
                ),
            },
            producer="candidate_local_physicalizer",
        )
        candidate_invocation_ids = {
            str(invocation_id)
            for invocation_id, raw in invocations.items()
            if isinstance(raw, Mapping)
            and str(raw.get("context_id") or "") in context_ids
        }
        joins = graph_state.get("joins", {})
        if not isinstance(joins, Mapping):
            joins = {}
        changed = True
        while changed:
            changed = False
            expanded = set(candidate_invocation_ids)
            for invocation_id in tuple(candidate_invocation_ids):
                raw = invocations.get(invocation_id)
                if not isinstance(raw, Mapping):
                    continue
                for related in (
                    raw.get("parent_invocation_id"),
                    raw.get("return_target_id"),
                    *tuple(raw.get("children") or ()),
                    *tuple(raw.get("blocking_children") or ()),
                ):
                    if related:
                        expanded.add(str(related))
                join_id = raw.get("join_id")
                join = joins.get(str(join_id)) if join_id else None
                if isinstance(join, Mapping):
                    expanded.update(str(item) for item in join.get("members", ()))
                    expanded.update(str(item) for item in join.get("waiters", ()))
            if expanded != candidate_invocation_ids:
                candidate_invocation_ids = expanded
                changed = True
        local_predictions = {
            invocation_id: self._frontier_predictions[invocation_id]
            for invocation_id in sorted(candidate_invocation_ids)
            if invocation_id in self._frontier_predictions
        }
        active_tool_count = sum(
            1
            for raw in invocations.values()
            if isinstance(raw, Mapping)
            and str(raw.get("state") or "")
            == InvocationState.WAIT_TOOL.value
        )
        active_family_counts = Counter(
            str(raw.get("active_tool_family"))
            for raw in invocations.values()
            if isinstance(raw, Mapping)
            and str(raw.get("state") or "")
            == InvocationState.WAIT_TOOL.value
            and raw.get("active_tool_family")
        )
        local_features: dict[str, Mapping[str, object]] = {}
        for invocation_id in sorted(candidate_invocation_ids):
            cached = self._frontier_features.get(invocation_id)
            source = self._frontier_feature_sources.get(invocation_id)
            invocation = self.graph.invocations.get(invocation_id)
            if source is not None and invocation is not None:
                current = source.materialize(
                    invocation,
                    now_ms=delta.observation.ts_ms,
                ).to_dict()
            elif cached is not None:
                current = dict(cached)
            else:
                continue
            current["active_tool_count"] = active_tool_count
            tool_family = str(current.get("tool_family") or "unknown")
            current["backend_pressure"] = (
                f"active_family:{active_family_counts.get(tool_family, 0)}"
                if tool_family != "unknown"
                else "unknown"
            )
            local_features[invocation_id] = current
        if local_predictions:
            metadata["frontier_predictions"] = MetadataValue(
                source=MetadataSource.PREDICTED,
                value=local_predictions,
                producer="candidate_local_frontier_prediction",
            )
        if local_features:
            metadata["frontier_features"] = MetadataValue(
                source=MetadataSource.OBSERVED,
                value=local_features,
                producer="candidate_local_frontier_features",
            )
        return (
            replace(
                policy_input,
                physical_kv=replace(
                    policy_input.physical_kv,
                    bundles=bundles,
                ),
                optional_metadata=metadata,
            ),
            True,
            None,
        )


class LatestWinsJointPlanWorker:
    """Capacity-one asynchronous planner with failure isolation.

    A pending snapshot is replaced by a newer snapshot. The item currently
    being evaluated is never cancelled, and the worker never reaches into live
    scheduler state.
    """

    def __init__(
        self,
        planner: JointPlanProducer | None = None,
        *,
        assembler: IncrementalPolicyInputAssembler | None = None,
        thread_name: str = "beliefkv-joint-shadow",
    ) -> None:
        self.planner = planner or ObservedJointPlanner()
        self.assembler = assembler
        self._incremental_mode = assembler is not None
        self._condition = threading.Condition()
        self._pending: _WorkItem | None = None
        self._latest: JointShadowResult | None = None
        self._closed = False
        self._busy = False
        self._next_sequence = 0
        self._submitted_count = 0
        self._started_count = 0
        self._completed_count = 0
        self._apply_only_count = 0
        self._failed_count = 0
        self._dropped_pending_count = 0
        self._coalesced_pending_count = 0
        self._superseded_result_count = 0
        self._last_trigger_capture_ms: float | None = None
        self._planning_dirty = False
        self._mirror_failed = False
        self._cached_observed_policy_input: PolicyInput | None = None
        self._cached_observed_plan: JointPlan | None = None
        self._last_risk_action_signature: tuple[object, ...] | None = None
        self._risk_dirty = False
        self._risk_trigger_signatures: set[tuple[str, str, str, int]] = set()
        self._thread = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self._thread.start()

    def submit(self, policy_input: PolicyInput) -> JointShadowSubmission:
        enqueue_started_ns = time.perf_counter_ns()
        submitted_ms = _monotonic_ms()
        with self._condition:
            if self._closed:
                raise RuntimeError("joint shadow worker is closed")
            self._next_sequence += 1
            sequence = self._next_sequence
            replaced = self._pending.sequence if self._pending is not None else None
            if replaced is not None:
                self._dropped_pending_count += 1
            self._pending = _WorkItem(
                sequence=sequence,
                submitted_monotonic_ms=submitted_ms,
                policy_input=policy_input,
            )
            self._submitted_count += 1
            self._condition.notify()
        return JointShadowSubmission(
            sequence=sequence,
            snapshot_id=policy_input.snapshot_id,
            submitted_monotonic_ms=submitted_ms,
            enqueue_ms=(time.perf_counter_ns() - enqueue_started_ns) / 1_000_000.0,
            replaced_sequence=replaced,
        )

    @property
    def supports_incremental_delta(self) -> bool:
        # Keep the runtime on the incremental fail-closed path after a mirror
        # failure. Returning False here would silently reactivate legacy full
        # PolicyInput capture on the scheduler thread.
        return self._incremental_mode

    def submit_delta(self, delta: JointShadowDelta) -> JointShadowSubmission:
        if self.assembler is None:
            raise RuntimeError("joint shadow worker has no incremental assembler")
        enqueue_started_ns = time.perf_counter_ns()
        submitted_ms = _monotonic_ms()
        with self._condition:
            if self._closed:
                raise RuntimeError("joint shadow worker is closed")
            if self._mirror_failed:
                raise RuntimeError("joint shadow worker mirror failed closed")
            self._next_sequence += 1
            sequence = self._next_sequence
            replaced = self._pending.sequence if self._pending is not None else None
            pending_deltas: tuple[JointShadowDelta, ...] = ()
            if self._pending is not None:
                if self._pending.policy_input is not None:
                    raise RuntimeError(
                        "cannot mix legacy snapshots and incremental deltas"
                    )
                pending_deltas = self._pending.deltas
                self._coalesced_pending_count += 1
            self._pending = _WorkItem(
                sequence=sequence,
                submitted_monotonic_ms=submitted_ms,
                deltas=pending_deltas + (delta,),
            )
            self._submitted_count += 1
            self._condition.notify()
        return JointShadowSubmission(
            sequence=sequence,
            snapshot_id=f"pending-shadow-delta-{sequence:08d}",
            submitted_monotonic_ms=submitted_ms,
            enqueue_ms=(time.perf_counter_ns() - enqueue_started_ns) / 1_000_000.0,
            replaced_sequence=replaced,
        )

    def latest(self, *, after_sequence: int = 0) -> JointShadowResult | None:
        with self._condition:
            if self._latest is None or self._latest.sequence <= after_sequence:
                return None
            return self._latest

    def stats(self) -> JointShadowWorkerStats:
        with self._condition:
            return JointShadowWorkerStats(
                submitted_count=self._submitted_count,
                started_count=self._started_count,
                completed_count=self._completed_count,
                apply_only_count=self._apply_only_count,
                failed_count=self._failed_count,
                dropped_pending_count=self._dropped_pending_count,
                coalesced_pending_count=self._coalesced_pending_count,
                superseded_result_count=self._superseded_result_count,
                pending_count=int(self._pending is not None),
                busy=self._busy,
                latest_published_sequence=(
                    self._latest.sequence if self._latest is not None else 0
                ),
            )

    def close(self, *, timeout_s: float = 5.0) -> bool:
        if timeout_s < 0:
            raise ValueError("worker close timeout must be non-negative")
        with self._condition:
            if not self._closed:
                self._closed = True
                if self._pending is not None:
                    self._pending = None
                    self._dropped_pending_count += 1
                self._condition.notify_all()
        self._thread.join(timeout=timeout_s)
        return not self._thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                item = self._pending
                self._pending = None
                self._busy = True
                self._started_count += 1
            assert item is not None
            started_ms = _monotonic_ms()
            plan = None
            error = None
            policy_input = item.policy_input
            snapshot_build_ms = 0.0
            snapshot_delta_apply_ms = 0.0
            snapshot_materialize_ms = 0.0
            state_stamp = None
            trigger = "legacy_policy_input"
            trigger_interval_ms = None
            planning_budget_ms = None
            planning_attempted = False
            risk_only = False
            risk_consumed = False
            risk_action_signature: tuple[object, ...] | None = None
            publish_result = True
            risk_evaluation_requested = False
            risk_funnel_reason: str | None = None
            try:
                if item.deltas:
                    assert self.assembler is not None
                    apply_started_ns = time.perf_counter_ns()
                    delta = coalesce_joint_shadow_deltas(item.deltas)
                    self.assembler.apply(delta)
                    self._planning_dirty = (
                        self._planning_dirty or delta.planning_requested
                    )
                    self._risk_dirty = (
                        self._risk_dirty or delta.risk_evaluation_requested
                    )
                    self._risk_trigger_signatures.update(
                        delta.risk_trigger_signature
                    )
                    snapshot_delta_apply_ms = (
                        time.perf_counter_ns() - apply_started_ns
                    ) / 1_000_000.0
                    state_stamp = delta.stamp
                    trigger = delta.trigger
                    if self._planning_dirty:
                        materialize_started_ns = time.perf_counter_ns()
                        policy_input = self.assembler.build()
                        snapshot_materialize_ms = (
                            time.perf_counter_ns() - materialize_started_ns
                        ) / 1_000_000.0
                        snapshot_build_ms = (
                            snapshot_delta_apply_ms + snapshot_materialize_ms
                        )
                        if self._last_trigger_capture_ms is not None:
                            trigger_interval_ms = max(
                                0.0,
                                delta.captured_monotonic_ms
                                - self._last_trigger_capture_ms,
                            )
                        self._last_trigger_capture_ms = (
                            delta.captured_monotonic_ms
                        )
                    elif (
                        self._risk_dirty
                        and self._cached_observed_policy_input is not None
                        and self._cached_observed_plan is not None
                    ):
                        materialize_started_ns = time.perf_counter_ns()
                        policy_input = self.assembler.refresh_predictive_semantics(
                            self._cached_observed_policy_input,
                            risk_trigger_signature=tuple(
                                sorted(self._risk_trigger_signatures)
                            ),
                        )
                        risk_consumed = True
                        risk_only = True
                        plan = self._cached_observed_plan
                        (
                            policy_input,
                            risk_evaluation_requested,
                            risk_funnel_reason,
                        ) = self._materialize_predictive_candidates_safely(
                            policy_input,
                            plan,
                        )
                        snapshot_materialize_ms = (
                            time.perf_counter_ns() - materialize_started_ns
                        ) / 1_000_000.0
                        snapshot_build_ms = (
                            snapshot_delta_apply_ms + snapshot_materialize_ms
                        )
                        if risk_evaluation_requested:
                            risk_action_signature = self._risk_action_signature(
                                policy_input
                            )
                            if (
                                risk_action_signature
                                == self._last_risk_action_signature
                            ):
                                risk_evaluation_requested = False
                                risk_funnel_reason = "unchanged_action_signature"
                        else:
                            risk_funnel_reason = (
                                risk_funnel_reason
                                or "candidate_physicalization_failed"
                            )
                    else:
                        if self._risk_dirty:
                            risk_funnel_reason = "no_cached_observed_plan"
                        else:
                            publish_result = False
                if publish_result and not risk_only and risk_funnel_reason is None:
                    planning_attempted = True
                    if policy_input is None:
                        raise RuntimeError(
                            "joint shadow work item has no policy input"
                        )
                    budget_for_trigger = getattr(
                        self.planner, "trigger_budget_ms", None
                    )
                    if callable(budget_for_trigger):
                        planning_budget_ms = budget_for_trigger(
                            trigger_interval_ms
                        )
                        plan = self.planner.plan(
                            policy_input,
                            planning_budget_ms=planning_budget_ms,
                            cancel_check=lambda: self._has_newer_pending(
                                item.sequence
                            ),
                        )
                    else:
                        plan = self.planner.plan(policy_input)
                    if plan is not None:
                        self._cached_observed_policy_input = policy_input
                        self._cached_observed_plan = plan
                    if (
                        plan is not None
                        and item.deltas
                        and self._risk_dirty
                        and self.assembler is not None
                    ):
                        policy_input = self.assembler.refresh_predictive_semantics(
                            policy_input,
                            risk_trigger_signature=tuple(
                                sorted(self._risk_trigger_signatures)
                            ),
                        )
                        (
                            policy_input,
                            risk_evaluation_requested,
                            risk_funnel_reason,
                        ) = self._materialize_predictive_candidates_safely(
                            policy_input,
                            plan,
                        )
                        risk_consumed = True
                        if risk_evaluation_requested:
                            risk_action_signature = self._risk_action_signature(
                                policy_input
                            )
                            if (
                                risk_action_signature
                                == self._last_risk_action_signature
                            ):
                                risk_evaluation_requested = False
                                risk_funnel_reason = "unchanged_action_signature"
                        else:
                            risk_funnel_reason = (
                                risk_funnel_reason
                                or "candidate_physicalization_failed"
                            )
            except Exception as caught:
                error = f"{type(caught).__name__}: {caught}"
                if item.deltas and self.assembler is not None:
                    self._mirror_failed = True
                    self.assembler = None
            completed_ms = _monotonic_ms()
            result = JointShadowResult(
                sequence=item.sequence,
                snapshot_id=(
                    policy_input.snapshot_id
                    if policy_input is not None
                    else f"failed-shadow-work-{item.sequence:08d}"
                ),
                submitted_monotonic_ms=item.submitted_monotonic_ms,
                started_monotonic_ms=started_ms,
                completed_monotonic_ms=completed_ms,
                plan=plan,
                error=error,
                policy_input=policy_input,
                snapshot_build_ms=snapshot_build_ms,
                snapshot_delta_apply_ms=snapshot_delta_apply_ms,
                snapshot_materialize_ms=snapshot_materialize_ms,
                state_stamp=state_stamp,
                trigger=trigger,
                trigger_interval_ms=trigger_interval_ms,
                planning_budget_ms=planning_budget_ms,
                risk_evaluation_requested=risk_evaluation_requested,
                planning_attempted=planning_attempted,
                risk_funnel_reason=risk_funnel_reason,
            )
            with self._condition:
                self._busy = False
                self._completed_count += 1
                if error is not None:
                    self._failed_count += 1
                superseded = (
                    self._pending is not None
                    and self._pending.sequence > result.sequence
                )
                if planning_attempted:
                    self._planning_dirty = superseded
                if risk_consumed and not superseded:
                    self._risk_dirty = False
                    self._risk_trigger_signatures.clear()
                    if risk_action_signature is not None:
                        self._last_risk_action_signature = risk_action_signature
                if not publish_result:
                    self._apply_only_count += 1
                elif superseded:
                    self._superseded_result_count += 1
                elif (
                    self._latest is None
                    or result.sequence > self._latest.sequence
                ):
                    self._latest = result
                self._condition.notify_all()

    def _materialize_predictive_candidates_safely(
        self,
        policy_input: PolicyInput,
        plan: JointPlan,
    ) -> tuple[PolicyInput, bool, str | None]:
        assert self.assembler is not None
        try:
            return self.assembler.materialize_predictive_candidates(
                policy_input,
                plan,
            )
        except Exception:
            return policy_input, False, "candidate_physicalization_failed"

    @staticmethod
    def _risk_action_signature(policy_input: PolicyInput) -> tuple[object, ...]:
        trigger = policy_input.optional_metadata.get(
            "beliefkv_predictive_risk_trigger"
        )
        scope = policy_input.optional_metadata.get(
            "beliefkv_predictive_candidate_scope"
        )
        trigger_value = trigger.value if trigger is not None else {}
        scope_value = scope.value if scope is not None else {}
        if not isinstance(trigger_value, Mapping):
            trigger_value = {}
        if not isinstance(scope_value, Mapping):
            scope_value = {}
        return (
            tuple(tuple(item) for item in trigger_value.get("events", ())),
            scope_value.get("beneficiary_request_id"),
            tuple(scope_value.get("victim_generations", ())),
            policy_input.resources.hbm_available_bytes // (64 << 20),
        )

    def _has_newer_pending(self, sequence: int) -> bool:
        with self._condition:
            return self._pending is not None and self._pending.sequence > sequence

    def __enter__(self) -> "LatestWinsJointPlanWorker":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class LatestWinsPredictiveRiskWorker:
    """Independent, capacity-one predictive worker with cooperative cancel."""

    def __init__(
        self,
        observer: PredictiveRiskShadowObserver,
        *,
        thread_name: str = "beliefkv-predictive-risk",
    ) -> None:
        self.observer = observer
        self._condition = threading.Condition()
        self._pending: _PredictiveWorkItem | None = None
        self._latest: PredictiveRiskWorkerResult | None = None
        self._closed = False
        self._busy = False
        self._next_sequence = 0
        self._submitted_count = 0
        self._started_count = 0
        self._completed_count = 0
        self._failed_count = 0
        self._dropped_pending_count = 0
        self._superseded_result_count = 0
        self._last_trigger_signature: tuple[object, ...] | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        result: JointShadowResult,
    ) -> PredictiveRiskSubmission:
        started_ns = time.perf_counter_ns()
        if (
            result.plan is None
            or result.policy_input is None
            or result.state_stamp is None
        ):
            raise ValueError("predictive risk submission requires a complete observed plan")
        submitted_ms = _monotonic_ms()
        with self._condition:
            if self._closed:
                raise RuntimeError("predictive risk worker is closed")
            self._next_sequence += 1
            replaced = self._pending.sequence if self._pending is not None else None
            if self._pending is not None:
                self._pending = None
                self._dropped_pending_count += 1
            self._pending = _PredictiveWorkItem(
                sequence=self._next_sequence,
                source_joint_sequence=result.sequence,
                submitted_monotonic_ms=submitted_ms,
                policy_input=result.policy_input,
                source_plan=result.plan,
                state_stamp=result.state_stamp,
            )
            enqueued = True
            suppression_reason = None
            sequence = self._next_sequence
            self._submitted_count += 1
            self._condition.notify_all()
        return PredictiveRiskSubmission(
            sequence=sequence,
            source_joint_sequence=result.sequence,
            source_snapshot_id=result.policy_input.snapshot_id,
            submitted_monotonic_ms=submitted_ms,
            enqueue_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
            enqueued=enqueued,
            replaced_sequence=replaced,
            suppression_reason=suppression_reason,
        )

    def latest(self, *, after_sequence: int = 0) -> PredictiveRiskWorkerResult | None:
        with self._condition:
            if self._latest is None or self._latest.sequence <= after_sequence:
                return None
            return self._latest

    def stats(self) -> JointShadowWorkerStats:
        with self._condition:
            return JointShadowWorkerStats(
                submitted_count=self._submitted_count,
                started_count=self._started_count,
                completed_count=self._completed_count,
                apply_only_count=0,
                failed_count=self._failed_count,
                dropped_pending_count=self._dropped_pending_count,
                coalesced_pending_count=0,
                superseded_result_count=self._superseded_result_count,
                pending_count=int(self._pending is not None),
                busy=self._busy,
                latest_published_sequence=(
                    self._latest.sequence if self._latest is not None else 0
                ),
            )

    def close(self, *, timeout_s: float = 5.0) -> bool:
        if timeout_s < 0:
            raise ValueError("worker close timeout must be non-negative")
        with self._condition:
            self._closed = True
            if self._pending is not None:
                self._pending = None
                self._dropped_pending_count += 1
            self._condition.notify_all()
        self._thread.join(timeout=timeout_s)
        return not self._thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                item = self._pending
                self._pending = None
                self._busy = True
                self._started_count += 1
            assert item is not None
            started_ms = _monotonic_ms()
            shadow = None
            error = None
            eligibility = None
            suppression_reason = None
            try:
                eligibility = self.observer.eligibility_index.probe(
                    item.policy_input
                )
                trigger_metadata = item.policy_input.optional_metadata.get(
                    "beliefkv_predictive_risk_trigger"
                )
                scope_metadata = item.policy_input.optional_metadata.get(
                    "beliefkv_predictive_candidate_scope"
                )
                trigger_value = (
                    trigger_metadata.value
                    if trigger_metadata is not None
                    and isinstance(trigger_metadata.value, Mapping)
                    else {}
                )
                scope_value = (
                    scope_metadata.value
                    if scope_metadata is not None
                    and isinstance(scope_metadata.value, Mapping)
                    else {}
                )
                trigger_signature = (
                    (
                        tuple(
                            tuple(item)
                            for item in trigger_value.get("events", ())
                        ),
                        scope_value.get("beneficiary_request_id"),
                        tuple(scope_value.get("victim_generations", ())),
                        eligibility.trigger_signature,
                    )
                    if eligibility.has_candidate
                    else ("no_candidate",)
                )
                with self._condition:
                    if trigger_signature == self._last_trigger_signature:
                        suppression_reason = "unchanged_action_bucket"
                    else:
                        self._last_trigger_signature = trigger_signature
                if not eligibility.has_candidate:
                    suppression_reason = "no_action_specific_candidate"
                if suppression_reason is not None:
                    raise StopIteration
                graph = RuntimeCausalContextGraph.from_snapshot(
                    item.policy_input.runtime_graph.state
                )
                model_metadata = item.policy_input.optional_metadata.get(
                    "frontier_prediction_model_version"
                )
                model_version = (
                    str(model_metadata.value)
                    if model_metadata is not None
                    else "unavailable"
                )
                evidence_read_set = PredictiveEvidenceReadSet(
                    graph_version=item.state_stamp.graph_version,
                    page_revision=item.state_stamp.page_revision,
                    topology_revision=item.state_stamp.topology_revision,
                    fairness_revision=item.state_stamp.fairness_revision,
                    admission_revision=item.state_stamp.admission_revision,
                    transfer_epoch=item.state_stamp.transfer_epoch,
                    obligation_revision=item.state_stamp.obligation_revision,
                    lease_revision=item.state_stamp.lease_revision,
                    grace_revision=item.state_stamp.grace_revision,
                    parser_frontier_revision=(
                        item.state_stamp.parser_frontier_revision
                    ),
                    model_version=model_version,
                )
                shadow = self.observer.evaluate(
                    item.policy_input,
                    graph=graph,
                    source_plan=item.source_plan,
                    eligibility=eligibility,
                    evidence_read_set=evidence_read_set,
                    cancel_check=lambda: self._is_superseded(item.sequence),
                )
            except StopIteration:
                pass
            except Exception as caught:
                error = f"{type(caught).__name__}: {caught}"
            completed_ms = _monotonic_ms()
            worker_result = PredictiveRiskWorkerResult(
                sequence=item.sequence,
                source_joint_sequence=item.source_joint_sequence,
                source_snapshot_id=item.policy_input.snapshot_id,
                submitted_monotonic_ms=item.submitted_monotonic_ms,
                started_monotonic_ms=started_ms,
                completed_monotonic_ms=completed_ms,
                policy_input=item.policy_input,
                shadow=shadow,
                error=error,
                eligibility_ms=(
                    eligibility.probe_ms if eligibility is not None else 0.0
                ),
                eligibility=eligibility,
                suppression_reason=suppression_reason,
                counterfactual_shadow=None,
                counterfactual_error=None,
            )
            with self._condition:
                self._busy = False
                self._completed_count += 1
                if error is not None:
                    self._failed_count += 1
                superseded = self._next_sequence > item.sequence
                if superseded:
                    self._superseded_result_count += 1
                elif self._latest is None or item.sequence > self._latest.sequence:
                    self._latest = worker_result
                self._condition.notify_all()

    def _is_superseded(self, sequence: int) -> bool:
        del sequence
        with self._condition:
            # Eligibility equivalence is known only inside this worker. Do not
            # cancel an in-flight evaluation merely because a newer compact
            # snapshot was enqueued; the completed result is discarded if a
            # materially newer item remains.
            return self._closed


def _monotonic_ms() -> float:
    return time.monotonic_ns() / 1_000_000.0
