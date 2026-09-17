from __future__ import annotations

from dataclasses import replace
from beliefkv.policy.predictive_joint import (
    PackageScenarioEvaluation,
    PredictiveActionKind,
    PredictiveActionPackage,
    ScenarioCost,
)
from beliefkv.policy.predictive_timeline import (
    CandidatePhysicalPlan,
    CandidateTimelineEvaluator,
    PhysicalizedInvocationDemand,
    ScheduledBatchQuantum,
    ScheduledRequestQuantum,
    ScheduledTransfer,
)
from beliefkv.predictor.frontier_belief import (
    BoundaryEvent,
    DemandPhase,
    DemandScenario,
    DependencyMode,
    FrontierDemandOutcome,
)
from beliefkv.predictor.hardware_service import GPUServiceCurveModel


def _batch_row(
    sample_id: str,
    requests: tuple[tuple[int, int], ...],
    elapsed_ms: float,
) -> dict[str, object]:
    return {
        "row_type": "gpu_batch_service_interval",
        "sample_id": sample_id,
        "split": "train",
        "phase": "decode",
        "batch_size": len(requests),
        "request_samples": [
            {
                "request_id": f"{sample_id}-{index}",
                "sequence_tokens_before": sequence,
                "token_delta": tokens,
                "cache_hit_ratio": 0.0,
            }
            for index, (sequence, tokens) in enumerate(requests)
        ],
        "chunk_position": "first",
        "prefill_decode_mixed": False,
        "pcie_contention_state": "idle",
        "hicache_inflight_bytes": 0,
        "service_elapsed_ms": elapsed_ms,
        "warmup": False,
        "evidence_role": "controlled_microbenchmark",
    }


def _service_model() -> GPUServiceCurveModel:
    model = GPUServiceCurveModel(minimum_support=1)
    model.fit(
        [
            _batch_row("single-a", ((4096, 100),), 10.0),
            _batch_row("single-b", ((4096, 100),), 10.0),
            _batch_row("batched-a", ((4096, 100), (4096, 100)), 12.0),
            _batch_row("batched-b", ((4096, 100), (4096, 100)), 12.0),
        ]
    )
    return model


def _scenario() -> DemandScenario:
    children = tuple(
        FrontierDemandOutcome(
            invocation_id=child,
            boundary_event=BoundaryEvent.RETURN,
            dependency_mode=DependencyMode.NONE,
            phase=DemandPhase.DECODE,
            current_sequence_tokens=4096,
            remaining_decode_tokens=100,
            prompt_growth_tokens=0,
            next_output_tokens=0,
        )
        for child in ("child-a", "child-b")
    )
    parent = FrontierDemandOutcome(
        invocation_id="parent",
        boundary_event=BoundaryEvent.UNKNOWN,
        dependency_mode=DependencyMode.JOIN_ALL,
        phase=DemandPhase.EXTERNAL,
        current_sequence_tokens=8192,
        remaining_decode_tokens=0,
        prompt_growth_tokens=128,
        next_output_tokens=16,
        dependency_invocation_ids=("child-a", "child-b"),
        join_id="join",
    )
    return DemandScenario("scenario", (*children, parent), 1.0)


def _physical_demands() -> tuple[PhysicalizedInvocationDemand, ...]:
    return tuple(
        PhysicalizedInvocationDemand(child, 0, 100, 4096)
        for child in ("child-a", "child-b")
    )


def test_join_is_resolved_after_candidate_batch_schedule() -> None:
    evaluator = CandidateTimelineEvaluator(_service_model(), service_quantile=0.9)
    serial = CandidatePhysicalPlan(
        package_id="serial",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=_physical_demands(),
        batches=tuple(
            ScheduledBatchQuantum(
                f"serial-{index}",
                DemandPhase.DECODE,
                (ScheduledRequestQuantum(child, 100, 4096),),
                chunk_position="first",
            )
            for index, child in enumerate(("child-a", "child-b"))
        ),
    )
    batched = CandidatePhysicalPlan(
        package_id="batched",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=_physical_demands(),
        batches=(
            ScheduledBatchQuantum(
                "batched",
                DemandPhase.DECODE,
                (
                    ScheduledRequestQuantum("child-a", 100, 4096),
                    ScheduledRequestQuantum("child-b", 100, 4096),
                ),
                chunk_position="first",
            ),
        ),
    )

    serial_timeline = evaluator.evaluate(_scenario(), serial)
    batched_timeline = evaluator.evaluate(_scenario(), batched)

    assert (
        serial_timeline.join_reentry_offsets_ms["join"]
        > batched_timeline.join_reentry_offsets_ms["join"]
    )
    assert serial_timeline.join_reentry_offsets_ms["join"] != max(100, 100)


def test_join_release_respects_child_return_completion_floor() -> None:
    scenario = _scenario()
    outcomes = tuple(
        replace(item, completion_floor_ms=300_000.0)
        if item.invocation_id == "child-a"
        else item
        for item in scenario.outcomes
    )
    evaluator = CandidateTimelineEvaluator(_service_model(), service_quantile=0.9)
    plan = CandidatePhysicalPlan(
        package_id="child-return-floor",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=_physical_demands(),
        batches=tuple(
            ScheduledBatchQuantum(
                child,
                DemandPhase.DECODE,
                (ScheduledRequestQuantum(child, 100, 4096),),
                chunk_position="first",
            )
            for child in ("child-a", "child-b")
        ),
    )

    timeline = evaluator.evaluate(
        DemandScenario("child-return-floor", outcomes, 1.0), plan
    )
    child = next(
        item
        for item in timeline.invocation_outcomes
        if item.invocation_id == "child-a"
    )

    assert child.completion_offset_ms == 300_000.0
    assert child.completion_source == "child_completion_model"
    assert timeline.join_reentry_offsets_ms["join"] == 300_000.0


def test_service_estimates_are_reused_across_candidate_timelines() -> None:
    evaluator = CandidateTimelineEvaluator(_service_model(), service_quantile=0.9)
    plan = CandidatePhysicalPlan(
        package_id="batched",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=_physical_demands(),
        batches=(
            ScheduledBatchQuantum(
                "batched",
                DemandPhase.DECODE,
                (
                    ScheduledRequestQuantum("child-a", 100, 4096),
                    ScheduledRequestQuantum("child-b", 100, 4096),
                ),
                chunk_position="first",
            ),
        ),
    )

    evaluator.evaluate(_scenario(), plan)
    first_hits, first_misses, entries = evaluator.service_cache_stats()
    evaluator.evaluate(_scenario(), plan)
    second_hits, second_misses, _ = evaluator.service_cache_stats()

    assert (first_hits, first_misses, entries) == (0, 1, 1)
    assert second_hits == 1
    assert second_misses == 1


def _beneficiary_scenario() -> DemandScenario:
    outcomes = tuple(
        FrontierDemandOutcome(
            invocation_id=invocation_id,
            boundary_event=BoundaryEvent.UNKNOWN,
            dependency_mode=DependencyMode.NONE,
            phase=DemandPhase.DECODE,
            current_sequence_tokens=4096,
            remaining_decode_tokens=100,
            prompt_growth_tokens=0,
            next_output_tokens=0,
        )
        for invocation_id in ("running", "beneficiary")
    )
    return DemandScenario("beneficiary-scenario", outcomes, 1.0)


def _beneficiary_plan(*, demand_bytes: int) -> CandidatePhysicalPlan:
    return CandidatePhysicalPlan(
        package_id="beneficiary-plan",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=tuple(
            PhysicalizedInvocationDemand(invocation_id, 0, 100, 4096)
            for invocation_id in ("running", "beneficiary")
        ),
        batches=tuple(
            ScheduledBatchQuantum(
                invocation_id,
                DemandPhase.DECODE,
                (ScheduledRequestQuantum(invocation_id, 100, 4096),),
                chunk_position="first",
            )
            for invocation_id in ("running", "beneficiary")
        ),
        hbm_capacity_bytes=1_000,
        initial_hbm_used_bytes=700,
        kv_bytes_per_token=1,
        projected_beneficiary_request_id="request-beneficiary",
        projected_beneficiary_invocation_id="beneficiary",
        projected_beneficiary_startup_bytes=demand_bytes,
    )


def test_slot_wait_without_hbm_deficit_is_not_a_projected_kv_opportunity() -> None:
    timeline = CandidateTimelineEvaluator(_service_model()).evaluate(
        _beneficiary_scenario(),
        _beneficiary_plan(demand_bytes=200),
    )

    assert timeline.service_quanta[1].start_offset_ms > 0
    assert timeline.projected_beneficiary_block_offset_ms is None
    assert timeline.projected_beneficiary_deficit_bytes == 0
    assert timeline.future_hbm_overflow_bytes == 0


def test_beneficiary_attempt_records_the_exact_projected_hbm_deficit() -> None:
    timeline = CandidateTimelineEvaluator(_service_model()).evaluate(
        _beneficiary_scenario(),
        _beneficiary_plan(demand_bytes=350),
    )

    assert (
        timeline.projected_beneficiary_block_offset_ms
        == timeline.service_quanta[1].start_offset_ms
    )
    # The first running quantum grows resident KV by 100 bytes before the
    # beneficiary attempts admission: 700 + 100 + 350 - 1000 = 150.
    assert timeline.projected_beneficiary_deficit_bytes == 150
    assert timeline.future_hbm_overflow_bytes == 0



def test_beneficiary_growth_derives_future_block_from_service_timeline() -> None:
    plan = replace(
        _beneficiary_plan(demand_bytes=50),
        initial_hbm_used_bytes=850,
        projected_beneficiary_deficit_bytes=50,
    )

    timeline = CandidateTimelineEvaluator(_service_model()).evaluate(
        _beneficiary_scenario(),
        plan,
    )

    assert (
        timeline.projected_beneficiary_block_offset_ms
        == timeline.service_quanta[1].completion_offset_ms
    )
    assert timeline.projected_beneficiary_deficit_bytes == 50
    assert timeline.future_hbm_overflow_bytes == 50


def test_explicit_beneficiary_block_time_requires_positive_deficit() -> None:
    try:
        replace(
            _beneficiary_plan(demand_bytes=50),
            projected_beneficiary_block_offset_ms=10.0,
        )
    except ValueError as exc:
        assert "block evidence" in str(exc)
    else:
        raise AssertionError("explicit block time without a deficit must be rejected")


def test_projected_beneficiary_bytes_must_be_non_negative() -> None:
    try:
        CandidatePhysicalPlan(
            package_id="invalid-beneficiary",
            physical_snapshot_id="snapshot",
            physical_snapshot_revision=1,
            invocation_demands=(),
            batches=(),
            hbm_capacity_bytes=1_000,
            kv_bytes_per_token=1,
            projected_beneficiary_request_id="request",
            projected_beneficiary_invocation_id="invocation",
            projected_beneficiary_startup_bytes=-1,
        )
    except ValueError as exc:
        assert "HBM ledger" in str(exc)
    else:
        raise AssertionError("negative projected demand must be rejected")


def test_timed_scenario_is_the_only_input_to_risk_cost() -> None:
    evaluator = CandidateTimelineEvaluator(_service_model())
    plan = CandidatePhysicalPlan(
        package_id="batched",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=_physical_demands(),
        batches=(
            ScheduledBatchQuantum(
                "batched",
                DemandPhase.DECODE,
                (
                    ScheduledRequestQuantum("child-a", 100, 4096),
                    ScheduledRequestQuantum("child-b", 100, 4096),
                ),
                chunk_position="first",
            ),
        ),
    )
    timeline = evaluator.evaluate(_scenario(), plan)
    package = PredictiveActionPackage(
        "batched", PredictiveActionKind.PREFETCH_GPU, ("context",)
    )
    evaluation = PackageScenarioEvaluation.from_timed_scenarios(
        package,
        {"scenario": timeline},
        unlock_invocation_ids=("parent",),
        other_cost=ScenarioCost(100, 0),
    )

    assert evaluation.costs_by_scenario["scenario"].action_unlock_delay_ms == (
        timeline.join_reentry_offsets_ms["join"]
    )
    assert evaluation.costs_by_scenario["scenario"].deterministic_feasible


def test_join_release_and_parent_completion_are_distinct() -> None:
    evaluator = CandidateTimelineEvaluator(_service_model())
    plan = CandidatePhysicalPlan(
        package_id="parent-resume",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=(
            *_physical_demands(),
            PhysicalizedInvocationDemand("parent", 0, 16, 8192),
        ),
        batches=(
            ScheduledBatchQuantum(
                "children",
                DemandPhase.DECODE,
                (
                    ScheduledRequestQuantum("child-a", 100, 4096),
                    ScheduledRequestQuantum("child-b", 100, 4096),
                ),
                chunk_position="first",
            ),
            ScheduledBatchQuantum(
                "parent",
                DemandPhase.DECODE,
                (ScheduledRequestQuantum("parent", 16, 8192),),
                chunk_position="first",
                ready_after_transfer_ids=("restore-parent",),
            ),
        ),
        transfers=(ScheduledTransfer("restore-parent", 0.0, 25.0, 25.0),),
    )

    timeline = evaluator.evaluate(_scenario(), plan)
    parent = next(
        item for item in timeline.invocation_outcomes if item.invocation_id == "parent"
    )

    assert timeline.join_reentry_offsets_ms["join"] == (
        timeline.service_quanta[0].completion_offset_ms
    )
    assert parent.completion_offset_ms == (
        timeline.service_quanta[-1].completion_offset_ms
    )
    assert parent.completion_offset_ms > timeline.join_reentry_offsets_ms["join"]
    assert timeline.service_quanta[-1].start_offset_ms == 25.0


def test_reactive_restore_starts_only_after_join_release() -> None:
    evaluator = CandidateTimelineEvaluator(_service_model())
    plan = CandidatePhysicalPlan(
        package_id="reactive-parent",
        physical_snapshot_id="snapshot",
        physical_snapshot_revision=1,
        invocation_demands=(
            *_physical_demands(),
            PhysicalizedInvocationDemand("parent", 0, 16, 8192),
        ),
        batches=(
            ScheduledBatchQuantum(
                "children",
                DemandPhase.DECODE,
                (
                    ScheduledRequestQuantum("child-a", 100, 4096),
                    ScheduledRequestQuantum("child-b", 100, 4096),
                ),
            ),
            ScheduledBatchQuantum(
                "parent",
                DemandPhase.DECODE,
                (ScheduledRequestQuantum("parent", 16, 8192),),
                ready_after_transfer_ids=("restore-parent",),
            ),
        ),
        transfers=(
            ScheduledTransfer(
                "restore-parent",
                0.0,
                25.0,
                25.0,
                ready_after_dependency_release_ids=("parent",),
            ),
        ),
    )

    timeline = evaluator.evaluate(_scenario(), plan)
    join_release = timeline.join_reentry_offsets_ms["join"]

    assert timeline.transfer_completion_offsets_ms["restore-parent"] == (
        join_release + 25.0
    )
    assert timeline.service_quanta[-1].start_offset_ms == join_release + 25.0


def test_explicit_projected_block_is_used_without_a_batch_attempt() -> None:
    plan = replace(
        _beneficiary_plan(demand_bytes=200),
        batches=(),
        projected_beneficiary_block_offset_ms=750.0,
        projected_beneficiary_deficit_bytes=125,
    )

    timeline = CandidateTimelineEvaluator(_service_model()).evaluate(
        _beneficiary_scenario(),
        plan,
    )

    assert timeline.projected_beneficiary_block_offset_ms == 750.0
    assert timeline.projected_beneficiary_deficit_bytes == 125
    assert timeline.first_hbm_pressure_offset_ms == 750.0
    assert timeline.first_hbm_pressure_deficit_bytes == 125
