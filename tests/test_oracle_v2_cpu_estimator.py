from __future__ import annotations

import json

import pytest

from beliefkv.oracle import (
    FrozenActionBoundary,
    FrozenActionBoundaryKind,
    FrozenAgentDemand,
    FrozenContextMode,
    FrozenDemandProvenance,
    FrozenInvocationDemand,
    FrozenInvocationRelation,
    FrozenLLMCallDemand,
    FrozenPhysicalCall,
    FrozenPhysicalSidecar,
    FrozenToolDemand,
    FrozenToolOutcome,
    LogicalInvocationKey,
)
from beliefkv.oracle.cpu_estimator import (
    CPUOracleArm,
    CPUOracleConfig,
    PlannerOverhead,
    ServiceEnvelope,
    _ContextRadixResidency,
    _JointOpportunity,
    _ServiceEstimator,
    _Simulation,
)


def _logical_key(name: str) -> LogicalInvocationKey:
    return LogicalInvocationKey(
        workload_instance="task-1",
        canonical_agent_path=("root", name),
        parent_spawn_ordinal=0,
        invocation_ordinal=0,
        context_epoch=0,
    )


def test_physical_sidecar_is_strict_and_round_trips() -> None:
    call = FrozenPhysicalCall(
        invocation=_logical_key("child"),
        call_ordinal=0,
        trace_request_ordinal=0,
        runtime_context_epoch=0,
        observed_cache_hit_tokens=1,
        observed_unique_growth_bytes=3 * 98_304,
        prompt_token_symbols=(11, 12),
        cache_commit_token_symbols=(11, 12, 13),
    )
    expected = FrozenPhysicalSidecar(
        truth_id="truth-1",
        truth_digest="digest-1",
        source_trace_id="trace-1",
        kv_bytes_per_token=98_304,
        initial_radix_state="empty_server_boot",
        calls=(call,),
    )

    assert FrozenPhysicalSidecar.from_json_bytes(
        expected.canonical_bytes()
    ).canonical_bytes() == expected.canonical_bytes()

    malformed = json.loads(expected.canonical_bytes())
    malformed["calls"] = [42]
    with pytest.raises(TypeError, match="calls must contain only objects"):
        FrozenPhysicalSidecar.from_json_bytes(
            json.dumps(malformed).encode("utf-8")
        )


def test_context_radix_counts_shared_unique_tokens() -> None:
    radix = _ContextRadixResidency()
    left = _logical_key("left")
    right = _logical_key("right")

    assert radix.replace(left, (1, 2, 3)) == 3
    assert radix.replace(right, (1, 2, 4)) == 1
    assert radix.unique_tokens == 4
    assert radix.prefix_hit((1, 2, 5)) == 2

    assert radix.remove(left) == 1
    assert radix.unique_tokens == 3
    assert radix.remove(right) == 3
    assert radix.unique_tokens == 0


def test_context_radix_extends_owner_without_rebuilding_prefix() -> None:
    radix = _ContextRadixResidency()
    owner = _logical_key("append-only")

    assert radix.replace(owner, (1, 2, 3)) == 3
    original_leaf = radix.leaves[owner]
    assert radix.prefix_hit_for_owner(owner, (1, 2, 3, 4, 5)) == 3
    assert radix.replace(owner, (1, 2, 3, 4, 5)) == 2
    assert radix.unique_tokens == 5
    assert radix.leaves[owner] is not original_leaf
    assert radix.remove(owner) == 5
    assert radix.unique_tokens == 0


def test_runtime_service_profile_selects_requested_envelope() -> None:
    profile = {
        "prefill": {
            1: {"p50_ms": 100.0, "p95_ms": 200.0, "mean_ms": 150.0}
        },
        "decode": {
            1: {"p50_ms": 10.0, "p95_ms": 20.0, "mean_ms": 15.0},
            4: {"p50_ms": 30.0, "p95_ms": 40.0, "mean_ms": 35.0},
        },
    }
    nominal = _ServiceEstimator(
        object(),  # type: ignore[arg-type]
        ServiceEnvelope.NOMINAL,
        runtime_profile=profile,
    )
    slow = _ServiceEstimator(
        object(),  # type: ignore[arg-type]
        ServiceEnvelope.SLOW,
        runtime_profile=profile,
    )
    validation = _ServiceEstimator(
        object(),  # type: ignore[arg-type]
        ServiceEnvelope.NOMINAL,
        runtime_profile=profile,
        runtime_statistic="mean_ms",
    )
    graph32 = _ServiceEstimator(
        object(),  # type: ignore[arg-type]
        ServiceEnvelope.GRAPH32_SENSITIVITY,
        runtime_profile=profile,
    )

    assert nominal.prefill_ms(
        sequence_tokens=0, token_delta=128, first_chunk=True
    ) == 100.0
    assert slow.decode_ms((1000, 1000, 1000)) == 40.0
    assert validation.decode_ms((1000,)) == 15.0
    assert graph32.decode_ms(tuple([1000] * 32)) == pytest.approx(6.26)


def test_invalid_runtime_service_statistic_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid runtime service statistic"):
        _ServiceEstimator(
            object(),  # type: ignore[arg-type]
            ServiceEnvelope.NOMINAL,
            runtime_statistic="maximum_ms",
        )


def test_control_quantum_must_be_positive() -> None:
    with pytest.raises(ValueError, match="planning limits"):
        CPUOracleConfig(control_quantum_tokens=0)

def _oracle_root(
    name: str,
    *,
    path: tuple[int, ...],
    tool_wait_ms: float | None,
    trace_ordinal: int,
) -> tuple[FrozenInvocationDemand, tuple[FrozenPhysicalCall, ...]]:
    key = LogicalInvocationKey(
        workload_instance=name,
        canonical_agent_path=("root",),
        parent_spawn_ordinal=0,
        invocation_ordinal=0,
        context_epoch=0,
    )
    if tool_wait_ms is None:
        calls = (
            FrozenLLMCallDemand(
                call_ordinal=0,
                prompt_tokens=len(path),
                incremental_prompt_tokens=len(path),
                output_tokens=1,
                parent_reentry_prompt_growth_tokens=0,
                boundary=FrozenActionBoundary(FrozenActionBoundaryKind.FINAL),
            ),
        )
        tools = ()
    else:
        calls = (
            FrozenLLMCallDemand(
                call_ordinal=0,
                prompt_tokens=len(path),
                incremental_prompt_tokens=len(path),
                output_tokens=1,
                parent_reentry_prompt_growth_tokens=0,
                boundary=FrozenActionBoundary(
                    FrozenActionBoundaryKind.TOOL,
                    tool_ordinals=(0,),
                ),
            ),
            FrozenLLMCallDemand(
                call_ordinal=1,
                prompt_tokens=len(path),
                incremental_prompt_tokens=0,
                output_tokens=1,
                parent_reentry_prompt_growth_tokens=0,
                boundary=FrozenActionBoundary(FrozenActionBoundaryKind.FINAL),
            ),
        )
        tools = (
            FrozenToolDemand(
                tool_ordinal=0,
                starts_after_call_ordinal=0,
                tool_family="synthetic_wait",
                backend_class="timer",
                service_duration_ms=tool_wait_ms,
                result_prompt_tokens=0,
                outcome=FrozenToolOutcome.SUCCESS,
            ),
        )

    invocation = FrozenInvocationDemand(
        key=key,
        agent_definition_id=name,
        relation=FrozenInvocationRelation.ROOT,
        context_mode=FrozenContextMode.FRESH,
        parent=None,
        semantic_owner=key,
        calls=calls,
        tools=tools,
    )
    physical = tuple(
        FrozenPhysicalCall(
            invocation=key,
            call_ordinal=call.call_ordinal,
            trace_request_ordinal=trace_ordinal + call.call_ordinal,
            runtime_context_epoch=0,
            observed_cache_hit_tokens=0 if call.call_ordinal == 0 else len(path),
            observed_unique_growth_bytes=len(path),
            prompt_token_symbols=path,
            cache_commit_token_symbols=path,
        )
        for call in calls
    )
    return invocation, physical


def _causal_transfer_simulation(arm: CPUOracleArm) -> _Simulation:
    parent_near, near_physical = _oracle_root(
        "parent-near",
        path=tuple(range(1, 7)),
        tool_wait_ms=200.0,
        trace_ordinal=0,
    )
    parent_far, far_physical = _oracle_root(
        "parent-far",
        path=tuple(range(11, 17)),
        tool_wait_ms=1_000.0,
        trace_ordinal=10,
    )
    beneficiary, beneficiary_physical = _oracle_root(
        "beneficiary",
        path=tuple(range(21, 29)),
        tool_wait_ms=None,
        trace_ordinal=20,
    )
    truth = FrozenAgentDemand(
        provenance=FrozenDemandProvenance(
            truth_id="synthetic-causal-transfer",
            source_trace_id="synthetic",
            workload_manifest_id="synthetic",
            model_revision="synthetic",
            tokenizer_revision="synthetic",
            runtime_revision="synthetic",
            harness_revision="synthetic",
            exporter_revision="synthetic",
        ),
        invocations=(parent_near, parent_far, beneficiary),
    )
    sidecar = FrozenPhysicalSidecar(
        truth_id=truth.truth_id,
        truth_digest=truth.truth_digest,
        source_trace_id="synthetic",
        kv_bytes_per_token=1,
        initial_radix_state="empty_server_boot",
        calls=near_physical + far_physical + beneficiary_physical,
    )
    profile = {
        "prefill": {
            1: {"p50_ms": 1.0, "p95_ms": 1.0, "mean_ms": 1.0},
        },
        "decode": {
            1: {"p50_ms": 1.0, "p95_ms": 1.0, "mean_ms": 1.0},
            2: {"p50_ms": 1.0, "p95_ms": 1.0, "mean_ms": 1.0},
            3: {"p50_ms": 1.0, "p95_ms": 1.0, "mean_ms": 1.0},
        },
    }
    return _Simulation(
        truth=truth,
        sidecar=sidecar,
        service_model=object(),  # type: ignore[arg-type]
        transfer_rates={
            "d2h": {"p50": 1.0, "p95": 1.0},
            "h2d": {"p50": 1.0, "p95": 1.0},
        },
        workflow_release_ms={
            "parent-near": 0.0,
            "parent-far": 10.0,
            "beneficiary": 50.0,
        },
        config=CPUOracleConfig(
            hbm_capacity_tokens=18,
            host_capacity_bytes=100,
            max_running_requests=32,
            prefill_chunk_tokens=32,
            transfer_commit_guard_ms=2.0,
        ),
        arm=arm,
        envelope=ServiceEnvelope.NOMINAL,
        overhead=PlannerOverhead.ZERO,
        cross_run_prefix_identity="synthetic_no_share",
        runtime_service_profile=profile,
        runtime_service_statistic=None,
    )


def test_causal_kv_oracle_shadows_far_parent_and_prefetches_at_latest_start() -> None:
    current = _causal_transfer_simulation(CPUOracleArm.C0_CURRENT)
    current_result = current.run()
    oracle = _causal_transfer_simulation(CPUOracleArm.C2_KV)
    oracle_result = oracle.run()

    assert current.victim_history[0].workload_instance == "parent-near"
    assert oracle.victim_history[0].workload_instance == "parent-far"
    assert oracle_result.proactive_shadow_count >= 1
    assert oracle_result.shadow_commit_count >= 1
    assert oracle_result.latest_prefetch_count >= 1
    assert oracle_result.d2h_bytes > 0
    assert oracle_result.h2d_bytes > 0
    assert oracle_result.joint_opportunity_window_count >= 1
    assert oracle_result.opportunity_victim_reentry_ratio == 1.0
    assert current_result.transfer_count >= 2


def test_future_ready_call_tracks_tool_return_residual_boundary() -> None:
    simulation = _causal_transfer_simulation(CPUOracleArm.C2_KV)
    state = next(
        item
        for key, item in simulation.invocations.items()
        if key.workload_instance == "parent-near"
    )
    state.created = True
    state.next_call_index = 1
    state.pending_tools = 1
    state.tool_completion_ms = 200.0

    future = simulation._future_ready_call(state, set())

    assert future is not None
    timestamp, demand, _ = future
    assert timestamp == 200.0
    assert demand.call_ordinal == 1


def test_opportunity_byte_time_deduplicates_victim_and_beneficiary() -> None:
    simulation = _causal_transfer_simulation(CPUOracleArm.C0_CURRENT)
    victim = _logical_key("victim")
    first = (_logical_key("first-beneficiary"), 0)
    second = (_logical_key("second-beneficiary"), 0)
    common = {
        "victim": victim,
        "reclaimable_bytes": 100,
        "slack_ms": 1_000.0,
        "d2h_ms": 10.0,
        "h2d_ms": 10.0,
        "beneficiary_gain_ms": 100.0,
        "restore_stall_ms": 0.0,
        "stall_free_round_trip": True,
        "net_positive": True,
    }
    simulation._install_joint_opportunities(
        {
            (victim, first): _JointOpportunity(beneficiary=first, **common),
            (victim, second): _JointOpportunity(beneficiary=second, **common),
        }
    )
    simulation._advance_time(10.0)

    assert simulation.opportunity_window_counts["eviction"] == 2
    assert simulation.opportunity_unique_victim_byte_ms["eviction"] == 1_000.0
    assert simulation.opportunity_blocked_beneficiary_work_ms["eviction"] == 20.0
    assert (
        simulation.opportunity_unique_victim_byte_ms["stall_free_round_trip"]
        == 1_000.0
    )
