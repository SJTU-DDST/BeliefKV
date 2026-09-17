from __future__ import annotations

import json
import math

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.predictor.action_frontier import (
    ActionTimingCurve,
    OperationalReleaseModel,
    PooledConditionalClassifier,
    PooledConditionalDemandModel,
)
from beliefkv.runtime.action_frontier import (
    ActionFrontierObserver,
    JsonActionParser,
    ParserStatus,
    StructuredActionKind,
    characterize_action_frontier_coverage,
)


def _event(
    sequence: int,
    kind: RuntimeEventKind,
    *,
    invocation_id: str = "coder",
    target_invocation_id: str | None = None,
    attributes: dict[str, object] | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"event-{sequence}",
        ts_ms=float(sequence * 10),
        kind=kind,
        workflow_id="workflow",
        invocation_id=invocation_id,
        target_invocation_id=target_invocation_id,
        context_id="context-coder",
        attributes=attributes or {},
    )


def test_incremental_json_parser_reports_first_real_boundary_token() -> None:
    parser = JsonActionParser()

    partial = parser.feed('{"action":"tool",', generated_tokens=4)
    valid = parser.feed('"name":"search","arguments":{}}', generated_tokens=11)
    later = parser.feed("   ", generated_tokens=12)

    assert partial.status == ParserStatus.INCOMPLETE
    assert partial.boundary_token_index is None
    assert valid.status == ParserStatus.VALID
    assert valid.action_kind == StructuredActionKind.FUNCTION_CALL
    assert valid.boundary_token_index == 11
    assert later.boundary_token_index == 11


def test_free_text_is_unknown_and_never_gets_a_fabricated_boundary() -> None:
    update = JsonActionParser().feed("I think the answer is...", generated_tokens=6)

    assert update.status == ParserStatus.UNKNOWN
    assert update.action_kind == StructuredActionKind.UNKNOWN
    assert update.boundary_token_index is None


def test_runtime_terminal_valid_action_keeps_boundary_unknown() -> None:
    observer = ActionFrontierObserver()
    observer.observe_runtime_event(
        _event(
            1,
            RuntimeEventKind.LLM_SUBMIT,
            attributes={"request_id": "request", "output_tokens": 0},
        ),
        runnable_frontier_before=("coder", "reviewer"),
        context_gpu_bytes=1024,
    )
    state = observer.observe_runtime_event(
        _event(
            2,
            RuntimeEventKind.LLM_RESULT,
            attributes={
                "request_id": "request",
                "output_tokens": 20,
                "parser_status": "valid",
                "structured_action_kinds": ["function_call"],
                "structured_action_names": ["search"],
                "action_boundary_token_index": None,
                "action_boundary_source": "runtime_structured_output",
            },
        )
    )

    assert state is not None
    assert state.parser_status == ParserStatus.VALID
    assert state.valid_action_ts_ms == 20.0
    assert state.boundary_token_index is None
    assert state.boundary_source == "runtime_structured_output"


def test_observer_records_tool_gap_frontier_delta_kv_transition_and_reentry() -> None:
    observer = ActionFrontierObserver()
    observer.observe_runtime_event(
        _event(
            1,
            RuntimeEventKind.LLM_SUBMIT,
            attributes={"request_id": "request"},
        ),
        runnable_frontier_before=("coder", "reviewer"),
        context_gpu_bytes=4096,
    )
    observer.observe_runtime_event(
        _event(
            2,
            RuntimeEventKind.LLM_RESULT,
            attributes={
                "output_tokens": 8,
                "parser_status": "valid",
                "structured_action_kinds": ["function_call"],
                "structured_action_names": ["run_tests"],
            },
        )
    )
    action = observer.observe_runtime_event(
        _event(3, RuntimeEventKind.TOOL_START),
        runnable_frontier_after=("reviewer",),
        context_gpu_bytes=3072,
    )
    reentered = observer.observe_runtime_event(
        _event(8, RuntimeEventKind.TOOL_END),
        runnable_frontier_after=("coder", "reviewer"),
        context_gpu_bytes=3072,
    )

    assert action is not None
    assert action.tool_start_gap_ms == 10.0
    assert action.frontier_added == ()
    assert action.frontier_removed == ("coder",)
    assert action.active_kv_bytes_before == 4096
    assert action.waiting_kv_bytes_after == 3072
    assert reentered is not None
    assert reentered.reentry_delay_ms == 50.0
    assert {item.kind for item in observer.drain_audit_events()} == {
        "action_frontier_updated",
        "valid_action_unlocked",
    }


def test_p6_coverage_separates_exact_and_runtime_only_boundaries() -> None:
    observer = ActionFrontierObserver()
    observer.begin(
        request_id="exact",
        workflow_id="workflow",
        invocation_id="exact-invocation",
        context_id="exact-context",
        ts_ms=0.0,
    )
    observer.observe_parser_update(
        "exact",
        JsonActionParser().feed(
            '{"action":"tool","name":"search"}', generated_tokens=8
        ),
        ts_ms=1.0,
    )
    observer.begin(
        request_id="runtime-only",
        workflow_id="workflow",
        invocation_id="runtime-invocation",
        context_id="runtime-context",
        ts_ms=2.0,
    )
    observer.observe_runtime_event(
        RuntimeEvent(
            event_id="runtime-result",
            ts_ms=3.0,
            kind=RuntimeEventKind.LLM_RESULT,
            workflow_id="workflow",
            invocation_id="runtime-invocation",
            context_id="runtime-context",
            attributes={
                "request_id": "runtime-only",
                "output_tokens": 12,
                "parser_status": "valid",
                "structured_action_kinds": ["function_call"],
                "structured_action_names": ["read_file"],
            },
        )
    )

    coverage = characterize_action_frontier_coverage(observer.snapshots())

    assert observer.revision == 4
    assert coverage.action_call_count == 2
    assert coverage.exact_boundary_call_count == 1
    assert coverage.runtime_only_boundary_call_count == 1
    assert coverage.exact_boundary_call_coverage == 0.5


def test_internal_summary_is_not_an_agent_action() -> None:
    observer = ActionFrontierObserver()

    assert observer.observe_runtime_event(
        _event(
            1,
            RuntimeEventKind.LLM_SUBMIT,
            attributes={"request_id": "summary", "runtime_internal": True},
        )
    ) is None
    assert observer.observe_runtime_event(
        _event(
            2,
            RuntimeEventKind.LLM_RESULT,
            attributes={
                "runtime_internal": True,
                "parser_status": "valid",
                "structured_action_kinds": ["final_answer"],
                "output_tokens": 16,
            },
        )
    ) is None

    assert observer.snapshots() == ()
    assert observer.coverage().call_count == 0


def test_join_satisfied_is_attributed_to_the_waiting_parent() -> None:
    observer = ActionFrontierObserver()
    observer.observe_runtime_event(
        _event(
            1,
            RuntimeEventKind.LLM_SUBMIT,
            invocation_id="parent",
            attributes={"request_id": "spawn-call"},
        )
    )
    observer.observe_runtime_event(
        _event(
            2,
            RuntimeEventKind.LLM_RESULT,
            invocation_id="parent",
            attributes={
                "parser_status": "valid",
                "structured_action_kinds": ["spawn"],
                "structured_action_names": ["task"],
                "output_tokens": 8,
            },
        )
    )
    observer.observe_runtime_event(
        RuntimeEvent(
            event_id="join-wait",
            ts_ms=30.0,
            kind=RuntimeEventKind.JOIN_WAIT,
            workflow_id="workflow",
            invocation_id="parent",
            join_id="join",
        )
    )
    state = observer.observe_runtime_event(
        RuntimeEvent(
            event_id="join-satisfied",
            ts_ms=80.0,
            kind=RuntimeEventKind.JOIN_SATISFIED,
            workflow_id="workflow",
            join_id="join",
        )
    )

    assert state is not None
    assert state.reentry_ts_ms == 80.0
    coverage = observer.coverage()
    assert coverage.reentry_eligible_call_count == 1
    assert coverage.reentry_observed_count == 1
    assert coverage.reentry_censored_count == 0
    assert coverage.reentry_cause_coverage == 1.0


def test_suppressed_tool_call_is_an_explicit_censored_reentry() -> None:
    observer = ActionFrontierObserver()
    observer.observe_runtime_event(
        _event(
            1,
            RuntimeEventKind.LLM_SUBMIT,
            attributes={"request_id": "tool-call"},
        )
    )
    observer.observe_runtime_event(
        _event(
            2,
            RuntimeEventKind.LLM_RESULT,
            attributes={
                "parser_status": "valid",
                "structured_action_kinds": ["function_call"],
                "structured_action_names": ["execute"],
                "output_tokens": 8,
            },
        )
    )
    state = observer.observe_runtime_event(
        _event(
            3,
            RuntimeEventKind.CALL_CENSORED,
            invocation_id="fallback-root-identity",
            attributes={
                "censor_reason": "duplicate_suppressed",
                "invocation_identity_fallback": True,
                "tool_name": "execute",
            },
        )
    )

    assert state is not None
    assert state.reentry_ts_ms == 30.0
    assert state.reentry_status == "censored"
    assert state.reentry_censor_reason == "duplicate_suppressed"
    coverage = observer.coverage()
    assert coverage.reentry_eligible_call_count == 1
    assert coverage.reentry_observed_count == 0
    assert coverage.reentry_censored_count == 1
    assert coverage.reentry_cause_coverage == 1.0


def test_action_timing_curve_uses_log_interpolation_and_rejects_non_monotonic() -> None:
    curve = ActionTimingCurve(
        tau_ms=(10.0, 100.0),
        release_within_probability=(0.2, 0.8),
        support_level="pooled",
        training_support=12.0,
    )
    log_midpoint_tau = math.sqrt((1.0 + 10.0) * (1.0 + 100.0)) - 1.0

    assert curve.release_within(log_midpoint_tau) == pytest.approx(0.5)
    assert curve.release_within(0.0) == 0.0
    assert curve.release_within(1_000.0) == pytest.approx(0.8)

    with pytest.raises(ValueError, match="non-decreasing"):
        ActionTimingCurve(
            tau_ms=(10.0, 100.0),
            release_within_probability=(0.8, 0.2),
            support_level="pooled",
            training_support=1.0,
        )


def test_pooled_conditional_demand_model_fit_predict_and_round_trip() -> None:
    samples = []
    for context_tokens, target in (
        (128, 16),
        (256, 24),
        (512, 40),
        (1024, 72),
        (2048, 136),
        (4096, 264),
    ):
        samples.append(
            (
                {
                    "agent_definition_id": "coder",
                    "state": "running_llm",
                    "tool_family": "none",
                    "backend_class": "local",
                    "command_class": "none",
                    "boundary_history": ("tool",),
                    "current_sequence_tokens": context_tokens,
                    "generated_tokens": context_tokens // 32,
                },
                float(target),
                1.0,
            )
        )

    model = PooledConditionalDemandModel(regularization=1e-2)
    metrics = model.fit(samples)
    low = samples[1][0]
    high = samples[-1][0]
    low_values, low_mass, support, level = model.predict(low)
    high_values, high_mass, _, _ = model.predict(high)

    assert metrics["sample_count"] == len(samples)
    assert metrics["episode_weight"] == pytest.approx(float(len(samples)))
    assert model.fitted
    assert len(low_values) == len(low_mass) == 20
    assert sum(low_mass) == pytest.approx(1.0)
    assert support == pytest.approx(float(len(samples)))
    assert level == "pooled"
    assert high_values[len(high_values) // 2] > low_values[len(low_values) // 2]

    restored = PooledConditionalDemandModel.from_dict(
        json.loads(json.dumps(model.to_dict(), sort_keys=True))
    )
    restored_prediction = restored.predict(high)

    assert restored.to_dict() == model.to_dict()
    assert restored_prediction[0] == pytest.approx(high_values)
    assert restored_prediction[1] == pytest.approx(high_mass)
    assert restored_prediction[2:] == model.predict(high)[2:]


def test_operational_release_model_curve_is_monotonic_and_round_trips() -> None:
    samples = []
    for elapsed_ms in (0.0, 200.0, 400.0):
        features = {
            "agent_definition_id": "coder",
            "tool_family": "shell",
            "backend_class": "sandbox",
            "command_class": "pytest",
            "elapsed_wait_ms": elapsed_ms,
            "current_sequence_tokens": 4096,
            "active_tool_count": 1,
        }
        residual_ms = 800.0 - elapsed_ms
        for tau_ms in (50.0, 100.0, 200.0, 400.0, 800.0, 1600.0):
            samples.append((features, tau_ms, tau_ms >= residual_ms, 1.0))

    model = OperationalReleaseModel(regularization=1e-3)
    metrics = model.fit(samples)
    prediction_features = {
        "agent_definition_id": "coder",
        "tool_family": "shell",
        "backend_class": "sandbox",
        "command_class": "pytest",
        "elapsed_wait_ms": 200.0,
        "current_sequence_tokens": 4096,
        "active_tool_count": 1,
    }
    curve = model.curve(prediction_features)

    assert metrics["sample_count"] == len(samples)
    assert model.fitted
    assert curve is not None
    assert all(
        left <= right
        for left, right in zip(
            curve.release_within_probability,
            curve.release_within_probability[1:],
        )
    )
    assert curve.release_within(1600.0) > curve.release_within(100.0)

    restored = OperationalReleaseModel.from_dict(
        json.loads(json.dumps(model.to_dict(), sort_keys=True))
    )
    restored_curve = restored.curve(prediction_features)

    assert restored.to_dict() == model.to_dict()
    assert restored_curve is not None
    assert restored_curve.tau_ms == curve.tau_ms
    assert restored_curve.release_within_probability == pytest.approx(
        curve.release_within_probability
    )


def test_pooled_conditional_classifier_learns_rare_conditional_class() -> None:
    samples = []
    for index in range(40):
        samples.append(
            (
                {
                    "agent_definition_id": "coder",
                    "state": "running_llm",
                    "boundary_history": ("tool",),
                    "generated_tokens": index,
                },
                "tool",
                1.0,
            )
        )
    for index in range(8):
        samples.append(
            (
                {
                    "agent_definition_id": "planner",
                    "state": "running_llm",
                    "boundary_history": ("spawn",),
                    "generated_tokens": 100 + index,
                },
                "spawn",
                1.0,
            )
        )

    model = PooledConditionalClassifier(
        regularization=1e-3, balance_power=0.5
    )
    metrics = model.fit(samples)
    probabilities = model.predict(samples[-1][0])

    assert metrics["class_count"] == 2
    assert probabilities["spawn"] > probabilities["tool"]
    assert sum(probabilities.values()) == pytest.approx(1.0)

    restored = PooledConditionalClassifier.from_dict(
        json.loads(json.dumps(model.to_dict(), sort_keys=True))
    )
    assert restored.to_dict() == model.to_dict()
    assert restored.predict(samples[-1][0]) == pytest.approx(probabilities)


def test_censored_identity_fallback_rejects_ambiguous_latest_action() -> None:
    observer = ActionFrontierObserver()
    for invocation_id, request_id in (
        ("coder-a", "request-a"),
        ("coder-b", "request-b"),
    ):
        observer.observe_runtime_event(
            _event(
                1,
                RuntimeEventKind.LLM_SUBMIT,
                invocation_id=invocation_id,
                attributes={"request_id": request_id},
            )
        )
        observer.observe_runtime_event(
            _event(
                2,
                RuntimeEventKind.LLM_RESULT,
                invocation_id=invocation_id,
                attributes={
                    "parser_status": "valid",
                    "structured_action_kinds": ["function_call"],
                    "structured_action_names": ["execute"],
                    "output_tokens": 8,
                },
            )
        )

    state = observer.observe_runtime_event(
        _event(
            3,
            RuntimeEventKind.CALL_CENSORED,
            invocation_id="fallback-root-identity",
            attributes={
                "censor_reason": "duplicate_suppressed",
                "invocation_identity_fallback": True,
                "tool_name": "execute",
            },
        )
    )

    assert state is None
    assert observer.snapshot("request-a").reentry_ts_ms is None
    assert observer.snapshot("request-b").reentry_ts_ms is None


def test_join_timeout_is_attributed_to_the_waiting_parent_as_censored() -> None:
    observer = ActionFrontierObserver()
    observer.observe_runtime_event(
        _event(
            1,
            RuntimeEventKind.LLM_SUBMIT,
            invocation_id="parent",
            attributes={"request_id": "spawn-call"},
        )
    )
    observer.observe_runtime_event(
        _event(
            2,
            RuntimeEventKind.LLM_RESULT,
            invocation_id="parent",
            attributes={
                "parser_status": "valid",
                "structured_action_kinds": ["spawn"],
                "structured_action_names": ["task"],
                "output_tokens": 8,
            },
        )
    )
    observer.observe_runtime_event(
        RuntimeEvent(
            event_id="join-wait",
            ts_ms=30.0,
            kind=RuntimeEventKind.JOIN_WAIT,
            workflow_id="workflow",
            invocation_id="parent",
            join_id="join",
        )
    )
    state = observer.observe_runtime_event(
        RuntimeEvent(
            event_id="join-timeout",
            ts_ms=80.0,
            kind=RuntimeEventKind.JOIN_TIMEOUT,
            workflow_id="workflow",
            join_id="join",
        )
    )

    assert state is not None
    assert state.invocation_id == "parent"
    assert state.reentry_status == "censored"
    assert state.reentry_censor_reason == "join_timeout"
