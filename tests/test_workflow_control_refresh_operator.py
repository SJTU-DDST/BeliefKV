from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import start_workflow_control_optimized_candidate as operator
from scripts import watch_workflow_benefit_results as results
from scripts.review_learned_work_ranking import control_optimization_scope
from scripts.review_workflow_objective_pair import verify_objective_pair
from tests.test_workflow_control_operator import qualified_pair, waiter


@pytest.fixture
def refreshed(monkeypatch):
    for name in (
        "HERE", "SERVING", "FROZEN", "OPERATOR_CPU", "REG", "OBSERVER", "RUN",
        "PORT", "CONTROL_SCOPE", "CHANGED_MODULES", "CONTROL_REFRESH",
    ):
        monkeypatch.setattr(operator, name, getattr(operator, name))
    for name in ("candidate", "OUTPUT", "PLAN", "STATE"):
        monkeypatch.setattr(results, name, getattr(results, name))
    operator.select_control_refresh_candidate()
    return operator


def refresh_pair(tmp_path):
    reference, candidate, pinned = qualified_pair(tmp_path)
    path = Path(candidate["gpu_execution"]["control_optimization_qualification"])
    proof = json.loads(path.read_text())
    proof["complete_prior_1346_cases_retained"] = True
    proof.update(tool_offload_retry_interval_ms=400, tool_offload_control_cost_margin_ms=400)
    candidate["common_environment"]["BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS"] = "400"
    candidate["comparison_contract"].update(
        tool_offload_retry_interval_ms=400,
        tool_offload_control_cost_margin_ms=400,
        real_tool_request_and_transfer_events_wake_immediately=True,
        joint_predictor_optimization="qualified_conditional_progress_tree",
        prediction_parameters_and_support_unchanged=False,
    )
    candidate_path = Path(candidate["common_environment"]["WORKFLOW_WORK_MODEL"])
    model = json.loads(candidate_path.read_text())
    model["conditional_progress_tree_head"] = {"qualified_test_head": True}
    candidate_path.write_text(json.dumps(model))
    for prefix in ("", "BELIEFKV_"):
        candidate["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(candidate_path)
    reference_path = Path(reference["common_environment"]["WORKFLOW_WORK_MODEL"])
    proof.update(
        joint_predictor_optimization="qualified_conditional_progress_tree",
        predictor_export_path=str(candidate_path), predictor_export_sha256=sha256_file(candidate_path),
        predictor_source_model_path=str(reference_path),
        predictor_source_model_sha256=sha256_file(reference_path),
    )
    path.write_text(json.dumps(proof))
    candidate["gpu_execution"]["control_optimization_qualification_sha256"] = sha256_file(path)
    return reference, candidate, pinned


def test_refresh_is_a_separate_attempt_and_keeps_the_interrupted_identity(refreshed):
    assert operator.HERE != operator.INTERRUPTED_HERE
    assert operator.REG != operator.INTERRUPTED_REG
    assert operator.PORT == 19002
    assert operator.candidate_options() == ["--control-refresh"]
    assert operator.SERVING.name == "beliefkv-latest-tool-control400-20261104"
    assert len(operator.CHANGED_MODULES) == 12
    assert {
        "beliefkv/runtime/predicted_tool_offload.py",
        "beliefkv/runtime/reference_tool_replacement.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
    } <= operator.CHANGED_MODULES


def test_refreshed_joint_scope_requires_complete_prior_serving_checks(tmp_path, refreshed):
    reference, candidate, _pin = refresh_pair(tmp_path)
    before = deepcopy((reference, candidate))
    assert control_optimization_scope(candidate)["scope"] == operator.CONTROL_SCOPE
    assert len(verify_objective_pair(reference, candidate)) == 2
    assert before == (reference, candidate)


@pytest.mark.parametrize("change", ("lost_cases", "missing_runtime", "extra_module", "unknown_scope", "invalid_scope"))
def test_refreshed_scope_rejects_unqualified_or_unknown_changes(tmp_path, refreshed, change):
    _reference, candidate, _pin = refresh_pair(tmp_path)
    path = Path(candidate["gpu_execution"]["control_optimization_qualification"])
    proof = json.loads(path.read_text())
    if change == "lost_cases":
        proof["complete_prior_1346_cases_retained"] = False
    elif change == "missing_runtime":
        proof["changed_runtime_modules"].remove("beliefkv/runtime/sglang_v0520_runtime.py")
    elif change == "extra_module":
        proof["changed_runtime_modules"].append("beliefkv/runtime/workflow_combination.py")
    else:
        candidate["comparison_contract"]["synchronous_control_optimization"] = (
            "unknown" if change == "unknown_scope" else []
        )
    path.write_text(json.dumps(proof))
    candidate["gpu_execution"]["control_optimization_qualification_sha256"] = sha256_file(path)
    with pytest.raises(ValueError, match="qualified joint-change"):
        control_optimization_scope(candidate)


@pytest.mark.parametrize("change", ("interval", "margin", "events", "other_environment", "unqualified"))
def test_only_the_qualified_retry_cadence_can_change(tmp_path, refreshed, change):
    reference, candidate, _pin = refresh_pair(tmp_path)
    if change == "interval":
        candidate["common_environment"]["BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS"] = "500"
    elif change == "margin":
        candidate["comparison_contract"]["tool_offload_control_cost_margin_ms"] = 800
    elif change == "events":
        candidate["comparison_contract"]["real_tool_request_and_transfer_events_wake_immediately"] = False
    elif change == "unqualified":
        candidate["comparison_contract"].pop("synchronous_control_optimization")
    else:
        candidate["common_environment"]["HOST_GB"] = "800"
    with pytest.raises(ValueError):
        verify_objective_pair(reference, candidate)


def test_refresh_bootstrap_propagates_selection_after_two_idle_observations(
    tmp_path, monkeypatch, refreshed,
):
    here = waiter(monkeypatch, tmp_path)
    monkeypatch.setattr(operator.causal.qualified, "resources", lambda _port: {"ready": True})
    monkeypatch.setattr(operator.subprocess, "run", lambda *args, **kwargs: NS(
        returncode=0, stdout="registered", stderr="",
    ))
    launches = []
    monkeypatch.setattr(operator.subprocess, "Popen", lambda command, **kwargs: (
        launches.append(command) or NS(pid=123)
    ))
    monkeypatch.setattr(operator.causal.reservation, "process", lambda _pid: {"pid": 123})
    operator.wait_and_start()
    assert launches[0][-2:] == ["--observe", "--control-refresh"]
    state = json.loads((here / "resource_waiter.state.json").read_text())
    assert state["status"] == "comparison_registered"
    assert state["consecutive_ready"] == 2


def test_refresh_waiter_child_preserves_selection(tmp_path, monkeypatch, refreshed):
    here = tmp_path / "new"
    here.mkdir()
    monkeypatch.setattr(operator, "HERE", here)
    monkeypatch.setattr(operator, "REG", tmp_path / "new-registration.json")
    monkeypatch.setattr(operator, "checked_qualification", lambda: {})
    monkeypatch.setattr(operator, "check_reservation", lambda: None)
    monkeypatch.setattr(operator.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(operator.causal.reservation, "process", lambda _pid: {"pid": 123})
    launches = []
    monkeypatch.setattr(operator.subprocess, "Popen", lambda command, **kwargs: (
        launches.append(command) or NS(pid=123)
    ))
    operator.queue()
    assert launches[0][-2:] == ["--wait-and-start", "--control-refresh"]


def test_refresh_results_child_preserves_selection(tmp_path, monkeypatch, refreshed):
    results.select_control_optimized_candidate(refresh=True)
    assert results.OUTPUT == operator.HERE / "final_results_r1"
    output = tmp_path / "new-results"
    output.mkdir()
    monkeypatch.setattr(results, "OUTPUT", output)
    monkeypatch.setattr(results, "STATE", output / "state.json")
    monkeypatch.setattr(results.causal, "load", lambda _path: {"analysis_source": {"clean": True}})
    monkeypatch.setattr(results, "git_source_state", lambda _path: {"clean": True})
    monkeypatch.setattr(results.causal.reservation, "process", lambda _pid: {"pid": 123})
    launches = []
    monkeypatch.setattr(results.subprocess, "Popen", lambda command, **kwargs: (
        launches.append(command) or NS(pid=123)
    ))
    results.queue()
    assert launches[0][-2:] == ["--watch", "--control-refresh"]
    assert launches[0][launches[0].index("--output-dir") + 1] == str(output)


def interrupted_fixture(tmp_path, monkeypatch):
    here = tmp_path / "interrupted"
    output = here / "user_pause_r1"
    output.mkdir(parents=True)
    run = tmp_path / "raw/full"
    run.mkdir(parents=True)
    reg = tmp_path / "old.registration.json"
    reg.write_text("{}")
    state = {"watcher": {"pid": 1}, "run_root": str(run.parent)}
    cleanup = {"identities": [{"pid": 2}, {"pid": 3}], "remaining_processes": []}
    request = {
        "last_cohort_observer_state": {"process": {"pid": 4}},
        "results_observer_state": {"process": {"pid": 5}},
    }
    (here / "STOP").write_text("preserve user interruption")
    paths = {
        reg.with_suffix(".state.json"): state,
        run / "owned_process_cleanup.json": cleanup,
        output / "request.json": request,
    }
    for path, value in paths.items():
        path.write_text(json.dumps(value))
    review = {
        "status": "user_requested_pause_verified", "completed_comparison": False,
        "automatic_resume_allowed": False, "remaining_owned_processes": [],
        "evidence_sha256": {str(path): sha256_file(path) for path in (*paths, reg, here / "STOP")},
    }
    (output / "pause_review.json").write_text(json.dumps(review))
    monkeypatch.setattr(operator, "INTERRUPTED_HERE", here)
    monkeypatch.setattr(operator, "INTERRUPTED_REG", reg)
    monkeypatch.setattr(operator.causal.reservation, "alive", lambda handle: False)
    return here


def test_actual_interrupted_exit_is_verified_without_modifying_old_evidence(tmp_path, monkeypatch):
    here = interrupted_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    operator.verify_interrupted_attempt()
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert (here / "STOP").is_file()


@pytest.mark.parametrize("live_pid", (1, 2, 3, 4, 5))
def test_any_actual_interrupted_owner_or_observer_blocks_new_launch(tmp_path, monkeypatch, live_pid):
    interrupted_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(operator.causal.reservation, "alive", lambda handle: handle["pid"] == live_pid)
    with pytest.raises(ValueError, match="actual owned exit"):
        operator.verify_interrupted_attempt()


def test_changed_interrupted_evidence_is_preserved_and_rejected(tmp_path, monkeypatch):
    here = interrupted_fixture(tmp_path, monkeypatch)
    (here / "STOP").write_text("changed")
    with pytest.raises(ValueError, match="input changed"):
        operator.verify_interrupted_attempt()
