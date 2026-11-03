import json
from types import SimpleNamespace

import pytest

from scripts import start_workflow_benefit_only_candidate as operator


def waiter(monkeypatch, tmp_path):
    here, old = tmp_path / "candidate", tmp_path / "ratio"
    here.mkdir()
    old.mkdir()
    monkeypatch.setattr(operator, "HERE", here)
    monkeypatch.setattr(operator, "OLD", old)
    monkeypatch.setattr(operator, "REG", tmp_path / "registration.json")
    monkeypatch.setattr(operator, "checked_qualification", lambda: {"inputs_sha256": {}})
    monkeypatch.setattr(operator, "check_reservation", lambda: None)
    monkeypatch.setattr(operator.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(operator.os.path, "lexists", lambda _path: False)
    flags = ("registration", "run-root", "port", "experiment-config", "cpu-report", "cpu-sources", "engine-sources")
    command = ["python", "previous.py"]
    for flag in flags:
        command.extend(("--" + flag, "previous"))
    (old / "launch.json").write_text(json.dumps({"command": command}))
    return here


def test_waiter_never_registers_during_busy_or_single_idle_observation(monkeypatch, tmp_path):
    here = waiter(monkeypatch, tmp_path)
    samples, launched = [], []
    resources = iter((False, False, True, True))

    def observe(_port):
        ready = next(resources)
        samples.append(ready)
        return {"ready": ready, "compute_processes": [] if ready else [["foreign", "123"]]}

    def register(command, **_kwargs):
        launched.append((list(samples), command))
        return SimpleNamespace(returncode=1, stdout="", stderr="preserve explicit registration failure")

    monkeypatch.setattr(operator.causal.qualified, "resources", observe)
    monkeypatch.setattr(operator.subprocess, "run", register)
    operator.wait_and_start()
    assert len(launched) == 1
    assert launched[0][0] == [False, False, True, True]
    state = json.loads((here / "resource_waiter.state.json").read_bytes())
    assert state["status"] == "stopped_preserve_failure"
    assert "registration refusal" in state["error"]
    assert json.loads((here / "registration_result.json").read_bytes())["returncode"] == 1


@pytest.mark.parametrize("reason", ("explicit_stop", "new_reservation"))
def test_waiter_preserves_stop_or_new_gpu_reservation_before_registration(monkeypatch, tmp_path, reason):
    here = waiter(monkeypatch, tmp_path)
    if reason == "explicit_stop":
        (here / "STOP").touch()
    else:
        def held():
            raise RuntimeError("new reservation")

        monkeypatch.setattr(operator, "check_reservation", held)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("stopped waiters cannot allocate a GPU or register a server")

    monkeypatch.setattr(operator.causal.qualified, "resources", forbidden)
    monkeypatch.setattr(operator.subprocess, "run", forbidden)
    operator.wait_and_start()
    state = json.loads((here / "resource_waiter.state.json").read_bytes())
    assert state["status"] == "stopped_preserve_failure"
    assert not (here / "launch.json").exists()
