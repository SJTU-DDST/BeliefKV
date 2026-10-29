from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from experiments.reports.tokencake_radix_session_20261028 import defer_gpu_components as queue


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def cpu(tmp_path):
    report = tmp_path / "cpu.xml"
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for index in range(742):
        ET.SubElement(suite, "testcase", classname="queue.fixture", name=str(index))
    ET.ElementTree(root).write(report)
    sources = write(tmp_path / "sources.json", {})
    prior = write(tmp_path / "prior.json", {})
    document = {
        "totals": {"tests": 742, "errors": 0, "failures": 0, "skipped": 0},
        "unique_test_identities": 742,
        "all_prior_726_identities_retained": True,
        "new_native_session_gpu_scenes_executed": False,
        "sources_unchanged_during_execution": True,
        "returncode": 0,
        "source_manifest": {"path": str(sources), "sha256": queue.sha256_file(sources)},
        "report": {"path": str(report), "sha256": queue.sha256_file(report)},
        "prior_inputs_sha256": {str(prior): queue.sha256_file(prior)},
    }
    return NS(
        path=write(tmp_path / "cpu.json", document), document=document, report=report,
        sources=sources, prior=prior,
    )


@pytest.mark.parametrize("fault", (
    None, "report_bytes", "source_bytes", "prior_bytes", "failed_case",
    "duplicate_case", "returncode", "unbound_sources", "claimed_gpu",
))
def test_cpu_prerequisite_requires_original_passing_evidence(cpu, fault):
    document = deepcopy(cpu.document)
    if fault in ("report_bytes", "source_bytes", "prior_bytes"):
        getattr(cpu, {
            "report_bytes": "report", "source_bytes": "sources", "prior_bytes": "prior",
        }[fault]).write_text("changed")
    elif fault in ("failed_case", "duplicate_case"):
        root = ET.parse(cpu.report).getroot()
        cases = list(root.iter("testcase"))
        if fault == "failed_case":
            ET.SubElement(cases[0], "failure")
        else:
            cases[0].set("name", cases[1].get("name"))
        ET.ElementTree(root).write(cpu.report)
        document["report"]["sha256"] = queue.sha256_file(cpu.report)
    elif fault is not None:
        document[{
            "returncode": "returncode", "unbound_sources": "sources_unchanged_during_execution",
            "claimed_gpu": "new_native_session_gpu_scenes_executed",
        }[fault]] = 1 if fault == "returncode" else fault == "claimed_gpu"
    write(cpu.path, document)
    if fault is None:
        assert queue.verify_cpu_evidence(cpu.path) == cpu.document
    else:
        with pytest.raises(ValueError):
            queue.verify_cpu_evidence(cpu.path)


@pytest.mark.parametrize("fault", (None, "missing_boundary", "remaining", "live"))
def test_actual_predecessor_cleanup_is_required(tmp_path, monkeypatch, fault):
    previous = {"run_root": str(tmp_path), "order": ["kvflow_observed_graph"]}
    document = {
        "identities": [{"pid": 41, "start_ticks": "100"}],
        "finished_at": "observed", "remaining_processes": [],
    }
    if fault == "missing_boundary":
        del document["finished_at"]
    elif fault == "remaining":
        document["remaining_processes"] = document["identities"]
    write(tmp_path / "kvflow_observed_graph/owned_process_cleanup.json", document)
    monkeypatch.setattr(queue.controls.comparison, "verify_sources", Mock())
    monkeypatch.setattr(queue.controls.reservation, "alive", lambda row: fault == "live")
    if fault is None:
        queue.verify_predecessor_cleanup(previous)
    else:
        with pytest.raises(ValueError, match="cleanup|remains alive"):
            queue.verify_predecessor_cleanup(previous)


def test_gpu_child_is_created_only_after_actual_predecessor_exit_and_resources(
    tmp_path, monkeypatch,
):
    predecessor = write(tmp_path / "previous.json", {})
    path = write(tmp_path / "queue.json", {
        "after_registration": str(predecessor), "engine_checkout": "engine",
        "native_patch": "patch", "engine_tree": "tree", "native_patch_sha256": "sha",
    })
    run = tmp_path / "run"
    events, identities, checks = [], [], iter((False, False, True))
    monkeypatch.setattr(queue, "RUN", run)
    monkeypatch.setattr(queue, "APPLICATION", tmp_path / "frozen")
    monkeypatch.setattr(queue, "verify", lambda plan: events.append("sources"))
    monkeypatch.setattr(queue, "verify_predecessor_cleanup", lambda plan: events.append("cleanup"))
    monkeypatch.setattr(queue.time, "sleep", lambda duration: None)
    monkeypatch.setattr(queue.controls.fresh, "check_hold", lambda *a, **k: None)
    monkeypatch.setattr(queue.signal, "signal", lambda *args: None)

    def ready(plan):
        result = next(checks)
        events.append("previous_exit" if result else "previous_running")
        return result

    monkeypatch.setattr(queue.controls, "predecessor_ready", ready)
    monkeypatch.setattr(
        queue.controls, "wait_for_resources", lambda *a, **k: events.append("fresh_resources"),
    )
    monkeypatch.setattr(queue, "prefix_qualification_environment", lambda *args: {})

    def spawn(command, **kwargs):
        events.append("gpu_child")
        identity = {"pid": 42, "start_ticks": "200", "argv": command}
        identities.append(identity)
        write(run / "components/summary.json", {"owned_gpu_pid": 42})
        return NS(pid=42, poll=lambda: 0, wait=lambda **kwargs: 0)

    monkeypatch.setattr(queue.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        queue.controls.reservation, "process",
        lambda pid: identities[-1] if pid == 42 else {"pid": pid},
    )
    monkeypatch.setattr(queue.controls, "owned_processes", lambda rows: [])
    cleanup = Mock()
    monkeypatch.setattr(queue.controls, "cleanup", cleanup)
    monkeypatch.setattr(queue, "gpu_processes", lambda: [])
    validated = Mock()
    monkeypatch.setattr(queue, "validate_report", validated)
    queue.watch(path)
    assert events.index("previous_exit") < events.index("cleanup") < events.index("fresh_resources")
    assert events.index("fresh_resources") < events.index("gpu_child")
    state = json.loads(path.with_suffix(".state.json").read_bytes())
    assert state["status"] == "native_session_components_qualified"
    assert state["performance_accepted"] is False
    validated.assert_called_once_with(
        {"owned_gpu_pid": 42}, engine_tree="tree", patch_sha256="sha", root=queue.APPLICATION,
    )
    cleanup.assert_called_once()


def test_failed_predecessor_never_reaches_resource_or_gpu_checks(tmp_path, monkeypatch):
    previous = write(tmp_path / "previous.json", {})
    path = write(tmp_path / "queue.json", {"after_registration": str(previous)})
    monkeypatch.setattr(queue, "verify", lambda plan: None)
    monkeypatch.setattr(queue.signal, "signal", lambda *args: None)
    monkeypatch.setattr(queue.controls.fresh, "check_hold", lambda *a, **k: None)
    monkeypatch.setattr(
        queue.controls, "predecessor_ready", Mock(side_effect=ValueError("predecessor failed")),
    )
    resources, gpu = Mock(), Mock()
    monkeypatch.setattr(queue.controls, "wait_for_resources", resources)
    monkeypatch.setattr(queue.subprocess, "Popen", gpu)
    queue.watch(path)
    resources.assert_not_called()
    gpu.assert_not_called()
    state = json.loads(path.with_suffix(".state.json").read_bytes())
    assert state["status"] == "stopped_for_investigation"
    assert "predecessor failed" in state["error"]
