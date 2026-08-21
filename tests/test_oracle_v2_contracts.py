from __future__ import annotations

from dataclasses import replace

import pytest

from beliefkv.oracle import (
    AgentFutureField,
    FrozenActionBoundary,
    FrozenActionBoundaryKind,
    FrozenAgentDemand,
    FrozenContextMode,
    FrozenDemandProvenance,
    FrozenInvocationDemand,
    FrozenInvocationRelation,
    FrozenJoinDemand,
    FrozenJoinKey,
    FrozenJoinMode,
    FrozenLLMCallDemand,
    FrozenToolDemand,
    FrozenToolKey,
    KVFutureField,
    LogicalInvocationKey,
    OracleCallPhase,
    OracleFutureAccessViolation,
    OracleInvocationProgress,
    OracleReplayCursor,
    OracleReplayCursorError,
    OracleTruthProvider,
    PerfectFutureOracleArm,
)
from beliefkv.policy.joint_oracle import ORACLE_IMPLEMENTATION_STATUS


def _keys():
    root = LogicalInvocationKey("task-1", ("supervisor",), 0, 0, 0)
    child = LogicalInvocationKey(
        "task-1",
        ("supervisor", "researcher"),
        0,
        0,
        0,
    )
    resumed = LogicalInvocationKey(
        "task-1",
        ("supervisor", "researcher", "handoff-reviewer"),
        0,
        0,
        0,
    )
    return root, child, resumed


def _call(
    ordinal: int,
    boundary: FrozenActionBoundary,
    *,
    prompt: int = 128,
    incremental: int = 32,
    output: int = 16,
    reentry: int = 0,
) -> FrozenLLMCallDemand:
    return FrozenLLMCallDemand(
        call_ordinal=ordinal,
        prompt_tokens=prompt,
        incremental_prompt_tokens=incremental,
        output_tokens=output,
        parent_reentry_prompt_growth_tokens=reentry,
        boundary=boundary,
    )


def _demand(*, reverse: bool = False) -> FrozenAgentDemand:
    root, child, resumed = _keys()
    join_key = FrozenJoinKey("task-1", root, 0)
    root_calls = (
        _call(
            0,
            FrozenActionBoundary(
                FrozenActionBoundaryKind.SPAWN,
                target_invocations=(child,),
                output_token_offset=8,
            ),
        ),
        _call(
            1,
            FrozenActionBoundary(
                FrozenActionBoundaryKind.JOIN,
                joins=(join_key,),
            ),
            reentry=64,
        ),
        _call(2, FrozenActionBoundary(FrozenActionBoundaryKind.FINAL)),
    )
    child_calls = (
        _call(
            0,
            FrozenActionBoundary(
                FrozenActionBoundaryKind.TOOL,
                tool_ordinals=(0,),
            ),
        ),
        _call(
            1,
            FrozenActionBoundary(
                FrozenActionBoundaryKind.HANDOFF,
                target_invocations=(resumed,),
            ),
        ),
        _call(
            2,
            FrozenActionBoundary(
                FrozenActionBoundaryKind.RETURN,
                target_invocations=(root,),
            ),
        ),
    )
    resumed_calls = (
        _call(
            0,
            FrozenActionBoundary(
                FrozenActionBoundaryKind.RETURN,
                target_invocations=(child,),
            ),
        ),
    )
    root_invocation = FrozenInvocationDemand(
        key=root,
        agent_definition_id="supervisor",
        relation=FrozenInvocationRelation.ROOT,
        context_mode=FrozenContextMode.FRESH,
        parent=None,
        semantic_owner=root,
        calls=tuple(reversed(root_calls)) if reverse else root_calls,
    )
    child_invocation = FrozenInvocationDemand(
        key=child,
        agent_definition_id="researcher",
        relation=FrozenInvocationRelation.SPAWN,
        context_mode=FrozenContextMode.FRESH,
        parent=root,
        semantic_owner=child,
        calls=tuple(reversed(child_calls)) if reverse else child_calls,
        tools=(
            FrozenToolDemand(
                tool_ordinal=0,
                starts_after_call_ordinal=0,
                tool_family="read_file",
                backend_class="sandbox",
                service_duration_ms=125.5,
                result_prompt_tokens=80,
            ),
        ),
    )
    resumed_invocation = FrozenInvocationDemand(
        key=resumed,
        agent_definition_id="reviewer",
        relation=FrozenInvocationRelation.HANDOFF,
        context_mode=FrozenContextMode.RESUME,
        parent=child,
        semantic_owner=child,
        calls=resumed_calls,
    )
    join = FrozenJoinDemand(
        key=join_key,
        mode=FrozenJoinMode.ALL,
        members=(resumed, child) if reverse else (child, resumed),
        waiters=(root,),
    )
    invocations = (resumed_invocation, child_invocation, root_invocation) if reverse else (
        root_invocation,
        child_invocation,
        resumed_invocation,
    )
    return FrozenAgentDemand(
        provenance=FrozenDemandProvenance(
            truth_id="oracle-v2-fixture",
            source_trace_id="trace-fixture",
            workload_manifest_id="manifest-fixture",
            model_revision="model-fixture",
            tokenizer_revision="tokenizer-fixture",
            runtime_revision="runtime-fixture",
            harness_revision="harness-fixture",
            exporter_revision="exporter-fixture",
        ),
        invocations=invocations,
        joins=(join,),
    )


def _cursor(
    demand: FrozenAgentDemand,
    *,
    revision: int = 0,
    progress_updates: dict[
        LogicalInvocationKey, OracleInvocationProgress
    ] | None = None,
    completed_invocations: tuple[LogicalInvocationKey, ...] = (),
    completed_tools: tuple[FrozenToolKey, ...] = (),
    satisfied_joins: tuple[FrozenJoinKey, ...] = (),
) -> OracleReplayCursor:
    updates = progress_updates or {}
    progress = []
    for invocation in demand.invocations:
        progress.append(
            updates.get(
                invocation.key,
                OracleInvocationProgress(
                    logical_key=invocation.key,
                    current_call_ordinal=invocation.calls[0].call_ordinal,
                    call_phase=OracleCallPhase.NOT_STARTED,
                    prefilled_prompt_tokens=0,
                    generated_tokens=0,
                ),
            )
        )
    return OracleReplayCursor(
        cursor_revision=revision,
        invocation_progress=tuple(progress),
        completed_invocations=completed_invocations,
        completed_tools=completed_tools,
        satisfied_joins=satisfied_joins,
    )


def _provider(
    demand: FrozenAgentDemand,
    arm: PerfectFutureOracleArm,
    *,
    capacity: int = 4096,
) -> OracleTruthProvider:
    return OracleTruthProvider(
        demand,
        arm=arm,
        expected_truth_id=demand.truth_id,
        expected_truth_digest=demand.truth_digest,
        access_ledger_capacity=capacity,
    )


def _completed_progress(
    demand: FrozenAgentDemand,
    logical_key: LogicalInvocationKey,
) -> OracleInvocationProgress:
    invocation = next(item for item in demand.invocations if item.key == logical_key)
    call = invocation.calls[-1]
    return OracleInvocationProgress(
        logical_key=logical_key,
        current_call_ordinal=call.call_ordinal,
        call_phase=OracleCallPhase.INVOCATION_COMPLETE,
        prefilled_prompt_tokens=call.incremental_prompt_tokens,
        generated_tokens=call.output_tokens,
    )


def test_frozen_demand_schema_v2_is_byte_stable_and_round_trips() -> None:
    expected = _demand()
    reordered = _demand(reverse=True)

    assert expected.schema_version == 2
    assert expected.canonical_bytes() == reordered.canonical_bytes()
    assert expected.truth_digest == reordered.truth_digest
    assert (
        FrozenAgentDemand.from_json_bytes(expected.canonical_bytes()).canonical_bytes()
        == expected.canonical_bytes()
    )


def test_frozen_demand_rejects_unknown_and_inexact_scalar_types() -> None:
    raw = _demand().to_dict()
    raw["historical_queue_wait_ms"] = 42
    with pytest.raises(ValueError, match="unknown fields"):
        FrozenAgentDemand.from_dict(raw)

    raw = _demand().to_dict()
    raw["invocations"][0]["calls"][0]["prompt_tokens"] = 128.9
    with pytest.raises(TypeError, match="prompt_tokens"):
        FrozenAgentDemand.from_dict(raw)

    raw = _demand().to_dict()
    raw["provenance"]["model_revision"] = None
    with pytest.raises(TypeError, match="model_revision"):
        FrozenAgentDemand.from_dict(raw)

    raw = _demand().to_dict()
    raw["schema_version"] = True
    with pytest.raises(TypeError, match="schema_version"):
        FrozenAgentDemand.from_dict(raw)

    with pytest.raises(ValueError, match="duplicate key"):
        FrozenAgentDemand.from_json_bytes(
            b'{"schema_version":2,"schema_version":2}'
        )


def test_frozen_demand_rejects_incomplete_join_and_bad_creation_edge() -> None:
    demand = _demand()
    root, child, resumed = _keys()
    bad_join = replace(demand.joins[0], waiters=(root, resumed))
    with pytest.raises(ValueError, match="each JOIN waiter"):
        replace(demand, joins=(bad_join,))

    resumed_invocation = next(
        item for item in demand.invocations if item.key == resumed
    )
    bad_resumed = replace(resumed_invocation, relation=FrozenInvocationRelation.CALL)
    with pytest.raises(ValueError, match="matching creation edge"):
        replace(
            demand,
            invocations=tuple(
                bad_resumed if item.key == resumed else item
                for item in demand.invocations
            ),
        )

    child_invocation = next(item for item in demand.invocations if item.key == child)
    bad_terminal = replace(
        child_invocation.calls[-1],
        boundary=FrozenActionBoundary(
            FrozenActionBoundaryKind.RETURN,
            target_invocations=(resumed,),
        ),
    )
    with pytest.raises(ValueError, match="RETURN exactly"):
        replace(
            demand,
            invocations=tuple(
                replace(
                    item,
                    calls=item.calls[:-1] + (bad_terminal,),
                )
                if item.key == child
                else item
                for item in demand.invocations
            ),
        )


def test_frozen_demand_rejects_invalid_context_owner_and_root_terminal() -> None:
    demand = _demand()
    root, _, resumed = _keys()
    resumed_invocation = next(
        item for item in demand.invocations if item.key == resumed
    )
    with pytest.raises(ValueError, match="FRESH/FORK invocation"):
        replace(
            demand,
            invocations=tuple(
                replace(resumed_invocation, context_mode=FrozenContextMode.FRESH)
                if item.key == resumed
                else item
                for item in demand.invocations
            ),
        )

    root_invocation = next(item for item in demand.invocations if item.key == root)
    bad_root_call = replace(
        root_invocation.calls[-1],
        boundary=FrozenActionBoundary(FrozenActionBoundaryKind.RETURN),
    )
    with pytest.raises(ValueError, match="workflow root"):
        replace(
            demand,
            invocations=tuple(
                replace(item, calls=item.calls[:-1] + (bad_root_call,))
                if item.key == root
                else item
                for item in demand.invocations
            ),
        )


@pytest.mark.parametrize(
    ("arm", "view"),
    (
        (PerfectFutureOracleArm.O0_CURRENT, "agent"),
        (PerfectFutureOracleArm.O0_CURRENT, "kv"),
        (PerfectFutureOracleArm.O1_AGENT, "kv"),
        (PerfectFutureOracleArm.O2_KV, "agent"),
    ),
)
def test_wrong_arm_future_query_fails_immediately(arm, view) -> None:
    demand = _demand()
    root, _, _ = _keys()
    provider = _provider(demand, arm)
    cursor = _cursor(demand)

    with pytest.raises(OracleFutureAccessViolation):
        if view == "agent":
            provider.agent_future.query(
                planner_epoch=7,
                logical_key=root,
                cursor=cursor,
                field=AgentFutureField.REMAINING_DEMAND,
                reason="rank execution package",
            )
        else:
            provider.kv_future.query(
                planner_epoch=7,
                logical_key=root,
                cursor=cursor,
                field=KVFutureField.FUTURE_REUSE,
                reason="rank residency candidate",
            )
    assert len(provider.access_ledger) == 1
    assert not provider.access_ledger[0].allowed


def test_kv_future_aggregates_all_invocations_of_semantic_owner() -> None:
    demand = _demand()
    _, child, resumed = _keys()
    completed_tool = FrozenToolKey(child, 0)
    cursor = _cursor(
        demand,
        progress_updates={child: _completed_progress(demand, child)},
        completed_invocations=(child,),
        completed_tools=(completed_tool,),
    )
    provider = _provider(demand, PerfectFutureOracleArm.O2_KV)

    proof = provider.kv_future.query(
        planner_epoch=1,
        logical_key=child,
        cursor=cursor,
        field=KVFutureField.NO_FUTURE_USE_PROOF,
        reason="decide whether context can be dropped",
    )
    uses = provider.kv_future.query(
        planner_epoch=1,
        logical_key=child,
        cursor=cursor,
        field=KVFutureField.FUTURE_GROWTH,
        reason="size shared-context future growth",
    )

    assert not proof.proven
    assert proof.semantic_owner == child
    assert {item.invocation for item in uses} == {resumed}


def test_cursor_rejects_nonexistent_call_and_backward_progress() -> None:
    demand = _demand()
    root, _, _ = _keys()
    provider = _provider(demand, PerfectFutureOracleArm.O1_AGENT)
    bad_progress = OracleInvocationProgress(
        logical_key=root,
        current_call_ordinal=999,
        call_phase=OracleCallPhase.NOT_STARTED,
        prefilled_prompt_tokens=0,
        generated_tokens=0,
    )
    with pytest.raises(OracleReplayCursorError, match="absent"):
        provider.agent_future.query(
            planner_epoch=1,
            logical_key=root,
            cursor=_cursor(demand, progress_updates={root: bad_progress}),
            field=AgentFutureField.REMAINING_DEMAND,
            reason="reject invalid cursor",
        )

    first_call = next(
        item for item in demand.invocations if item.key == root
    ).calls[0]
    forward = OracleInvocationProgress(
        logical_key=root,
        current_call_ordinal=0,
        call_phase=OracleCallPhase.DECODE,
        prefilled_prompt_tokens=first_call.incremental_prompt_tokens,
        generated_tokens=5,
    )
    provider.agent_future.query(
        planner_epoch=2,
        logical_key=root,
        cursor=_cursor(demand, revision=1, progress_updates={root: forward}),
        field=AgentFutureField.REMAINING_DEMAND,
        reason="record forward progress",
    )
    backward = replace(forward, generated_tokens=4)
    with pytest.raises(OracleReplayCursorError, match="token progress"):
        provider.agent_future.query(
            planner_epoch=3,
            logical_key=root,
            cursor=_cursor(demand, revision=2, progress_updates={root: backward}),
            field=AgentFutureField.REMAINING_DEMAND,
            reason="reject backward progress",
        )


def test_cursor_produces_residual_tool_and_recursive_child_demand() -> None:
    demand = _demand()
    root, child, _ = _keys()
    child_call = next(
        item for item in demand.invocations if item.key == child
    ).calls[0]
    active_tool = OracleInvocationProgress(
        logical_key=child,
        current_call_ordinal=0,
        call_phase=OracleCallPhase.BOUNDARY_WAIT,
        prefilled_prompt_tokens=child_call.incremental_prompt_tokens,
        generated_tokens=child_call.output_tokens,
        active_tool_ordinal=0,
        tool_elapsed_ms=25.5,
    )
    cursor = _cursor(demand, progress_updates={child: active_tool})
    provider = _provider(demand, PerfectFutureOracleArm.O3_JOINT)

    remaining = provider.agent_future.query(
        planner_epoch=1,
        logical_key=child,
        cursor=cursor,
        field=AgentFutureField.REMAINING_DEMAND,
        reason="estimate residual child demand",
    )
    children = provider.agent_future.query(
        planner_epoch=1,
        logical_key=root,
        cursor=cursor,
        field=AgentFutureField.CHILD_COMPLETION_DEMAND,
        reason="compose recursive child completion",
    )
    intervals = provider.kv_future.query(
        planner_epoch=1,
        logical_key=child,
        cursor=cursor,
        field=KVFutureField.PARKED_INTERVAL,
        reason="measure context parked slack",
    )

    assert remaining.tool_service_ms == pytest.approx(100.0)
    assert children[0].remaining.child_count == 1
    tool_interval = next(item for item in intervals if item.kind == "tool")
    assert tool_interval.tool_key == FrozenToolKey(child, 0)
    assert tool_interval.residual_external_duration_ms == pytest.approx(100.0)


def test_content_digest_binds_truth_even_when_truth_id_matches() -> None:
    demand = _demand()
    _, child, _ = _keys()
    child_invocation = next(item for item in demand.invocations if item.key == child)
    changed = replace(
        demand,
        invocations=tuple(
            replace(
                child_invocation,
                tools=(replace(child_invocation.tools[0], service_duration_ms=200.0),),
            )
            if item.key == child
            else item
            for item in demand.invocations
        ),
    )
    assert changed.truth_id == demand.truth_id
    assert changed.truth_digest != demand.truth_digest

    with pytest.raises(ValueError, match="digest"):
        OracleTruthProvider(
            changed,
            arm=PerfectFutureOracleArm.O3_JOINT,
            expected_truth_id=demand.truth_id,
            expected_truth_digest=demand.truth_digest,
        )


def test_access_audit_is_bounded_but_preserves_aggregate_counts() -> None:
    demand = _demand()
    root, _, _ = _keys()
    cursor = _cursor(demand)
    provider = _provider(
        demand,
        PerfectFutureOracleArm.O3_JOINT,
        capacity=2,
    )
    for field in (
        AgentFutureField.NEXT_ACTION_BOUNDARY,
        AgentFutureField.REMAINING_DEMAND,
        AgentFutureField.SERVICE_UNLOCK,
    ):
        provider.agent_future.query(
            planner_epoch=1,
            logical_key=root,
            cursor=cursor,
            field=field,
            reason="bounded audit test",
        )

    assert len(provider.access_ledger) == 2
    assert provider.access_summary["total_queries"] == 3
    assert provider.access_summary["evicted_recent_records"] == 1


def test_old_oracle_is_machine_readable_legacy_only() -> None:
    assert ORACLE_IMPLEMENTATION_STATUS == "legacy_offline_diagnostic_only"
