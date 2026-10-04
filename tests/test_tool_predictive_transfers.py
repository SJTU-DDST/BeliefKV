from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import hashlib
import json
import time

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.runtime.sglang_v0520_prediction import (
    NativeToolWaitHint, validate_tool_timing_artifact,
)
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import req


def event(seq, kind, **kwargs):
    return RuntimeEvent(str(seq), float(seq), kind, "wf", **kwargs)


def tool_runtime():
    runtime = NativeAdmissionRuntime(enable_tool_prefetch=True)
    request = req("tool")
    request.session_id, request.session_generation = "s", 1
    runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE, invocation_id="tool",
              context_id="ctx-tool", agent_definition_id="child", agent_instance_id="child"),
        event(2, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_family": "shell", "tool_run_id": "long"}),
    ))
    runtime.predictor_sha256 = "a" * 64
    now = time.monotonic() * 1000
    hint = NativeToolWaitHint(
        runtime.context_sessions["ctx-tool"], 100., 300., 900., now, now + 5000.,
        runtime.predictor_sha256, 2.,
    )
    runtime.tool_wait_hints["ctx-tool"] = hint
    return runtime, hint


def test_parallel_tools_wake_only_when_last_member_completes():
    graph = RuntimeCausalContextGraph()
    graph.apply_batch((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE, invocation_id="tool",
              context_id="ctx", agent_definition_id="child", agent_instance_id="child"),
        event(2, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_run_id": "long", "tool_family": "shell"}),
        event(3, RuntimeEventKind.TOOL_START, invocation_id="tool",
              attributes={"tool_run_id": "short", "tool_family": "filesystem"}),
    ))
    delta = graph.apply(event(4, RuntimeEventKind.TOOL_END, invocation_id="tool",
                             attributes={"tool_run_id": "short"}))
    assert not delta.awakened_invocations
    assert graph.invocations["tool"].state is InvocationState.WAIT_TOOL
    assert graph.invocations["tool"].active_tool_start_ms == 2.
    delta = graph.apply(event(5, RuntimeEventKind.TOOL_END, invocation_id="tool",
                             attributes={"tool_run_id": "long"}))
    assert delta.awakened_invocations == {"tool"}
    assert not graph.invocations["tool"].active_tool_calls


def test_tool_h2d_uses_same_wait_identity_and_does_not_admit_agent():
    runtime, hint = tool_runtime()
    step = PrefetchLoadStep(hint.key, 1, 2, 1, 2)
    opportunity = NS(step=step, fits_current_free_lists=True)
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity), \
         patch.object(runtime, "_tool_service_supported", return_value=True), \
         patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=step), \
         patch.object(runtime, "issue_prefetch_gpu_step", return_value="cmd") as issue:
        assert runtime.running_batch_retraction_barrier_required(NS())
        runtime.dispatch_tool_prefetch()
        issue.assert_called_once()
        assert runtime.counts["tool_prefetch_issued"] == 1
        assert runtime.graph.invocations["tool"].state is InvocationState.WAIT_TOOL
        runtime.on_events((event(3, RuntimeEventKind.TOOL_END, invocation_id="tool",
                                attributes={"tool_run_id": "long"}),))
        runtime.dispatch_tool_prefetch()
        assert issue.call_count == 1


def test_tool_forecast_is_conditioned_on_still_waiting_not_reset_at_reply():
    runtime, hint = tool_runtime()
    cdf = ((100., .1), (500., .5), (1000., .9), (2000., .95))
    hint = NativeToolWaitHint(
        hint.key, 100, 500, 1000, 1000., 6000., hint.predictor_sha256, 2., cdf,
    )
    assert hint.release_probability_within(500., now_ms=1500.) == pytest.approx(.8)


def test_calibrated_event_heads_do_not_require_or_modify_action_eligibility(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    contract = {"server_identity": {"sglang_version": "0.5.20"},
                "model_revision_sha256": {"config.json": hashlib.sha256(b"{}").hexdigest()}}
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = dataset / "dataset_manifest.json"
    manifest.write_text(json.dumps({"source": {"runtime_environment_contract": contract}}))
    artifact = tmp_path / "head.json"
    raw = {
        "calibration_coverage": .9, "components": {"operational_release": {"trained": True}},
        "metadata": {
            "online_eligible": False, "predictive_action_eligible": False,
            "calibration_status": "calibrated_native_heads_only",
            "native_event_timing_report": {"target": "time_until_all_active_tools_return_not_first_gpu_service"},
            "dataset_dirs": [str(dataset)],
            "dataset_manifest_file_sha256s": [hashlib.sha256(manifest.read_bytes()).hexdigest()],
        },
    }
    artifact.write_text(json.dumps(raw))
    validate_tool_timing_artifact(
        str(artifact), expected_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        model_path=str(model),
    )
    assert json.loads(artifact.read_text())["metadata"]["online_eligible"] is False
    (model / "config.json").write_text("changed")
    with pytest.raises(ValueError, match="revision"):
        validate_tool_timing_artifact(
            str(artifact), expected_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            model_path=str(model),
        )
