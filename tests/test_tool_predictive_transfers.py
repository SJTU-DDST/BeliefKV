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
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep, PhysicalActionCompleted
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


def test_one_second_window_and_parking_use_one_conditional_distribution():
    runtime, hint = tool_runtime()
    now = time.monotonic() * 1000
    weak = NativeToolWaitHint(
        hint.key, 100., 500., 2000., now, now + 5000.,
        hint.predictor_sha256, hint.invocation_revision_ts_ms,
        ((100., .01), (1000., .2), (5000., .6)),
    )
    assert not runtime._tool_prefetch_ready(weak)
    far = NativeToolWaitHint(
        hint.key, 2000., 3000., 6000., now, now + 5000.,
        hint.predictor_sha256, hint.invocation_revision_ts_ms,
        ((100., .9), (1000., .95), (5000., .99)),
    )
    assert runtime._tool_prefetch_ready(far)


def test_overdue_median_is_not_a_completion_signal():
    runtime, hint = tool_runtime()
    overdue = NativeToolWaitHint(
        hint.key, 50., 100., 500., 1000., 6000.,
        hint.predictor_sha256, hint.invocation_revision_ts_ms,
    )
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1.5):
        assert overdue.live(overdue.key, now_ms=1500.)
        assert overdue.remaining_quantile(.5, now_ms=1500.) is None
        assert not runtime._tool_prefetch_ready(overdue)


def test_conditional_quantiles_and_cdf_stay_consistent_after_old_median():
    runtime, hint = tool_runtime()
    surviving = NativeToolWaitHint(
        hint.key, 100., 500., 1000., 1000., 6000.,
        hint.predictor_sha256, hint.invocation_revision_ts_ms,
        ((100., .1), (500., .5), (1000., .9), (5000., .98)),
    )
    remaining = surviving.remaining_quantile(.5, now_ms=1500.)
    assert remaining is not None and 0 < remaining < 500.
    assert surviving.release_probability_within(remaining, now_ms=1500.) == pytest.approx(.5)


def test_long_wait_and_prefetch_never_both_true_for_surviving_curve():
    runtime, hint = tool_runtime()
    distribution = NativeToolWaitHint(
        hint.key, 10., 20., 30., 1000., 11000.,
        hint.predictor_sha256, hint.invocation_revision_ts_ms,
        ((100., .01), (1000., .03), (5000., .5), (10000., .9), (20000., .99)),
    )
    runtime.tool_wait_hints[hint.key.context_id] = distribution
    for now in (1., 1.2, 2., 4., 6., 9.):
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now):
            assert not (
                runtime._tool_prefetch_ready(distribution)
                and runtime._long_tool_wait(hint.key)
            )


def test_saturated_or_unsupported_cdf_is_not_zero_remaining_work():
    runtime, hint = tool_runtime()
    tail = NativeToolWaitHint(
        hint.key, 1., 2., 3., 1000., 6000., hint.predictor_sha256,
        hint.invocation_revision_ts_ms, ((100., .1), (1000., .2)),
    )
    assert tail.remaining_quantile(.5, now_ms=1500.) is None
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1.5):
        assert not runtime._tool_prefetch_ready(tail)


def leased_runtime():
    runtime, hint = tool_runtime()
    node = NS(id=1, creation_time=2, component_data={
        0: NS(value=object()), 2: NS(value=object()),
    })
    runtime.attach_native_cache(NS(
        tree_core=NS(node_by_id=lambda node_id: node),
        session_refs=NS(_session_generations={"s": 1}, _closed_session_ids=set()),
    ))
    step = PrefetchLoadStep(hint.key, 1, 2, 1, 2)
    action = PhysicalActionCompleted(
        "lease", "PREFETCH_GPU", hint.key.context_id, hint.key.context_epoch,
        (1,), (("kv", 20), ("mamba", 30)), 50,
    )
    runtime._prefetch_steps[action.command_id] = (step, "tool_wait", 2.)
    runtime._register_prefetch_service_lease(action)
    assert action.command_id in runtime._prefetch_service_leases
    return runtime, hint, node, action


def test_ack_keeps_bounded_residency_out_of_pressure_parking():
    runtime, hint, node, action = leased_runtime()
    runtime._parent_pressure_candidates[1] = (hint.key, 2)
    with patch.object(runtime, "_long_tool_wait", return_value=True):
        assert not runtime._live_parent_pressure_node(1, 2)
    assert runtime.counts["prefetch_residency_pressure_protected"] == 1
    assert action.command_id in runtime._prefetch_service_leases


def test_tool_end_keeps_completed_restore_until_first_gpu_service():
    runtime, hint, node, action = leased_runtime()
    runtime.on_events((event(
        3, RuntimeEventKind.TOOL_END, invocation_id="tool",
        attributes={"tool_run_id": "long"},
    ),))
    assert action.command_id in runtime._prefetch_service_leases
    request = req("tool")
    request.session_id, request.session_generation = "s", 1
    request.output_ids = [42]
    request.finished = lambda: False
    runtime.on_batch_completed(NS(reqs=[request]))
    assert not runtime._prefetch_service_leases
    assert runtime.counts["prefetch_residency_released:first_gpu_service"] == 1


def test_residency_expiry_is_bounded_and_does_not_free_native_data():
    runtime, hint, node, action = leased_runtime()
    lease = runtime._prefetch_service_leases[action.command_id]
    assert lease.expires_at - lease.acknowledged_at == pytest.approx(2.)
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=lease.expires_at):
        runtime._refresh_prefetch_service_leases()
    assert not runtime._prefetch_service_leases
    assert node.component_data[0].value is not None
    assert node.component_data[2].value is not None
    assert runtime.counts["prefetch_residency_released:service_window_expired"] == 1


def test_native_eviction_explicitly_revokes_soft_residency():
    runtime, hint, node, action = leased_runtime()
    node.component_data[2].value = None
    runtime._refresh_prefetch_service_leases()
    assert not runtime._prefetch_service_leases
    assert runtime.counts["prefetch_residency_released:native_residency_lost"] == 1


def test_changed_wait_prediction_explicitly_revokes_residency():
    runtime, hint, node, action = leased_runtime()
    now = time.monotonic() * 1000
    runtime.tool_wait_hints[hint.key.context_id] = NativeToolWaitHint(
        hint.key, 2500., 5000., 10000., now, now + 5000.,
        hint.predictor_sha256, hint.invocation_revision_ts_ms,
    )
    runtime._refresh_prefetch_service_leases()
    assert not runtime._prefetch_service_leases
    assert runtime.counts["prefetch_residency_released:prediction_window_left"] == 1


def test_session_change_revokes_residency_instead_of_blocking_new_agent():
    runtime, hint, node, action = leased_runtime()
    runtime._native_cache.session_refs._session_generations["s"] = 2
    runtime._refresh_prefetch_service_leases()
    assert not runtime._prefetch_service_leases
    assert runtime.counts["prefetch_residency_released:session_changed"] == 1


def test_service_lease_survives_one_epoch_until_new_request_consumes():
    runtime, hint, node, action = leased_runtime()
    runtime.on_events((
        event(3, RuntimeEventKind.TOOL_END, invocation_id="tool",
              attributes={"tool_run_id": "long"}),
        event(4, RuntimeEventKind.LLM_SUBMIT, invocation_id="tool",
              context_id="ctx-tool", context_epoch=1,
              attributes={"request_id": "resumed"}),
    ))
    request = req("tool")
    request.rid = "resumed"
    request.beliefkv_metadata["context_epoch"] = 1
    request.session_id, request.session_generation = "s", 1
    request.origin_input_ids, request.output_ids = [1, 2], [3]
    assert runtime.register_visible_request(request)
    assert action.command_id in runtime._prefetch_service_leases
    runtime.on_batch_completed(NS(reqs=[request]))
    assert not runtime._prefetch_service_leases
    assert runtime.counts["prefetch_residency_released:first_gpu_service"] == 1


def test_verified_native_ack_registers_service_lease_before_ticket_retirement():
    runtime, hint, node, action = leased_runtime()
    runtime._prefetch_service_leases.clear()
    step = PrefetchLoadStep(hint.key, 1, 2, 1, 2)
    runtime._prefetch_steps[action.command_id] = (step, "tool_wait", 2.)
    with patch.object(runtime.physical_ledger, "observe", return_value=(action,)):
        assert runtime.on_native_transfer_commit(NS()) == (action,)
    assert action.command_id in runtime._prefetch_service_leases
    assert action.command_id not in runtime._prefetch_steps


def test_timing_only_hint_acceptance_defers_expensive_physical_ancestry():
    runtime, hint = tool_runtime()
    runtime._tool_timing_only = True
    runtime._model_worker = NS(disabled=False)
    with patch.object(runtime, "capture_shadow_candidate") as capture:
        runtime._accept_tool_wait(hint)
        runtime._accept_tool_wait(hint)
    capture.assert_not_called()
    assert runtime.tool_wait_hints[hint.key.context_id] is hint
    assert runtime.counts["tool_hint_physical_inspection_deferred"] == 2


def test_unchanged_long_wait_does_not_submit_prediction_every_decode_tick():
    runtime, hint = tool_runtime()
    runtime._tool_timing_only = True
    submitted = []
    runtime._model_worker = NS(disabled=False, submit_tool_wait=submitted.append)
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=3.):
        runtime._submit_tool_wait("ctx-tool")
        runtime._submit_tool_wait("ctx-tool")
    assert len(submitted) == 1
    assert runtime.counts["tool_unchanged_wait_prediction_skipped"] == 1


def test_tool_opportunity_cache_avoids_repeated_native_inspection():
    runtime, hint = tool_runtime()
    opportunity = NS(step=None, fits_current_free_lists=None)
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity) as inspect:
        runtime._roll_tool_prefetch()
        runtime._roll_tool_prefetch()
    assert inspect.call_count == 1
    assert runtime._tool_ticket is None


def test_tool_ack_does_not_drain_overlap_again_for_already_restored_pages():
    runtime, hint = tool_runtime()
    step = PrefetchLoadStep(hint.key, 1, 2, 1, 2)
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=NS(
        step=step, fits_current_free_lists=True,
    )), patch.object(runtime, "_tool_service_supported", return_value=True), \
         patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=step), \
         patch.object(runtime, "issue_prefetch_gpu_step", return_value="c1"):
        assert runtime.running_batch_retraction_barrier_required(NS())
        runtime.dispatch_tool_prefetch()
    runtime.completed_physical_actions.append(NS(command_id="c1", action="PREFETCH_GPU"))
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=None):
        assert not runtime.running_batch_retraction_barrier_required(NS())
    assert runtime._tool_ticket is None
    assert runtime.counts["tool_overlap_drain_requested"] == 1


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
