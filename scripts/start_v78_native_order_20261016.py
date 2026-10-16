#!/usr/bin/env python3
"""Freeze the V76 native-order ablation and continue after complete V77."""

from __future__ import annotations

from collections import Counter
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
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.arrival_schedule import arrival_schedule_digest, load_workflow_arrivals
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.launch_semantic_h2d_after_pair import _require_clean, _require_idle_port, fresh_pair_environment
from scripts.start_v77_native_close_20261016 import (
    BASE, EXPECTED_SETTINGS, MATCHED_FIELDS, bound, load, now, operations, write_json,
)

MAIN = BASE / "BeliefKV"
APP = BASE / ".worktrees/beliefkv-v1278-native-order-gpu-20261016"
REFERENCE = MAIN / "experiments/raw/qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback"
BASELINE = MAIN / "experiments/raw/qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch"
RUN = MAIN / "experiments/raw/qwen35_native_reactive_online_poisson_156root_20261016_v78_native_order"
REPORTS = MAIN / "experiments/reports"
WAIT_STATE = BASE / "ops/v77_native_close_20261016.state.json"
QUALITY_STATE = BASE / "ops/quality_eval/quality_v75_v76_cpu_20261016.state.json"
STATE = BASE / "ops/v78_native_order_20261016.state.json"
LOG = BASE / "ops/v78_native_order_20261016.log"
QUALIFICATION = REPORTS / "v1281_v78_native_order_candidate_20261016.json"
REGISTRATION = REPORTS / "v1282_v78_native_order_registration_20261016.json"
STARTUP = REPORTS / "v1283_v78_native_order_actual_startup_20261016.json"
FINAL = REPORTS / "v1284_v78_native_order_completion_20261016.json"
SUPERVISOR = REPORTS / "v1285_v78_native_order_supervisor_20261016.json"
PLACEMENT = REPORTS / "v1286_v78_native_order_actual_placement_20261016.json"
PORT = 18482
PATCH = "patches/sglang-v0.5.20-beliefkv-staging.patch"
ENGINE_TREE = "53020ce3efef3ed362e9af0db77147b39070f064"
ORDER_QUALIFICATION = REPORTS / "v1252_native_prefill_order_qualification_20261016.json"
CHECKS = ("v1280_v78_native_order_final_cpu_20261016.xml",)
POLICY = {
    "control": True, "resident_first": True, "final_stage_priority": True,
    "prepare_host": True, "predictive_h2d": False,
    "h2d_opportunity_sampling": False, "semantic_text_stream": False,
    "execution_handoff": False,
}
RUNTIME = {
    "physical_disabled": False, "prepare_host": True, "resident_first": True,
    "final_stage_priority": True, "final_stage_prefetch": False,
    "tool_prefetch": False, "semantic_worker_configured": False,
    "tool_timing_only": True, "tool_predictor_configured": True,
    "execution_handoff": False, "h2d_opportunity_sampling": False,
    "prefill_ordering_mode": "native",
}


def build_plan(reference, *, commit, patch_sha, manifest):
    if reference.get("order") != ["reactive"] or reference.get("policy_configuration") != {"reactive": POLICY}:
        raise ValueError("the reference must be the unmodified V76 no-prefetch policy")
    for key, expected in EXPECTED_SETTINGS.items():
        if reference.get(key) != expected:
            raise ValueError(f"V76 configuration differs: {key}")
    if (
        reference["shared_system_configuration"]["write_back_batch_nodes"] != 32
        or reference["sglang_patch_sha256"] != patch_sha
    ):
        raise ValueError("the V76 physical engine must remain unchanged")
    ids, arrivals = reference["workload_instance_ids_in_manifest_order"], reference["arrival_schedule"]
    if (
        len(ids) != 156 or len(set(ids)) != 156
        or [row["instance_id"] for row in arrivals] != ids
        or arrival_schedule_digest(arrivals) != reference["arrival_schedule_sha256"]
    ):
        raise ValueError("the matched arrival identities or digest differ")
    plan = deepcopy(reference)
    plan.update(
        code_commit=commit, workload_manifest=str(manifest),
        prefill_ordering_mode="native", formal_acceptance=False,
        scope="Development native-order isolation against V76; historical native is a reference, not causal proof.",
        capacity_bypass_policy="native_capacity_only;runtime_custom_bypass_disabled",
        development_ablation=(
            "Retain V76 workload, engine, PREPARE, tool timing and lifecycle; "
            "disable extra workflow/residency/aging/final/attained-work ordering "
            "and custom capacity bypass. Configured priority booleans remain true "
            "for compatibility; prefill_ordering_mode records effective behavior."
        ),
        cpu_quality_overlap_possible=True,
    )
    plan["policy_configuration"]["reactive"]["prefill_ordering_mode"] = "native"
    return plan


def collection_environment(plan, reference):
    environment = fresh_pair_environment(
        reference, RUN, python=sys.executable, port=PORT, arm_order="reactive",
        native_reference_run=REFERENCE, execution_handoff=False,
        host_numa_memory_policy="preferred",
    )
    environment.update(
        RESUME_PENDING="1", CUDA_VISIBLE_DEVICES="0",
        PYTHONPATH=f"{APP}:{(APP / 'third_party/sglang-v0.5.20/python').resolve()}",
        WORKLOAD_MANIFEST=plan["workload_manifest"],
        BELIEFKV_NATIVE_PREFILL_ORDER="1",
        BELIEFKV_WORKFLOW_ATTAINED_WORK="1", BELIEFKV_WORKFLOW_PROGRESS="0",
        BELIEFKV_TOOL_COMPLETION_RESTORE="0", BELIEFKV_CONTEXT_SUMMARY_INPUT_MODE="xml",
        BELIEFKV_RECORD_OUTPUT_TOKEN_IDS="0", BELIEFKV_EMIT_SEMANTIC_TEXT="0",
        SGLANG_PATCH_FLAVOR="staging", SGLANG_NATIVE_SESSION_CLOSE_RECLAIM="0",
        SGLANG_HICACHE_WRITE_BACK_BATCH_NODES="32",
    )
    for field, variable in (
        ("semantic_artifact", "SEMANTIC_REPORT_ARTIFACT"),
        ("h2d_seed_artifact", "H2D_SEED_ARTIFACT"),
        ("tool_timing_artifact", "TOOL_TIMING_ARTIFACT"),
        ("transfer_service_seed", "TRANSFER_SERVICE_SEED"),
    ):
        environment[variable] = plan[field]
    return environment


def source_state():
    _require_clean(APP)
    source = git_source_state(APP)
    engine = git_source_state(APP / "third_party/sglang-v0.5.20", canonical_patch=APP / PATCH)
    if not engine["canonical_patch"]["matches_worktree"] or engine["canonical_patch"]["actual_tree"] != ENGINE_TREE:
        raise ValueError("the unchanged complete V75/V76 engine is required")
    ordering = load(ORDER_QUALIFICATION)
    if sha256_file(APP / "beliefkv/runtime/sglang_v0520_runtime.py") != ordering["source_sha256"]["beliefkv/runtime/sglang_v0520_runtime.py"]:
        raise ValueError("the CPU-qualified ordering runtime changed")
    changes = subprocess.check_output(
        ["git", "diff", "--name-only", load(BASELINE / "ab_plan.json")["code_commit"],
         source["commit"], "--", "beliefkv", "scripts/run_deepagents_swebench.py",
         "scripts/run_qwen35_native_regime_probe.sh", "scripts/run_qwen35_semantic_h2d_ab.sh"],
        cwd=APP, text=True,
    ).splitlines()
    if set(changes) - {
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/v0520_native_telemetry.py",
    }:
        raise ValueError("application, tools or collection behavior differs from V76")
    return source, engine, changes


def prepare():
    if any(path.exists() for path in (
        RUN, QUALIFICATION, REGISTRATION, STATE, LOG, STARTUP, FINAL, SUPERVISOR, PLACEMENT,
    )):
        raise FileExistsError("preserve existing V78 artifacts")
    source, engine, changes = source_state()
    reference = load(BASELINE / "ab_plan.json")
    manifest = APP / Path(reference["workload_manifest"]).relative_to(MAIN)
    if sha256_file(manifest) != reference["workload_sha256"]:
        raise ValueError("the task manifest changed")
    plan = build_plan(
        reference, commit=source["commit"],
        patch_sha=engine["canonical_patch"]["sha256"], manifest=manifest,
    )
    native = load(REFERENCE / "ab_plan.json")
    if any(plan[field] != native[field] for field in MATCHED_FIELDS):
        raise ValueError("native tasks, arrivals or resource settings differ")
    checks, identities = [], set()
    for name in CHECKS:
        path = REPORTS / name
        document = ET.parse(path)
        cases = list(document.getroot().iter("testcase"))
        if not cases or any(
            case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")
        ):
            raise ValueError("the CPU qualification is incomplete")
        identities.update((case.get("classname"), case.get("name")) for case in cases)
        checks.append(bound(path))
    alive, _, process, _, _ = operations()
    previous = load(WAIT_STATE)
    if not previous.get("supervisor") or not alive(previous["supervisor"]):
        raise ValueError("the exact V77 supervisor is missing")
    environment = collection_environment(plan, native)
    RUN.mkdir(parents=True)
    write_json(RUN / "ab_plan.json", plan)
    load_workflow_arrivals(RUN / "ab_plan.json", instance_ids=plan["workload_instance_ids_in_manifest_order"])
    write_json(QUALIFICATION, {
        "at": now(), "source": source, "engine": engine, "checks": checks,
        "unique_cpu_test_cases": len(identities),
        "ordering_qualification": bound(ORDER_QUALIFICATION),
        "application_changed_paths": changes,
        "scope": "CPU/configuration qualification; no GPU speedup, quality or novelty established.",
    })
    helpers = [
        bound(BASE / "ops" / name) for name in (
            "server_reservation.py", "start_v64_latest_read_feedback_20261015.py",
            "observe_v60_startup.py", "observe_v55_numa_placement.py",
        )
    ]
    registration = {
        "at": now(), "status": "prepared_waiting_for_complete_v77",
        "source": source, "engine": engine, "qualification": bound(QUALIFICATION),
        "helper": bound(Path(__file__).resolve()), "helpers": helpers,
        "plan": bound(RUN / "ab_plan.json"),
        "v76_plan": bound(BASELINE / "ab_plan.json"),
        "reference_plan": bound(REFERENCE / "ab_plan.json"),
        "reference_capacity": bound(REFERENCE / "native/server/native_capacity_census.json"),
        "wait_registration": previous["registration"],
        "wait_supervisor": previous["supervisor"],
        "wait_recovery": previous.get("postprocessing_recovery_continuation"),
        "environment": environment, "prepare_process": process(os.getpid()),
    }
    write_json(REGISTRATION, registration)
    print(json.dumps({"registration": str(REGISTRATION), "source": source["commit"]}))


def verify_source(registration):
    if bound(Path(__file__).resolve()) != registration["helper"]:
        raise ValueError("execute the exact frozen V78 helper")
    receipts = [
        registration[key] for key in (
            "helper", "qualification", "plan", "v76_plan", "reference_plan",
            "reference_capacity", "wait_registration",
        )
    ] + registration["helpers"]
    if registration.get("wait_recovery"):
        receipts.append(registration["wait_recovery"])
    qualification = load(QUALIFICATION)
    receipts.extend(qualification["checks"])
    receipts.append(qualification["ordering_qualification"])
    if any(bound(Path(item["path"])) != item for item in receipts):
        raise ValueError("a registered source, plan, receipt or helper changed")
    source, engine, _ = source_state()
    if source != registration["source"] or engine != registration["engine"]:
        raise ValueError("the frozen application or engine changed")
    if registration["environment"] != collection_environment(load(RUN / "ab_plan.json"), load(REFERENCE / "ab_plan.json")):
        raise ValueError("the frozen launch environment differs")


def prerequisite_status(previous, supervisor_live, driver_live):
    if supervisor_live or driver_live:
        return "waiting_for_complete_v77"
    if previous.get("status") != "completed" or previous.get("error"):
        raise ValueError("V77 ended without successful complete collection")
    return "ready_for_resource_check"


def latest_runtime():
    path = RUN / "reactive/opportunities/admission_opportunities.jsonl"
    if not path.exists():
        return None
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 524288))
        lines = stream.read().splitlines()
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if row.get("event") == "admission_runtime_state":
            return row
    return None


def startup_configuration(serving, telemetry, runtime, manifest, capacity, plan, reference_capacity):
    expected = {
        "status": "ready", "max_running_requests": 48, "context_length": 131072,
        "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
        "enable_mixed_chunk": False, "enable_prefill_delayer": False,
        "min_free_slots_delay": None, "enable_beliefkv_admission": True,
    }
    if any(serving.get(key) != value for key, value in expected.items()):
        raise ValueError("actual serving configuration differs")
    if any(runtime.get(key) != value for key, value in RUNTIME.items()):
        raise ValueError("actual native-order runtime differs")
    counts = runtime.get("counts") or {}
    if counts.get("native_prefill_order_plans", 0) <= 0 or any(counts.get(key, 0) for key in (
        "prefetch_native_issued", "resident_first_ordered", "workflow_work_head_ordered",
        "final_priority_ordered", "prefetch_priority_ordered",
    )):
        raise ValueError("native ordering was not exercised or extra priorities/prefetch ran")
    if (
        telemetry.get("writer_error") or telemetry.get("record_output_token_ids")
        or (telemetry.get("native_session_close_reclaim") or {}).get("enabled")
        or telemetry.get("write_back_batch", {}).get("max_nodes") != 32
    ):
        raise ValueError("unexpected telemetry, close-GC or write-back configuration")
    if (
        manifest["arrival_schedule"] != plan["arrival_schedule"]
        or manifest["arrival_schedule_sha256"] != plan["arrival_schedule_sha256"]
        or manifest["client_inflight_root_window"] != 156
        or manifest["config"]["concurrency"] != 156
        or capacity != reference_capacity
    ):
        raise ValueError("actual arrivals, concurrency or physical capacity differs")
    return expected


def verify_startup(registration, state):
    alive, _, process, _, first_service = operations()
    arm = RUN / "reactive"
    ready = load(arm / "server/native_telemetry_ready.json")
    scheduler = process(ready["scheduler_pid"])
    service, runtime = first_service(arm / "server/runtime_audit.jsonl"), latest_runtime()
    if not scheduler or not alive(scheduler) or service is None or runtime is None:
        return
    telemetry = load(arm / "server/native_telemetry_status.json")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/get_server_info", timeout=5) as response:
        serving = json.load(response)
    capacity = load(arm / "server/native_capacity_census.json")["capacity"]
    settings = startup_configuration(
        serving, telemetry, runtime, load(arm / "client_156/manifest.json"),
        capacity, load(RUN / "ab_plan.json"),
        load(REFERENCE / "native/server/native_capacity_census.json")["capacity"],
    )
    if not PLACEMENT.exists():
        subprocess.run(
            [sys.executable, str(BASE / "ops/observe_v55_numa_placement.py"),
             "--run-root", str(RUN), "--arm", "reactive", "--output", str(PLACEMENT)],
            check=True, timeout=45,
        )
    placement = load(PLACEMENT)
    if (
        placement["capacity_census"]["capacity"] != capacity
        or placement["scheduler_identity"]["start_ticks"] != scheduler["start_ticks"]
        or not alive(scheduler)
    ):
        raise ValueError("actual NUMA placement or scheduler identity differs")
    verify_source(registration)
    overlap = load(QUALITY_STATE) if QUALITY_STATE.exists() else {}
    write_json(STARTUP, {
        "at": now(), "status": "actual_native_order_source_service_arrivals_capacity_verified",
        "scheduler": scheduler, "driver": state["driver"], "first_actual_service": service,
        "settings": settings, "runtime_configuration": RUNTIME,
        "native_prefill_order_plans": runtime["counts"]["native_prefill_order_plans"],
        "configured_priorities_are_not_effective_ordering": True,
        "capacity": capacity, "plan": registration["plan"], "placement": bound(PLACEMENT),
        "cpu_quality_state": overlap,
        "scope": "Actual startup only; no final performance or quality conclusion.",
    })
    state["startup"] = bound(STARTUP)


def postprocess():
    arm = RUN / "reactive"
    summary = load(arm / "client_156/summary.json")
    plan = load(RUN / "ab_plan.json")
    ids = [row["instance_id"] for row in summary["workflows"]]
    if summary["workflow_count"] != 156 or sorted(ids) != sorted(plan["workload_instance_ids_in_manifest_order"]):
        raise ValueError("not every planned task was collected")
    comparison = load(RUN / "native_reference_comparison.json")
    if not comparison["same_task_set"] or not comparison["same_physical_pool_capacity"]:
        raise ValueError("completed comparison has unmatched tasks or pools")
    if not (RUN / "timelines/reactive.html").is_file():
        raise ValueError("the complete HTML timeline is missing")
    runtime = latest_runtime()
    if not runtime or runtime.get("prefill_ordering_mode") != "native" or runtime.get("physical_disabled"):
        raise ValueError("the final runtime does not confirm native ordering")
    write_json(FINAL, {
        "at": now(), "status": "complete_development_collection_quality_ungraded",
        "summary": bound(arm / "client_156/summary.json"),
        "outcomes": dict(Counter(row["outcome"] for row in summary["workflows"])),
        "comparison": bound(RUN / "native_reference_comparison.json"),
        "prefill_ordering_mode": runtime["prefill_ordering_mode"],
        "native_prefill_order_plans": runtime["counts"].get("native_prefill_order_plans"),
        "scope": "Keep all outcomes and guard labels; official quality and causal gains remain unproven.",
    })


def watch():
    registration = load(REGISTRATION)
    alive, owned_runs, process, resources, _ = operations()
    state = {
        "status": "waiting_for_complete_v77", "supervisor": process(os.getpid()),
        "registration": bound(REGISTRATION), "run_root": str(RUN), "started_at": now(),
    }
    child = None
    try:
        verify_source(registration)
        while True:
            previous = load(WAIT_STATE)
            supervisor_live = alive(registration["wait_supervisor"])
            driver_live = bool(previous.get("driver") and alive(previous["driver"]))
            state.update(
                observed_at=now(), previous_status=previous["status"],
                wait_supervisor_live=supervisor_live, wait_driver_live=driver_live,
            )
            state["status"] = prerequisite_status(previous, supervisor_live, driver_live)
            if state["status"] == "ready_for_resource_check":
                result = previous.get("result")
                if not result or bound(Path(result["path"])) != result:
                    raise ValueError("V77 completion evidence is missing or changed")
                previous_summary = load(Path(previous["run_root"]) / "native/client_156/summary.json")
                if sorted(row["instance_id"] for row in previous_summary["workflows"]) != sorted(load(RUN / "ab_plan.json")["workload_instance_ids_in_manifest_order"]):
                    raise ValueError("V77 did not collect the full matched task set")
                if resources()["ready"] and not owned_runs() and not Path("/tmp/beliefkv-experiments.paused").exists():
                    break
                state["status"] = "waiting_for_gpu_and_resource_release"
            write_json(STATE, state)
            time.sleep(10)
        verify_source(registration)
        _require_idle_port(PORT)
        child = subprocess.Popen(
            ["bash", str(APP / "scripts/run_qwen35_semantic_h2d_ab.sh")],
            cwd=APP, env={**os.environ, **registration["environment"]}, start_new_session=True,
        )
        state.update(status="running_reactive_native_order", driver=process(child.pid), launched_at=now())
        write_json(STATE, state)
        while child.poll() is None:
            runtime = latest_runtime()
            if runtime and (runtime.get("physical_disabled") or runtime["counts"].get("prefetch_native_issued")):
                raise ValueError("runtime failure or unexpected predictive H2D")
            if "startup" not in state:
                try:
                    verify_startup(registration, state)
                except (FileNotFoundError, urllib.error.URLError, TimeoutError):
                    pass
            telemetry_path = RUN / "reactive/server/native_telemetry_status.json"
            if telemetry_path.exists() and load(telemetry_path).get("writer_error"):
                raise ValueError("native telemetry writer failed")
            state.update(observed_at=now(), prefill_ordering_mode=(runtime or {}).get("prefill_ordering_mode"))
            write_json(STATE, state)
            time.sleep(10)
        if child.returncode != 0 or "startup" not in state:
            raise ValueError(f"native-order collection/startup failed: exit={child.returncode}")
        verify_source(registration)
        postprocess()
        state.update(status="completed", result=bound(FINAL), finished_at=now())
    except Exception as error:
        if child is not None and child.poll() is None and state.get("driver") and alive(state["driver"]):
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
        raise FileExistsError("preserve an existing V78 supervisor")
    verify_source(load(REGISTRATION))
    _, _, process, _, _ = operations()
    with LOG.open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch"],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    record = {"at": now(), "supervisor": process(child.pid), "registration": bound(REGISTRATION)}
    write_json(SUPERVISOR, record)
    print(json.dumps(record))


if __name__ == "__main__":
    actions = {"--prepare": prepare, "--start": start, "--watch": watch}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("use --prepare, --start or --watch")
    actions[sys.argv[1]]()
