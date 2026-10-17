#!/usr/bin/env python3
"""Recover complete V77 collection without changing its failed native gate."""

from __future__ import annotations

from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent
REPORTS = ROOT / "experiments/reports"
V77_APP = BASE / ".worktrees/beliefkv-v1213-native-close-gpu-20261016"
V78_APP = BASE / ".worktrees/beliefkv-v1278-native-order-gpu-20261016"
V77_STATE = BASE / "ops/v77_native_close_20261016.state.json"
V78_STATE = BASE / "ops/v78_native_order_20261016.state.json"
RECOVERY = REPORTS / "v1321_v77_complete_collection_recovery_20261016.json"
WAIT_STATE = BASE / "ops/v77_complete_collection_recovered_20261016.state.json"
REGISTRATION = REPORTS / "v1322_v78_recovered_continuation_20261016.json"
SUPERVISOR = REPORTS / "v1323_v78_recovered_supervisor_20261016.json"
CPU = REPORTS / "v1324_v77_v78_collection_recovery_cpu_20261016.xml"
STATE = BASE / "ops/v78_native_order_recovered_20261016.state.json"
LOG = BASE / "ops/v78_native_order_recovered_20261016.log"


def frozen_helper(app, name):
    sys.path.insert(0, str(app))
    path = app / "scripts" / f"start_{name}_20261016.py"
    spec = importlib.util.spec_from_file_location(f"_frozen_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def collection_evidence(previous, summary, telemetry, expected_ids, finished_ids):
    if previous.get("status") != "stopped_for_diagnosis" or previous.get("error") != (
        "ValueError('native-close collection/startup failed: exit=1')"
    ) or not previous.get("startup"):
        raise ValueError("not the diagnosed native-gate exit1 after verified startup")
    rows = summary.get("workflows", ())
    ids = [row["instance_id"] for row in rows]
    if (
        not expected_ids or len(expected_ids) != len(set(expected_ids))
        or summary.get("workflow_count") != len(expected_ids)
        or len(ids) != len(set(ids)) or sorted(ids) != sorted(expected_ids)
        or len(finished_ids) != len(set(finished_ids))
        or sorted(finished_ids) != sorted(expected_ids)
    ):
        raise ValueError("collection must cover every planned task exactly once")
    outcomes = Counter(row.get("outcome") for row in rows)
    eligible = sum(row.get("native_agent_jct_eligible") is True for row in rows)
    if (
        set(outcomes) - {"completed", "incomplete"}
        or summary.get("completed_workflows") != outcomes["completed"]
        or summary.get("native_agent_jct_eligible_workflows") != eligible
        or eligible >= len(expected_ids)
        or any(
            row.get("error") is not None
            or any((row.get("trace") or {}).get(key) is not True for key in (
                "workflow_lifecycle_valid", "llm_pairing_valid", "tool_pairing_valid",
            ))
            for row in rows
        )
    ):
        raise ValueError("exit1 is not explained solely by recorded native-gate failures")
    if any(key not in telemetry or telemetry[key] != value for key, value in {
        "writer_error": None, "dropped_records": 0, "failed_records": 0,
        "writer_queue_depth": 0, "pending_batch_count": 0, "pending_request_count": 0,
    }.items()):
        raise ValueError("telemetry did not close cleanly")
    return {
        "status": "complete_collection_with_failed_native_gate",
        "workflow_count": len(rows), "outcomes": dict(outcomes),
        "native_agent_jct_eligible_workflows": eligible,
        "native_gate_passed": False, "original_driver_exit_code": 1,
        "scope": (
            "Collection completeness only. Preserve incomplete, guard, quality and "
            "JCT eligibility labels; no native-gate or performance acceptance."
        ),
    }


def write_new(path, payload):
    with path.open("x") as stream:
        stream.write(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def recover_v77():
    if RECOVERY.exists() or WAIT_STATE.exists():
        raise FileExistsError("preserve existing collection recovery evidence")
    module = frozen_helper(V77_APP, "v77_native_close")
    module.verify_source(module.load(module.REGISTRATION))
    previous = module.load(V77_STATE)
    alive, _, _, _, _ = module.operations()
    if any(previous.get(key) and alive(previous[key]) for key in ("supervisor", "driver")):
        raise ValueError("the original collection processes are still live")
    arm = module.RUN / "native"
    summary_path = arm / "client_156/summary.json"
    arrival_path = arm / "client_156/workflow_arrivals.jsonl"
    telemetry_path = arm / "server/native_telemetry_status.json"
    plan = module.load(module.RUN / "ab_plan.json")
    expected = plan["workload_instance_ids_in_manifest_order"]
    finished = [
        row["instance_id"] for line in arrival_path.read_text().splitlines()
        if (row := json.loads(line)).get("event") == "workflow_finished"
    ]
    evidence = collection_evidence(
        previous, module.load(summary_path), module.load(telemetry_path), expected, finished,
    )
    if len(expected) != 156 or not (module.RUN / "native.launch.log").read_text().rstrip().endswith(
        f"Native regime probe: client exit=1, artifacts={arm}"
    ):
        raise ValueError("the full registered cohort or recorded driver exit differs")
    inputs = [module.bound(path) for path in (
        V77_STATE, summary_path, arrival_path, telemetry_path,
        module.RUN / "ab_plan.json", module.RUN / "native.launch.log",
        Path(previous["startup"]["path"]),
    )]
    if module.bound(Path(previous["startup"]["path"])) != previous["startup"]:
        raise ValueError("verified startup evidence changed")
    for row in module.load(summary_path)["workflows"]:
        path = arm / "client_156/workflows" / row["instance_id"] / "result.json"
        result = module.load(path)
        if any(result.get(key) != row.get(key) for key in (
            "instance_id", "outcome", "error", "native_agent_jct_eligible",
        )):
            raise ValueError("per-task result disagrees with the aggregate")
        inputs.append(module.bound(path))
    module.postprocess()
    module.verify_source(module.load(module.REGISTRATION))
    if any(module.bound(Path(item["path"])) != item for item in inputs):
        raise ValueError("immutable collection inputs changed during postprocessing")
    write_new(RECOVERY, {
        "at": module.now(), **evidence, "original_state": previous,
        "inputs": inputs, "postprocessing": module.bound(module.FINAL),
        "frozen_helper": module.bound(Path(module.__file__)),
        "wrapper": module.bound(Path(__file__)), "cpu_receipt": module.bound(CPU),
    })
    write_new(WAIT_STATE, {
        "status": "completed", "completion_scope": evidence["scope"],
        "run_root": str(module.RUN), "supervisor": previous["supervisor"],
        "driver": previous["driver"], "result": module.bound(module.FINAL),
        "native_gate_passed": False, "original_driver_exit_code": 1,
        "original_status": previous["status"], "collection_recovery": module.bound(RECOVERY),
    })
    print(json.dumps({"recovery": str(RECOVERY), "collection": evidence}, indent=2))


def prepare():
    if any(path.exists() for path in (REGISTRATION, SUPERVISOR, STATE, LOG)):
        raise FileExistsError("preserve an existing recovered continuation")
    module = frozen_helper(V78_APP, "v78_native_order")
    module.verify_source(module.load(module.REGISTRATION))
    previous = module.load(V78_STATE)
    alive, _, _, _, _ = module.operations()
    if previous.get("status") != "stopped_for_diagnosis" or "startup" in previous:
        raise ValueError("V78 is no longer an unstarted stopped continuation")
    if any(previous.get(key) and alive(previous[key]) for key in ("supervisor", "driver")):
        raise ValueError("the original continuation processes are still live")
    recovery = module.load(RECOVERY)
    wait = module.load(WAIT_STATE)
    if (
        recovery["status"] != "complete_collection_with_failed_native_gate"
        or wait.get("collection_recovery") != module.bound(RECOVERY)
        or wait.get("native_gate_passed") is not False
        or module.bound(Path(wait["result"]["path"])) != wait["result"]
        or (module.RUN / "reactive").exists()
    ):
        raise ValueError("unverified predecessor recovery or V78 already started")
    inputs = [
        module.bound(path) for path in (
            V78_STATE, RECOVERY, WAIT_STATE, module.REGISTRATION, CPU,
        )
    ] + recovery["inputs"] + [recovery["postprocessing"]]
    write_new(REGISTRATION, {
        "at": module.now(), "wrapper": module.bound(Path(__file__)),
        "frozen_helper": module.bound(Path(module.__file__)), "inputs": inputs,
        "scope": (
            "Run unchanged registered V78 after complete V77 collection and recovered "
            "reporting. Original native-gate failure and stopped states remain intact."
        ),
    })
    print(json.dumps({"registration": str(REGISTRATION)}))


def verify(module, registration):
    for item in [registration["wrapper"], registration["frozen_helper"], *registration["inputs"]]:
        if module.bound(Path(item["path"])) != item:
            raise ValueError("a recovery source, CPU receipt or collection input changed")
    module.verify_source(module.load(module.REGISTRATION))


def watch():
    module = frozen_helper(V78_APP, "v78_native_order")
    registration = module.load(REGISTRATION)
    verify(module, registration)
    module.WAIT_STATE, module.STATE = WAIT_STATE, STATE
    original_write = module.write_json

    def write_with_recovery(path, payload):
        if path == STATE:
            payload = {**payload, "collection_recovery_continuation": module.bound(REGISTRATION)}
        original_write(path, payload)

    module.write_json = write_with_recovery
    module.watch()


def start():
    module = frozen_helper(V78_APP, "v78_native_order")
    verify(module, module.load(REGISTRATION))
    if any(path.exists() for path in (STATE, LOG, SUPERVISOR)):
        raise FileExistsError("preserve an existing recovered supervisor")
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
    print(json.dumps(record))


if __name__ == "__main__":
    actions = {
        "--recover-v77": recover_v77, "--prepare": prepare, "--start": start, "--watch": watch,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("use --recover-v77, --prepare, --start or --watch")
    actions[sys.argv[1]]()
