#!/usr/bin/env python3
"""Finish the current ablation, run repaired Full, and try V108 policy if needed."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments import full_regression_recovery as recovery
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_frontier_ablation_after_campaign as full

fresh, comparison = full.fresh, full.comparison
qualified, reservation, load = full.qualified, full.reservation, full.load


def signal_owned(identity: dict, sig: int) -> None:
    if not reservation.alive(identity):
        raise ValueError("supervisor identity expired before signal")
    os.kill(identity["pid"], sig)


def live_children(identity: dict) -> list[dict]:
    path = Path(f"/proc/{identity['pid']}/task/{identity['pid']}/children")
    return [
        row for value in path.read_text().split()
        if (row := reservation.process(int(value))) and reservation.alive(row)
    ]


def verify_preserved(plan: dict, previous: dict) -> None:
    predecessor = load(Path(plan["predecessor_registration"]))
    run = Path(predecessor["run_root"])
    for label in plan["preserve_arms"]:
        outcome = fresh.completed_arm(
            load(run / label / "client_36/summary.json"),
            previous["original_exit_codes"][label], 36,
        )
        if outcome != previous["arm_outcomes"][label]:
            raise ValueError("completed predecessor outcome changed")
        if not (run / "timelines" / f"{label}.json.gz").is_file():
            raise ValueError("completed predecessor timeline is missing")
    if not (run / "module_comparison.json").is_file():
        raise ValueError("completed predecessor comparison is missing")


def retire_at_boundary(plan: dict) -> bool:
    path = Path(plan["predecessor_state"])
    previous = load(path)
    identity = plan["predecessor_watcher"]
    driver = previous.get("driver")
    if not recovery.handover_ready(
        plan, previous, driver_live=bool(driver and reservation.alive(driver)),
    ):
        if not reservation.alive(identity):
            raise ValueError("predecessor exited without complete preserved arms")
        return False
    verify_preserved(plan, previous)
    if reservation.alive(identity):
        signal_owned(identity, signal.SIGSTOP)
        try:
            previous = load(path)
            current = reservation.process(identity["pid"])
            driver = previous.get("driver")
            if (
                current is None or current["start_ticks"] != identity["start_ticks"]
                or current["state"] not in ("T", "t")
                or not recovery.handover_ready(
                    plan, previous, driver_live=bool(driver and reservation.alive(driver)),
                )
                or live_children(identity)
            ):
                return False
            signal_owned(identity, signal.SIGTERM)
        finally:
            if reservation.alive(identity):
                signal_owned(identity, signal.SIGCONT)
        deadline = time.monotonic() + 5.
        while reservation.alive(identity):
            if time.monotonic() >= deadline:
                raise ValueError("retired supervisor did not exit")
            time.sleep(.1)
    previous.update(
        status="superseded_after_completed_arm",
        superseded_by=plan["registration"], superseded_at=reservation.now().isoformat(),
        skipped_pending_arms=[
            name for name in previous["order"] if name not in previous["completed_arms"]
        ],
    )
    reservation.write_json(path, previous)
    return True


def register(args) -> None:
    if args.port == args.fallback_port or any(
        not 1 <= port <= 65535 for port in (args.port, args.fallback_port)
    ):
        raise ValueError("repair and fallback need distinct valid ports")
    if any(path.exists() for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root, args.fallback_root,
    )):
        raise FileExistsError("preserve previous recovery registrations and runs")
    reference, predecessor = load(args.reference), load(args.predecessor)
    fresh.verify(reference)
    fresh.verify(predecessor)
    fresh.check_hold(reference, released=True)
    reference_state = load(args.reference.with_suffix(".state.json"))
    if not full.reference_ready(
        {"reference_watcher": reference_state["watcher"]}, reference_state,
    ):
        raise ValueError("the historical native/JOIN campaign is still active")
    preceding_state = args.predecessor.with_suffix(".state.json")
    state = load(preceding_state)
    if (
        not reservation.alive(state["watcher"])
        or state.get("status") not in ("running_kv_only", "postprocessing_kv_only")
        or state.get("completed_arms") != ["schedule_only"]
    ):
        raise ValueError("register recovery behind the active KV-only arm")
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    if not {
        "scripts/run_full_regression_recovery.py",
        "beliefkv/experiments/full_regression_recovery.py",
        "tests/test_full_regression_recovery.py", "tests/test_tool_continuity.py",
    } <= sources.keys():
        raise ValueError("CPU qualification omits recovery behavior")
    fresh._require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("freeze recovery from qualified main")
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    checkout = comparison.EXPERIMENTS / ".worktrees" / (
        f"beliefkv-v126-recovery-frozen-{revision[:12]}-20261022"
    )
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    link = checkout / "third_party/sglang-v0.5.20"
    if not link.exists():
        link.symlink_to(reference["engine_checkout"], target_is_directory=True)
    if link.resolve() != Path(reference["engine_checkout"]).resolve():
        raise ValueError("recovery points to another engine")
    comparison.verify_cpu_snapshot(checkout, sources)
    inputs = {row["path"]: row["sha256"] for row in reference["external_inputs"]}
    for path in (
        args.reference, args.predecessor, args.cpu_report, args.cpu_sources,
        args.v108_candidate,
    ):
        inputs[str(path.resolve())] = sha256_file(path)
    base = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        "development_checkout": str(ROOT), "code_commit": revision,
        "execution_checkout": str(checkout), "engine_checkout": reference["engine_checkout"],
        "engine_tree": reference["engine_tree"],
        "source_sha256": sources, "cpu_report": str(args.cpu_report.resolve()),
        "passing_cpu_cases": count,
        "external_inputs": [{"path": name, "sha256": digest} for name, digest in sorted(inputs.items())],
        "hold_file": reference["hold_file"], "gpu_not_before": reference["gpu_not_before"],
        "reference_registration": str(args.reference.resolve()),
        "reference_state": str(args.reference.with_suffix(".state.json").resolve()),
        "reference_watcher": reference_state["watcher"], "reference_run": reference["run_root"],
        "order": ["full"], "performance_accepted": False,
    }
    if "engine_cpu_qualification" in reference:
        base["engine_cpu_qualification"] = reference["engine_cpu_qualification"]
    children = {}
    for label, root, port in (
        ("repaired_full", args.run_root, args.port),
        ("v108_policy", args.fallback_root, args.fallback_port),
    ):
        path = args.registration.with_name(args.registration.stem + f".{label}.json")
        if path.exists() or path.with_suffix(".state.json").exists():
            raise FileExistsError("preserve previous child registration")
        child = {
            **base, "run_root": str(root.resolve()), "port": port, "candidate_label": label,
            "environment": recovery.environment(reference, checkout, port, fallback=label == "v108_policy"),
        }
        fresh.verify(child)
        reservation.write_json(path, child)
        children[label] = str(path.resolve())
    plan = {
        **base, "registration": str(args.registration.resolve()), "children": children,
        "predecessor_registration": str(args.predecessor.resolve()),
        "predecessor_state": str(preceding_state.resolve()),
        "predecessor_watcher": state["watcher"], "preserve_arms": ["schedule_only", "kv_only"],
        "fallback_scope": (
            "V108 policy configuration on latest qualified code and engine, retaining "
            "CPU optimizations and seed21. Not an exact V108 binary/trajectory replay."
        ),
    }
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("xb") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_full_regression_recovery.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "children": children, "code_commit": revision, "performance_accepted": False,
    }, indent=2))


def evaluate(child_path: Path) -> dict:
    child, state = load(child_path), load(child_path.with_suffix(".state.json"))
    if state.get("status") != "collected_performance_review_pending":
        raise ValueError(f"Full collection failed: {state.get('status')}: {state.get('error')}")
    run, reference = Path(child["run_root"]), Path(child["reference_run"])
    candidate = child["candidate_label"]
    arms = {
        candidate: run / "full",
        "native_matched": reference / "native_matched",
        "join_priority_only": reference / "join_priority_only",
    }
    arguments = [sys.executable, str(ROOT / "scripts/compare_native_policy_runs.py")]
    for label, arm in arms.items():
        arguments.extend(("--arm", f"{label}={arm}"))
    subprocess.run(
        [*arguments, "--busy-window", "600:1800", "--output", str(run / "recovery_busy.json")],
        check=True, capture_output=True,
    )
    report = recovery.performance_gate(
        load(run / "internal_comparison.json"), load(run / "recovery_busy.json"),
        {name: load(arm / "client_36/summary.json") for name, arm in arms.items()}, candidate,
    )
    reservation.write_json(run / "recovery_gate.json", report)
    return report


def watch(registration: Path) -> None:
    plan, path = load(registration), registration.with_suffix(".state.json")
    state = {
        "status": "waiting_preserved_kv_only", "watcher": reservation.process(os.getpid()),
        "registration": str(registration), "performance_accepted": False,
    }
    with registration.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            next_verify = 0.
            while True:
                fresh.check_hold(plan, released=True)
                if time.monotonic() >= next_verify:
                    fresh.verify(plan)
                    next_verify = time.monotonic() + 15.
                state["checked_at"] = reservation.now().isoformat()
                reservation.write_json(path, state)
                if retire_at_boundary(plan):
                    break
                time.sleep(1)
            state["predecessor_retired_at"] = reservation.now().isoformat()
            for label in ("repaired_full", "v108_policy"):
                child = Path(plan["children"][label])
                state.update(status="running_" + label, active_registration=str(child))
                reservation.write_json(path, state)
                full.watch(child)
                report = evaluate(child)
                state[label] = report
                reservation.write_json(path, state)
                if label == "repaired_full" and not report["fallback_required"]:
                    break
            state.update(
                status="collected_performance_review_pending", finished_at=reservation.now().isoformat(),
                final_policy="v108_policy" if "v108_policy" in state else "repaired_full",
            )
        except Exception as error:
            state.update(
                status="stopped_for_investigation", error=repr(error),
                finished_at=reservation.now().isoformat(),
            )
        finally:
            reservation.write_json(path, state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--predecessor", type=Path)
    parser.add_argument("--v108-candidate", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--cpu-sources", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--fallback-root", type=Path)
    parser.add_argument("--port", type=int, default=18534)
    parser.add_argument("--fallback-port", type=int, default=18536)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        if any(value is None for name, value in vars(args).items()
               if name not in ("watch", "port", "fallback_port")):
            parser.error("registration requires reference, predecessor, V108 and CPU evidence")
        register(args)


if __name__ == "__main__":
    main()
