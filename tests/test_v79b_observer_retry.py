from copy import deepcopy
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import start_v79b_checkpoint_observer_retry_20261016 as retry
from scripts.observe_owned_scheduler_numa import operations_directory
from scripts.start_v79b_checkpoint_observer_retry_20261016 import (
    FROZEN_OBSERVER, OBSERVER, observer_command, require_import_failure,
    retry_environment,
)


def test_operations_helpers_are_resolved_from_the_run_not_the_worktree(tmp_path):
    operations = tmp_path / "ops"
    operations.mkdir()
    (operations / "server_reservation.py").touch()
    run = tmp_path / "BeliefKV/experiments/raw/run"
    assert operations_directory(run) == operations


def test_missing_operations_helpers_are_an_explicit_error(tmp_path):
    with pytest.raises(ValueError, match="operations helpers"):
        operations_directory(tmp_path / "BeliefKV/experiments/raw/run")


def test_only_frozen_placement_observer_is_redirected():
    command = ["python", str(FROZEN_OBSERVER), "--arm", "native"]
    before = deepcopy(command)
    assert observer_command(command) == ["python", str(OBSERVER), *command[2:]]
    assert command == before
    unrelated = ["python", "serving.py"]
    assert observer_command(unrelated) is unrelated


def test_retry_changes_only_native_output_directory(tmp_path):
    original = {
        "RUN_ROOT": "/old/native", "PORT": "18483", "ROOT_COUNT": "156",
        "WORKFLOW_ARRIVAL_SCHEDULE": "/old/ab_plan.json",
    }
    candidate = {**original, "RUN_ROOT": str(tmp_path / "native")}
    assert retry_environment(original, candidate, tmp_path) == candidate
    for key, value in (
        ("PORT", "18484"), ("ROOT_COUNT", "108"), ("NEW_POLICY", "1"),
        ("WORKFLOW_ARRIVAL_SCHEDULE", "/retry/ab_plan.json"),
    ):
        with pytest.raises(ValueError, match="only the fresh output"):
            retry_environment(original, {**candidate, key: value}, tmp_path)


def test_configured_retry_preserves_the_registered_arrival_plan(monkeypatch, tmp_path):
    previous = tmp_path / "original"
    module = SimpleNamespace(
        RUN=previous, REGISTRATION=previous / "registration.json",
        subprocess=subprocess,
    )

    def environment(plan):
        return {
            "RUN_ROOT": str(module.RUN / "native"),
            "WORKFLOW_ARRIVAL_SCHEDULE": str(module.RUN / "ab_plan.json"),
        }

    original = {"environment": environment({})}
    original_registration = module.REGISTRATION
    module.collection_environment = environment
    module.load = lambda path: original if path == original_registration else {"tasks": 156}

    def verify(registration):
        assert registration["environment"] == module.collection_environment({})

    module.verify_source = verify
    monkeypatch.setattr(retry, "frozen_helper", lambda app, name: module)
    monkeypatch.setattr(retry, "RUN", tmp_path / "retry")
    configured, _, _ = retry.configure()
    expected = {**original["environment"], "RUN_ROOT": str(tmp_path / "retry/native")}
    assert configured.collection_environment({}) == expected
    configured.verify_source({"environment": expected, "retry_inputs": []})
    assert configured.RUN == Path(tmp_path / "retry")


def failure():
    return {
        "status": "stopped_for_diagnosis",
        "error": "CalledProcessError: observe_owned_scheduler_numa.py",
        "supervisor": {"pid": 1}, "driver": {"pid": 2},
    }


def test_original_observer_import_failure_is_classified_without_relabeling():
    original = failure()
    before = deepcopy(original)
    require_import_failure(
        original, "ModuleNotFoundError: No module named 'server_reservation'",
        lambda identity: False,
    )
    assert original == before


@pytest.mark.parametrize("changed", ["running", "startup", "other_error", "live", "other_log"])
def test_unclassified_or_live_original_run_cannot_be_retried(changed):
    original = failure()
    log = "ModuleNotFoundError: No module named 'server_reservation'"
    if changed == "running":
        original["status"] = "running_native_checkpoint_diagnostic"
    elif changed == "startup":
        original["startup"] = {}
    elif changed == "other_error":
        original["error"] = "native collection failed"
    elif changed == "other_log":
        log = "different exception"
    with pytest.raises(ValueError, match="not the exited observer"):
        require_import_failure(original, log, lambda identity: changed == "live")
