from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import start_workflow_control_optimized_candidate as operator
from scripts import watch_workflow_benefit_results as results
from scripts.prepare_workflow_benefit_only import benefit_only_artifact
from scripts.review_learned_work_ranking import control_optimization_scope
from scripts.review_workflow_objective_pair import verify_objective_pair
from tests.test_learned_work_ranking_review import learned_pair


def qualified_pair(tmp_path):
    _control, reference = learned_pair(tmp_path)
    reference["comparison_contract"].update(
        cost_tie_break_added=False, prediction_parameters_and_support_unchanged=True,
    )
    old = Path(reference["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(old.read_bytes())
    profile = raw["admission_cost_profile"]
    from hashlib import sha256

    profile["identity"] = sha256(json.dumps({
        "costs": profile["costs"], "capacity_summary_kind": profile["kind"],
    }, sort_keys=True).encode()).hexdigest()
    old.write_text(json.dumps(raw))
    for prefix in ("", "BELIEFKV_"):
        reference["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(old)
    candidate = deepcopy(reference)
    model = tmp_path / "benefit.json"
    model.write_text(json.dumps(benefit_only_artifact(raw)))
    for prefix in ("", "BELIEFKV_"):
        candidate["common_environment"].update({
            prefix + "WORKFLOW_WORK_MODEL": str(model),
            prefix + "WORKFLOW_WORK_MODEL_SHA256": sha256_file(model),
        })
    pinned = tmp_path / "control_input.json"
    pinned.write_text('{"CPU": true}')
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "source": {"commit": "optimized", "dirty": False},
        "complete_prior_1179_cases_retained": True,
        "changed_runtime_modules": sorted(operator.CHANGED_MODULES),
        "inputs_sha256": {str(pinned): sha256_file(pinned)},
    }))
    candidate["gpu_execution"] = {
        "serving_commit": "optimized",
        "control_optimization_qualification": str(qualification),
        "control_optimization_qualification_sha256": sha256_file(qualification),
    }
    candidate["comparison_contract"].update(
        combination_selection_objective="benefit",
        only_combination_selection_objective_changed=False,
        synchronous_control_optimization=operator.CONTROL_SCOPE,
        admission_objective_and_control_optimization_are_joint_change=True,
        ranking_gain_isolated=False,
    )
    return reference, candidate, pinned


def test_joint_comparison_accepts_same_predictor_but_does_not_claim_objective_isolation(tmp_path):
    reference, candidate, _pin = qualified_pair(tmp_path)
    before = deepcopy((reference, candidate))
    assert len(verify_objective_pair(reference, candidate)) == 2
    assert control_optimization_scope(candidate)["ranking_gain_isolated"] is False
    assert (reference, candidate) == before


@pytest.mark.parametrize("change", (
    "objective_only", "ranking_isolated", "different_commit", "changed_qualification",
    "changed_input", "changed_host", "changed_tools", "changed_predictor",
))
def test_joint_comparison_rejects_false_attribution_and_changed_work(tmp_path, change):
    reference, candidate, pinned = qualified_pair(tmp_path)
    if change == "objective_only":
        candidate["comparison_contract"]["only_combination_selection_objective_changed"] = True
    elif change == "ranking_isolated":
        candidate["comparison_contract"]["ranking_gain_isolated"] = True
    elif change == "different_commit":
        candidate["gpu_execution"]["serving_commit"] = "other"
    elif change == "changed_qualification":
        Path(candidate["gpu_execution"]["control_optimization_qualification"]).write_text("{}")
    elif change == "changed_input":
        pinned.write_text("{}")
    elif change == "changed_host":
        candidate["common_environment"]["HOST_GB"] = "800"
    elif change == "changed_tools":
        candidate["tool_timing_overlay"] = {"changed": True}
    else:
        path = Path(candidate["common_environment"]["WORKFLOW_WORK_MODEL"])
        raw = json.loads(path.read_bytes())
        raw["input_kind"] = "full_prompt"
        path.write_text(json.dumps(raw))
        for prefix in ("", "BELIEFKV_"):
            candidate["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    with pytest.raises(ValueError):
        verify_objective_pair(reference, candidate)


def waiter(monkeypatch, tmp_path):
    here = tmp_path / "new"
    here.mkdir()
    monkeypatch.setattr(operator, "HERE", here)
    monkeypatch.setattr(operator, "REG", tmp_path / "registration.json")
    monkeypatch.setattr(operator, "checked_qualification", lambda: {"source": {}, "inputs_sha256": {}})
    monkeypatch.setattr(operator, "check_reservation", lambda: None)
    monkeypatch.setattr(operator.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(operator.os.path, "lexists", lambda _path: False)
    monkeypatch.setattr(operator, "launch_command", lambda: ["qualified-serving", "register"])
    return here


def test_optimized_waiter_requires_two_fresh_idle_observations(monkeypatch, tmp_path):
    here = waiter(monkeypatch, tmp_path)
    samples, launches = [], []
    readings = iter((False, True, False, True, True))

    def observe(_port):
        ready = next(readings)
        samples.append(ready)
        return {"ready": ready}

    def register(command, **kwargs):
        launches.append((list(samples), command, kwargs["cwd"]))
        return SimpleNamespace(returncode=1, stdout="", stderr="preserved refusal")

    monkeypatch.setattr(operator.causal.qualified, "resources", observe)
    monkeypatch.setattr(operator.subprocess, "run", register)
    operator.wait_and_start()
    assert launches == [([False, True, False, True, True], ["qualified-serving", "register"], operator.SERVING)]
    state = json.loads((here / "resource_waiter.state.json").read_bytes())
    assert state["status"] == "stopped_preserve_failure"
    launch = json.loads((here / "launch.json").read_bytes())
    assert launch["benefit_objective_and_control_optimization_are_joint_change"] is True
    assert not (here / "registration.json").exists()


@pytest.mark.parametrize("reason", ("STOP", "reservation"))
def test_optimized_waiter_obeys_cancellation_without_gpu_actions(monkeypatch, tmp_path, reason):
    here = waiter(monkeypatch, tmp_path)
    if reason == "STOP":
        (here / "STOP").touch()
    else:
        def held():
            raise RuntimeError("new user reservation")

        monkeypatch.setattr(operator, "check_reservation", held)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cancelled waiters must not register or inspect GPU resources")

    monkeypatch.setattr(operator.causal.qualified, "resources", forbidden)
    monkeypatch.setattr(operator.subprocess, "run", forbidden)
    operator.wait_and_start()
    assert not (here / "launch.json").exists()
    assert json.loads((here / "resource_waiter.state.json").read_bytes())["status"] == "stopped_preserve_failure"


def test_launch_uses_qualified_serving_sources_and_unchanged_arrivals(monkeypatch, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    flags = (
        "registration", "run-root", "port", "experiment-config", "cpu-report", "cpu-sources",
        "engine-sources", "workflow-arrivals",
    )
    command = ["python", "old.py"]
    for flag in flags:
        command.extend(("--" + flag, "preserve-arrivals" if flag == "workflow-arrivals" else "old"))
    (old / "launch.json").write_text(json.dumps({"command": command}))
    monkeypatch.setattr(operator, "OLD", old)
    actual = operator.launch_command()
    assert actual[1] == str(operator.SERVING / "scripts/run_causal_comparison.py")
    assert actual[actual.index("--cpu-report") + 1] == str(operator.FROZEN / "cpu.xml")
    assert actual[actual.index("--engine-sources") + 1] == str(
        operator.HERE / "native_sources_for_registration.json",
    )
    assert actual[actual.index("--workflow-arrivals") + 1] == "preserve-arrivals"
    assert command[1] == "old.py"


@pytest.mark.parametrize("live,registered", ((True, False), (False, True), (False, False)))
def test_retirement_requires_actual_owned_exit_and_no_old_gpu_registration(monkeypatch, tmp_path, live, registered):
    here = tmp_path / "old"
    (here / "final_results_r1").mkdir(parents=True)
    for path in (here / "resource_waiter.state.json", here / "final_results_r1/watcher.state.json"):
        path.write_text(json.dumps({"status": "stopped_preserve_failure", "process": {"pid": 1}}))
    registration, observer = tmp_path / "registration.json", tmp_path / "observer.json"
    if registered:
        registration.write_text("{}")
    monkeypatch.setattr(operator.previous, "HERE", here)
    monkeypatch.setattr(operator.previous, "REG", registration)
    monkeypatch.setattr(operator.previous, "OBSERVER", observer)
    monkeypatch.setattr(operator.causal.reservation, "alive", lambda _handle: live)
    if live or registered:
        with pytest.raises(ValueError):
            operator.verify_retired_waiters()
    else:
        operator.verify_retired_waiters()


def test_optimized_results_watcher_preserves_candidate_selection_in_child(monkeypatch, tmp_path):
    for name in ("candidate", "OUTPUT", "PLAN", "STATE"):
        monkeypatch.setattr(results, name, getattr(results, name))
    results.select_control_optimized_candidate()
    assert results.candidate is operator
    assert results.OUTPUT == operator.HERE / "final_results_r1"
    output = tmp_path / "results"
    output.mkdir()
    monkeypatch.setattr(results, "OUTPUT", output)
    monkeypatch.setattr(results, "STATE", output / "state.json")
    monkeypatch.setattr(results.causal, "load", lambda _path: {"analysis_source": {"clean": True}})
    monkeypatch.setattr(results, "git_source_state", lambda _path: {"clean": True})
    monkeypatch.setattr(results.causal.reservation, "process", lambda _pid: {"pid": 123})
    launches = []
    monkeypatch.setattr(
        results.subprocess, "Popen",
        lambda command, **_kwargs: launches.append(command) or SimpleNamespace(pid=123),
    )
    results.queue()
    assert launches[0][-2:] == ["--watch", "--control-optimized"]


def test_explicit_serving_checkout_does_not_require_post_exec_waiter_arguments(tmp_path):
    serving = tmp_path / "qualified-serving"
    assert results.qualified_serving_checkout({"argv": []}, {"serving_checkout": str(serving)}) == serving


def test_historical_result_observers_keep_the_waiter_checkout_fallback(tmp_path):
    entrypoint = tmp_path / "historical-serving/scripts/operator.py"
    assert results.qualified_serving_checkout({"argv": ["python", str(entrypoint)]}, {}) == entrypoint.parents[1]


@pytest.mark.parametrize("live,registered,reason", (
    (False, False, "engine source tree or CPU evidence changed"),
    (True, False, "engine source tree or CPU evidence changed"),
    (False, True, "engine source tree or CPU evidence changed"),
    (False, False, "another registration failure"),
))
def test_registration_retry_requires_matching_failure_and_actual_owned_exit(
    monkeypatch, tmp_path, live, registered, reason,
):
    failed = tmp_path / "failed"
    (failed / "final_results_r1").mkdir(parents=True)
    for path in (failed / "resource_waiter.state.json", failed / "final_results_r1/watcher.state.json"):
        path.write_text(json.dumps({"status": "stopped_preserve_failure", "process": {"pid": 123}}))
    (failed / "registration_result.json").write_text(json.dumps({"returncode": 1, "stderr": reason}))
    old_registration = tmp_path / "workflow_original73_host400_control_optimized_20261103_r1.registration.json"
    if registered:
        old_registration.write_text("{}")
    monkeypatch.setattr(operator, "FAILED_ATTEMPT", failed)
    monkeypatch.setattr(operator, "OPS", tmp_path)
    monkeypatch.setattr(operator.causal.reservation, "alive", lambda _handle: live)
    if live or registered or reason != "engine source tree or CPU evidence changed":
        with pytest.raises(ValueError):
            operator.verify_failed_attempt()
    else:
        operator.verify_failed_attempt()
