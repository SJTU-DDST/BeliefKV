"""Call-local queue parsing is advisory; final identities and terminal state stay live."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, req


@pytest.fixture
def queue_fixture():
    runtime = NativeAdmissionRuntime()
    runtime._tool_timing_only = True
    runtime.enable_resident_first = False
    runtime.enable_final_stage_priority = False
    runtime.on_events((event(0, RuntimeEventKind.WORKFLOW_START),))
    requests = []
    for index, name in enumerate(("first", "second"), 1):
        runtime.on_events((event(
            index, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id=name, context_id=f"ctx-{name}", context_epoch=0,
        ),))
        request = req(name)
        request.session_id, request.session_generation = f"session-{name}", 1
        request.origin_input_ids, request.output_ids = [1, 2], []
        assert runtime.register_visible_request(request)
        requests.append(request)
    try:
        yield runtime, [requests[0], req("plain", tagged=False), requests[1]]
    finally:
        runtime.close()


def plan_and_select(runtime, queue, inspection=None, adder=None):
    plan = runtime.plan_native_prefill(
        queue, running_batch=None, adder=adder, _queue_inspection=inspection,
    )
    selected = select_native_prefill_candidates(
        queue, plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    return plan, selected


@pytest.mark.parametrize("mode", ("ranked", "native", "drained", "single"))
def test_inspected_queue_keeps_plan_selection_and_native_positions(queue_fixture, mode):
    runtime, queue = queue_fixture
    adder = None
    if mode == "native":
        runtime._native_prefill_order = True
    elif mode == "drained":
        adder = NS(rem_chunk_tokens=0, can_run_list=[object()], dllm_config=None)
    elif mode == "single":
        queue = queue[:2]
    expected = plan_and_select(runtime, queue, adder=adder)
    inspection = runtime.inspect_native_prefill_queue(queue)
    assert inspection.terminal_request_ids == ()
    assert inspection.entries[0][1] is runtime.visible["first"]
    assert plan_and_select(runtime, queue, inspection, adder) == expected
    assert expected[1].candidates[1] is queue[1]
    assert not runtime.physical_ledger.pending_count


@pytest.mark.parametrize("change", (
    "semantic", "graph", "sessions", "reorder", "replacement", "append",
    "remove", "foreign_owner", "terminal_snapshot",
))
def test_changed_queue_or_revision_reparses_instead_of_using_old_plan_inputs(
    queue_fixture, change,
):
    runtime, queue = queue_fixture
    inspection = runtime.inspect_native_prefill_queue(queue)
    if change == "semantic":
        runtime.semantic_revision += 1
    elif change == "graph":
        runtime.graph._graph_version += 1
    elif change == "sessions":
        runtime._context_sessions_revision += 1
    elif change == "reorder":
        queue[:] = list(reversed(queue))
    elif change == "replacement":
        queue[0] = NS(**vars(queue[0]))
    elif change == "append":
        queue.append(req("extra", tagged=False))
    elif change == "remove":
        queue.pop(1)
    elif change == "foreign_owner":
        inspection = replace(inspection, owner=object())
    else:
        inspection = replace(inspection, terminal_request_ids=("already-ended",))
    tagged = next(request for request in queue if request.beliefkv_metadata is not None)
    tagged.beliefkv_metadata = {**tagged.beliefkv_metadata, "context_epoch": False}
    expected = plan_and_select(runtime, queue)
    assert tagged.rid not in {key.request_id for key in expected[0].prioritized}
    assert plan_and_select(runtime, queue, inspection) == expected


@pytest.mark.parametrize("change", (
    "workflow", "invocation", "context", "epoch", "bool_epoch", "attempt",
    "bool_attempt", "session", "generation", "bool_generation", "session_alias",
))
def test_final_selector_rechecks_live_fields_after_inspection(queue_fixture, change):
    runtime, queue = queue_fixture
    inspection = runtime.inspect_native_prefill_queue(queue)
    target = queue[0]
    if change == "workflow":
        target.beliefkv_metadata["root_workflow_id"] = "other"
    elif change == "invocation":
        target.beliefkv_metadata["invocation_id"] = "other"
    elif change == "context":
        target.beliefkv_metadata["context_id"] = "other"
    elif change == "epoch":
        target.beliefkv_metadata["context_epoch"] = 1
    elif change == "bool_epoch":
        target.beliefkv_metadata["context_epoch"] = False
    elif change == "attempt":
        target.cache_request_handle.attempt_id = 1
    elif change == "bool_attempt":
        target.cache_request_handle.attempt_id = False
    elif change == "session":
        target.session_id = "other"
    elif change == "generation":
        target.session_generation = 2
    elif change == "bool_generation":
        target.session_generation = True
    else:
        target.session = NS(session_id="other")
    _, selected = plan_and_select(runtime, queue, inspection)
    assert target not in selected.candidates
    assert selected.candidates == (queue[1], queue[2])
    assert selected.rejected[0][0] == target.rid
    assert selected.rejected[0][1] in ("invalid_identity", "identity_changed")
    assert not runtime.physical_ledger.pending_count


@pytest.mark.parametrize("change", ("workflow", "invocation", "epoch"))
def test_final_terminal_check_remains_live_even_without_a_revision_change(
    queue_fixture, change,
):
    runtime, queue = queue_fixture
    inspection = runtime.inspect_native_prefill_queue(queue)
    if change == "workflow":
        runtime.graph.workflows["wf"].end_ts_ms = 100.
    elif change == "invocation":
        runtime.graph.invocations["first"].state = InvocationState.DONE
    else:
        runtime.graph.contexts["ctx-first"].epoch = 1
    _, selected = plan_and_select(runtime, queue, inspection)
    assert queue[0] not in selected.candidates
    assert selected.rejected[0] == ("first", "no_authorization")
    assert not runtime.physical_ledger.pending_count


def test_invalid_identity_can_be_repaired_before_planning(queue_fixture):
    runtime, queue = queue_fixture
    queue[0].beliefkv_metadata["context_epoch"] = False
    inspection = runtime.inspect_native_prefill_queue(queue)
    assert inspection.entries[0][1] is None
    queue[0].beliefkv_metadata["context_epoch"] = 0
    assert plan_and_select(runtime, queue, inspection) == plan_and_select(runtime, queue)
    assert queue[0] in plan_and_select(runtime, queue, inspection)[1].candidates


def test_terminal_identification_is_unchanged_and_retirement_discards_old_inputs(queue_fixture):
    runtime, queue = queue_fixture
    runtime.graph.invocations["first"].state = InvocationState.DONE
    inspection = runtime.inspect_native_prefill_queue(queue)
    assert inspection.terminal_request_ids == runtime.terminal_waiting_request_ids(queue) == ("first",)
    runtime.retire_terminal_request("first")
    queue.pop(0)
    assert plan_and_select(runtime, queue, inspection) == plan_and_select(runtime, queue)


def test_tagged_bound_is_preserved_when_native_requests_share_the_queue(queue_fixture):
    runtime, queue = queue_fixture
    tagged = []
    for index in range(513):
        request = req(f"bounded-{index}")
        assert runtime.register_visible_request(request)
        tagged.append(request)
    queued = [*tagged[:512], queue[1], tagged[-1]]
    inspection = runtime.inspect_native_prefill_queue(queued)
    plan, selected = plan_and_select(runtime, queued, inspection)
    assert len(plan.prioritized) == 512
    assert selected.candidates == (*tagged[:512], queue[1])
    assert selected.rejected == ((tagged[-1].rid, "no_authorization"),)
