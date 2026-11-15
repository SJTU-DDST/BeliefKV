import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

from scripts import run_tool_path_followup as runner


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def previous(tmp_path):
    owner = {"pid": 10, "start_ticks": "20", "argv": ["python", "previous.py"], "state": "S"}
    plan = {
        "previous_execution_plan": str(tmp_path / "previous/execution_plan.json"),
        "previous_memory_resident_owner": str(tmp_path / "previous/resident_owner.json"),
        "previous_registration": "capacity.reg", "cohort": "fixed18", "qualified_commit": "source",
        "stages": {
            "full": {"registration": "full.reg"},
            "oracle": {"registration": "tool.reg"},
        },
    }
    published = {
        "stages": {
            "oracle": {"registration": "capacity.reg"}, "full": {"registration": "full.reg"},
        },
        "cohort": "fixed18", "qualified_commit": "source",
    }
    reservation = NS(process=lambda pid: owner)
    return plan, published, owner, reservation


def test_live_dependency_waits_for_actual_execution_plan_publication(tmp_path, monkeypatch):
    plan, published, owner, reservation = previous(tmp_path)
    sleeps = []

    def publish(_seconds):
        sleeps.append(True)
        write(Path(plan["previous_execution_plan"]), published)

    monkeypatch.setattr(runner.time, "sleep", publish)
    result = runner.wait_previous(plan, tmp_path, owner, reservation, completed=False)
    assert result == published and sleeps == [True]
    wait = runner.load(tmp_path / "continuation_wait.json")
    assert wait["status"] == "waiting_previous_execution_plan_publication"
    assert wait["GPU_registration_created"] is False


def test_complete_review_also_waits_for_resident_owner_publication(tmp_path, monkeypatch):
    plan, published, owner, reservation = previous(tmp_path)
    write(Path(plan["previous_execution_plan"]), published)
    state = {
        "process": owner, "status": "oracle_complete_no_promising_gain", "measured_completed": 18,
    }
    write(Path(plan["previous_execution_plan"]).parent / "state.json", state)

    def publish(_seconds):
        write(Path(plan["previous_memory_resident_owner"]), {
            "status": "holding_memory_bridge_between_experiments", "owner": owner,
            "release_file": str(tmp_path / "not_released"),
        })

    monkeypatch.setattr(runner.time, "sleep", publish)
    assert runner.wait_previous(plan, tmp_path, owner, reservation, completed=True) == state


def test_missing_live_predecessor_is_terminal_without_new_registration(tmp_path):
    plan, _published, owner, reservation = previous(tmp_path)
    reservation.process = lambda pid: None
    with pytest.raises(ValueError, match="exited or changed identity"):
        runner.wait_previous(plan, tmp_path, owner, reservation, completed=False)
    assert not (tmp_path / "continuation_wait.json").exists()


def test_published_foreign_plan_is_rejected_before_ownership_transfer(tmp_path):
    plan, published, owner, reservation = previous(tmp_path)
    published["stages"]["oracle"]["registration"] = "foreign.reg"
    write(Path(plan["previous_execution_plan"]), published)
    with pytest.raises(ValueError, match="published execution plan changed"):
        runner.wait_previous(plan, tmp_path, owner, reservation, completed=False)


def test_actual_bootstrap_failure_is_preserved(tmp_path):
    plan, _published, owner, reservation = previous(tmp_path)
    write(Path(plan["previous_execution_plan"]).parent / "bootstrap_error.json", {"error": "failed"})
    with pytest.raises(ValueError, match="preceding launch failed"):
        runner.wait_previous(plan, tmp_path, owner, reservation, completed=False)


def test_real_registered_watcher_and_global_audit_are_wired_without_gpu(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[1]
        / "experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1"
        / "host400_registered_copy_pressure_r1/capacity_review_correction_r1"
        / "arrival_pressure_followup_r1/background100_mean60/continuous_campaign_20261113_r1"
        / "background133_execution_r1/coordinator_handoff_r2/maximal_benefit_campaign_startup_r2"
        / "tool_boundary_preemption_r1/bounded_schedule_r1/competitive_window_r1/coordinate.py"
    )
    if not source.exists():
        pytest.skip("this integration binds the actual qualified local handoff component")
    spec = importlib.util.spec_from_file_location("actual_tool_path_handoff_test", source)
    actual_handoff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actual_handoff)
    root = tmp_path / "qualified"
    registration = tmp_path / "tool.reg"
    write(registration, {"execution_checkout": str(root)})
    plan = {
        "inputs_sha256": {}, "operator": str(root), "cohort": "fixed18",
        "stages": {
            "full": {"registration": "full.reg"},
            "oracle": {"registration": str(registration)},
            "learned": {"configuration": "learned-reference"},
        },
    }
    write(tmp_path / "plan.json", plan)
    write(tmp_path / "launch_intent.json", {
        "inputs_sha256": {}, "adapter_source": str(source.parent), "previous_coordinator": {},
    })
    calls, waits = [], []
    monkeypatch.setattr(runner, "subprocess", NS(
        Popen=lambda command, **kwargs: calls.append((command, kwargs)) or NS(pid=99),
    ))
    monkeypatch.setattr(runner, "wait_previous", lambda *args, **kwargs: waits.append(kwargs["completed"]))
    campaign, handoff = NS(), NS()
    queue = NS(
        causal=NS(reservation=NS()), SOURCE=source.parent,
        base=NS(campaign=campaign, handoff=handoff, BaseWatcher=actual_handoff.RegisteredWatcher),
    )

    def watch():
        handoff.await_current(Path("/unused/previous.json"))

        def prepared():
            write(tmp_path / "preparation.json", {"inputs_sha256": {}})

        routed = queue.QueueWatcher(runner.subprocess, registration, source.parent / "supervisor.py", prepared)
        command = [
            sys.executable, str(root / "scripts/run_causal_comparison.py"),
            "--watch", str(registration),
        ]
        routed.Popen(command, env={"CUDA_VISIBLE_DEVICES": "99999"})
        with pytest.raises(ValueError, match="unexpected or duplicate"):
            routed.Popen(command)
        audit = [
            sys.executable, str(root / "scripts/audit_workflow_combination_choices.py"),
            "--registration", str(registration), "--control", "full.reg", "--cohort", "fixed18",
            "--complete", "--policy-kind", "oracle", "--model-work-reference", "learned-reference",
            "--output", "choices.json",
        ]
        campaign.subprocess.Popen(audit)

    queue.watch = watch
    monkeypatch.setattr(runner, "module_at", lambda *args: queue)
    runner.coordinate(tmp_path)
    assert waits == [False, True]
    assert campaign.LABELS == ("full", "oracle")
    assert campaign.stage_promising is runner.promising
    assert len(calls) == 2
    assert calls[0][0] == [sys.executable, str(Path(runner.__file__)), "--watch", str(registration)]
    assert calls[0][1]["env"][runner.DIRECTORY_ENV] == str(tmp_path)
    assert str(root / "scripts/audit_ranked_oracle_service.py") in calls[1][0]
    assert "--model-work-reference" not in calls[1][0]
    assert runner.load(tmp_path / "preparation.json")["global_tool_path_method"] is True
