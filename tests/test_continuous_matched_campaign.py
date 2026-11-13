import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import run_continuous_matched_campaign as campaign


@pytest.mark.parametrize("arm", campaign.LABELS)
def test_actual_replay_recognizes_each_method_and_requires_live_owners(arm):
    owners = {name: {"pid": pid} for pid, name in enumerate(("watcher", "server", "driver"), 1)}
    state = {"status": "running_" + arm, **owners}
    assert campaign.serving_ready(state, arm, lambda _: True)
    assert not campaign.serving_ready(state, arm, lambda owner: owner["pid"] != 3)
    assert not campaign.serving_ready({**state, "driver": None}, arm, lambda _: True)
    assert not campaign.serving_ready({**state, "status": "starting_" + arm}, arm, lambda _: True)


def test_baselines_keep_all_common_traffic_and_sampling_flags(tmp_path):
    stage = {
        "registration": "/ops/workflow_original100_full_attempt.registration.json",
        "run_root": "/raw/workflow_original100_full_attempt", "configuration": "full.json",
        "command": [
            "python", "frozen/run_causal_comparison.py", "--registration",
            "/ops/workflow_original100_full_attempt.registration.json",
            "--run-root", "/raw/workflow_original100_full_attempt",
            "--port", "19041", "--experiment-config", "full.json", "--arms", "full",
            "--gpu-smoke", "passed-smoke.json", "--sampling-calibration", "passed-sampling.json",
            "--workflow-arrivals", "fixed-arrivals.json", "--tool-duration-overlay", "fixed-tools.json",
        ],
        "existing_watcher": {"pid": 1},
    }
    result = campaign.baseline_stage(stage, "native_matched", "native.json", tmp_path, 19045)
    assert "existing_watcher" not in result
    assert result["registration"] == "/ops/workflow_original100_native_matched_attempt.registration.json"
    assert result["run_root"] == "/raw/workflow_original100_native_matched_attempt"
    for flag in ("--gpu-smoke", "--sampling-calibration", "--workflow-arrivals", "--tool-duration-overlay"):
        assert result["command"][result["command"].index(flag) + 1] == stage["command"][stage["command"].index(flag) + 1]
    assert result["command"][result["command"].index("--arms") + 1] == "native_matched"
    assert stage["existing_watcher"] == {"pid": 1}


def test_registration_resolves_paths_before_switching_to_frozen_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    campaign.write(Path("execution.json"), {"operator": "/a/different/frozen/checkout"})
    args = SimpleNamespace(
        execution_plan=Path("execution.json"), baselines_preparation=Path("baselines.json"),
        output_dir=Path("new-campaign"), device_state=Path("device.json"),
        device_owner_file=Path("owner.json"),
    )

    def stop_before_side_effects(operator):
        assert operator == Path("/a/different/frozen/checkout")
        raise RuntimeError("paths inspected before registration")

    monkeypatch.setattr(campaign, "services", stop_before_side_effects)
    with pytest.raises(RuntimeError, match="paths inspected"):
        campaign.register(args)
    for name, value in vars(args).items():
        assert value.is_absolute(), name
        assert value.parent == tmp_path


def test_popen_receipt_may_precede_exec_but_start_identity_and_watch_plan_must_match():
    owner = {"pid": 123, "start_ticks": "456", "argv": []}
    actual = {**owner, "argv": ["python", "/frozen/run.py", "--watch", "/ops/plan.json"]}
    assert campaign.verify_serving_owner(owner, actual, Path("/ops/plan.json")) == actual
    with pytest.raises(ValueError, match="identity changed"):
        campaign.verify_serving_owner(owner, {**actual, "start_ticks": "789"}, Path("/ops/plan.json"))
    with pytest.raises(ValueError, match="registered plan"):
        campaign.verify_serving_owner(owner, actual, Path("/ops/different.json"))


@pytest.mark.parametrize("mismatch", (False, True))
def test_five_stage_handoff_reuses_full_keeps_negative_diagnostics_and_checks_work(
    tmp_path, monkeypatch, mismatch,
):
    operator = tmp_path / "frozen"
    scripts = operator / "scripts"
    scripts.mkdir(parents=True)
    for name in ("stop_after_measured_cohort.py", "verify_measured_execution.py"):
        (scripts / name).write_text("# frozen qualified source\n")
    old = tmp_path / "old"
    old.mkdir()
    cohort = tmp_path / "cohort.json"
    campaign.write(cohort, {"measured_workflow_ids": ["fixed-a", "fixed-b"]})
    events, living, owners = [], set(), {}
    next_pid = 100

    def identity(pid):
        return {"pid": pid, "start_ticks": str(pid), "argv": ["frozen", str(pid)]}

    def install_stage(label):
        nonlocal next_pid
        next_pid += 1
        registration = Path(stages[label]["registration"])
        owner = {**identity(next_pid), "argv": ["frozen", "--watch", str(registration)]}
        owners[label] = owner
        living.update((owner["pid"], owner["pid"] + 1000, owner["pid"] + 2000))
        arm = "full" if label in campaign.LABELS[:3] else label
        campaign.write(registration, {
            "execution_checkout": str(operator), "code_commit": "frozen",
            "passing_cpu_cases": 8687, "order": [arm],
        })
        campaign.write(registration.with_suffix(".state.json"), {
            "status": "running_" + arm, "watcher": owner,
            "server": identity(owner["pid"] + 1000), "driver": identity(owner["pid"] + 2000),
        })
        return owner

    stages = {}
    for label in campaign.LABELS:
        configuration = tmp_path / (label + ".json")
        campaign.write(configuration, {
            "order": ["full" if label in campaign.LABELS[:3] else label],
            "comparison_contract": {},
        })
        stages[label] = {
            "registration": str(tmp_path / (label + ".registration.json")),
            "configuration": str(configuration), "review": str(tmp_path / (label + "_review")),
            "run_root": str(tmp_path / ("raw_" + label)), "port": 19041,
            "command": ["register", label],
        }
    stages["full"]["existing_watcher"] = install_stage("full")
    plan = {
        "operator": str(operator), "inputs_sha256": {}, "cohort": str(cohort),
        "stages": stages, "minimum_model_work_p80_p20": 2.6,
        "device_state": str(tmp_path / "device.json"),
        "device_owner_file": str(tmp_path / "owner.json"),
        "previous_execution_plan": str(old / "plan.json"),
        "previous_coordinator": identity(1),
        "configurations": {label: stages[label]["configuration"] for label in campaign.LABELS[:3]},
    }
    plan_path = tmp_path / "plan.json"
    campaign.write(plan_path, plan)
    alive = lambda owner: bool(owner) and owner["pid"] in living
    reservation = SimpleNamespace(process=identity, alive=alive)
    causal = SimpleNamespace(
        reservation=reservation, qualified=SimpleNamespace(resources=lambda _: {"ready": True}),
    )
    ranking = SimpleNamespace(released=lambda: None)
    finish = SimpleNamespace(verify_pins=lambda _: None, wait_observer_publication=lambda *args: None)
    monkeypatch.setattr(campaign, "services", lambda _: (causal, ranking, finish))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(campaign.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(campaign.signal, "signal", lambda *args: None)
    monkeypatch.setitem(sys.modules, "scripts.review_matched_work_ranking", SimpleNamespace(
        evaluate_pair_cohorts=lambda *args, **kwargs: {"diagnostic": "negative_or_positive_retained"},
    ))

    def run(arguments, **kwargs):
        assert kwargs["cwd"] == str(operator)
        label = arguments[1]
        assert events[-1] == ("verified", campaign.LABELS[campaign.LABELS.index(label) - 1])
        events.append(("registered", label))
        launched_owner = install_stage(label)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"watcher": {**launched_owner, "argv": []}}), stderr="",
        )

    class Child:
        def __init__(self, arguments, **kwargs):
            nonlocal next_pid
            next_pid += 1
            self.pid, self.returncode = next_pid, 0
            if "--watch" in arguments:
                registration = Path(arguments[-1])
                observer = campaign.load(registration)
                label = next(
                    label for label in campaign.LABELS
                    if observer["comparison"] == stages[label]["registration"]
                )
                living.difference_update((
                    owners[label]["pid"], owners[label]["pid"] + 1000, owners[label]["pid"] + 2000,
                ))
                campaign.write(registration.with_suffix(".state.json"), {
                    "process": identity(self.pid), "status": "measured_cohort_review_complete",
                    "accepted_for_oracle_diagnostic": False, "failed_checks": ["supply_diagnostic"],
                })
                review = Path(observer["analysis_output"])
                review.mkdir()
                campaign.write(review / "cohort.json", {"fixed_cohort": True})
            elif "-c" in arguments:
                label = Path(arguments[-1]).name.removesuffix("_execution_proof.json")
                work = {"request": "changed" if mismatch and label == "oracle" else "same"}
                campaign.write(Path(arguments[-1]), {"work": work})
                events.append(("verified", label))

        def poll(self):
            return self.returncode

    monkeypatch.setattr(campaign.subprocess, "run", run)
    monkeypatch.setattr(campaign.subprocess, "Popen", Child)
    campaign.watch(plan_path)
    state = campaign.load(tmp_path / "state.json")
    assert not state["goal_complete"]
    assert state["workload_diagnostics"]["full"]["accepted_for_oracle_diagnostic"] is False
    if mismatch:
        assert state["status"] == "stopped_preserve_five_method_comparison"
        assert "complete measured native work changed" in state["error"]
        assert state["completed_stages"] == ["full"]
        assert [label for kind, label in events if kind == "registered"] == ["oracle"]
    else:
        assert state["status"] == "complete_five_method_comparison"
        assert state["completed_stages"] == list(campaign.LABELS)
        assert [label for kind, label in events if kind == "registered"] == list(campaign.LABELS[1:])
        assert campaign.load(tmp_path / "coordinator_handoff.json")["Full_supervisor_preserved"] == owners["full"]
