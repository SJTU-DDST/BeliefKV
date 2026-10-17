#!/usr/bin/env python3
"""Retry unchanged V78 in fresh output with a reactive-aware NUMA observer."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.resume_v78_after_complete_v77_20261016 import (
    V78_APP, WAIT_STATE, frozen_helper, write_new,
)

BASE = ROOT.parent
REPORTS = ROOT / "experiments/reports"
FAILED_STATE = BASE / "ops/v78_native_order_recovered_20261016.state.json"
FAILED_LOG = BASE / "ops/v78_native_order_recovered_20261016.log"
RUN = ROOT / "experiments/raw/qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order"
FAILURE = REPORTS / "v1325_v78_numa_observer_failure_20261016.json"
REGISTRATION = REPORTS / "v1326_v78b_native_order_retry_registration_20261016.json"
SUPERVISOR = REPORTS / "v1327_v78b_native_order_retry_supervisor_20261016.json"
STARTUP = REPORTS / "v1328_v78b_native_order_actual_startup_20261016.json"
FINAL = REPORTS / "v1329_v78b_native_order_completion_20261016.json"
PLACEMENT = REPORTS / "v1330_v78b_native_order_actual_placement_20261016.json"
CPU = REPORTS / "v1332_v78b_observer_retry_final_cpu_20261016.xml"
STATE = BASE / "ops/v78b_native_order_20261016.state.json"
LOG = BASE / "ops/v78b_native_order_20261016.log"
OBSERVER = ROOT / "scripts/observe_owned_scheduler_numa.py"
LEGACY_OBSERVER = BASE / "ops/observe_v55_numa_placement.py"


def retry_environment(original, candidate, run):
    expected = {**original, "RUN_ROOT": str(run)}
    if candidate != expected:
        raise ValueError("retry may change only the fresh output directory")
    return candidate


def observer_command(command):
    if len(command) > 1 and command[1] == str(LEGACY_OBSERVER):
        return [command[0], str(OBSERVER), *command[2:]]
    return command


def configure():
    module = frozen_helper(V78_APP, "v78_native_order")
    original_run, original_registration_path = module.RUN, module.REGISTRATION
    original_registration = module.load(original_registration_path)
    original_verify = module.verify_source
    original_verify(original_registration)
    module.RUN, module.REGISTRATION = RUN, REGISTRATION
    module.STATE, module.WAIT_STATE = STATE, WAIT_STATE
    module.STARTUP, module.FINAL, module.PLACEMENT = STARTUP, FINAL, PLACEMENT

    def verify(registration):
        try:
            module.RUN = original_run
            original_verify(original_registration)
        finally:
            module.RUN = RUN
        for item in registration["retry_inputs"]:
            if module.bound(Path(item["path"])) != item:
                raise ValueError("a retry source, plan or receipt changed")
        if module.load(RUN / "ab_plan.json") != module.load(original_run / "ab_plan.json"):
            raise ValueError("retry must retain the exact original plan")
        retry_environment(
            original_registration["environment"],
            module.collection_environment(
                module.load(RUN / "ab_plan.json"), module.load(module.REFERENCE / "ab_plan.json"),
            ), RUN,
        )
        if registration["environment"] != {
            **original_registration["environment"], "RUN_ROOT": str(RUN),
        }:
            raise ValueError("registered retry environment differs")

    module.verify_source = verify
    original_subprocess = module.subprocess

    def run(command, *args, **kwargs):
        return original_subprocess.run(observer_command(command), *args, **kwargs)

    module.subprocess = SimpleNamespace(**{**vars(original_subprocess), "run": run})
    original_write = module.write_json

    def write(path, payload):
        if path == STATE:
            payload = {**payload, "retry_registration": module.bound(REGISTRATION)}
        original_write(path, payload)

    module.write_json = write
    return module, original_registration_path, original_registration


def prepare():
    if any(path.exists() for path in (
        RUN, FAILURE, REGISTRATION, SUPERVISOR, STARTUP, FINAL, PLACEMENT, STATE, LOG,
    )):
        raise FileExistsError("preserve existing retry artifacts")
    module, original_path, original = configure()
    failed = module.load(FAILED_STATE)
    alive, _, _, _, _ = module.operations()
    if (
        failed.get("status") != "stopped_for_diagnosis" or "startup" in failed
        or str(LEGACY_OBSERVER) not in failed.get("error", "")
        or "invalid choice: 'reactive'" not in FAILED_LOG.read_text()
        or any(failed.get(key) and alive(failed[key]) for key in ("supervisor", "driver"))
    ):
        raise ValueError("not the diagnosed exited reactive-observer startup failure")
    plan = module.load(Path(original["plan"]["path"]))
    environment = retry_environment(
        original["environment"],
        module.collection_environment(plan, module.load(module.REFERENCE / "ab_plan.json")), RUN,
    )
    failure_inputs = [module.bound(path) for path in (
        FAILED_STATE, FAILED_LOG, Path(failed["collection_recovery_continuation"]["path"]),
    )]
    write_new(FAILURE, {
        "at": module.now(), "state": failed, "inputs": failure_inputs,
        "diagnosis": (
            "Legacy NUMA observer rejects reactive at argument parsing. The frozen "
            "watcher terminated the attempt; preserve its partial raw data."
        ),
        "retry_scope": "Same frozen app, engine, plan and environment except fresh RUN_ROOT; replace only the placement observer.",
    })
    RUN.mkdir()
    write_new(RUN / "ab_plan.json", deepcopy(plan))
    registration = deepcopy(original)
    registration.update(
        at=module.now(), environment=environment, plan=module.bound(RUN / "ab_plan.json"),
        retry_inputs=[
            module.bound(path) for path in (
                Path(__file__), OBSERVER, CPU, FAILURE, WAIT_STATE, original_path,
                RUN / "ab_plan.json",
                ROOT / "scripts/resume_v78_after_complete_v77_20261016.py",
            )
        ] + failure_inputs,
        retry_scope="Observer/continuation repair only; all task and serving settings unchanged.",
    )
    write_new(REGISTRATION, registration)
    module.verify_source(registration)
    print(str(REGISTRATION))


def watch():
    module, _, _ = configure()
    module.verify_source(module.load(REGISTRATION))
    module.watch()


def start():
    if any(path.exists() for path in (STATE, LOG, SUPERVISOR)):
        raise FileExistsError("preserve an existing retry supervisor")
    module, _, _ = configure()
    module.verify_source(module.load(REGISTRATION))
    _, _, process, _, _ = module.operations()
    with LOG.open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch"], cwd=V78_APP,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    record = {
        "at": module.now(), "supervisor": process(child.pid),
        "registration": module.bound(REGISTRATION),
    }
    write_new(SUPERVISOR, record)
    print(record)


if __name__ == "__main__":
    actions = {"--prepare": prepare, "--start": start, "--watch": watch}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("use --prepare, --start or --watch")
    actions[sys.argv[1]]()
