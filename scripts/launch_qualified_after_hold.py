#!/usr/bin/env python3
"""Prepare current qualified sources and launch once after the user's GPU hold."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
OPS = REPO.parent / "ops"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.launch_semantic_h2d_after_pair import _require_clean, fresh_pair_environment
from scripts.audit_delegation_workload import audit as audit_delegation
from scripts.resume_predictive_after_native import write_json

PYTHON = "/home/longhao/miniconda3/envs/beliefkv-next/bin/python"
HOLD = OPS / "gpu_hold_until_20261021_0600.json"
PAUSE = Path("/tmp/beliefkv-experiments.paused")
MINIMUM_TIME = datetime(2026, 10, 21, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
BASE_PLAN = REPO / "experiments/raw/qwen35_locobench_long_36root_running8_20261020_v101/ab_plan.json"
RUN = REPO / "experiments/raw/qwen35_locobench_long_36root_running8_20261021_v103"
QUALIFIED = OPS / "v103_latest_after_hold_20261021.qualified.json"
REGISTRATION = OPS / "v103_latest_after_hold_20261021.registration.json"
STATE = OPS / "v103_latest_after_hold_20261021.state.json"
LOG = OPS / "v103_latest_after_hold_20261021.log"
DRIVER_LOG = OPS / "v103_latest_after_hold_driver_20261021.log"
PORT = 18506
MANIFEST = "configs/workloads/locobench_agent_long_36root_20261020.json"


def machine_helper(name):
    if str(OPS) not in sys.path:
        sys.path.insert(0, str(OPS))
    return importlib.import_module(name)


def now():
    return datetime.now(MINIMUM_TIME.tzinfo)


def command(argv):
    return subprocess.check_output(argv, text=True, timeout=45).strip()


def process(pid):
    return machine_helper("server_reservation").process(pid)


def alive(identity):
    return machine_helper("server_reservation").alive(identity)


def owned_runs():
    return machine_helper("server_reservation").owned_runs()


def resources(port):
    return machine_helper("start_v88_after_0600_20261020").resources(port)


def workload_inputs():
    return machine_helper("start_v96_root_scoped_36root_20261020").workload_inputs()


def checks(path, count):
    report = ET.parse(path).getroot()
    if any(list(report.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError(f"CPU qualification has non-passing cases: {path}")
    cases = list(report.iter("testcase"))
    if len({(row.get("classname"), row.get("name")) for row in cases}) != count or len(cases) != count:
        raise ValueError(f"CPU qualification case count changed: {path}")


class HoldChanged(ValueError):
    pass


def launch_time(hold):
    deadline = datetime.fromisoformat(hold["gpu_not_before"])
    if deadline.tzinfo is None or deadline < MINIMUM_TIME:
        raise HoldChanged("one-time GPU deadline is missing or earlier than user authorization")
    return deadline


def require_hold(hold, marker_sha256, at, marker_identity=None):
    if hold.get("automatic_resume_allowed") is not True:
        raise HoldChanged("one-time automatic resume is disabled")
    if not PAUSE.is_file() or sha256_file(PAUSE) != marker_sha256:
        raise HoldChanged("pause marker changed; preserve the newer hold or cancellation")
    if marker_identity is not None:
        observed = PAUSE.stat()
        if [observed.st_dev, observed.st_ino, observed.st_uid] != marker_identity:
            raise HoldChanged("pause marker was replaced; preserve the newer hold")
    return at >= launch_time(hold)


def current_cpu(hold):
    cpu_path = Path(hold["latest_cpu_qualification"])
    cpu = json.loads(cpu_path.read_bytes())
    if cpu["status"] != "cpu_qualified_gpu_validation_pending":
        raise ValueError("latest source qualification is not ready")
    for item in cpu["suites"].values():
        checks(Path(item["report"]), item["distinct_current_source_cpu_cases"])
        for path, expected in item["source_sha256"].items():
            if sha256_file(Path(path)) != expected:
                raise ValueError(f"qualification is stale: {path}")
    for path, expected in cpu["source_sha256"].items():
        if sha256_file(Path(path)) != expected:
            raise ValueError(f"qualification helper changed: {path}")
    policy = cpu["latest_policy"]
    if (
        policy["join_prefetch_trigger"] not in ("observed_eos", "selective_report_or_eos")
        or policy["execution_handoff"] is not False
        or policy["observed_eos_requires_service_forecast"] is not False
        or policy["mamba_prepare"] is not False
    ):
        raise ValueError("qualified policy differs from the authorized current policy")
    _require_clean(REPO)
    if command(["git", "-C", str(REPO), "branch", "--show-current"]) != "main":
        raise ValueError("freeze current development from main")
    engine = Path(cpu["candidate_engine"])
    canonical = git_source_state(
        engine, canonical_patch=REPO / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if canonical["actual_tree"] != cpu["canonical_patch"]["actual_tree"]:
        raise ValueError("native tree differs from qualified sources")
    return cpu_path, cpu, canonical


def environment(plan, cpu, checkout, engine):
    policy = cpu["latest_policy"]
    if (
        plan["root_count"] != 36 or plan["server_running"] != 8
        or plan["fanout_profile"] != "native_in_graph_nested_2to4"
        or plan["host_split"] != "80:20"
        or plan["context_tokens"] != 262144
        or plan["context_lifecycle"]["window_tokens"] != 262144
        or plan["context_lifecycle"]["compression_trigger_ratio"] != .95
        or len(plan["arrival_schedule"]) != 36
        or any(item["offset_seconds"] for item in plan["arrival_schedule"])
    ):
        raise ValueError("common workload differs from the current 36-root contract")
    env = fresh_pair_environment(
        plan, RUN, python=PYTHON, port=PORT, execution_handoff=False,
        arm_order="predictive_h2d native", host_numa_memory_policy="preferred",
    )
    env.update({
        "PYTHONPATH": str(checkout), "CUDA_VISIBLE_DEVICES": "0",
        "TOKENIZERS_PARALLELISM": "false", "SGLANG_SOURCE_CHECKOUT": str(engine),
        "WORKLOAD_MANIFEST": str(checkout / MANIFEST),
        "JOIN_PREFETCH_TRIGGER": policy["join_prefetch_trigger"],
        "JOIN_PREDICTION_MIN_FULL_BYTES": str(policy["selective_prediction_min_missing_full_bytes"]),
        "JOIN_PREDICTION_INTERVAL_MS": str(policy["selective_prediction_interval_ms"]),
        "JOIN_PREDICTION_MAX_CANDIDATES": str(policy["selective_prediction_max_candidates"]),
        "PREFETCH_LEAD_MS": str(policy["selective_prediction_max_advance_ms"]),
        "EOS_PROTOCOL_WINDOW_MS": str(policy["eos_to_native_arrival_startup_floor_ms"]),
        "BORROWABLE_PARENT_HEADROOM": "1", "REQUIRE_DELEGATION_SHAPE": "1",
        "HICACHE_SIZE_GB": "200", "HICACHE_WRITE_POLICY": "write_back",
        "DEVICE_FULL_TOKENS": "", "DEVICE_MAMBA_SLOTS": "",
        "BELIEFKV_EXPERIMENT_PAUSE_FILE": str(PAUSE),
    })
    return env


def frozen_engine(cpu, canonical):
    original = Path(cpu["candidate_engine"])
    target = REPO.parent / f".worktrees/sglang-v103-frozen-{canonical['actual_tree'][:12]}-20261021"
    patch = REPO / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    if not target.exists():
        revision = command(["git", "-C", str(original), "rev-parse", "HEAD"])
        subprocess.run(
            ["git", "-C", str(original), "worktree", "add", "--detach", str(target), revision],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "-C", str(target), "apply", str(patch)], check=True, capture_output=True)
    observed = git_source_state(target, canonical_patch=patch)["canonical_patch"]
    if observed["actual_tree"] != canonical["actual_tree"]:
        raise ValueError("frozen native engine does not match qualified tree")
    for source, expected in cpu["suites"]["engine"]["source_sha256"].items():
        path = Path(source)
        if path.is_relative_to(original) and sha256_file(target / path.relative_to(original)) != expected:
            raise ValueError(f"frozen native source changed: {source}")
    return target, observed


def prepare():
    hold = json.loads(HOLD.read_bytes())
    launch_time(hold)
    cpu_path, cpu, canonical = current_cpu(hold)
    if RUN.exists() or DRIVER_LOG.exists():
        raise FileExistsError("preserve existing experiment and driver log")
    revision = command(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    workload_paths, workload = workload_inputs()
    plan = json.loads(BASE_PLAN.read_bytes())
    engine, engine_state = frozen_engine(cpu, canonical)
    checkout = REPO.parent / f".worktrees/beliefkv-v103-frozen-{revision[:12]}-20261021"
    if not checkout.exists():
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "add", "--detach", str(checkout), revision],
            check=True, capture_output=True,
        )
    _require_clean(checkout)
    link = checkout / "third_party/sglang-v0.5.20"
    if not link.exists():
        link.symlink_to(engine, target_is_directory=True)
    if link.resolve() != engine.resolve():
        raise ValueError("execution checkout points to a different native engine")
    env = environment(plan, cpu, checkout, engine)
    sources = {
        path: sha256_file(checkout / path)
        for path in command([
            "git", "-C", str(checkout), "ls-files", "--",
            "beliefkv", "scripts", "configs", "patches",
        ]).splitlines()
    }
    for path, expected in sources.items():
        if sha256_file(REPO / path) != expected:
            raise ValueError(f"main changed while freezing: {path}")
    inputs = workload_paths | {
        BASE_PLAN, cpu_path, Path(cpu["engine_export"]), Path(cpu["benchmark"]),
        Path(__file__),
    }
    inputs.update(Path(machine_helper(name).__file__) for name in (
        "server_reservation", "start_v88_after_0600_20261020",
        "start_v96_root_scoped_36root_20261020",
    ))
    inputs.update(Path(item["report"]) for item in cpu["suites"].values())
    inputs.update(Path(path) for path in cpu["source_sha256"])
    tokenizer = Path(env["CONTEXT_TOKENIZER_PATH"])
    inputs.update(tokenizer / name for name in (
        "config.json", "tokenizer_config.json", "tokenizer.json",
    ))
    for key in ("H2D_SEED_ARTIFACT", "TOOL_TIMING_ARTIFACT", "TRANSFER_SERVICE_SEED"):
        if env.get(key):
            inputs.add(Path(env[key]))
    candidate = {
        "status": "cpu_qualified_not_gpu_accepted", "qualified_at": now().isoformat(),
        "code_commit": revision, "execution_checkout": str(checkout),
        "engine_checkout": str(engine), "engine": engine_state,
        "cpu_qualification": str(cpu_path), "cpu_qualification_sha256": sha256_file(cpu_path),
        "cpu_checks": cpu["suites"]["application"]["distinct_current_source_cpu_cases"],
        "native_cpu_checks": cpu["suites"]["engine"]["distinct_current_source_cpu_cases"],
        "source_sha256": sources, "workload": workload, "environment": env,
        "external_inputs": [
            {"path": str(path), "sha256": sha256_file(path)} for path in sorted(inputs)
        ],
        "gpu_not_before": launch_time(hold).isoformat(),
        "comparison_scope": (
            "Predictive first, native only after workload-shape qualification. "
            "Same native engine, tasks, arrivals and harness. Frontend cache differs "
            "by arm as authorized; this is not isolated prefetch attribution."
        ),
        "performance_accepted": False,
    }
    write_json(QUALIFIED, candidate)
    return candidate


def verify_latest():
    hold = json.loads(HOLD.read_bytes())
    cpu_path, cpu, _ = current_cpu(hold)
    revision = command(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    candidate = json.loads(QUALIFIED.read_bytes()) if QUALIFIED.exists() else None
    if candidate is None or (
        candidate["code_commit"] != revision
        or candidate["cpu_qualification"] != str(cpu_path)
        or candidate["cpu_qualification_sha256"] != sha256_file(cpu_path)
    ):
        # A fresh interpreter also reads the current harness and plan helpers.
        subprocess.run(
            [PYTHON, str(Path(__file__).resolve()), "--prepare"],
            cwd=REPO, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            check=True, capture_output=True,
        )
        candidate = json.loads(QUALIFIED.read_bytes())
    checkout = Path(candidate["execution_checkout"])
    _require_clean(checkout)
    for relative, expected in candidate["source_sha256"].items():
        if sha256_file(checkout / relative) != expected or sha256_file(REPO / relative) != expected:
            raise ValueError(f"frozen application changed: {relative}")
    for row in candidate["external_inputs"]:
        if sha256_file(Path(row["path"])) != row["sha256"]:
            raise ValueError(f"frozen input changed: {row['path']}")
    frozen = git_source_state(
        Path(candidate["engine_checkout"]),
        canonical_patch=checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if frozen["actual_tree"] != cpu["canonical_patch"]["actual_tree"]:
        raise ValueError("frozen engine changed since qualification")
    return candidate


def driver_environment(candidate, inherited):
    clean = {name: value for name, value in inherited.items() if not name.startswith("BELIEFKV_")}
    return {**clean, **candidate["environment"]}


def runtime_failure(candidate, telemetry, runtime):
    if telemetry and any(telemetry.get(key) for key in (
        "writer_error", "dropped_records", "failed_records",
    )):
        return "native_telemetry_failure"
    if runtime is not None:
        if runtime.get("physical_disabled") is True:
            return "physical_actions_disabled"
        env = candidate["environment"]
        selective = env["JOIN_PREFETCH_TRIGGER"] == "selective_report_or_eos"
        expected = {
            "min_full_bytes": int(env["JOIN_PREDICTION_MIN_FULL_BYTES"]),
            "interval_ms": float(env["JOIN_PREDICTION_INTERVAL_MS"]),
            "max_candidates": int(env["JOIN_PREDICTION_MAX_CANDIDATES"]),
        } if selective else {}
        policy = runtime.get("selective_join_prefetch") or {}
        if (
            runtime.get("join_prefetch_trigger") != env["JOIN_PREFETCH_TRIGGER"]
            or runtime.get("execution_handoff") is not False
            or policy.get("enabled") is not selective
            or any(policy.get(key) != value for key, value in expected.items())
        ):
            return "frozen_runtime_configuration_mismatch"
    return None


def last_runtime_state(path):
    if not path.exists():
        return None
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 2 * 1024 ** 2))
        lines = stream.read().splitlines()
    for line in reversed(lines):
        if b'"admission_runtime_state"' not in line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if row.get("event") == "admission_runtime_state":
            return row
    return None


def stop_owned(state, reason):
    identities = owned_runs().get(RUN, [])
    driver = state.get("driver")
    if driver is not None and alive(driver) and not any(
        item["pid"] == driver["pid"] for item in identities
    ):
        identities.append(driver)
    cutoff = RUN / "server_reservation_cutoff.json"
    if not cutoff.exists():
        write_json(cutoff, {
            "reason": reason, "stop_started_at": now().isoformat(), "run": str(RUN),
            "identities": identities, "administratively_interrupted": False, "errors": [],
        })
    machine_helper("server_reservation").stop_run(RUN, identities, state)


def watch():
    with (OPS / "v103_latest_after_hold_20261021.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        registration = json.loads(REGISTRATION.read_bytes())
        state = {
            "status": "waiting_until_authorized_time", "registered_at": now().isoformat(),
            "watcher": process(os.getpid()), "run_root": str(RUN),
            "registration": str(REGISTRATION),
        }
        write_json(STATE, state)
        try:
            consecutive = 0
            while True:
                hold = json.loads(HOLD.read_bytes())
                state["checked_at"] = now().isoformat()
                if sha256_file(Path(__file__)) != registration["operator_source_sha256"]:
                    raise HoldChanged("registered operator source changed")
                if not require_hold(
                    hold, registration["pause_sha256"], now(), registration["pause_identity"],
                ):
                    state["status"] = "waiting_until_authorized_time"
                    state["gpu_not_before"] = launch_time(hold).isoformat()
                    write_json(STATE, state)
                    time.sleep(15)
                    continue
                try:
                    candidate = verify_latest()
                    available = resources(PORT)
                    state.update(status="waiting_for_idle_gpu", resources=available)
                    state.pop("last_error", None)
                    consecutive = consecutive + 1 if available["ready"] else 0
                    if consecutive >= 2:
                        break
                except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as error:
                    consecutive = 0
                    state.update(status="waiting_for_current_qualification_or_resources", last_error=repr(error))
                write_json(STATE, state)
                time.sleep(15)
            candidate = verify_latest()
            if not require_hold(
                json.loads(HOLD.read_bytes()), registration["pause_sha256"], now(),
                registration["pause_identity"],
            ):
                raise HoldChanged("authorization is not current")
            if not resources(PORT)["ready"]:
                raise ValueError("resources changed before launch")
            subprocess.run([
                PYTHON, str(Path(candidate["execution_checkout"]) / "scripts/preflight_qwen35_host_pool.py"),
                "--memory-policy", "preferred", "--node", "1", "--size-gb", "200",
            ], check=True, capture_output=True, timeout=30)
            machine_helper("start_v88_after_0600_20261020").clear_stale_socket(PORT)
            candidate = verify_latest()
            if not resources(PORT)["ready"] or not require_hold(
                json.loads(HOLD.read_bytes()), registration["pause_sha256"], now(),
                registration["pause_identity"],
            ):
                raise HoldChanged("resources or user authorization changed before release")
            PAUSE.unlink()
            try:
                with DRIVER_LOG.open("xb") as output:
                    driver = subprocess.Popen(
                        ["bash", str(Path(candidate["execution_checkout"]) / "scripts/run_qwen35_long_repository_ab.sh")],
                        cwd=candidate["execution_checkout"],
                        env=driver_environment(candidate, os.environ),
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except Exception:
                if not PAUSE.exists():
                    PAUSE.write_text(registration["pause_text"])
                raise
            state.update(
                status="running_predictive_then_conditional_native", launched_at=now().isoformat(),
                candidate=candidate, driver=process(driver.pid), driver_log=str(DRIVER_LOG),
            )
            write_json(STATE, state)
            next_shape = 0.
            while driver.poll() is None:
                state["checked_at"] = now().isoformat()
                reason = "new_user_pause" if PAUSE.exists() else None
                arm = RUN / "predictive_h2d"
                try:
                    status_path = arm / "server/native_telemetry_status.json"
                    telemetry = json.loads(status_path.read_bytes()) if status_path.exists() else None
                    runtime = last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
                    reason = reason or runtime_failure(candidate, telemetry, runtime)
                    if time.monotonic() >= next_shape and not state.get("early_delegation_observed"):
                        next_shape = time.monotonic() + 60.
                        if (arm / "client_36/manifest.json").exists():
                            shape = audit_delegation(RUN)
                            state["delegation_checks"] = shape["checks"]
                            rejected = [
                                row["instance_id"] for row in shape["workflows"]
                                if (
                                    row["terminal_outcome"] is not None
                                    and not row["root_delegation_rounds"]
                                ) or (
                                    row["initial_root_fanout"] is not None
                                    and row["initial_root_fanout"] not in (2, 3, 4)
                                )
                            ]
                            if rejected:
                                reason = reason or "observed_delegation_workload_rejected"
                                state["rejected_workflows"] = rejected
                            state["early_delegation_observed"] = all(
                                passed for key, passed in shape["checks"].items()
                                if key != "terminal_results_complete"
                            )
                    state.pop("last_observation_error", None)
                except (OSError, ValueError, KeyError) as error:
                    # A torn observation is not evidence that the run stopped.
                    state["last_observation_error"] = repr(error)
                if reason is not None:
                    stop_owned(state, reason)
                    state["status"] = "administratively_stopped_for_observed_problem"
                    state["stop_reason"] = reason
                    break
                write_json(STATE, state)
                time.sleep(15)
            state.update(exit_code=driver.wait(), finished_at=now().isoformat())
            if state["status"] != "administratively_stopped_for_observed_problem":
                state["status"] = "completed" if state["exit_code"] == 0 else "experiment_failed"
            write_json(STATE, state)
        except HoldChanged as error:
            state.update(status="cancelled_hold_changed", last_error=str(error))
            write_json(STATE, state)
        except Exception as error:
            state.update(status="preflight_or_launch_failed", last_error=repr(error))
            write_json(STATE, state)
            raise


def register():
    if any(path.exists() for path in (REGISTRATION, STATE, LOG, DRIVER_LOG, RUN)):
        raise FileExistsError("preserve existing schedule, state and experiment")
    hold = json.loads(HOLD.read_bytes())
    marker = PAUSE.read_text()
    require_hold(hold, sha256_file(PAUSE), now())
    identity = PAUSE.stat()
    write_json(REGISTRATION, {
        "registered_at": now().isoformat(), "pause_sha256": sha256_file(PAUSE),
        "pause_text": marker, "gpu_not_before": launch_time(hold).isoformat(),
        "pause_identity": [identity.st_dev, identity.st_ino, identity.st_uid],
        "operator_source_sha256": sha256_file(Path(__file__)),
    })
    with LOG.open("xb") as output:
        watcher = subprocess.Popen(
            [PYTHON, str(Path(__file__).resolve()), "--watch"],
            cwd=REPO, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({"watcher_pid": watcher.pid, "state": str(STATE),
                      "gpu_not_before": launch_time(hold).isoformat()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if sum((args.prepare, args.register, args.watch)) != 1:
        parser.error("choose --prepare, --register or --watch")
    if args.prepare:
        candidate = prepare()
        print(json.dumps({key: candidate[key] for key in (
            "status", "code_commit", "execution_checkout", "engine_checkout",
            "cpu_checks", "native_cpu_checks", "gpu_not_before",
        )}, indent=2))
    elif args.register:
        register()
    else:
        watch()
