"""Online forecast retries depend on the live call and both usable histories."""

from dataclasses import asdict, replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import CausalGraphError
from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from tests.test_online_workload_timing import tool_event
from tests.test_sglang_v0520_runtime import event, req
from tests.test_tool_predictive_transfers import tool_runtime


def completed(model, index, *, workflow="workflow", shape="pytest", duration=1000., **attrs):
    event = tool_event(
        RuntimeEventKind.TOOL_START, call=str(index), invocation=f"past-{index}",
        observed_command_shape=shape,
    )
    event.workflow_id = workflow
    model.observe(event)
    model.observe(NS(
        kind=RuntimeEventKind.TOOL_END, workflow_id=workflow,
        invocation_id=event.invocation_id, ts_ms=duration,
        attributes={"tool_run_id": str(index), "duration_ms": duration, **attrs},
    ))


def ready(model, *, workflow="workflow", invocation="agent", call="live"):
    event = tool_event(
        RuntimeEventKind.TOOL_START, invocation=invocation, call=call, start=10_000.,
    )
    event.workflow_id = workflow
    model.observe(event)


def test_unrelated_history_preserves_live_revision_and_prediction():
    model = OnlineWorkloadToolTiming()
    for index in range(8):
        completed(model, index, duration=1000. + index * 100)
    ready(model)
    before = model.estimate_revision("workflow", "agent")
    estimate = asdict(model.estimate("workflow", "agent", now_ms=10_100.))
    global_before = model.revision
    completed(model, 100, workflow="other", shape="git_status")
    assert model.revision == global_before + 1
    assert model.estimate_revision("workflow", "agent") == before
    assert asdict(model.estimate("workflow", "agent", now_ms=10_100.)) == estimate


@pytest.mark.parametrize("workflow,censored", (
    ("workflow", False), ("other", False), ("workflow", True), ("other", True),
))
def test_either_relevant_history_and_censored_samples_invalidate(workflow, censored):
    model = OnlineWorkloadToolTiming()
    ready(model)
    before = model.estimate_revision("workflow", "agent")
    completed(model, 1, workflow=workflow, censored=censored)
    assert model.estimate_revision("workflow", "agent") != before
    assert model._history_revisions.keys() == model.samples.keys()


def test_history_eviction_and_recreation_cannot_reuse_a_revision():
    model = OnlineWorkloadToolTiming(max_keys=2)
    completed(model, 1)
    ready(model)
    before = model.estimate_revision("workflow", "agent")
    completed(model, 2, shape="git_status")
    absent = model.estimate_revision("workflow", "agent")
    assert absent != before
    assert absent[-2:] == (None, None)
    completed(model, 3)
    assert model.estimate_revision("workflow", "agent") not in (before, absent)
    assert model._history_revisions.keys() == model.samples.keys()


def test_bounded_window_updates_even_when_newest_sample_is_unchanged():
    model = OnlineWorkloadToolTiming(window=2)
    completed(model, 1, duration=100.)
    completed(model, 2, duration=1000.)
    ready(model)
    before = model.estimate_revision("workflow", "agent")
    completed(model, 3, duration=1000.)
    assert model.estimate_revision("workflow", "agent") != before
    assert list(model.samples[("workflow", "sandbox", "pytest")]) == [
        (1000., True), (1000., True),
    ]


def test_duplicate_start_parallel_group_and_terminal_cleanup_keep_live_identity():
    model = OnlineWorkloadToolTiming()
    ready(model)
    before = model.estimate_revision("workflow", "agent")
    ready(model)
    assert model.estimate_revision("workflow", "agent") == before
    ready(model, call="second")
    assert model.estimate_revision("workflow", "agent") is None
    model.observe(tool_event(
        RuntimeEventKind.TOOL_END, call="second", start=10_100., duration_ms=float("nan"),
    ))
    assert model.estimate_revision("workflow", "agent") == before
    model.observe(tool_event(RuntimeEventKind.RETURN))
    assert model.estimate_revision("workflow", "agent") is None
    ready(model, call="next")
    assert model.estimate_revision("workflow", "agent") != before


def test_invalid_duration_does_not_invalidate_surviving_history():
    model = OnlineWorkloadToolTiming()
    ready(model)
    before = model.estimate_revision("workflow", "agent")
    completed(model, 1, duration=float("nan"))
    assert model.estimate_revision("workflow", "agent") == before
    assert not model._history_revisions


@pytest.mark.parametrize("related", (False, True))
def test_runtime_recomputes_only_related_history_before_existing_retry_deadline(related):
    runtime, hint = tool_runtime()
    runtime._online_tool_timing = model = OnlineWorkloadToolTiming()
    for index in range(8):
        completed(model, index, workflow=hint.key.root_workflow_id)
    ready(
        model, workflow=hint.key.root_workflow_id,
        invocation=hint.key.invocation_id,
    )
    clock = [10.]
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic", lambda: clock[0],
        ), patch.object(model, "estimate", wraps=model.estimate) as estimate:
            runtime._submit_online_tool_wait(hint.key.context_id)
            assert estimate.call_count == 1
            completed(model, 100, shape="pytest" if related else "git_status")
            runtime._submit_online_tool_wait(hint.key.context_id)
            assert estimate.call_count == 1 + int(related)
            clock[0] += .151
            runtime._submit_online_tool_wait(hint.key.context_id)
            assert estimate.call_count == 2 + int(related)
            assert not runtime.physical_ledger.pending_count
            assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("max_keys", (1, 2))
def test_runtime_drops_a_supported_hint_when_unrelated_history_evicts_its_sources(max_keys):
    runtime, hint = tool_runtime()
    runtime._online_tool_timing = model = OnlineWorkloadToolTiming(max_keys=max_keys)
    for index in range(8):
        completed(model, index, workflow=hint.key.root_workflow_id)
    ready(
        model, workflow=hint.key.root_workflow_id,
        invocation=hint.key.invocation_id,
    )
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", lambda: 10.):
            runtime._submit_online_tool_wait(hint.key.context_id)
            assert hint.key.context_id in runtime.tool_wait_hints
            completed(model, 100, shape="git_status")
            runtime._submit_online_tool_wait(hint.key.context_id)
            assert hint.key.context_id not in runtime.tool_wait_hints
            assert runtime.counts["online_tool_cold_or_tail_unsupported"] == 1
            assert not runtime.physical_disabled
            assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def predicted_runtime():
    runtime, hint = tool_runtime()
    runtime._online_tool_timing = model = OnlineWorkloadToolTiming()
    for index in range(8):
        completed(
            model, index, workflow=hint.key.root_workflow_id,
            duration=1000. + index * 100.,
        )
    ready(
        model, workflow=hint.key.root_workflow_id,
        invocation=hint.key.invocation_id, call="long",
    )
    runtime._submit_online_tool_wait(hint.key.context_id)
    return runtime, runtime.context_sessions[hint.key.context_id]


@pytest.mark.parametrize("new_session", (None, "s"))
def test_successor_request_retires_only_the_old_query_identity(new_session):
    with patch("time.monotonic", lambda: 10.):
        runtime, key = predicted_runtime()
        other = replace(key, request_id="unrelated", context_id="unrelated")
        runtime._online_tool_queries[other] = record = (12., None, 99_000.)
        successor = req("tool")
        successor.rid = "successor"
        successor.session_id = new_session
        successor.session_generation = 1 if new_session is not None else None
        try:
            assert runtime.register_visible_request(successor)
            assert key not in runtime._online_tool_queries
            assert runtime._online_tool_queries == {other: record}
            assert runtime._online_tool_timing.samples
            assert not runtime.physical_ledger.pending_count
        finally:
            runtime.close()


@pytest.mark.parametrize("change_attempt", (False, True))
def test_requeue_preserves_current_query_deadline_and_retires_replaced_attempt(change_attempt):
    with patch("time.monotonic", lambda: 10.):
        runtime, key = predicted_runtime()
        request = req("tool")
        request.session_id, request.session_generation = "s", 1
        request.cache_request_handle.attempt_id = int(change_attempt)
        record = runtime._online_tool_queries[key]
        try:
            runtime.on_requests_requeued((request,), is_retracted=change_attempt)
            if change_attempt:
                assert not runtime._online_tool_queries
            else:
                assert runtime._online_tool_queries == {key: record}
                with patch.object(
                    runtime._online_tool_timing, "estimate",
                    side_effect=AssertionError("the unchanged live query must retain its deadline"),
                ):
                    runtime._submit_online_tool_wait(key.context_id)
            assert not runtime.physical_ledger.pending_count
        finally:
            runtime.close()


@pytest.mark.parametrize("kind", (
    RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL,
))
def test_terminal_context_retires_query_before_its_workflow_ends(kind):
    with patch("time.monotonic", lambda: 10.):
        runtime, key = predicted_runtime()
        try:
            runtime.on_events((event(
                3, kind, invocation_id="tool", context_id="ctx-tool",
            ),))
            assert key not in runtime._online_tool_queries
            assert runtime.graph.workflows[key.root_workflow_id].end_ts_ms is None
            assert runtime._online_tool_timing.samples
        finally:
            runtime.close()


@pytest.mark.parametrize("retire", ("forget", "abort"))
def test_session_retirement_releases_online_query_without_discarding_training(retire):
    with patch("time.monotonic", lambda: 10.):
        runtime, key = predicted_runtime()
        try:
            if retire == "forget":
                runtime._forget_session(key.request_id)
            else:
                runtime.on_abort_request(NS(abort_all=False, rid=key.request_id))
            assert not runtime._online_tool_queries
            assert runtime._online_tool_timing.samples
        finally:
            runtime.close()


def test_repeated_attempt_changes_keep_one_live_query_and_identical_predictions():
    with patch("time.monotonic", lambda: 10.):
        runtime, key = predicted_runtime()
        request = req("tool")
        request.session_id, request.session_generation = "s", 1
        expected = asdict(runtime.tool_wait_hints[key.context_id])
        expected.pop("key")
        try:
            for attempt in range(1, 257):
                request.cache_request_handle.attempt_id = attempt
                runtime.on_requests_requeued((request,), is_retracted=True)
                runtime._submit_online_tool_wait(key.context_id)
                current = runtime.context_sessions[key.context_id]
                assert set(runtime._online_tool_queries) == {current}
                observed = asdict(runtime.tool_wait_hints[key.context_id])
                assert observed.pop("key") == asdict(current)
                assert observed == expected
            assert not runtime.physical_disabled
            assert not runtime.physical_ledger.pending_count
        finally:
            runtime.close()


def test_discarded_causal_mirror_cannot_retain_online_query_authority():
    with patch("time.monotonic", lambda: 10.):
        runtime, _ = predicted_runtime()
        try:
            with pytest.raises(CausalGraphError, match="unknown invocation"):
                runtime.on_events((event(
                    3, RuntimeEventKind.TOOL_END, invocation_id="unknown",
                    attributes={"tool_run_id": "missing"},
                ),))
            assert not runtime._online_tool_queries
            assert runtime.counts["causal_mirror_discarded"] == 1
        finally:
            runtime.close()
