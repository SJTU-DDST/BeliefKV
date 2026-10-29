from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from experiments.reports.hbm_dispatch_20261029 import queue_candidate as queue
from scripts import run_causal_comparison as causal


def template():
    options = {
        "--registration": "/experiment/ops/old.registration.json",
        "--after-registration": "/experiment/ops/original.registration.json",
        "--experiment-config": "/frozen/old.json",
        "--engine-sources": "/frozen/native.json",
        "--source-arm": "/data/original", "--gpu-smoke": "/data/smoke.json",
        "--sampling-calibration": "/data/sampling.json", "--max-running-requests": "24",
        "--workflow-arrivals": "/data/arrivals.json", "--tool-duration-overlay": "/data/tools.json",
        "--engine": "/engine", "--engine-report": "/data/native.xml", "--arms": "full",
    }
    return ["python", "/frozen/run_causal_comparison.py", *(
        value for pair in options.items() for value in pair
    )]


def test_policy_change_uses_resource_queue_without_relaxing_same_policy_resume(tmp_path):
    before = template()
    command = queue.build_command(before, tmp_path)
    assert "--after-registration" not in command
    assert "--after-registration" in before
    for flag in (
        "--source-arm", "--gpu-smoke", "--sampling-calibration", "--max-running-requests",
        "--workflow-arrivals", "--tool-duration-overlay", "--engine", "--engine-report",
    ):
        assert command[command.index(flag) + 1] == before[before.index(flag) + 1]
    assert command[command.index("--registration") + 1].endswith("_r2.registration.json")
    assert Path(command[command.index("--engine-sources") + 1]) == tmp_path / "native_sources.json"
    assert command[command.index("--experiment-config") + 1].endswith("hbm_dispatch_20261029/full.json")


def test_no_register_or_review_process_before_actual_predecessor_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "complete_work", lambda _: None)
    monkeypatch.setattr(queue.subprocess, "run", lambda *args, **kwargs: pytest.fail("premature launch"))
    assert not queue.launch_if_complete({"predecessor": str(tmp_path / "previous.json")}, tmp_path)
    assert not list(tmp_path.iterdir())


def test_failed_predecessor_cannot_release_candidate(monkeypatch, tmp_path):
    def failed(_):
        raise ValueError("incomplete work or owned exit")

    monkeypatch.setattr(queue, "complete_work", failed)
    monkeypatch.setattr(queue.subprocess, "run", lambda *args, **kwargs: pytest.fail("premature launch"))
    with pytest.raises(ValueError, match="incomplete work"):
        queue.launch_if_complete({"predecessor": str(tmp_path / "previous.json")}, tmp_path)


def test_successful_gate_registers_one_full_then_a_cpu_only_reviewer(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "complete_work", lambda _: {"exact": "work"})
    monkeypatch.setattr(queue, "verify", lambda _: None)
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return NS(returncode=0, stdout="registered", stderr="")

    monkeypatch.setattr(queue.subprocess, "run", run)
    plan = {
        "predecessor": str(tmp_path / "original.registration.json"),
        "candidate_registration": str(tmp_path / "candidate.registration.json"),
        "command": ["python", "run_causal_comparison.py", "--arms", "full"],
    }
    assert queue.launch_if_complete(plan, tmp_path)
    assert len(commands) == 2 and commands[0][0] == plan["command"]
    assert commands[0][1]["env"]["CUDA_VISIBLE_DEVICES"] == "99999"
    assert commands[1][0][1].endswith("review_after_candidate.py")
    assert (tmp_path / "candidate_entry.json").exists()
    assert (tmp_path / "reviewer_entry.json").exists()


def test_failed_registration_retains_failure_and_does_not_launch_reviewer(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "complete_work", lambda _: {"exact": "work"})
    monkeypatch.setattr(queue, "verify", lambda _: None)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return NS(returncode=1, stdout="", stderr="preserved registration refusal")

    monkeypatch.setattr(queue.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="registration refused"):
        queue.launch_if_complete({"predecessor": "prior.json", "command": ["register"]}, tmp_path)
    assert commands == [["register"]]
    assert causal.load(tmp_path / "candidate_entry.json")["returncode"] == 1
    assert not (tmp_path / "reviewer_entry.json").exists()
