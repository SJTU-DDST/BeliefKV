from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest

from beliefkv.core.config import BeliefKVConfig
from beliefkv.experiments.oracle_gpu_replay import (
    _TokenPathMapper,
    OracleGPUReplay,
)
from beliefkv.oracle.contracts import (
    FrozenActionBoundary,
    FrozenActionBoundaryKind,
    FrozenAgentDemand,
    FrozenContextMode,
    FrozenDemandProvenance,
    FrozenInvocationDemand,
    FrozenInvocationRelation,
    FrozenLLMCallDemand,
    LogicalInvocationKey,
    OracleCallPhase,
    OracleInvocationProgress,
    OracleReplayCursor,
    PerfectFutureOracleArm,
)
from beliefkv.oracle.physical_sidecar import (
    FrozenPhysicalCall,
    FrozenPhysicalSidecar,
)
from beliefkv.oracle.truth_provider import OracleTruthProvider
from beliefkv.policy.perfect_future_joint import (
    OracleReadyRequest,
    PerfectFutureJointPlanner,
)
from beliefkv.policy.reference.base import ResidencyAction
from beliefkv.runtime.sglang_v052rc1 import (
    EmbeddedSGLangRuntime,
    _OracleJointDirective,
)


class _MemorySink:
    def __init__(self) -> None:
        self.events = []

    def emit_batch(self, events) -> None:
        self.events.extend(events)


class _NullAudit:
    def emit(self, *_args, **_kwargs) -> None:
        return None


def _root(workload: str, *, output_tokens: int) -> FrozenInvocationDemand:
    key = LogicalInvocationKey(workload, ("supervisor",), 0, 0, 0)
    return FrozenInvocationDemand(
        key=key,
        agent_definition_id="supervisor",
        relation=FrozenInvocationRelation.ROOT,
        context_mode=FrozenContextMode.FRESH,
        parent=None,
        semantic_owner=key,
        calls=(
            FrozenLLMCallDemand(
                call_ordinal=0,
                prompt_tokens=32,
                incremental_prompt_tokens=32,
                output_tokens=output_tokens,
                parent_reentry_prompt_growth_tokens=0,
                boundary=FrozenActionBoundary(FrozenActionBoundaryKind.FINAL),
            ),
        ),
    )


def _demand() -> FrozenAgentDemand:
    return FrozenAgentDemand(
        provenance=FrozenDemandProvenance(
            truth_id="gpu-replay-fixture",
            source_trace_id="trace",
            workload_manifest_id="manifest",
            model_revision="model",
            tokenizer_revision="tokenizer",
            runtime_revision="runtime",
            harness_revision="harness",
            exporter_revision="exporter",
        ),
        invocations=(_root("long", output_tokens=64), _root("short", output_tokens=8)),
    )


def _cursor(demand: FrozenAgentDemand) -> OracleReplayCursor:
    return OracleReplayCursor(
        cursor_revision=0,
        invocation_progress=tuple(
            OracleInvocationProgress(
                logical_key=item.key,
                current_call_ordinal=0,
                call_phase=OracleCallPhase.PREFILL,
                prefilled_prompt_tokens=0,
                generated_tokens=0,
            )
            for item in demand.invocations
        ),
    )


def test_token_mapper_preserves_actual_context_continuation() -> None:
    mapper = _TokenPathMapper(vocab_size=32)
    owner = LogicalInvocationKey("task", ("root",), 0, 0, 0)
    first_prompt = mapper.prompt(owner=owner, frozen_prompt=(10, 11, 12))
    mapper.commit(
        owner=owner,
        physical=FrozenPhysicalCall(
            invocation=owner,
            call_ordinal=0,
            trace_request_ordinal=0,
            runtime_context_epoch=0,
            observed_cache_hit_tokens=0,
            observed_unique_growth_bytes=4,
            prompt_token_symbols=(10, 11, 12),
            cache_commit_token_symbols=(10, 11, 12, 13),
        ),
        actual_prompt=first_prompt,
        output_token_ids=[29, 30],
    )

    second_prompt = mapper.prompt(
        owner=owner, frozen_prompt=(10, 11, 12, 13, 14)
    )

    assert second_prompt[:4] == first_prompt + [29]
    assert len(second_prompt) == 5


def test_perfect_future_planner_orders_short_terminal_unlock_first() -> None:
    demand = _demand()
    provider = OracleTruthProvider(
        demand,
        arm=PerfectFutureOracleArm.O3_JOINT,
        expected_truth_id=demand.truth_id,
        expected_truth_digest=demand.truth_digest,
    )
    planner = PerfectFutureJointPlanner(provider, replay_id="replay")
    ready = {
        item.key.workload_instance: OracleReadyRequest(
            request_id=item.key.workload_instance,
            logical_key=item.key,
            call_ordinal=0,
            prompt_tokens=32,
            output_tokens=item.calls[0].output_tokens,
        )
        for item in demand.invocations
    }

    directive = planner.compile(cursor=_cursor(demand), ready=ready)

    assert directive.ordered_request_ids == ("short", "long")
    assert provider.access_summary["total_queries"] == 4


def test_o3_noop_candidate_uses_frozen_o0_order_without_agent_queries() -> None:
    demand = _demand()
    provider = OracleTruthProvider(
        demand,
        arm=PerfectFutureOracleArm.O3_JOINT,
        expected_truth_id=demand.truth_id,
        expected_truth_digest=demand.truth_digest,
    )
    by_workload = {
        item.key.workload_instance: item for item in demand.invocations
    }
    planner = PerfectFutureJointPlanner(
        provider,
        replay_id="replay",
        frozen_execution_order={
            (by_workload["long"].key, 0): 0,
            (by_workload["short"].key, 0): 1,
        },
    )
    ready = {
        item.key.workload_instance: OracleReadyRequest(
            request_id=item.key.workload_instance,
            logical_key=item.key,
            call_ordinal=0,
            prompt_tokens=32,
            output_tokens=item.calls[0].output_tokens,
        )
        for item in demand.invocations
    }

    directive = planner.compile(cursor=_cursor(demand), ready=ready)

    assert directive.ordered_request_ids == ("long", "short")
    assert provider.access_summary["total_queries"] == 0


def test_oracle_directive_parser_is_strict() -> None:
    raw = {
        "schema_version": 1,
        "directive_id": "directive-1",
        "sequence": 1,
        "replay_id": "replay",
        "truth_id": "truth",
        "truth_digest": "a" * 64,
        "ordered_request_ids": ["r1", "r2"],
        "semantic_residency": [],
    }
    assert _OracleJointDirective.from_mapping(raw).ordered_request_ids == (
        "r1",
        "r2",
    )
    with pytest.raises(ValueError, match="unique"):
        _OracleJointDirective.from_mapping(
            {**raw, "ordered_request_ids": ["r1", "r1"]}
        )
    with pytest.raises(TypeError, match="must be strings"):
        _OracleJointDirective.from_mapping(
            {**raw, "ordered_request_ids": [1]}
        )


def test_oracle_config_requires_frozen_identity_and_joint_o3() -> None:
    base = BeliefKVConfig()
    with pytest.raises(ValueError, match="cannot load truth state"):
        replace(base, perfect_future_truth_id="truth")
    fields = {
        "perfect_future_oracle_mode": "o3_joint",
        "perfect_future_truth_path": "/tmp/truth.json",
        "perfect_future_truth_id": "truth",
        "perfect_future_truth_digest": "a" * 64,
        "perfect_future_replay_id": "replay",
    }
    with pytest.raises(ValueError, match="online JointPlan data plane"):
        replace(base, **fields)
    assert replace(base, joint_policy_enabled=True, **fields)


def test_oracle_seed_recompiles_when_native_visibility_changes() -> None:
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.config = SimpleNamespace(perfect_future_oracle_mode="o3_joint")
    runtime._oracle_joint_directive = _OracleJointDirective.from_mapping(
        {
            "schema_version": 1,
            "directive_id": "directive-1",
            "sequence": 1,
            "replay_id": "replay",
            "truth_id": "truth",
            "truth_digest": "a" * 64,
            "ordered_request_ids": ["request-1"],
            "semantic_residency": [],
        }
    )
    runtime._oracle_joint_decision = None
    runtime._oracle_last_compile_ms = None
    runtime._oracle_last_visible_request_ids = None
    runtime._oracle_consumed_directive_ids = set()
    runtime._online_joint_epoch_sequence = 0
    runtime._online_joint_counts = Counter()
    runtime.audit = _NullAudit()
    visible = []
    runtime._policy_runtime_runnable = lambda _now: tuple(visible)
    runtime._physical_commit_semantic_residency = (
        lambda _plan, decision, now_ms: decision
    )

    empty = runtime._oracle_joint_admission_decision(now_ms=1.0)
    visible.append(SimpleNamespace(request_id="request-1"))
    admitted = runtime._oracle_joint_admission_decision(now_ms=2.0)

    assert empty.reason == "no_action"
    assert admitted.reason == "applicable"
    assert admitted.view.immediate_request_ids == ("request-1",)


def test_output_token_id_parser_accepts_native_logprob_records() -> None:
    assert OracleGPUReplay._output_token_ids(
        {
            "meta_info": {
                "output_token_logprobs": [
                    [-0.1, 7, None],
                    {"token_id": 9, "logprob": -0.2},
                ]
            }
        }
    ) == [7, 9]


def test_single_workflow_replay_reaches_natural_terminal(tmp_path) -> None:
    invocation = _root("single", output_tokens=2)
    demand = FrozenAgentDemand(
        provenance=FrozenDemandProvenance(
            truth_id="single-replay",
            source_trace_id="trace",
            workload_manifest_id="manifest",
            model_revision="model",
            tokenizer_revision="tokenizer",
            runtime_revision="runtime",
            harness_revision="harness",
            exporter_revision="exporter",
        ),
        invocations=(invocation,),
    )
    sidecar = FrozenPhysicalSidecar(
        truth_id=demand.truth_id,
        truth_digest=demand.truth_digest,
        source_trace_id="trace",
        kv_bytes_per_token=16,
        initial_radix_state="empty_server_boot",
        calls=(
            FrozenPhysicalCall(
                invocation=invocation.key,
                call_ordinal=0,
                trace_request_ordinal=0,
                runtime_context_epoch=0,
                observed_cache_hit_tokens=0,
                observed_unique_growth_bytes=528,
                prompt_token_symbols=tuple(range(32)),
                cache_commit_token_symbols=tuple(range(33)),
            ),
        ),
    )
    sink = _MemorySink()
    replay = OracleGPUReplay(
        truth=demand,
        sidecar=sidecar,
        arm=PerfectFutureOracleArm.O0_CURRENT,
        replay_id="single-o0",
        base_url="http://test",
        event_sink=sink,
        output_dir=tmp_path,
        vocab_size=64,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert len(payload["input_ids"]) == 32
        return httpx.Response(
            200,
            json={
                "meta_info": {
                    "output_token_logprobs": [
                        [-0.1, 40, None],
                        [-0.2, 41, None],
                    ]
                }
            },
        )

    async def execute() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            await replay._run_root(invocation, client)

    try:
        asyncio.run(execute())
    finally:
        replay._request_output.close()
        replay._access_output.close()
        replay.emitter.close()

    assert replay._completed_workflows == 1
    assert replay._completed_requests == 1
    assert invocation.key in replay.completed_invocations
    assert [item.kind.value for item in sink.events] == [
        "workflow_start",
        "invocation_create",
        "return",
        "workflow_end",
    ]


def test_parked_action_uses_exact_next_prompt_prefix_morphology() -> None:
    invocation = _root("shape", output_tokens=2)
    current = FrozenPhysicalCall(
        invocation=invocation.key,
        call_ordinal=0,
        trace_request_ordinal=0,
        runtime_context_epoch=0,
        observed_cache_hit_tokens=0,
        observed_unique_growth_bytes=64,
        prompt_token_symbols=(1, 2, 3),
        cache_commit_token_symbols=(1, 2, 3, 4),
    )
    next_use = FrozenPhysicalCall(
        invocation=invocation.key,
        call_ordinal=1,
        trace_request_ordinal=1,
        runtime_context_epoch=1,
        observed_cache_hit_tokens=3,
        observed_unique_growth_bytes=32,
        prompt_token_symbols=(1, 2, 3, 9),
        cache_commit_token_symbols=(1, 2, 3, 9, 10),
    )
    replay = OracleGPUReplay.__new__(OracleGPUReplay)
    replay.progress = {invocation.key: SimpleNamespace(call_ordinal=0)}
    replay.physical_by_call = {(invocation.key, 0): current}
    replay.physical_by_owner = {invocation.key: (current, next_use)}
    replay.sidecar = SimpleNamespace(kv_bytes_per_token=16)

    action, target_bytes, _ = replay._parked_residency_action(invocation)
    replay.physical_by_owner = {
        invocation.key: (
            current,
            replace(
                next_use,
                prompt_token_symbols=(1, 8, 9, 10),
                cache_commit_token_symbols=(1, 8, 9, 10, 11),
            ),
        )
    }
    low_reuse_action, _, _ = replay._parked_residency_action(invocation)

    assert action == ResidencyAction.COMMIT_CPU
    assert target_bytes == 64
    assert low_reuse_action is None
