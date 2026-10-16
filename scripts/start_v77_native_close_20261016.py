#!/usr/bin/env python3
"""Freeze a native-close engineering baseline after the complete V76 arm."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
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

from beliefkv.experiments.arrival_schedule import (
    arrival_schedule_digest,
    load_workflow_arrivals,
)
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.launch_semantic_h2d_after_pair import _require_clean, _require_idle_port


BASE = Path("/home/longhao/experiment")
MAIN = BASE / "BeliefKV"
APP = BASE / ".worktrees/beliefkv-v1213-native-close-gpu-20261016"
REFERENCE = MAIN / "experiments/raw/qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback"
RUN = MAIN / "experiments/raw/qwen35_native_close_gc_online_poisson_156root_20261016_v77"
REPORTS = MAIN / "experiments/reports"
WAIT_STATE = BASE / "ops/v76_no_prefetch_20261016.state.json"
QUALITY_STATE = BASE / "ops/quality_eval/quality_v75_v76_cpu_20261016.state.json"
STATE = BASE / "ops/v77_native_close_20261016.state.json"
LOG = BASE / "ops/v77_native_close_20261016.log"
QUALIFIED_ENGINE = REPORTS / "v1211_native_session_close_launch_candidate_20261016.json"
QUALIFICATION = REPORTS / "v1214_v77_native_close_candidate_20261016.json"
REGISTRATION = REPORTS / "v1215_v77_native_close_registration_20261016.json"
STARTUP = REPORTS / "v1216_v77_native_close_actual_startup_20261016.json"
FINAL = REPORTS / "v1217_v77_native_close_completion_20261016.json"
SUPERVISOR = REPORTS / "v1218_v77_native_close_supervisor_20261016.json"
PLACEMENT = REPORTS / "v1219_v77_native_close_actual_placement_20261016.json"
PORT = 18481
PATCH = "patches/sglang-v0.5.20-native-session-close-staging.patch"
ENGINE_TREE = "76cf97d2f462db4f4edbc691cb7e56cf7c8e2568"

EXPECTED_SETTINGS = {
    "root_count": 156, "server_running": 48, "context_tokens": 131072,
    "max_completion_tokens": 8192, "sampling_seed": 21, "temperature": 0.0,
    "activation_wall_clock_seconds": 14400.0, "sandbox_command_timeout_s": 180,
    "recursion_limit": 2048, "finalization_reserve_steps": 32,
    "native_reactive_guard_profile": True, "completion_gate_enabled": False,
    "child_final_report_shadow": True, "child_finish_chunk_shadow": True,
    "stream_http_timing_shadow": True, "fanout_profile": "native_in_graph_2to4",
    "requested_children_per_round": [2, 4], "host_gb": 200,
    "host_split": "80:20", "host_numa_node": 1,
    "host_numa_memory_policy": "preferred", "mem_fraction_static": 0.94,
    "mamba_full_memory_ratio": 0.9, "native_write_policy": "write_back",
    "workflow_arrival_mode": "poisson", "workflow_arrival_seed": 21,
    "workflow_arrival_rate_per_second": 1 / 15,
    "workflow_arrival_batch_size": 0, "workflow_arrival_batch_interval_ms": 0,
    "demand_restore_policy": "native_prefill_layerwise_dependencies",
}
MATCHED_FIELDS = tuple(EXPECTED_SETTINGS) + (
    "arrival_schedule", "arrival_schedule_sha256", "workload_sha256",
    "workload_instance_ids_in_manifest_order", "in_graph_initial_tool_choice",
    "in_graph_initial_parallel_tool_calls", "in_graph_initial_fanout_generation_bounds",
    "in_graph_named_task_constraint",
)
CHECKS = (
    "v1205_native_session_close_lifecycle_cpu_20261016.xml",
    "v1206_native_close_counter_telemetry_cpu_20261016.xml",
    "v1212_native_session_close_launcher_constraints_cpu_20261016.xml",
    "v1213_native_close_baseline_driver_cpu_20261016.xml",
)


def now():
    return datetime.now().astimezone().isoformat()


def load(path):
    return json.loads(path.read_bytes())


def bound(path):
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def operations():
    ops = BASE / "ops"
    if str(ops) not in sys.path:
        sys.path.insert(0, str(ops))
    from server_reservation import alive, owned_runs, process
    from start_v64_latest_read_feedback_20261015 import resources
    from observe_v60_startup import first_actual_service
    return alive, owned_runs, process, resources, first_actual_service


def build_plan(reference, *, commit, patch_sha, manifest):
    for key, expected in EXPECTED_SETTINGS.items():
        if reference.get(key) != expected:
            raise ValueError(f"reference setting differs: {key}")
    if reference["shared_system_configuration"]["write_back_batch_nodes"] != 32:
        raise ValueError("the reference must already use write-back batch32")
    ids = reference["workload_instance_ids_in_manifest_order"]
    arrivals = reference["arrival_schedule"]
    if (
        len(ids) != 156 or len(set(ids)) != 156
        or [row["instance_id"] for row in arrivals] != ids
        or arrival_schedule_digest(arrivals) != reference["arrival_schedule_sha256"]
    ):
        raise ValueError("reference arrival identities or digest differ")
    plan = {key: deepcopy(reference[key]) for key in MATCHED_FIELDS}
    plan.update(
        schema_version=1, code_commit=commit, sglang_patch_sha256=patch_sha,
        workload_manifest=str(manifest), order=["native"],
        scope="Development native-close engineering baseline; not a causal or novelty claim.",
        native_reference_run=str(REFERENCE), formal_acceptance=False,
        same_seed_is_not_same_trajectory=True,
        native_session_close_reclaim=True,
        shared_system_configuration={
            "write_back_batch_nodes": 32,
            "policy_scope": "native_device_FULL_order_ancestor_chain",
        },
        policy_configuration={"native": {
            "control": False, "resident_first": False, "final_stage_priority": False,
            "prepare_host": False, "predictive_h2d": False, "execution_handoff": False,
            "native_session_close_reclaim": True,
        }},
        cpu_quality_overlap_possible=True,
        quality_overlap_semantics=(
            "Existing V75/V76 grading may use one CPU core and 8GiB during this "
            "development run. Record actual overlap; do not claim matched formal resources."
        ),
    )
    return plan


def collection_environment(plan):
    environment = {
        "PYTHON": sys.executable, "PYTHONPATH": str(APP),
        "CUDA_VISIBLE_DEVICES": "0", "PORT": str(PORT),
        "RUN_ROOT": str(RUN / "native"), "ROOT_COUNT": str(plan["root_count"]),
        "WORKLOAD_MANIFEST": plan["workload_manifest"],
        "WORKFLOW_ARRIVAL_SCHEDULE": str(RUN / "ab_plan.json"),
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "SAMPLING_SEED": str(plan["sampling_seed"]),
        "ACTIVATION_WALL_CLOCK_SECONDS": str(plan["activation_wall_clock_seconds"]),
        "HOST_SPLIT": plan["host_split"], "HOST_NUMA_MEMORY_POLICY": "preferred",
        "HICACHE_SIZE_GB": "200", "HICACHE_WRITE_POLICY": "write_back",
        "FANOUT_PROFILE": plan["fanout_profile"], "CHILD_FINAL_REPORT_SHADOW": "1",
        "NATIVE_POLICY_BASELINE": "1", "AB_MODE": "off", "CONFIRMED_JOIN_CANARY": "0",
        "PREPARE_HOST": "0", "EXECUTION_HANDOFF": "0", "ENABLE_TOOL_TIMING": "0",
        "SGLANG_PATCH_FLAVOR": "native_session_close",
        "SGLANG_NATIVE_SESSION_CLOSE_RECLAIM": "1",
        "SGLANG_HICACHE_WRITE_BACK_BATCH_NODES": "32",
        "BELIEFKV_CONTEXT_SUMMARY_INPUT_MODE": "xml",
    }
    for name in (
        "WORKFLOW_PROGRESS", "WORKFLOW_ATTAINED_WORK", "TOOL_COMPLETION_RESTORE",
        "RECORD_OUTPUT_TOKEN_IDS", "EMIT_SEMANTIC_TEXT",
        "ENABLE_FINAL_STAGE_PREFETCH", "ENABLE_FINAL_STAGE_PRIORITY",
        "ENABLE_PREPARE_HOST", "ENABLE_TOOL_PREFETCH", "ENABLE_EXECUTION_HANDOFF",
        "ENABLE_RESIDENT_FIRST", "H2D_OPPORTUNITY_SAMPLING",
    ):
        environment[f"BELIEFKV_{name}"] = "0"
    return environment


def source_state():
    _require_clean(APP)
    source = git_source_state(APP)
    engine = git_source_state(APP / "third_party/sglang-v0.5.20", canonical_patch=APP / PATCH)
    if engine["canonical_patch"]["actual_tree"] != ENGINE_TREE:
        raise ValueError("the native-close engine tree differs")
    qualified = load(QUALIFIED_ENGINE)
    if (
        sha256_file(APP / "scripts/launch_qwen35_native_v0520.sh")
        != qualified["application"]["launcher_sha256"]
        or engine["canonical_patch"]["sha256"] != qualified["engine"]["combined_patch_sha256"]
    ):
        raise ValueError("the qualified launcher or combined patch differs")
    return source, engine


def application_changes(reference, source):
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", reference["code_commit"], source["commit"],
         "--", "beliefkv", "scripts/run_deepagents_swebench.py"],
        cwd=APP, text=True,
    ).splitlines()
    allowed = {
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/v0520_native_telemetry.py",
    }
    if set(changed) - allowed:
        raise ValueError("native application/model/tool code differs from the reference")
    return changed


def cpu_receipts():
    receipts = []
    for name in CHECKS:
        path = REPORTS / name
        cases = list(ET.parse(path).getroot().iter("testcase"))
        if not cases or any(
            case.find(tag) is not None
            for case in cases for tag in ("failure", "error", "skipped")
        ):
            raise ValueError(f"CPU qualification is incomplete: {name}")
        receipts.append({**bound(path), "passed_cases": len(cases)})
    return receipts


def prepare():
    if any(path.exists() for path in (
        RUN, QUALIFICATION, REGISTRATION, STATE, LOG, SUPERVISOR, STARTUP, PLACEMENT, FINAL,
    )):
        raise FileExistsError("preserve existing native-close artifacts")
    source, engine = source_state()
    reference = load(REFERENCE / "ab_plan.json")
    manifest = APP / Path(reference["workload_manifest"]).relative_to(MAIN)
    if sha256_file(manifest) != reference["workload_sha256"]:
        raise ValueError("frozen task manifest differs")
    plan = build_plan(
        reference, commit=source["commit"],
        patch_sha=engine["canonical_patch"]["sha256"], manifest=manifest,
    )
    changes = application_changes(reference, source)
    checks = cpu_receipts()
    alive, _, process, _, _ = operations()
    previous = load(WAIT_STATE)
    identity = previous["supervisor"]
    if not alive(identity):
        raise ValueError("the exact V76 waiting/running supervisor is missing")
    helpers = [
        bound(BASE / "ops" / name) for name in (
            "server_reservation.py", "start_v64_latest_read_feedback_20261015.py",
            "observe_v60_startup.py", "observe_v55_numa_placement.py",
        )
    ]
    RUN.mkdir(parents=True)
    write_json(RUN / "ab_plan.json", plan)
    load_workflow_arrivals(RUN / "ab_plan.json", instance_ids=plan["workload_instance_ids_in_manifest_order"])
    qualification = {
        "at": now(), "source": source, "engine": engine, "checks": checks,
        "engine_launcher_qualification": bound(QUALIFIED_ENGINE),
        "native_application_changed_paths": changes,
        "native_application_semantics": (
            "Model, prompt and tool code unchanged. Native admission is disabled; "
            "output-ID collection remains off and close counters use existing low-frequency snapshots."
        ),
        "scope": "CPU qualification and fixed configuration only; no GPU gain or paper novelty established.",
    }
    write_json(QUALIFICATION, qualification)
    registration = {
        "at": now(), "status": "prepared_waiting_for_complete_v76",
        "source": source, "engine": engine, "qualification": bound(QUALIFICATION),
        "helper": bound(Path(__file__).resolve()), "helpers": helpers,
        "plan": bound(RUN / "ab_plan.json"),
        "reference_plan": bound(REFERENCE / "ab_plan.json"),
        "reference_capacity": bound(REFERENCE / "native/server/native_capacity_census.json"),
        "wait_state": str(WAIT_STATE), "wait_supervisor": identity,
        "wait_registration": previous["registration"],
        "environment": collection_environment(plan),
        "prepare_process": process(os.getpid()),
    }
    write_json(REGISTRATION, registration)
    print(json.dumps({"registration": str(REGISTRATION), "source_commit": source["commit"],
                      "arrival_schedule_sha256": plan["arrival_schedule_sha256"]}))


def verify_source(registration):
    if bound(Path(__file__).resolve()) != registration["helper"]:
        raise ValueError("execute the exact frozen helper from its registered checkout")
    for receipt in (
        registration["helper"], registration["qualification"], registration["plan"],
        registration["reference_plan"], registration["reference_capacity"],
        registration["wait_registration"],
        *registration["helpers"],
    ):
        if bound(Path(receipt["path"])) != receipt:
            raise ValueError("a registered source, helper or plan changed")
    qualified = load(QUALIFICATION)
    if bound(QUALIFIED_ENGINE) != qualified["engine_launcher_qualification"]:
        raise ValueError("the engine launcher qualification changed")
    if any(bound(Path(item["path"]))["sha256"] != item["sha256"] for item in qualified["checks"]):
        raise ValueError("a CPU receipt changed")
    source, engine = source_state()
    if source != registration["source"] or engine != registration["engine"]:
        raise ValueError("the frozen application or engine changed")


def prerequisite_status(previous, supervisor_live, driver_live):
    if supervisor_live or driver_live:
        return "waiting_for_complete_v76"
    if previous.get("status") != "completed" or previous.get("error"):
        raise ValueError("V76 ended without a complete successful collection")
    return "ready_for_resource_check"


def startup_configuration(serving, telemetry, manifest, capacity, plan, reference_capacity):
    expected = {
        "status": "ready", "max_running_requests": 48, "context_length": 131072,
        "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
        "enable_mixed_chunk": False, "enable_prefill_delayer": False,
        "min_free_slots_delay": None, "enable_beliefkv_admission": False,
    }
    if any(serving.get(key) != value for key, value in expected.items()):
        raise ValueError("actual native serving configuration differs")
    stats = telemetry.get("native_session_close_reclaim")
    if not isinstance(stats, dict) or not stats.get("enabled"):
        raise ValueError("native-close reclamation is not actually enabled")
    if telemetry.get("writer_error") or telemetry.get("record_output_token_ids"):
        raise ValueError("unexpected writer error or output-token collection")
    if telemetry.get("write_back_batch", {}).get("max_nodes") != 32:
        raise ValueError("actual write-back batch differs")
    if (
        manifest["arrival_schedule"] != plan["arrival_schedule"]
        or manifest["arrival_schedule_sha256"] != plan["arrival_schedule_sha256"]
        or manifest["client_inflight_root_window"] != 156
        or manifest["config"]["concurrency"] != 156
    ):
        raise ValueError("actual task arrival or concurrency differs")
    if capacity != reference_capacity:
        raise ValueError("actual physical capacity differs")
    return expected


def verify_startup(registration, state):
    alive, _, process, _, first_service = operations()
    arm = RUN / "native"
    ready = load(arm / "server/native_telemetry_ready.json")
    scheduler = process(ready["scheduler_pid"])
    service = first_service(arm / "server/runtime_audit.jsonl")
    telemetry = load(arm / "server/native_telemetry_status.json")
    if not scheduler or not alive(scheduler) or service is None:
        return
    if telemetry.get("native_session_close_reclaim") is None:
        return
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/get_server_info", timeout=5) as response:
        serving = json.load(response)
    manifest = load(arm / "client_156/manifest.json")
    capacity = load(arm / "server/native_capacity_census.json")["capacity"]
    plan = load(RUN / "ab_plan.json")
    expected = startup_configuration(
        serving, telemetry, manifest, capacity, plan,
        load(REFERENCE / "native/server/native_capacity_census.json")["capacity"],
    )
    if not PLACEMENT.exists():
        subprocess.run(
            [sys.executable, str(BASE / "ops/observe_v55_numa_placement.py"),
             "--run-root", str(RUN), "--arm", "native", "--output", str(PLACEMENT)],
            check=True, timeout=45,
        )
    placement = load(PLACEMENT)
    if (
        placement["capacity_census"]["capacity"] != capacity
        or placement["scheduler_identity"]["start_ticks"] != scheduler["start_ticks"]
        or not alive(scheduler)
    ):
        raise ValueError("actual placement or scheduler identity differs")
    verify_source(registration)
    overlap = load(QUALITY_STATE) if QUALITY_STATE.exists() else {}
    record = {
        "at": now(), "status": "actual_source_service_capacity_arrivals_and_close_gc_verified",
        "scheduler": scheduler, "driver": state["driver"],
        "first_actual_service": service, "settings": expected,
        "capacity": capacity, "plan": registration["plan"],
        "native_session_close_reclaim": telemetry["native_session_close_reclaim"],
        "placement": bound(PLACEMENT),
        "cpu_quality_state": {key: overlap.get(key) for key in (
            "status", "supervisor", "grading_started", "observed_at",
        )},
        "cpu_quality_supervisor_live": bool(overlap.get("supervisor") and alive(overlap["supervisor"])),
        "scope": "Actual startup only; no causal performance or quality conclusion.",
    }
    write_json(STARTUP, record)
    state["startup"] = bound(STARTUP)


def postprocess():
    arm = RUN / "native"
    summary = load(arm / "client_156/summary.json")
    ids = [row["instance_id"] for row in summary["workflows"]]
    plan = load(RUN / "ab_plan.json")
    if summary["workflow_count"] != 156 or sorted(ids) != sorted(plan["workload_instance_ids_in_manifest_order"]):
        raise ValueError("collection does not cover every planned task")
    commands = [
        ("audit_native_h2d_sources.py", "--arm", str(arm), "--output", str(arm / "native_h2d_sources.json")),
        ("audit_prefetch_lifecycle.py", "--arm", str(arm), "--output", str(arm / "prefetch_lifecycle.json")),
        ("audit_native_memory_opportunity.py", "--arm", str(arm), "--output", str(arm / "memory_opportunity.json")),
        ("render_p6_execution_timeline.py", "--run-dir", str(arm), "--arm", "native",
         "--output-html", str(RUN / "timelines/native.html")),
        ("compare_native_policy_runs.py", "--arm", f"native_close={arm}",
         "--arm", f"native={REFERENCE / 'native'}",
         "--timeline", f"native_close={RUN / 'timelines/native.json.gz'}",
         "--timeline", f"native={REFERENCE / 'timelines/native.json.gz'}",
         "--output", str(RUN / "native_reference_comparison.json")),
        ("summarize_semantic_h2d_ab.py", "--cleanup-arm", str(arm)),
    ]
    for command in commands:
        with (RUN / f"postprocess_{command[0]}.log").open("x") as log:
            subprocess.run(
                [sys.executable, str(APP / "scripts" / command[0]), *command[1:]],
                cwd=APP, env={**os.environ, "CUDA_VISIBLE_DEVICES": "999"},
                stdout=log, stderr=subprocess.STDOUT, check=True,
            )
    telemetry = load(arm / "server/native_telemetry_status.json")
    stats = telemetry["native_session_close_reclaim"]
    comparison = load(RUN / "native_reference_comparison.json")
    if not comparison["same_task_set"] or not comparison["same_physical_pool_capacity"]:
        raise ValueError("completed comparison has unmatched tasks or physical pools")
    write_json(FINAL, {
        "at": now(), "status": "complete_development_collection_quality_ungraded",
        "summary": bound(arm / "client_156/summary.json"),
        "outcomes": dict(Counter(row["outcome"] for row in summary["workflows"])),
        "native_session_close_reclaim": stats,
        "physical_reclamation_observed": bool(stats["counts"]["discarded_nodes"]),
        "comparison": bound(RUN / "native_reference_comparison.json"),
        "scope": "Historical development baseline. Keep all tasks, incomplete and guard labels; quality and causal gains unproven.",
    })


def watch():
    registration = load(REGISTRATION)
    alive, owned_runs, process, resources, _ = operations()
    state = {
        "status": "waiting_for_complete_v76", "supervisor": process(os.getpid()),
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
                previous_run = Path(previous["run_root"])
                if not (previous_run / "native_reference_comparison.json").exists():
                    raise ValueError("V76 postprocessing is incomplete")
                previous_summary = load(previous_run / "reactive/client_156/summary.json")
                previous_ids = [row["instance_id"] for row in previous_summary["workflows"]]
                if (
                    previous_summary["workflow_count"] != 156
                    or sorted(previous_ids) != sorted(load(RUN / "ab_plan.json")["workload_instance_ids_in_manifest_order"])
                ):
                    raise ValueError("V76 did not cover every planned task")
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
        state.update(status="running_native_close", driver=process(child.pid), launched_at=now())
        write_json(STATE, state)
        while child.poll() is None:
            if "startup" not in state:
                try:
                    verify_startup(registration, state)
                except (FileNotFoundError, urllib.error.URLError, TimeoutError):
                    pass
            telemetry_path = RUN / "native/server/native_telemetry_status.json"
            if telemetry_path.exists():
                telemetry = load(telemetry_path)
                if telemetry.get("writer_error"):
                    raise ValueError("native telemetry writer failed")
                state["native_session_close_reclaim"] = telemetry.get("native_session_close_reclaim")
            state["observed_at"] = now()
            write_json(STATE, state)
            time.sleep(10)
        if child.returncode != 0 or "startup" not in state:
            raise ValueError(f"native-close collection/startup failed: exit={child.returncode}")
        state.update(status="postprocessing", driver_exit_code=child.returncode)
        write_json(STATE, state)
        verify_source(registration)
        postprocess()
        state.update(status="completed", result=bound(FINAL), finished_at=now())
    except Exception as error:
        if child is not None and child.poll() is None and alive(state["driver"]):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            while child.poll() is None:
                state.update(
                    status="stopping_owned_driver", error=repr(error), observed_at=now(),
                )
                write_json(STATE, state)
                time.sleep(5)
        state.update(status="stopped_for_diagnosis", error=repr(error), finished_at=now())
    write_json(STATE, state)


def start():
    if any(path.exists() for path in (STATE, LOG, SUPERVISOR)):
        raise FileExistsError("preserve an existing supervisor")
    registration = load(REGISTRATION)
    verify_source(registration)
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
