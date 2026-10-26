from copy import deepcopy
from datetime import datetime
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from scripts import deferred_free_page_validation as queue


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root, prepared_root = tmp_path / "main", tmp_path / "prepared"
    for base in (root, prepared_root):
        base.mkdir()
        (base / "model.py").write_text("same qualified model source\n")
    operator = root / queue.OPERATOR
    operator.parent.mkdir()
    operator.write_text("operator source fixture\n")
    tests = root / queue.TESTS
    tests.parent.mkdir()
    tests.write_text("operator test fixture\n")
    hold = write(tmp_path / "hold.json", {
        "gpu_not_before": "2026-10-27T11:00:00+08:00", "automatic_resume_allowed": True,
    })
    source = {"commit": "main", "dirty": False}
    prepared_source = {"commit": "prepared", "dirty": False}
    monkeypatch.setattr(queue, "ROOT", root)
    monkeypatch.setattr(queue.model, "SOURCES", ("model.py",))
    monkeypatch.setattr(queue.controls.fresh, "PAUSE", tmp_path / "pause")
    monkeypatch.setattr(
        queue, "git_source_state",
        lambda path: deepcopy(source if path == root else prepared_source),
    )
    monkeypatch.setattr(queue.controls.comparison, "verify_sources", Mock())
    hashes = {"model.py": queue.sha256_file(root / "model.py")}
    reference = write(tmp_path / "reference.json", {"external_inputs": []})
    position = [
        sys.executable, str(prepared_root / "scripts/run_position_replay_calibration.py"),
        "--registration", str(tmp_path / "position.json"), "--run-root", str(tmp_path / "position"),
        "--comparison-registration", str(tmp_path / "comparison.json"),
        "--comparison-root", str(tmp_path / "comparison"),
    ]
    commands = {
        queue.MODEL: [
            sys.executable, str(root / "scripts/qualify_session_native_model.py"), "run",
            "--reference", str(reference), "--output-dir", str(tmp_path / "model"),
        ],
        queue.POSITION: position,
    }
    prepared = {
        "application_git_root": str(prepared_root), "native_engine_root": str(tmp_path / "native"),
        "application_git": deepcopy(prepared_source), "native_engine": {"canonical_patch": {"actual_tree": "tree"}},
        "source_sha256": hashes,
        "source_hashes": {"path": str(write(tmp_path / "hashes.json", hashes))},
        "snapshot": {"path": str(write(tmp_path / "snapshot.json", {
            "checkout": str(prepared_root), "engine_checkout": str(tmp_path / "native"),
        }))},
        "gpu_hold": {"path": str(hold)},
        "execution_recipe": {"commands": commands, "comparison_order": ["full", "thunderagent"]},
    }
    for name in ("checks", "native_proof_rebinding", "experiment_configuration"):
        prepared[name] = {"path": str(write(tmp_path / (name + ".json"), {}))}
    prepared_path = write(tmp_path / "prepared.json", prepared)
    plan = {
        "hold": {"path": str(hold), "sha256": queue.sha256_file(hold)},
        "not_before": "2026-10-27T11:00:00+08:00",
        "operator_sha256": queue.sha256_file(operator), "main_source": deepcopy(source),
        "prepared": str(prepared_path), "commands": commands,
        "inputs": [{"path": str(prepared_path), "sha256": queue.sha256_file(prepared_path)}],
    }
    return NS(
        root=root, prepared_root=prepared_root, hold=hold, plan=plan,
        prepared=prepared, prepared_path=prepared_path, source=source,
        path=write(tmp_path / "queue.json", plan), tmp=tmp_path,
    )


def at(hour, minute=0):
    return datetime(2026, 10, 27, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_gate_never_queries_or_uses_gpu_before_the_authorized_time(fixture, monkeypatch):
    resources = Mock(side_effect=AssertionError("no resource or GPU query during the hold"))
    monkeypatch.setattr(queue.controls.qualified, "resources", resources)
    assert not queue.gate(fixture.plan, at=at(10, 59))
    assert queue.gate(fixture.plan, at=at(11))
    resources.assert_not_called()


@pytest.mark.parametrize("fault", ("extended", "disabled", "pause", "operator", "stop"))
def test_new_hold_cancellation_or_operator_edits_stop_the_queue(fixture, fault):
    if fault in ("extended", "disabled"):
        document = queue.load(fixture.hold)
        document["gpu_not_before" if fault == "extended" else "automatic_resume_allowed"] = (
            "2026-10-28T11:00:00+08:00" if fault == "extended" else False
        )
        write(fixture.hold, document)
    elif fault == "pause":
        queue.controls.fresh.PAUSE.touch()
    elif fault == "operator":
        (fixture.root / queue.OPERATOR).write_text("changed\n")
    with pytest.raises(ValueError):
        queue.gate(fixture.plan, at=at(12), stopped=fault == "stop")


@pytest.mark.parametrize("value", ("2026-10-27T10:00:00+08:00", "2026-10-27T11:00:00"))
def test_deadline_cannot_be_earlier_or_timezone_ambiguous(value):
    with pytest.raises(ValueError, match="authorization"):
        queue.deadline({"gpu_not_before": value})


@pytest.mark.parametrize("fault", (None, "input", "main", "prepared", "model_source"))
def test_full_source_proof_is_rechecked_before_gpu_and_handoff(fixture, fault):
    if fault == "input":
        fixture.prepared_path.write_text("{}")
    elif fault == "main":
        fixture.source["commit"] = "other"
    elif fault == "prepared":
        fixture.prepared["application_git"]["commit"] = "other"
        write(fixture.prepared_path, fixture.prepared)
        fixture.plan["inputs"][0]["sha256"] = queue.sha256_file(fixture.prepared_path)
    elif fault == "model_source":
        (fixture.root / "model.py").write_text("unqualified\n")
    if fault is None:
        queue.verify_inputs(fixture.plan)
        queue.controls.comparison.verify_sources.assert_called_once()
    else:
        with pytest.raises(ValueError, match="changed|differs"):
            queue.verify_inputs(fixture.plan)


@pytest.mark.parametrize("fault", (
    None, "status", "tokens", "numerics", "whole_population", "performance",
    "registration", "components", "model", "live_child", "missing_cleanup_boundary",
))
def test_only_actual_model_components_and_complete_owned_cleanup_allow_handoff(
    fixture, monkeypatch, fault,
):
    output = fixture.tmp / "model"
    summary = {
        "status": "session_native_model_qualified", "actual_greedy_tokens_equal": True,
        "model_numerical_equivalence_verified": True, "whole_population_qualified": False,
        "performance_accepted": False, "results": {"complete": True},
    }
    cleanup = {"remaining_processes": [], "finished_at": "observed"}
    if fault in ("status", "tokens", "numerics", "whole_population", "performance"):
        key = {
            "status": "status", "tokens": "actual_greedy_tokens_equal",
            "numerics": "model_numerical_equivalence_verified",
            "whole_population": "whole_population_qualified", "performance": "performance_accepted",
        }[fault]
        summary[key] = "failed" if fault == "status" else fault in ("whole_population", "performance")
    elif fault == "live_child":
        cleanup["remaining_processes"] = [{"pid": 77}]
    elif fault == "missing_cleanup_boundary":
        del cleanup["finished_at"]
    write(output / "registration.json", {
        "configuration": {}, "engine_tree": "tree", "native_patch_sha256": "patch",
    })
    write(output / "summary.json", summary)
    write(output / "native_components/summary.json", {})
    write(output / "owned_process_cleanup.json", cleanup)
    checks = {}
    for name, owner, field in (
        ("registration", queue.model, "check_registration"),
        ("model", queue.model, "validate_model"),
        ("components", queue.model.components, "validate_report"),
    ):
        checks[name] = Mock(side_effect=ValueError(name + " proof changed") if fault == name else None)
        monkeypatch.setattr(owner, field, checks[name])
    if fault is None:
        result = queue.model_evidence(output)
        assert result["actual_model_and_components_revalidated"] is True
        assert all(check.call_count == 1 for check in checks.values())
    else:
        with pytest.raises(ValueError):
            queue.model_evidence(output)


def position_handle(fixture, monkeypatch, *, comparison=False, fault=None):
    path = queue.option(fixture.plan["commands"][queue.POSITION], (
        "--comparison-registration" if comparison else "--registration"
    ))
    checkout = fixture.prepared_root / "new-frozen"
    script = "run_causal_comparison.py" if comparison else "run_position_replay_calibration.py"
    identity = {
        "pid": 30, "start_ticks": "300",
        "argv": [sys.executable, str(checkout / "scripts" / script), "--watch", str(path)],
    }
    registered = {
        "execution_checkout": str(checkout), "code_commit": "prepared",
        "engine_tree": "tree", "source_sha256": fixture.prepared["source_sha256"],
        "order" if comparison else "comparison_order": ["full", "thunderagent"],
    }
    if fault in ("code_commit", "engine_tree", "source_sha256"):
        registered[fault] = "wrong"
    elif fault == "order":
        registered["order" if comparison else "comparison_order"] = ["native_matched"]
    elif fault == "argv":
        identity["argv"][-1] = "/foreign/registration.json"
    elif fault == "missing_watch_argument":
        identity["argv"].pop()
    write(path, registered)
    write(path.with_suffix(".state.json"), {
        "status": "waiting_gpu_calibration", "watcher": identity,
    })
    monkeypatch.setattr(queue.controls.reservation, "alive", lambda _: fault != "dead")
    monkeypatch.setattr(queue.controls.reservation, "process", lambda _: identity)
    return path, identity


@pytest.mark.parametrize("fault", (
    None, "code_commit", "engine_tree", "source_sha256", "order", "argv",
    "missing_watch_argument", "dead",
))
def test_handoff_requires_a_live_bound_supervisor_and_the_original_control_order(
    fixture, monkeypatch, fault,
):
    path, identity = position_handle(fixture, monkeypatch, fault=fault)
    if fault is None:
        result = queue.handoff(fixture.plan["commands"], fixture.prepared)
        assert result["registration"] == str(path) and result["watcher"] == identity
    elif fault == "dead":
        assert queue.handoff(fixture.plan["commands"], fixture.prepared) is None
    else:
        with pytest.raises(ValueError):
            queue.handoff(fixture.plan["commands"], fixture.prepared)


def test_handoff_can_follow_a_position_supervisor_into_its_actual_comparison(fixture, monkeypatch):
    position_handle(fixture, monkeypatch)
    path, identity = position_handle(fixture, monkeypatch, comparison=True)
    assert queue.handoff(fixture.plan["commands"], fixture.prepared)["watcher"] == identity
    assert queue.handoff(fixture.plan["commands"], fixture.prepared)["registration"] == str(path)


def test_actual_registration_receipt_preserves_ownership_after_parent_exit(fixture, monkeypatch):
    path, identity = position_handle(fixture, monkeypatch)
    log = write(fixture.tmp / "position.log", {"registration": str(path), "watcher": identity})
    assert queue.published_watcher(log, fixture.plan["commands"][queue.POSITION]) == identity
    changed = deepcopy(identity)
    changed["argv"][-1] = "/foreign/registration.json"
    monkeypatch.setattr(queue.controls.reservation, "process", lambda _: changed)
    with pytest.raises(ValueError, match="identity"):
        queue.published_watcher(log, fixture.plan["commands"][queue.POSITION])


@pytest.mark.parametrize("fault", (
    None, "new_hold", "failed_model", "child_exit", "source_changed", "stop_after_position",
))
def test_cpu_wait_then_model_then_verified_handoff_without_early_gpu_work(
    fixture, monkeypatch, fault,
):
    clock, launches = [at(10, 59)], []
    monkeypatch.setattr(queue.controls.reservation, "now", lambda: clock[0])
    handlers = {}
    monkeypatch.setattr(queue.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr(queue.model, "check_hold", lambda: None)
    verified = Mock()
    if fault == "source_changed":
        verified.side_effect = (None, None, ValueError("main changed"))
    monkeypatch.setattr(queue, "verify_inputs", verified)
    monkeypatch.setattr(queue.controls, "owned_processes", lambda _rows: [])
    watcher = {
        "pid": 99, "start_ticks": "handoff",
        "argv": [sys.executable, str(fixture.prepared_root / "scripts/run_position_replay_calibration.py"),
                 "--watch", str(fixture.tmp / "position.json")],
    }
    monkeypatch.setattr(queue.controls.reservation, "process", lambda pid: (
        deepcopy(watcher) if pid == 99 else {"pid": pid, "start_ticks": "owned"}
    ))
    monkeypatch.setattr(queue.controls.reservation, "alive", lambda _: True)
    cleanup = Mock()
    monkeypatch.setattr(queue.controls, "cleanup", cleanup)
    evidence = Mock(
        return_value={"actual_model_and_components_revalidated": True},
        side_effect=ValueError("model evidence failed") if fault == "failed_model" else None,
    )
    monkeypatch.setattr(queue, "model_evidence", evidence)
    monkeypatch.setattr(queue, "handoff", lambda *_: {
        "watcher": {"pid": 99, "start_ticks": "handoff"}, "registration": "verified",
    })

    def sleep(_seconds):
        assert not launches
        if fault == "new_hold":
            write(fixture.hold, {"gpu_not_before": "2026-10-28T11:00:00+08:00"})
        clock[0] = at(11)

    monkeypatch.setattr(queue.time, "sleep", sleep)

    class Child:
        def __init__(self, argv, **kwargs):
            assert clock[0] >= at(11)
            assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "99999"
            assert kwargs["start_new_session"] is True
            self.pid = 100 + len(launches)
            launches.append(argv)
            if argv == fixture.plan["commands"][queue.POSITION]:
                kwargs["stdout"].write(json.dumps({
                    "registration": str(fixture.tmp / "position.json"), "watcher": watcher,
                }))
                if fault == "stop_after_position":
                    handlers[queue.signal.SIGTERM]()

        def poll(self):
            return 1 if fault == "child_exit" else 0

        def wait(self):
            return self.poll()

    monkeypatch.setattr(queue.subprocess, "Popen", Child)
    assert queue.watch(fixture.path) is (fault is None)
    state = queue.load(fixture.path.with_suffix(".state.json"))
    assert state["performance_accepted"] is False
    if fault is None:
        assert launches == [fixture.plan["commands"][queue.MODEL], fixture.plan["commands"][queue.POSITION]]
        assert state["status"] == "handed_to_qualified_supervisor"
        cleanup.assert_not_called()
    elif fault == "new_hold":
        assert not launches
    elif fault == "stop_after_position":
        assert len(launches) == 2
        assert any(row["pid"] == 99 for row in cleanup.call_args.args[1])
    else:
        assert len(launches) == 1
    if fault == "child_exit":
        assert cleanup.call_args.args[1] == [{"pid": 100, "start_ticks": "owned"}]


def test_registration_only_starts_a_cpu_waiter_and_preserves_the_prepared_recipe(fixture, monkeypatch):
    report = fixture.tmp / "operator.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_deferred_free_page_validation" '
        'name="verified"/></testsuite></testsuites>'
    )
    certificate = write(fixture.tmp / "operator_sources.json", {
        "source_sha256": {name: queue.sha256_file(fixture.root / name) for name in (queue.OPERATOR, queue.TESTS)},
        "report_sha256": queue.sha256_file(report),
    })
    previous = fixture.prepared_path.read_bytes()
    monkeypatch.setattr(queue.controls.reservation, "process", lambda pid: {"pid": pid})
    launched = Mock(return_value=NS(pid=101))
    monkeypatch.setattr(queue.subprocess, "Popen", launched)
    path = fixture.tmp / "new_queue.json"
    queue.register(NS(
        registration=path, prepared=fixture.prepared_path, cpu_report=report, cpu_sources=certificate,
    ))
    assert queue.load(path)["not_before"] == "2026-10-27T11:00:00+08:00"
    assert launched.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "99999"
    assert launched.call_args.args[0][-2:] == ["--watch", str(path)]
    assert fixture.prepared_path.read_bytes() == previous
    assert not (fixture.tmp / "model").exists()
