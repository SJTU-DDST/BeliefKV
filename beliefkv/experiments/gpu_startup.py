"""Retry pre-replay GPU resource conflicts without changing serving capacity."""

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import time


class ResourceConflict(ValueError):
    def __init__(self, reason, evidence, *, unattributed=False):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence
        self.unattributed = unattributed


def snapshot():
    common = ["nvidia-smi", "--id=0", "--format=csv,noheader,nounits"]

    def query(fields):
        return subprocess.check_output(
            [*common, *fields], text=True, timeout=10,
        ).strip()

    gpu = list(csv.reader(io.StringIO(query([
        "--query-gpu=memory.free,memory.total,utilization.gpu",
    ]))))
    if len(gpu) != 1 or len(gpu[0]) != 3:
        raise ValueError("GPU startup observation lacks one complete device")
    free, total, utilization = (int(value.strip()) for value in gpu[0])
    processes = []
    for row in csv.reader(io.StringIO(query([
        "--query-compute-apps=pid,used_gpu_memory",
    ]))):
        if len(row) != 2:
            raise ValueError("GPU startup observation has incomplete process accounting")
        pid, used = (value.strip() for value in row)
        if not pid.isdecimal() or not used.isdecimal():
            raise ValueError("GPU startup observation has unknown process accounting")
        processes.append({"pid": int(pid), "used_memory_mib": int(used)})
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "free_memory_mib": free, "total_memory_mib": total,
        "utilization_percent": utilization, "compute_processes": processes,
    }


def owned_process(pid, identities, process):
    known = {row["pid"]: row for row in identities}
    visited = set()
    while pid and pid not in visited:
        visited.add(pid)
        current = process(pid)
        if not current:
            return False
        if pid in known:
            return current.get("start_ticks") == known[pid].get("start_ticks")
        pid = current.get("ppid")
    return False


class Monitor:
    def __init__(self, directory, *, process, observe=None, clock=None):
        self.path = Path(directory) / "startup_resources.jsonl"
        self.process = process
        self.observe = snapshot if observe is None else observe
        self.clock = time.monotonic if clock is None else clock
        self.next_check = 0.

    def check(self, identities, *, force=False):
        now = self.clock()
        if not force and now < self.next_check:
            return
        self.next_check = now + 2.
        observation = self.observe()
        foreign = [
            row for row in observation["compute_processes"]
            if not owned_process(row["pid"], identities, self.process)
        ]
        observation["foreign_compute_processes"] = foreign
        with self.path.open("a") as stream:
            stream.write(json.dumps(observation, sort_keys=True) + "\n")
        if foreign:
            raise ResourceConflict("foreign_GPU_context_during_startup", observation)


def verify_capacity(expected, actual):
    if actual == expected:
        return
    changed = {name for name in expected.keys() | actual.keys()
               if expected.get(name) != actual.get(name)}
    device_fields = {"device_full_bytes", "device_full_tokens", "device_total_bytes"}
    if (
        expected.get("pool_layout") == actual.get("pool_layout") == "static_full_only"
        and changed == device_fields
        and type(actual.get("device_full_tokens")) is int
        and 0 < actual["device_full_tokens"] < expected["device_full_tokens"]
        and actual["device_full_bytes"] == actual["device_total_bytes"]
        == actual["device_full_tokens"] * 98304
        and expected["device_full_bytes"] == expected["device_total_bytes"]
        == expected["device_full_tokens"] * 98304
    ):
        raise ResourceConflict(
            "undersized_device_pool", {"expected": expected, "actual": actual},
            unattributed=True,
        )
    raise ValueError("actual startup capacity differs beyond a smaller device KV pool")


def retry_startup(root, start, *, wait_ready, verify_inputs, cleanup, alive, process,
                  state, pulse, observe=None, max_unattributed_failures=3):
    """Only startup is retried; callers submit replay after this returns."""
    root = Path(root)
    attempt_number, unattributed = 0, 0
    while True:
        state["status"] = state.get("startup_wait_status", "waiting_startup_resources")
        wait_ready()
        verify_inputs()
        attempt_number += 1
        attempt = root / "startup_attempts" / f"{attempt_number:04d}"
        attempt.mkdir(parents=True, exist_ok=False)
        state.update(startup_attempt=attempt_number, startup_attempt_path=str(attempt))
        monitor = Monitor(attempt, process=process, observe=observe)
        try:
            monitor.check([], force=True)
            result = start(attempt, monitor)
        except BaseException as error:
            state["status"] = "cleaning_failed_startup"
            report = cleanup(attempt)
            if report.get("remaining_processes") or any(
                alive(identity) for identity in report.get("identities", ())
            ):
                raise RuntimeError("failed GPU startup still has an actual owned process") from error
            retryable = isinstance(error, ResourceConflict)
            if retryable and error.unattributed:
                unattributed += 1
                retryable = unattributed < max_unattributed_failures
            failure = {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": repr(error), "resource_conflict": isinstance(error, ResourceConflict),
                "retryable": retryable, "actual_owned_exit_verified": True,
                "unattributed_capacity_failures": unattributed,
                "cleanup": report,
            }
            if isinstance(error, ResourceConflict):
                failure.update(reason=error.reason, evidence=error.evidence)
            with (attempt / "failure.json").open("x") as stream:
                json.dump(failure, stream, indent=2, sort_keys=True)
            state["last_startup_failure"] = {
                "path": str(attempt / "failure.json"), **failure,
            }
            if not retryable:
                raise
            state["status"] = state.get("startup_wait_status", "waiting_startup_resources")
            state["startup_resource_retries"] = attempt_number
            pulse()
            continue
        for name in ("server", "server.log", "opportunities"):
            source = attempt / name
            if source.exists():
                target = root / name
                if target.exists() or target.is_symlink():
                    raise FileExistsError("preserve an existing accepted startup: " + str(target))
                target.symlink_to(source.relative_to(root), target_is_directory=source.is_dir())
        with (root / "accepted_startup.json").open("x") as stream:
            json.dump({
                "attempt": attempt_number, "directory": str(attempt),
                "resource_retries": attempt_number - 1,
                "serving_capacity_preserved": True,
                "no_replay_requests_submitted_during_failed_startups": True,
            }, stream, indent=2, sort_keys=True)
        state["accepted_startup_path"] = str(attempt)
        return result
