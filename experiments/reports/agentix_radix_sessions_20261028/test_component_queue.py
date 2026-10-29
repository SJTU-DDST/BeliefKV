"""CPU-only boundaries for the queued Agentix component operator."""

import json
from pathlib import Path

import pytest

from experiments.reports.agentix_radix_sessions_20261028 import queue_components as queue


def test_waiting_predecessor_never_queries_or_allocates_gpu(tmp_path, monkeypatch):
    class EndObservation(Exception):
        pass

    registration = tmp_path / "registration.json"
    registration.write_text(json.dumps({"output_dir": str(tmp_path)}))
    monkeypatch.setattr(queue, "verify_inputs", lambda _plan: None)
    monkeypatch.setattr(queue, "predecessor_ready", lambda _plan: False)
    monkeypatch.setattr(queue.controls.fresh, "check_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(queue.controls, "owned_processes", lambda rows: rows)
    monkeypatch.setattr(queue.controls, "cleanup", lambda *_args: None)
    monkeypatch.setattr(queue.signal, "signal", lambda *_args: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("live predecessor forbids GPU queries and child startup")

    def end(_duration):
        raise EndObservation("bounded CPU observation")

    monkeypatch.setattr(queue.controls.qualified, "resources", forbidden)
    monkeypatch.setattr(queue.subprocess, "Popen", forbidden)
    monkeypatch.setattr(queue.time, "sleep", end)
    queue.watch(registration)
    summary = json.loads((tmp_path / "summary.json").read_bytes())
    assert summary["status"] == "failed"
    assert "EndObservation" in summary["error"]
    assert summary["performance_accepted"] is False


def test_live_predecessor_cannot_be_released_by_cleanup_file_alone(monkeypatch):
    monkeypatch.setattr(queue, "predecessor_ready", lambda _plan: False)
    monkeypatch.setattr(queue.controls, "load", lambda *_: pytest.fail("do not inspect terminal data"))
    with pytest.raises(ValueError, match="still running"):
        queue.verify_predecessor_cleanup({})


def test_predecessor_keeps_exact_registered_contract(tmp_path, monkeypatch):
    previous = {"source_plan": {"requests": 3674}, "engine_tree": "tokencake", "order": ["paper"]}
    path = tmp_path / "previous.registration.json"
    path.write_text(json.dumps(previous))
    observed = []
    monkeypatch.setattr(
        queue.controls, "predecessor_ready",
        lambda plan: observed.append(plan) or False,
    )
    assert not queue.predecessor_ready({"predecessor": str(path)})
    assert observed == [{**previous, "after_registration": str(path)}]


def test_source_change_is_rejected_before_native_inspection(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_text("changed")
    monkeypatch.setattr(queue.controls.comparison, "verify_cpu_snapshot", lambda *_: None)
    monkeypatch.setattr(queue.controls, "load", lambda *_: {})
    monkeypatch.setattr(queue, "git_source_state", lambda *_args, **_kwargs: pytest.fail("native not yet"))
    with pytest.raises(ValueError, match="input or predecessor changed"):
        queue.verify_inputs({
            "checkout": str(queue.ROOT), "cpu_manifest": "manifest",
            "inputs_sha256": {str(source): "old"},
        })
