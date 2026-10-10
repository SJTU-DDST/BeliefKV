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
    ActionLocalShadowCandidate,
    ContextSessionAnchors,
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PhysicalReceiptError,
    PrefetchLoadStep,
    SessionH2DOpportunity,
    ShadowBackupStep,
)
from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
from beliefkv.runtime.semantic_report_worker import (
    SEMANTIC_TEXT, SemanticReportInput, SemanticReportReply,
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
    completed = runtime.on_native_transfer_commit(NS(
        direction="d2h", status="completed", node_ids=(11,),
        num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
        child_commits=(NS(
            command_id="prepare-a", anchor_node_id=11,
            published_node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            num_bytes=25,
        ),),
    ))
    assert [action.command_id for action in completed] == ["prepare-a"]
    assert list(runtime.completed_physical_actions) == list(completed)
    assert runtime.counts["native_physical_completed"] == 1
    assert runtime.physical_ledger.pending_count == 0
    replay = runtime.on_native_transfer_commit(NS(
        direction="d2h", status="completed", node_ids=(11,),
        num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
        child_commits=(NS(
            command_id="prepare-a", anchor_node_id=11,
            published_node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            num_bytes=25,
        ),),
    ))
    assert replay == ()
    assert runtime.physical_disabled
    assert runtime.counts["physical_receipt_failed"] == 1
    with pytest.raises(PhysicalReceiptError, match="no live causal context"):
        runtime.register_physical_action(expectation)


def test_h2d_ack_bridge_uses_native_generation_not_a_stale_context_binding():
    runtime = NativeAdmissionRuntime()
    tagged = req("a")
    tagged.session_id, tagged.session_generation = "session", 4
    runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="a", context_id="ctx-a"),
    ))
    expected = PhysicalActionExpectation(
        "h2d", "PREFETCH_GPU", "ctx-a", 0,
        (PhysicalChildExpectation(11, (11,), (("kv", 20),), 20),),
        (("kv", 10),), session_id="session", session_generation=4,
    )
    runtime.register_physical_action(expected)
    runtime.attach_native_cache(NS(session_refs=NS(
        snapshot_session_leaf_anchors=lambda session, generation, max_leaves:
        ((0, ((11, 1),)), (2, ((11, 1),)))
        if (session, generation) == ("session", 4) else None,
    )))
    runtime.on_events((event(
        2, RuntimeEventKind.LLM_SUBMIT, invocation_id="a", context_id="ctx-a",
        context_epoch=1, attributes={"request_id": "next"},
    ),))
    assert "ctx-a" not in runtime.context_sessions
    actions = runtime.on_native_transfer_commit(NS(
        direction="h2d", status="completed", node_ids=(11,),
        num_tokens_by_pool=(("kv", 2),),
        child_commits=(NS(
            command_id="h2d", anchor_node_id=11, published_node_ids=(11,),
            num_tokens_by_pool=(("kv", 2),), num_bytes=20,
        ),),
    ))
    assert len(actions) == 1
    assert actions[0].context_epoch == 0
    assert not runtime.physical_disabled


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


def test_session_anchors_normalize_native_float64_for_physical_step():
    import numpy as np

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
    cache = NS(session_refs=NS(
        snapshot_session_leaf_anchors=lambda *args, **kwargs: (
            (0, ((11, np.float64(25)),)),
            (2, ((11, np.float64(25)),)),
        )
    ))
    anchors = runtime.snapshot_session_anchors(
        cache, context_id="ctx-a", context_epoch=0
    )
    assert anchors is not None
    assert anchors.component_leaves == (
        (0, ((11, 25.0),)), (2, ((11, 25.0),))
    )
    assert type(anchors.component_leaves[0][1][0][1]) is float


def test_finished_session_records_input_restore_limit_without_old_model_worker():
    runtime = NativeAdmissionRuntime()
    tagged = req("a")
    tagged.session_id, tagged.session_generation = "session-a", 4
    tagged.origin_input_ids, tagged.output_ids = list(range(8)), [80, 81, 82]
    runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="a", context_id="ctx-a"),
    ))
    tagged.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=(tagged,)))
    cache = NS(session_refs=NS(
        snapshot_session_leaf_anchors=lambda *args, **kwargs: (
            (0, ((11, 25),)), (2, ((11, 25),)),
        ),
    ))
    assert runtime._model_worker is None
    anchors = runtime.snapshot_session_anchors(cache, context_id="ctx-a", context_epoch=0)
    assert anchors.reusable_input_tokens == 7


def test_context_opportunity_requires_live_wait_or_ready_session_epoch():
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
    cache = NS(session_refs=NS(
        snapshot_session_leaf_anchors=lambda session, generation, max_leaves: (
            ((0, ((11, 25),)), (2, ((11, 25),)))
            if (session, generation, max_leaves) == ("session-a", 4, 8)
            else None
        ),
    ))
    runtime.attach_native_cache(cache)
    sentinel = object()
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=sentinel,
    ) as inspect:
        assert runtime.inspect_context_h2d_opportunity(
            context_id="ctx-a", context_epoch=0,
        ) is sentinel
        assert isinstance(inspect.call_args.args[1], ContextSessionAnchors)
        assert inspect.call_args.args[1].key.session_id == "session-a"
        assert runtime.inspect_context_h2d_opportunity(
            context_id="ctx-a", context_epoch=1,
        ) is None
        runtime.on_events((
            event(3, RuntimeEventKind.CONTEXT_ADVANCE,
                  invocation_id="a", context_id="ctx-a", context_epoch=1),
        ))
        assert runtime.inspect_context_h2d_opportunity(
            context_id="ctx-a", context_epoch=0,
        ) is None
        assert inspect.call_count == 1


def test_safe_point_persists_bounded_wait_and_admission_opportunities(tmp_path):
    runtime = NativeAdmissionRuntime(opportunity_dir=str(tmp_path))
    waiting, tool = req("waiting"), req("tool")
    for request in (waiting, tool):
        request.session_id = f"session-{request.rid}"
        request.session_generation = 2
        assert runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="waiting", context_id="ctx-waiting"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="tool", context_id="ctx-tool"),
        event(3, RuntimeEventKind.TOOL_START,
              invocation_id="tool", context_id="ctx-tool"),
    ))
    runtime.attach_native_cache(object())
    key = runtime.context_sessions["ctx-waiting"]
    headroom = StaticPoolHeadroomObservation(
        True, device_full_free_tokens=100, device_mamba_free_slots=4,
        host_full_free_tokens=50, host_mamba_free_slots=2,
    )
    opportunity = SessionH2DOpportunity(
        ContextSessionAnchors(key, (), 1.0), headroom,
        PrefetchLoadStep(key, 11, 2.5, 11, 2.5), 20, 1, True,
        host_backed_full_missing_device_tokens=20,
        host_backed_mamba_missing_device_nodes=1,
        unbacked_full_nodes=0,
        unbacked_mamba_leaves=0,
        blocked_detail="parent_full_not_device",
    )
    with patch.object(runtime, "inspect_context_h2d_opportunity",
                      side_effect=lambda *, context_id, context_epoch,
                      admission_candidate=False:
                      opportunity if context_id == "ctx-waiting" else None) as inspect:
        runtime.scheduler_step(waiting_queue=[waiting])
        runtime.scheduler_step(waiting_queue=[waiting])
        assert inspect.call_count == 2  # 1-second sampling interval
    assert runtime.physical_ledger.pending_count == 0
    runtime.close()
    rows = [
        json.loads(line) for line in
        (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()
    ]
    states = [row for row in rows if row["event"] == "admission_runtime_state"]
    assert states[-1]["final"] is True
    rows = [row for row in rows if row["event"] != "admission_runtime_state"]
    assert len(rows) == 3
    census = next(row for row in rows if row["event"] == "safe_point_census")
    assert census["candidate_count"] == 2
    assert census["waiting_queue_size"] == 1
    assert census["sample_wall_ms"] >= 0
    by_source = {row["source"]: row for row in rows
                 if row["event"] == "session_h2d_opportunity"}
    assert by_source["admission_candidate"]["required_full_tokens"] == 20
    assert by_source["admission_candidate"]["host_backed_full_missing_device_tokens"] == 20
    assert by_source["admission_candidate"]["host_backed_mamba_missing_device_nodes"] == 1
    assert by_source["admission_candidate"]["unbacked_full_nodes"] == 0
    assert by_source["admission_candidate"]["blocked_detail"] == "parent_full_not_device"
    assert by_source["admission_candidate"]["fits_current_free_lists"] is True
    assert by_source["admission_candidate"]["session_generation"] == 2
    assert by_source["admission_candidate"]["node_id"] == 11
    assert by_source["admission_candidate"]["node_creation_time"] == 2.5
    assert by_source["admission_candidate"]["leaf_node_id"] == 11
    assert by_source["admission_candidate"]["leaf_creation_time"] == 2.5
    assert by_source["tool_wait"]["reason"] == "no_live_session_or_anchors"
    assert "node_creation_time" not in by_source["tool_wait"]
    assert json.loads((tmp_path / "admission_opportunities_status.json").read_text())[
        "complete"
    ] is True


@pytest.mark.parametrize("source", ("tool_wait", "join_wait"))
@pytest.mark.parametrize("write_back", (False, True))
def test_wait_prepare_probe_requires_native_prerequisites_and_host_space(
    tmp_path, source, write_back,
):
    runtime = NativeAdmissionRuntime(opportunity_dir=str(tmp_path))
    request = req("tool")
    request.session_id = "session-tool"
    request.session_generation = 1
    assert runtime.register_visible_request(request)
    events = [
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="tool", context_id="ctx-tool"),
    ]
    if source == "tool_wait":
        events.append(event(2, RuntimeEventKind.TOOL_START,
                            invocation_id="tool", context_id="ctx-tool"))
    else:
        events.extend((
            event(2, RuntimeEventKind.INVOCATION_CREATE,
                  invocation_id="child", context_id="ctx-child"),
            event(3, RuntimeEventKind.JOIN_CREATE,
                  join_id="join", member_invocation_ids=("child",)),
            event(4, RuntimeEventKind.JOIN_WAIT,
                  invocation_id="tool", join_id="join"),
        ))
    runtime.on_events(tuple(events))
    cache = NS(
        enable_session_radix_cache=True,
        cache_controller=NS(write_policy=(
            "write_back" if write_back else
            "write_through" if source == "tool_wait" else "write_through_selective"
        )),
        tree_core=NS(is_write_back=write_back),
    )
    runtime.attach_native_cache(cache)
    key = runtime.context_sessions["ctx-tool"]
    anchors = ContextSessionAnchors(key, ((0, ((11, 1),)), (2, ((11, 1),))), 1.0)
    node = NS(
        node_id=11, full_device_tokens=20, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
    )
    candidate = ActionLocalShadowCandidate(anchors, (node,), 20, 1)
    step = ShadowBackupStep(key, 11, 2.5, 11, 2.5)
    headroom = StaticPoolHeadroomObservation(
        True, device_full_free_tokens=200, device_mamba_free_slots=8,
        host_full_free_tokens=10, host_mamba_free_slots=2,
    )
    h2d = SessionH2DOpportunity(anchors, headroom, None, 0, 0, None)
    with (
        patch.object(runtime, "inspect_context_h2d_opportunity", return_value=h2d),
        patch.object(runtime, "capture_shadow_candidate", return_value=candidate),
        patch("beliefkv.runtime.sglang_v0520_runtime.next_shadow_backup_step",
              return_value=step),
    ):
        runtime.scheduler_step()
    runtime.close()
    rows = [
        json.loads(line) for line in
        (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()
    ]
    prepare = next(row for row in rows
                   if row["event"] == "session_h2d_opportunity")
    assert prepare["source"] == source
    assert prepare["prepare_node_id"] == 11
    assert prepare["prepare_node_creation_time"] == 2.5
    assert prepare["prepare_leaf_node_id"] == 11
    assert prepare["prepare_leaf_creation_time"] == 2.5
    assert prepare["prepare_required_full_tokens"] == 20
    assert prepare["prepare_required_mamba_slots"] == 0
    assert prepare["prepare_reason"] == "insufficient_host_free_lists"
    assert prepare["prepare_fits_current_host_free_lists"] is False


def test_write_back_prepare_probe_rejects_non_write_back_tree():
    runtime = NativeAdmissionRuntime()
    try:
        runtime.attach_native_cache(NS(
            enable_session_radix_cache=True,
            cache_controller=NS(write_policy="write_back"),
            tree_core=NS(is_write_back=False),
        ))
        row = {}
        runtime._observe_prepare_opportunity(object(), row, None)
        assert row["prepare_reason"] == "native_prepare_prerequisites_disabled"
    finally:
        runtime.close()


def test_safe_point_records_missing_session_and_bounded_queue_scan(tmp_path):
    runtime = NativeAdmissionRuntime(opportunity_dir=str(tmp_path))
    no_session = req("unbound")
    assert runtime.register_visible_request(no_session)
    runtime.scheduler_step(waiting_queue=[no_session] + [req("plain", tagged=False)] * 513)
    runtime.close()
    rows = [
        json.loads(line) for line in
        (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()
    ]
    census = next(row for row in rows if row["event"] == "safe_point_census")
    candidate = next(row for row in rows
                     if row["event"] == "session_h2d_opportunity")
    assert census["waiting_queue_size"] == 514
    assert census["waiting_scanned"] == 512
    assert candidate["reason"] == "no_bound_session"


def test_safe_point_distinguishes_missing_anchors_from_stale_context(tmp_path):
    runtime = NativeAdmissionRuntime(opportunity_dir=str(tmp_path))
    request = req("root")
    request.session_id = "session-root"
    request.session_generation = 3
    assert runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="root", context_id="ctx-root"),
        event(2, RuntimeEventKind.TOOL_START,
              invocation_id="root", context_id="ctx-root"),
    ))
    cache = NS(session_refs=NS(
        snapshot_session_leaf_anchors=lambda *args, **kwargs: None,
    ))
    runtime.attach_native_cache(cache)
    runtime.scheduler_step()
    runtime.close()
    rows = [
        json.loads(line) for line in
        (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()
    ]
    [candidate] = [
        row for row in rows if row["event"] == "session_h2d_opportunity"
    ]
    assert candidate["reason"] == "no_live_session_or_anchors"
    assert candidate["no_live_detail"] == "native_anchor_snapshot_rejected"
    key = runtime.context_sessions["ctx-root"]
    cache.session_refs.snapshot_session_leaf_anchors = (
        lambda *args, **kwargs: ((0, ()), (2, ()))
    )
    assert runtime._missing_opportunity_detail(
        key, admission_candidate=False,
    ) == "session_has_no_cached_leaves"
    cache.session_refs.snapshot_session_leaf_anchors = (
        lambda *args, **kwargs: ((0, ((11, 1),)), (2, ()))
    )
    assert runtime._missing_opportunity_detail(
        key, admission_candidate=False,
    ) == "anchor_snapshot_normalization_failed"
    runtime.graph.contexts["ctx-root"].epoch = 1
    assert runtime._missing_opportunity_detail(
        key, admission_candidate=False,
    ) == "context_epoch_changed"
    runtime.graph.contexts["ctx-root"].epoch = 0
    runtime.context_sessions["ctx-root"] = replace(key, context_epoch=1)
    assert runtime._missing_opportunity_detail(
        key, admission_candidate=False,
    ) == "context_binding_changed"


def test_safe_point_rotates_through_more_candidates_than_per_tick_limit(tmp_path):
    runtime = NativeAdmissionRuntime(opportunity_dir=str(tmp_path))
    requests = [req(str(index)) for index in range(20)]
    for request in requests:
        assert runtime.register_visible_request(request)
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               side_effect=(1.0, 2.1)):
        runtime.scheduler_step(waiting_queue=requests)
        runtime.scheduler_step(waiting_queue=requests)
    runtime.close()
    rows = [
        json.loads(line) for line in
        (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()
    ]
    seen = {row["request_id"] for row in rows
            if row["event"] == "session_h2d_opportunity"}
    assert len(seen) == 20
    assert [row["sampled"] for row in rows if row["event"] == "safe_point_census"] == [
        16, 16,
    ]


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
        _num_tokens_by_pool=lambda _: {"kv": 2},
        _transfer_num_bytes=lambda _: 20,
    )
    sent = []

    def native_shadow(**kwargs):
        assert kwargs["beliefkv_include_mamba"] is False
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
            num_tokens_by_pool=(("kv", 2),),
            child_commits=(NS(
                command_id=command_id, anchor_node_id=11,
                published_node_ids=(11,),
                num_tokens_by_pool=(("kv", 2),),
                num_bytes=20,
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


def test_submitted_waiting_request_can_predict_and_prefetch_before_admission():
    runtime = NativeAdmissionRuntime()
    runtime.predictor_sha256 = "a" * 64
    runtime.enable_admission_prefetch = True
    request = req("submitted")
    request.session_id = "session-submitted"
    request.session_generation = 3
    assert runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="submitted", context_id="ctx-submitted",
              agent_definition_id="role", agent_instance_id="submitted"),
        event(2, RuntimeEventKind.LLM_SUBMIT,
              invocation_id="submitted", context_id="ctx-submitted"),
    ))
    key = runtime.context_sessions["ctx-submitted"]
    tasks = []
    runtime._model_worker = NS(disabled=False, submit=lambda batch: tasks.extend(batch))
    runtime.plan_native_prefill([request], running_batch=None, adder=None)
    assert len(tasks) == 1
    assert tasks[0][0] == key
    assert tasks[0][1].state == "running_llm"

    cache = NS(session_refs=NS(
        snapshot_session_leaf_anchors=lambda session, generation, max_leaves:
        ((0, ((11, 4),)), (2, ((11, 4),))),
    ))
    runtime.attach_native_cache(cache)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=object(),
    ) as inspect:
        assert runtime.inspect_context_h2d_opportunity(
            context_id=key.context_id, context_epoch=0,
        ) is None
        assert runtime.inspect_context_h2d_opportunity(
            context_id=key.context_id, context_epoch=0,
            admission_candidate=True,
        ) is not None
        assert inspect.call_count == 1

    now = time.monotonic() * 1000
    runtime.demand_hints[request.rid] = NativeDemandHint(
        key, 12, now, now + 5000, "a" * 64,
        runtime.graph.invocations["submitted"].updated_ts_ms,
    )
    step = PrefetchLoadStep(key, 11, 4, 11, 4)
    with patch.object(runtime, "capture_shadow_candidate", return_value=object()):
        with patch("beliefkv.runtime.sglang_v0520_runtime.next_prefetch_gpu_step",
                   return_value=step):
            with patch.object(runtime, "issue_prefetch_gpu_step",
                              return_value="h2d-submitted") as issue:
                assert runtime.defer_prefill_for_prefetch(request)
                issue.assert_called_once_with(step, source="admission")
    runtime.physical_ledger.is_pending = lambda command_id: True
    runtime.on_events((
        event(3, RuntimeEventKind.LLM_RESULT,
              invocation_id="submitted", context_id="ctx-submitted",
              attributes={"terminal": True}),
    ))
    assert not runtime.defer_prefill_for_prefetch(request)
    assert runtime._admission_lease is None


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
    runtime.graph.invocations["child"].parent_invocation_id = "parent"
    runtime._tool_metadata["child"] = (
        "sandbox", "execute", "test_suite", None, "", 3100.0, 16,
    )
    child_features = runtime._local_frontier_features(
        runtime.graph.invocations["child"], 0, now_ms=10.0,
    )
    assert child_features.project_class_duration_median_ms == 3100.0
    runtime._tool_metadata["child"] = (
        "sandbox", "execute", "test_suite", None, "", 3100.0, 15,
    )
    assert runtime._local_frontier_features(
        runtime.graph.invocations["child"], 0, now_ms=10.0,
    ).project_class_duration_median_ms is None
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


def test_bootstrap_join_does_not_prefetch_unrelated_planner_kv():
    runtime = NativeAdmissionRuntime()
    runtime.enable_confirmed_join_canary = True
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
        event(3, RuntimeEventKind.JOIN_CREATE, join_id="join",
              member_invocation_ids=("child",),
              attributes={"mode": "all", "parent_prefix_continuation": False}),
        event(4, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    runtime.on_events((event(5, RuntimeEventKind.RETURN,
                             invocation_id="child"),))
    runtime.on_events((event(6, RuntimeEventKind.JOIN_SATISFIED,
                             join_id="join"),))
    assert runtime.graph.joins["join"].satisfied
    assert runtime._join_ticket is None
    assert runtime.counts["join_prefetch_prefix_discontinuous"] > 0


def test_confirmed_join_canary_is_bounded_without_predictor(tmp_path):
    with pytest.raises(ValueError, match="event socket and no predictor"):
        NativeAdmissionRuntime(enable_confirmed_join_canary=True)
    with pytest.raises(ValueError, match="event socket and no predictor"):
        NativeAdmissionRuntime(
            event_socket_path=str(tmp_path / "invalid.sock"),
            enable_confirmed_join_canary=True,
            enable_admission_prefetch=True,
        )

    runtime = NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "confirmed.sock"),
        enable_confirmed_join_canary=True,
        opportunity_dir=str(tmp_path / "opportunities"),
    )
    try:
        assert runtime.predictor_sha256 is None
        assert runtime.enable_admission_prefetch is False
        assert runtime.counts["confirmed_join_canary_configured"] == 1
        parent = req("parent")
        parent.session_id, parent.session_generation = "s", 1
        assert runtime.register_visible_request(parent)
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
            event(5, RuntimeEventKind.STRUCTURED_ACTION,
                  invocation_id="child", context_id="ctx-child",
                  context_epoch=0, join_id="join",
                  attributes={
                      "beliefkv_child_completion_intent": True,
                      "structured_action_names": ["ChildCompletion"],
                      "request_id": "child-llm",
                  }),
        ))
        assert runtime._join_ticket is None
        runtime.on_events((event(
            6, RuntimeEventKind.RETURN, invocation_id="child",
        ),))
        assert runtime._join_ticket is not None
        assert runtime._join_ticket.phase == "confirmed"
        assert runtime._live_join_ticket()
        assert runtime.running_batch_retraction_barrier_required(object())
        assert not runtime.running_batch_retraction_barrier_required(object())
        runtime.on_running_batch_retraction_barrier_drained(object())
        assert runtime.counts["join_overlap_drain_requested"] == 1
        assert runtime.counts["join_overlap_drain_completed"] == 1
        runtime.attach_native_cache(object())
        observation = NS(
            step=None, no_step_reason="already_device_resident",
            host_backed_full_missing_device_tokens=0,
            host_backed_mamba_missing_device_nodes=0,
            fits_current_free_lists=None,
        )
        with patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=None):
            with patch.object(
                runtime, "inspect_context_h2d_opportunity",
                return_value=observation,
            ) as inspect:
                runtime.dispatch_join_prefetch()
                runtime.dispatch_join_prefetch()
                inspect.assert_called_once_with(
                    context_id="ctx-parent", context_epoch=0,
                )
        key = runtime.context_sessions["ctx-parent"]
        step = PrefetchLoadStep(key, 11, 4, 11, 4)
        with patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=step):
            with patch.object(runtime, "issue_prefetch_gpu_step",
                              return_value="confirmed-h2d") as issue:
                runtime.dispatch_join_prefetch()
                issue.assert_called_once_with(step, source="join_ticket")
                runtime.completed_physical_actions.append(
                    NS(command_id="confirmed-h2d", action="PREFETCH_GPU")
                )
                runtime.dispatch_join_prefetch()
                assert issue.call_count == 1
        assert runtime._join_ticket.issued_nodes == 1
        assert not runtime.running_batch_retraction_barrier_required(object())
        assert runtime.counts["join_prefetch_confirmed_issued"] == 1
        assert runtime.counts["join_prefetch_acked"] == 1
        assert runtime.issue_prefetch_gpu_step(step, source="tool_wait") is None
        runtime.on_events((event(
            7, RuntimeEventKind.CONTEXT_ADVANCE,
            invocation_id="parent", context_id="ctx-parent", context_epoch=1,
        ),))
        assert runtime._join_ticket is None
        assert not runtime.running_batch_retraction_barrier_required(object())
    finally:
        runtime.close()
    records = [
        json.loads(line)
        for line in (
            tmp_path / "opportunities" / "admission_opportunities.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    rejected = [
        row for row in records if row["event"] == "confirmed_join_no_h2d_step"
    ]
    confirmed = [
        row for row in records if row["event"] == "confirmed_join_ticket"
    ]
    assert len(confirmed) == 1
    assert confirmed[0]["join_id"] == "join"
    assert sum(
        row["event"] == "confirmed_join_overlap_drain_requested"
        for row in records
    ) == 1
    assert sum(
        row["event"] == "confirmed_join_overlap_drain_completed"
        for row in records
    ) == 1
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "already_device_resident"
    assert "no_live_detail" not in rejected[0]
    assert rejected[0]["join_id"] == "join"
    closed = [
        row for row in records if row["event"] == "confirmed_join_ticket_closed"
    ]
    assert len(closed) == 1
    assert closed[0]["reason"] == "event_invalidated"
    assert closed[0]["issued_nodes"] == 1
    assert closed[0]["no_step_recorded"] is True


def test_confirmed_join_ticket_expiry_is_recorded_once(tmp_path):
    runtime = NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "confirmed.sock"),
        enable_confirmed_join_canary=True,
        opportunity_dir=str(tmp_path / "opportunities"),
    )
    try:
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
            event(5, RuntimeEventKind.RETURN, invocation_id="child"),
        ))
        assert runtime._join_ticket is not None
        runtime._join_ticket.expires_at = time.monotonic() - 1
        runtime.dispatch_join_prefetch()
        assert runtime._join_ticket is None
        runtime.dispatch_join_prefetch()
    finally:
        runtime.close()
    records = [
        json.loads(line)
        for line in (
            tmp_path / "opportunities" / "admission_opportunities.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    closed = [
        row for row in records if row["event"] == "confirmed_join_ticket_closed"
    ]
    assert len(closed) == 1
    assert closed[0]["reason"] == "expired"
    assert closed[0]["issued_nodes"] == 0
    assert closed[0]["no_step_recorded"] is False


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


def final_stage_runtime(*, stage_only=False, event_socket_path=None, stage_records=None):
    runtime = NativeAdmissionRuntime(
        event_socket_path=event_socket_path,
        enable_final_stage_prefetch=stage_only,
    )
    if stage_records is not None:
        runtime._opportunity_writer = NS(record=stage_records.append)
    runtime.enable_admission_prefetch = not stage_only
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
        event(3, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="other", context_id="ctx-other",
              agent_definition_id="other", agent_instance_id="other"),
        event(4, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(5, RuntimeEventKind.JOIN_WAIT,
              invocation_id="parent", join_id="join"),
    ))
    runtime.on_events((event(
        6, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child", context_id="ctx-child",
        context_epoch=0, join_id="join",
        attributes={"beliefkv_child_completion_intent": True,
                    "child_completion_signal_kind": "stage",
                    "estimated_final_report_tokens": 128},
    ),))
    return runtime


def test_multi_child_join_prefetch_waits_for_the_observed_last_unfinished_child():
    from collections import deque

    runtime = NativeAdmissionRuntime()
    parent = req("parent")
    parent.session_id, parent.session_generation = "s", 1
    runtime.register_visible_request(parent)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE, invocation_id="parent",
              context_id="ctx-parent", agent_definition_id="root", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE, invocation_id="a",
              context_id="ctx-a", agent_definition_id="child", agent_instance_id="a"),
        event(3, RuntimeEventKind.INVOCATION_CREATE, invocation_id="b",
              context_id="ctx-b", agent_definition_id="child", agent_instance_id="b"),
        event(4, RuntimeEventKind.JOIN_CREATE, join_id="join",
              member_invocation_ids=("a", "b")),
        event(5, RuntimeEventKind.JOIN_WAIT, invocation_id="parent", join_id="join"),
    ))
    assert runtime._semantic_parent("a") is None
    assert runtime._semantic_parent("b") is None
    runtime.on_events((
        event(
            6, RuntimeEventKind.STRUCTURED_ACTION,
            invocation_id="b", context_id="ctx-b", context_epoch=0, join_id="join",
            attributes={"beliefkv_child_completion_intent": True,
                        "child_completion_signal_kind": "stage",
                        "estimated_final_report_tokens": 256},
        ),
        event(
            7, RuntimeEventKind.LLM_SUBMIT,
            invocation_id="b", context_id="ctx-b", context_epoch=1,
            attributes={"request_id": "b"},
        ),
    ))
    assert not runtime._final_stages
    assert runtime._join_ticket is None
    assert runtime._child_report_notices["b"].estimated_tokens == 256
    assert runtime._child_report_notices["b"].context_epoch == 1
    assert runtime._child_report_notices["b"].request_id == "b"
    child = req("b")
    child.session_id, child.session_generation = "bs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.register_visible_request(child)
    runtime.on_events((event(8, RuntimeEventKind.RETURN, invocation_id="a"),))
    assert runtime._semantic_parent("b")[0] == "join"
    assert not runtime.graph.joins["join"].satisfied
    now = time.monotonic() * 1000
    submitted = []
    runtime._semantic_worker = NS(
        poll=lambda: (), submit=submitted.append,
        ready=True, disabled=False, dropped=0, error="", close=lambda: None,
    )
    runtime._semantic_progress["b"] = deque(((now - 200, 20),))
    runtime.on_events((RuntimeEvent(
        "body-b", now, RuntimeEventKind.STRUCTURED_ACTION, "wf",
        invocation_id="b", context_id="ctx-b", context_epoch=1,
        attributes={SEMANTIC_TEXT: True, "request_id": "b",
                    "content_chars": 128, "content_tail": "Report complete."},
    ),))
    runtime._poll_semantic_reports(now + 1)
    assert len(submitted) == 1
    assert submitted[0].notice_active
    assert submitted[0].estimated_report_tokens == 256
    reply = SemanticReportReply(submitted[0], .9, 10., 30., 60., 0.)
    runtime._semantic_worker.poll = lambda: (reply,)
    runtime._poll_semantic_reports(now + 200)
    assert runtime._final_stages["join"].semantic_only
    assert runtime._semantic_forecasts["b"].observation.notice_active
    runtime.on_events((event(
        9, RuntimeEventKind.RETURN, invocation_id="b", context_id="ctx-b",
        context_epoch=1,
    ),))
    assert runtime.graph.joins["join"].satisfied
    assert runtime._semantic_parent("b") is None
    assert not runtime._child_report_notices
    runtime.close()


def test_terminal_cache_watch_is_read_only_and_keeps_shared_node_evidence(monkeypatch):
    monkeypatch.setenv("BELIEFKV_TERMINAL_CACHE_DIAGNOSTICS", "1")
    runtime = NativeAdmissionRuntime()
    emitted = []
    runtime._opportunity_writer = NS(record=emitted.append)
    key = PrefillCandidateKey("r", "wf", "child", "ctx", 0, 0, "s", 1)
    refs = NS(snapshot_session_leaf_anchors=lambda *_args, **_kwargs: (
        (0, ((11, 4),)), (2, ((12, 5),)),
    ))
    cache = NS(session_refs=refs, tree_core=NS(
        node_by_id=lambda node: NS(creation_time={11: 4, 12: 5}[node]),
    ))
    runtime._native_cache = cache
    from beliefkv.runtime.sglang_v0520_observer import UnifiedNodeSummary
    summary = UnifiedNodeSummary(
        11, 7, 4, 10, 10, True, True, 0, 0, 0, 0, 2, 2, 2, 2, None, None,
    )
    shared = replace(summary, node_id=7, parent_id=None, creation_time=3)
    later_shared = replace(shared, full_session_refs=3)
    leaf = replace(summary, node_id=12, creation_time=5)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_unified_node_closure",
        side_effect=[
            NS(observable=True, nodes=(summary, shared)),
            NS(observable=True, nodes=(leaf, later_shared)),
        ],
    ) as observe:
        runtime._track_terminal_cache(key)
    assert observe.call_count == 2
    [record] = emitted
    assert record["event"] == "terminal_context_cache_sample"
    assert record["nodes"][0]["full_session_refs"] == 2
    assert record["nodes"][0]["mamba_device_present"]
    assert [node["node_id"] for node in record["nodes"]] == [11, 7, 12]
    assert record["nodes"][1]["full_session_refs"] == 3
    assert "not exclusive dead bytes" in record["semantics"]


def test_terminal_cache_sampling_stops_after_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("BELIEFKV_TERMINAL_CACHE_DIAGNOSTICS", "1")
    runtime = NativeAdmissionRuntime(opportunity_dir=tmp_path)
    key = PrefillCandidateKey("r", "wf", "child", "ctx", 0, 0, "s", 1)
    runtime._native_cache = NS(
        session_refs=NS(
            snapshot_session_leaf_anchors=lambda *_args, **_kwargs: ((0, ((11, 4),)),),
        ),
        tree_core=NS(node_by_id=lambda _node: NS(creation_time=4)),
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=()),
    ) as observe:
        runtime._track_terminal_cache(key)
        assert runtime._terminal_cache_watches
        runtime.close()
        observe.reset_mock()
        runtime.scheduler_step()
        runtime.close()
        observe.assert_not_called()
    assert not runtime._terminal_cache_watches
    rows = [
        json.loads(line)
        for line in (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()
    ]
    assert [row["event"] for row in rows] == [
        "terminal_context_cache_sample", "admission_runtime_state",
    ]
    assert rows[-1]["final"] is True
    assert json.loads(
        (tmp_path / "admission_opportunities_status.json").read_text()
    )["complete"] is True


def test_reactive_keeps_final_priority_without_any_predictive_transfer(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY", "1")
    monkeypatch.setenv("BELIEFKV_ENABLE_PREPARE_HOST", "0")
    runtime = final_stage_runtime()
    runtime.enable_admission_prefetch = False
    child, other = req("child"), req("other")
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    runtime.register_visible_request(other)
    assert select(runtime, [other, child]).candidates[0] is child
    runtime.dispatch_join_prefetch()
    assert runtime._join_ticket is None
    assert runtime.refreshed_shadow_backup_step() is None


@pytest.mark.parametrize("clock_domain, expected_tokens, expected_guard", [
    (None, 20, 100.), ("kernel-A", 50, 0.), ("kernel-B", 20, 100.),
])
def test_semantic_body_is_read_only_and_uses_only_prior_decode_progress(
    monkeypatch, clock_domain, expected_tokens, expected_guard,
):
    from collections import deque

    monkeypatch.setattr(
        "beliefkv.runtime.sglang_v0520_runtime.local_monotonic_clock_domain",
        lambda: "kernel-A",
    )
    runtime = final_stage_runtime()
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    key = runtime.visible["child"]
    now = time.monotonic() * 1000
    submitted = []
    worker = NS(
        poll=lambda: (), submit=submitted.append,
        ready=True, disabled=False, dropped=0, error="",
    )
    runtime._semantic_worker = worker
    runtime._semantic_keys["child"] = key
    runtime._semantic_progress["child"] = deque(
        ((now - 250, 5), (now - 120, 20), (now - 30, 50), (now + 10, 9999)),
    )
    revision = runtime.semantic_revision
    runtime.on_events((RuntimeEvent(
        "body", now, RuntimeEventKind.STRUCTURED_ACTION, "wf",
        invocation_id="child", context_id="ctx-child", context_epoch=1,
        attributes={SEMANTIC_TEXT: True, "request_id": "child",
                    "content_chars": 128, "content_tail": "Report complete.",
                    "monotonic_clock_domain": clock_domain},
    ),))
    assert runtime.semantic_revision == revision
    runtime._poll_semantic_reports(now + 1)
    assert submitted[0].observed_output_tokens == expected_tokens
    assert submitted[0].causal_progress_guard_ms == expected_guard
    assert submitted[0].key == key
    runtime._poll_semantic_reports(now + 400)
    assert len(submitted) == 1
    assert "child" not in runtime._semantic_frames
    assert runtime.counts["semantic_unchanged_frame_skipped"] == 0
    runtime.on_events((RuntimeEvent(
        "duplicate-body", now, RuntimeEventKind.STRUCTURED_ACTION, "wf",
        invocation_id="child", context_id="ctx-child", context_epoch=1,
        attributes={SEMANTIC_TEXT: True, "request_id": "child",
                    "content_chars": 128, "content_tail": "Report complete.",
                    "monotonic_clock_domain": clock_domain},
    ),))
    assert runtime.counts["semantic_unchanged_frame_skipped"] == 1
    assert "child" not in runtime._semantic_frames
    runtime.on_events((RuntimeEvent(
        "tool-body", now + 2, RuntimeEventKind.STRUCTURED_ACTION, "wf",
        invocation_id="child", context_id="ctx-child", context_epoch=1,
        attributes={SEMANTIC_TEXT: True, "request_id": "child", "tool_chunk": True},
    ),))
    assert "child" not in runtime._semantic_frames
    assert not runtime._final_stages
    assert runtime._semantic_keys["child"] == key
    assert "child" in runtime._decoded_tool_requests
    runtime.on_events((RuntimeEvent(
        "late-body", now + 3, RuntimeEventKind.STRUCTURED_ACTION, "wf",
        invocation_id="child", context_id="ctx-child", context_epoch=1,
        attributes={SEMANTIC_TEXT: True, "request_id": "child",
                    "content_chars": 160, "content_tail": "Report complete.",
                    "tool_chunk": False, "monotonic_clock_domain": clock_domain},
    ),))
    assert "child" not in runtime._semantic_frames
    assert not runtime._semantic_key_live(key, now + 3)
    assert runtime.counts["semantic_text_after_tool_ignored"] == 1
    runtime._clear_semantic_invocation("child")
    assert "child" not in runtime._decoded_tool_requests
    assert "child" not in runtime._semantic_keys


def semantic_pending_fixture():
    from collections import deque

    runtime = final_stage_runtime()
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    now = time.monotonic() * 1000
    submitted = []
    runtime._semantic_worker = NS(
        poll=lambda: (), submit=submitted.append,
        ready=True, disabled=False, dropped=0, error="", close=lambda: None,
    )
    runtime._semantic_progress["child"] = deque(((now - 200, 20),))

    def text(timestamp, chars):
        runtime.on_events((RuntimeEvent(
            f"body-{timestamp}", timestamp, RuntimeEventKind.STRUCTURED_ACTION, "wf",
            invocation_id="child", context_id="ctx-child", context_epoch=1,
            attributes={SEMANTIC_TEXT: True, "request_id": "child",
                        "content_chars": chars, "content_tail": "Report complete."},
        ),))

    return runtime, now, submitted, text


def test_semantic_notice_history_survives_transfer_stage_expiry():
    runtime, now, submitted, text = semantic_pending_fixture()
    runtime._final_stages["join"].expires_at = 0.
    text(now, 128)
    runtime._poll_semantic_reports(now + 1)
    assert submitted[0].notice_active
    assert submitted[0].estimated_report_tokens == 128
    runtime.close()


@pytest.mark.parametrize("kind", [
    RuntimeEventKind.TOOL_START, RuntimeEventKind.CONTEXT_COMPACT,
    RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL,
    RuntimeEventKind.WORKFLOW_END,
])
def test_semantic_notice_history_is_retired_on_execution_change(kind):
    runtime, _, _, _ = semantic_pending_fixture()
    runtime.on_events((event(
        8, kind, invocation_id="child", context_id="ctx-child",
        context_epoch=2 if kind is RuntimeEventKind.CONTEXT_COMPACT else 1,
    ),))
    assert not runtime._child_report_notices
    runtime.close()


def test_semantic_notice_does_not_carry_into_another_request():
    runtime, _, _, _ = semantic_pending_fixture()
    runtime.on_events((event(
        8, RuntimeEventKind.LLM_SUBMIT,
        invocation_id="child", context_id="ctx-child", context_epoch=2,
        attributes={"request_id": "next"},
    ),))
    assert not runtime._child_report_notices
    runtime.close()


def test_semantic_pending_snapshot_coalesces_then_resubmits_at_existing_interval():
    runtime, now, submitted, text = semantic_pending_fixture()
    text(now, 128)
    runtime._poll_semantic_reports(now + 1)
    text(now + 10, 160)
    text(now + 20, 192)
    runtime._poll_semantic_reports(now + 30)
    assert len(submitted) == 1
    assert runtime._semantic_frames["child"].ts_ms == now + 20
    runtime._poll_semantic_reports(now + 400)
    assert [item.content_chars for item in submitted] == [128, 192]
    assert submitted[-1].observed_ts_ms == now + 20
    assert not runtime._semantic_frames
    assert runtime._semantic_progress["child"]
    assert runtime._semantic_keys["child"] == submitted[-1].key
    runtime.close()


def test_semantic_pending_snapshot_retries_missing_target_without_new_text():
    runtime, now, submitted, text = semantic_pending_fixture()
    text(now, 128)
    with patch.object(runtime, "_semantic_transfer_target_ready", return_value=False):
        runtime._poll_semantic_reports(now + 1)
    assert not submitted
    assert "child" in runtime._semantic_frames
    with patch.object(runtime, "_semantic_transfer_target_ready", return_value=True):
        runtime._poll_semantic_reports(now + 200)
    assert len(submitted) == 1
    assert not runtime._semantic_frames
    runtime.close()


def test_semantic_pending_snapshot_retries_missing_service_without_new_text():
    runtime, now, submitted, text = semantic_pending_fixture()
    history = runtime._semantic_progress.pop("child")
    text(now, 128)
    runtime._poll_semantic_reports(now + 1)
    assert not submitted
    assert "child" in runtime._semantic_frames
    runtime._semantic_progress["child"] = history
    runtime._poll_semantic_reports(now + 200)
    assert len(submitted) == 1
    assert not runtime._semantic_frames
    runtime.close()


def test_semantic_pending_snapshot_expires_without_retaining_queue_capacity():
    runtime, now, submitted, text = semantic_pending_fixture()
    text(now, 128)
    runtime._poll_semantic_reports(now + 1_501)
    assert not submitted
    assert not runtime._semantic_frames
    assert runtime.counts["semantic_pending_frame_expired"] == 1
    text(now + 1_600, 160)
    runtime._poll_semantic_reports(now + 1_601)
    assert len(submitted) == 1
    runtime.close()


def test_semantic_reply_still_creates_final_stage_after_snapshot_submission():
    runtime, now, submitted, text = semantic_pending_fixture()
    runtime._clear_final_stage("join")
    text(now, 128)
    runtime._poll_semantic_reports(now + 1)
    assert len(submitted) == 1
    assert not runtime._semantic_frames
    reply = SemanticReportReply(submitted[0], .9, 10., 30., 60., 0.)
    runtime._semantic_worker.poll = lambda: (reply,)
    runtime._poll_semantic_reports(now + 200)
    assert runtime._semantic_forecasts["child"] == reply
    assert runtime._final_stages["join"].request_id == "child"
    assert runtime._final_stages["join"].semantic_only
    runtime.close()


def test_native_decode_tool_marker_invalidates_forecast_before_client_tool_chunk():
    from beliefkv.control.causal_graph import InvocationState

    runtime = final_stage_runtime()
    child = req("child")
    child.beliefkv_metadata["context_epoch"] = 1
    child.session_id, child.session_generation = "cs", 1
    child.origin_input_ids, child.output_ids = [1, 2], [3, 248058, 4]
    child.finished = lambda: False
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1, attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    runtime._semantic_worker = NS()
    runtime._tool_open_token_ids = frozenset({248058})
    key = runtime.visible["child"]
    runtime.on_batch_completed(NS(reqs=[child]))
    assert "child" in runtime._decoded_tool_requests
    assert "child" not in runtime._child_report_notices
    assert not runtime._semantic_key_live(key, time.monotonic() * 1000)
    assert not runtime._final_stages
    assert runtime.graph.invocations["child"].state is InvocationState.RUNNING_LLM


def test_semantic_eos_window_creates_only_h2d_candidate_not_final_priority():
    from collections import deque

    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    key = runtime.visible["child"]
    now = time.monotonic() * 1000
    runtime._semantic_keys["child"] = key
    runtime._semantic_progress["child"] = deque(
        ((now - 250, 5), (now - 120, 20), (now - 30, 50)),
    )
    runtime._semantic_finished["child"] = (now - 10, 50)
    del runtime.visible["child"]
    item = SemanticReportInput(key, now - 20, 20, 128, "Done.", False, 0, 1, 2)
    reply = SemanticReportReply(item, .9, 0., 80., 200., 5.)
    replies = [reply]
    runtime._semantic_worker = NS(
        poll=lambda: tuple(replies), submit=lambda _: None,
        ready=True, disabled=False, dropped=0, error="",
    )
    runtime._poll_semantic_reports(now)
    stage = runtime._final_stages["join"]
    assert stage.semantic_only
    assert stage.generated_tokens == 50
    assert stage.tokens_per_second is not None
    assert runtime._live_final_stage(stage)
    runtime._h2d_samples.extend(((101, 10.),) * 3)
    runtime._native_cache = NS(cache_controller=NS(mem_pool_host=NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=1)),
        "mamba": NS(host_pool=NS(size_per_token=1)),
    })))
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=NS(
        step="native-step", fits_current_free_lists=True,
        required_full_tokens=100, required_mamba_slots=1,
    )):
        runtime._roll_final_stage()
    assert runtime._join_ticket is not None
    assert runtime._join_ticket.stage_bound
    assert runtime.running_batch_retraction_barrier_required(NS()) is True
    assert runtime.running_batch_retraction_barrier_required(NS()) is False
    # The same reply cannot attach to the child's next epoch.
    runtime.on_events((event(
        8, RuntimeEventKind.CONTEXT_ADVANCE, invocation_id="child",
        context_id="ctx-child", context_epoch=2,
    ),))
    runtime._poll_semantic_reports(now + 1)
    assert runtime.counts["semantic_result_stale"] == 1


@pytest.mark.parametrize("reason, body, tools, should_issue", [
    ("stop", True, False, True), ("stop", False, False, False),
    ("length", True, False, False), ("abort", True, False, False),
    ("stop", True, True, False),
])
def test_observed_native_final_body_does_not_require_a_model_forecast(
    monkeypatch, reason, body, tools, should_issue,
):
    monkeypatch.setenv("BELIEFKV_EOS_PROTOCOL_WINDOW_MS", "250")
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    key = runtime.visible["child"]
    now = time.monotonic() * 1000
    runtime._semantic_worker = NS()
    runtime._semantic_keys["child"] = key
    runtime._semantic_finished["child"] = (now, 3)
    child.finished_reason = NS(to_json=lambda: {"type": reason})
    child.output_ids = [1, 2, 3]
    if body:
        runtime._semantic_body_seen.add("child")
    if tools:
        runtime._decoded_tool_requests.add("child")
    runtime._record_native_final_body(child, key)
    assert ("join" in runtime._final_stages) is should_issue
    assert not runtime._semantic_forecasts
    runtime._h2d_samples.extend(((101, 10.),) * 3)
    runtime._native_cache = NS(cache_controller=NS(mem_pool_host=NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=1)),
        "mamba": NS(host_pool=NS(size_per_token=1)),
    })))
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=NS(
        step="native-step", fits_current_free_lists=True,
        required_full_tokens=100, required_mamba_slots=1,
    )):
        runtime._roll_final_stage()
    assert (runtime._join_ticket is not None) is should_issue
    if should_issue:
        assert runtime._final_stages["join"].semantic_only
        assert runtime._final_stages["join"].tokens_per_second is None
        runtime._semantic_finished["child"] = (now - 251, 3)
        runtime._join_ticket = None
        runtime._roll_final_stage()
        assert runtime._join_ticket is None


def test_native_reasoning_suffix_proves_visible_body_without_treating_reasoning_as_body():
    runtime = NativeAdmissionRuntime()
    runtime._reasoning_close_token_ids = frozenset({99})
    key = PrefillCandidateKey("r", "wf", "child", "ctx-child", 0, 0)
    child = NS(
        output_ids=[1, 2, 99, 10, 11], beliefkv_metadata={},
        finished_reason=NS(to_json=lambda: {"type": "stop"}),
        tokenizer=NS(decode=lambda tokens, **kwargs: "Report." if 10 in tokens else ""),
    )
    runtime._record_native_final_body(child, key)
    assert runtime._semantic_eos_proofs["r"] is True
    empty = replace(key, request_id="empty")
    child.output_ids = [1, 2, 99, 11]
    runtime._record_native_final_body(child, empty)
    assert runtime._semantic_eos_proofs["empty"] is False


def test_semantic_work_queue_requires_real_parent_host_restore_target():
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime.enable_final_stage_prefetch = True
    runtime._native_cache = NS()
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=NS(step=None)) as inspect:
        assert not runtime._semantic_transfer_target_ready("child", 1000.)
        assert not runtime._semantic_transfer_target_ready("child", 1050.)
        inspect.assert_called_once()
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=NS(step="host-only")):
        assert runtime._semantic_transfer_target_ready("child", 1101.)


def test_semantic_rate_does_not_project_short_decode_burst_as_wall_clock_share():
    runtime = NativeAdmissionRuntime()
    runtime._semantic_progress["child"] = [
        (0., 0), (3000., 20), (4500., 30), (5000., 80),
    ]
    assert runtime._semantic_rate("child") == pytest.approx(16.)
    runtime._semantic_progress["single"] = [(1000., 10)]
    assert runtime._semantic_rate("single") is None


def test_final_stage_promotes_only_bound_join_child_with_admission_budget():
    stage_records = []
    runtime = final_stage_runtime(stage_records=stage_records)
    assert runtime.counts["final_stage_accepted"] == 1
    assert len(stage_records) == 1
    assert stage_records[0]["event"] == "child_final_stage_accepted"
    assert stage_records[0]["join_id"] == "join"
    assert runtime._join_ticket is None
    other, child = req("other"), req("child")
    runtime.register_visible_request(other)
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT,
        invocation_id="child", context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    assert select(runtime, [other, child]).candidates[0] is child
    runtime.on_prefill_candidate_result(child, admitted=True, result="ok")
    assert runtime.counts["final_priority_admitted"] == 1
    assert select(runtime, [other, child]).candidates[0] is other
    runtime.on_events((event(
        8, RuntimeEventKind.TOOL_START,
        invocation_id="child", context_id="ctx-child",
    ),))
    assert "join" not in runtime._final_stages


def test_final_stage_rebinds_one_epoch_late_notice_only_to_live_child_request():
    stage_records = []
    runtime = final_stage_runtime(stage_records=stage_records)
    child = req("child")
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT,
        invocation_id="child", context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    runtime.on_events((event(
        8, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child", context_id="ctx-child", context_epoch=0,
        join_id="join",
        attributes={
            "source": "deepagents_completion_stage",
            "beliefkv_child_completion_intent": True,
            "child_completion_signal_kind": "stage",
            "estimated_final_report_tokens": 128,
        },
    ),))
    assert runtime.counts["final_stage_epoch_handoff"] == 1
    assert runtime._final_stages["join"].child_epoch == 1
    assert runtime._final_stages["join"].request_id == "child"
    assert runtime._final_request_stages["child"] is runtime._final_stages["join"]
    assert runtime._child_report_notices["child"].context_epoch == 1
    assert runtime._child_report_notices["child"].request_id == "child"
    assert stage_records[-1]["epoch_handoff"] is True

    runtime.on_events((event(
        9, RuntimeEventKind.LLM_SUBMIT,
        invocation_id="child", context_id="ctx-child", context_epoch=2,
        attributes={"request_id": "other-request"},
    ),))
    runtime.on_events((event(
        10, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child", context_id="ctx-child", context_epoch=0,
        join_id="join",
        attributes={
            "source": "deepagents_completion_stage",
            "beliefkv_child_completion_intent": True,
            "child_completion_signal_kind": "stage",
            "estimated_final_report_tokens": 128,
        },
    ),))
    assert runtime.counts["join_intent_stale"] >= 1
    assert runtime.counts["final_stage_epoch_handoff"] == 1


def test_final_stage_can_run_without_predictor_but_not_in_baseline(tmp_path):
    with pytest.raises(ValueError, match="event socket"):
        NativeAdmissionRuntime(enable_final_stage_prefetch=True)
    runtime = final_stage_runtime(
        stage_only=True, event_socket_path=str(tmp_path / "stage.sock"),
    )
    try:
        other, child = req("other"), req("child")
        runtime.register_visible_request(other)
        child.beliefkv_metadata["context_epoch"] = 1
        runtime.on_events((event(
            7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
            context_id="ctx-child", context_epoch=1,
            attributes={"request_id": "child"},
        ),))
        runtime.register_visible_request(child)
        assert runtime.predictor_sha256 is None
        assert select(runtime, [other, child]).candidates[0] is child
        assert runtime._live_final_stage(runtime._final_stages["join"])
    finally:
        runtime.close()

    baseline = final_stage_runtime()
    baseline.enable_admission_prefetch = False
    baseline.register_visible_request(req("other"))
    child = req("child")
    child.beliefkv_metadata["context_epoch"] = 1
    baseline.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    baseline.register_visible_request(child)
    assert select(baseline, [req("other"), child]).candidates[0].rid == "other"


def test_reactive_h2d_ack_bootstraps_final_stage_service_samples():
    runtime = NativeAdmissionRuntime()
    for direction, size, elapsed in (
        ("d2h", 100, 150.0),
        ("h2d", 105, 200.0),
        ("h2d", 105, 250.0),
        ("h2d", 105, 180.0),
        ("h2d", 0, 50.0),
    ):
        runtime.on_native_transfer_commit(NS(
            direction=direction, status="completed", child_commits=(),
            actual_bytes=size, submit_to_ack_ms=elapsed,
        ))
    assert list(runtime._h2d_samples) == [
        (105, 200.0), (105, 250.0), (105, 180.0),
    ]
    assert runtime.counts["h2d_service_sample"] == 3


def test_join_prepare_is_selective_and_invalidates_on_parent_reentry():
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime.enable_prepare_host = True
    runtime.attach_native_cache(NS())
    key = runtime.context_sessions["ctx-parent"]
    step = ShadowBackupStep(key, 11, 4, 11, 4)
    from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=0, device_mamba_free_slots=1,
            host_full_free_tokens=1000, host_mamba_free_slots=10,
        ),
    ), patch.object(runtime, "refreshed_shadow_backup_step", return_value=step), patch.object(
        runtime, "issue_shadow_backup_step", return_value="prepare",
    ) as issue:
        runtime.dispatch_join_prepare([req("child")])
        issue.assert_called_once_with(step, source="join_prepare")
    assert runtime._live_parent_pressure_node(11, 4)
    runtime.on_events((event(7, RuntimeEventKind.RETURN, invocation_id="child"),))
    assert not runtime._live_parent_pressure_node(11, 4)


@pytest.mark.parametrize("method", ("dispatch_join_prepare", "dispatch_tool_prepare"))
def test_full_only_prepare_does_not_scan_backups_for_mamba_only_pressure(method):
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime.enable_prepare_host = True
    runtime.attach_native_cache(NS())
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=100_000, device_mamba_free_slots=0,
            host_full_free_tokens=1000, host_mamba_free_slots=0,
        ),
    ), patch.object(runtime, "refreshed_shadow_backup_step") as observe, patch.object(
        runtime, "issue_shadow_backup_step",
    ) as issue:
        getattr(runtime, method)([req("child")])
    observe.assert_not_called()
    issue.assert_not_called()
    assert runtime.counts["prepare_mamba_pressure_only"] == 1


@pytest.mark.parametrize("needed,host_free,capture_expected", (
    (100, 99, False), (100, 100, True), (0, 0, True), (None, 0, True),
))
def test_prepare_budget_hint_only_skips_definitely_unfittable_closures(
    needed, host_free, capture_expected,
):
    runtime = final_stage_runtime()
    key = runtime.context_sessions["ctx-parent"]
    from beliefkv.runtime.sglang_v0520_physical import ContextSessionAnchors
    anchors = ContextSessionAnchors(
        key, ((0, ((11, 4),)), (2, ((11, 4),))), 1., reusable_input_tokens=1000,
    )
    with patch.object(runtime, "snapshot_session_anchors", return_value=anchors), patch(
        "beliefkv.runtime.sglang_v0520_runtime.missing_prepare_full_prefix_tokens",
        return_value=needed,
    ), patch(
        "beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow",
    ) as capture:
        runtime.capture_shadow_candidate(
            NS(), context_id=key.context_id, context_epoch=key.context_epoch,
            include_non_actionable=True, host_full_free_tokens=host_free,
        )
        assert capture.call_count == int(capture_expected)
        assert runtime.counts["prepare_prefix_budget_rejected_early"] == int(not capture_expected)
        capture.reset_mock()
        runtime.capture_shadow_candidate(
            NS(), context_id=key.context_id, context_epoch=key.context_epoch,
            include_non_actionable=True,
        )
        capture.assert_called_once()
    runtime.close()


def test_final_stage_latest_start_requires_serviced_decode_and_h2d_evidence():
    runtime = final_stage_runtime()
    child = req("child")
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    stage = runtime._final_stages["join"]
    stage.generated_tokens = 64
    stage.tokens_per_second = 40
    runtime.attach_native_cache(NS(cache_controller=NS(
        mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=5)),
        })
    )))
    observation = NS(
        step=PrefetchLoadStep(stage.key, 11, 4, 11, 4),
        fits_current_free_lists=True,
        required_full_tokens=10, required_mamba_slots=1,
    )
    with patch.object(runtime, "inspect_context_h2d_opportunity",
                      return_value=observation), patch.object(
        runtime, "refreshed_prefetch_gpu_step", return_value=observation.step,
    ), patch.object(
        runtime, "issue_prefetch_gpu_step", return_value="command",
    ) as issue:
        runtime.dispatch_join_prefetch()
        issue.assert_not_called()
        runtime._h2d_samples.extend([(105, 200.0)] * 3)
        runtime.dispatch_join_prefetch()
        issue.assert_not_called()  # Still too early in the final decode.
        stage.generated_tokens = 120
        runtime.dispatch_join_prefetch()
        issue.assert_called_once()
        assert runtime._join_ticket.issued_nodes == stage.issued_nodes == 1
    runtime.on_events((event(
        8, RuntimeEventKind.RETURN, invocation_id="child",
    ),))
    assert "join" not in runtime._final_stages


@pytest.mark.parametrize("ack_count, native_directions, enough_service", [
    (3, ("d2h",), True),
    (0, ("d2h", "h2d", "d2h", "h2d", "h2d"), True),
    (2, ("h2d",), False),
    (0, ("d2h", "d2h"), False),
])
def test_final_stage_service_evidence_preserves_separate_ack_and_native_histories(
    ack_count, native_directions, enough_service,
):
    runtime = final_stage_runtime()
    runtime._h2d_samples.extend([(105, 200.)] * ack_count)
    runtime._native_service_samples.extend(NS(direction=value) for value in native_directions)
    runtime._roll_final_stage()
    assert runtime.counts["final_stage_no_h2d_service_evidence"] == int(not enough_service)
    assert runtime._join_ticket is None


@pytest.mark.parametrize("statistic, notice, should_issue", [
    ("upper", True, False), ("center", True, True), ("center", False, False),
])
def test_semantic_work_statistic_does_not_bypass_notice_or_physical_checks(
    monkeypatch, statistic, notice, should_issue,
):
    monkeypatch.setenv("BELIEFKV_SEMANTIC_WORK_STATISTIC", statistic)
    runtime = final_stage_runtime()
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    stage = runtime._final_stages["join"]
    stage.generated_tokens, stage.tokens_per_second = 120, 40.
    runtime._semantic_worker = NS()
    runtime._semantic_forecasts["child"] = SemanticReportReply(
        SemanticReportInput(
            runtime.visible["child"], time.monotonic() * 1000,
            120, 256, "Evidence complete.", notice, 500, 1, 2,
        ), .99, 0., 4., 400., 5.,
    )
    runtime._h2d_samples.extend([(105, 200.)] * 3)
    runtime.attach_native_cache(NS(cache_controller=NS(
        mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=5)),
        }),
    )))
    observation = NS(
        step=PrefetchLoadStep(stage.key, 11, 4, 11, 4),
        fits_current_free_lists=True, required_full_tokens=10, required_mamba_slots=1,
    )
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=observation), \
        patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=observation.step), \
        patch.object(runtime, "issue_prefetch_gpu_step", return_value="command") as issue:
        runtime.dispatch_join_prefetch()
        assert bool(issue.call_count) is should_issue
    assert not runtime._semantic_finished
    if should_issue:
        assert runtime._join_ticket.stage_bound
        assert runtime._issued_join_nodes("join", stage.key) == 1


def test_semantic_work_statistic_rejects_unknown_policy(monkeypatch):
    monkeypatch.setenv("BELIEFKV_SEMANTIC_WORK_STATISTIC", "auto-benefit")
    with pytest.raises(ValueError, match="work statistic"):
        NativeAdmissionRuntime()


def test_final_stage_tool_and_epoch_change_cancel_provisional_h2d():
    runtime = final_stage_runtime()
    stage = runtime._final_stages["join"]
    runtime._join_ticket = NS(
        join_id="join", stage_bound=True, phase="provisional"
    )
    runtime.on_events((event(
        7, RuntimeEventKind.TOOL_START,
        invocation_id="child", context_id="ctx-child",
    ),))
    assert "join" not in runtime._final_stages
    assert runtime._join_ticket is None
    assert runtime.counts["final_stage_tool_invalidated"] == 1

    runtime.on_events((event(
        8, RuntimeEventKind.TOOL_END,
        invocation_id="child", context_id="ctx-child",
    ),))
    runtime.on_events((event(
        9, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child", context_id="ctx-child",
        context_epoch=0, join_id="join",
        attributes={"beliefkv_child_completion_intent": True,
                    "child_completion_signal_kind": "stage",
                    "estimated_final_report_tokens": 128},
    ),))
    assert runtime._final_stages["join"].child_id == stage.child_id
    runtime.on_events((event(
        10, RuntimeEventKind.CONTEXT_ADVANCE,
        invocation_id="child", context_id="ctx-child", context_epoch=1,
    ),))
    runtime.scheduler_step()
    assert not runtime._final_stages


def test_join_prefetch_budget_survives_stage_recreation_and_counts_only_issued():
    runtime = final_stage_runtime()
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1,
        attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    key = runtime._final_stages["join"].key
    runtime._h2d_samples.extend([(105, 200.0)] * 3)
    runtime.attach_native_cache(NS(cache_controller=NS(
        mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=5)),
        })
    )))
    observation = NS(
        step=PrefetchLoadStep(key, 11, 4, 11, 4),
        fits_current_free_lists=True,
        required_full_tokens=10, required_mamba_slots=1,
    )
    with patch.object(
        runtime, "inspect_context_h2d_opportunity", return_value=observation,
    ), patch.object(
        runtime, "refreshed_prefetch_gpu_step", return_value=observation.step,
    ), patch.object(
        runtime, "issue_prefetch_gpu_step", side_effect=[None, "cmd-1", "cmd-2"],
    ) as issue:
        stage = runtime._final_stages["join"]
        stage.generated_tokens, stage.tokens_per_second = 120, 80
        runtime.dispatch_join_prefetch()
        assert runtime._issued_join_nodes("join", key) == 0
        runtime.dispatch_join_prefetch()
        assert runtime._issued_join_nodes("join", key) == 1
        for sequence in (8, 9, 10):
            runtime._clear_final_stage("join")
            runtime.on_events((event(
                sequence, RuntimeEventKind.STRUCTURED_ACTION,
                invocation_id="child", context_id="ctx-child", context_epoch=1,
                join_id="join",
                attributes={"beliefkv_child_completion_intent": True,
                            "child_completion_signal_kind": "stage",
                            "estimated_final_report_tokens": 128},
            ),))
            stage = runtime._final_stages["join"]
            stage.request_id = "child"
            stage.generated_tokens, stage.tokens_per_second = 120, 80
            runtime.dispatch_join_prefetch()
        assert issue.call_count == 3  # One declined call, two issued calls.
        assert runtime._issued_join_nodes("join", key) == 2
        assert stage.issued_nodes == 2
    assert runtime._issued_join_nodes(
        "join", replace(key, request_id="another-attempt", attempt_id=2),
    ) == 2
    assert runtime._issued_join_nodes("join", replace(key, context_epoch=1)) == 0
    assert runtime._issued_join_nodes("join", replace(key, session_generation=2)) == 0
    runtime._clear_final_stage("join")
    child_key = runtime.visible["child"]
    now_ms = time.monotonic() * 1000
    runtime._semantic_keys["child"] = child_key
    reply = SemanticReportReply(
        SemanticReportInput(child_key, now_ms, 120, 128, "Done.", False, 0, 1, 2),
        .9, 0., 8., 50., 5.,
    )
    runtime._semantic_worker = NS(
        poll=lambda: (reply,), ready=True, disabled=False, dropped=0,
    )
    runtime._poll_semantic_reports(now_ms)
    assert runtime._final_stages["join"].semantic_only
    assert runtime._final_stages["join"].issued_nodes == 2
    runtime.on_events((event(11, RuntimeEventKind.WORKFLOW_END),))
    assert not runtime._join_prefetch_issued


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

def test_admission_plans_only_queued_invocations_without_whole_graph_scan():
    runtime = NativeAdmissionRuntime()
    a = req("a")
    runtime.register_visible_request(a)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="a", context_id="ctx-a"),
        event(2, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="not-queued", context_id="ctx-other"),
    ))
    with (
        patch.object(runtime.graph, "ready_invocations", side_effect=AssertionError),
        patch.object(runtime.frontier, "_active_descendant_counts", side_effect=AssertionError),
        patch.object(runtime.frontier, "admission_rank",
                     wraps=runtime.frontier.admission_rank) as describe,
    ):
        assert select(runtime, [a]).candidates == (a,)
    assert describe.call_args_list[0].args == ("a",)
    assert describe.call_count == 1


def test_shared_path_timing_is_persisted_without_per_call_rows(tmp_path):
    runtime = NativeAdmissionRuntime(opportunity_dir=str(tmp_path))
    runtime.on_events((event(0, RuntimeEventKind.WORKFLOW_START),))
    runtime.scheduler_step()
    runtime.close()
    rows = [json.loads(line) for line in
            (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()]
    final = next(row for row in rows if row.get("final"))
    phases = final["shared_path_timing"]["phases"]
    assert phases["scheduler_maintenance"]["count"] == 1
    assert phases["event_apply"]["count"] == 1
    assert phases["graph_apply"]["count"] == 1
    assert phases["event_apply"]["total_ms"] >= phases["graph_apply"]["total_ms"]
    assert not any(row["event"] == "hotpath_call" for row in rows)


def test_no_prefetch_lease_skips_per_decode_identity_parsing():
    runtime = NativeAdmissionRuntime()
    a = req("a")
    a.finished = lambda: False
    with patch("beliefkv.runtime.sglang_v0520_runtime._request_key",
               side_effect=AssertionError("no active restore or final stage")):
        runtime.on_batch_completed(NS(reqs=[a]))

def test_native_fenced_prefetch_does_not_request_overlap_drain():
    runtime = NativeAdmissionRuntime()
    runtime.attach_native_cache(NS(supports_beliefkv_overlap_prefetch=lambda: True))
    with patch.object(runtime, "_roll_tool_prefetch", side_effect=AssertionError):
        assert runtime.running_batch_retraction_barrier_required(NS(reqs=[])) is False
    assert not runtime.counts["tool_overlap_drain_requested"]
    assert not runtime.counts["join_overlap_drain_requested"]
    runtime.enable_confirmed_join_canary = True
    assert runtime.can_prefetch_during_overlap() is False


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
