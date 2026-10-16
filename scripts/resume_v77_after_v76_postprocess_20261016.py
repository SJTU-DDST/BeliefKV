#!/usr/bin/env python3
"""Resume the frozen V77 helper after verified V76 reporting recovery."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


BASE = Path("/home/longhao/experiment")
MAIN = BASE / "BeliefKV"
APP = BASE / ".worktrees/beliefkv-v1213-native-close-gpu-20261016"
OPS = BASE / "ops"
REPORTS = MAIN / "experiments/reports"
ORIGINAL_STATE = OPS / "v76_no_prefetch_20261016.state.json"
V77_STATE = OPS / "v77_native_close_20261016.state.json"
RECOVERY = REPORTS / "v1267_v76_postprocess_recovery_20261016.json"
REGISTRATION = REPORTS / "v1268_v77_recovered_continuation_20261016.json"
SUPERVISOR = REPORTS / "v1269_v77_recovered_supervisor_20261016.json"
WAIT_STATE = OPS / "v76_postprocess_recovered_20261016.state.json"
LOG = OPS / "v77_native_close_recovery_20261016.log"


def load(path):
    return json.loads(path.read_bytes())


def bound(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write_new(path, payload):
    with path.open("x") as stream:
        stream.write(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def frozen_helper():
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    path = APP / "scripts/start_v77_native_close_20261016.py"
    spec = importlib.util.spec_from_file_location("_frozen_v77_postprocess_recovery", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def recovery_state(previous, summary, comparison, expected_ids, finished_ids):
    ids = [row["instance_id"] for row in summary["workflows"]]
    if previous.get("status") != "driver_failed" or previous.get("exit_code") != 2:
        raise ValueError("recovery applies only to the diagnosed reporting exit2")
    if (
        summary.get("workflow_count") != len(expected_ids)
        or len(ids) != len(set(ids))
        or sorted(ids) != sorted(expected_ids)
        or len(finished_ids) != len(set(finished_ids))
        or sorted(finished_ids) != sorted(expected_ids)
    ):
        raise ValueError("V76 collection does not cover every planned workflow")
    if (
        comparison.get("same_task_set") is not True
        or comparison.get("same_physical_pool_capacity") is not True
        or set(comparison.get("runs", {})) != {"reactive", "native"}
        or Path(comparison["runs"]["reactive"]["run"]).resolve()
        != Path(previous["run_root"]).resolve() / "reactive"
    ):
        raise ValueError("recovered comparison has unmatched tasks, capacity or arm identity")
    return {
        "status": "completed",
        "completion_scope": "Full collection and independently recovered reporting; original driver failure is retained.",
        "run_root": previous["run_root"],
        "driver": previous["driver"],
        "original_driver_exit_code": previous["exit_code"],
        "original_driver_status": previous["status"],
        "outcomes": dict(Counter(row["outcome"] for row in summary["workflows"])),
    }


def verify(registration, module):
    if bound(Path(__file__)) != registration["wrapper"]:
        raise ValueError("execute the registered recovery wrapper")
    for item in (
        registration["wrapper"], registration["recovery"], registration["wait_state"],
        registration["original_registration"], registration["frozen_helper"],
        *registration["recovery_inputs"],
    ):
        if bound(Path(item["path"])) != item:
            raise ValueError("a recovery source or verified input changed")
    module.verify_source(module.load(module.REGISTRATION))


def prepare():
    module = frozen_helper()
    original_registration = module.load(module.REGISTRATION)
    module.verify_source(original_registration)
    previous = load(ORIGINAL_STATE)
    run = Path(previous["run_root"])
    summary_path = run / "reactive/client_156/summary.json"
    comparison_path = run / "native_reference_comparison.json"
    arrival_path = run / "reactive/client_156/workflow_arrivals.jsonl"
    failure_log = OPS / "v76_no_prefetch_20261016.log"
    if not failure_log.read_text().rstrip().endswith(
        "compare_native_policy_runs.py: error: historical reference requires "
        "predictive_h2d and its referenced native arm"
    ):
        raise ValueError("the recorded failure is not the diagnosed arm-name parser error")
    telemetry_path = run / "reactive/server/native_telemetry_status.json"
    telemetry = load(telemetry_path)
    if "writer_error" not in telemetry or telemetry["writer_error"]:
        raise ValueError("V76 telemetry did not close without a writer error")
    plan = load(module.RUN / "ab_plan.json")
    expected_ids = plan["workload_instance_ids_in_manifest_order"]
    finished_ids = [
        row["instance_id"]
        for line in arrival_path.read_text().splitlines()
        if (row := json.loads(line)).get("event") == "workflow_finished"
    ]
    state = recovery_state(previous, load(summary_path), load(comparison_path), expected_ids, finished_ids)
    alive, _, _, _, _ = module.operations()
    if alive(previous["supervisor"]) or alive(previous["driver"]):
        raise ValueError("the original V76 processes are still live")
    old_v77 = load(V77_STATE)
    if old_v77["status"] != "stopped_for_diagnosis" or "startup" in old_v77:
        raise ValueError("V77 is not an unstarted stopped continuation")
    if alive(old_v77["supervisor"]) or (module.RUN / "native.launch.log").exists():
        raise ValueError("a V77 supervisor or collection already exists")
    inputs = [
        bound(path) for path in (
            ORIGINAL_STATE, summary_path, comparison_path, arrival_path,
            failure_log, telemetry_path,
            run / "reactive/client_156/manifest.json", module.RUN / "ab_plan.json",
        )
    ]
    for instance in expected_ids:
        inputs.append(bound(run / "reactive/client_156/workflows" / instance / "result.json"))
    write_new(RECOVERY, {
        "at": module.now(), "status": "complete_collection_with_recovered_reporting",
        "original_v76_state": previous, "original_stopped_v77_state": old_v77,
        "inputs": inputs, "comparison_source": bound(MAIN / "scripts/compare_native_policy_runs.py"),
        "cpu_receipt": bound(REPORTS / "v1266_reactive_native_reference_cpu_20261016.xml"),
        "recovery_cpu_receipt": bound(REPORTS / "v1270_v77_recovery_cpu_20261016.xml"),
        "scope": "Reporting recovery only; no relabeling of outcomes, guards, quality or original driver exit2.",
    })
    state["postprocessing_recovery"] = bound(RECOVERY)
    write_new(WAIT_STATE, state)
    write_new(REGISTRATION, {
        "at": module.now(), "wrapper": bound(Path(__file__)),
        "frozen_helper": original_registration["helper"],
        "original_registration": bound(module.REGISTRATION),
        "recovery": bound(RECOVERY), "wait_state": bound(WAIT_STATE),
        "recovery_inputs": inputs,
        "scope": "Use the unchanged V77 watcher and resource/startup checks with the verified recovered predecessor state.",
    })
    print(json.dumps({"registration": str(REGISTRATION), "recovery": str(RECOVERY)}))


def watch():
    module = frozen_helper()
    registration = load(REGISTRATION)
    verify(registration, module)
    module.WAIT_STATE = WAIT_STATE
    original_write = module.write_json

    def write_with_recovery(path, payload):
        if path == module.STATE:
            payload = {**payload, "postprocessing_recovery_continuation": bound(REGISTRATION)}
        original_write(path, payload)

    module.write_json = write_with_recovery
    module.watch()


def start():
    module = frozen_helper()
    registration = load(REGISTRATION)
    verify(registration, module)
    if LOG.exists() or SUPERVISOR.exists():
        raise FileExistsError("preserve the existing recovery supervisor")
    alive, _, process, _, _ = module.operations()
    current = load(V77_STATE)
    if (
        current["status"] != "stopped_for_diagnosis"
        or any(current.get(key) and alive(current[key]) for key in ("supervisor", "driver"))
        or (module.RUN / "native.launch.log").exists()
    ):
        raise ValueError("V77 is no longer an unstarted stopped continuation")
    with LOG.open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch"],
            cwd=APP, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    record = {
        "at": datetime.now().astimezone().isoformat(), "supervisor": process(child.pid),
        "registration": bound(REGISTRATION),
    }
    write_new(SUPERVISOR, record)
    print(json.dumps(record))


if __name__ == "__main__":
    actions = {"--prepare": prepare, "--start": start, "--watch": watch}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("use --prepare, --start or --watch")
    actions[sys.argv[1]]()
