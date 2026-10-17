#!/usr/bin/env python3
"""Measure the native no-Host control after complete frozen V79b collection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import start_v79_checkpoint_diagnostic_20261016 as diagnostic
from scripts.launch_semantic_h2d_after_pair import _require_clean, _require_idle_port
from scripts.start_v77_native_close_20261016 import bound, load, now, operations, write_json

BASE = Path("/home/longhao/experiment")
MAIN = BASE / "BeliefKV"
APP = BASE / ".worktrees/beliefkv-v1413-native-no-host-20261016"
REFERENCE = diagnostic.REFERENCE
PREVIOUS = MAIN / "experiments/raw/qwen35_native_checkpoint_diagnostic_online_poisson_156root_20261016_v79b"
RUN = MAIN / "experiments/raw/qwen35_native_without_host_online_poisson_156root_20261016_v80"
REPORTS = MAIN / "experiments/reports"
WAIT_STATE = BASE / "ops/v79b_checkpoint_diagnostic_20261016.state.json"
WAIT_FINAL = REPORTS / "v1390_v79b_checkpoint_diagnostic_completion_20261016.json"
STATE = BASE / "ops/v80_native_without_host_20261016.state.json"
LOG = BASE / "ops/v80_native_without_host_20261016.log"
CPU = REPORTS / "v1423_native_without_host_compatible_final_cpu_20261016.xml"
QUALIFICATION = REPORTS / "v1414_native_without_host_qualification_20261016.json"
REGISTRATION = REPORTS / "v1415_v80_native_without_host_registration_20261016.json"
SUPERVISOR = REPORTS / "v1416_v80_native_without_host_supervisor_20261016.json"
STARTUP = REPORTS / "v1417_v80_native_without_host_actual_startup_20261016.json"
PLACEMENT = REPORTS / "v1418_v80_native_without_host_placement_20261016.json"
FINAL = REPORTS / "v1419_v80_native_without_host_complete_20261016.json"
PORT = 18484
PATCH = diagnostic.PATCH
ENGINE_TREE = diagnostic.ENGINE_TREE
DEVICE_FIELDS = (
    "pool_layout", "device_full_tokens", "device_mamba_slots",
    "device_full_bytes", "device_mamba_bytes", "device_total_bytes",
)
HOST_FIELDS = (
    "host_full_tokens", "host_mamba_slots",
    "host_full_bytes", "host_mamba_bytes", "host_total_bytes",
)
CHANGED_RUNTIME_PATHS = {
    "beliefkv/runtime/sglang_v0520_observer.py",
    "beliefkv/runtime/v0520_native_telemetry.py",
    "scripts/launch_qwen35_native_v0520.sh",
    "scripts/run_qwen35_native_regime_probe.sh",
}


def build_plan(reference, capacity, *, commit, manifest):
    plan = diagnostic.build_plan(reference, commit=commit, manifest=manifest)
    if (
        any(type(capacity.get(key)) is not int or capacity[key] <= 0 for key in DEVICE_FIELDS[1:])
        or capacity.get("pool_layout") != "static_separate_full_mamba"
        or capacity["device_total_bytes"] != capacity["device_full_bytes"] + capacity["device_mamba_bytes"]
    ):
        raise ValueError("the reference physical HBM census is incomplete")
    plan.update(
        host_gb=0, host_budget_gb=reference["host_gb"],
        demand_restore_policy="native_recompute_without_host",
        checkpoint_path_diagnostic={"enabled": False},
        fixed_device_capacity={key: capacity[key] for key in DEVICE_FIELDS},
        reference_host_capacity={key: capacity[key] for key in HOST_FIELDS},
        native_reference_run=str(REFERENCE / "native"),
        intentional_differences=[
            "Host allocation and transfers disabled; 200GB Host budget remains available.",
            "Physical HBM token/state limits are explicitly fixed to the reference census.",
        ],
        scope="Native no-Host engineering control; no new cache policy, prefetch or scheduling contribution.",
    )
    return plan


def collection_environment(plan):
    return {
        **diagnostic.collection_environment(plan),
        "PYTHONPATH": str(APP), "PORT": str(PORT), "RUN_ROOT": str(RUN / "native"),
        "WORKFLOW_ARRIVAL_SCHEDULE": str(RUN / "ab_plan.json"),
        "HICACHE_SIZE_GB": "0", "BELIEFKV_CHECKPOINT_PATH_SHADOW": "0",
        "DEVICE_FULL_TOKENS": str(plan["fixed_device_capacity"]["device_full_tokens"]),
        "DEVICE_MAMBA_SLOTS": str(plan["fixed_device_capacity"]["device_mamba_slots"]),
    }


def startup_configuration(serving, telemetry, manifest, capacity, plan):
    expected = {
        "status": "ready", "max_running_requests": 48, "context_length": 131072,
        "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
        "enable_mixed_chunk": False, "enable_prefill_delayer": False,
        "min_free_slots_delay": None, "enable_beliefkv_admission": False,
        "enable_hierarchical_cache": False, "enable_session_radix_cache": True,
        "mamba_max_states_per_path": -1, "mamba_full_memory_ratio": 0.9,
        "mamba_radix_cache_strategy": "extra_buffer",
        "max_mamba_cache_size": plan["fixed_device_capacity"]["device_mamba_slots"],
        "max_total_tokens": plan["fixed_device_capacity"]["device_full_tokens"],
    }
    if any(serving.get(key) != value for key, value in expected.items()):
        raise ValueError("actual native no-Host configuration differs")
    if (
        telemetry.get("host_tier_enabled") is not False
        or telemetry.get("checkpoint_path_shadow") is not False
        or telemetry.get("record_output_token_ids") is not False
        or telemetry.get("writer_error")
        or (telemetry.get("native_session_close_reclaim") or {}).get("enabled")
    ):
        raise ValueError("actual Host tier, diagnostic or reclamation differs")
    if (
        {key: capacity.get(key) for key in DEVICE_FIELDS} != plan["fixed_device_capacity"]
        or any(capacity.get(key) != 0 for key in HOST_FIELDS)
        or manifest["arrival_schedule"] != plan["arrival_schedule"]
        or manifest["arrival_schedule_sha256"] != plan["arrival_schedule_sha256"]
        or manifest["client_inflight_root_window"] != 156
        or manifest["config"]["concurrency"] != 156
        or not manifest["config"]["native_policy_baseline"]
    ):
        raise ValueError("actual physical HBM, Host allocation or workload differs")
    return expected


def source_state():
    _require_clean(APP)
    source = git_source_state(APP)
    engine = git_source_state(APP / "third_party/sglang-v0.5.20", canonical_patch=APP / PATCH)
    if (
        not engine["canonical_patch"]["matches_worktree"]
        or engine["canonical_patch"]["actual_tree"] != ENGINE_TREE
    ):
        raise ValueError("the original complete native engine is required")
    changed = subprocess.check_output([
        "git", "diff", "--name-only", load(PREVIOUS / "ab_plan.json")["code_commit"],
        source["commit"], "--", "beliefkv", "scripts/run_deepagents_swebench.py",
        "scripts/launch_qwen35_native_v0520.sh", "scripts/run_qwen35_native_regime_probe.sh",
    ], cwd=APP, text=True).splitlines()
    if set(changed) - CHANGED_RUNTIME_PATHS:
        raise ValueError("model, prompt, harness or policy code differs from the native diagnostic")
    return source, engine, changed


def verify_source(registration):
    for item in registration["inputs"]:
        if bound(Path(item["path"])) != item:
            raise ValueError("a frozen no-Host input changed")
    source, engine, _ = source_state()
    if source != registration["source"] or engine != registration["engine"]:
        raise ValueError("the frozen no-Host source changed")
    if registration["environment"] != collection_environment(load(RUN / "ab_plan.json")):
        raise ValueError("the registered no-Host environment differs")


def prepare():
    if any(path.exists() for path in (
        RUN, QUALIFICATION, REGISTRATION, SUPERVISOR, STARTUP, PLACEMENT, FINAL, STATE, LOG,
    )):
        raise FileExistsError("preserve existing V80 artifacts")
    source, engine, changed = source_state()
    reference = load(REFERENCE / "ab_plan.json")
    manifest = APP / Path(reference["workload_manifest"]).relative_to(MAIN)
    if sha256_file(manifest) != reference["workload_sha256"]:
        raise ValueError("the native task manifest differs")
    capacity_path = REFERENCE / "native/server/native_capacity_census.json"
    plan = build_plan(reference, load(capacity_path)["capacity"], commit=source["commit"], manifest=manifest)
    cases = list(ET.parse(CPU).getroot().iter("testcase"))
    if not cases or any(case.find(kind) is not None for case in cases for kind in ("failure", "error", "skipped")):
        raise ValueError("no-Host CPU qualification is incomplete")
    alive, _, _, _, _ = operations()
    previous = load(WAIT_STATE)
    if previous.get("run_root") != str(PREVIOUS) or not alive(previous["supervisor"]) or not alive(previous["driver"]):
        raise ValueError("the actual frozen V79b predecessor is not running")
    RUN.mkdir()
    write_json(RUN / "ab_plan.json", plan)
    write_json(QUALIFICATION, {
        "at": now(), "source": source, "engine": engine, "changed_paths": changed,
        "cpu": bound(CPU), "passing_cpu_cases": len(cases),
        "scope": "CPU and source qualification only; no GPU or performance evidence.",
    })
    inputs = [bound(path) for path in (
        APP / "scripts" / Path(__file__).name,
        APP / "scripts/start_v79_checkpoint_diagnostic_20261016.py",
        APP / "scripts/start_v77_native_close_20261016.py",
        APP / "scripts/observe_owned_scheduler_numa.py",
        BASE / "ops/server_reservation.py", BASE / "ops/start_v64_latest_read_feedback_20261015.py",
        BASE / "ops/observe_v60_startup.py",
        CPU, QUALIFICATION, RUN / "ab_plan.json", manifest,
        REFERENCE / "ab_plan.json", capacity_path,
        REPORTS / "v1387_v79b_checkpoint_observer_retry_registration_20261016.json",
        REPORTS / "v1389_v79b_checkpoint_diagnostic_actual_startup_20261016.json",
    )]
    registration = {
        "at": now(), "source": source, "engine": engine, "inputs": inputs,
        "environment": collection_environment(plan),
        "wait_supervisor": previous["supervisor"], "wait_driver": previous["driver"],
        "scope": "Wait for complete V79b collection and actual resource release; then one full native no-Host control.",
    }
    write_json(REGISTRATION, registration)
    verify_source(registration)
    print(str(REGISTRATION))


def predecessor_evidence(previous, registration):
    if (
        previous.get("status") != "completed" or previous.get("run_root") != str(PREVIOUS)
        or bound(WAIT_FINAL) != previous.get("result")
    ):
        raise ValueError("the predecessor did not finish complete diagnostic collection")
    for key in ("supervisor", "driver"):
        if any(previous.get(key, {}).get(field) != registration["wait_" + key].get(field)
               for field in ("pid", "start_ticks")):
            raise ValueError("the predecessor process identity changed")
    evidence, inputs = diagnostic.collection_inputs(
        PREVIOUS / "native", load(PREVIOUS / "ab_plan.json"), previous["original_client_exit_code"],
    )
    return {**evidence, "inputs": [*inputs, bound(WAIT_FINAL)]}


def verify_startup(registration, state):
    alive, _, process, _, first_service = operations()
    arm = RUN / "native"
    ready = load(arm / "server/native_telemetry_ready.json")
    scheduler = process(ready["scheduler_pid"])
    service = first_service(arm / "server/runtime_audit.jsonl")
    if not scheduler or not alive(scheduler) or service is None:
        return
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/get_server_info", timeout=5) as response:
        serving = json.load(response)
    capacity = load(arm / "server/native_capacity_census.json")["capacity"]
    settings = startup_configuration(
        serving, load(arm / "server/native_telemetry_status.json"),
        load(arm / "client_156/manifest.json"), capacity, load(RUN / "ab_plan.json"),
    )
    if not PLACEMENT.exists():
        subprocess.run([
            sys.executable, str(APP / "scripts/observe_owned_scheduler_numa.py"),
            "--run-root", str(RUN), "--arm", "native", "--output", str(PLACEMENT),
        ], check=True, timeout=45)
    placement = load(PLACEMENT)
    if (
        placement["capacity_census"]["capacity"] != capacity
        or placement["scheduler_identity"]["start_ticks"] != scheduler["start_ticks"]
        or not alive(scheduler)
    ):
        raise ValueError("actual scheduler identity or placement census differs")
    verify_source(registration)
    write_json(STARTUP, {
        "at": now(), "status": "actual_native_without_host_service_verified",
        "scheduler": scheduler, "driver": state["driver"], "first_actual_service": service,
        "settings": settings, "capacity": capacity, "placement": bound(PLACEMENT),
        "scope": "Real startup and capacity only; no JCT or task-quality acceptance.",
    })
    state["startup"] = bound(STARTUP)


def finish_collection(state, client_exit):
    arm = RUN / "native"
    evidence, inputs = diagnostic.collection_inputs(arm, load(RUN / "ab_plan.json"), client_exit)
    if (arm / "server/transfer_telemetry.jsonl").stat().st_size:
        raise ValueError("transfers were recorded despite the disabled Host tier")
    with (RUN / "timeline.log").open("x") as log:
        subprocess.run([
            sys.executable, str(APP / "scripts/render_p6_execution_timeline.py"),
            "--run-dir", str(arm), "--arm", "native",
            "--output-html", str(RUN / "timelines/native.html"),
        ], cwd=APP, env={**os.environ, "CUDA_VISIBLE_DEVICES": "999"},
            stdout=log, stderr=subprocess.STDOUT, check=True)
    write_json(FINAL, {
        "at": now(), "status": "complete_native_without_host_collection",
        **evidence, "inputs": inputs, "startup": state["startup"],
        "timeline": bound(RUN / "timelines/native.html"),
        "scope": "Full native no-Host control; preserve all quality, incomplete and guard labels.",
    })


def watch():
    registration = load(REGISTRATION)
    alive, owned_runs, process, resources, _ = operations()
    state = {
        "status": "waiting_for_complete_v79b", "supervisor": process(os.getpid()),
        "registration": bound(REGISTRATION), "run_root": str(RUN), "started_at": now(),
    }
    child = None
    try:
        verify_source(registration)
        while True:
            live = any(alive(registration[key]) for key in ("wait_supervisor", "wait_driver"))
            state.update(observed_at=now(), previous_processes_live=live)
            if not live:
                state["predecessor_collection"] = predecessor_evidence(load(WAIT_STATE), registration)
                if resources()["ready"] and not owned_runs() and not Path("/tmp/beliefkv-experiments.paused").exists():
                    break
                state["status"] = "waiting_for_gpu_and_resource_release"
            write_json(STATE, state)
            time.sleep(10)
        verify_source(registration)
        _require_idle_port(PORT)
        with (RUN / "native.launch.log").open("x") as log:
            child = subprocess.Popen(
                ["bash", str(APP / "scripts/run_qwen35_native_regime_probe.sh")],
                cwd=APP, env={**os.environ, **registration["environment"]},
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        state.update(status="running_native_without_host", driver=process(child.pid), launched_at=now())
        write_json(STATE, state)
        while child.poll() is None:
            if "startup" not in state:
                try:
                    verify_startup(registration, state)
                except (FileNotFoundError, urllib.error.URLError, TimeoutError):
                    pass
            telemetry = RUN / "native/server/native_telemetry_status.json"
            if telemetry.exists() and load(telemetry).get("writer_error"):
                raise ValueError("native no-Host telemetry writer failed")
            state["observed_at"] = now()
            write_json(STATE, state)
            time.sleep(10)
        if child.returncode not in (0, 1) or "startup" not in state:
            raise ValueError(f"native no-Host collection/startup failed: exit={child.returncode}")
        verify_source(registration)
        state.update(status="validating_complete_collection", original_client_exit_code=child.returncode)
        write_json(STATE, state)
        finish_collection(state, child.returncode)
        state.update(status="completed", result=bound(FINAL), finished_at=now())
    except Exception as error:
        if child is not None and child.poll() is None and alive(state["driver"]):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            while child.poll() is None:
                state.update(status="stopping_owned_driver", error=repr(error), observed_at=now())
                write_json(STATE, state)
                time.sleep(5)
        state.update(status="stopped_for_diagnosis", error=repr(error), finished_at=now())
    write_json(STATE, state)


def start():
    if any(path.exists() for path in (STATE, LOG, SUPERVISOR)):
        raise FileExistsError("preserve an existing V80 supervisor")
    verify_source(load(REGISTRATION))
    _, _, process, _, _ = operations()
    with LOG.open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(APP / "scripts" / Path(__file__).name), "--watch"],
            cwd=APP, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    record = {"at": now(), "supervisor": process(child.pid), "registration": bound(REGISTRATION)}
    write_json(SUPERVISOR, record)
    print(json.dumps(record))


if __name__ == "__main__":
    actions = {"--prepare": prepare, "--start": start, "--watch": watch}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("use --prepare, --start or --watch")
    actions[sys.argv[1]]()
