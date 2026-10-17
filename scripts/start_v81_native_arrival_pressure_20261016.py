#!/usr/bin/env python3
"""Qualify the denser Poisson workload after complete frozen V79b collection."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.arrival_schedule import load_workflow_arrivals
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import start_v79_checkpoint_diagnostic_20261016 as diagnostic
from scripts.launch_semantic_h2d_after_pair import _require_clean, _require_idle_port
from scripts.start_v77_native_close_20261016 import bound, load, now, operations, write_json
from scripts.start_v80_native_without_host_20261016 import predecessor_evidence

BASE = Path("/home/longhao/experiment")
MAIN = BASE / "BeliefKV"
APP = BASE / ".worktrees/beliefkv-v1447-native-arrival10-gpu-20261016"
REFERENCE = diagnostic.REFERENCE
PREVIOUS = MAIN / "experiments/raw/qwen35_native_checkpoint_diagnostic_online_poisson_156root_20261016_v79b"
RUN = MAIN / "experiments/raw/qwen35_native_online_poisson10s_156root_20261016_v81"
REPORTS = MAIN / "experiments/reports"
PROFILE = APP / "configs/migration/qwen35_native_poisson_156root_mean10s_2026-10-16.json"
SCHEDULE = APP / "experiments/plans/qwen35_native_poisson_156root_mean10s_20261016/workflow_arrivals.json"
PREPARATION = REPORTS / "v1446_native_poisson_10s_workload_preparation_20261016.json"
WAIT_STATE = BASE / "ops/v79b_checkpoint_diagnostic_20261016.state.json"
STATE = BASE / "ops/v81_native_arrival_pressure_20261016.state.json"
LOG = BASE / "ops/v81_native_arrival_pressure_20261016.log"
QUALIFICATION = REPORTS / "v1447_v81_native_arrival_pressure_qualification_20261016.json"
REGISTRATION = REPORTS / "v1448_v81_native_arrival_pressure_registration_20261016.json"
SUPERVISOR = REPORTS / "v1449_v81_native_arrival_pressure_supervisor_20261016.json"
STARTUP = REPORTS / "v1450_v81_native_arrival_pressure_actual_startup_20261016.json"
PLACEMENT = REPORTS / "v1451_v81_native_arrival_pressure_placement_20261016.json"
FINAL = REPORTS / "v1452_v81_native_arrival_pressure_complete_20261016.json"
PORT = 18485
PATCH = diagnostic.PATCH
ENGINE_TREE = diagnostic.ENGINE_TREE


def build_plan(reference, profile, schedule, capacity, *, commit, manifest):
    plan = diagnostic.build_plan(reference, commit=commit, manifest=manifest)
    for key in (
        "root_count", "server_running", "host_gb", "host_split", "context_tokens",
        "max_completion_tokens", "sandbox_command_timeout_s",
        "activation_wall_clock_seconds", "workflow_arrival_mode",
        "workflow_arrival_seed", "workflow_arrival_batch_size",
        "workflow_arrival_batch_interval_ms",
    ):
        if profile[key] != reference[key]:
            raise ValueError(f"workload candidate changes another setting: {key}")
    if (
        profile["workflow_arrival_rate_per_second"] != 0.1
        or profile["workflow_arrival_mean_interval_seconds"] != 10
        or profile["reference_native_policy"] != reference["policy_configuration"]["native"]
        or schedule["workflow_arrival_rate_per_second"] != 0.1
        or schedule["workflow_arrival_seed"] != reference["workflow_arrival_seed"]
        or [row["instance_id"] for row in schedule["arrival_schedule"]]
        != reference["workload_instance_ids_in_manifest_order"]
    ):
        raise ValueError("the prepared native policy or arrival stream differs")
    plan.update(
        workflow_arrival_rate_per_second=0.1,
        arrival_schedule=deepcopy(schedule["arrival_schedule"]),
        arrival_schedule_sha256=schedule["arrival_schedule_sha256"],
        checkpoint_path_diagnostic={"enabled": False},
        fixed_device_capacity={key: capacity[key] for key in (
            "device_full_tokens", "device_mamba_slots",
        )},
        workload_candidate_profile=bound(PROFILE),
        contextual_15s_native_reference=str(REFERENCE / "native"),
        matched_historical_performance_control=False,
        intentional_differences=[
            "Poisson mean arrival interval 15s to 10s; same distinct tasks/order/seed.",
            "No continuation-prefix diagnostic; original native policy and resources.",
        ],
        scope="One native workload qualification; no new cache policy or performance claim.",
    )
    return plan


def collection_environment(plan):
    return {
        **diagnostic.collection_environment(plan),
        "PYTHONPATH": str(APP), "PORT": str(PORT), "RUN_ROOT": str(RUN / "native"),
        "WORKFLOW_ARRIVAL_SCHEDULE": str(RUN / "ab_plan.json"),
        "ARRIVAL_RATE_PER_SECOND": "0.1",
        "BELIEFKV_CHECKPOINT_PATH_SHADOW": "0",
        "DEVICE_FULL_TOKENS": str(plan["fixed_device_capacity"]["device_full_tokens"]),
        "DEVICE_MAMBA_SLOTS": str(plan["fixed_device_capacity"]["device_mamba_slots"]),
    }


def source_state():
    _require_clean(APP)
    source = git_source_state(APP)
    engine = git_source_state(APP / "third_party/sglang-v0.5.20", canonical_patch=APP / PATCH)
    if (
        not engine["canonical_patch"]["matches_worktree"]
        or engine["canonical_patch"]["actual_tree"] != ENGINE_TREE
    ):
        raise ValueError("the unchanged native engine is required")
    previous = load(PREVIOUS / "ab_plan.json")
    changed = subprocess.check_output([
        "git", "diff", "--name-only", previous["code_commit"], source["commit"], "--",
        "beliefkv", "scripts/run_deepagents_swebench.py",
        "scripts/launch_qwen35_native_v0520.sh", "scripts/run_qwen35_native_regime_probe.sh",
    ], cwd=APP, text=True).splitlines()
    if set(changed) - {
        "beliefkv/runtime/sglang_v0520_observer.py",
        "beliefkv/runtime/v0520_native_telemetry.py",
        "scripts/launch_qwen35_native_v0520.sh",
        "scripts/run_qwen35_native_regime_probe.sh",
    }:
        raise ValueError("model, prompt, harness or policy code differs from frozen native")
    return source, engine, changed


def verify_source(registration):
    for item in registration["inputs"]:
        if bound(Path(item["path"])) != item:
            raise ValueError("a frozen workload source, arrival table or helper changed")
    source, engine, _ = source_state()
    if source != registration["source"] or engine != registration["engine"]:
        raise ValueError("the frozen native workload source changed")
    if registration["environment"] != collection_environment(load(RUN / "ab_plan.json")):
        raise ValueError("the registered native workload environment differs")


def prepare():
    if any(path.exists() for path in (
        RUN, QUALIFICATION, REGISTRATION, SUPERVISOR, STARTUP, PLACEMENT, FINAL, STATE, LOG,
    )):
        raise FileExistsError("preserve existing V81 artifacts")
    source, engine, changed = source_state()
    profile, schedule = load(PROFILE), load(SCHEDULE)
    reference_path = MAIN / profile["reference_plan"]
    if sha256_file(reference_path) != profile["reference_plan_sha256"]:
        raise ValueError("the original workload plan changed")
    reference = load(reference_path)
    manifest = APP / Path(reference["workload_manifest"]).relative_to(MAIN)
    if sha256_file(manifest) != reference["workload_sha256"]:
        raise ValueError("the task manifest differs")
    ids = reference["workload_instance_ids_in_manifest_order"]
    arrivals = load_workflow_arrivals(SCHEDULE, instance_ids=ids)
    preparation = load(PREPARATION)
    if (
        sha256_file(SCHEDULE) != preparation["output"]["sha256"]
        or schedule["arrival_schedule_sha256"] != preparation["arrival_schedule_sha256"]
        or len(arrivals) != 156
    ):
        raise ValueError("the qualified arrival schedule differs")
    capacity_path = REFERENCE / "native/server/native_capacity_census.json"
    plan = build_plan(
        reference, profile, schedule, load(capacity_path)["capacity"],
        commit=source["commit"], manifest=manifest,
    )
    alive, _, _, _, _ = operations()
    previous = load(WAIT_STATE)
    if any(key not in previous for key in ("supervisor", "driver")):
        raise ValueError("the predecessor identities are missing")
    if previous["status"] != "completed" and not (
        alive(previous["supervisor"]) and alive(previous["driver"])
    ):
        raise ValueError("the predecessor is neither running nor complete")
    RUN.mkdir()
    write_json(RUN / "ab_plan.json", plan)
    load_workflow_arrivals(RUN / "ab_plan.json", instance_ids=ids)
    write_json(QUALIFICATION, {
        "at": now(), "source": source, "engine": engine, "changed_paths": changed,
        "arrival_preparation": bound(PREPARATION), "validated_arrival_count": len(arrivals),
        "scope": "Source/settings/arrival-loader qualification only; no new policy tests or GPU evidence.",
    })
    inputs = [bound(path) for path in (
        APP / "scripts" / Path(__file__).name,
        APP / "scripts/start_v79_checkpoint_diagnostic_20261016.py",
        APP / "scripts/start_v77_native_close_20261016.py",
        APP / "scripts/start_v80_native_without_host_20261016.py",
        APP / "scripts/observe_owned_scheduler_numa.py",
        BASE / "ops/server_reservation.py", BASE / "ops/start_v64_latest_read_feedback_20261015.py",
        BASE / "ops/observe_v60_startup.py", PROFILE, SCHEDULE, PREPARATION,
        QUALIFICATION, RUN / "ab_plan.json", manifest, reference_path, capacity_path,
        REPORTS / "v1387_v79b_checkpoint_observer_retry_registration_20261016.json",
        REPORTS / "v1389_v79b_checkpoint_diagnostic_actual_startup_20261016.json",
    )]
    write_json(REGISTRATION, {
        "at": now(), "source": source, "engine": engine, "inputs": inputs,
        "environment": collection_environment(plan),
        "wait_supervisor": previous["supervisor"], "wait_driver": previous["driver"],
        "scope": "After complete V79b and actual resource release, qualify one native 10s-arrival workload.",
    })
    verify_source(load(REGISTRATION))
    print(str(REGISTRATION))


def verify_startup(registration, state):
    alive, _, process, _, first_service = operations()
    arm = RUN / "native"
    scheduler = process(load(arm / "server/native_telemetry_ready.json")["scheduler_pid"])
    service = first_service(arm / "server/runtime_audit.jsonl")
    if not scheduler or not alive(scheduler) or service is None:
        return
    plan = load(RUN / "ab_plan.json")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/get_server_info", timeout=5) as response:
        serving = json.load(response)
    expected = {
        "status": "ready", "max_running_requests": 48, "context_length": 131072,
        "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
        "enable_mixed_chunk": False, "enable_prefill_delayer": False,
        "min_free_slots_delay": None, "enable_beliefkv_admission": False,
        "enable_hierarchical_cache": True, "enable_session_radix_cache": True,
        "mamba_max_states_per_path": -1, "mamba_full_memory_ratio": 0.9,
        "mamba_radix_cache_strategy": "extra_buffer",
        "max_total_tokens": plan["fixed_device_capacity"]["device_full_tokens"],
        "max_mamba_cache_size": plan["fixed_device_capacity"]["device_mamba_slots"],
    }
    if any(serving.get(key) != value for key, value in expected.items()):
        raise ValueError("actual native workload configuration differs")
    manifest = load(arm / "client_156/manifest.json")
    capacity = load(arm / "server/native_capacity_census.json")["capacity"]
    telemetry = load(arm / "server/native_telemetry_status.json")
    if (
        capacity != load(REFERENCE / "native/server/native_capacity_census.json")["capacity"]
        or manifest["arrival_schedule"] != plan["arrival_schedule"]
        or manifest["arrival_schedule_sha256"] != plan["arrival_schedule_sha256"]
        or manifest["client_inflight_root_window"] != 156
        or manifest["config"]["concurrency"] != 156
        or not manifest["config"]["native_policy_baseline"]
        or telemetry.get("host_tier_enabled") is not True
        or telemetry.get("checkpoint_path_shadow") is not False
        or telemetry.get("record_output_token_ids") is not False
        or telemetry.get("writer_error")
        or (telemetry.get("native_session_close_reclaim") or {}).get("enabled")
        or telemetry.get("write_back_batch", {}).get("max_nodes") != 32
    ):
        raise ValueError("actual arrivals, physical capacity, policy or diagnostic differs")
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
        raise ValueError("actual scheduler identity or NUMA capacity differs")
    verify_source(registration)
    write_json(STARTUP, {
        "at": now(), "status": "actual_native_10s_arrivals_service_verified",
        "scheduler": scheduler, "driver": state["driver"], "first_actual_service": service,
        "settings": expected, "capacity": capacity, "placement": bound(PLACEMENT),
        "scope": "Startup/settings only; not workload suitability, quality or performance acceptance.",
    })
    state["startup"] = bound(STARTUP)


def finish_collection(state, client_exit):
    arm = RUN / "native"
    evidence, inputs = diagnostic.collection_inputs(arm, load(RUN / "ab_plan.json"), client_exit)
    with (RUN / "timeline.log").open("x") as log:
        subprocess.run([
            sys.executable, str(APP / "scripts/render_p6_execution_timeline.py"),
            "--run-dir", str(arm), "--arm", "native",
            "--output-html", str(RUN / "timelines/native.html"),
        ], cwd=APP, env={**os.environ, "CUDA_VISIBLE_DEVICES": "999"},
            stdout=log, stderr=subprocess.STDOUT, check=True)
    write_json(FINAL, {
        "at": now(), "status": "complete_native_arrival_pressure_collection",
        **evidence, "inputs": inputs, "startup": state["startup"],
        "timeline": bound(RUN / "timelines/native.html"),
        "scope": "Complete workload collection; suitability and quality require subsequent analysis.",
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
        state.update(status="running_native_arrival_pressure", driver=process(child.pid), launched_at=now())
        write_json(STATE, state)
        while child.poll() is None:
            if "startup" not in state:
                try:
                    verify_startup(registration, state)
                except (FileNotFoundError, urllib.error.URLError, TimeoutError):
                    pass
            telemetry = RUN / "native/server/native_telemetry_status.json"
            if telemetry.exists() and load(telemetry).get("writer_error"):
                raise ValueError("native workload telemetry writer failed")
            state["observed_at"] = now()
            write_json(STATE, state)
            time.sleep(10)
        if child.returncode not in (0, 1) or "startup" not in state:
            raise ValueError(f"native workload collection/startup failed: exit={child.returncode}")
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
        raise FileExistsError("preserve an existing V81 supervisor")
    verify_source(load(REGISTRATION))
    _, _, process, _, _ = operations()
    with LOG.open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(APP / "scripts" / Path(__file__).name), "--watch"],
            cwd=APP, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    write_json(SUPERVISOR, {
        "at": now(), "supervisor": process(child.pid), "registration": bound(REGISTRATION),
    })
    print(str(SUPERVISOR))


if __name__ == "__main__":
    actions = {"--prepare": prepare, "--start": start, "--watch": watch}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("use --prepare, --start or --watch")
    actions[sys.argv[1]]()
