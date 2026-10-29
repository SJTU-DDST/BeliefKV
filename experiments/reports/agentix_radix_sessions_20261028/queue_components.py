"""Queue the four native-consumer GPU checks after complete TokenCake cleanup."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.agentix_engine import verify_qualification_engine
from scripts import run_causal_comparison as controls
from scripts.qualify_agentix_pressure import validate_report as validate_pressure
from scripts.qualify_agentix_runtime import validate_swap
from scripts.run_agentx_baseline import freeze, prefix_qualification_environment
from scripts.qualify_agentix_locobench_model import completed_predecessor


TREE = "c489dbb85265efce25622a52abfd698e9199688d"


def verify_inputs(plan):
    if Path(plan["checkout"]).resolve() != ROOT:
        raise ValueError("GPU supervisor is not using its frozen application")
    controls.comparison.verify_cpu_snapshot(ROOT, controls.load(Path(plan["cpu_manifest"])))
    for path, expected in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != expected:
            raise ValueError("Agentix component input or predecessor changed")
    native = git_source_state(
        Path(plan["engine_checkout"]),
        canonical_patch=ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if (
        not native["matches_worktree"] or native["actual_tree"] != plan["engine_tree"]
        or native["sha256"] != plan["native_patch_sha256"]
    ):
        raise ValueError("Agentix component native tree or canonical patch changed")
    verify_qualification_engine(Path(plan["engine_checkout"]), native["actual_tree"])


def predecessor_ready(plan):
    path = Path(plan["predecessor"])
    if plan.get("failed_predecessor_skipped_by_user"):
        return completed_predecessor(path, allow_failed=True)
    previous = controls.load(path)
    return controls.predecessor_ready({**previous, "after_registration": str(path)})


def verify_predecessor_cleanup(plan):
    if not predecessor_ready(plan):
        raise ValueError("TokenCake supervisor or owned children are still running")
    if plan.get("failed_predecessor_skipped_by_user"):
        return
    path = Path(plan["predecessor"])
    previous = controls.load(path)
    controls.predecessor_work({**previous, "after_registration": str(path)})
    arm = Path(previous["run_root"]) / "tokencake_paper_observed"
    cleanup = controls.load(arm / "owned_process_cleanup.json")
    drain = controls.load(arm / "telemetry_drain.json")
    idle = drain["drained_status"]
    if (
        cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at")
        or any(controls.reservation.alive(row) for row in cleanup["identities"])
        or not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        or any(idle.get(name) != 0 for name in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
            "failed_records", "dropped_records",
        ))
        or idle.get("writer_error") is not None
    ):
        raise ValueError("complete TokenCake has not drained and actually cleaned up")


def start(args):
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError("preserve every earlier Agentix component attempt")
    cpu = controls.load(args.cpu_evidence)
    if (
        cpu["totals"].get("tests", 0) < 762
        or any(cpu["totals"].get(name) != 0 for name in ("errors", "failures", "skipped"))
        or cpu["all_prior_752_identities_retained"] is not True
    ):
        raise ValueError("source-bound native-consumer CPU evidence is incomplete")
    for name in ("source_manifest", "report"):
        if sha256_file(Path(cpu[name]["path"])) != cpu[name]["sha256"]:
            raise ValueError("Agentix CPU evidence changed")
    predecessor = args.predecessor.resolve()
    previous = controls.load(predecessor)
    controls.fresh.check_hold(previous, released=True)
    frozen = freeze(args.engine.resolve(), args.native_patch.resolve())
    verify_qualification_engine(Path(frozen["engine_checkout"]), frozen["engine_tree"])
    application = Path(frozen["checkout"])
    controls.comparison.verify_cpu_snapshot(
        application, controls.load(Path(cpu["source_manifest"]["path"])),
    )
    relative = Path(__file__).relative_to(ROOT)
    output.mkdir(parents=True)
    inputs = (
        args.cpu_evidence.resolve(), Path(cpu["source_manifest"]["path"]),
        Path(cpu["report"]["path"]), predecessor, args.native_patch.resolve(),
        application / relative,
    )
    plan = {
        **frozen, "registered_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output), "predecessor": str(predecessor),
        "hold_file": previous["hold_file"], "port": 18706,
        "reference_environment": previous["reference_environment"],
        "cpu_manifest": cpu["source_manifest"]["path"],
        "inputs_sha256": {str(path): sha256_file(path) for path in inputs},
        "scope": "four_small_pool_gpu_checks_only_not_model_or_formal_baseline",
        "performance_accepted": False,
        "failed_predecessor_skipped_by_user": args.allow_failed_predecessor,
    }
    registration = output / "registration.json"
    controls.reservation.write_json(registration, plan)
    with (output / "supervisor.log").open("x") as log:
        process = subprocess.Popen(
            [sys.executable, str(application / relative), "watch", "--registration", str(registration)],
            cwd=application, env={**os.environ, "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    identity = controls.reservation.process(process.pid)
    if identity is None:
        raise ValueError("Agentix component waiter has no owned process identity")
    controls.reservation.write_json(output / "supervisor.json", identity)
    print(json.dumps({
        "status": "queued_after_complete_tokencake", "registration": str(registration),
        "supervisor": identity, "GPU_experiment_started": False,
    }, indent=2))


def watch(path):
    plan = controls.load(path)
    output, identities, stopped = Path(plan["output_dir"]), [], False
    state = {
        "status": "waiting_predecessor_exit", "performance_accepted": False,
        "registered_frozen_application": plan.get("checkout"),
        "application_commit": plan.get("application_commit"),
        "engine_checkout": plan.get("engine_checkout"), "engine_tree": plan.get("engine_tree"),
    }

    def stop(*_args):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise ValueError("Agentix component supervisor received a stop request")
        controls.fresh.check_hold(plan, released=True)
        identities[:] = controls.owned_processes(identities)
        state.update(checked_at=controls.reservation.now().isoformat(), owned=identities)
        controls.reservation.write_json(output / "state.json", state)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (output / "supervisor.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            verify_inputs(plan)
            while not predecessor_ready(plan):
                pulse()
                time.sleep(5.)
            verify_predecessor_cleanup(plan)
            state["status"] = "waiting_idle_gpu"
            while True:
                pulse()
                if controls.qualified.resources(plan["port"])["ready"]:
                    time.sleep(10.)
                    pulse()
                    if controls.qualified.resources(plan["port"])["ready"]:
                        break
                time.sleep(5.)
            verify_inputs(plan)
            reports = {}
            for kind in ("swap", "pressure"):
                for sessions in (False, True):
                    name = kind + ("_native_sessions" if sessions else "_plain")
                    pulse()
                    state["status"] = "qualifying_" + name
                    command = [
                        sys.executable, str(ROOT / f"scripts/qualify_agentix_{kind}.py"),
                        *(["run"] if kind == "pressure" else []),
                        "--engine-checkout", plan["engine_checkout"], "--canonical-patch",
                        str(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
                        "--output-dir", str(output / name),
                        *(["--native-radix-sessions"] if sessions else []),
                    ]
                    with (output / (name + ".log")).open("x") as log:
                        child = subprocess.Popen(
                            command, cwd=ROOT, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                            env=prefix_qualification_environment(plan, os.environ),
                        )
                        identity = controls.reservation.process(child.pid)
                        if identity is None:
                            raise ValueError("Agentix component child has no process identity")
                        identities.append(identity)
                        deadline = time.monotonic() + 180.
                        while child.poll() is None:
                            pulse()
                            if time.monotonic() >= deadline:
                                raise TimeoutError("bounded Agentix component exceeded 180 seconds")
                            time.sleep(.5)
                        if child.wait():
                            raise ValueError("Agentix component failed; retain this exact attempt")
                    report_path = output / name / "summary.json"
                    report = controls.load(report_path)
                    if kind == "swap":
                        validate_swap(report, plan, require_native_sessions=sessions)
                    else:
                        validate_pressure(
                            report, engine_tree=plan["engine_tree"], patch_sha256=plan["native_patch_sha256"],
                            require_native_sessions=sessions,
                        )
                    reports[name] = {
                        "path": str(report_path), "sha256": sha256_file(report_path),
                        "spawned_process": identity, "actual_exit_verified": True,
                    }
                    state["completed_scenes"] = reports.copy()
                    verify_inputs(plan)
            state.update(
                status="four_native_consumer_gpu_checks_qualified",
                reports=reports, whole_model_or_formal_baseline_qualified=False,
            )
        except BaseException as error:
            state.update(status="failed", error=repr(error))
        finally:
            try:
                controls.cleanup(output, identities, state["status"])
            except BaseException as error:
                state.update(status="failed", cleanup_error=repr(error))
            state["finished_at"] = controls.reservation.now().isoformat()
            controls.reservation.write_json(output / "summary.json", state)
            controls.reservation.write_json(output / "state.json", state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("start")
    for name in ("output-dir", "cpu-evidence", "predecessor", "engine", "native-patch"):
        launch.add_argument("--" + name, required=True, type=Path)
    launch.add_argument("--allow-failed-predecessor", action="store_true")
    monitor = commands.add_parser("watch")
    monitor.add_argument("--registration", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "start":
        start(args)
    else:
        watch(args.registration)
