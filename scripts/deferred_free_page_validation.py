#!/usr/bin/env python3
"""Queue the qualified model/replay chain behind the user's one-time GPU hold."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import qualify_session_native_model as model

controls = model.controls
OPERATOR = "scripts/deferred_free_page_validation.py"
TESTS = "tests/test_deferred_free_page_validation.py"
MODEL = "session_model_and_components"
POSITION = "position_smoke_then_full_thunderagent"


def load(path):
    return json.loads(Path(path).read_bytes())


def option(argv, name):
    if argv.count(name) != 1 or argv.index(name) + 1 == len(argv):
        raise ValueError("queued command lacks a unique argument: " + name)
    return Path(argv[argv.index(name) + 1])


def deadline(hold):
    value = datetime.fromisoformat(hold["gpu_not_before"])
    if value.tzinfo is None or value < model.components.NOT_BEFORE:
        raise ValueError("queued GPU deadline is earlier than user authorization")
    return value


def gate(plan, *, at=None, stopped=False):
    if stopped:
        raise ValueError("deferred validation received a stop request")
    if controls.fresh.PAUSE.exists():
        raise controls.fresh.HoldChanged("new user pause")
    hold_path = Path(plan["hold"]["path"])
    hold = load(hold_path)
    if (
        sha256_file(hold_path) != plan["hold"]["sha256"]
        or hold.get("automatic_resume_allowed") is not True
        or deadline(hold).isoformat() != plan["not_before"]
    ):
        raise controls.fresh.HoldChanged("reservation changed after queue registration")
    if sha256_file(ROOT / OPERATOR) != plan["operator_sha256"]:
        raise ValueError("queued operator source changed")
    return (at or controls.reservation.now()) >= deadline(hold)


def verify_inputs(plan):
    for row in plan["inputs"]:
        if sha256_file(Path(row["path"])) != row["sha256"]:
            raise ValueError("queued input changed: " + row["path"])
    if git_source_state(ROOT) != plan["main_source"]:
        raise ValueError("main changed after queue registration")
    prepared = load(plan["prepared"])
    checkout = Path(prepared["application_git_root"])
    if git_source_state(checkout) != prepared["application_git"]:
        raise ValueError("prepared execution source changed")
    controls.comparison.verify_sources({
        "execution_checkout": str(checkout),
        "source_sha256": prepared["source_sha256"], "external_inputs": [],
        "engine_checkout": prepared["native_engine_root"],
        "engine_tree": prepared["native_engine"]["canonical_patch"]["actual_tree"],
    })
    if any(
        sha256_file(ROOT / name) != prepared["source_sha256"][name]
        for name in model.SOURCES
    ):
        raise ValueError("model launcher differs from qualified prepared sources")


def fresh_outputs(commands, *, model_pending):
    paths = [option(commands[POSITION], name) for name in (
        "--registration", "--run-root", "--comparison-registration", "--comparison-root",
    )]
    if model_pending:
        paths.append(option(commands[MODEL], "--output-dir"))
    for path in paths:
        related = (path, path.with_suffix(".state.json"), path.with_suffix(".log")) if path.suffix == ".json" else (path,)
        if any(item.exists() for item in related):
            raise FileExistsError("preserve existing deferred experiment: " + str(path))


def model_evidence(output):
    registration = load(output / "registration.json")
    summary = load(output / "summary.json")
    if (
        summary.get("status") != "session_native_model_qualified"
        or summary.get("model_numerical_equivalence_verified") is not True
        or summary.get("actual_greedy_tokens_equal") is not True
        or summary.get("whole_population_qualified") is not False
        or summary.get("performance_accepted") is not False
    ):
        raise ValueError("actual session model qualification did not pass")
    model.check_registration(registration)
    model.validate_model(summary["results"], registration["configuration"])
    model.components.validate_report(
        load(output / "native_components/summary.json"),
        engine_tree=registration["engine_tree"], patch_sha256=registration["native_patch_sha256"],
    )
    cleanup = load(output / "owned_process_cleanup.json")
    if cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at"):
        raise ValueError("model qualification did not release its owned processes")
    return {
        "registration_sha256": sha256_file(output / "registration.json"),
        "summary_sha256": sha256_file(output / "summary.json"),
        "components_sha256": sha256_file(output / "native_components/summary.json"),
        "cleanup_sha256": sha256_file(output / "owned_process_cleanup.json"),
        "actual_model_and_components_revalidated": True,
    }


def handoff(commands, prepared):
    position = option(commands[POSITION], "--registration")
    comparison = option(commands[POSITION], "--comparison-registration")
    if not position.with_suffix(".state.json").is_file():
        return None
    position_state = load(position.with_suffix(".state.json"))
    if position_state.get("status") == "stopped_for_investigation":
        raise ValueError("new position supervisor stopped; preserve its evidence")
    candidates = [(position, position_state, "comparison_order", "run_position_replay_calibration.py")]
    if comparison.with_suffix(".state.json").is_file():
        candidates.append((
            comparison, load(comparison.with_suffix(".state.json")), "order", "run_causal_comparison.py",
        ))
    for path, state, order_key, script in reversed(candidates):
        if not path.is_file():
            continue
        registered = load(path)
        if (
            registered.get("code_commit") != prepared["application_git"]["commit"]
            or registered.get("engine_tree") != prepared["native_engine"]["canonical_patch"]["actual_tree"]
            or registered.get(order_key) != ["full", "thunderagent"]
            or registered.get("source_sha256") != prepared["source_sha256"]
        ):
            raise ValueError("handoff does not match the qualified source and control order")
        identity = state.get("watcher")
        if not identity or not controls.reservation.alive(identity):
            continue
        current = controls.reservation.process(identity["pid"])
        argv = current["argv"]
        expected = str(Path(registered["execution_checkout"]) / "scripts" / script)
        if (
            expected not in argv or "--watch" not in argv
            or argv.index("--watch") + 1 >= len(argv)
            or argv[argv.index("--watch") + 1] != str(path)
        ):
            raise ValueError("handoff supervisor identity differs from its registration")
        controls.comparison.verify_sources(registered)
        return {"registration": str(path), "watcher": current, "state": state["status"]}
    return None


def published_watcher(log_path, argv):
    receipt = load(log_path)
    path = option(argv, "--registration")
    if receipt.get("registration") != str(path):
        raise ValueError("registration child published another supervisor")
    identity = receipt.get("watcher")
    if not identity or not controls.reservation.alive(identity):
        return None
    current = controls.reservation.process(identity["pid"])
    observed = current["argv"]
    if (
        observed != identity.get("argv") or "--watch" not in observed
        or observed.index("--watch") + 1 >= len(observed)
        or observed[observed.index("--watch") + 1] != str(path)
        or not any(Path(item).name == "run_position_replay_calibration.py" for item in observed)
    ):
        raise ValueError("published watcher changed identity")
    return current


def watch(path):
    plan = load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve previous deferred execution state")
    state = {
        "status": "preflight", "watcher": controls.reservation.process(os.getpid()),
        "not_before": plan["not_before"], "performance_accepted": False,
    }
    stopped, owned, accepted_handoff = False, [], False

    def stop(*_args):
        nonlocal stopped
        stopped = True

    def pulse():
        nonlocal owned
        ready = gate(plan, stopped=stopped)
        if owned:
            owned = controls.owned_processes(owned)
        state.update(checked_at=controls.reservation.now().isoformat(), owned=owned)
        controls.reservation.write_json(state_path, state)
        return ready

    def command(argv, label):
        if not pulse():
            raise controls.fresh.HoldChanged("GPU command attempted before reservation release")
        log_path = path.with_suffix("." + label + ".log")
        with log_path.open("x") as log:
            child = subprocess.Popen(
                argv, cwd=ROOT,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        identity = controls.reservation.process(child.pid)
        if identity is None:
            raise ValueError("deferred child has no owned process identity")
        owned.append(identity)
        while child.poll() is None:
            pulse()
            time.sleep(.5)
        if child.wait():
            raise ValueError("deferred stage failed: " + label)
        if label == "position":
            # The registration process can exit before its detached child is
            # discovered by a process-tree scan. Retain its actual PID receipt.
            watcher = published_watcher(log_path, argv)
            if watcher is not None:
                owned.append(watcher)
        pulse()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            verify_inputs(plan)
            prepared = load(plan["prepared"])
            commands = plan["commands"]
            state["status"] = "waiting_user_reservation"
            while not pulse():
                time.sleep(10.)
            verify_inputs(plan)
            fresh_outputs(commands, model_pending=True)
            model.check_hold()
            state["status"] = "running_session_model_qualification"
            command(commands[MODEL], "model")
            state["model_evidence"] = model_evidence(option(commands[MODEL], "--output-dir"))
            if owned:
                raise ValueError("model command exited with live owned descendants")
            verify_inputs(plan)
            fresh_outputs(commands, model_pending=False)
            state["status"] = "registering_position_and_controls"
            command(commands[POSITION], "position")
            until = time.monotonic() + 30.
            while time.monotonic() < until:
                pulse()
                result = handoff(commands, prepared)
                if result is not None:
                    state.update(status="handed_to_qualified_supervisor", handoff=result)
                    owned.clear()
                    accepted_handoff = True
                    break
                time.sleep(.5)
            if not accepted_handoff:
                raise ValueError("new calibration did not publish a live verified supervisor")
        except BaseException as error:
            state.update(status="stopped_for_investigation", error_type=type(error).__name__, error=str(error))
        finally:
            if owned and not accepted_handoff:
                cleanup = path.parent / (path.stem + ".cleanup")
                try:
                    cleanup.mkdir(exist_ok=False)
                    controls.cleanup(cleanup, owned, "deferred_validation_stop")
                except BaseException as error:
                    state["cleanup_error"] = repr(error)
            state["finished_at"] = controls.reservation.now().isoformat()
            controls.reservation.write_json(state_path, state)
    return accepted_handoff


def register(args):
    for path in (args.registration, args.registration.with_suffix(".state.json"), args.registration.with_suffix(".log")):
        if path.exists():
            raise FileExistsError("preserve existing deferred queue records")
    prepared = load(args.prepared)
    certificate = load(args.cpu_sources)
    required = {OPERATOR, TESTS}
    if (
        not required <= certificate["source_sha256"].keys()
        or any(sha256_file(ROOT / name) != certificate["source_sha256"][name] for name in required)
        or certificate["report_sha256"] != sha256_file(args.cpu_report)
    ):
        raise ValueError("deferred operator CPU source binding is missing or stale")
    checks = ET.parse(args.cpu_report).getroot()
    if any(list(checks.iter(tag)) for tag in ("failure", "error", "skipped")) or not any(
        row.get("classname") == "tests.test_deferred_free_page_validation" for row in checks.iter("testcase")
    ):
        raise ValueError("deferred operator requires its actual passing CPU tests")
    root = Path(prepared["source_hashes"]["path"])
    checkout = Path(prepared["snapshot"]["path"])
    snapshot = load(checkout)
    prepared["application_git_root"] = snapshot["checkout"]
    prepared["native_engine_root"] = snapshot["engine_checkout"]
    # Add no permissions to the immutable recipe; the queue only supplies its root paths.
    commands = prepared["execution_recipe"]["commands"]
    if set(commands) != {MODEL, POSITION} or prepared["execution_recipe"]["comparison_order"] != ["full", "thunderagent"]:
        raise ValueError("deferred recipe has unexpected stages or controls")
    if (
        Path(commands[MODEL][1]) != ROOT / "scripts/qualify_session_native_model.py"
        or Path(commands[POSITION][1]) != Path(snapshot["checkout"]) / "scripts/run_position_replay_calibration.py"
        or load(root) != prepared["source_sha256"]
    ):
        raise ValueError("deferred commands differ from the prepared source")
    fresh_outputs(commands, model_pending=True)
    source = git_source_state(ROOT)
    if source["dirty"]:
        raise ValueError("commit completed operator sources before queueing")
    hold_path = Path(prepared["gpu_hold"]["path"])
    hold = load(hold_path)
    if hold.get("automatic_resume_allowed") is not True or controls.fresh.PAUSE.exists():
        raise controls.fresh.HoldChanged("automatic deferred execution is paused")
    snapshot_path = args.registration.with_suffix(".prepared.json")
    if snapshot_path.exists():
        raise FileExistsError("preserve previous deferred prepared binding")
    inputs = [args.prepared, args.cpu_report, args.cpu_sources, root, checkout]
    for name in ("checks", "native_proof_rebinding", "experiment_configuration"):
        inputs.append(Path(prepared[name]["path"]))
    controls.reservation.write_json(snapshot_path, prepared)
    inputs.append(snapshot_path)
    for argv in commands.values():
        for name in (
            "--reference", "--source-registration", "--cpu-report", "--cpu-sources",
            "--engine-report", "--engine-sources", "--experiment-config", "--canonical-patch",
        ):
            if name in argv:
                inputs.append(option(argv, name))
    reference = load(option(commands[MODEL], "--reference"))
    sealed = {str(path.resolve()): sha256_file(path) for path in inputs}
    for row in reference["external_inputs"]:
        if sha256_file(Path(row["path"])) != row["sha256"]:
            raise ValueError("source comparison input changed before queue registration")
        sealed[row["path"]] = row["sha256"]
    plan = {
        "status": "cpu_queue_registered", "main_source": source,
        "operator_sha256": sha256_file(ROOT / OPERATOR),
        "prepared": str(snapshot_path.resolve()), "commands": commands,
        "not_before": deadline(hold).isoformat(),
        "hold": {"path": str(hold_path), "sha256": sha256_file(hold_path)},
        "inputs": [{"path": path, "sha256": value} for path, value in sorted(sealed.items())],
        "performance_accepted": False,
    }
    verify_inputs(plan)
    controls.reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(ROOT / OPERATOR), "--watch", str(args.registration.resolve())],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({"registration": str(args.registration), "watcher": controls.reservation.process(child.pid),
                      "not_before": plan["not_before"], "gpu_experiment_started": False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    for name in ("prepared", "cpu-report", "cpu-sources", "registration"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.watch:
        raise SystemExit(0 if watch(args.watch.resolve()) else 1)
    if any(getattr(args, name) is None for name in ("prepared", "cpu_report", "cpu_sources", "registration")):
        parser.error("queue registration requires prepared sources, CPU evidence and fresh outputs")
    register(args)


if __name__ == "__main__":
    main()
