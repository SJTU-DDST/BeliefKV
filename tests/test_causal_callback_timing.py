import asyncio
from dataclasses import replace
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import (
    CausalReplayError, NativeReplayCompletion, compile_causal_replay,
    evaluate_causal_plan, event_node_id, execute_causal_replay,
)
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from tests.test_causal_replay import sequential_trace


def adjusted_trace(start_shift=0., end_shift=3.5):
    trace = sequential_trace()
    for name, shift in (("tool-start-wait", start_shift), ("tool-end-wait", end_shift)):
        original = trace.named[name]
        attributes = dict(original.attributes)
        if shift:
            attributes.update(
                callback_observed_ts_ms=original.ts_ms - shift,
                callback_publication_adjustment_ms=shift,
            )
        if name.startswith("tool-end"):
            attributes["duration_ms"] = 10. - end_shift + start_shift
        updated = replace(original, attributes=attributes)
        trace.events[trace.events.index(original)] = updated
        trace.named[name] = updated
    return trace


@pytest.mark.parametrize("start_shift,end_shift", ((0., 3.5), (.5, 0.), (.5, 3.5)))
def test_verified_callback_timestamps_preserve_tool_time_and_published_delivery_delay(
    start_shift, end_shift,
):
    baseline = sequential_trace()
    expected = compile_causal_replay(baseline.ordered(), baseline.native)
    trace = adjusted_trace(start_shift, end_shift)
    plan = compile_causal_replay(trace.ordered(), trace.native)
    end = trace.named["tool-end-wait"]
    node = next(node for node in plan.nodes if node.node_id == event_node_id(end.event_id))
    assert node.event is end
    assert node.event.attributes["duration_ms"] == 10. - end_shift + start_shift
    assert node.delay_ms == 10.
    assert plan.summary() == expected.summary()
    assert evaluate_causal_plan(plan, {"r0": 0., "r1": 0.}) == evaluate_causal_plan(
        expected, {"r0": 0., "r1": 0.},
    )


@pytest.mark.parametrize("change", (
    {"callback_observed_ts_ms": None},
    {"callback_publication_adjustment_ms": None},
    {"callback_observed_ts_ms": float("nan")},
    {"callback_observed_ts_ms": -1.},
    {"callback_observed_ts_ms": 119.},
    {"callback_publication_adjustment_ms": -3.5},
    {"callback_publication_adjustment_ms": 8.},
    {"callback_publication_adjustment_ms": True},
))
def test_inconsistent_callback_clock_evidence_cannot_bypass_tool_validation(change):
    trace = adjusted_trace()
    original = trace.named["tool-end-wait"]
    updated = replace(original, attributes={**original.attributes, **change})
    trace.events[trace.events.index(original)] = updated
    with pytest.raises(CausalReplayError, match="callback"):
        compile_causal_replay(trace.ordered(), trace.native)


def test_observed_callback_metadata_does_not_waive_an_incorrect_declared_tool_duration():
    trace = adjusted_trace()
    original = trace.named["tool-end-wait"]
    updated = replace(original, attributes={**original.attributes, "duration_ms": 1000.})
    trace.events[trace.events.index(original)] = updated
    with pytest.raises(CausalReplayError, match="tool duration"):
        compile_causal_replay(trace.ordered(), trace.native)


def test_replayed_events_get_fresh_timing_without_original_callback_clock_metadata():
    trace = adjusted_trace(.5, 3.5)
    plan = compile_causal_replay(trace.ordered(), trace.native)

    async def scenario():
        events = []

        async def backend(request, _submit):
            return NativeReplayCompletion(
                request, time.monotonic() * 1000., local_monotonic_clock_domain(),
                *plan.request_usage[request],
            )

        async def emit(event):
            events.append(event)

        result = await execute_causal_replay(plan, backend, emit)
        assert result["causal_execution_completed"] and result["completed_requests"] == 2
        start = next(event for event in events if event.kind is Kind.TOOL_START)
        end = next(event for event in events if event.kind is Kind.TOOL_END)
        assert end.attributes["duration_ms"] == end.ts_ms - start.ts_ms
        assert end.attributes["duration_ms"] >= 10.
        for event in events:
            assert "callback_observed_ts_ms" not in event.attributes
            assert "callback_publication_adjustment_ms" not in event.attributes

    asyncio.run(scenario())
