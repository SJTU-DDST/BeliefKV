"""Run only frozen Agentix byte checks after the failed TokenCake fully exited."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REGISTRATION = HERE / "gpu_native_consumers_r2/registration.json"
PLAN = json.loads(REGISTRATION.read_bytes())
APPLICATION = Path(PLAN["checkout"])
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.agentix_radix_sessions_20261028.queue_components import verify_inputs
from scripts import run_causal_comparison as controls
from scripts.qualify_agentix_pressure import validate_report as validate_pressure
from scripts.qualify_agentix_runtime import validate_swap
from scripts.run_agentx_baseline import prefix_qualification_environment


def main():
    run = HERE / "gpu_native_consumers_r3"
    if run.exists():
        raise FileExistsError("preserve every previous physical qualification attempt")
    previous = Path(PLAN["predecessor"])
    state = controls.load(previous.with_suffix(".state.json"))
    prior = controls.load(previous)
    cleanup_path = Path(prior["run_root"]) / "tokencake_paper_observed/owned_process_cleanup.json"
    cleanup = controls.load(cleanup_path)
    old_queue = controls.load(HERE / "gpu_native_consumers_r2/summary.json")
    old_identity = controls.load(HERE / "gpu_native_consumers_r2/supervisor.json")
    identities = [
        *(state.get(name) for name in ("watcher", "server", "driver") if state.get(name)),
        *state.get("owned", ()), *cleanup["identities"], old_identity,
    ]
    if (
        state["status"] != "stopped_for_investigation" or state["completed_arms"]
        or state["original_exit_codes"] != {"tokencake_paper_observed": 1}
        or cleanup["remaining_processes"] or not cleanup.get("finished_at")
        or any(controls.reservation.alive(row) for row in identities)
        or old_queue["status"] != "failed" or old_queue.get("owned") != []
    ):
        raise ValueError("failed TokenCake or its stopped dependent waiter has not actually cleaned")
    verify_inputs(PLAN)
    controls.fresh.check_hold(PLAN, released=True)
    for ordinal in range(2):
        if not controls.qualified.resources(PLAN["port"])["ready"]:
            raise ValueError("independent Agentix byte-check resources are unavailable")
        if ordinal == 0:
            time.sleep(10.)
            controls.fresh.check_hold(PLAN, released=True)
    run.mkdir()
    inputs = (
        Path(__file__), REGISTRATION, previous, previous.with_suffix(".state.json"),
        cleanup_path, HERE / "gpu_native_consumers_r2/summary.json",
        ROOT / "experiments/reports/tokencake_native_hooks_20261028/formal_native_hooks_failure.json",
    )
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "independent_small_gpu_byte_checks_after_failed_predecessor_cleanup_not_formal_baseline",
        "registered_frozen_application": str(APPLICATION),
        "application_commit": PLAN["application_commit"], "engine_tree": PLAN["engine_tree"],
        "engine_checkout": PLAN["engine_checkout"],
        "inputs_sha256": {str(path): sha256_file(path) for path in inputs},
        "original_failed_queue_preserved": True, "performance_accepted": False,
        "reports": {}, "whole_model_or_formal_baseline_qualified": False,
    }
    owned = []
    try:
        for kind in ("swap", "pressure"):
            for sessions in (False, True):
                controls.fresh.check_hold(PLAN, released=True)
                verify_inputs(PLAN)
                name = kind + ("_native_sessions" if sessions else "_plain")
                print("Starting " + name, flush=True)
                command = [
                    sys.executable, str(APPLICATION / f"scripts/qualify_agentix_{kind}.py"),
                    *(["run"] if kind == "pressure" else []),
                    "--engine-checkout", PLAN["engine_checkout"], "--canonical-patch",
                    str(APPLICATION / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
                    "--output-dir", str(run / name),
                    *(["--native-radix-sessions"] if sessions else []),
                ]
                with (run / (name + ".log")).open("x") as log:
                    child = subprocess.Popen(
                        command, cwd=APPLICATION, stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                        env=prefix_qualification_environment(PLAN, os.environ),
                    )
                    identity = controls.reservation.process(child.pid)
                    if identity is None:
                        raise ValueError("actual Agentix byte child has no process identity")
                    owned.append(identity)
                    deadline = time.monotonic() + 180.
                    while child.poll() is None:
                        controls.fresh.check_hold(PLAN, released=True)
                        owned[:] = controls.owned_processes(owned)
                        if time.monotonic() >= deadline:
                            raise TimeoutError("independent Agentix byte scene exceeded 180 seconds")
                        time.sleep(.5)
                    if child.wait():
                        raise ValueError("independent Agentix byte scene failed; preserve original attempt")
                path = run / name / "summary.json"
                report = controls.load(path)
                if kind == "swap":
                    validate_swap(report, PLAN, require_native_sessions=sessions)
                else:
                    validate_pressure(
                        report, engine_tree=PLAN["engine_tree"],
                        patch_sha256=PLAN["native_patch_sha256"], require_native_sessions=sessions,
                    )
                record["reports"][name] = {
                    "path": str(path), "sha256": sha256_file(path),
                    "spawned_process": identity, "actual_exit_verified": True,
                }
                print("Passed " + name, flush=True)
        verify_inputs(PLAN)
        if any(sha256_file(Path(path)) != digest for path, digest in record["inputs_sha256"].items()):
            raise ValueError("independent GPU qualifier input changed")
        record["status"] = "four_native_consumer_gpu_checks_qualified"
    except BaseException as error:
        record.update(status="failed", error=repr(error))
    finally:
        try:
            controls.cleanup(run, owned, record["status"])
        except BaseException as error:
            record.update(status="failed", cleanup_error=repr(error))
        record["finished_at"] = controls.reservation.now().isoformat()
        controls.reservation.write_json(run / "summary.json", record)
    print(json.dumps({
        "status": record["status"], "completed_scenes": list(record["reports"]),
        "error": record.get("error"),
    }, indent=2))
    if record["status"] != "four_native_consumer_gpu_checks_qualified":
        raise ValueError("independent Agentix byte qualification failed")


if __name__ == "__main__":
    main()
