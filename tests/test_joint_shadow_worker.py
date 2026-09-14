from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from beliefkv.control.controller import BeliefKVController
from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.core.config import BeliefKVConfig
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.policy.joint_scheduler import (
    JointPlannerConfig,
    ObservedJointPlanner,
)
from beliefkv.policy.reference import MetadataSource, MetadataValue, RunnableInvocation
from beliefkv.policy.predictive_joint import BeneficiaryOpportunityProbe
from beliefkv.policy.resource_snapshot import RuntimeResourceObservation
from beliefkv.runtime.joint_shadow import (
    FrontierFeatureSource,
    ActionLocalPhysicalOverlayBatch,
    IncrementalPolicyInputAssembler,
    JointShadowDelta,
    JointShadowResult,
    JointShadowStateStamp,
    LatestWinsJointPlanWorker,
    LatestWinsPredictiveRiskProcessWorker,
    LatestWinsPredictiveRiskWorker,
    ObservedSeedBeneficiaryHint,
    PredictiveRiskSubmission,
    WorkflowFairnessReplica,
    coalesce_joint_shadow_deltas,
)
from beliefkv.policy.risk_shadow import (
    PredictiveEligibility,
    PrefetchTarget,
)
from beliefkv.runtime.protocol import PageHandle
from tests.test_joint_scheduler import _invocation, _with_runtime_state
from tests.test_whatif_packer import _input


def _policy_input():
    policy_input = _input(capacity=1_500, reserved=0, include_cpu_target=False)
    request = policy_input.runnable_frontier[0]
    return _with_runtime_state(
        policy_input,
        (request,),
        {
            request.invocation_id: _invocation(
                request.workflow_id,
                request.context_id,
            )
        },
    )


def _event(sequence: int, kind: RuntimeEventKind, **kwargs) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"shadow-{sequence}",
        ts_ms=float(sequence),
        kind=kind,
        workflow_id="wf",
        **kwargs,
    )


def _delta(
    controller: BeliefKVController,
    *,
    event_sequence: int,
    page_revision: int,
    ts_ms: float,
    planning_requested: bool = True,
) -> JointShadowDelta:
    events = controller.runtime_events_since(event_sequence)
    pages = controller.page_index.replica_delta_since(page_revision)
    account = controller.fairness.accounts["wf"]
    control_state = controller.policy_control_state(ts_ms)
    observation = RuntimeResourceObservation(
        ts_ms=ts_ms,
        hbm_capacity_bytes=1_000,
        hbm_used_bytes=controller.page_index.gpu_bytes,
        host_capacity_bytes=1_000,
        host_used_bytes=controller.page_index.cpu_bytes,
        host_free_bytes=1_000 - controller.page_index.cpu_bytes,
    )
    return JointShadowDelta(
        event_from_sequence=events.from_sequence,
        event_to_sequence=events.to_sequence,
        runtime_events=events.events,
        page_delta=pages,
        observation=observation,
        runnable_frontier=(),
        fairness_accounts=(
            WorkflowFairnessReplica(
                workflow_id="wf",
                weight=account.weight,
                attained_service_ms=account.attained_service_ms,
                virtual_runtime_ms=account.virtual_runtime,
                dispatch_count=account.dispatch_count,
            ),
        ),
        external_workflow_charges=(),
        control_state=control_state,
        transfer_telemetry=(),
        capabilities=_policy_input().capabilities,
        stamp=JointShadowStateStamp(
            graph_version=controller.graph.graph_version,
            consumer_version=controller.data_consumers.version,
            event_sequence=events.to_sequence,
            page_revision=pages.to_revision,
            topology_revision=pages.topology_revision,
            fairness_revision=controller.fairness.revision,
            transfer_epoch=int(control_state["transfer_epoch"]),
            runnable_signature=(),
            hbm_used_bytes=observation.hbm_used_bytes,
            host_free_bytes=observation.host_free_bytes,
        ),
        trigger="test",
        captured_monotonic_ms=0,
        planning_requested=planning_requested,
    )


def _beneficiary_risk_evidence(
    plan_id: str,
) -> tuple[RunnableInvocation, ObservedSeedBeneficiaryHint]:
    runnable = RunnableInvocation(
        request_id="beneficiary",
        workflow_id="wf",
        invocation_id="beneficiary-invocation",
        context_id="beneficiary-context",
        context_epoch=0,
        submitted_ts_ms=1.0,
        startup_bytes=100,
        admission_startup_bytes=64,
        admission_growth_bytes=32,
        causal_class="engine_waiting:ready",
    )
    hint = ObservedSeedBeneficiaryHint(
        plan_id=plan_id,
        request_id=runnable.request_id,
        invocation_id=runnable.invocation_id,
        context_id=runnable.context_id,
        context_epoch=runnable.context_epoch,
        startup_bytes=64,
        growth_bytes=32,
        seed_generation=1,
        created_ts_ms=2.5,
        published_ts_ms=3.0,
    )
    return runnable, hint

class _BlockingPlanner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.sequences: list[str] = []
        self.delegate = ObservedJointPlanner(
            JointPlannerConfig(max_planning_budget_ms=100.0)
        )

    def plan(self, policy_input):
        self.sequences.append(policy_input.snapshot_id)
        if len(self.sequences) == 1:
            self.started.set()
            assert self.release.wait(timeout=2)
        return self.delegate.plan(policy_input)


class _FailingPlanner:
    def plan(self, policy_input):
        del policy_input
        raise ValueError("expected failure")


class _BlockingFailOncePlanner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.call_count = 0
        self.delegate = ObservedJointPlanner(
            JointPlannerConfig(max_planning_budget_ms=100.0)
        )

    def plan(self, policy_input):
        self.call_count += 1
        if self.call_count == 1:
            self.started.set()
            assert self.release.wait(timeout=2)
            raise ValueError("expected planner failure")
        return self.delegate.plan(policy_input)


class _BlockingFailingAssembler(IncrementalPolicyInputAssembler):
    def __init__(self, config: BeliefKVConfig) -> None:
        super().__init__(config)
        self.started = threading.Event()
        self.release = threading.Event()

    def apply(self, delta: JointShadowDelta) -> None:
        del delta
        self.started.set()
        assert self.release.wait(timeout=2)
        raise RuntimeError("expected mirror failure")


class _CountingPlanner:
    def __init__(self) -> None:
        self.call_count = 0
        self.delegate = ObservedJointPlanner(
            JointPlannerConfig(max_planning_budget_ms=100.0)
        )

    def plan(self, policy_input):
        self.call_count += 1
        return self.delegate.plan(policy_input)


class _FixedEligibilityIndex:
    def probe(self, policy_input):
        return PredictiveEligibility(
            source_snapshot_id=policy_input.snapshot_id,
            prefetch_targets=(
                PrefetchTarget(
                    invocation_id="invocation-target",
                    context_id="ctx-target",
                    state="wait_tool",
                    missing_gpu_bytes=100,
                ),
            ),
            prepare_host_victims=(),
            probe_ms=0.01,
            trigger_signature=(("ctx-target", "wait_tool", 1), (), 1, 1),
            belief_signature=(("fixed",),),
        )


class _BlockingRiskObserver:
    def __init__(self) -> None:
        self.eligibility_index = _FixedEligibilityIndex()
        self.started = threading.Event()
        self.release = threading.Event()

    def evaluate(self, *_args, cancel_check=None, **_kwargs):
        self.started.set()
        while not self.release.wait(timeout=0.01):
            if cancel_check is not None and cancel_check():
                return "cancelled"
        return "risk-result"


class _RecordingRiskObserver:
    def __init__(self, *, selected_action: str) -> None:
        self.eligibility_index = _FixedEligibilityIndex()
        self.selected_action = selected_action
        self.calls: list[tuple[str, int]] = []

    def evaluate(self, policy_input, *, source_plan, **_kwargs):
        self.calls.append((policy_input.snapshot_id, id(source_plan)))
        return SimpleNamespace(selected_action=self.selected_action)


def test_predictive_process_worker_round_trips_candidate_input() -> None:
    policy_input = _policy_input()
    graph_controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    graph_controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    policy_input = replace(
        policy_input,
        runtime_graph=replace(
            policy_input.runtime_graph,
            graph_version=graph_controller.graph.graph_version,
            state=graph_controller.graph.snapshot(),
        ),
    )
    plan = ObservedJointPlanner().plan(policy_input)
    observed_result = JointShadowResult(
        sequence=1,
        snapshot_id=policy_input.snapshot_id,
        submitted_monotonic_ms=1.0,
        started_monotonic_ms=1.0,
        completed_monotonic_ms=1.0,
        plan=plan,
        error=None,
        policy_input=policy_input,
        state_stamp=JointShadowStateStamp(
            graph_version=policy_input.runtime_graph.graph_version,
            consumer_version=0,
            event_sequence=0,
            page_revision=policy_input.physical_kv.allocator_version,
            topology_revision=policy_input.physical_kv.topology_version,
            fairness_revision=0,
            transfer_epoch=0,
            runnable_signature=(),
            hbm_used_bytes=policy_input.resources.hbm_used_bytes,
            host_free_bytes=policy_input.resources.host_free_bytes,
        ),
    )
    worker = LatestWinsPredictiveRiskProcessWorker(
        _RecordingRiskObserver(selected_action="prepare_host")
    )
    submission = worker.submit(observed_result)
    result = None
    for _ in range(500):
        result = worker.latest(after_sequence=submission.sequence - 1)
        if result is not None:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert result.error is None
    assert result.shadow is not None
    assert result.shadow.selected_action == "prepare_host"
    assert result.policy_input is policy_input
    assert result.input_serialize_ms > 0
    assert result.output_deserialize_ms > 0
    stats = worker.stats()
    assert stats.started_count == 1
    assert stats.completed_count == 1
    assert stats.pending_count == 0
    assert not stats.busy
    assert worker.close()


def test_coalesced_delta_keeps_events_and_latest_page_state() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    handle = PageHandle(1, 0)
    controller.page_index.register_page(handle, size_bytes=100)
    controller.page_index.bind_pages("ctx", 0, (handle,))
    initial = _delta(controller, event_sequence=0, page_revision=0, ts_ms=2)

    controller.page_index.set_engine_lock(handle, 1)
    controller.process_runtime_event(
        _event(
            3,
            RuntimeEventKind.CONTEXT_ADVANCE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    second = _delta(
        controller,
        event_sequence=initial.event_to_sequence,
        page_revision=initial.page_delta.to_revision,
        ts_ms=3,
    )
    controller.page_index.set_engine_lock(handle, 2)
    controller.process_runtime_event(
        _event(
            4,
            RuntimeEventKind.CONTEXT_ADVANCE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    third = _delta(
        controller,
        event_sequence=second.event_to_sequence,
        page_revision=second.page_delta.to_revision,
        ts_ms=4,
    )

    merged = coalesce_joint_shadow_deltas((second, third))
    assert tuple(event.event_id for event in merged.runtime_events) == (
        "shadow-3",
        "shadow-4",
    )
    assert merged.page_delta.pages == ()
    assert len(merged.page_delta.page_states) == 1
    assert merged.page_delta.page_states[0].engine_lock_ref == 2
    assert merged.page_delta.contexts == ()

    assembler = IncrementalPolicyInputAssembler(config)
    assembler.apply(initial)
    assembler.apply(merged)
    assert assembler.page_index.require_page(handle).engine_lock_ref == 2
    assert assembler.graph.graph_version == controller.graph.graph_version


def test_graph_snapshot_rebuild_preserves_planning_state() -> None:
    controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )

    rebuilt = RuntimeCausalContextGraph.from_snapshot(
        controller.graph.snapshot()
    )

    assert rebuilt.snapshot() == controller.graph.snapshot()


def test_incremental_assembler_accepts_self_contained_page_rebuild() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    handle = PageHandle(1, 0)
    controller.page_index.register_page(handle, size_bytes=100)
    controller.page_index.bind_pages("ctx", 0, (handle,))
    initial = _delta(controller, event_sequence=0, page_revision=0, ts_ms=2)
    assembler = IncrementalPolicyInputAssembler(config)
    assembler.apply(initial)

    complete_pages = controller.page_index.replica_delta_since(0)
    rebuild = replace(
        initial,
        event_from_sequence=initial.event_to_sequence,
        runtime_events=(),
        page_delta=replace(
            complete_pages,
            from_revision=initial.page_delta.to_revision,
        ),
        trigger="page_journal_rebuild",
    )
    assembler.apply(rebuild)

    assert assembler.page_index.require_page(handle).size_bytes == 100
    assert assembler.page_index.revision == controller.page_index.revision


def test_predictive_worker_cannot_delay_observed_plan_publication() -> None:
    observed_worker = LatestWinsJointPlanWorker()
    policy_input = _policy_input()
    graph_controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    graph_controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    policy_input = replace(
        policy_input,
        runtime_graph=replace(
            policy_input.runtime_graph,
            graph_version=graph_controller.graph.graph_version,
            state=graph_controller.graph.snapshot(),
        ),
    )
    observed_submission = observed_worker.submit(policy_input)
    observed_result = None
    for _ in range(100):
        observed_result = observed_worker.latest(
            after_sequence=observed_submission.sequence - 1
        )
        if observed_result is not None:
            break
        threading.Event().wait(0.01)
    assert observed_result is not None
    observed_result = replace(
        observed_result,
        state_stamp=JointShadowStateStamp(
            graph_version=policy_input.runtime_graph.graph_version,
            consumer_version=0,
            event_sequence=0,
            page_revision=policy_input.physical_kv.allocator_version,
            topology_revision=policy_input.physical_kv.topology_version,
            fairness_revision=0,
            transfer_epoch=0,
            runnable_signature=(),
            hbm_used_bytes=policy_input.resources.hbm_used_bytes,
            host_free_bytes=policy_input.resources.host_free_bytes,
        ),
    )
    risk_observer = _BlockingRiskObserver()
    risk_worker = LatestWinsPredictiveRiskWorker(risk_observer)
    risk_worker.submit(observed_result)
    assert risk_observer.started.wait(timeout=1)

    second_submission = observed_worker.submit(policy_input)
    second_result = None
    for _ in range(100):
        second_result = observed_worker.latest(
            after_sequence=second_submission.sequence - 1
        )
        if second_result is not None:
            break
        threading.Event().wait(0.01)

    assert second_result is not None
    assert second_result.plan is not None
    risk_observer.release.set()
    assert risk_worker.close()
    assert observed_worker.close()


def test_predictive_worker_suppresses_unchanged_bucket_off_scheduler() -> None:
    observed_worker = LatestWinsJointPlanWorker()
    policy_input = _policy_input()
    graph_controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    graph_controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    policy_input = replace(
        policy_input,
        runtime_graph=replace(
            policy_input.runtime_graph,
            graph_version=graph_controller.graph.graph_version,
            state={"rccg": graph_controller.graph.snapshot()},
        ),
    )
    submission = observed_worker.submit(policy_input)
    observed_result = None
    for _ in range(100):
        observed_result = observed_worker.latest(
            after_sequence=submission.sequence - 1
        )
        if observed_result is not None:
            break
        threading.Event().wait(0.01)
    assert observed_result is not None
    assert observed_result.policy_input is not None
    observed_result = replace(
        observed_result,
        state_stamp=JointShadowStateStamp(
            graph_version=observed_result.policy_input.runtime_graph.graph_version,
            consumer_version=0,
            event_sequence=0,
            page_revision=observed_result.policy_input.physical_kv.allocator_version,
            topology_revision=observed_result.policy_input.physical_kv.topology_version,
            fairness_revision=0,
            transfer_epoch=0,
            runnable_signature=(),
            hbm_used_bytes=observed_result.policy_input.resources.hbm_used_bytes,
            host_free_bytes=observed_result.policy_input.resources.host_free_bytes,
        ),
    )
    risk_observer = _BlockingRiskObserver()
    risk_worker = LatestWinsPredictiveRiskWorker(risk_observer)
    first = risk_worker.submit(observed_result)
    assert first.enqueued
    assert risk_observer.started.wait(timeout=1)

    duplicate = risk_worker.submit(
        replace(observed_result, sequence=observed_result.sequence + 1)
    )

    assert duplicate.enqueued
    assert duplicate.sequence == first.sequence + 1
    assert duplicate.suppression_reason is None
    risk_observer.release.set()
    completed = None
    for _ in range(100):
        completed = risk_worker.latest(after_sequence=first.sequence)
        if completed is not None and completed.sequence == duplicate.sequence:
            break
        threading.Event().wait(0.01)
    assert completed is not None
    assert completed.error is None
    assert completed.shadow is None
    assert completed.suppression_reason == "unchanged_action_bucket"
    assert risk_worker.close()
    assert observed_worker.close()


def test_predictive_worker_evaluates_one_unified_model() -> None:
    observed_worker = LatestWinsJointPlanWorker()
    policy_input = _policy_input()
    graph_controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    graph_controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    policy_input = replace(
        policy_input,
        runtime_graph=replace(
            policy_input.runtime_graph,
            graph_version=graph_controller.graph.graph_version,
            state=graph_controller.graph.snapshot(),
        ),
    )
    submission = observed_worker.submit(policy_input)
    observed_result = None
    for _ in range(100):
        observed_result = observed_worker.latest(
            after_sequence=submission.sequence - 1
        )
        if observed_result is not None:
            break
        threading.Event().wait(0.01)
    assert observed_result is not None
    assert observed_result.policy_input is not None
    observed_result = replace(
        observed_result,
        state_stamp=JointShadowStateStamp(
            graph_version=observed_result.policy_input.runtime_graph.graph_version,
            consumer_version=0,
            event_sequence=0,
            page_revision=observed_result.policy_input.physical_kv.allocator_version,
            topology_revision=observed_result.policy_input.physical_kv.topology_version,
            fairness_revision=0,
            transfer_epoch=0,
            runnable_signature=(),
            hbm_used_bytes=observed_result.policy_input.resources.hbm_used_bytes,
            host_free_bytes=observed_result.policy_input.resources.host_free_bytes,
        ),
    )
    primary = _RecordingRiskObserver(selected_action="prepare_host")
    risk_worker = LatestWinsPredictiveRiskWorker(primary)
    predictive_submission = risk_worker.submit(observed_result)
    result = None
    for _ in range(100):
        result = risk_worker.latest(
            after_sequence=predictive_submission.sequence - 1
        )
        if result is not None:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert result.error is None
    assert result.shadow is not None
    assert primary.calls == [
        (policy_input.snapshot_id, id(observed_result.plan))
    ]
    assert risk_worker.close()
    assert observed_worker.close()


def test_worker_replaces_only_the_pending_snapshot() -> None:
    planner = _BlockingPlanner()
    worker = LatestWinsJointPlanWorker(planner)
    policy_input = _policy_input()
    first = worker.submit(policy_input)
    assert planner.started.wait(timeout=2)
    second = worker.submit(policy_input)
    third = worker.submit(policy_input)
    planner.release.set()

    result = None
    for _ in range(100):
        result = worker.latest(after_sequence=first.sequence)
        if result is not None and result.sequence == third.sequence:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert result.sequence == third.sequence
    assert result.plan is not None
    assert result.error is None
    stats = worker.stats()
    assert second.sequence == third.sequence - 1
    assert stats.submitted_count == 3
    assert stats.started_count == 2
    assert stats.completed_count == 2
    assert stats.dropped_pending_count == 1
    assert worker.close()


def test_assembler_attaches_frontier_predictions_to_policy_input() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    handle = PageHandle(1, 0)
    controller.page_index.register_page(handle, size_bytes=100)
    controller.page_index.bind_pages("ctx", 0, (handle,))
    delta = _delta(
        controller,
        event_sequence=0,
        page_revision=0,
        ts_ms=2,
    )
    request = RunnableInvocation(
        request_id="request-root",
        workflow_id="wf",
        invocation_id="root",
        context_id="ctx",
        context_epoch=0,
        submitted_ts_ms=1.0,
        startup_bytes=100,
        predicted_remaining_decode_tokens=64.0,
        predicted_external_wait_ms=512.0,
        prediction_support_level="backoff",
        prediction_ood_reasons=("ood_unknown_project",),
    )
    delta = replace(
        delta,
        runnable_frontier=(request,),
        frontier_predictions={
            "root": {
                "remaining_decode_tokens_p50": 64.0,
                "remaining_external_wait_ms_p50": 512.0,
                "next_output_tokens_p50": 53.0,
                "support_level": "backoff",
                "ood_reasons": ["ood_unknown_project"],
            }
        },
        frontier_features={
            "root": {
                "invocation_id": "root",
                "state": "ready",
                "current_sequence_tokens": 4096,
            }
        },
    )

    assembler = IncrementalPolicyInputAssembler(config)
    assembler.apply(delta)
    policy_input = assembler.build()

    metadata = policy_input.optional_metadata.get("frontier_predictions")
    assert metadata is not None
    assert metadata.source == MetadataSource.PREDICTED
    assert metadata.value["root"]["remaining_decode_tokens_p50"] == 64.0
    feature_metadata = policy_input.optional_metadata.get("frontier_features")
    assert feature_metadata is not None
    assert feature_metadata.value["root"]["state"] == "ready"
    assert "beliefkv_transfer_model_mode" not in policy_input.optional_metadata
    assert policy_input.runnable_frontier[0].predicted_remaining_decode_tokens == 64.0


def test_worker_contains_planner_failure_and_remains_closeable() -> None:
    worker = LatestWinsJointPlanWorker(_FailingPlanner())
    submission = worker.submit(_policy_input())

    result = None
    for _ in range(100):
        result = worker.latest(after_sequence=submission.sequence - 1)
        if result is not None:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert result.plan is None
    assert result.error == "ValueError: expected failure"
    assert worker.stats().failed_count == 1
    assert worker.close()


def test_incremental_worker_merges_pending_deltas_without_losing_events() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    handle = PageHandle(1, 0)
    controller.page_index.register_page(handle, size_bytes=100)
    controller.page_index.bind_pages("ctx", 0, (handle,))
    first_delta = _delta(
        controller,
        event_sequence=0,
        page_revision=0,
        ts_ms=2,
    )

    planner = _BlockingPlanner()
    worker = LatestWinsJointPlanWorker(
        planner,
        assembler=IncrementalPolicyInputAssembler(config),
    )
    first = worker.submit_delta(first_delta)
    assert planner.started.wait(timeout=2)

    controller.process_runtime_event(
        _event(
            3,
            RuntimeEventKind.CONTEXT_ADVANCE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    second_delta = _delta(
        controller,
        event_sequence=first_delta.event_to_sequence,
        page_revision=first_delta.page_delta.to_revision,
        ts_ms=3,
    )
    second = worker.submit_delta(second_delta)

    controller.process_runtime_event(
        _event(
            4,
            RuntimeEventKind.CONTEXT_ADVANCE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    third_delta = _delta(
        controller,
        event_sequence=second_delta.event_to_sequence,
        page_revision=second_delta.page_delta.to_revision,
        ts_ms=4,
    )
    third = worker.submit_delta(third_delta)
    planner.release.set()

    result = None
    for _ in range(100):
        result = worker.latest(after_sequence=first.sequence)
        if result is not None and result.sequence == third.sequence:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert result.error is None
    assert result.policy_input is not None
    assert result.state_stamp is not None
    assert result.snapshot_build_ms == (
        result.snapshot_delta_apply_ms + result.snapshot_materialize_ms
    )
    assert result.snapshot_delta_apply_ms >= 0
    assert result.snapshot_materialize_ms >= 0
    assert result.state_stamp.event_sequence == controller.runtime_event_sequence
    assert (
        result.policy_input.runtime_graph.graph_version
        == controller.graph.graph_version
    )
    assert second.sequence == third.sequence - 1
    assert worker.stats().dropped_pending_count == 0
    assert worker.stats().coalesced_pending_count == 1
    assert worker.close()


def test_incremental_worker_applies_progress_without_materializing_plan() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    first_delta = _delta(
        controller,
        event_sequence=0,
        page_revision=0,
        ts_ms=2,
    )
    planner = _BlockingPlanner()
    planner.release.set()
    worker = LatestWinsJointPlanWorker(
        planner,
        assembler=IncrementalPolicyInputAssembler(config),
    )
    first = worker.submit_delta(first_delta)
    for _ in range(100):
        if worker.latest(after_sequence=first.sequence - 1) is not None:
            break
        threading.Event().wait(0.01)
    assert len(planner.sequences) == 1

    controller.process_runtime_event(
        _event(
            3,
            RuntimeEventKind.CONTEXT_ADVANCE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    progress_delta = _delta(
        controller,
        event_sequence=first_delta.event_to_sequence,
        page_revision=first_delta.page_delta.to_revision,
        ts_ms=3,
        planning_requested=False,
    )
    progress = worker.submit_delta(progress_delta)
    for _ in range(100):
        if worker.stats().completed_count >= 2:
            break
        threading.Event().wait(0.01)

    assert len(planner.sequences) == 1
    assert worker.latest(after_sequence=first.sequence) is None
    assert worker.stats().apply_only_count == 1
    assert worker.assembler is not None
    assert (
        worker.assembler.graph.graph_version
        == controller.graph.graph_version
    )

    controller.process_runtime_event(
        _event(
            4,
            RuntimeEventKind.CONTEXT_ADVANCE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    critical_delta = _delta(
        controller,
        event_sequence=progress_delta.event_to_sequence,
        page_revision=progress_delta.page_delta.to_revision,
        ts_ms=4,
    )
    critical = worker.submit_delta(critical_delta)
    result = None
    for _ in range(100):
        result = worker.latest(after_sequence=first.sequence)
        if result is not None and result.sequence == critical.sequence:
            break
        threading.Event().wait(0.01)

    assert progress.sequence == critical.sequence - 1
    assert result is not None
    assert result.plan is not None
    assert len(planner.sequences) == 2
    assert worker.close()


def test_incremental_assembler_uses_non_atomic_worker_mirrors(monkeypatch) -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    delta = _delta(controller, event_sequence=0, page_revision=0, ts_ms=2)
    assembler = IncrementalPolicyInputAssembler(config)
    calls: list[tuple[str, bool]] = []
    graph_apply = assembler.graph.apply_batch
    consumer_apply = assembler.data_consumers.apply_batch

    def record_graph(events, *, atomic=True):
        calls.append(("graph", atomic))
        return graph_apply(events, atomic=atomic)

    def record_consumers(events, *, atomic=True):
        calls.append(("consumers", atomic))
        return consumer_apply(events, atomic=atomic)

    monkeypatch.setattr(assembler.graph, "apply_batch", record_graph)
    monkeypatch.setattr(
        assembler.data_consumers,
        "apply_batch",
        record_consumers,
    )
    assembler.apply(delta)

    assert calls == [("graph", False), ("consumers", False)]


def test_risk_event_reuses_cached_observed_seed_without_replanning() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
        performance_mode=True,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    handle = PageHandle(1, 0)
    controller.page_index.register_page(handle, size_bytes=100)
    controller.page_index.bind_pages("ctx", 0, (handle,))
    planner = _CountingPlanner()
    forwarded: list[JointShadowResult] = []

    def forward(result: JointShadowResult) -> PredictiveRiskSubmission:
        forwarded.append(result)
        return PredictiveRiskSubmission(
            sequence=7,
            source_joint_sequence=result.sequence,
            source_snapshot_id=result.snapshot_id,
            submitted_monotonic_ms=result.completed_monotonic_ms,
            enqueue_ms=0.01,
            enqueued=True,
            replaced_sequence=None,
        )

    worker = LatestWinsJointPlanWorker(
        planner,
        assembler=IncrementalPolicyInputAssembler(config),
        risk_result_sink=forward,
    )
    initial = _delta(controller, event_sequence=0, page_revision=0, ts_ms=2)
    initial_submission = worker.submit_delta(initial)
    initial_result = None
    for _ in range(100):
        initial_result = worker.latest(
            after_sequence=initial_submission.sequence - 1
        )
        if initial_result is not None:
            break
        threading.Event().wait(0.01)
    assert initial_result is not None
    assert initial_result.planning_attempted
    assert initial_result.plan is not None
    beneficiary, beneficiary_hint = _beneficiary_risk_evidence(
        initial_result.plan.plan_id
    )

    controller.process_runtime_event(
        _event(
            3,
            RuntimeEventKind.TOOL_START,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
            attributes={"tool_family": "shell"},
        )
    )
    risk = replace(
        _delta(
            controller,
            event_sequence=initial.event_to_sequence,
            page_revision=initial.page_delta.to_revision,
            ts_ms=3,
            planning_requested=False,
        ),
        runnable_frontier=(beneficiary,),
        observed_seed_beneficiary=beneficiary_hint,
        risk_evaluation_requested=True,
        risk_trigger_signature=(("prepare", "tool_start", "root", 0),),
    )
    risk_submission = worker.submit_delta(risk)
    risk_result = None
    for _ in range(100):
        risk_result = worker.latest(after_sequence=initial_submission.sequence)
        if risk_result is not None and risk_result.sequence == risk_submission.sequence:
            break
        threading.Event().wait(0.01)

    assert risk_result is not None
    assert risk_result.risk_evaluation_requested
    assert forwarded and forwarded[0].sequence == risk_submission.sequence
    assert risk_result.predictive_submission is not None
    assert risk_result.predictive_submission.sequence == 7
    assert not risk_result.planning_attempted
    assert planner.call_count == 1
    trigger = risk_result.policy_input.optional_metadata[
        "beliefkv_predictive_risk_trigger"
    ].value
    assert trigger["events"] == (("prepare", "tool_start", "root", 0),)
    scope = risk_result.policy_input.optional_metadata[
        "beliefkv_predictive_candidate_scope"
    ].value
    assert scope["victim_context_ids"] == ("ctx",)
    assert "root" in risk_result.policy_input.runtime_graph.state["rccg"][
        "invocations"
    ]
    assert worker.close()


def test_semantic_progress_does_not_erase_inflight_risk_trigger() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
        performance_mode=True,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            _event(1, RuntimeEventKind.WORKFLOW_START),
            _event(
                2,
                RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    handle = PageHandle(1, 0)
    controller.page_index.register_page(handle, size_bytes=100)
    controller.page_index.bind_pages("ctx", 0, (handle,))
    assembler = IncrementalPolicyInputAssembler(config)
    planner = _CountingPlanner()
    worker = LatestWinsJointPlanWorker(planner, assembler=assembler)
    initial = _delta(controller, event_sequence=0, page_revision=0, ts_ms=2)
    initial_submission = worker.submit_delta(initial)
    initial_result = None
    for _ in range(100):
        initial_result = worker.latest(after_sequence=initial_submission.sequence - 1)
        if initial_result is not None:
            break
        threading.Event().wait(0.01)
    assert initial_result is not None and initial_result.plan is not None
    beneficiary, beneficiary_hint = _beneficiary_risk_evidence(
        initial_result.plan.plan_id
    )

    first_started = threading.Event()
    second_started = threading.Event()
    first_release = threading.Event()
    second_release = threading.Event()
    original_materialize = assembler.materialize_predictive_candidates
    materialize_calls = 0

    def blocking_materialize(*args, **kwargs):
        nonlocal materialize_calls
        materialize_calls += 1
        if materialize_calls == 1:
            first_started.set()
            assert first_release.wait(timeout=2)
        elif materialize_calls == 2:
            second_started.set()
            assert second_release.wait(timeout=2)
        return original_materialize(*args, **kwargs)

    assembler.materialize_predictive_candidates = blocking_materialize
    controller.process_runtime_event(
        _event(
            3,
            RuntimeEventKind.TOOL_START,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
            attributes={"tool_family": "shell"},
        )
    )
    risk = replace(
        _delta(
            controller,
            event_sequence=initial.event_to_sequence,
            page_revision=initial.page_delta.to_revision,
            ts_ms=3,
            planning_requested=False,
        ),
        runnable_frontier=(beneficiary,),
        observed_seed_beneficiary=beneficiary_hint,
        risk_evaluation_requested=True,
        risk_trigger_signature=(("prepare", "tool_start", "root", 0),),
    )
    risk_submission = worker.submit_delta(risk)
    assert first_started.wait(timeout=2)

    controller.process_runtime_event(
        _event(
            4,
            RuntimeEventKind.STRUCTURED_ACTION,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    semantic = replace(
        _delta(
            controller,
            event_sequence=risk.event_to_sequence,
            page_revision=risk.page_delta.to_revision,
            ts_ms=4,
            planning_requested=False,
        ),
        runnable_frontier=(beneficiary,),
        observed_seed_beneficiary=beneficiary_hint,
    )
    semantic_submission = worker.submit_delta(semantic)
    first_release.set()
    assert second_started.wait(timeout=2)
    risk_result = worker.latest(after_sequence=initial_submission.sequence)
    assert risk_result is not None
    assert risk_result.sequence == risk_submission.sequence
    assert risk_result.risk_evaluation_requested

    second_release.set()
    result = None
    for _ in range(200):
        result = worker.latest(after_sequence=initial_submission.sequence)
        if result is not None and result.sequence == semantic_submission.sequence:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert result.sequence == semantic_submission.sequence
    assert result.risk_evaluation_requested
    assert not result.planning_attempted
    assert materialize_calls == 2
    assert planner.call_count == 1
    assert worker.close()


def test_incremental_worker_recovers_diverged_mirror_from_full_resync() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_event(_event(1, RuntimeEventKind.WORKFLOW_START))
    delta = _delta(controller, event_sequence=0, page_revision=0, ts_ms=1)
    bad_delta = replace(
        delta,
        stamp=replace(delta.stamp, graph_version=delta.stamp.graph_version + 1),
    )
    worker = LatestWinsJointPlanWorker(
        assembler=IncrementalPolicyInputAssembler(config)
    )
    submission = worker.submit_delta(bad_delta)
    result = None
    for _ in range(100):
        result = worker.latest(after_sequence=submission.sequence - 1)
        if result is not None:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert "graph version diverged" in (result.error or "")
    assert worker.mirror_failed
    with pytest.raises(RuntimeError, match="mirror failed closed"):
        worker.submit_delta(delta)
    assert worker.reset_incremental_mirror()
    assert not worker.mirror_failed

    recovered_submission = worker.submit_delta(delta)
    recovered = None
    for _ in range(100):
        recovered = worker.latest(
            after_sequence=recovered_submission.sequence - 1
        )
        if recovered is not None:
            break
        threading.Event().wait(0.01)

    assert recovered is not None
    assert recovered.error is None
    assert recovered.plan is not None
    assert worker.close()


def test_incremental_worker_publishes_failure_before_superseding_delta() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_event(_event(1, RuntimeEventKind.WORKFLOW_START))
    delta = _delta(controller, event_sequence=0, page_revision=0, ts_ms=1)
    assembler = _BlockingFailingAssembler(config)
    worker = LatestWinsJointPlanWorker(assembler=assembler)

    failed_submission = worker.submit_delta(delta)
    assert assembler.started.wait(timeout=2)
    superseding_submission = worker.submit_delta(delta)
    assembler.release.set()

    result = None
    for _ in range(100):
        result = worker.latest(after_sequence=failed_submission.sequence - 1)
        if result is not None:
            break
        threading.Event().wait(0.01)

    assert result is not None
    assert result.sequence == failed_submission.sequence
    assert result.error == "RuntimeError: expected mirror failure"
    assert superseding_submission.sequence > result.sequence
    assert worker.stats().dropped_pending_count == 1
    assert worker.mirror_failed
    assert worker.close()


def test_incremental_planner_failure_preserves_pending_delta() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_event(_event(1, RuntimeEventKind.WORKFLOW_START))
    first = _delta(controller, event_sequence=0, page_revision=0, ts_ms=1)
    planner = _BlockingFailOncePlanner()
    worker = LatestWinsJointPlanWorker(
        planner,
        assembler=IncrementalPolicyInputAssembler(config),
    )

    failed_submission = worker.submit_delta(first)
    assert planner.started.wait(timeout=2)
    controller.process_runtime_event(
        _event(
            2,
            RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    second = _delta(
        controller,
        event_sequence=first.event_to_sequence,
        page_revision=first.page_delta.to_revision,
        ts_ms=2,
    )
    successful_submission = worker.submit_delta(second)
    planner.release.set()

    failure = None
    for _ in range(100):
        failure = worker.latest(after_sequence=failed_submission.sequence - 1)
        if failure is not None:
            break
        threading.Event().wait(0.01)
    assert failure is not None
    assert failure.sequence == failed_submission.sequence
    assert failure.error == "ValueError: expected planner failure"

    success = None
    for _ in range(100):
        success = worker.latest(after_sequence=failed_submission.sequence)
        if success is not None:
            break
        threading.Event().wait(0.01)
    assert success is not None
    assert success.sequence == successful_submission.sequence
    assert success.error is None
    assert success.plan is not None
    assert not worker.mirror_failed
    assert worker.close()


def test_coalesced_frontier_feature_deltas_preserve_updates_and_removals() -> None:
    config = BeliefKVConfig(
        hbm_capacity_bytes=1_000,
        host_capacity_bytes=1_000,
        reserve_hbm_bytes=0,
        predictor_enabled=False,
        shadow_enabled=False,
    )
    controller = BeliefKVController(config)
    controller.process_runtime_event(
        _event(1, RuntimeEventKind.WORKFLOW_START)
    )
    first = replace(
        _delta(controller, event_sequence=0, page_revision=0, ts_ms=1),
        planning_requested=False,
        frontier_features={"a": {"invocation_id": "a", "state": "ready"}},
        frontier_feature_sources=(FrontierFeatureSource("a"),),
    )
    controller.process_runtime_event(
        _event(
            2,
            RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="root",
            context_id="ctx",
            context_epoch=0,
        )
    )
    second = replace(
        _delta(
            controller,
            event_sequence=first.event_to_sequence,
            page_revision=first.page_delta.to_revision,
            ts_ms=2,
        ),
        planning_requested=False,
        frontier_features={"b": {"invocation_id": "b", "state": "wait_tool"}},
        frontier_feature_sources=(
            FrontierFeatureSource("b", context_tokens=4096),
        ),
        removed_frontier_invocation_ids=frozenset({"a"}),
    )

    combined = coalesce_joint_shadow_deltas((first, second))

    assert dict(combined.frontier_features) == {
        "b": {"invocation_id": "b", "state": "wait_tool"}
    }
    assert combined.removed_frontier_invocation_ids == frozenset({"a"})
    assert combined.frontier_feature_sources == (
        FrontierFeatureSource("b", context_tokens=4096),
    )


def test_shadow_delta_coalesces_latest_observed_seed_beneficiary() -> None:
    controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    controller.process_runtime_event(_event(1, RuntimeEventKind.WORKFLOW_START))
    first = replace(
        _delta(controller, event_sequence=0, page_revision=0, ts_ms=1),
        observed_seed_beneficiary=ObservedSeedBeneficiaryHint(
            "seed-1", "request-1", "invocation-1", "context-1", 0, 64, 32
        ),
    )
    controller.process_runtime_event(
        _event(
            2,
            RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="root",
            context_id="context-2",
            context_epoch=0,
        )
    )
    second = replace(
        _delta(
            controller,
            event_sequence=first.event_to_sequence,
            page_revision=first.page_delta.to_revision,
            ts_ms=2,
        ),
        observed_seed_beneficiary=ObservedSeedBeneficiaryHint(
            "seed-2", "request-2", "invocation-2", "context-2", 0, 128, 64
        ),
    )

    combined = coalesce_joint_shadow_deltas((first, second))

    assert combined.observed_seed_beneficiary == second.observed_seed_beneficiary


def test_shadow_delta_coalesces_explicit_overlay_clear() -> None:
    controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    controller.process_runtime_event(_event(1, RuntimeEventKind.WORKFLOW_START))
    probe = BeneficiaryOpportunityProbe(
        beneficiary_request_id="request-1",
        beneficiary_context_id="context-1",
        beneficiary_context_epoch=0,
        required_bytes=96,
        hbm_available_bytes=0,
        hbm_risk_margin_bytes=64,
        projected_running_growth_bytes=64,
        projected_hbm_available_bytes=0,
        predicted_block_time_ms=0.0,
        predicted_deficit_bytes=96,
        running_request_count=0,
        max_running_requests=32,
        beneficiary_slot_blocked=False,
        beneficiary_hbm_blocked=True,
        beneficiary_slot_then_hbm_blocked=False,
        hbm_opportunity_possible=True,
        captured_ts_ms=1.0,
    )
    first = replace(
        _delta(controller, event_sequence=0, page_revision=0, ts_ms=1),
        action_local_overlay_batch=ActionLocalPhysicalOverlayBatch(
            beneficiary_risk_signature=("request-1", "context-1", 0, 64, 32),
            opportunity=probe,
            selection_reason="no_victim_context_selected",
        ),
        action_local_overlay_replaced=True,
    )
    controller.process_runtime_event(
        _event(
            2,
            RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="root",
            context_id="context-2",
            context_epoch=0,
        )
    )
    second = replace(
        _delta(
            controller,
            event_sequence=first.event_to_sequence,
            page_revision=first.page_delta.to_revision,
            ts_ms=2,
        ),
        action_local_overlay_batch=None,
        action_local_overlay_replaced=True,
    )

    combined = coalesce_joint_shadow_deltas((first, second))

    assert combined.action_local_overlay_replaced
    assert combined.action_local_overlay_batch is None

def test_frontier_feature_source_materializes_worker_graph_state() -> None:
    source = FrontierFeatureSource(
        "invocation",
        boundary_history=("tool_start",),
        context_tokens=4096,
        generated_tokens=32,
        backend_class="local_shell",
        command_class="pytest",
    )
    invocation = SimpleNamespace(
        state=InvocationState.WAIT_TOOL,
        agent_definition_id="coder",
        active_tool_family="shell",
        active_tool_start_ms=100.0,
    )

    features = source.materialize(invocation, now_ms=350.0)

    assert features.state == "wait_tool"
    assert features.elapsed_wait_ms == 250.0
    assert features.current_sequence_tokens == 4096
    assert features.command_class == "pytest"
