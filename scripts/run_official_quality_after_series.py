#!/usr/bin/env python3
"""Grade the complete V76/V75 outputs after their GPU supervisors have exited."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


BASE = Path("/home/longhao/experiment")
ROOT = BASE / "BeliefKV"
OPS = BASE / "ops/quality_eval"
PYTHON = OPS / "venv/bin/python"
APP_PYTHON = Path("/home/longhao/miniconda3/envs/beliefkv-next/bin/python")
FROZEN = BASE / ".worktrees/beliefkv-v1160-no-prefetch-20261016"
CORE_SOURCE = ROOT / (
    "experiments/reports/v1178_reused_analysis_sources_20261016/"
    "run_official_quality_cpu_20261015.py"
)
OWNER_SOURCE = BASE / "ops/server_reservation.py"
HELPER = OPS / "run_v75_v76_quality_cpu_20261016.py"
REGISTRATION = ROOT / "experiments/reports/v1185_v75_v76_quality_registration_20261016.json"
ENVIRONMENT = ROOT / "experiments/reports/v1184_v75_v76_quality_environment_20261016.json"
STATE = OPS / "quality_v75_v76_cpu_20261016.state.json"
LOG = OPS / "quality_v75_v76_cpu_20261016.supervisor.log"
LOCK = OPS / "quality_v75_v76_cpu_20261016.lock"
RUN_ID = "beliefkv-v75-v76-quality-cpu-20261016"
V75 = ROOT / "experiments/raw/qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback"
V76 = ROOT / "experiments/raw/qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch"
ARMS = {
    "v76_reactive": {
        "client": V76 / "reactive/client_156",
        "evaluation": ROOT / "experiments/reports/v1181_v76_quality_inputs_20261016",
    },
    "v75_native": {
        "client": V75 / "native/client_156",
        "evaluation": ROOT / "experiments/reports/v1182_v75_native_quality_inputs_20261016",
    },
    "v75_predictive": {
        "client": V75 / "predictive_h2d/client_156",
        "evaluation": ROOT / "experiments/reports/v1183_v75_predictive_quality_inputs_20261016",
    },
}


def load(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding(path):
    return {"path": str(path), "sha256": digest(path)}


def now():
    return datetime.now().astimezone().isoformat()


def write(path, value):
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live_waiters(registration, inspect):
    result = []
    for expected in registration["wait_processes"]:
        current = inspect(expected["pid"])
        if current and current["state"] != "Z" and (
            current["start_ticks"] == expected["start_ticks"]
        ):
            result.append(current)
    return result


def complete_summary(path, expected_ids):
    summary = load(path)
    rows = summary.get("workflows")
    if (
        summary.get("workflow_count") != len(expected_ids)
        or not isinstance(rows, list) or len(rows) != len(expected_ids)
        or {row["instance_id"] for row in rows} != set(expected_ids)
    ):
        raise ValueError(f"source has incomplete task coverage: {path}")
    return summary


def check_registration():
    registration = load(REGISTRATION)
    for item in registration["sources"]:
        if digest(Path(item["path"])) != item["sha256"]:
            raise ValueError(f"registered quality source changed: {item['path']}")
    if registration["arms"] != {
        name: {key: str(value) for key, value in arm.items()}
        for name, arm in ARMS.items()
    }:
        raise ValueError("registered quality cohorts changed")
    return registration


def configured_core():
    core = module_from(CORE_SOURCE, "series_official_quality_core")
    core.SNAPSHOT = ENVIRONMENT
    core.GRADER_RUN_ID = RUN_ID
    core.DEADLINE = core.ADMISSION_DEADLINE = datetime.max.replace(tzinfo=timezone.utc)
    core.ARMS = ARMS
    return core


def export_and_prepare(registration, core):
    workload = Path(registration["workload"]["path"])
    if digest(workload) != registration["workload"]["sha256"]:
        raise ValueError("registered workload changed")
    for arm in ARMS.values():
        complete_summary(arm["client"] / "summary.json", registration["instance_ids"])
        if not arm["evaluation"].exists():
            subprocess.run([
                str(APP_PYTHON), str(FROZEN / "scripts/export_qwen35_swebench_predictions.py"),
                "--client-dir", str(arm["client"]), "--workload-manifest", str(workload),
                "--output-dir", str(arm["evaluation"]),
            ], check=True, timeout=120)
    if not ENVIRONMENT.exists():
        core.prepare()
    core.check_harness()


def supervise():
    with LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        registration = check_registration()
        owner = module_from(OWNER_SOURCE, "quality_process_ownership")
        state = {
            "status": "waiting_for_complete_v75_v76",
            "supervisor": owner.process(os.getpid()), "started_at": now(),
            "registration": binding(REGISTRATION), "arms": {},
            "gpu_work": False, "grading_started": False,
        }
        write(STATE, state)
        try:
            while waiting := live_waiters(registration, owner.process):
                state.update(observed_at=now(), live_wait_processes=waiting)
                write(STATE, state)
                time.sleep(10)
            state.pop("live_wait_processes", None)
            core = configured_core()
            state.update(status="preparing_complete_quality_inputs", observed_at=now())
            write(STATE, state)
            export_and_prepare(registration, core)
            state.update(environment=binding(ENVIRONMENT), grading_started=True)
            task_env = {
                **os.environ, "CUDA_VISIBLE_DEVICES": "999", "NVIDIA_VISIBLE_DEVICES": "void",
                "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                "HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
            }
            for name, arm in ARMS.items():
                check_registration()
                with (arm["evaluation"] / "official_cpu_evaluation.log").open("ab") as log:
                    worker = subprocess.Popen(
                        [str(PYTHON), str(HELPER), "--worker", name],
                        env=task_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                    )
                    state.update(
                        status="running_official_cpu_grading", current_arm=name,
                        worker=owner.process(worker.pid), observed_at=now(),
                    )
                    write(STATE, state)
                    try:
                        while worker.poll() is None:
                            state.update(observed_at=now())
                            write(STATE, state)
                            time.sleep(5)
                    finally:
                        if worker.poll() is None:
                            os.killpg(worker.pid, signal.SIGTERM)
                            worker.wait(timeout=20)
                        core.cleanup()
                state.pop("worker", None)
                outcome = {"worker_returncode": worker.returncode, "finished_at": now()}
                try:
                    scorer = module_from(
                        FROZEN / "scripts/score_qwen35_native_official.py", "series_official_scorer",
                    )
                    score = scorer.score_run(arm["client"], arm["evaluation"], RUN_ID)
                    score_path = arm["evaluation"] / "official_quality_summary.json"
                    write(score_path, score)
                    outcome.update(
                        score=binding(score_path),
                        complete_official_grading=score["complete_official_grading"],
                        resolved=len(score["resolved_ids"]),
                        unresolved=len(score["unresolved_ids"]),
                        evaluator_errors=score["error_ids"],
                        evaluator_incomplete=score["incomplete_ids"],
                        native_non_completed=score["native_non_completed_ids"],
                    )
                except Exception as error:
                    outcome.update(report_audit_error=repr(error), complete_official_grading=False)
                state["arms"][name] = outcome
                write(STATE, state)
            state.update(
                status=(
                    "official_grading_finished" if all(
                        result["complete_official_grading"] for result in state["arms"].values()
                    ) else "quality_reports_require_diagnosis"
                ),
                finished_at=now(),
            )
            write(STATE, state)
        except Exception as error:
            state.update(status="quality_supervisor_failed", error=repr(error), observed_at=now())
            write(STATE, state)
            raise


def register():
    if any(path.exists() for path in (REGISTRATION, STATE, LOG)):
        raise FileExistsError("preserve existing quality registration and results")
    owner = module_from(OWNER_SOURCE, "quality_registration_ownership")
    expected_ids = load(
        ROOT / "experiments/reports/v1177_v73_official_quality_preflight_20261016.json"
    )["instance_ids"]
    waiting = []
    for path in (
        BASE / "ops/v75_batched_writeback_20261016.state.json",
        BASE / "ops/v76_no_prefetch_20261016.state.json",
    ):
        source = load(path)
        for field in ("supervisor", "driver"):
            identity = source.get(field)
            if identity and owner.alive(identity):
                waiting.append(identity)
    workload = ROOT / "configs/migration/qwen35_native_predictive_replenished_108plus48_2026-10-09.json"
    registration = {
        "at": now(), "order": list(ARMS), "instance_ids": expected_ids,
        "sources": [binding(path) for path in (
            HELPER, CORE_SOURCE, OWNER_SOURCE,
            FROZEN / "scripts/export_qwen35_swebench_predictions.py",
            FROZEN / "scripts/score_qwen35_native_official.py",
        )],
        "wait_processes": waiting, "workload": binding(workload),
        "arms": {
            name: {key: str(value) for key, value in arm.items()} for name, arm in ARMS.items()
        },
        "limits": {"workers": 1, "nano_cpus": 1000000000, "memory_bytes": 8 * 1024**3},
        "scope": (
            "Wait for the recorded GPU supervisors/drivers to exit; retain every task outcome. "
            "Official grading is distinct from model completion and performance acceptance. "
            "No recurring reservation deadline; no GPU access. Later GPU-development overlap, "
            "if any, must be recorded separately from matched resource performance trials."
        ),
    }
    with REGISTRATION.open("x") as stream:
        stream.write(json.dumps(registration, indent=2) + "\n")
    with LOG.open("xb") as log:
        supervisor = subprocess.Popen(
            [str(PYTHON), str(HELPER), "--watch"],
            cwd=OPS, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "supervisor": owner.process(supervisor.pid),
        "registration": str(REGISTRATION), "state": str(STATE),
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--worker", choices=ARMS)
    args = parser.parse_args()
    os.nice(15)
    if args.register:
        register()
    elif args.watch:
        supervise()
    else:
        check_registration()
        configured_core().worker(args.worker)


if __name__ == "__main__":
    main()
