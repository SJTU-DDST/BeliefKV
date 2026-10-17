#!/usr/bin/env python3
"""Collect native continuation-prefix evidence after the frozen V78b finishes."""

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

from beliefkv.experiments.arrival_schedule import arrival_schedule_digest
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.launch_semantic_h2d_after_pair import _require_clean, _require_idle_port
from scripts.start_v77_native_close_20261016 import (
    BASE, EXPECTED_SETTINGS, MATCHED_FIELDS, bound, load, now, operations, write_json,
)

MAIN = BASE / "BeliefKV"
APP = BASE / ".worktrees/beliefkv-v1366-checkpoint-diagnostic-gpu-20261016"
PREVIOUS_APP = BASE / ".worktrees/beliefkv-v1278-native-order-gpu-20261016"
PREVIOUS = MAIN / "experiments/raw/qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order"
REFERENCE = MAIN / "experiments/raw/qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback"
RUN = MAIN / "experiments/raw/qwen35_native_checkpoint_diagnostic_online_poisson_156root_20261016_v79"
REPORTS = MAIN / "experiments/reports"
WAIT_STATE = BASE / "ops/v78b_native_order_20261016.state.json"
WAIT_REGISTRATION = REPORTS / "v1326_v78b_native_order_retry_registration_20261016.json"
QUALITY_STATE = BASE / "ops/quality_eval/quality_v77_cpu_20261016.state.json"
STATE = BASE / "ops/v79_checkpoint_diagnostic_20261016.state.json"
LOG = BASE / "ops/v79_checkpoint_diagnostic_20261016.log"
CPU = REPORTS / "v1374_v79_checkpoint_diagnostic_frozen_path_cpu_20261016.xml"
QUALIFICATION = REPORTS / "v1367_v79_checkpoint_diagnostic_candidate_20261016.json"
REGISTRATION = REPORTS / "v1368_v79_checkpoint_diagnostic_registration_20261016.json"
SUPERVISOR = REPORTS / "v1369_v79_checkpoint_diagnostic_supervisor_20261016.json"
STARTUP = REPORTS / "v1370_v79_checkpoint_diagnostic_actual_startup_20261016.json"
FINAL = REPORTS / "v1371_v79_checkpoint_diagnostic_completion_20261016.json"
PLACEMENT = REPORTS / "v1372_v79_checkpoint_diagnostic_placement_20261016.json"
PROBE_QUALIFICATION = REPORTS / "v1352_main_integration_qualification_20261016.json"
PORT = 18483
PATCH = "patches/sglang-v0.5.20-beliefkv-staging.patch"
ENGINE_TREE = "53020ce3efef3ed362e9af0db77147b39070f064"
POLICY = {
    "control": False, "resident_first": False, "final_stage_priority": False,
    "prepare_host": False, "predictive_h2d": False, "execution_handoff": False,
    "native_session_close_reclaim": False,
}
CHANGED_PATHS = {
    "beliefkv/runtime/checkpoint_path_probe.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/v0520_native_telemetry.py",
    "scripts/launch_qwen35_native_v0520.sh",
    "scripts/run_qwen35_native_regime_probe.sh",
    "scripts/run_qwen35_semantic_h2d_ab.sh",
}


def build_plan(reference, *, commit, manifest):
    if any(reference.get(key) != value for key, value in EXPECTED_SETTINGS.items()):
        raise ValueError("native workload or resource settings changed")
    ids, arrivals = reference["workload_instance_ids_in_manifest_order"], reference["arrival_schedule"]
    if (
        len(ids) != 156 or len(set(ids)) != 156
        or [row["instance_id"] for row in arrivals] != ids
        or arrival_schedule_digest(arrivals) != reference["arrival_schedule_sha256"]
        or reference["shared_system_configuration"]["write_back_batch_nodes"] != 32
    ):
        raise ValueError("native identities, arrivals or write-back batch changed")
    plan = {key: deepcopy(reference[key]) for key in MATCHED_FIELDS}
    plan.update(
        schema_version=1, code_commit=commit, workload_manifest=str(manifest),
        sglang_patch_sha256=reference["sglang_patch_sha256"],
        order=["native"], policy_configuration={"native": deepcopy(POLICY)},
        shared_system_configuration=deepcopy(reference["shared_system_configuration"]),
        native_session_close_reclaim=False, formal_acceptance=False,
        checkpoint_path_diagnostic={
            "enabled": True, "path_node_limit": 256, "context_history_limit": 1024,
            "generation_prefix_compatibility": True, "raw_output_token_logging": False,
        },
        scope="Full native diagnostic collection; no new scheduling/cache policy or performance claim.",
        cpu_quality_overlap_possible=True,
    )
    return plan


def collection_environment(plan):
    environment = {
        "PYTHON": sys.executable, "PYTHONPATH": str(APP), "CUDA_VISIBLE_DEVICES": "0",
        "PORT": str(PORT), "RUN_ROOT": str(RUN / "native"),
        "ROOT_COUNT": "156", "WORKLOAD_MANIFEST": plan["workload_manifest"],
        "WORKFLOW_ARRIVAL_SCHEDULE": str(RUN / "ab_plan.json"),
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "SAMPLING_SEED": "21", "ACTIVATION_WALL_CLOCK_SECONDS": "14400.0",
        "HOST_SPLIT": "80:20", "HOST_NUMA_MEMORY_POLICY": "preferred",
        "HICACHE_SIZE_GB": "200", "HICACHE_WRITE_POLICY": "write_back",
        "FANOUT_PROFILE": "native_in_graph_2to4", "CHILD_FINAL_REPORT_SHADOW": "1",
        "NATIVE_POLICY_BASELINE": "1", "AB_MODE": "off", "CONFIRMED_JOIN_CANARY": "0",
        "PREPARE_HOST": "0", "EXECUTION_HANDOFF": "0", "ENABLE_TOOL_TIMING": "0",
        "SGLANG_PATCH_FLAVOR": "staging", "SGLANG_NATIVE_SESSION_CLOSE_RECLAIM": "0",
        "SGLANG_HICACHE_WRITE_BACK_BATCH_NODES": "32",
        "SGLANG_HICACHE_WRITE_BACK_BATCH_FRONTIER": "0",
        "BELIEFKV_CONTEXT_SUMMARY_INPUT_MODE": "xml",
        "BELIEFKV_CHECKPOINT_PATH_SHADOW": "1",
    }
    for name in (
        "WORKFLOW_PROGRESS", "WORKFLOW_ATTAINED_WORK", "TOOL_COMPLETION_RESTORE",
        "RECORD_OUTPUT_TOKEN_IDS", "EMIT_SEMANTIC_TEXT", "NATIVE_PREFILL_ORDER",
        "ENABLE_FINAL_STAGE_PREFETCH", "ENABLE_FINAL_STAGE_PRIORITY",
        "ENABLE_PREPARE_HOST", "ENABLE_TOOL_PREFETCH", "ENABLE_EXECUTION_HANDOFF",
        "ENABLE_RESIDENT_FIRST", "H2D_OPPORTUNITY_SAMPLING",
    ):
        environment[f"BELIEFKV_{name}"] = "0"
    return environment


def complete_collection(summary, telemetry, expected, finished, client_exit):
    rows = summary.get("workflows", ())
    ids = [row["instance_id"] for row in rows]
    if (
        len(expected) != 156 or len(set(expected)) != 156
        or len(ids) != len(set(ids)) or sorted(ids) != sorted(expected)
        or len(finished) != len(set(finished)) or sorted(finished) != sorted(expected)
        or summary.get("workflow_count") != 156
    ):
        raise ValueError("collection does not cover the exact 156 tasks")
    outcomes = Counter(row.get("outcome") for row in rows)
    eligible = sum(row.get("native_agent_jct_eligible") is True for row in rows)
    if (
        set(outcomes) - {"completed", "incomplete"}
        or summary.get("completed_workflows") != outcomes["completed"]
        or summary.get("native_agent_jct_eligible_workflows") != eligible
        or type(client_exit) is not int or client_exit not in (0, 1)
        or (client_exit == 0) != (eligible == 156)
        or any(
            row.get("error") is not None
            or any((row.get("trace") or {}).get(key) is not True for key in (
                "workflow_lifecycle_valid", "llm_pairing_valid", "tool_pairing_valid",
            ))
            for row in rows
        )
    ):
        raise ValueError("collection exception, invalid lifecycle or unexplained native-gate exit")
    if any(telemetry.get(key, "missing") != value for key, value in {
        "writer_error": None, "dropped_records": 0, "failed_records": 0,
        "writer_queue_depth": 0, "pending_batch_count": 0, "pending_request_count": 0,
    }.items()):
        raise ValueError("telemetry did not close cleanly")
    return {
        "workflow_count": 156, "outcomes": dict(outcomes),
        "native_agent_jct_eligible_workflows": eligible,
        "original_client_exit_code": client_exit, "native_gate_passed": eligible == 156,
        "scope": "Collection completeness only; original incomplete, guard and quality labels are retained.",
    }


def collection_inputs(arm, plan, client_exit):
    client = arm / "client_156"
    summary, telemetry = load(client / "summary.json"), load(arm / "server/native_telemetry_status.json")
    arrivals = client / "workflow_arrivals.jsonl"
    finished = [
        row["instance_id"] for line in arrivals.read_text().splitlines()
        if (row := json.loads(line)).get("event") == "workflow_finished"
    ]
    evidence = complete_collection(
        summary, telemetry, plan["workload_instance_ids_in_manifest_order"], finished, client_exit,
    )
    inputs = [bound(path) for path in (
        client / "summary.json", arrivals, arm / "server/native_telemetry_status.json",
    )]
    for row in summary["workflows"]:
        path = client / "workflows" / row["instance_id"] / "result.json"
        result = load(path)
        if any(result.get(key) != row.get(key) for key in (
            "instance_id", "outcome", "error", "native_agent_jct_eligible",
        )):
            raise ValueError("per-task result differs from aggregate")
        inputs.append(bound(path))
    return evidence, inputs


def source_state():
    _require_clean(APP)
    source = git_source_state(APP)
    engine = git_source_state(APP / "third_party/sglang-v0.5.20", canonical_patch=APP / PATCH)
    if not engine["canonical_patch"]["matches_worktree"] or engine["canonical_patch"]["actual_tree"] != ENGINE_TREE:
        raise ValueError("the unchanged complete V75 engine is required")
    qualified = load(PROBE_QUALIFICATION)
    for name in (
        "beliefkv/runtime/checkpoint_path_probe.py", "beliefkv/runtime/v0520_native_telemetry.py",
    ):
        if sha256_file(APP / name) != qualified["source_sha256"][name]:
            raise ValueError("the CPU-qualified continuation diagnostic changed")
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", load(REFERENCE / "ab_plan.json")["code_commit"],
         source["commit"], "--", "beliefkv", "scripts/run_deepagents_swebench.py",
         "scripts/launch_qwen35_native_v0520.sh", "scripts/run_qwen35_native_regime_probe.sh",
         "scripts/run_qwen35_semantic_h2d_ab.sh"], cwd=APP, text=True,
    ).splitlines()
    if set(changed) - CHANGED_PATHS:
        raise ValueError("model, prompt, harness or tool code differs from native")
    return source, engine, changed


def verify_source(registration):
    for item in registration["inputs"]:
        if bound(Path(item["path"])) != item:
            raise ValueError("a registered source, plan, helper or CPU receipt changed")
    source, engine, _ = source_state()
    if source != registration["source"] or engine != registration["engine"]:
        raise ValueError("the frozen native diagnostic source changed")
    if registration["environment"] != collection_environment(load(RUN / "ab_plan.json")):
        raise ValueError("the registered diagnostic environment changed")


def matched_predecessor_plan(previous, candidate):
    if any(previous.get(key) != candidate.get(key) for key in MATCHED_FIELDS):
        raise ValueError("predecessor tasks, arrivals or physical configuration differ")


def prepare():
    if any(path.exists() for path in (
        RUN, QUALIFICATION, REGISTRATION, STATE, LOG, SUPERVISOR, STARTUP, PLACEMENT, FINAL,
    )):
        raise FileExistsError("preserve existing V79 artifacts")
    source, engine, changed = source_state()
    reference = load(REFERENCE / "ab_plan.json")
    manifest = APP / Path(reference["workload_manifest"]).relative_to(MAIN)
    if sha256_file(manifest) != reference["workload_sha256"]:
        raise ValueError("native task manifest changed")
    if engine["canonical_patch"]["sha256"] != reference["sglang_patch_sha256"]:
        raise ValueError("native engine patch differs")
    plan = build_plan(reference, commit=source["commit"], manifest=manifest)
    cases = list(ET.parse(CPU).getroot().iter("testcase"))
    if not cases or any(case.find(kind) is not None for case in cases for kind in ("failure", "error", "skipped")):
        raise ValueError("CPU qualification is incomplete")
    alive, _, _, _, _ = operations()
    previous = load(WAIT_STATE)
    if previous.get("run_root") != str(PREVIOUS) or not alive(previous["supervisor"]):
        raise ValueError("the actual registered V78b supervisor is missing")
    RUN.mkdir()
    write_json(RUN / "ab_plan.json", plan)
    write_json(QUALIFICATION, {
        "at": now(), "source": source, "engine": engine, "changed_paths": changed,
        "cpu": bound(CPU), "passing_cpu_cases": len(cases),
        "probe_qualification": bound(PROBE_QUALIFICATION),
        "scope": "CPU/source/configuration only; no GPU evidence or performance improvement.",
    })
    inputs = [bound(path) for path in (
        Path(__file__).resolve(), APP / "scripts/observe_owned_scheduler_numa.py",
        APP / "scripts/start_v77_native_close_20261016.py", CPU, PROBE_QUALIFICATION,
        QUALIFICATION, RUN / "ab_plan.json", manifest, REFERENCE / "ab_plan.json",
        REFERENCE / "native/server/native_capacity_census.json", WAIT_REGISTRATION,
        BASE / "ops/server_reservation.py",
        BASE / "ops/start_v64_latest_read_feedback_20261015.py",
        BASE / "ops/observe_v60_startup.py",
    )]
    write_json(REGISTRATION, {
        "at": now(), "source": source, "engine": engine, "inputs": inputs,
        "wait_supervisor": previous["supervisor"], "wait_driver": previous["driver"],
        "environment": collection_environment(plan),
        "scope": "Wait for complete V78b collection and resource release, then collect one full native diagnostic run.",
    })
    verify_source(load(REGISTRATION))
    print(str(REGISTRATION))


def predecessor_evidence(previous, registration):
    for key, registered in (("supervisor", "wait_supervisor"), ("driver", "wait_driver")):
        if any(previous.get(key, {}).get(field) != registration[registered].get(field)
               for field in ("pid", "start_ticks")):
            raise ValueError("the registered predecessor process identity changed")
    if previous.get("run_root") != str(PREVIOUS) or not previous.get("startup"):
        raise ValueError("predecessor identity or verified startup is missing")
    if previous.get("status") != "completed" and not (
        previous.get("status") == "stopped_for_diagnosis"
        and previous.get("error") == "ValueError('native-order collection/startup failed: exit=1')"
    ):
        raise ValueError("predecessor ended for an unclassified reason")
    old = load(WAIT_REGISTRATION)
    previous_plan = PREVIOUS / "ab_plan.json"
    if bound(previous_plan) != old["plan"]:
        raise ValueError("the registered predecessor plan changed")
    matched_predecessor_plan(load(previous_plan), load(RUN / "ab_plan.json"))
    _require_clean(PREVIOUS_APP)
    if (
        git_source_state(PREVIOUS_APP) != old["source"]
        or git_source_state(PREVIOUS_APP / "third_party/sglang-v0.5.20", canonical_patch=PREVIOUS_APP / PATCH) != old["engine"]
        or bound(Path(previous["startup"]["path"])) != previous["startup"]
    ):
        raise ValueError("the predecessor's frozen source or startup changed")
    log = PREVIOUS / "reactive.launch.log"
    exit_code = next((
        value for value in (0, 1) if log.read_text().rstrip().endswith(
            f"Native regime probe: client exit={value}, artifacts={PREVIOUS / 'reactive'}"
        )
    ), None)
    evidence, inputs = collection_inputs(PREVIOUS / "reactive", load(PREVIOUS / "ab_plan.json"), exit_code)
    return {
        **evidence, "original_supervisor_state": previous,
        "inputs": [*inputs, bound(log), previous["startup"], bound(WAIT_REGISTRATION)],
    }


def startup_configuration(serving, telemetry, manifest, capacity, plan, reference_capacity):
    expected = {
        "status": "ready", "max_running_requests": 48, "context_length": 131072,
        "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
        "enable_mixed_chunk": False, "enable_prefill_delayer": False,
        "min_free_slots_delay": None, "enable_beliefkv_admission": False,
        "mamba_max_states_per_path": -1, "mamba_full_memory_ratio": 0.9,
        "mamba_radix_cache_strategy": "extra_buffer",
    }
    if any(serving.get(key) != value for key, value in expected.items()):
        raise ValueError("actual native scheduling, capacity or path-cap configuration differs")
    if (
        telemetry.get("checkpoint_path_shadow") is not True
        or telemetry.get("record_output_token_ids") is not False
        or telemetry.get("writer_error")
        or (telemetry.get("native_session_close_reclaim") or {}).get("enabled")
        or telemetry.get("write_back_batch", {}).get("max_nodes") != 32
    ):
        raise ValueError("actual diagnostic, reclamation or write-back configuration differs")
    if (
        manifest["arrival_schedule"] != plan["arrival_schedule"]
        or manifest["arrival_schedule_sha256"] != plan["arrival_schedule_sha256"]
        or manifest["client_inflight_root_window"] != 156
        or manifest["config"]["concurrency"] != 156
        or capacity != reference_capacity
    ):
        raise ValueError("actual workload or physical capacity differs")
    return expected


def continuation_sample(path):
    if not path.exists():
        return None
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        offset = max(0, size - 1024 * 1024)
        stream.seek(offset)
        if offset:
            stream.readline()
        payload = stream.read()
    for line in payload.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            continue
        row = json.loads(line)
        if row.get("event") == "generation_continuation_prefix_probe":
            return row
    return None


def verify_startup(registration, state):
    alive, _, process, _, first_service = operations()
    arm = RUN / "native"
    ready = load(arm / "server/native_telemetry_ready.json")
    scheduler = process(ready["scheduler_pid"])
    telemetry = load(arm / "server/native_telemetry_status.json")
    sample = continuation_sample(arm / "server/eviction_attribution.jsonl")
    service = first_service(arm / "server/runtime_audit.jsonl")
    if not scheduler or not alive(scheduler) or sample is None or service is None:
        return
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/get_server_info", timeout=5) as response:
        serving = json.load(response)
    capacity = load(arm / "server/native_capacity_census.json")["capacity"]
    expected = startup_configuration(
        serving, telemetry, load(arm / "client_156/manifest.json"), capacity,
        load(RUN / "ab_plan.json"), load(REFERENCE / "native/server/native_capacity_census.json")["capacity"],
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
        raise ValueError("actual scheduler identity or NUMA capacity differs")
    verify_source(registration)
    quality = load(QUALITY_STATE) if QUALITY_STATE.exists() else {}
    write_json(STARTUP, {
        "at": now(), "status": "actual_native_service_and_continuation_probe_verified",
        "scheduler": scheduler, "driver": state["driver"],
        "first_actual_service": service, "first_continuation_probe": sample,
        "settings": expected, "capacity": capacity, "placement": bound(PLACEMENT),
        "cpu_quality_state": quality,
        "scope": "Diagnostic startup only; no performance or checkpoint policy acceptance.",
    })
    state["startup"] = bound(STARTUP)


def finish_collection(state, client_exit):
    evidence, inputs = collection_inputs(RUN / "native", load(RUN / "ab_plan.json"), client_exit)
    with (RUN / "timeline.log").open("x") as log:
        subprocess.run([
            sys.executable, str(APP / "scripts/render_p6_execution_timeline.py"),
            "--run-dir", str(RUN / "native"), "--arm", "native",
            "--output-html", str(RUN / "timelines/native.html"),
        ], cwd=APP, env={**os.environ, "CUDA_VISIBLE_DEVICES": "999"},
            stdout=log, stderr=subprocess.STDOUT, check=True)
    write_json(FINAL, {
        "at": now(), "status": "complete_native_diagnostic_collection",
        **evidence, "inputs": inputs, "startup": state["startup"],
        "timeline": bound(RUN / "timelines/native.html"),
        "scope": "Complete diagnostic collection; task quality, useful throughput and novelty remain unproved.",
    })


def watch():
    registration = load(REGISTRATION)
    alive, owned_runs, process, resources, _ = operations()
    state = {
        "status": "waiting_for_complete_v78b", "supervisor": process(os.getpid()),
        "registration": bound(REGISTRATION), "run_root": str(RUN), "started_at": now(),
    }
    child = None
    try:
        verify_source(registration)
        while True:
            previous = load(WAIT_STATE)
            live = any(alive(registration[key]) for key in ("wait_supervisor", "wait_driver"))
            state.update(observed_at=now(), previous_processes_live=live)
            if not live:
                state["predecessor_collection"] = predecessor_evidence(previous, registration)
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
        state.update(status="running_native_checkpoint_diagnostic", driver=process(child.pid), launched_at=now())
        write_json(STATE, state)
        while child.poll() is None:
            if "startup" not in state:
                try:
                    verify_startup(registration, state)
                except (FileNotFoundError, urllib.error.URLError, TimeoutError):
                    pass
            path = RUN / "native/server/native_telemetry_status.json"
            if path.exists() and load(path).get("writer_error"):
                raise ValueError("diagnostic telemetry writer failed")
            state["observed_at"] = now()
            write_json(STATE, state)
            time.sleep(10)
        if child.returncode not in (0, 1) or "startup" not in state:
            raise ValueError(f"diagnostic collection/startup failed: exit={child.returncode}")
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
        raise FileExistsError("preserve an existing V79 supervisor")
    verify_source(load(REGISTRATION))
    _, _, process, _, _ = operations()
    with LOG.open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch"], cwd=APP,
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
