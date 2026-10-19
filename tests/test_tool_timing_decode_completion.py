from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, req


@pytest.mark.parametrize("tool_timing_only", (False, True))
def test_tool_timing_keeps_completed_tokens_without_decode_progress(tool_timing_only):
    runtime = NativeAdmissionRuntime()
    runtime._tool_timing_only = tool_timing_only
    request = req("tool")
    request.session_id, request.session_generation = "session", 1
    request.origin_input_ids, request.output_ids = [1, 2, 3], [4]
    request.finished = lambda: False
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE,
              invocation_id="tool", context_id="ctx-tool"),
    ))
    runtime.register_visible_request(request)
    try:
        runtime.on_batch_completed(NS(reqs=[request]))
        if tool_timing_only:
            assert "ctx-tool" not in runtime._context_tokens
        else:
            assert runtime._context_tokens["ctx-tool"] == (0, 3, 1, False)
        request.output_ids.append(5)
        request.finished = lambda: True
        runtime.on_batch_completed(NS(reqs=[request]))
        runtime.on_events((event(
            2, RuntimeEventKind.TOOL_START,
            invocation_id="tool", context_id="ctx-tool",
            attributes={"tool_family": "shell"},
        ),))
        features = runtime._local_frontier_features(
            runtime.graph.invocations["tool"], 0, now_ms=3.,
        )
        assert features.generated_tokens == 2
        assert features.current_sequence_tokens == 5
        assert runtime._context_tokens["ctx-tool"] == (0, 3, 2, False)
        assert "tool" not in runtime.visible
    finally:
        runtime.close()


def test_tool_timing_preserves_previous_completed_epoch_during_new_decode():
    runtime = NativeAdmissionRuntime()
    runtime._tool_timing_only = True
    request = req("tool")
    request.beliefkv_metadata["context_epoch"] = 1
    request.session_id, request.session_generation = "session", 1
    request.origin_input_ids, request.output_ids = [1, 2, 3], [4]
    request.finished = lambda: False
    runtime.register_visible_request(request)
    runtime._context_tokens["ctx-tool"] = previous = (0, 2, 2, False)
    try:
        runtime.on_batch_completed(NS(reqs=[request]))
        assert runtime._context_tokens["ctx-tool"] is previous
        request.finished = lambda: True
        runtime.on_batch_completed(NS(reqs=[request]))
        assert runtime._context_tokens["ctx-tool"] == (1, 3, 1, False)
    finally:
        runtime.close()


def test_unfinished_tool_timing_request_still_consumes_parent_handoff():
    runtime = NativeAdmissionRuntime()
    runtime._tool_timing_only = True
    request = req("parent")
    request.session_id, request.session_generation = "session", 1
    request.origin_input_ids, request.output_ids = [1, 2, 3], [4]
    request.finished = lambda: False
    runtime.register_visible_request(request)
    runtime._parent_capacity_handoffs["ctx-parent"] = object()
    try:
        with patch.object(runtime, "_observe_parent_handoff_service") as observe:
            runtime.on_batch_completed(NS(reqs=[request]))
        observe.assert_called_once_with(request)
        assert "ctx-parent" not in runtime._context_tokens
        assert "parent" in runtime.visible
    finally:
        runtime.close()


def test_unfinished_tool_timing_request_still_records_restore_first_service():
    runtime = NativeAdmissionRuntime()
    runtime._tool_timing_only = True
    request = req("parent")
    request.session_id, request.session_generation = "session", 1
    request.origin_input_ids, request.output_ids = [1, 2, 3], [4]
    request.finished = lambda: False
    runtime.register_visible_request(request)
    key = runtime.visible["parent"]
    runtime._prefetch_steps["restore"] = (
        PrefetchLoadStep(key, 11, 1, 11, 1), "join_ticket", None,
    )
    try:
        runtime.on_batch_completed(NS(reqs=[request]))
        assert runtime._prefetch_first_services["restore"][1] == "parent"
        assert "ctx-parent" not in runtime._context_tokens
        assert "parent" in runtime.visible
    finally:
        runtime.close()
