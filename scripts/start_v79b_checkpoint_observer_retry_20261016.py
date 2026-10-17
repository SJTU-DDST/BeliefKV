#!/usr/bin/env python3
"""Retry frozen V79 after an observer-only import failure, in fresh output."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.resume_v78_after_complete_v77_20261016 import frozen_helper, write_new

BASE = ROOT.parent
APP = BASE / ".worktrees/beliefkv-v1366-checkpoint-diagnostic-gpu-20261016"
REPORTS = ROOT / "experiments/reports"
RUN = ROOT / "experiments/raw/qwen35_native_checkpoint_diagnostic_online_poisson_156root_20261016_v79b"
FAILED_STATE = BASE / "ops/v79_checkpoint_diagnostic_20261016.state.json"
FAILED_LOG = BASE / "ops/v79_checkpoint_diagnostic_20261016.log"
FAILURE = REPORTS / "v1386_v79_observer_import_failure_20261016.json"
REGISTRATION = REPORTS / "v1387_v79b_checkpoint_observer_retry_registration_20261016.json"
SUPERVISOR = REPORTS / "v1388_v79b_checkpoint_observer_retry_supervisor_20261016.json"
STARTUP = REPORTS / "v1389_v79b_checkpoint_diagnostic_actual_startup_20261016.json"
FINAL = REPORTS / "v1390_v79b_checkpoint_diagnostic_completion_20261016.json"
PLACEMENT = REPORTS / "v1391_v79b_checkpoint_diagnostic_placement_20261016.json"
CPU = REPORTS / "v1393_v79b_retry_final_cpu_20261016.xml"
STATE = BASE / "ops/v79b_checkpoint_diagnostic_20261016.state.json"
LOG = BASE / "ops/v79b_checkpoint_diagnostic_20261016.log"
OBSERVER = ROOT / "scripts/observe_owned_scheduler_numa.py"
FROZEN_OBSERVER = APP / "scripts/observe_owned_scheduler_numa.py"


def retry_environment(original, candidate, run):
    if candidate != {**original, "RUN_ROOT": str(run / "native")}:
        raise ValueError("retry may change only the fresh output directory")
    return candidate


def observer_command(command):
    if len(command) > 1 and command[1] == str(FROZEN_OBSERVER):
        return [command[0], str(OBSERVER), *command[2:]]
    return command


def require_import_failure(failed, log, alive):
    if (
        failed.get("status") != "stopped_for_diagnosis"
        or "startup" in failed
        or "observe_owned_scheduler_numa.py" not in failed.get("error", "")
        or "ModuleNotFoundError: No module named 'server_reservation'" not in log
        or any(failed.get(key) and alive(failed[key]) for key in ("supervisor", "driver"))
    ):
        raise ValueError("not the exited observer import failure before startup qualification")


def configure():
    module = frozen_helper(APP, "v79_checkpoint_diagnostic")
    original_run, original_registration_path = module.RUN, module.REGISTRATION
    original_registration = module.load(original_registration_path)
    original_verify = module.verify_source
    original_verify(original_registration)
    module.RUN, module.REGISTRATION = RUN, REGISTRATION
    module.STATE, module.LOG = STATE, LOG
    module.STARTUP, module.FINAL, module.PLACEMENT = STARTUP, FINAL, PLACEMENT
    original_environment = module.collection_environment

    def collection_environment(plan):
        return {
            **original_environment(plan),
            "WORKFLOW_ARRIVAL_SCHEDULE": original_registration["environment"]["WORKFLOW_ARRIVAL_SCHEDULE"],
        }

    module.collection_environment = collection_environment

    def verify(registration):
        try:
            module.RUN = original_run
            original_verify(original_registration)
        finally:
            module.RUN = RUN
        for item in registration["retry_inputs"]:
            if module.bound(Path(item["path"])) != item:
                raise ValueError("a retry observer, source, plan or receipt changed")
        if module.load(RUN / "ab_plan.json") != module.load(original_run / "ab_plan.json"):
            raise ValueError("retry must retain the exact frozen diagnostic plan")
        retry_environment(
            original_registration["environment"],
            module.collection_environment(module.load(RUN / "ab_plan.json")), RUN,
        )
        retry_environment(original_registration["environment"], registration["environment"], RUN)

    module.verify_source = verify
    original_subprocess = module.subprocess

    def run(command, *args, **kwargs):
        return original_subprocess.run(observer_command(command), *args, **kwargs)

    module.subprocess = SimpleNamespace(**{**vars(original_subprocess), "run": run})
    return module, original_registration_path, original_registration


def prepare():
    if any(path.exists() for path in (
        RUN, FAILURE, REGISTRATION, SUPERVISOR, STARTUP, FINAL, PLACEMENT, STATE, LOG,
    )):
        raise FileExistsError("preserve existing original and retry artifacts")
    module, original_path, original = configure()
    failed = module.load(FAILED_STATE)
    alive, _, _, _, _ = module.operations()
    require_import_failure(failed, FAILED_LOG.read_text(), alive)
    original_run = Path(original["environment"]["RUN_ROOT"]).parent
    if module.continuation_sample(original_run / "native/server/eviction_attribution.jsonl") is None:
        raise ValueError("the original diagnostic lacks an actual continuation probe")
    cases = list(module.ET.parse(CPU).getroot().iter("testcase"))
    if not cases or any(case.find(kind) is not None for case in cases for kind in (
        "failure", "error", "skipped",
    )):
        raise ValueError("retry CPU qualification is incomplete")
    plan = module.load(original_run / "ab_plan.json")
    environment = retry_environment(
        original["environment"], module.collection_environment(plan), RUN,
    )
    write_new(FAILURE, {
        "at": module.now(), "state": failed,
        "inputs": [module.bound(path) for path in (FAILED_STATE, FAILED_LOG, original_path)],
        "diagnosis": (
            "The frozen NUMA observer resolves ops relative to a .worktrees checkout, "
            "then cannot import server_reservation. The supervisor stops the partial run."
        ),
        "scope": "Preserve partial data and failed startup; retry changes only the placement observer and fresh output.",
    })
    RUN.mkdir()
    write_new(RUN / "ab_plan.json", deepcopy(plan))
    registration = deepcopy(original)
    registration.update(
        at=module.now(), environment=environment,
        retry_inputs=[module.bound(path) for path in (
            Path(__file__), OBSERVER, CPU, FAILURE, FAILED_STATE, FAILED_LOG,
            original_path, RUN / "ab_plan.json",
            ROOT / "scripts/resume_v78_after_complete_v77_20261016.py",
        )],
        retry_scope="Unchanged frozen app, engine, tasks, arrival table and serving configuration; corrected external NUMA observer and new RUN_ROOT.",
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
            [sys.executable, str(Path(__file__).resolve()), "--watch"],
            cwd=APP, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
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
