from dataclasses import replace
from types import SimpleNamespace

from beliefkv.control.causal_graph import (
    ContextRecord,
    InvocationRecord,
    InvocationState,
    JoinRecord,
    RuntimeCausalContextGraph,
    WorkflowRecord,
)
from beliefkv.policy.joint_scheduler import AsyncSemanticJointPlanner, JointPlannerConfig
from beliefkv.policy.reference import (
    AdmissionAction,
    MetadataSource,
    MetadataValue,
    PhysicalBundleSnapshot,
    RunnableInvocation,
)
from beliefkv.policy.risk_shadow import (
    PrepareHostVictim,
    PredictiveActionCertificate,
    PredictiveIntent,
    PredictiveEligibilityIndex,
    PredictiveEligibility,
    PredictiveRiskShadowConfig,
    PredictiveRiskShadowObserver,
    PredictiveRiskShadowResult,
    _transfer_deadline_and_slack,
    validate_predictive_causal_certificate,
    validate_predictive_certificate,
)
from beliefkv.policy.predictive_joint import (
    PredictiveActionKind,
    ProjectedReclaimRequirement,
)
from beliefkv.predictor.frontier_belief import PredictiveEvidenceReadSet
from beliefkv.predictor.hardware_service import GPUServiceCurveModel
from beliefkv.predictor.structured_frontier import (
    EmpiricalDistribution,
    LocalFrontierFeatures,
    LocalFrontierPrediction,
    WaitBelief,
    WaitBeliefKind,
)
from tests.test_whatif_packer import _input


def _distribution(value: float) -> EmpiricalDistribution:
    return EmpiricalDistribution((value,), (1.0,), 10.0)


def test_transfer_guard_is_recomputed_at_conservative_deadline() -> None:
    deadline, slack = _transfer_deadline_and_slack(
        125.0,
        200.0,
        transfer_ms=100.0,
        guard_ms=25.0,
    )
    assert deadline == 125.0
    assert slack == 0.0

    _, positive = _transfer_deadline_and_slack(
        125.001,
        200.0,
        transfer_ms=100.0,
        guard_ms=25.0,
    )
    assert positive is not None and positive > 0


def test_join_slack_uses_dependency_release_survival_not_resource_feasibility() -> None:
    belief = SimpleNamespace(
        other_probability_mass=0.10,
        scenarios=(
            SimpleNamespace(scenario_id="early", probability_mass=0.45),
            SimpleNamespace(scenario_id="late", probability_mass=0.45),
        ),
    )
    evaluation = SimpleNamespace(
        dependency_release_offsets_by_scenario={
            "early": {"parent": 50.0},
            "late": {"parent": 200.0},
        }
    )

    probability, conservative = (
        PredictiveRiskShadowObserver._dependency_release_timing(
            belief,
            evaluation,
            invocation_id="parent",
            required_wait_ms=100.0,
            release_within=False,
        )
    )

    assert probability == 0.45
    assert conservative == 0.0  # OTHER has no finite bound and stays conservative.

    assert (
        PredictiveRiskShadowObserver._dependency_release_timing(
            belief,
            evaluation,
            invocation_id="parent",
            required_wait_ms=100.0,
            release_within=True,
        )
        is None
    )  # OTHER has no finite upper reentry bound for PREFETCH_GPU.


def test_candidate_packages_exclude_invocations_outside_belief_scope() -> None:
    observer = PredictiveRiskShadowObserver.__new__(
        PredictiveRiskShadowObserver
    )
    observer.config = SimpleNamespace(
        max_full_prefetch_hbm_ratio=0.05,
        max_candidates=8,
    )
    eligibility = PredictiveEligibility(
        source_snapshot_id="snapshot",
        prefetch_targets=(),
        prepare_host_victims=(
            PrepareHostVictim(
                "invocation-in", "ctx-in", "WAIT_TOOL", 100, 100
            ),
            PrepareHostVictim(
                "invocation-other", "ctx-other", "WAIT_TOOL", 100, 100
            ),
            PrepareHostVictim(
                "invocation-third", "ctx-third", "WAIT_TOOL", 100, 100
            ),
        ),
        probe_ms=0.0,
    )

    packages = observer._candidate_packages(
        _input(capacity=1_000, reserved=0),
        SimpleNamespace(plan_id="plan"),
        eligibility,
        allowed_invocation_ids=frozenset(
            {"invocation-in", "invocation-other", "invocation-third"}
        ),
        projected_requirement=ProjectedReclaimRequirement(
            beneficiary_request_id="beneficiary",
            beneficiary_invocation_id="beneficiary-invocation",
            beneficiary_context_id="beneficiary-context",
            beneficiary_context_epoch=0,
            required_startup_bytes=64,
            required_growth_bytes=64,
            source_joint_plan_id="plan",
            causal_package_generation="g1:a1:c0",
        ),
    )

    assert [package.package_id for package in packages] == [
        "plan:a0",
        "plan:prepare:ctx-in",
        "plan:prepare:ctx-other",
    ]


def _prediction() -> LocalFrontierPrediction:
    return LocalFrontierPrediction(
        invocation_id="invocation-target",
        boundary_distribution={"tool": 1.0},
        current_sequence_tokens=4096,
        remaining_decode_tokens=_distribution(0),
        remaining_external_wait=_distribution(100),
        tool_terminal_distribution={"success": 1.0},
        prompt_growth_tokens=_distribution(32),
        next_output_tokens=_distribution(16),
        support_level="exact",
        calibration_coverage=0.95,
    )


def _tool_wait_belief(
    value: float,
    *,
    support_level: str = "exact",
) -> WaitBelief:
    distribution = (
        _distribution(value)
        if support_level != "unavailable"
        else EmpiricalDistribution.empty()
    )
    return WaitBelief(
        kind=WaitBeliefKind.TOOL,
        residual_duration=distribution,
        terminal_distribution={"success": 1.0},
        support_level=support_level,
        ood_reasons=(
            ()
            if support_level != "unavailable"
            else ("tool_wait_unavailable",)
        ),
    )


def _service_row(sample_id: str, phase: str, token_delta: int) -> dict[str, object]:
    return {
        "row_type": "gpu_batch_service_interval",
        "sample_id": sample_id,
        "split": "train",
        "phase": phase,
        "batch_size": 1,
        "request_samples": [
            {
                "request_id": sample_id,
                "sequence_tokens_before": 4096,
                "token_delta": token_delta,
                "cache_hit_ratio": 0.0,
            }
        ],
        "chunk_position": "first",
        "prefill_decode_mixed": False,
        "pcie_contention_state": "idle",
        "hicache_inflight_bytes": 0,
        "service_elapsed_ms": 1.0,
        "warmup": False,
        "evidence_role": "controlled_microbenchmark",
    }


def _graph() -> RuntimeCausalContextGraph:
    graph = RuntimeCausalContextGraph()
    graph.workflows["workflow-target"] = WorkflowRecord(
        "workflow-target", 0.0, invocation_ids={"invocation-target"}
    )
    graph.contexts["ctx-target"] = ContextRecord(
        "workflow-target",
        "ctx-target",
        0,
        0.0,
        100.0,
        invocation_ids={"invocation-target"},
    )
    graph.invocations["invocation-target"] = InvocationRecord(
        workflow_id="workflow-target",
        invocation_id="invocation-target",
        context_id="ctx-target",
        agent_definition_id="coder",
        agent_instance_id="coder-0",
        state=InvocationState.WAIT_TOOL,
        created_ts_ms=0.0,
        updated_ts_ms=100.0,
        active_tool_family="shell",
        active_tool_start_ms=90.0,
    )
    graph._graph_version = 3
    return graph


def _attach_graph(policy_input, graph):
    return replace(
        policy_input,
        runtime_graph=replace(
            policy_input.runtime_graph,
            graph_version=graph.graph_version,
            state=graph.snapshot(),
        ),
    )


def _add_waiting_victim(graph: RuntimeCausalContextGraph) -> None:
    graph.workflows["workflow-old"] = WorkflowRecord(
        "workflow-old", 0.0, invocation_ids={"invocation-old"}
    )
    graph.contexts["ctx-old"] = ContextRecord(
        "workflow-old",
        "ctx-old",
        0,
        0.0,
        100.0,
        invocation_ids={"invocation-old"},
    )
    graph.invocations["invocation-old"] = InvocationRecord(
        workflow_id="workflow-old",
        invocation_id="invocation-old",
        context_id="ctx-old",
        agent_definition_id="coder",
        agent_instance_id="coder-old",
        state=InvocationState.WAIT_TOOL,
        created_ts_ms=0.0,
        updated_ts_ms=100.0,
        active_tool_family="shell",
        active_tool_start_ms=90.0,
    )


def test_local_frontier_prediction_round_trip_preserves_distributions() -> None:
    prediction = _prediction()

    restored = LocalFrontierPrediction.from_dict(prediction.to_dict())

    assert restored == prediction


def test_prefetch_is_deferred_without_projected_hbm_beneficiary() -> None:
    policy_input = _input(capacity=1_000, reserved=100, include_cpu_target=True)
    prediction = replace(
        _prediction(),
        remaining_external_wait=_distribution(1),
        wait_belief=_tool_wait_belief(1),
    )
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )
    observer = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            minimum_calibration_coverage=0.9,
            kv_bytes_per_token=1,
        ),
    )
    evidence = PredictiveEvidenceReadSet(
        graph_version=3,
        page_revision=5,
        topology_revision=4,
        fairness_revision=0,
        admission_revision=0,
        transfer_epoch=0,
        obligation_revision=0,
        lease_revision=0,
        grace_revision=0,
        parser_frontier_revision=0,
        model_version="frontier-test-v1",
    )

    result = observer.evaluate(
        policy_input,
        graph=_graph(),
        source_plan=source_plan,
        evidence_read_set=evidence,
    )

    assert result.status == "skipped"
    assert result.blocked_reasons == ("no_projected_hbm_beneficiary",)
    assert not source_plan.prediction_used


def test_candidate_local_inference_covers_complete_join_closure() -> None:
    policy_input = _input(capacity=1_000, reserved=100, include_cpu_target=True)
    graph = _graph()
    parent = graph.invocations["invocation-target"]
    parent.state = InvocationState.WAIT_JOIN
    parent.active_tool_family = None
    parent.active_tool_start_ms = None
    parent.join_id = "join-target"
    child_ids = ("child-a", "child-b", "child-c")
    parent.child_invocation_ids.update(child_ids)
    parent.blocking_child_ids.update(child_ids)
    graph.workflows["workflow-target"].invocation_ids.update(child_ids)
    for child_id in child_ids:
        context_id = f"ctx-{child_id}"
        graph.contexts[context_id] = ContextRecord(
            "workflow-target",
            context_id,
            0,
            10.0,
            100.0,
            parent_context_id="ctx-target",
            invocation_ids={child_id},
        )
        graph.invocations[child_id] = InvocationRecord(
            workflow_id="workflow-target",
            invocation_id=child_id,
            context_id=context_id,
            agent_definition_id="browser",
            agent_instance_id=child_id,
            state=InvocationState.WAIT_TOOL,
            created_ts_ms=10.0,
            updated_ts_ms=100.0,
            parent_invocation_id="invocation-target",
            parent_context_id="ctx-target",
            active_tool_family="shell",
            active_tool_start_ms=90.0,
        )
    graph.joins["join-target"] = JoinRecord(
        workflow_id="workflow-target",
        join_id="join-target",
        member_invocation_ids=set(child_ids),
        waiter_invocation_ids={"invocation-target"},
    )
    graph._graph_version = 11

    features = {
        invocation_id: LocalFrontierFeatures(
            invocation_id=invocation_id,
            state=invocation.state.value,
            agent_definition_id=invocation.agent_definition_id,
            tool_family=invocation.active_tool_family or "unknown",
            elapsed_wait_ms=10.0,
            current_sequence_tokens=4096,
        ).to_dict()
        for invocation_id, invocation in graph.invocations.items()
    }
    policy_input = _attach_graph(policy_input, graph)
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_features": MetadataValue(
                MetadataSource.OBSERVED,
                features,
                "test-frontier-features",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "candidate-local-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )

    class FeatureEchoModel:
        model_version = "candidate-local-test-v1"

        @staticmethod
        def predict(item: LocalFrontierFeatures) -> LocalFrontierPrediction:
            if item.state == InvocationState.WAIT_JOIN.value:
                wait = WaitBelief(
                    kind=WaitBeliefKind.JOIN,
                    support_level="structural",
                    dependency_composed=True,
                )
                return replace(
                    _prediction(),
                    invocation_id=item.invocation_id,
                    remaining_external_wait=EmpiricalDistribution.empty(),
                    wait_belief=wait,
                )
            return replace(
                _prediction(),
                invocation_id=item.invocation_id,
                wait_belief=_tool_wait_belief(100.0),
            )

    observer = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
        ),
        frontier_model=FeatureEchoModel(),
    )
    result = observer.evaluate(
        policy_input,
        graph=graph,
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=11,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="candidate-local-test-v1",
        ),
    )

    assert "closure_prediction_incomplete" not in result.blocked_reasons
    assert "frontier_inputs_unavailable" not in result.blocked_reasons


def test_full_prefetch_over_canary_cap_is_filtered_before_risk_evaluation() -> None:
    policy_input = _input(capacity=1_000, reserved=100, include_cpu_target=True)
    prediction = _prediction()
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill", "prefill", 32),
            _service_row("decode", "decode", 16),
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=8,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
            max_full_prefetch_hbm_ratio=0.05,
        ),
    ).evaluate(
        policy_input,
        graph=_graph(),
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.selected_action == "observed_baseline"
    assert not any(
        item["action"] == "prefetch_gpu" for item in result.candidate_summaries
    )

def test_backoff_shadow_cannot_select_prefetch() -> None:
    policy_input = _input(capacity=1_000, reserved=100, include_cpu_target=True)
    prediction = replace(
        _prediction(),
        support_level="backoff",
        calibration_coverage=0.99,
        wait_belief=_tool_wait_belief(
            0, support_level="unavailable"
        ),
        head_support={
            "tool_wait": "unavailable",
            "prompt_growth": "unavailable",
        },
    )
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )
    observer = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            minimum_calibration_coverage=0.9,
            kv_bytes_per_token=1,
        ),
    )

    result = observer.evaluate(
        policy_input,
        graph=_graph(),
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.status == "skipped"
    assert result.blocked_reasons == ("no_projected_hbm_beneficiary",)


def test_calibrated_backoff_can_supply_prefetch_specific_heads() -> None:
    policy_input = _input(capacity=1_000, reserved=100, include_cpu_target=True)
    prediction = replace(
        _prediction(),
        support_level="backoff",
        calibration_coverage=0.99,
        ood_reasons=("boundary_unavailable",),
        calibrated_intervals={
            "prompt_growth_tokens": (16.0, 64.0),
        },
        wait_belief=_tool_wait_belief(10, support_level="backoff"),
        head_support={
            "tool_wait": "backoff",
            "prompt_growth": "backoff",
        },
    )
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            minimum_calibration_coverage=0.9,
            minimum_causal_slack_probability=0.0,
            kv_bytes_per_token=1,
        ),
    ).evaluate(
        policy_input,
        graph=_graph(),
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.status == "skipped"
    assert result.blocked_reasons == ("no_projected_hbm_beneficiary",)


def test_physically_blocked_target_cannot_select_prefetch() -> None:
    policy_input = _input(capacity=1_000, reserved=100, include_cpu_target=True)
    bundles = tuple(
        replace(
            bundle,
            actionable=False,
            blocker_codes=("ancestor_closure",),
        )
        if bundle.bundle_id == "target-cpu"
        else bundle
        for bundle in policy_input.physical_kv.bundles
    )
    prediction = _prediction()
    policy_input = replace(
        policy_input,
        physical_kv=replace(policy_input.physical_kv, bundles=bundles),
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
        ),
    ).evaluate(
        policy_input,
        graph=_graph(),
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.selected_action == "observed_baseline"


def test_prefetch_is_not_generated_before_prepare_recourse_is_validated() -> None:
    policy_input = _input(capacity=930, reserved=100, include_cpu_target=True)
    prediction = _prediction()
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
        ),
    ).evaluate(
        policy_input,
        graph=_graph(),
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.selected_action == "observed_baseline"
    assert result.candidate_summaries == ()
    assert result.blocked_reasons == ("no_projected_hbm_beneficiary",)


def test_prepare_host_receives_recourse_value_only_before_future_pressure() -> None:
    graph = _graph()
    _add_waiting_victim(graph)
    target_record = graph.invocations["invocation-target"]
    target_record.state = InvocationState.READY
    target_record.active_tool_family = None
    target_record.active_tool_start_ms = None
    graph.workflows["workflow-target"].invocation_ids.add(
        "invocation-running"
    )
    graph.contexts["ctx-recent"] = ContextRecord(
        "workflow-target",
        "ctx-recent",
        0,
        0.0,
        100.0,
        invocation_ids={"invocation-running"},
    )
    graph.invocations["invocation-running"] = InvocationRecord(
        workflow_id="workflow-target",
        invocation_id="invocation-running",
        context_id="ctx-recent",
        agent_definition_id="coder",
        agent_instance_id="coder-running",
        state=InvocationState.RUNNING_LLM,
        created_ts_ms=0.0,
        updated_ts_ms=100.0,
    )
    # Keep the first predicted deficit within the victim's exclusive suffix so
    # PREPARE_HOST can actually replace the reactive offload path.
    policy_input = _input(capacity=1_230, reserved=100, include_cpu_target=False)
    policy_input = replace(
        policy_input,
        physical_kv=replace(
            policy_input.physical_kv,
            bundles=tuple(
                replace(bundle, cpu_bytes=0)
                if bundle.bundle_id == "old"
                else bundle
                for bundle in policy_input.physical_kv.bundles
            ),
        ),
    )
    target = replace(
        _prediction(),
        prompt_growth_tokens=_distribution(300),
    )
    running = replace(
        _prediction(),
        invocation_id="invocation-running",
        remaining_decode_tokens=_distribution(200),
        remaining_external_wait=_distribution(0),
        prompt_growth_tokens=_distribution(0),
        next_output_tokens=_distribution(0),
    )
    victim = replace(
        _prediction(),
        invocation_id="invocation-old",
        remaining_external_wait=_distribution(1000),
        wait_belief=_tool_wait_belief(1000),
    )
    policy_input = _attach_graph(
        replace(
            policy_input,
            optional_metadata={
                "frontier_predictions": MetadataValue(
                    MetadataSource.PREDICTED,
                    {
                        target.invocation_id: target.to_dict(),
                        running.invocation_id: running.to_dict(),
                        victim.invocation_id: victim.to_dict(),
                    },
                    "test-frontier",
                ),
                "frontier_prediction_model_version": MetadataValue(
                    MetadataSource.PREDICTED,
                    "frontier-test-v1",
                    "test-frontier",
                ),
                "beliefkv_transfer_interference_policy": MetadataValue(
                    MetadataSource.APPLICATION_PROVIDED,
                    {
                        "mode": "stall_fraction",
                        "stall_fraction": 0.1,
                        "service_epoch": "test-transfer-v1",
                    },
                    "test-interference-policy",
                ),
                "beliefkv_transfer_service_curve_snapshot": MetadataValue(
                    MetadataSource.OBSERVED,
                    {
                        "schema_version": 1,
                        "min_samples": 1,
                        "warm_start_hardware_key": "test-shape-v1",
                        "fallback": {
                            "bandwidth_gbps": 24.0,
                            "overhead_ms": 0.1,
                            "safety_factor": 1.25,
                        },
                        "buckets": [
                            {
                                "direction": "d2h",
                                "size_bucket": 7,
                                "page_count_bucket": 0,
                                "compute_phase": "unknown",
                                "command_kind": "offload_context",
                                "host_copy_state": "missing",
                                "pinned_host": True,
                                "native_traffic_bucket": 0,
                                "sample_count": 1,
                                "usable_count": 1,
                                "outcome_count": 1,
                                "rejection_probability": 0.0,
                                "setup_p90_ms": 0.1,
                                "callback_floor_p90_ms": 0.1,
                                "fixed_overhead_p90_ms": 0.0,
                                "effective_bytes_per_ms_p10": 100.0,
                                "estimated_unhidden_stall_p90_ms": 0.25,
                            }
                        ],
                    },
                    "test-shape-curve",
                ),
            },
        ),
        graph,
    )
    beneficiary_request = replace(
        policy_input.runnable_frontier[0],
        admission_startup_bytes=100,
        admission_growth_bytes=300,
        causal_class="engine_waiting:slot",
    )
    running_request = RunnableInvocation(
        request_id="request-running",
        workflow_id="workflow-target",
        invocation_id="invocation-running",
        context_id="ctx-recent",
        context_epoch=0,
        submitted_ts_ms=0.0,
        startup_bytes=0,
        admission_startup_bytes=0,
        admission_growth_bytes=200,
        causal_class="engine_running:decode",
    )
    policy_input = replace(
        policy_input,
        runnable_frontier=(running_request, beneficiary_request),
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    source_plan = replace(
        source_plan,
        execution=replace(
            source_plan.execution,
            ordered_request_ids=("request-running",),
        ),
        admissions=tuple(
            replace(
                admission,
                action=(
                    AdmissionAction.DEFER
                    if admission.request_id == "request-target"
                    else AdmissionAction.ADMIT
                ),
                reserved_bytes=0,
            )
            for admission in source_plan.admissions
        ),
        candidate_order_request_ids=("request-target",),
    )
    service_model = GPUServiceCurveModel(minimum_support=1)
    long_decode = _service_row("decode-long", "decode", 200)
    long_decode["service_elapsed_ms"] = 10.0
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-large", "prefill", 300),
            _service_row("decode-a", "decode", 16),
            long_decode,
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=6,
            kv_bytes_per_token=1,
            transfer_commit_guard_ms=0.0,
        ),
    ).evaluate(
        policy_input,
        graph=graph,
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    prepare = next(
        item
        for item in result.candidate_summaries
        if ":prepare:ctx-old" in str(item["package_id"])
    )
    assert prepare["scenario_projection"] == "prepare_host"
    assert prepare["expected_benefit_ms"] > 0, prepare
    assert "insufficient_expected_benefit" not in prepare["reasons"]
    assert prepare["prepare_recourse_failure_counts"] == {"eligible": 1}
    diagnostic = prepare["prepare_recourse_scenarios"][0]
    assert diagnostic["shadow_completion_ms"] <= diagnostic["first_pressure_ms"]
    assert diagnostic["first_pressure_ms"] < diagnostic["parent_reentry_ms"]
    assert (
        diagnostic["exclusive_reclaimable_bytes"]
        >= diagnostic["pressure_deficit_bytes"]
    )
    assert diagnostic["baseline_reactive_d2h_ms"] > 0
    assert diagnostic["transfer_duration_source"] == "bucket"
    assert diagnostic["transfer_service_epoch"] == "test-shape-v1"
    assert diagnostic["transfer_shape_supported"] is True
    assert diagnostic["predicted_extent_count"] == 1
    assert diagnostic["morphology_slack_ms"] > 0
    assert diagnostic["conservative_morphology_slack_ms"] > 0
    assert diagnostic["morphology_debt_ms"] == diagnostic[
        "shape_aware_transfer_p90_ms"
    ]
    assert diagnostic["morphology_penalty_ms"] == (
        diagnostic["shape_aware_transfer_p90_ms"]
        - diagnostic["byte_only_transfer_ms"]
    )
    assert diagnostic["interference_source"] == "stall_fraction_sensitivity"
    assert diagnostic["interference_service_epoch"] == "test-transfer-v1"
    assert diagnostic["interference_to_transfer_ratio"] == 0.1
    assert diagnostic["reactive_victim_model"] == "beneficiary_bound_same_closure"


def test_join_revision_invalidates_action_specific_causal_certificate() -> None:
    certificate = {
        "context_epochs": [],
        "invocation_evidence": [],
        "join_evidence": [["join-1", "all", False, []]],
        "communication_evidence": [],
        "model_version": "frontier-v1",
    }
    current_graph = {
        "contexts": {},
        "invocations": {},
        "joins": {
            "join-1": {
                "mode": "all",
                "satisfied": True,
                "completed": ["child-1"],
            }
        },
        "communication_edges": [],
    }

    reasons = validate_predictive_causal_certificate(
        certificate,
        current_graph,
        current_model_version="frontier-v1",
    )

    assert reasons == ("join_revision:join-1",)


def test_shared_locked_prefix_does_not_expand_semantic_belief_scope() -> None:
    policy_input = _input(capacity=1_000, reserved=100, include_cpu_target=False)
    graph = _graph()
    _add_waiting_victim(graph)
    target_record = graph.invocations["invocation-target"]
    target_record.state = InvocationState.READY
    target_record.active_tool_family = None
    target_record.active_tool_start_ms = None
    predictions = {
        "invocation-target": _prediction().to_dict(),
        "invocation-old": replace(
            _prediction(), invocation_id="invocation-old"
        ).to_dict(),
    }
    context_ids = {"ctx-target"}
    for index in range(40):
        workflow_id = f"workflow-peer-{index}"
        context_id = f"ctx-peer-{index}"
        invocation_id = f"invocation-peer-{index}"
        graph.workflows[workflow_id] = WorkflowRecord(
            workflow_id, 0.0, invocation_ids={invocation_id}
        )
        graph.contexts[context_id] = ContextRecord(
            workflow_id,
            context_id,
            0,
            0.0,
            100.0,
            invocation_ids={invocation_id},
        )
        graph.invocations[invocation_id] = InvocationRecord(
            workflow_id=workflow_id,
            invocation_id=invocation_id,
            context_id=context_id,
            agent_definition_id="coder",
            agent_instance_id=f"coder-{index + 1}",
            state=InvocationState.WAIT_TOOL,
            created_ts_ms=0.0,
            updated_ts_ms=100.0,
            active_tool_family="shell",
            active_tool_start_ms=90.0,
        )
        predictions[invocation_id] = replace(
            _prediction(), invocation_id=invocation_id
        ).to_dict()
        context_ids.add(context_id)
    policy_input = _attach_graph(policy_input, graph)
    target_bundle = next(
        bundle
        for bundle in policy_input.physical_kv.bundles
        if bundle.bundle_id == "target-gpu"
    )
    shared_prefix = replace(
        target_bundle,
        bundle_id="shared-system-prefix",
        owner_context_ids=tuple(sorted(context_ids)),
        scope="shared_subtree",
        locked_bytes=target_bundle.gpu_bytes,
        actionable=False,
        blocker_codes=("node_locked",),
    )
    policy_input = replace(
        policy_input,
        physical_kv=replace(
            policy_input.physical_kv,
            bundles=tuple(
                shared_prefix
                if bundle.bundle_id == "target-gpu"
                else replace(bundle, cpu_bytes=0)
                if bundle.bundle_id == "old"
                else bundle
                for bundle in policy_input.physical_kv.bundles
            ),
        ),
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                predictions,
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    policy_input = replace(
        policy_input,
        runnable_frontier=(
            replace(
                policy_input.runnable_frontier[0],
                admission_startup_bytes=100,
                admission_growth_bytes=200,
                causal_class="engine_waiting:slot",
            ),
        ),
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    source_plan = replace(
        source_plan,
        admissions=tuple(
            replace(
                admission,
                action=AdmissionAction.DEFER,
                reserved_bytes=0,
            )
            for admission in source_plan.admissions
        ),
        candidate_order_request_ids=("request-target",),
    )
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
        ),
    ).evaluate(
        policy_input,
        graph=graph,
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.status == "evaluated"
    assert not any(
        reason.startswith("belief_compose_failed")
        for reason in result.blocked_reasons
    )


def test_no_candidate_gate_returns_before_belief_composition() -> None:
    graph = _graph()
    graph.invocations["invocation-target"].state = InvocationState.RUNNING_LLM
    policy_input = _attach_graph(
        _input(capacity=1_000, reserved=100, include_cpu_target=False),
        graph,
    )
    observer = PredictiveRiskShadowObserver(
        GPUServiceCurveModel(minimum_support=1),
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
        ),
    )
    observer.composer.compose = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("belief compose must not run")
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)

    result = observer.evaluate(
        policy_input,
        graph=graph,
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.status == "skipped"
    assert result.blocked_reasons == ("no_action_specific_candidate",)
    assert result.planning_ms < 1.0


def test_eligibility_reads_nested_runtime_rccg_snapshot() -> None:
    graph = _graph()
    policy_input = _input(
        capacity=1_000,
        reserved=100,
        include_cpu_target=True,
    )
    policy_input = replace(
        policy_input,
        runtime_graph=replace(
            policy_input.runtime_graph,
            graph_version=graph.graph_version,
            state={"rccg": graph.snapshot()},
        ),
    )

    eligibility = PredictiveEligibilityIndex().probe(policy_input)

    assert eligibility.has_candidate
    assert eligibility.prefetch_targets[0].context_id == "ctx-target"


def _radix_extent(
    extent_id: str,
    owners: tuple[str, ...],
    *,
    size: int,
    children: tuple[str, ...] = (),
    blockers: tuple[str, ...] = (),
) -> PhysicalBundleSnapshot:
    return PhysicalBundleSnapshot(
        bundle_id=f"bundle-{extent_id}",
        owner_context_ids=owners,
        scope="exclusive_suffix" if len(owners) == 1 else "shared_subtree",
        physical_unique_bytes=size,
        gpu_bytes=size,
        cpu_bytes=0,
        marginal_reclaimable_bytes=0 if blockers else size,
        closure_bytes=size,
        locked_bytes=size if "node_locked" in blockers else 0,
        residency="gpu_only",
        generation_fingerprint=f"generation-{extent_id}",
        extent_ids=(extent_id,),
        lease_kind="wait_tool",
        actionable=not blockers,
        blocker_codes=blockers,
        child_extent_ids=children,
    )


def test_prepare_shadow_absorbs_descendant_closure_without_claiming_child_bytes() -> None:
    graph = _graph()
    parent = _radix_extent(
        "parent",
        ("ctx-target",),
        size=300,
        children=("child",),
        blockers=("descendant_closure",),
    )
    child = _radix_extent("child", ("ctx-child",), size=200)
    base = _attach_graph(_input(capacity=1_000, reserved=0), graph)
    policy_input = replace(
        base,
        physical_kv=replace(
            base.physical_kv,
            gpu_bytes=500,
            cpu_bytes=0,
            bundles=(parent, child),
        ),
        resources=replace(base.resources, hbm_used_bytes=500, hbm_reserved_bytes=0),
    )

    eligibility = PredictiveEligibilityIndex().probe(policy_input)

    victim = next(
        item
        for item in eligibility.prepare_host_victims
        if item.context_id == "ctx-target"
    )
    assert victim.shadow_bytes == 500
    assert victim.reclaimable_bytes == 300


def test_prepare_shadow_rejects_running_descendant_owner() -> None:
    graph = _graph()
    parent = _radix_extent(
        "parent",
        ("ctx-target",),
        size=300,
        children=("child",),
        blockers=("descendant_closure",),
    )
    child = _radix_extent(
        "child",
        ("ctx-child",),
        size=200,
        blockers=("owner_running",),
    )
    base = _attach_graph(_input(capacity=1_000, reserved=0), graph)
    policy_input = replace(
        base,
        physical_kv=replace(
            base.physical_kv,
            gpu_bytes=500,
            cpu_bytes=0,
            bundles=(parent, child),
        ),
        resources=replace(base.resources, hbm_used_bytes=500, hbm_reserved_bytes=0),
    )

    eligibility = PredictiveEligibilityIndex().probe(policy_input)

    assert not eligibility.prepare_host_victims


def test_eligibility_trigger_tracks_material_belief_bucket_change() -> None:
    graph = _graph()
    prediction = _prediction()
    policy_input = _attach_graph(
        _input(capacity=1_000, reserved=100, include_cpu_target=True), graph
    )
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    index = PredictiveEligibilityIndex()
    first = index.probe(policy_input)
    changed_prediction = replace(
        prediction,
        next_output_tokens=_distribution(512),
    )
    changed = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {changed_prediction.invocation_id: changed_prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )

    second = index.probe(changed)

    assert first.trigger_signature != second.trigger_signature


def test_eligibility_trigger_ignores_generation_only_physical_churn() -> None:
    graph = _graph()
    prediction = _prediction()
    policy_input = _attach_graph(
        _input(capacity=1_000, reserved=100, include_cpu_target=True), graph
    )
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    index = PredictiveEligibilityIndex()
    first = index.probe(policy_input)
    changed_bundles = tuple(
        replace(bundle, generation_fingerprint=f"next-{bundle.bundle_id}")
        for bundle in policy_input.physical_kv.bundles
    )
    snapshot_id = "generation-only-change"
    generation_only = replace(
        policy_input,
        physical_kv=replace(
            policy_input.physical_kv,
            snapshot_id=snapshot_id,
            bundles=changed_bundles,
        ),
        runtime_graph=replace(policy_input.runtime_graph, snapshot_id=snapshot_id),
        resources=replace(policy_input.resources, snapshot_id=snapshot_id),
    )

    second = index.probe(generation_only)

    assert first.trigger_signature == second.trigger_signature


def test_future_safe_prefetch_remains_deferred_without_prepare_evidence() -> None:
    graph = _graph()
    policy_input = _attach_graph(
        _input(capacity=1_100, reserved=100, include_cpu_target=True), graph
    )
    prediction = _prediction()
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
        ),
    ).evaluate(
        policy_input,
        graph=graph,
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.selected_action == "observed_baseline"
    assert result.candidate_summaries == ()
    assert result.blocked_reasons == ("no_projected_hbm_beneficiary",)


def test_reclaim_then_prefetch_is_deferred_until_prepare_is_consumed() -> None:
    graph = _graph()
    _add_waiting_victim(graph)
    policy_input = _input(capacity=800, reserved=100, include_cpu_target=True)
    bundles = tuple(
        replace(bundle, cpu_bytes=0)
        if bundle.bundle_id == "old"
        else bundle
        for bundle in policy_input.physical_kv.bundles
    )
    target_prediction = _prediction()
    old_prediction = replace(
        _prediction(), invocation_id="invocation-old"
    )
    policy_input = _attach_graph(
        replace(
            policy_input,
            physical_kv=replace(policy_input.physical_kv, bundles=bundles),
            optional_metadata={
                "frontier_predictions": MetadataValue(
                    MetadataSource.PREDICTED,
                    {
                        target_prediction.invocation_id: target_prediction.to_dict(),
                        old_prediction.invocation_id: old_prediction.to_dict(),
                    },
                    "test-frontier",
                ),
                "frontier_prediction_model_version": MetadataValue(
                    MetadataSource.PREDICTED,
                    "frontier-test-v1",
                    "test-frontier",
                ),
            },
        ),
        graph,
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("prefill-b", "prefill", 32),
            _service_row("decode-a", "decode", 16),
            _service_row("decode-b", "decode", 16),
        ]
    )

    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=6,
            kv_bytes_per_token=1,
        ),
    ).evaluate(
        policy_input,
        graph=graph,
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=0,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )

    assert result.selected_action == "observed_baseline"
    assert result.candidate_summaries == ()
    assert result.blocked_reasons == ("no_projected_hbm_beneficiary",)


def test_action_certificate_ignores_unrelated_global_revision() -> None:
    graph = _graph()
    policy_input = _attach_graph(
        _input(capacity=1_100, reserved=100, include_cpu_target=True), graph
    )
    prediction = _prediction()
    policy_input = replace(
        policy_input,
        optional_metadata={
            "frontier_predictions": MetadataValue(
                MetadataSource.PREDICTED,
                {prediction.invocation_id: prediction.to_dict()},
                "test-frontier",
            ),
            "frontier_prediction_model_version": MetadataValue(
                MetadataSource.PREDICTED,
                "frontier-test-v1",
                "test-frontier",
            ),
        },
    )
    source_plan = AsyncSemanticJointPlanner(
        JointPlannerConfig(max_planning_budget_ms=100.0)
    ).plan(policy_input)
    service_model = GPUServiceCurveModel(minimum_support=1)
    service_model.fit(
        [
            _service_row("prefill-a", "prefill", 32),
            _service_row("decode-a", "decode", 16),
        ]
    )
    result = PredictiveRiskShadowObserver(
        service_model,
        PredictiveRiskShadowConfig(
            particle_count=16,
            top_k=4,
            max_candidates=4,
            kv_bytes_per_token=1,
        ),
    ).evaluate(
        policy_input,
        graph=graph,
        source_plan=source_plan,
        evidence_read_set=PredictiveEvidenceReadSet(
            graph_version=3,
            page_revision=5,
            topology_revision=4,
            fairness_revision=0,
            admission_revision=0,
            transfer_epoch=7,
            obligation_revision=0,
            lease_revision=0,
            grace_revision=0,
            parser_frontier_revision=0,
            model_version="frontier-test-v1",
        ),
    )
    certificate = PredictiveActionCertificate(
        package_id="prepare:ctx-target",
        action="prepare_host",
        source_snapshot_id=policy_input.snapshot_id,
        target_context_id="ctx-target",
        context_epochs=(("ctx-target", 0),),
        invocation_evidence=(
            ("invocation-target", "wait_tool", 100.0, None),
        ),
        join_evidence=(),
        communication_evidence=(),
        bundle_evidence=tuple(
            (
                bundle.bundle_id,
                bundle.generation_fingerprint,
                bundle.gpu_bytes,
                bundle.cpu_bytes,
            )
            for bundle in policy_input.physical_kv.bundles
            if "ctx-target" in bundle.owner_context_ids
        ),
        required_hbm_free_bytes=0,
        required_host_free_bytes=0,
        transfer_epoch=7,
        transfer_service_evidence=(100.0, 100.0, 0.1),
        model_version="frontier-test-v1",
    ).to_dict()
    unrelated_revision = replace(
        policy_input,
        runtime_graph=replace(
            policy_input.runtime_graph,
            graph_version=policy_input.runtime_graph.graph_version + 1,
        ),
    )

    assert validate_predictive_certificate(
        certificate,
        unrelated_revision,
        current_transfer_epoch=7,
    ) == ()

    changed_bundles = tuple(
        replace(bundle, generation_fingerprint="changed-generation")
        if "ctx-target" in bundle.owner_context_ids
        else bundle
        for bundle in policy_input.physical_kv.bundles
    )
    changed_physical = replace(
        policy_input,
        physical_kv=replace(policy_input.physical_kv, bundles=changed_bundles),
    )
    assert any(
        reason.startswith("bundle_generation:")
        for reason in validate_predictive_certificate(
            certificate,
            changed_physical,
            current_transfer_epoch=7,
        )
    )

    graph.invocations["invocation-target"].updated_ts_ms += 1.0
    changed_causal = _attach_graph(policy_input, graph)
    assert any(
        reason.startswith("invocation_revision:invocation-target")
        for reason in validate_predictive_certificate(
            certificate,
            changed_causal,
            current_transfer_epoch=7,
        )
    )
