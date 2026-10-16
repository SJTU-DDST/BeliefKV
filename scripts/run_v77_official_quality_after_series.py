#!/usr/bin/env python3
"""Grade complete V77 outputs after V77 and the existing quality queue exit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess


BASE = Path("/home/longhao/experiment")
ROOT = BASE / "BeliefKV"
OPS = BASE / "ops/quality_eval"
FROZEN = BASE / ".worktrees/beliefkv-v1213-native-close-gpu-20261016"
SERIES_SOURCE = FROZEN / "scripts/run_official_quality_after_series.py"
GPU_REGISTRATION = ROOT / "experiments/reports/v1215_v77_native_close_registration_20261016.json"
OLD_QUALITY_REGISTRATION = ROOT / "experiments/reports/v1185_v75_v76_quality_registration_20261016.json"
GPU_STATE = BASE / "ops/v77_native_close_20261016.state.json"
OLD_QUALITY_STATE = OPS / "quality_v75_v76_cpu_20261016.state.json"
REGISTRATION = ROOT / "experiments/reports/v1223_v77_quality_registration_20261016.json"
ENVIRONMENT = ROOT / "experiments/reports/v1224_v77_quality_environment_20261016.json"
EVALUATION = ROOT / "experiments/reports/v1222_v77_quality_inputs_20261016"
CPU_CHECK = ROOT / "experiments/reports/v1221_v77_quality_queue_cpu_20261016.xml"
STATE = OPS / "quality_v77_cpu_20261016.state.json"
LOG = OPS / "quality_v77_cpu_20261016.supervisor.log"
LOCK = OPS / "quality_v77_cpu_20261016.lock"
SUPERVISOR = ROOT / "experiments/reports/v1225_v77_quality_supervisor_20261016.json"
RUN = ROOT / "experiments/raw/qwen35_native_close_gc_online_poisson_156root_20261016_v77"
RUN_ID = "beliefkv-v77-native-close-quality-cpu-20261016"


def load(path):
    return json.loads(path.read_bytes())


def cohort_ids(plan):
    ids = plan["workload_instance_ids_in_manifest_order"]
    if (
        plan["root_count"] != 156 or len(ids) != 156 or len(set(ids)) != 156
        or plan["order"] != ["native"]
        or [row["instance_id"] for row in plan["arrival_schedule"]] != ids
    ):
        raise ValueError("V77 quality must retain the complete native task cohort")
    return list(ids)


def dynamic_waiters(paths, inspect):
    live = {}
    for path in paths:
        state = load(Path(path))
        for name in ("supervisor", "driver", "worker"):
            expected = state.get(name)
            if not expected:
                continue
            current = inspect(expected["pid"])
            if (
                current and current["state"] != "Z"
                and current["start_ticks"] == expected["start_ticks"]
            ):
                live[current["pid"], current["start_ticks"]] = current
    return list(live.values())


def configure_series(series=None):
    if series is None:
        spec = importlib.util.spec_from_file_location("v77_frozen_quality_series", SERIES_SOURCE)
        series = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(series)
    series.FROZEN = FROZEN
    series.HELPER = Path(__file__).resolve()
    series.REGISTRATION = REGISTRATION
    series.ENVIRONMENT = ENVIRONMENT
    series.STATE, series.LOG, series.LOCK = STATE, LOG, LOCK
    series.RUN_ID = RUN_ID
    series.ARMS = {"v77_native_close": {
        "client": RUN / "native/client_156", "evaluation": EVALUATION,
    }}
    original_waiters = series.live_waiters
    original_write = series.write

    def waiters(registration, inspect):
        result = original_waiters(registration, inspect)
        result.extend(dynamic_waiters(registration["dynamic_wait_states"], inspect))
        return list({(row["pid"], row["start_ticks"]): row for row in result}.values())

    def write(path, value):
        if path == STATE and value.get("status") == "waiting_for_complete_v75_v76":
            value = {**value, "status": "waiting_for_complete_v77_and_prior_quality"}
        original_write(path, value)

    series.live_waiters, series.write = waiters, write
    series.register = lambda: register(series)
    return series


def register(series):
    if any(path.exists() for path in (
        REGISTRATION, STATE, LOG, LOCK, ENVIRONMENT, EVALUATION, SUPERVISOR,
    )):
        raise FileExistsError("preserve existing V77 quality artifacts")
    owner = series.module_from(series.OWNER_SOURCE, "v77_quality_owner")
    gpu_registration = load(GPU_REGISTRATION)
    plan_path = Path(gpu_registration["plan"]["path"])
    if series.digest(plan_path) != gpu_registration["plan"]["sha256"]:
        raise ValueError("registered V77 plan changed")
    plan = load(plan_path)
    ids = cohort_ids(plan)
    workload = Path(plan["workload_manifest"])
    if series.digest(workload) != plan["workload_sha256"]:
        raise ValueError("registered V77 workload changed")
    waiting = []
    for path in (GPU_STATE, OLD_QUALITY_STATE):
        expected = load(path)["supervisor"]
        if not owner.alive(expected):
            raise ValueError("the exact predecessor supervisor is missing")
        waiting.append(expected)
    checks = load_cpu_check(CPU_CHECK)
    sources = (
        series.HELPER, SERIES_SOURCE, series.CORE_SOURCE, series.OWNER_SOURCE,
        FROZEN / "scripts/export_qwen35_swebench_predictions.py",
        FROZEN / "scripts/score_qwen35_native_official.py",
        GPU_REGISTRATION, OLD_QUALITY_REGISTRATION, plan_path, CPU_CHECK,
    )
    registration = {
        "at": series.now(), "order": list(series.ARMS), "instance_ids": ids,
        "sources": [series.binding(path) for path in sources],
        "wait_processes": waiting,
        "dynamic_wait_states": [str(GPU_STATE), str(OLD_QUALITY_STATE)],
        "workload": series.binding(workload),
        "arms": {
            name: {key: str(value) for key, value in arm.items()}
            for name, arm in series.ARMS.items()
        },
        "cpu_checks": checks,
        "limits": {
            "workers": 1, "nano_cpus": 1_000_000_000, "memory_bytes": 8 * 1024**3,
            "task_timeout_seconds": 1800, "gpu_access": False,
        },
        "scope": (
            "Wait for V77 and the prior CPU quality series, including live future "
            "drivers/workers. Grade all156 patches without changing outcome/guard "
            "labels, the frozen scorer or its measurement_complete condition. "
            "Official grading alone does not establish performance or novelty."
        ),
    }
    with REGISTRATION.open("x") as stream:
        stream.write(json.dumps(registration, indent=2) + "\n")
    with LOG.open("xb") as log:
        child = subprocess.Popen(
            [str(series.PYTHON), str(series.HELPER), "--watch"],
            cwd=OPS, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    record = {
        "at": series.now(), "supervisor": owner.process(child.pid),
        "registration": series.binding(REGISTRATION),
    }
    series.write(SUPERVISOR, record)
    print(json.dumps(record))


def load_cpu_check(path):
    import xml.etree.ElementTree as ET

    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases or any(
        case.find(tag) is not None
        for case in cases for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("quality queue CPU qualification is incomplete")
    return {"passed_cases": len(cases)}


if __name__ == "__main__":
    configure_series().main()
