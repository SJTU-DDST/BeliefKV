from types import SimpleNamespace as NS
import hashlib
import json
from pathlib import Path
import time

import pytest

from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_work_worker import WorkPrediction
from tests.test_workflow_work import model_file
from tests.test_paper_baseline_runtime import request
from tests.test_reference_sibling_coadmission import group
from scripts import run_causal_comparison as comparison
from scripts.prepare_workflow_size_candidate import prepare


def settings(tmp_path):
    path, digest = model_file(tmp_path)
    return {
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_CONTROL_INTERVAL_MS": "2000",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1", "TOOL_RESTORE_MODE": "speculative",
        "TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0", "WORKFLOW_SIZE_RESUME": "1",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
    }


@pytest.mark.parametrize("mode", MODES)
def test_model_and_rank_only_reach_explicit_full_composition(tmp_path, mode):
    configured = arm_environment(settings(tmp_path), mode)
    assert configured["BELIEFKV_WORKFLOW_SIZE_RESUME"] == ("1" if mode == "full" else "0")
    assert bool(configured["BELIEFKV_WORKFLOW_WORK_MODEL"]) == (mode == "full")


@pytest.mark.parametrize("changes", (
    {"AGENT_ADMISSION": "off"}, {"AGENT_FREE_CONTINUATION_ADMISSION": "0"},
    {"WORKFLOW_WORK_MODEL": ""}, {"WORKFLOW_WORK_MODEL_SHA256": "bad"},
))
def test_invalid_composition_is_refused_before_registration(tmp_path, changes):
    with pytest.raises(ValueError):
        arm_environment({**settings(tmp_path), **changes}, "full")


def test_real_runtime_configuration_and_unchanged_capacity_authority(monkeypatch, tmp_path):
    configured = arm_environment(settings(tmp_path), "full")
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "work.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        admission = runtime._agent_admission
        runtime.register_visible_request(request("one", "a", tokens=300))
        runtime.register_visible_request(request("two", "b", tokens=300))
        admission.tick(now=time.monotonic())
        state = admission.status()
        assert state["workflow_size_resume"] is True
        assert state["control_interval_ms"] == 2000.
        assert state["decay_half_life_s"] == 1.
        assert state["workflow_size_new_preemption"] is False
        validate_reference_admission(
            state, control_interval_ms=2000, free_continuation_admission=True,
            workflow_size_resume=True,
            workflow_work_model_sha256=configured["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"],
        )
        with pytest.raises(ValueError):
            validate_reference_admission(
                state, control_interval_ms=2000, free_continuation_admission=True,
            )
        assert not runtime.layer_handoff_enabled and not runtime.enable_tool_prefetch
    finally:
        runtime.close()


def test_production_preflight_binds_the_new_setting(tmp_path):
    result = comparison.preflight_runtime(arm_environment(settings(tmp_path), "full"), str(tmp_path))
    assert result["verified_configuration"]["workflow_size_resume"] is True


def test_formal_preflight_refuses_a_diagnostic_model_even_with_exact_hash(tmp_path):
    configured = settings(tmp_path)
    path = Path(configured["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["metadata"]["decision_eligible"] = False
    path.write_text(json.dumps(raw))
    configured["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="held-out calibration"):
        comparison.preflight_runtime(arm_environment(configured, "full"), str(tmp_path))


@pytest.mark.parametrize("overlap", (False, True))
def test_candidate_preparation_preserves_base_and_project_split(tmp_path, overlap):
    path, _digest = model_file(tmp_path)
    model = Path(path)
    raw = json.loads(model.read_bytes())
    raw["metadata"].update({
        "training_projects": ["train"], "calibration_projects": ["calibration"],
        "prefill_token_weight": .01, "decision_eligible": False,
    })
    model.write_text(json.dumps(raw))
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "test_projects": ["train" if overlap else "test"],
    }))
    base = Path(__file__).resolve().parents[1] / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
    original = base.read_bytes()
    if overlap:
        with pytest.raises(ValueError, match="separate training"):
            prepare(base, model, evaluation)
        return
    candidate = prepare(base, model, evaluation)
    assert base.read_bytes() == original
    assert candidate["status"] == "cpu_candidate_waiting_for_qualified_work_predictor"
    assert candidate["common_environment"]["AGENT_CONTROL_INTERVAL_MS"] == "2000"
    assert candidate["common_environment"]["BELIEFKV_LAYER_HANDOFF"] == "0"
    assert candidate["common_environment"]["TOOL_PREFETCH"] == "0"
    assert not candidate["gpu_execution"]["registered"]


def test_actual_paused_competition_uses_one_workflow_bucket_and_real_sibling_index(
    monkeypatch, tmp_path,
):
    actual_paused_competition(monkeypatch, tmp_path, trusted=True)


def test_uncertain_prediction_preserves_original_resume_order_even_with_ready_siblings(
    monkeypatch, tmp_path,
):
    actual_paused_competition(monkeypatch, tmp_path, trusted=False)


def actual_paused_competition(
    monkeypatch, tmp_path, *, trusted,
):
    configured = arm_environment({
        **settings(tmp_path), "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, "full")
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "siblings.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        group(runtime)
        for name, invocation, size, workflow in (
            ("a1", "child-0", 300, "wf"), ("b", "peer", 200, "other"),
            ("a2", "child-1", 300, "wf"),
        ):
            runtime.register_visible_request(request(name, invocation, tokens=size, workflow=workflow))
        admission = runtime._agent_admission
        # Freeze the CPU predictor publication, not the production scheduler.
        admission._work.close()
        admission._work._stopped = False
        if trusted:
            admission._work._predictions.update({
                "wf": WorkPrediction((.9, .05, .05), 0, 1, time.monotonic(), "model"),
                "other": WorkPrediction((.05, .05, .9), 2, 1, time.monotonic(), "model"),
            })
        for item in admission.policy.programs.values():
            item.active = False
        admission.next_tick = 0.
        admission.tick(now=time.monotonic())
        assert admission._resume_groups == (
            {"child-0": "join", "child-1": "join"} if trusted else {}
        )
        assert {p.identity for p in admission.policy.programs.values() if p.active} == (
            {"child-0", "child-1"} if trusted else {"peer", "child-1"}
        )
        assert admission.policy.remaining_capacity() >= 0
        # The first child already exposed two native output tokens. Preserve
        # the original context-size tie breaker within their shared bucket.
        assert admission.policy.last_selection["selected_in_priority_order"] == (
            ("child-1", "child-0") if trusted else ("peer", "child-1")
        )
    finally:
        runtime.close()
