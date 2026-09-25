"""Native v0.5.20 semantic producer never authorizes physical operations."""

from __future__ import annotations

import json
import hashlib
import socket
import time
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.predictor.completion_lead import CompletionLead
from beliefkv.runtime.event_channel import SCHEMA_VERSION
from beliefkv.runtime.sglang_v0520_admission import (
    PrefillCandidateKey,
    select_native_prefill_candidates,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_prediction import (
    NativeDemandHint, NativeJoinWaitHint, NativeToolWaitHint,
)
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PhysicalReceiptError,
    PrefetchLoadStep,
    ShadowBackupStep,
)


def test_native_ack_is_credited_only_after_live_context_reconciliation():
    runtime = NativeAdmissionRuntime()
    tagged = req("a")
    assert runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(
            1, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="a", context_id="ctx-a",
            agent_definition_id="a", agent_instance_id="a",
        ),
    ))
    expectation = PhysicalActionExpectation(
        command_id="prepare-a",
        action="PREPARE_HOST",
        context_id="ctx-a",
        context_epoch=0,
        children=(PhysicalChildExpectation(
            anchor_node_id=11,
            published_node_ids=(11,),
            pool_bytes=(("kv", 20), ("mamba", 5)),
            num_bytes=25,
        ),),
        pool_bytes_per_token=(("kv", 10), ("mamba", 5)),
    )
    runtime.register_physical_action(expectation)
    runtime.on_native_transfer_commit(NS(
        direction="d2h", status="completed", node_ids=(11,),
        num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
        child_commits=(NS(
            command_id="prepare-a", anchor_node_id=11,
            published_node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            num_bytes=25,
        ),),
    ))
    assert [action.command_id for action in runtime.completed_physical_actions] == [
        "prepare-a"
    ]
    assert runtime.counts["native_physical_completed"] == 1
    assert runtime.physical_ledger.pending_count == 0
    runtime.on_native_transfer_commit(NS(
        direction="d2h", status="completed", node_ids=(11,),
        num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
        child_commits=(NS(
            command_id="prepare-a", anchor_node_id=11,
            published_node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            num_bytes=25,
        ),),
    ))
    assert runtime.physical_disabled
    assert runtime.counts["physical_receipt_failed"] == 1
    with pytest.raises(PhysicalReceiptError, match="no live causal context"):
        runtime.register_physical_action(expectation)


def test_finished_tool_context_keeps_session_anchor_but_rechecks_wait_state():
    runtime = NativeAdmissionRuntime()
    tagged = req("a")
    tagged.session_id = "session-a"
    tagged.session_generation = 4
    assert runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(
            1, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="a", context_id="ctx-a",
            agent_definition_id="a", agent_instance_id="a",
        ),
        event(
            2, RuntimeEventKind.TOOL_START,
            invocation_id="a", context_id="ctx-a",
            attributes={"tool_family": "shell"},
        ),
    ))
    tagged.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=(tagged,)))
    assert "a" not in runtime.visible
    cache = NS(session_refs=NS(
        snapshot_session_leaf_anchors=lambda session, generation, max_leaves: (
            ((0, ((11, 25),)), (2, ((11, 25),)))
            if (session, generation, max_leaves) == ("session-a", 4, 8)
            else None
        ),
    ))
    anchors = runtime.snapshot_session_anchors(
        cache, context_id="ctx-a", context_epoch=0
    )
    assert anchors is not None and anchors.key.session_generation == 4
    assert anchors.component_leaves[0][1] == ((11, 25),)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow",
        return_value="local-candidate",
    ) as capture:
        assert runtime.capture_shadow_candidate(
            cache, context_id="ctx-a", context_epoch=0
        ) == "local-candidate"
        assert capture.call_args.args[1].key == anchors.key
    assert runtime.snapshot_session_anchors(
        cache, context_id="ctx-a", context_epoch=1
    ) is None
    expected = PhysicalActionExpectation(
        "prepare-tool", "PREPARE_HOST", "ctx-a", 0,
        (PhysicalChildExpectation(11, (11,), (("kv", 10),), 10),),
        (("kv", 10),),
        session_id="session-a", session_generation=4,
    )
    runtime.register_physical_action(expected)
    with pytest.raises(PhysicalReceiptError, match="live causal context"):
        runtime.register_physical_action(
            PhysicalActionExpectation(
                "wrong-session", "PREPARE_HOST", "ctx-a", 0,
                expected.children, expected.pool_bytes_per_token,
                session_id="session-b", session_generation=4,
            )
        )
    runtime.on_events((
        event(3, RuntimeEventKind.TOOL_END, invocation_id="a", context_id="ctx-a"),
    ))
    with pytest.raises(PhysicalReceiptError, match="live causal context"):
        runtime.register_physical_action(
            PhysicalActionExpectation(
                "late", "PREPARE_HOST", "ctx-a", 0,
                (PhysicalChildExpectation(12, (12,), (("kv", 10),), 10),),
                expected.pool_bytes_per_token,
                session_id="session-a", session_generation=4,
            )
        )
    runtime.on_abort_request(NS(rid="a", abort_all=False))
    assert runtime.snapshot_session_anchors(
        cache, context_id="ctx-a", context_epoch=0
    ) is None


def test_tool_start_triggers_bounded_wait_prediction_then_local_probe():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    sent = []
    worker = NS(
        disabled=False, fileno=lambda: 71, poll=lambda: (),
        submit_tool_wait=lambda batch: sent.append(batch),
        close=lambda: None,
    )
    runtime._model_worker = worker
    runtime.attach_native_cache(object())
    request = req("tool")
    request.session_id = "session-tool"
    request.session_generation = 2
    request.origin_input_ids = [1, 2, 3]
    request.output_ids = [4, 5]
    runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(
            1, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="tool", context_id="ctx-tool",
            agent_definition_id="role", agent_instance_id="tool",
        ),
    ))
    runtime.on_batch_completed(NS(reqs=(request,)))
    assert runtime._context_tokens["ctx-tool"] == (0, 3, 2, False)
    request.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=(request,)))
    runtime.on_events((
        event(
            2, RuntimeEventKind.TOOL_START,
            invocation_id="tool", attributes={
                "tool_family": "shell", "backend_class": "sandbox",
                "command_class": "shell_read",
                "observed_command_class": "test_suite",
            },
        ),
    ))
    assert len(sent) == 1
    key, features, revision = sent[0][0]
    assert key.session_id == "session-tool"
    assert features.state == "wait_tool"
    assert features.generated_tokens == 2
    assert features.current_sequence_tokens == 5
    assert features.tool_family == "shell"
    assert features.backend_class == "sandbox"
    assert features.command_class == "shell_read"
    assert features.observed_command_class == "test_suite"
    assert features.active_tool_count == 1
    assert features.backend_pressure == "active_family:1"
    assert revision == 2.0
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=3.5):
        runtime._submit_tool_wait("ctx-tool")
    updated = sent[-1][0][1]
    assert updated.invocation_elapsed_ms == 3499.0
    assert updated.state_elapsed_ms == 3498.0
    assert updated.elapsed_wait_ms == 3498.0
    now = time.monotonic() * 1000
    hint = NativeToolWaitHint(key, 100.0, 300.0, 600.0, now, now + 5_000, "a" * 64, revision)
    worker.poll = lambda: (hint,)
    sentinel = object()
    with patch.object(runtime, "capture_shadow_candidate", return_value=sentinel) as capture:
        runtime.scheduler_step()
    assert runtime.tool_wait_hint == hint
    assert runtime.shadow_candidate is sentinel
    assert capture.call_count == 1
    assert runtime.counts["tool_wait_accepted"] == 1
    step = object()
    with patch.object(runtime, "capture_shadow_candidate", return_value=sentinel) as recapture:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.next_shadow_backup_step",
            return_value=step,
        ):
            assert runtime.refreshed_shadow_backup_step() is step
    assert recapture.call_count == 1
    prefetch_step = PrefetchLoadStep(key, 11, 25, 11, 25)
    with patch.object(runtime, "capture_shadow_candidate", return_value=sentinel) as recapture:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.next_prefetch_gpu_step",
            return_value=prefetch_step,
        ):
            assert runtime.refreshed_prefetch_gpu_step() is prefetch_step
    assert recapture.call_args.kwargs["for_prefetch"] is True
    runtime._forget_session("tool")
    assert runtime.tool_wait_hint is None
    assert runtime.shadow_candidate is None
    assert "ctx-tool" not in runtime._context_tokens
    runtime.tool_wait_hint = hint
    runtime.shadow_candidate = sentinel
    runtime.on_events((
        event(3, RuntimeEventKind.TOOL_END, invocation_id="tool"),
    ))
    assert tuple(runtime._boundary_history["tool"]) == ("tool_end",)
    assert "tool" not in runtime._tool_metadata
    assert runtime.tool_wait_hint is None
    assert runtime.shadow_candidate is None
    with patch.object(runtime, "capture_shadow_candidate") as capture:
        assert runtime.refreshed_prefetch_gpu_step() is None
        capture.assert_not_called()
    runtime.scheduler_step()
    assert runtime.counts["tool_wait_result_stale"] == 1
    runtime.close()


def test_live_child_decode_progress_enters_join_forecast():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    submitted = []
    runtime._model_worker = NS(
        disabled=False, submit_join_wait=lambda batch: submitted.extend(batch),
    )
    parent, child = req("parent"), req("child")
    parent.session_id, parent.session_generation = "parent-session", 1
    child.session_id, child.session_generation = "child-session", 1
    child.beliefkv_metadata["parent_invocation_id"] = "parent"
    child.origin_input_ids = list(range(10))
    child.output_ids = list(range(3))
    runtime.register_visible_request(parent)
    runtime.register_visible_request(child)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              parent_invocation_id="parent", relation_type="spawn",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    runtime.on_batch_completed(NS(reqs=(child,)))
    runtime._submit_join_wait((event(
        4, RuntimeEventKind.JOIN_WAIT, invocation_id="parent", join_id="join"
    ),))
    features = submitted[-1][-1][0][1]
    assert (features.generated_tokens, features.current_sequence_tokens) == (3, 13)
    assert features.is_child is True
    child.output_ids.append(3)
    runtime.on_batch_completed(NS(reqs=(child,)))
    runtime._submit_join_wait((event(
        4, RuntimeEventKind.JOIN_WAIT, invocation_id="parent", join_id="join"
    ),))
    assert submitted[-1][-1][0][1].generated_tokens == 4


def test_long_tool_wait_refresh_is_bounded_and_requires_idle_worker():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    submitted = []
    idle = [True]
    runtime._model_worker = NS(
        disabled=False, poll=lambda: (), fileno=lambda: 71,
        idle_for_refresh=lambda: idle[0],
        submit_tool_wait=lambda batch: submitted.append(batch),
    )
    request = req("tool")
    request.session_id, request.session_generation = "session-tool", 1
    runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="tool", context_id="ctx-tool",
              agent_definition_id="tool", agent_instance_id="tool"),
        event(2, RuntimeEventKind.TOOL_START,
              invocation_id="tool", attributes={"tool_family": "shell"}),
    ))
    key = runtime.context_sessions["ctx-tool"]
    runtime.tool_wait_hint = NativeToolWaitHint(
        key, 300.0, 1000.0, 5000.0, 900.0, 8000.0,
        "a" * 64, runtime.graph.invocations["tool"].updated_ts_ms,
    )
    submitted.clear()
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=3.0):
        idle[0] = False
        runtime.scheduler_step()
        assert not submitted
        idle[0] = True
        runtime.scheduler_step()
        runtime.scheduler_step()
        assert len(submitted) == 1
        assert runtime.counts["tool_wait_refresh_submitted"] == 1
    runtime.on_events((event(
        3, RuntimeEventKind.TOOL_END, invocation_id="tool"
    ),))
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=3.6):
        runtime.scheduler_step()
    assert len(submitted) == 1


def test_two_tool_wait_hints_survive_other_context_completion():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    requests = (req("a"), req("b"))
    for request in requests:
        request.session_id = f"session-{request.rid}"
        request.session_generation = 1
        runtime.register_visible_request(request)
    pending = []
    submitted = []
    runtime._model_worker = NS(
        disabled=False, poll=lambda: tuple(pending),
        submit_tool_wait=lambda batch: submitted.append(batch),
    )
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="a", context_id="ctx-a",
              agent_definition_id="a", agent_instance_id="a"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="b", context_id="ctx-b",
              agent_definition_id="b", agent_instance_id="b"),
        event(3, RuntimeEventKind.TOOL_START,
              invocation_id="a", context_id="ctx-a"),
        event(4, RuntimeEventKind.TOOL_START,
              invocation_id="b", context_id="ctx-b"),
    ))
    assert {batch[0][0].request_id for batch in submitted} == {"a", "b"}
    now = time.monotonic() * 1000
    pending.extend(
        NativeToolWaitHint(
            runtime.context_sessions[f"ctx-{name}"],
            300.0, 1000.0, 5000.0, now, now + 5_000, "a" * 64,
            runtime.graph.invocations[name].updated_ts_ms,
        )
        for name in ("a", "b")
    )
    runtime.scheduler_step()
    assert set(runtime.tool_wait_hints) == {"ctx-a", "ctx-b"}
    runtime.on_events((event(5, RuntimeEventKind.TOOL_END,
                             invocation_id="a", context_id="ctx-a"),))
    assert set(runtime.tool_wait_hints) == {"ctx-b"}
    assert runtime.tool_wait_hint.key.context_id == "ctx-b"


def test_idle_wait_scan_revisits_tool_waits_beyond_worker_batch_bound():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    submitted = []
    runtime._model_worker = NS(
        disabled=False, poll=lambda: (), idle_for_refresh=lambda: True,
        submit_tool_wait=lambda items: submitted.append(items),
    )
    requests = [req(f"tool-{index}") for index in range(10)]
    for request in requests:
        request.session_id, request.session_generation = request.rid, 1
        runtime.register_visible_request(request)
    events = [event(0, RuntimeEventKind.WORKFLOW_START)]
    events.extend(
        event(index + 1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id=request.rid, context_id=f"ctx-{request.rid}",
              agent_definition_id="role", agent_instance_id=request.rid)
        for index, request in enumerate(requests)
    )
    events.extend(
        event(index + 11, RuntimeEventKind.TOOL_START,
              invocation_id=request.rid, context_id=f"ctx-{request.rid}")
        for index, request in enumerate(requests)
    )
    runtime.on_events(tuple(events))
    submitted.clear()
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=3.0):
        runtime.scheduler_step()
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=3.6):
        runtime.scheduler_step()
    assert len(submitted) == 16
    assert {batch[0][0].context_id for batch in submitted} == {
        f"ctx-tool-{index}" for index in range(10)
    }


def test_join_wait_batch_covers_two_independent_parents():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    submitted = []
    runtime._model_worker = NS(
        disabled=False, submit_join_wait=lambda items: submitted.append(items),
        poll=lambda: (),
    )
    for name in ("parent-a", "parent-b"):
        request = req(name)
        request.session_id, request.session_generation = f"session-{name}", 1
        runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent-a", context_id="ctx-parent-a",
              agent_definition_id="parent", agent_instance_id="parent-a"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent-b", context_id="ctx-parent-b",
              agent_definition_id="parent", agent_instance_id="parent-b"),
        event(3, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child-a", context_id="ctx-child-a",
              agent_definition_id="child", agent_instance_id="child-a"),
        event(4, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child-b", context_id="ctx-child-b",
              agent_definition_id="child", agent_instance_id="child-b"),
        event(5, RuntimeEventKind.JOIN_CREATE,
              join_id="join-a", member_invocation_ids=("child-a",)),
        event(6, RuntimeEventKind.JOIN_CREATE,
              join_id="join-b", member_invocation_ids=("child-b",)),
        event(7, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent-a", join_id="join-a"),
        event(8, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent-b", join_id="join-b"),
    ))
    assert len(submitted) == 1
    assert {item[2] for item in submitted[0]} == {"join-a", "join-b"}


def test_join_hints_for_independent_parents_do_not_replace_each_other():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    pending = []
    runtime._model_worker = NS(
        disabled=False, poll=lambda: tuple(pending), submit_join_wait=lambda _: None,
    )
    for name in ("parent-a", "parent-b"):
        request = req(name)
        request.session_id, request.session_generation = name, 1
        runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent-a", context_id="ctx-parent-a",
              agent_definition_id="parent", agent_instance_id="parent-a"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent-b", context_id="ctx-parent-b",
              agent_definition_id="parent", agent_instance_id="parent-b"),
        event(3, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child-a", context_id="ctx-child-a",
              agent_definition_id="child", agent_instance_id="child-a"),
        event(4, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child-b", context_id="ctx-child-b",
              agent_definition_id="child", agent_instance_id="child-b"),
        event(5, RuntimeEventKind.JOIN_CREATE,
              join_id="join-a", member_invocation_ids=("child-a",)),
        event(6, RuntimeEventKind.JOIN_CREATE,
              join_id="join-b", member_invocation_ids=("child-b",)),
        event(7, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent-a", join_id="join-a"),
        event(8, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent-b", join_id="join-b"),
    ))
    now = time.monotonic() * 1000
    for suffix in ("a", "b"):
        child = runtime.graph.invocations[f"child-{suffix}"]
        parent = runtime.graph.invocations[f"parent-{suffix}"]
        pending.append(NativeJoinWaitHint(
            runtime.context_sessions[parent.context_id],
            f"join-{suffix}", "all", (f"child-{suffix}",),
            ((child.invocation_id, child.updated_ts_ms, child.state.value, 0),),
            100.0, 200.0, 300.0, now, now + 5_000, "a" * 64,
            parent.updated_ts_ms,
        ))
    runtime.scheduler_step()
    assert set(runtime.join_wait_hints) == {"join-a", "join-b"}
    runtime.on_events((event(9, RuntimeEventKind.RETURN,
                             invocation_id="child-a"),))
    assert set(runtime.join_wait_hints) == {"join-b"}


def test_online_boundary_feature_matches_observed_result_action():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    submitted = []
    runtime._model_worker = NS(
        disabled=False, submit_tool_wait=lambda batch: submitted.extend(batch),
    )
    request = req("tool")
    request.session_id, request.session_generation = "session-tool", 1
    runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="tool", context_id="ctx-tool",
              agent_definition_id="tool", agent_instance_id="tool"),
        event(2, RuntimeEventKind.LLM_RESULT, invocation_id="tool",
              attributes={"structured_action_kinds": ["function_call"]}),
        event(3, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_family": "shell"}),
    ))
    assert submitted[-1][1].boundary_history == ("function_call",)
    runtime.on_events((
        event(4, RuntimeEventKind.TOOL_END, invocation_id="tool"),
        event(5, RuntimeEventKind.RETURN, invocation_id="tool"),
    ))
    assert "tool" not in runtime._boundary_history
    assert "tool" not in runtime._tool_metadata


def test_long_join_wait_refresh_preserves_provisional_ticket():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    runtime.enable_admission_prefetch = True
    parent = req("parent")
    parent.session_id, parent.session_generation = "session-parent", 1
    runtime.register_visible_request(parent)
    sent = []
    runtime._model_worker = NS(
        disabled=False, poll=lambda: (), fileno=lambda: 72,
        idle_for_refresh=lambda: True,
        submit_join_wait=lambda batch: sent.extend(batch),
    )
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    key = runtime.context_sessions["ctx-parent"]
    child = runtime.graph.invocations["child"]
    runtime.join_wait_hint = NativeJoinWaitHint(
        key, "join", "all", ("child",),
        (("child", child.updated_ts_ms, child.state.value, 0),),
        300.0, 1000.0, 5000.0, 900.0, 8000.0,
        "a" * 64, runtime.graph.invocations["parent"].updated_ts_ms,
    )
    sent.clear()
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=3.0):
        runtime.scheduler_step()
        assert len(sent) == 1
        assert sent[0][2] == "join"
        assert runtime.counts["join_wait_refresh_submitted"] == 1
    runtime.on_events((event(
        5, RuntimeEventKind.RETURN, invocation_id="child"
    ),))
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=3.6):
        runtime.scheduler_step()
    assert len(sent) == 1


def test_shadow_step_recheck_rejects_changed_tool_invocation():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    tagged = req("tool")
    tagged.session_id = "session-tool"
    tagged.session_generation = 2
    runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(
            1, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="tool", context_id="ctx-tool",
            agent_definition_id="role", agent_instance_id="tool",
        ),
        event(
            2, RuntimeEventKind.TOOL_START, invocation_id="tool",
            attributes={"tool_family": "shell"},
        ),
    ))
    key = runtime.context_sessions["ctx-tool"]
    now = time.monotonic() * 1000
    runtime.tool_wait_hint = NativeToolWaitHint(
        key, 100.0, 300.0, 600.0, now, now + 5_000, "a" * 64,
        invocation_revision_ts_ms=0.0,
    )
    runtime.shadow_candidate = object()
    runtime.attach_native_cache(object())
    with patch.object(runtime, "capture_shadow_candidate") as capture:
        assert runtime.refreshed_shadow_backup_step() is None
        capture.assert_not_called()
    assert runtime.tool_wait_hint is None
    assert runtime.shadow_candidate is None


def test_native_shadow_transaction_waits_for_matching_ack():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    request = req("tool")
    request.session_id = "session-tool"
    request.session_generation = 2
    assert runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(
            1, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="tool", context_id="ctx-tool",
            agent_definition_id="role", agent_instance_id="tool",
        ),
        event(2, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_family": "shell"}),
    ))
    key = runtime.context_sessions["ctx-tool"]
    now = time.monotonic() * 1000
    runtime.tool_wait_hint = NativeToolWaitHint(
        key, 100.0, 300.0, 600.0, now, now + 5_000, "a" * 64, 2.0
    )
    step = ShadowBackupStep(key, 11, 4, 11, 4)
    controller = NS(
        mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=5)),
        }),
        _num_tokens_by_pool=lambda _: {"kv": 2, "mamba": 1},
        _transfer_num_bytes=lambda _: 27,
    )
    sent = []

    def native_shadow(**kwargs):
        op = NS(
            beliefkv_command_id=kwargs["beliefkv_command_id"],
            node_ids=[kwargs["node_id"]],
            device_indices=(0, 1),
            host_indices=(2, 3),
        )
        if not kwargs["beliefkv_before_enqueue"](op):
            return NS(issued=False, node_id=None)
        sent.append(op)
        return NS(issued=True, node_id=11)

    runtime.attach_native_cache(NS(
        cache_controller=controller, prepare_host_shadow=native_shadow
    ))
    with patch.object(runtime, "capture_shadow_candidate", return_value=object()), patch(
        "beliefkv.runtime.sglang_v0520_runtime.next_shadow_backup_step",
        return_value=step,
    ):
        command_id = runtime.issue_shadow_backup_step(step)
        assert command_id is not None
        assert len(sent) == 1
        assert runtime.physical_ledger.pending_count == 1
        assert not runtime.completed_physical_actions
        runtime.on_native_transfer_commit(NS(
            direction="d2h", status="completed", node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            child_commits=(NS(
                command_id=command_id, anchor_node_id=11,
                published_node_ids=(11,),
                num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
                num_bytes=27,
            ),),
        ))
        assert runtime.completed_physical_actions[0].command_id == command_id
        assert runtime.physical_ledger.pending_count == 0

        controller._transfer_num_bytes = lambda _: 1
        assert runtime.issue_shadow_backup_step(step) is None
        assert len(sent) == 1
        assert runtime.physical_ledger.pending_count == 0
        assert runtime.counts["shadow_reservation_rejected"] == 1


def test_native_shadow_explicit_decline_cancels_unsubmitted_reservation():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    request = req("tool")
    request.session_id = "s"
    request.session_generation = 1
    assert runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE, invocation_id="tool",
              context_id="ctx-tool", agent_definition_id="role",
              agent_instance_id="tool"),
        event(2, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_family": "shell"}),
    ))
    key = runtime.context_sessions["ctx-tool"]
    now = time.monotonic() * 1000
    runtime.tool_wait_hint = NativeToolWaitHint(
        key, 100.0, 300.0, 600.0, now, now + 5_000, "a" * 64, 2.0
    )
    step = ShadowBackupStep(key, 11, 4, 11, 4)
    controller = NS(
        mem_pool_host=NS(entry_map={"kv": NS(host_pool=NS(size_per_token=10))}),
        _num_tokens_by_pool=lambda _: {"kv": 2},
        _transfer_num_bytes=lambda _: 20,
    )

    def decline(**kwargs):
        assert kwargs["beliefkv_before_enqueue"](NS(
            beliefkv_command_id=kwargs["beliefkv_command_id"],
            node_ids=[11], device_indices=(0, 1), host_indices=(2, 3)
        ))
        return NS(issued=False, node_id=None)

    runtime.attach_native_cache(NS(
        cache_controller=controller, prepare_host_shadow=decline
    ))
    with patch.object(runtime, "capture_shadow_candidate", return_value=object()), patch(
        "beliefkv.runtime.sglang_v0520_runtime.next_shadow_backup_step",
        return_value=step,
    ):
        assert runtime.issue_shadow_backup_step(step) is None
    assert runtime.physical_ledger.pending_count == 0
    assert runtime.counts["shadow_native_declined"] == 1


def test_native_prefetch_issues_one_node_but_credits_only_h2d_ack():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    request = req("tool")
    request.session_id = "session-tool"
    request.session_generation = 2
    assert runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="tool", context_id="ctx-tool",
              agent_definition_id="role", agent_instance_id="tool"),
        event(2, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_family": "shell"}),
    ))
    key = runtime.context_sessions["ctx-tool"]
    now = time.monotonic() * 1000
    runtime.tool_wait_hint = NativeToolWaitHint(
        key, 100.0, 300.0, 600.0, now, now + 5_000, "a" * 64, 2.0
    )
    step = PrefetchLoadStep(key, 11, 4, 11, 4)
    controller = NS(
        mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=5)),
        }),
        _num_tokens_by_pool=lambda _: {"kv": 2, "mamba": 1},
        _transfer_num_bytes=lambda _: 25,
    )

    def native_load(**kwargs):
        accepted = kwargs["beliefkv_before_enqueue"](NS(
            beliefkv_command_id=kwargs["beliefkv_command_id"],
            node_ids=[kwargs["node_id"]],
            device_indices=(0, 1), host_indices=(2, 3),
            pool_transfers=[NS(
                name="mamba", indices_from_pool=None,
                device_indices=(4,), host_indices=(5,),
            )],
        ))
        return NS(issued=accepted, node_id=11 if accepted else None)

    runtime.attach_native_cache(NS(
        cache_controller=controller, prefetch_gpu_session_node=native_load
    ))
    with patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=step):
        command_id = runtime.issue_prefetch_gpu_step(step)
        assert command_id is not None
        assert runtime.physical_ledger.pending_count == 1
        assert not runtime.completed_physical_actions
        runtime.on_native_transfer_commit(NS(
            direction="h2d", status="completed", node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            child_commits=(NS(
                command_id=command_id, anchor_node_id=11,
                published_node_ids=(11,),
                num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
                num_bytes=25,
            ),),
        ))
        assert runtime.completed_physical_actions[0].action == "PREFETCH_GPU"
        assert runtime.physical_ledger.pending_count == 0
        controller._transfer_num_bytes = lambda _: 1
        assert runtime.issue_prefetch_gpu_step(step) is None
        assert runtime.physical_ledger.pending_count == 0
        assert runtime.counts["prefetch_reservation_rejected"] == 1


def test_tool_wait_hint_expiry_clears_read_only_candidate():
    runtime = NativeAdmissionRuntime()
    runtime.tool_wait_hint = NativeToolWaitHint(
        PrefillCandidateKey("tool", "wf", "tool", "ctx-tool", 0, 0),
        100.0, 300.0, 600.0,
        0.0, 1.0, "a" * 64,
    )
    runtime.shadow_candidate = object()
    runtime.scheduler_step()
    assert runtime.tool_wait_hint is None
    assert runtime.shadow_candidate is None
    assert runtime.counts["tool_wait_expired"] == 1


def test_ready_admission_prefetch_waits_for_ack_before_native_prefill():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    runtime.enable_admission_prefetch = True
    request = req("ready")
    request.session_id = "session-ready"
    request.session_generation = 3
    assert runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="ready", context_id="ctx-ready",
              agent_definition_id="role", agent_instance_id="ready"),
    ))
    key = runtime.context_sessions["ctx-ready"]
    now = time.monotonic() * 1000
    runtime.demand_hints["ready"] = NativeDemandHint(
        key, 12, now, now + 5000, "a" * 64, 1.0
    )
    runtime.attach_native_cache(object())
    runtime.physical_ledger.is_pending = lambda command_id: command_id == "h2d-1"
    step = PrefetchLoadStep(key, 11, 4, 11, 4)
    issued = []
    with patch.object(runtime, "capture_shadow_candidate", return_value=object()):
        with patch("beliefkv.runtime.sglang_v0520_runtime.next_prefetch_gpu_step",
                   side_effect=(step, None)) as select_step:
            with patch.object(runtime, "issue_prefetch_gpu_step",
                              side_effect=lambda *a, **kw: issued.append(kw) or "h2d-1"):
                assert runtime.defer_prefill_for_prefetch(request)
                assert issued == [{"source": "admission"}]
                assert runtime.defer_prefill_for_prefetch(request)
                assert select_step.call_count == 1
                runtime.completed_physical_actions.append(
                    NS(command_id="h2d-1", action="PREFETCH_GPU")
                )
                assert not runtime.defer_prefill_for_prefetch(request)
    assert runtime._admission_lease is None
    assert runtime.counts["admission_prefetch_acked"] == 1


def test_admission_prefetch_cannot_block_native_without_fresh_identity_or_hint():
    runtime = NativeAdmissionRuntime()
    runtime.enable_admission_prefetch = True
    tagged, plain = req("tagged"), req("plain", tagged=False)
    assert not runtime.defer_prefill_for_prefetch(plain)
    assert not runtime.defer_prefill_for_prefetch(tagged)
    tagged.session_id, tagged.session_generation = "s", 1
    runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="tagged", context_id="ctx-tagged",
              agent_definition_id="role", agent_instance_id="tagged"),
    ))
    assert not runtime.defer_prefill_for_prefetch(tagged)
    runtime.predictor_sha256 = "a" * 64
    key = runtime.context_sessions["ctx-tagged"]
    now = time.monotonic() * 1000
    runtime.demand_hints["tagged"] = NativeDemandHint(
        key, 12, now, now + 5000, "a" * 64, 1.0
    )
    runtime.attach_native_cache(object())
    with patch.object(runtime, "capture_shadow_candidate", return_value=None):
        assert not runtime.defer_prefill_for_prefetch(tagged)
    assert runtime._admission_lease is None
    tagged.session_generation = 2
    assert not runtime.defer_prefill_for_prefetch(tagged)


def test_admission_prefetch_requires_action_eligible_predictor():
    with pytest.raises(ValueError, match="pinned, live action predictor"):
        NativeAdmissionRuntime(enable_admission_prefetch=True)


def test_join_wait_prediction_tracks_child_revisions_and_expires_on_return():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    parent, child = req("parent"), req("child")
    parent.session_id, parent.session_generation = "session-parent", 2
    runtime.register_visible_request(parent)
    runtime.register_visible_request(child)
    submitted = []
    pending = []
    runtime._model_worker = NS(
        disabled=False, poll=lambda: tuple(pending),
        submit_join_wait=lambda tasks: submitted.extend(tasks),
        fileno=lambda: 72,
    )
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    assert len(submitted) == 1
    key, revision, join_id, mode, members, completed, children = submitted[0]
    assert (key.request_id, revision, join_id, mode, members) == (
        "parent", 4.0, "join", "all", ("child",),
    )
    assert children[0][0] == "child"
    assert completed == ()
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=0.01):
        runtime._submit_join_wait((event(4, RuntimeEventKind.JOIN_WAIT,
                                         invocation_id="parent", join_id="join"),))
    child_features = submitted[-1][-1][0][1]
    assert child_features.invocation_elapsed_ms == 8.0
    assert child_features.state_elapsed_ms == 8.0
    now = time.monotonic() * 1000
    pending.append(NativeJoinWaitHint(
        key, "join", "all", ("child",), (("child", 2.0, "ready", 0),),
        100.0, 250.0, 400.0, now, now + 5000, "a" * 64, revision,
    ))
    runtime.scheduler_step()
    assert runtime.join_wait_hint is pending[0]
    runtime.enable_admission_prefetch = True
    runtime.attach_native_cache(object())
    step = PrefetchLoadStep(key, 11, 4, 11, 4)
    with patch.object(runtime, "capture_shadow_candidate", return_value=object()):
        with patch("beliefkv.runtime.sglang_v0520_runtime.next_prefetch_gpu_step",
                   return_value=step):
            assert runtime.refreshed_prefetch_gpu_step(source="join_wait") == step
    runtime.on_events((event(
        5, RuntimeEventKind.RETURN, invocation_id="child",
    ),))
    assert runtime.join_wait_hint is None
    assert runtime.refreshed_prefetch_gpu_step(source="join_wait") is None


def test_join_prefetch_three_stages_and_confirmed_parent_ticket():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    runtime.enable_admission_prefetch = True
    parent, child = req("parent"), req("child")
    parent.session_id, parent.session_generation = "session-parent", 2
    runtime.register_visible_request(parent)
    runtime.register_visible_request(child)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    key = runtime.context_sessions["ctx-parent"]
    now = time.monotonic() * 1000
    hint = NativeJoinWaitHint(
        key, "join", "all", ("child",), (("child", 2.0, "ready", 0),),
        3_000.0, 4_000.0, 5_000.0, now, now + 5_000, "a" * 64, 4.0,
    )
    runtime._model_worker = NS(
        disabled=False, poll=lambda: (hint,), fileno=lambda: 72,
    )
    runtime.scheduler_step()
    runtime._model_worker = None
    runtime.attach_native_cache(object())
    step = PrefetchLoadStep(key, 11, 4, 11, 4)
    with patch.object(runtime, "capture_shadow_candidate", return_value=object()):
        with patch("beliefkv.runtime.sglang_v0520_runtime.next_prefetch_gpu_step",
                   return_value=step):
            issued = []
            with patch.object(runtime, "issue_prefetch_gpu_step",
                              side_effect=lambda *args, **kwargs:
                              issued.append(kwargs["source"]) or "h2d-1"):
                runtime.dispatch_join_prefetch()
                assert issued == []  # Long-horizon prediction is not a dispatch.
                runtime.on_events((event(
                    5, RuntimeEventKind.STRUCTURED_ACTION,
                    invocation_id="child", context_id="ctx-child",
                    context_epoch=0, join_id="join",
                    attributes={
                        "beliefkv_child_completion_intent": True,
                        "structured_action_names": ["ChildCompletion"],
                        "request_id": "child-llm",
                    },
                ),))
                assert runtime.graph.invocations["parent"].state.value == "wait_join"
                runtime.dispatch_join_prefetch()
                assert issued == ["join_ticket"]
                runtime.physical_ledger.is_pending = lambda command: True
                runtime.dispatch_join_prefetch()
                assert len(issued) == 1
                runtime.on_events((event(
                    6, RuntimeEventKind.RETURN, invocation_id="child",
                ),))
                assert runtime.graph.invocations["parent"].state.value == "ready"
                assert runtime._join_ticket.phase == "confirmed"
                assert runtime.refreshed_prefetch_gpu_step(source="join_ticket") == step
                runtime.dispatch_join_prefetch()
                assert len(issued) == 1  # ACK must arrive before another node.
                runtime.physical_ledger.is_pending = lambda command: False
                runtime.completed_physical_actions.append(
                    NS(command_id="h2d-1", action="PREFETCH_GPU")
                )
                runtime.dispatch_join_prefetch()
                assert issued == ["join_ticket", "join_ticket"]
    assert runtime.counts["join_prefetch_provisional_issued"] == 1
    assert runtime.counts["join_prefetch_confirmed_issued"] == 1
    runtime.on_events((event(
        7, RuntimeEventKind.CONTEXT_ADVANCE,
        invocation_id="parent", context_id="ctx-parent", context_epoch=1,
    ),))
    assert runtime._join_ticket is None


def test_join_prefetch_all_requires_last_child_and_rejects_false_intent():
    runtime = NativeAdmissionRuntime()
    runtime.enable_admission_prefetch = True
    parent = req("parent")
    parent.session_id, parent.session_generation = "s", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="a", context_id="ctx-a",
              agent_definition_id="a", agent_instance_id="a"),
        event(3, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="b", context_id="ctx-b",
              agent_definition_id="b", agent_instance_id="b"),
        event(4, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("a", "b")),
        event(5, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    def intent(seq, child):
        return event(
            seq, RuntimeEventKind.STRUCTURED_ACTION,
            invocation_id=child, context_id=f"ctx-{child}",
            context_epoch=0, join_id="join",
            attributes={
                "beliefkv_child_completion_intent": True,
                "structured_action_names": ["ChildCompletion"],
                "request_id": f"req-{child}",
            },
        )
    runtime.on_events((intent(6, "a"),))
    assert runtime._join_ticket is None
    assert runtime.counts["join_intent_stale"] == 1
    runtime.on_events((event(7, RuntimeEventKind.RETURN, invocation_id="a"),))
    runtime.on_events((intent(8, "b"),))
    assert runtime._join_ticket.phase == "provisional"
    runtime.on_events((event(
        9, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="b",
    ),))
    assert runtime._join_ticket is None


def test_read_only_completion_forecast_requires_live_child_and_fresh_delivery():
    model = CompletionLead(145, 186, 246, 428)
    runtime = NativeAdmissionRuntime(completion_lead=model)
    parent = req("parent")
    parent.session_id, parent.session_generation = "session-parent", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    now = time.monotonic() * 1000
    intent = event(
        5, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child", context_id="ctx-child",
        context_epoch=0, join_id="join",
        attributes={
            "beliefkv_child_completion_intent": True,
            "structured_action_names": [],
            "child_completion_signal_kind": "natural_final",
            "request_id": "child-request",
        },
    )
    runtime.on_events((replace(intent, ts_ms=now),))
    forecast = runtime.read_only_join_completion_forecast("join")
    assert forecast is not None
    assert 0 <= forecast[0] <= forecast[1] <= forecast[2] <= 246
    assert runtime.counts["join_completion_forecast_accepted"] == 1
    assert runtime.physical_ledger.pending_count == 0
    runtime.on_events((replace(
        event(6, RuntimeEventKind.RETURN, invocation_id="child"),
        ts_ms=now + 1,
    ),))
    assert runtime.read_only_join_completion_forecast("join") is None


def test_late_completion_intent_never_makes_short_forecast():
    runtime = NativeAdmissionRuntime(
        completion_lead=CompletionLead(145, 186, 246, 428)
    )
    parent = req("parent")
    parent.session_id, parent.session_generation = "session-parent", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    runtime.on_events((event(
        5, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child", context_id="ctx-child",
        context_epoch=0, join_id="join",
        attributes={
            "beliefkv_child_completion_intent": True,
            "structured_action_names": ["ChildCompletion"],
            "request_id": "child-request",
        },
    ),))
    assert runtime.counts["join_completion_forecast_too_late"] == 1
    assert runtime.read_only_join_completion_forecast("join") is None


def test_completion_lead_environment_must_be_pinned_and_read_only(
    tmp_path, monkeypatch
):
    path = tmp_path / "completion.json"
    path.write_text(json.dumps({
        "status": "offline_conditional_signal_diagnostic_only",
        "model": CompletionLead(145, 186, 246, 428).to_dict(),
    }), encoding="utf-8")
    monkeypatch.setenv("BELIEFKV_COMPLETION_LEAD_ARTIFACT", str(path))
    with pytest.raises(ValueError, match="both artifact and SHA-256"):
        NativeAdmissionRuntime()
    monkeypatch.setenv(
        "BELIEFKV_COMPLETION_LEAD_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    runtime = NativeAdmissionRuntime()
    assert runtime.completion_lead == CompletionLead(145, 186, 246, 428)
    assert runtime.enable_admission_prefetch is False
    with pytest.raises(ValueError, match="two sources"):
        NativeAdmissionRuntime(completion_lead=CompletionLead(1, 2, 3, 4))


def test_natural_final_join_signal_stays_bound_and_survives_late_model_hint():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    runtime.enable_admission_prefetch = True
    parent = req("parent")
    parent.session_id, parent.session_generation = "s", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="a", context_id="ctx-a",
              agent_definition_id="a", agent_instance_id="a"),
        event(3, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="b", context_id="ctx-b",
              agent_definition_id="b", agent_instance_id="b"),
        event(4, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("a", "b")),
        event(5, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))

    def natural(seq, child="b", *, names=()):
        return event(
            seq, RuntimeEventKind.STRUCTURED_ACTION,
            invocation_id=child, context_id=f"ctx-{child}",
            context_epoch=0, join_id="join",
            attributes={
                "beliefkv_child_completion_intent": True,
                "child_completion_signal_kind": "natural_final",
                "structured_action_names": list(names),
                "request_id": f"req-{child}",
            },
        )

    runtime.on_events((natural(6, "a"),))
    assert runtime._join_ticket is None
    runtime.on_events((event(7, RuntimeEventKind.RETURN, invocation_id="a"),))
    runtime.on_events((natural(8, names=("ChildCompletion",)),))
    assert runtime._join_ticket is None
    runtime.on_events((natural(9),))
    assert runtime._join_ticket.phase == "provisional"
    assert runtime.counts["join_intent_natural_final_accepted"] == 1
    key = runtime.context_sessions["ctx-parent"]
    child = runtime.graph.invocations["b"]
    parent_invocation = runtime.graph.invocations["parent"]
    now_ms = time.monotonic() * 1000
    hint = NativeJoinWaitHint(
        key, "join", "all", ("a", "b"), (
            ("b", child.updated_ts_ms, child.state.value, 0),
        ), 30_000.0, 40_000.0, 50_000.0,
        now_ms, now_ms + 5_000,
        "a" * 64, parent_invocation.updated_ts_ms,
    )
    runtime._model_worker = NS(
        disabled=False, poll=lambda: (hint,), fileno=lambda: 72,
    )
    runtime.scheduler_step()
    runtime._model_worker = None
    assert runtime._join_ticket.phase == "provisional"
    assert runtime.counts["join_wait_ticket_preserved"] == 1
    runtime.on_events((event(
        10, RuntimeEventKind.INVOCATION_CANCEL, invocation_id="b",
    ),))
    assert runtime._join_ticket is None


def test_fast_join_return_builds_confirmed_ticket_without_model_hint():
    runtime = NativeAdmissionRuntime()
    runtime.enable_admission_prefetch = True
    parent = req("parent")
    parent.session_id, parent.session_generation = "s", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    assert runtime._join_ticket is None
    runtime.on_events((event(5, RuntimeEventKind.RETURN, invocation_id="child"),))
    assert runtime._join_ticket.phase == "confirmed"
    assert runtime.counts["join_reentry_confirmed"] == 1
    runtime.on_events((event(
        6, RuntimeEventKind.JOIN_SATISFIED, join_id="join",
    ),))
    assert runtime.counts["join_reentry_confirmed"] == 1
    key = runtime.context_sessions["ctx-parent"]
    runtime.visible.pop("parent")
    runtime.register_physical_action(PhysicalActionExpectation(
        "join-h2d", "PREFETCH_GPU", "ctx-parent", 0,
        (PhysicalChildExpectation(11, (11,), (("kv", 10),), 10),),
        (("kv", 10),), session_id="s", session_generation=1,
    ))
    assert runtime.physical_ledger.pending_count == 1
    revision = runtime.semantic_revision
    runtime.on_events((event(
        7, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child", context_id="ctx-child",
        context_epoch=0, join_id="join",
        attributes={
            "beliefkv_child_completion_intent": True,
            "structured_action_names": ["ChildCompletion"],
            "request_id": "late",
        },
    ),))
    assert runtime.semantic_revision == revision
    assert runtime.counts["join_intent_stale"] == 1


def test_join_probabilistic_prefetch_requires_live_near_term_hint():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    runtime.enable_admission_prefetch = True
    parent = req("parent")
    parent.session_id, parent.session_generation = "s", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="parent", context_id="ctx-parent",
              agent_definition_id="parent", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="child", context_id="ctx-child",
              agent_definition_id="child", agent_instance_id="child"),
        event(3, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    key = runtime.context_sessions["ctx-parent"]
    now = time.monotonic() * 1000
    hint = NativeJoinWaitHint(
        key, "join", "all", ("child",), (("child", 2.0, "ready", 0),),
        500.0, 900.0, 1_500.0, now, now + 5_000, "a" * 64, 4.0,
    )
    runtime._model_worker = NS(disabled=False, poll=lambda: (hint,))
    runtime.scheduler_step()
    runtime._model_worker = None
    runtime.attach_native_cache(object())
    step = PrefetchLoadStep(key, 11, 4, 11, 4)
    with patch.object(runtime, "capture_shadow_candidate", return_value=object()):
        with patch("beliefkv.runtime.sglang_v0520_runtime.next_prefetch_gpu_step",
                   return_value=step):
            with patch.object(runtime, "issue_prefetch_gpu_step",
                              return_value="probabilistic-h2d") as issue:
                runtime.dispatch_join_prefetch()
                issue.assert_called_once_with(step, source="join_ticket")
    assert runtime.counts["join_prefetch_probabilistic_issued"] == 1
    runtime.on_events((event(5, RuntimeEventKind.RETURN, invocation_id="child"),))
    assert runtime.join_wait_hint is None
    assert runtime._join_ticket.phase == "confirmed"


def test_tool_wait_candidate_is_discarded_on_context_replacement_and_requeue():
    runtime = NativeAdmissionRuntime()
    original = req("old")
    original.beliefkv_metadata["context_id"] = "ctx-shared"
    original.session_id = "session"
    original.session_generation = 1
    assert runtime.register_visible_request(original)
    old_key = runtime.context_sessions["ctx-shared"]
    runtime.tool_wait_hint = NativeToolWaitHint(
        old_key, 100.0, 300.0, 600.0, 0.0, 1.0, "a" * 64,
    )
    runtime.shadow_candidate = object()
    runtime._context_tokens["ctx-shared"] = (0, 3, 2, False)

    successor = req("new")
    successor.beliefkv_metadata["context_id"] = "ctx-shared"
    successor.session_id = "session"
    successor.session_generation = 2
    assert runtime.register_visible_request(successor)
    assert runtime.tool_wait_hint is None
    assert runtime.shadow_candidate is None
    assert "ctx-shared" not in runtime._context_tokens

    runtime.tool_wait_hint = NativeToolWaitHint(
        runtime.context_sessions["ctx-shared"],
        100.0, 300.0, 600.0, 0.0, 1.0, "a" * 64,
    )
    runtime.shadow_candidate = object()
    successor.session_generation = 3
    runtime.on_requests_requeued((successor,), is_retracted=True)
    assert runtime.tool_wait_hint is None
    assert runtime.shadow_candidate is None


def req(name: str, *, tagged: bool = True):
    return NS(
        rid=name,
        beliefkv_metadata=(
            {
                "root_workflow_id": "wf",
                "invocation_id": name,
                "context_id": f"ctx-{name}",
                "context_epoch": 0,
            }
            if tagged
            else None
        ),
        cache_request_handle=NS(attempt_id=0),
        session_id=None,
        session_generation=None,
        finished=lambda: False,
    )


def event(seq: int, kind: RuntimeEventKind, **kwargs):
    return RuntimeEvent(
        event_id=f"e{seq}", ts_ms=float(seq), kind=kind, workflow_id="wf", **kwargs
    )


def select(runtime, native):
    plan = runtime.plan_native_prefill(native, running_batch=None, adder=None)
    return select_native_prefill_candidates(
        native, plan=plan, current_semantic_revision=runtime.semantic_revision
    )


def test_native_fallback_and_causal_join_straggler_ranking():
    runtime = NativeAdmissionRuntime()
    a, plain, b = req("a"), req("plain", tagged=False), req("b")
    assert runtime.register_visible_request(a)
    assert runtime.register_visible_request(b)
    assert select(runtime, [a, plain, b]).candidates == (a, plain, b)
    runtime.on_events(
        (
            event(0, RuntimeEventKind.WORKFLOW_START),
            event(
                1, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="a", context_id="ctx-a",
                agent_definition_id="a", agent_instance_id="a",
            ),
            event(
                2, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="b", context_id="ctx-b",
                agent_definition_id="b", agent_instance_id="b",
            ),
            event(
                3, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="waiter", context_id="ctx-waiter",
                agent_definition_id="waiter", agent_instance_id="waiter",
            ),
            event(
                4, RuntimeEventKind.JOIN_CREATE,
                join_id="join", member_invocation_ids=("b",),
            ),
            event(
                5, RuntimeEventKind.JOIN_WAIT,
                invocation_id="waiter", join_id="join",
            ),
        )
    )
    assert select(runtime, [a, plain, b]).candidates == (b, plain, a)
    assert runtime.visible == {"a": runtime.visible["a"], "b": runtime.visible["b"]}
    assert select(runtime, [a, plain, b]).rejected == ()


def test_stale_attempt_and_session_fail_closed_without_blocking_native():
    runtime = NativeAdmissionRuntime()
    tagged, plain = req("tagged"), req("plain", tagged=False)
    runtime.register_visible_request(tagged)
    plan = runtime.plan_native_prefill([tagged, plain], running_batch=None, adder=None)
    tagged.cache_request_handle.attempt_id += 1
    result = select_native_prefill_candidates(
        [tagged, plain], plan=plan,
        current_semantic_revision=runtime.semantic_revision,
    )
    assert result.candidates == (plain,)
    assert result.rejected == (("tagged", "identity_changed"),)
    runtime.on_requests_requeued([tagged], is_retracted=True)
    assert select(runtime, [tagged, plain]).candidates == (tagged, plain)
    tagged.session_id = "s1"
    tagged.session_generation = 1
    runtime.on_requests_requeued([tagged], is_retracted=True)
    plan = runtime.plan_native_prefill([tagged], running_batch=None, adder=None)
    tagged.session_generation = 2
    result = select_native_prefill_candidates(
        [tagged], plan=plan,
        current_semantic_revision=runtime.semantic_revision,
    )
    assert result.rejected == (("tagged", "identity_changed"),)


def test_fresh_plan_after_abort_and_completion_does_not_authorize_old_request():
    runtime = NativeAdmissionRuntime()
    first, second = req("first"), req("second")
    runtime.register_visible_request(first)
    runtime.register_visible_request(second)
    revision = runtime.semantic_revision
    runtime.on_abort_request(NS(rid="first", abort_all=False))
    assert runtime.semantic_revision > revision
    assert select(runtime, [first, second]).candidates == (second,)
    assert select(runtime, [first, second]).rejected == (
        ("first", "no_authorization"),
    )
    # Queue ownership stays with native SGLang; abort removes the native
    # waiting request separately. A ghost native request must not be admitted.
    second.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=[second]))
    assert "second" not in runtime.visible


def test_bounded_or_invalid_tagged_candidates_cannot_starve_untagged():
    runtime = NativeAdmissionRuntime()
    invalid, plain = req("invalid"), req("plain", tagged=False)
    invalid.beliefkv_metadata["context_epoch"] = True
    assert runtime.register_visible_request(invalid) is False
    assert select(runtime, [invalid, plain]).candidates == (plain,)
    many = [req(f"r{index}") for index in range(512)]
    for item in many:
        runtime.register_visible_request(item)
    beyond = req("beyond")
    runtime.register_visible_request(beyond)
    selection = select(runtime, many + [plain, beyond])
    assert selection.candidates == (*many, plain)
    assert selection.rejected == (("beyond", "no_authorization"),)


def test_partial_event_failure_discards_mirror_and_restores_native_order():
    runtime = NativeAdmissionRuntime()
    a, b = req("a"), req("b")
    runtime.register_visible_request(a)
    runtime.register_visible_request(b)
    with pytest.raises(Exception):
        runtime.on_events(
            (
                event(0, RuntimeEventKind.WORKFLOW_START),
                event(1, RuntimeEventKind.RETURN, invocation_id="missing"),
            )
        )
    assert runtime.graph.invocations == {}
    assert runtime.counts["causal_mirror_discarded"] == 1
    assert select(runtime, [a, b]).candidates == (a, b)


def test_terminal_workflow_is_removed_before_native_prefill():
    runtime = NativeAdmissionRuntime()
    tagged, plain = req("tagged"), req("plain", tagged=False)
    runtime.register_visible_request(tagged)
    runtime.on_events(
        (
            event(0, RuntimeEventKind.WORKFLOW_START),
            event(1, RuntimeEventKind.WORKFLOW_END),
        )
    )
    assert runtime.terminal_waiting_request_ids([tagged, plain]) == ("tagged",)
    assert select(runtime, [tagged, plain]).candidates == (plain,)
    runtime.retire_terminal_request("tagged")
    assert "tagged" not in runtime.visible


def test_scheduler_step_drains_causal_socket_and_close_releases_path(tmp_path):
    path = tmp_path / "causal.sock"
    runtime = NativeAdmissionRuntime(event_socket_path=str(path))
    message = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "message_id": "one",
            "events": [event(0, RuntimeEventKind.WORKFLOW_START).to_dict()],
        }
    ).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
        sender.sendto(message, str(path))
    runtime.scheduler_step()
    assert "wf" in runtime.graph.workflows
    assert runtime.semantic_revision == 1
    runtime.close()
    assert not path.exists()
