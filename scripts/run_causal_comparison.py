#!/usr/bin/env python3
"""Freeze and supervise selected same-work causal policy controls."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.delegation_policy import DYNAMIC_NESTED_IN_GRAPH_PROFILE, NESTED_IN_GRAPH_PROFILE
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import (
    RUNTIME_ARMS as PAPER_CONTROLS, environment as paper_environment,
    server_flags as paper_server_flags,
)
from beliefkv.experiments.subagent_retention import (
    ORDER as RETENTION_ORDER, environment as retention_environment,
)
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_gpu_replay import source_plan
from scripts.run_causal_source_capture import (
    comparison, engine_evidence, fresh, load, qualified, reservation, reusable_source,
    project_disjoint_workload, validated_capacity,
)
from scripts.benchmark_causal_sampling import calibration_batch_sizes
from beliefkv.experiments.prepare_ack_engine import prepare_ack_engine_compatibility
from beliefkv.core.events import RuntimeEventKind as Kind

ORDER = ("full", "native_matched", "join_priority_only", "no_background_prepare")
DEFAULT_ORDER = ("full", "join_priority_only")
CONTROLS = (*ORDER, "scheduler_only", "kv_only", "no_tool_prefetch")
RETENTION_CONTROLS = {f"subagent_{policy}": policy for policy in RETENTION_ORDER}
ALL_CONTROLS = (*CONTROLS, *RETENTION_CONTROLS, *PAPER_CONTROLS)
METRIC_BOUNDARY = "workflow_start_to_workflow_end"
SERVER_FIELDS = (
    "PREFETCH_LEAD_MS", "JOIN_PREFETCH_MIN_ADVANCE_MS", "TOOL_PREFETCH_MIN_ADVANCE_MS",
    "PARALLEL_WAIT_PREFETCH", "WAIT_PREFETCH_CONTEXTS",
    "PREDICTION_MAINTENANCE_INTERVAL_MS", "SEMANTIC_WORK_STATISTIC",
    "EOS_PROTOCOL_WINDOW_MS", "PROFILE_SHARED_PATH", "TERMINAL_CACHE_DIAGNOSTICS",
    "H2D_OPPORTUNITY_SAMPLING", "JOIN_PREFETCH_TRIGGER",
    "JOIN_PREDICTION_MIN_FULL_BYTES", "JOIN_PREDICTION_INTERVAL_MS",
    "JOIN_PREDICTION_MAX_CANDIDATES", "ONLINE_TOOL_TIMING",
    "BORROWABLE_PARENT_HEADROOM", "AGENT_ADMISSION",
)


def environment(
    reference, mode, checkout, engine, arm, port, plugin, *, sampling_mode="legacy",
    max_running_requests=8, execution_frontier_restore=False, frontier_cold_reclaim=False,
    tool_restore_mode="speculative",
):
    if mode not in ALL_CONTROLS:
        raise ValueError("unknown causal comparison control: " + mode)
    if sampling_mode not in ("legacy", "position"):
        raise ValueError("unknown causal replay sampling mode")
    if type(max_running_requests) is not int or not 1 <= max_running_requests <= 9999:
        raise ValueError("max running requests must be a positive integer")
    if type(execution_frontier_restore) is not bool:
        raise ValueError("execution frontier restore must be explicitly boolean")
    if tool_restore_mode not in ("speculative", "execution"):
        raise ValueError("unsupported declared tool restore mode")
    if type(frontier_cold_reclaim) is not bool or (
        frontier_cold_reclaim and not execution_frontier_restore
    ):
        raise ValueError("frontier cold reclaim requires an explicit frontier candidate")
    if reference["arms"]["full"].get("FRONTIER_COLD_RECLAIM", "0") != str(
        int(frontier_cold_reclaim)
    ):
        raise ValueError("causal comparison changed the declared frontier cold reclaim")
    retention = RETENTION_CONTROLS.get(mode)
    policy = (
        "scheduler_only" if mode in PAPER_CONTROLS else
        "full" if retention or mode in ("no_background_prepare", "no_tool_prefetch") else mode
    )
    required = {
        "MAX_RUNNING_REQUESTS": str(max_running_requests), "SERVER_RANDOM_SEED": "21",
        "SAMPLING_SEED": "21", "PYTHONHASHSEED": "21", "HICACHE_SIZE_GB": "200",
        "HOST_SPLIT": "80:20", "MODEL_CONTEXT_TOKENS": "262144",
        "JOIN_PREFETCH_TRIGGER": "observed_eos", "PREFILL_SCHEDULING_POLICY": "workflow",
        "TOOL_RESTORE_MODE": tool_restore_mode, "RESTORE_TIERS": "complete",
        "EXECUTION_FRONTIER_RESTORE": str(int(execution_frontier_restore)),
    }
    full_only = reference["arms"]["full"].get("MODEL_CACHE_LAYOUT") == "full_only"
    if full_only:
        required["HOST_SPLIT"] = "auto"
    for name, value in required.items():
        if reference["arms"]["full"].get(name) != value:
            raise ValueError("causal comparison changed the qualified V108/EOS profile: " + name)
    settings = (
        paper_environment(reference["arms"]["full"], mode) if mode in PAPER_CONTROLS else
        retention_environment(reference["arms"]["full"], retention) if retention else
        arm_environment(
            reference["arms"]["full"], policy,
            tool_prefetch=False if mode == "no_tool_prefetch" else None,
        )
    )
    if not retention:
        settings["BELIEFKV_SUBAGENT_RETENTION"] = "off"
    settings.update(
        PYTHONPATH=f"{checkout}:{plugin}", CUDA_VISIBLE_DEVICES="0",
        SGLANG_SOURCE_CHECKOUT=str(engine), RUN_ROOT=str(arm), PORT=str(port),
        MEM_FRACTION_STATIC="0.94", CONTEXT_LENGTH="262144",
        CHUNKED_PREFILL_SIZE="4096", ENABLE_SESSION_RADIX_CACHE="1",
        HOST_NUMA_NODE="1", HOST_NUMA_MEMORY_POLICY="preferred",
        HICACHE_WRITE_POLICY="write_back", BELIEFKV_FULL_MAMBA_HOST_SPLIT="80:20",
        BELIEFKV_CAUSAL_REPLAY_BRIDGE="1", SGLANG_PLUGINS="beliefkv_causal_replay",
        SGLANG_ENABLE_DELAY_SAMPLE="0" if sampling_mode == "position" else "1",
        BELIEFKV_CAUSAL_REPLAY_SAMPLING=sampling_mode, BELIEFKV_REPLAY_TOKEN_CAPTURE="0",
        BELIEFKV_RECORD_OUTPUT_TOKEN_IDS="1",
    )
    if full_only:
        settings.update(
            BELIEFKV_FULL_MAMBA_HOST_SPLIT="auto", REASONING_PARSER="",
            DEVICE_MAMBA_SLOTS="",
        )
    for name in SERVER_FIELDS:
        if name in settings:
            settings["BELIEFKV_" + name] = settings[name]
    for target, digest, source in (
        ("TOOL_TIMING_ARTIFACT", "TOOL_TIMING_SHA256", "TOOL_TIMING_ARTIFACT"),
        ("H2D_SEED", "H2D_SEED_SHA256", "H2D_SEED_ARTIFACT"),
        ("TRANSFER_SERVICE_SEED", "TRANSFER_SERVICE_SEED_SHA256", "TRANSFER_SERVICE_SEED"),
    ):
        settings["BELIEFKV_" + target] = ""
        settings["BELIEFKV_" + digest] = ""
        if policy in ("full", "kv_only") and settings.get(source):
            path = Path(settings[source])
            settings["BELIEFKV_" + target] = str(path)
            settings["BELIEFKV_" + digest] = sha256_file(path)
    flags = ["--enable-custom-logit-processor", *paper_server_flags(mode)]
    if not full_only:
        flags = ["--mamba-full-memory-ratio", "0.9", *flags]
    if settings.get("DEVICE_FULL_TOKENS"):
        flags += ["--max-total-tokens", settings["DEVICE_FULL_TOKENS"]]
    if settings.get("DEVICE_MAMBA_SLOTS"):
        flags += ["--max-mamba-cache-size", settings["DEVICE_MAMBA_SLOTS"]]
    socket_path = Path(f"/tmp/bkv-causal-{port}.sock")
    for name in ("BELIEFKV_NATIVE_TELEMETRY_DIR", "BELIEFKV_ADMISSION_TELEMETRY_DIR",
                 "BELIEFKV_ADMISSION_OPPORTUNITY_DIR"):
        settings.pop(name, None)
    if policy == "native_matched":
        settings["BELIEFKV_NATIVE_TELEMETRY_DIR"] = str(arm / "server")
    else:
        settings.update(
            BELIEFKV_ADMISSION_TELEMETRY_DIR=str(arm / "server"),
            BELIEFKV_ADMISSION_OPPORTUNITY_DIR=str(arm / "opportunities"),
        )
        flags += ["--enable-beliefkv-admission", "--beliefkv-event-socket-path", str(socket_path)]
    if mode == "no_background_prepare":
        settings["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] = "0"
    return settings, flags, socket_path if policy != "native_matched" else None


def retention_reference(reference, spec, order):
    if (
        not order or any(label != "native_matched" and label not in RETENTION_CONTROLS for label in order)
        or spec.get("source_profile") != "reusable_nested"
    ):
        raise ValueError("retention comparison requires its own reusable-workload matrix")
    # Validate every common invariant even for an initial native-only calibration.
    prepared = retention_environment(spec["common_environment"], "selective")
    common = {**reference["arms"]["full"], **prepared, "BELIEFKV_SUBAGENT_RETENTION": "off"}
    return {**reference, "arms": {**reference["arms"], "full": common}}


def tool_priority_reference(reference, window_ms):
    if type(window_ms) not in (int, float) or not math.isfinite(window_ms) or window_ms <= 0:
        raise ValueError("tool prefetch priority window must be finite and positive")
    common = {
        **reference["arms"]["full"], "TOOL_PREFETCH": "1",
        "TOOL_PREFETCH_PRIORITY_MS": str(window_ms),
        "BELIEFKV_TOOL_PREFETCH_PRIORITY_MS": str(window_ms),
    }
    return {**reference, "arms": {**reference["arms"], "full": common}}


def experiment_reference(reference, spec):
    common = spec.get("common_environment")
    if (
        spec.get("source_profile") != "nested_oneshot"
        or not isinstance(common, dict)
        or any(type(key) is not str or type(value) is not str for key, value in common.items())
        or common.get("FANOUT_PROFILE") not in (
            NESTED_IN_GRAPH_PROFILE, DYNAMIC_NESTED_IN_GRAPH_PROFILE,
        )
        or common.get("BELIEFKV_SUBAGENT_RETENTION") != "off"
        or any(
            not common.get(name, "").isascii() or not common.get(name, "").isdigit()
            or not 1 <= int(common[name]) <= limit
            for name, limit in (("ROOT_COUNT", 156), ("MAX_RUNNING_REQUESTS", 9999))
        )
    ):
        raise ValueError("invalid predeclared one-shot experiment configuration")
    configured = dict(reference["arms"]["full"])
    aliases = {
        "EXECUTION_FRONTIER_RESTORE": "BELIEFKV_ENABLE_FRONTIER_RESTORE",
        "JOIN_LAST_CHILD_PRIORITY": "BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY",
        "TOOL_CONTINUITY": "BELIEFKV_ENABLE_TOOL_CONTINUITY",
    }
    for name, value in common.items():
        if name.startswith("BELIEFKV_"):
            continue
        alias = aliases.get(name, "BELIEFKV_" + name)
        if alias in common and common[alias] != value:
            raise ValueError("conflicting predeclared experiment settings: " + name)
        configured.pop(alias, None)
    configured.update(common)
    return {**reference, "arms": {**reference["arms"], "full": configured}}


def source_reference(reference, roots, running, capacity):
    if (
        type(roots) is not int or not 1 <= roots <= 156
        or type(running) is not int or not 1 <= running <= 9999
    ):
        raise ValueError("source population and running limit must be positive integers")
    capacity = validated_capacity(capacity)
    common = dict(reference["arms"]["full"])
    for target, field in (
        ("DEVICE_FULL_TOKENS", "device_full_tokens"),
        ("DEVICE_MAMBA_SLOTS", "device_mamba_slots"),
    ):
        value = str(capacity[field]) if capacity[field] else ""
        if common.get(target) not in (None, "", value):
            raise ValueError("requested device capacity differs from the native source: " + target)
        common[target] = value
    common.update(ROOT_COUNT=str(roots), MAX_RUNNING_REQUESTS=str(running))
    return {**reference, "arms": {**reference["arms"], "full": common}}


def experiment_source_evidence(spec, quality, smoke_server, arm, roots, running):
    common = spec["common_environment"]
    source_path = Path(smoke_server["source_registration"])
    bindings = [
        row["sha256"] for row in smoke_server.get("external_inputs", ())
        if Path(row["path"]).resolve() == source_path.resolve()
    ]
    if bindings != [sha256_file(source_path)]:
        raise ValueError("GPU-checked source registration is missing or changed")
    source = load(source_path)
    source_env = source["environment"]
    frozen = Path(source_env["WORKLOAD_MANIFEST"])
    try:
        relative = frozen.relative_to(Path(source["execution_checkout"]))
    except ValueError as error:
        raise ValueError("source manifest is outside its qualified checkout") from error
    expected = sha256_file(ROOT / common["WORKLOAD_MANIFEST"])
    if (
        common["ROOT_COUNT"] != str(roots)
        or common["MAX_RUNNING_REQUESTS"] != str(running)
        or source_env.get("ROOT_COUNT") != str(roots)
        or source_env.get("MAX_RUNNING_REQUESTS") != str(running)
        or source_env.get("FANOUT_PROFILE") != common["FANOUT_PROFILE"]
        or quality.get("subagent_fanout_profile") != common["FANOUT_PROFILE"]
        or source.get("source_profile") != spec["source_profile"]
        or (Path(source["run_root"]) / "native_capture").resolve() != arm.resolve()
        or source.get("source_sha256", {}).get(str(relative)) != expected
        or sha256_file(frozen) != expected
        or (
            quality.get("workload_manifest_sha256") is not None
            and quality["workload_manifest_sha256"] != expected
        )
    ):
        raise ValueError("captured source differs from the predeclared experiment")
    project_disjoint_workload(frozen, expected_workflows=roots)
    return {
        "source_registration": str(source_path.resolve()),
        "frozen_manifest": str(frozen.resolve()), "manifest_sha256": expected,
        "basis": "gpu_bound_source_registration_and_cpu_qualified_frozen_manifest",
    }


def preflight_runtime(settings, model_path):
    if settings["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "native_matched":
        return {"native_runtime_not_enabled": True}
    expected = {
        "prefill_scheduling_policy": (
            "native" if settings["BELIEFKV_NATIVE_PREFILL_ORDER"] == "1"
            else settings["PREFILL_SCHEDULING_POLICY"]
        ),
        "idle_prepare_policy": settings.get(
            "IDLE_PREPARE_POLICY", settings["BELIEFKV_IDLE_PREPARE_POLICY"],
        ),
        "tool_restore_mode": settings["TOOL_RESTORE_MODE"],
        "enable_agent_working_set": settings.get("BELIEFKV_AGENT_ADMISSION", "off") in (
            "working_set", "thunderagent",
        ),
        "restore_tiers": settings.get("RESTORE_TIERS", settings["BELIEFKV_RESTORE_TIERS"]),
        "enable_tool_prefetch": settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "1",
        "enable_frontier_restore": settings["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "1",
        "frontier_restore_ordinary_quota": int(
            settings.get("BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA", "4"),
        ),
        "enable_frontier_cold_reclaim": settings["BELIEFKV_FRONTIER_COLD_RECLAIM"] == "1",
        "frontier_cold_reclaim_policy": settings.get(
            "BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY", "waiting",
        ),
        "tool_continuity": settings["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "1",
        "tool_continuity_grace_ms": float(
            settings.get("BELIEFKV_TOOL_CONTINUITY_GRACE_MS", "0"),
        ),
        "tool_prefetch_priority_ms": float(settings["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"]),
        "join_prefetch_trigger": settings["JOIN_PREFETCH_TRIGGER"],
        "enable_prefetch_residency_protection": (
            settings["PREFETCH_RESIDENCY_PROTECTION"] == "1"
            and settings["BELIEFKV_INTERNAL_COMPARISON_MODE"] in ("full", "kv_only")
        ),
    }
    with tempfile.TemporaryDirectory(prefix="beliefkv-causal-runtime-") as directory:
        env = qualified.driver_environment({"environment": settings}, os.environ)
        env["CUDA_VISIBLE_DEVICES"] = "99999"
        for name in (
            "BELIEFKV_ADMISSION_OPPORTUNITY_DIR", "BELIEFKV_ADMISSION_TELEMETRY_DIR",
            "BELIEFKV_NATIVE_TELEMETRY_DIR",
        ):
            env.pop(name, None)
        source = (
            "import json,sys; "
            "from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime; "
            "runtime=NativeAdmissionRuntime(event_socket_path=sys.argv[1],model_path=sys.argv[2]); "
            "expected=json.loads(sys.argv[3]); "
            "actual={name:(runtime._tool_continuity is not None "
            "if name=='tool_continuity' else getattr(runtime,name)) for name in expected}; "
            "runtime.close()\n"
            "if actual != expected:\n"
            "    raise ValueError(f'CPU runtime configuration differs: {actual!r} != {expected!r}')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", source, str(Path(directory) / "events.sock"), model_path,
             json.dumps(expected)],
            env=env, check=True, capture_output=True, text=True, timeout=45,
        )
    return {"actual_runtime_constructor_accepted": True, "runtime_configuration_verified": True,
            "verified_configuration": expected, "cuda_visible_devices": "99999",
            "output_tail": result.stdout[-1000:]}


def receipt_work(path, *, sampling_mode=None):
    result = {}
    for raw in path.open():
        row = json.loads(raw)
        request = row["request_id"]
        if row.get("exact_tokens_verified") is not True or request in result:
            raise ValueError("missing exact token verification or duplicate replay request")
        if sampling_mode == "position" and row.get("native_receipt", {}).get("sampling_path") != "position":
            raise ValueError("native request did not execute the registered position sampler")
        result[request] = (
            row["prompt_tokens"], row["output_tokens"], row["prompt_sha256"],
            row["output_sha256"], row["native_finish_reason"],
        )
    return result


def source_quality(summary, workflow_ids):
    rows = summary.get("workflows", [])
    by_id = {row.get("workflow_id"): row for row in rows}
    if (
        len(by_id) != len(rows) or set(by_id) != set(workflow_ids)
        or any(type(row.get("native_agent_jct_eligible")) is not bool for row in rows)
        or sum(row["native_agent_jct_eligible"] for row in rows)
        != summary.get("native_agent_jct_eligible_workflows")
        or not any(row["native_agent_jct_eligible"] for row in rows)
    ):
        raise ValueError("source guard qualifications do not match the replay population")
    return [
        {
            "workflow_id": row["workflow_id"], "instance_id": row["instance_id"],
            "reasons": row.get("native_agent_jct_exclusion_reasons", []),
        }
        for row in rows if not row["native_agent_jct_eligible"]
    ]


def complete_source_population(summary, graph):
    count = summary.get("workflow_count")
    rows = summary.get("workflows", [])
    declared = [row.get("workflow_id") for row in rows]
    started = {
        node.event.workflow_id for node in graph.nodes
        if node.event is not None and node.event.kind is Kind.WORKFLOW_START
    }
    if (
        type(count) is not int or count <= 0
        or type(summary.get("completed_workflows")) is not int
        or summary["completed_workflows"] != count
        or len(declared) != count or len(set(declared)) != count
        or set(declared) != started or graph.summary()["workflows"] != count
    ):
        raise ValueError("causal source must retain its entire declared workflow population")
    return count


def workflow_event_metrics(events, workflow_ids):
    expected, starts, ends = set(workflow_ids), {}, {}
    if not expected:
        raise ValueError("workflow metrics require the complete declared population")
    for row in events:
        kind = row.get("kind")
        if kind not in ("workflow_start", "workflow_end"):
            continue
        workflow, when = row.get("workflow_id"), row.get("ts_ms")
        target = starts if kind == "workflow_start" else ends
        if (
            workflow not in expected or workflow in target
            or type(when) not in (int, float) or not math.isfinite(when) or when < 0
        ):
            raise ValueError("workflow metrics have invalid or duplicate event boundaries")
        target[workflow] = when
    if starts.keys() != expected or ends.keys() != expected or any(
        ends[key] <= starts[key] for key in expected
    ):
        raise ValueError("workflow metrics require every complete positive event span")
    elapsed = {key: ends[key] - starts[key] for key in sorted(expected)}
    return {
        "metric_boundary": METRIC_BOUNDARY,
        "workflow_jct_ms": elapsed,
        "mean_jct_ms": statistics.mean(elapsed.values()),
        "workflow_event_makespan_ms": max(ends.values()) - min(starts.values()),
    }


def replay_event_metrics(result, arm, workflow_ids):
    path = arm / "replay/runtime_events.replay.jsonl"
    with path.open() as stream:
        metrics = workflow_event_metrics(
            (json.loads(line) for line in stream if line.strip()), workflow_ids,
        )
    measured = result.get("workflow_elapsed_ms", {})
    if measured.keys() != metrics["workflow_jct_ms"].keys() or any(
        type(measured[key]) not in (int, float)
        or not math.isfinite(measured[key])
        or not math.isclose(value, measured[key], rel_tol=1e-12, abs_tol=1e-5)
        for key, value in metrics["workflow_jct_ms"].items()
    ):
        raise ValueError("replay workflow JCT differs from its emitted event boundaries")
    return {**metrics, "workflow_event_log_sha256": sha256_file(path)}


def captured_native_baseline(summary, graph, launch):
    if not isinstance(launch, dict) or launch.get("native_policy_baseline") is not True:
        raise ValueError("captured native baseline requires a verified native source policy")
    count = complete_source_population(summary, graph)
    duration = summary.get("duration_seconds")
    rows = summary["workflows"]
    if (
        type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
        or any(
            type(row.get("duration_seconds")) not in (int, float)
            or not math.isfinite(row["duration_seconds"]) or row["duration_seconds"] <= 0
            for row in rows
        )
    ):
        raise ValueError("captured native baseline requires complete positive timings")
    work = graph.summary()
    if (
        any(type(work.get(name)) is not int or work[name] < 0 for name in (
            "requests", "prompt_tokens", "output_tokens",
        ))
        or work["requests"] == 0
    ):
        raise ValueError("captured native baseline requires verified source work totals")
    harness = {row["workflow_id"]: row["duration_seconds"] * 1000. for row in rows}
    try:
        metrics = workflow_event_metrics(
            (
                {"kind": node.event.kind.value, "workflow_id": node.event.workflow_id,
                 "ts_ms": node.event.ts_ms}
                for node in graph.nodes if node.event is not None
                and node.event.kind in (Kind.WORKFLOW_START, Kind.WORKFLOW_END)
            ),
            harness,
        )
    except (AttributeError, ValueError) as error:
        raise ValueError("captured native baseline lacks matching workflow event spans") from error
    elapsed = metrics["workflow_jct_ms"]
    makespan = metrics["workflow_event_makespan_ms"]
    return {
        "kind": "natural_native_source_capture",
        **metrics,
        "workflow_count": count, "workflow_jct_ms": elapsed,
        "mean_jct_ms": statistics.mean(elapsed.values()),
        "duration_ms": makespan,
        "source_harness_workflow_jct_ms": harness,
        "source_harness_mean_jct_ms": statistics.mean(harness.values()),
        "source_harness_duration_ms": duration * 1000.,
        "source_harness_output_tokens_per_second": work["output_tokens"] / duration,
        "requests": work["requests"], "prompt_tokens": work["prompt_tokens"],
        "output_tokens": work["output_tokens"],
        "whole_run_output_tokens_per_second": work["output_tokens"] * 1000. / makespan,
        "all_normal_tails_retained": True,
        "fresh_full_native_replay_required": False,
        "replay_error_basis": "historical_calibration_not_remeasured_on_this_source",
    }


def retention_source_matches(spec, summary, count):
    if spec["common_environment"].get("ROOT_COUNT") != str(count):
        raise ValueError("retention source differs from the declared root population")
    if "source_workload_sha256" in spec:
        expected = spec["source_workload_sha256"]
        if (
            type(expected) is not str or len(expected) != 64
            or any(char not in "0123456789abcdef" for char in expected)
            or summary.get("workload_manifest_sha256") != expected
        ):
            raise ValueError("retention source differs from the declared workload manifest")


def validate_result(plan, result, arm, *, live_control):
    control = result.get("control_delivery", {})
    delivered = control.get("workflows", {})
    if (
        result.get("causal_execution_completed") is not True
        or result.get("exact_tokens_verified") is not True
        or result.get("original_native_stopping_verified") is not True
        or result.get("native_session_http_lifecycle_completed") is not True
        or result.get("live_control_events_delivered") is not live_control
        or result.get("replay_process_python_hash_seed") != "21"
        or control.get("mode") != (
            "per_workflow_harness_queued" if live_control else "trace_only"
        )
        or (
            live_control and (
                set(delivered) != set(plan["workflow_ids"])
                or sum(row.get("accepted_batch_count", 0) for row in delivered.values())
                != plan["source_plan"]["nodes"] - plan["source_plan"]["requests"]
            )
        )
        or result.get("completed_requests") != plan["source_plan"]["requests"]
        or result.get("source_plan") != plan["source_plan"]
        or result.get("token_source", {}).get("token_source_sha256") != plan["token_source_sha256"]
        or set(result.get("workflow_elapsed_ms", {})) != set(plan["workflow_ids"])
        or sha256_file(arm / "replay/native_requests.jsonl") != result.get("native_requests_sha256")
    ):
        raise ValueError("complete same-work replay evidence is missing or changed")
    work = receipt_work(
        arm / "replay/native_requests.jsonl", sampling_mode=plan.get("sampling_mode"),
    )
    if (
        len(work) != result["completed_requests"]
        or sum(row[0] for row in work.values()) != plan["source_plan"]["prompt_tokens"]
        or sum(row[1] for row in work.values()) != plan["source_plan"]["output_tokens"]
    ):
        raise ValueError("replay receipt population differs from completed work")
    return work


def observed_configuration(plan, label, arm, *, final=False):
    retention = RETENTION_CONTROLS.get(label)
    external_policy = label if label in PAPER_CONTROLS else None
    mode = (
        "scheduler_only" if external_policy else
        "full" if retention or label in ("no_background_prepare", "no_tool_prefetch") else label
    )
    overrides = (
        {"background_prepare": False} if label == "no_background_prepare" else
        {"tool_prefetch": False} if label == "no_tool_prefetch" else None
    )
    if retention:
        overrides = {
            "tool_prefetch": plan["reference_environment"].get("TOOL_PREFETCH") != "0",
        }
    observation_options = {
        "max_running_requests": plan.get("max_running_requests", 8),
        "runtime_overrides": overrides,
    }
    if external_policy:
        observation_options["external_policy"] = external_policy
    comparison.observe_arm(mode, arm, **observation_options)
    if final and mode != "native_matched" and qualified.last_runtime_state(
        arm / "opportunities/admission_opportunities.jsonl",
    ) is None:
        raise ValueError("controlled arm has no observed runtime configuration")
    if final and external_policy:
        state = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        counts = (state.get("paper_baseline") or {}).get("counts", {})
        requests = plan["source_plan"]["requests"]
        if counts.get("registered") != requests or counts.get("completed") != requests:
            raise ValueError("paper policy did not observe the complete request population")
    if final and mode != "native_matched":
        state = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        agent_strategy = plan.get("reference_environment", {}).get(
            "BELIEFKV_AGENT_ADMISSION",
            plan.get("reference_environment", {}).get("AGENT_ADMISSION", "off"),
        )
        expected_agents = mode == "full" and agent_strategy in ("working_set", "thunderagent")
        actual_agents = state.get("agent_admission")
        if expected_agents:
            if (
                not isinstance(actual_agents, dict)
                or actual_agents.get("policy") != (
                    "thunderagent_with_native_migration" if agent_strategy == "thunderagent"
                    else "agent_working_set_with_native_migration"
                )
                or actual_agents.get("identity") != "invocation_id"
                or actual_agents.get("future_information_used") is not False
                or actual_agents.get("native_capacity_authority_preserved") is not True
                or actual_agents.get("counts", {}).get("registered") != plan["source_plan"]["requests"]
                or actual_agents.get("counts", {}).get("completed") != plan["source_plan"]["requests"]
            ):
                raise ValueError("agent working-set policy did not execute the declared request population")
        elif actual_agents is not None:
            raise ValueError("undeclared agent working-set admission")
    if retention:
        state = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        if state is not None:
            actual = state.get("subagent_retention") or {}
            common = plan["reference_environment"]
            if (
                actual.get("policy") != retention
                or actual.get("host_budget_bytes") != int(common["BELIEFKV_DORMANT_HOST_BYTES"])
                or actual.get("device_budget_bytes") != int(common["BELIEFKV_DORMANT_HBM_BYTES"])
            ):
                raise ValueError("effective dormant retention policy or budget differs")
    requested_priority = plan.get("tool_prefetch_priority_ms")
    if requested_priority is not None and mode != "native_matched":
        state = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        if state is not None:
            expected = (
                requested_priority
                if mode in ("full", "kv_only") and label != "no_tool_prefetch"
                and plan.get("tool_restore_mode", "speculative") == "speculative"
                else 0.
            )
            if state.get("tool_prefetch_priority_ms") != expected:
                raise ValueError("effective tool prefetch priority window differs")
    common = plan.get("reference_environment", {})
    requested_settings = (
        paper_environment(common, label) if external_policy else None
    )
    if "FRONTIER_RESTORE_ORDINARY_QUOTA" in common and mode != "native_matched":
        state = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        if state is not None:
            expected_quota = int(
                (requested_settings or arm_environment(common, mode))
                ["BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA"]
            )
            if state.get("frontier_restore_ordinary_quota") != expected_quota:
                raise ValueError("effective selected-consumer preload quota differs")
    if "TOOL_CONTINUITY" in common and mode != "native_matched":
        state = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        if state is not None:
            expected = (
                requested_settings or arm_environment(common, mode)
            )["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "1"
            if state.get("tool_continuity") is not expected:
                raise ValueError("effective short-tool continuity differs")
            grace = float(
                (requested_settings or arm_environment(common, mode))
                ["BELIEFKV_TOOL_CONTINUITY_GRACE_MS"]
            )
            if state.get("tool_continuity_grace_ms", 0.) != grace:
                raise ValueError("effective short-tool residency grace differs")
    census = arm / "server/native_capacity_census.json"
    if census.exists() and load(census)["capacity"] != plan["source_capacity"]:
        raise ValueError("actual FULL/Mamba/Host pool capacity changed")


def predecessor_ready(plan):
    dependency = plan.get("after_registration")
    if dependency is None:
        return True
    previous = load(Path(dependency))
    for name in (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "workflow_ids", "quality_exclusions",
        "quality_exclusion_details", "reference_environment", "engine_tree",
    ):
        if previous.get(name) != plan.get(name):
            raise ValueError("predecessor causal comparison differs: " + name)
    state = load(Path(dependency).with_suffix(".state.json"))
    if state.get("run_root") != previous["run_root"]:
        raise ValueError("predecessor state belongs to another run")
    status = state.get("status", "")
    if status == "collected_performance_review_pending":
        if (
            state.get("completed_arms") != previous["order"]
            or state.get("original_exit_codes") != {label: 0 for label in previous["order"]}
        ):
            raise ValueError("predecessor causal comparison did not complete every arm")
        identities = [
            *state.get("owned", []),
            *(state[name] for name in ("watcher", "driver", "server") if state.get(name)),
        ]
        if not state.get("watcher"):
            raise ValueError("predecessor supervisor identity is missing")
        return not any(reservation.alive(row) for row in identities)
    if status != "preflight" and not status.startswith(("running_", "starting_", "waiting_")):
        raise ValueError("predecessor causal comparison failed: " + status)
    if not state.get("watcher") or not reservation.alive(state["watcher"]):
        raise ValueError("predecessor supervisor exited before completing the comparison")
    return False


def predecessor_work(plan):
    if plan.get("after_registration") is None:
        return None
    if not predecessor_ready(plan):
        raise ValueError("predecessor causal comparison is still running")
    previous = load(Path(plan["after_registration"]))
    comparison.verify_sources(previous)
    expected = None
    for label in previous["order"]:
        arm = Path(previous["run_root"]) / label
        result = load(arm / "replay/summary.json")
        work = validate_result(previous, result, arm, live_control=label != "native_matched")
        observed_configuration(previous, label, arm, final=True)
        if expected is not None and work != expected:
            raise ValueError("predecessor request work changed between comparison arms")
        expected = work
    if expected is None:
        raise ValueError("predecessor causal comparison contains no completed work")
    return expected


def source_speed_check(source, result, tolerance):
    if (
        type(tolerance) not in (int, float) or not math.isfinite(tolerance)
        or not 0 < tolerance <= .5
    ):
        raise ValueError("source replay slowdown tolerance must be positive and at most 0.5")
    ratios = {}
    for field in ("mean_jct_ms", "replay_duration_ms"):
        baseline, observed = source[field], result[field]
        if any(
            type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            for value in (baseline, observed)
        ):
            raise ValueError("source speed check requires complete positive measurements")
        ratios[field] = observed / baseline
    return {
        "accepted": all(ratio <= 1. + tolerance for ratio in ratios.values()),
        "maximum_slowdown": tolerance, "replay_to_source_ratios": ratios,
        "scope": "Replay overhead calibration only; not a predictive speedup or task score.",
    }


def owned_processes(identities):
    known = {row["pid"]: row for row in identities if reservation.alive(row)}
    candidates = {}
    for directory in Path("/proc").iterdir():
        if directory.name.isdigit():
            row = reservation.process(int(directory.name))
            if row:
                candidates[row["pid"]] = row
    while True:
        children = {
            pid: row for pid, row in candidates.items()
            if row["ppid"] in known and pid not in known
        }
        if not children:
            return list(known.values())
        known.update(children)


def cleanup(arm, identities, reason):
    known = owned_processes(identities)
    record = {
        "reason": reason, "started_at": reservation.now().isoformat(),
        "identities": known, "signals": [],
    }
    for sig, grace in ((signal.SIGTERM, 12.), (signal.SIGKILL, 60.)):
        for row in known:
            if reservation.alive(row):
                try:
                    os.kill(row["pid"], sig)
                    record["signals"].append({"pid": row["pid"], "signal": sig.name})
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + grace
        while any(reservation.alive(row) for row in known) and time.monotonic() < deadline:
            time.sleep(.1)
    record["remaining_processes"] = []
    for identity in known:
        current = reservation.process(identity["pid"])
        if (
            current and current["start_ticks"] == identity["start_ticks"]
            and current.get("state") != "Z"
        ):
            record["remaining_processes"].append(current)
    record["finished_at"] = reservation.now().isoformat()
    reservation.write_json(arm / "owned_process_cleanup.json", record)
    if record["remaining_processes"]:
        raise RuntimeError("owned replay processes remain after cleanup")


def wait_for_telemetry_drain(arm, *, server, pulse, timeout=30.):
    """Keep completed work alive until the native writer reports a drained queue."""
    started = time.monotonic()
    deadline = started + timeout
    previous_zero = None
    path = arm / "server/native_telemetry_status.json"
    while True:
        pulse()
        if server.poll() is not None:
            raise ValueError("native server exited before telemetry drained")
        if path.is_file():
            status = load(path)
            if (
                status.get("writer_error") is not None
                or status.get("failed_records") != 0 or status.get("dropped_records") != 0
            ):
                raise ValueError("native telemetry writer failed before shutdown")
            snapshot = status.get("snapshot_ts_ms")
            drained = (
                type(snapshot) in (int, float) and math.isfinite(snapshot)
                and status.get("writer_queue_depth") == 0
                and status.get("pending_batch_count") == 0
                and status.get("pending_request_count") == 0
            )
            if drained:
                if previous_zero is not None and snapshot > previous_zero:
                    reservation.write_json(arm / "telemetry_drain.json", {
                        "observed_at": reservation.now().isoformat(),
                        "elapsed_seconds": time.monotonic() - started,
                        "status_path": str(path),
                        "drained_status": status,
                        "previous_zero_snapshot_ts_ms": previous_zero,
                        "scope": (
                            "Two fresh idle writer snapshots after all replay "
                            "requests completed, before process cleanup. This "
                            "collection wait is outside measured workflow JCT."
                        ),
                    })
                    return
                previous_zero = snapshot
            else:
                previous_zero = None
        if time.monotonic() >= deadline:
            raise ValueError("native telemetry did not drain before shutdown")
        time.sleep(.25)


def register(args):
    order = list(args.arms)
    if not order or len(order) != len(set(order)) or any(label not in ALL_CONTROLS for label in order):
        raise ValueError("select distinct supported causal controls")
    if args.after_registration is not None and args.after_registration.resolve() == args.registration.resolve():
        raise ValueError("a causal comparison cannot depend on itself")
    if args.maximum_replay_slowdown is not None and order[0] != "native_matched":
        raise ValueError("source speed calibration must precede the controlled arms")
    for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    ):
        if path.exists():
            raise FileExistsError("preserve existing causal comparison and registration")
    reference = load(args.reference)
    fresh.verify(reference)
    fresh.check_hold(reference, released=True)
    experiment_spec = load(args.experiment_config) if args.experiment_config else None
    if experiment_spec is not None:
        reference = experiment_reference(reference, experiment_spec)
        if (
            experiment_spec.get("native_reference", {}).get("repeat_full_native_replay") is False
            and "native_matched" in order
        ):
            raise ValueError("the predeclared experiment skips full native replay")
    if any(label in RETENTION_CONTROLS for label in order) and args.retention_config is None:
        raise ValueError("retention controls require an explicit reusable comparison configuration")
    retention_spec = load(args.retention_config) if args.retention_config is not None else None
    if retention_spec is not None:
        reference = retention_reference(reference, retention_spec, order)
    if args.tool_prefetch_priority_ms is not None:
        reference = tool_priority_reference(reference, args.tool_prefetch_priority_ms)
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    if not {
        "scripts/run_causal_comparison.py", "tests/test_causal_comparison.py",
        "scripts/benchmark_causal_sampling.py", "tests/test_causal_sampling_benchmark.py",
        "beliefkv/experiments/causal_replay_delivery.py", "tests/test_causal_replay_delivery.py",
    } <= sources.keys():
        raise ValueError("comparison operator or sampling calibration lacks CPU qualification")
    smoke = load(args.gpu_smoke)
    if (
        smoke.get("gpu_process_verified") is not True
        or smoke.get("native_data_plane_sample_verified") is not True
        or smoke.get("requests") != 24
        or type(smoke.get("native_stop_requests")) is not int
        or not 1 <= smoke["native_stop_requests"] <= 24
        or smoke.get("sampling_mode", "legacy") != args.sampling_mode
        or args.sampling_mode == "position" and smoke.get("native_position_sampling_verified") is not True
    ):
        raise ValueError("the predeclared real GPU data-plane check is incomplete")
    smoke_client = load(Path(smoke["client_registration"]))
    smoke_server = load(Path(smoke["server_registration"]))
    if (
        args.source_arm.resolve() != Path(smoke_server["source_arm"])
        or sha256_file(Path(smoke["client_registration"])) != smoke["client_registration_sha256"]
        or sha256_file(Path(smoke["server_registration"])) != smoke["server_registration_sha256"]
        or sha256_file(Path(smoke["native_requests"])) != smoke["native_requests_sha256"]
    ):
        raise ValueError("GPU sample came from another source or changed after qualification")
    for name in smoke_client["unchanged_server_modules"]:
        if sources.get(name) != smoke_client["unchanged_server_modules"][name]:
            raise ValueError("GPU-checked sampling/receipt source changed: " + name)
    running = args.max_running_requests if args.max_running_requests is not None else 8
    expected_batches = calibration_batch_sizes(running)
    calibration = load(args.sampling_calibration)
    if (
        calibration.get("native_sampler_sha256") != sha256_file(
            args.engine / "python/sglang/srt/layers/sampler.py",
        )
        or calibration.get("benchmark_sha256") != sources["scripts/benchmark_causal_sampling.py"]
        or calibration.get("forcing_sha256") != sources["beliefkv/experiments/causal_replay_sampling.py"]
        or [row["batch_size"] for row in calibration.get("cases", [])] != expected_batches
        or args.sampling_mode == "position" and calibration.get("position_dispatch_included") is not True
    ):
        raise ValueError("native replay sampling costs were not measured for this source")
    sampling_profile = reference["arms"].get("full", {}).get("SAMPLING_PROFILE")
    if sampling_profile in ("qwen35_coding", "qwen3_coder") and (
        calibration.get("sampling_profile") != sampling_profile
        or calibration.get("native_sampler_preserved") is not True
        or calibration.get("forcing_boundary") != "after_native_sampling"
    ):
        raise ValueError("coding replay did not measure its original native sampling path")
    graph, expected, evidence = source_plan(args.source_arm)
    summaries = list(args.source_arm.glob("client_*/summary.json"))
    quality = load(summaries[0])
    source_roots = complete_source_population(quality, graph)
    source_launch = args.source_arm / "server/launch_configuration.json"
    native_baseline = captured_native_baseline(quality, graph, load(source_launch))
    native_baseline.update(
        source_arm=str(args.source_arm.resolve()),
        client_summary_sha256=sha256_file(summaries[0]),
        launch_configuration_sha256=sha256_file(source_launch),
    )
    if smoke_server["source_server_parameters"].get("max_running_requests") != running:
        raise ValueError("comparison running limit differs from the GPU-checked source")
    experiment_evidence = None
    if experiment_spec is not None:
        experiment_evidence = experiment_source_evidence(
            experiment_spec, quality, smoke_server, args.source_arm, source_roots, running,
        )
    else:
        source_capture = load(Path(smoke_server["source_registration"]))
        project_disjoint_workload(
            Path(source_capture["environment"]["WORKLOAD_MANIFEST"]),
            expected_workflows=source_roots,
        )
    if retention_spec is not None:
        retention_source_matches(retention_spec, quality, source_roots)
    with CapturedTokenCorpus(args.source_arm / "server/request_tokens.jsonl", expected) as corpus:
        tokens = corpus.evidence
    if sampling_profile in ("qwen35_coding", "qwen3_coder") and (
        tokens.get("original_generation_parameters_complete") is not True
    ):
        raise ValueError("coding comparison lacks complete original generation parameters")
    source_capacity = load(args.source_arm / "server/native_capacity_census.json")["capacity"]
    reference = source_reference(reference, source_roots, running, source_capacity)
    engine = engine_evidence(args.engine, args.engine_report, args.engine_sources)
    captured_source = load(Path(smoke_server["source_registration"]))
    engine_compatibility = prepare_ack_engine_compatibility(
        args.engine, captured_source, candidate_tree=engine["tree"],
    )
    if (
        smoke_server.get("engine_tree") != engine["tree"]
        or smoke_server.get("engine_compatibility") != engine_compatibility
    ):
        raise ValueError("GPU check and controlled engine compatibility differ")
    fresh._require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("freeze qualified main")
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    checkout = comparison.EXPERIMENTS / ".worktrees" / f"beliefkv-causal-{revision[:12]}"
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    comparison.verify_cpu_snapshot(checkout, sources)
    frozen_engine = comparison.EXPERIMENTS / ".worktrees" / (
        f"sglang-causal-{engine['tree'][:12]}-{revision[:12]}"
    )
    base = qualified.command(["git", "-C", str(args.engine), "rev-parse", "HEAD"])
    subprocess.run(
        ["git", "-C", str(args.engine), "worktree", "add", "--detach", str(frozen_engine), base],
        check=True, capture_output=True,
    )
    patch = checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    subprocess.run(
        ["git", "-C", str(frozen_engine), "apply", str(patch)],
        check=True, capture_output=True,
    )
    frozen_tree = git_source_state(frozen_engine, canonical_patch=patch)["canonical_patch"]
    if not frozen_tree["matches_worktree"] or frozen_tree["actual_tree"] != engine["tree"]:
        raise ValueError("frozen native engine differs from the CPU-qualified source tree")
    inputs = [
        args.reference, args.cpu_report, args.cpu_sources, args.gpu_smoke,
        args.sampling_calibration, args.engine_report, args.engine_sources,
        Path(evidence["source_arm"]) / "server/request_tokens.jsonl",
        source_launch,
        Path(reservation.__file__),
    ]
    if args.retention_config is not None:
        inputs.append(args.retention_config)
        if not {
            "beliefkv/runtime/reusable_subagents.py",
            "beliefkv/runtime/reusable_subagent_middleware.py",
            "beliefkv/runtime/subagent_retention.py",
            "beliefkv/experiments/subagent_retention.py",
            "tests/test_reusable_subagents.py", "tests/test_subagent_retention.py",
        } <= sources.keys():
            raise ValueError("reusable retention sources lack CPU qualification")
    if args.experiment_config is not None:
        inputs.extend((args.experiment_config, ROOT / experiment_spec["common_environment"]["WORKLOAD_MANIFEST"]))
        inputs.extend(Path(experiment_evidence[name]) for name in (
            "source_registration", "frozen_manifest",
        ))
    inputs.extend(
        Path(name) if Path(name).is_absolute() else ROOT / name
        for name in evidence["input_jsonl_prefixes"]
    )
    inputs.extend(summaries)
    if args.after_registration is not None:
        inputs.append(args.after_registration)
    inputs.extend(Path(reference["arms"]["full"][name]) for name in (
        "TOOL_TIMING_ARTIFACT", "H2D_SEED_ARTIFACT", "TRANSFER_SERVICE_SEED",
    ) if reference["arms"]["full"].get(name))
    reuse_selection = (
        reusable_source(quality, graph, expected_workflows=source_roots)
        if args.retention_config is not None else None
    )
    if reuse_selection is not None and not reuse_selection["accepted"]:
        raise ValueError("retention source lacks complete natural same-handle reuse evidence")
    workflows = sorted({row.event.workflow_id for row in graph.nodes if row.event is not None})
    exclusions = source_quality(quality, workflows)
    plan = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        "development_checkout": str(ROOT), "execution_checkout": str(checkout),
        "code_commit": revision, "engine_checkout": str(frozen_engine),
        "engine_tree": engine["tree"], "engine_evidence": engine,
        "source_engine_tree": captured_source["engine_tree"],
        "engine_compatibility": engine_compatibility,
        "source_sha256": sources, "passing_cpu_cases": count,
        "external_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in sorted(set(inputs))
        ],
        "source_arm": str(args.source_arm.resolve()), "source_plan": graph.summary(),
        "token_source_sha256": tokens["token_source_sha256"],
        "source_capacity": source_capacity,
        "source_server_parameters": smoke_server["source_server_parameters"],
        "workflow_ids": workflows,
        "quality_exclusions": [row["workflow_id"] for row in exclusions],
        "quality_exclusion_details": exclusions,
        "independent_task_correctness_verified": False,
        "hold_file": reference["hold_file"], "order": order,
        "after_registration": (
            str(args.after_registration.resolve()) if args.after_registration is not None else None
        ),
        "reference_environment": reference["arms"]["full"],
        "tool_restore_mode": reference["arms"]["full"].get(
            "TOOL_RESTORE_MODE", "speculative",
        ),
        "execution_frontier_restore": (
            reference["arms"]["full"].get("EXECUTION_FRONTIER_RESTORE") == "1"
        ),
        "frontier_cold_reclaim": (
            reference["arms"]["full"].get("FRONTIER_COLD_RECLAIM", "0") == "1"
        ),
        "retention_configuration": str(args.retention_config.resolve()) if args.retention_config else None,
        "experiment_configuration": str(args.experiment_config.resolve()) if args.experiment_config else None,
        "experiment_source_evidence": experiment_evidence,
        "reusable_source_opportunities": reuse_selection,
        "tool_prefetch_priority_ms": args.tool_prefetch_priority_ms,
        "max_running_requests": running,
        "run_root": str(args.run_root.resolve()), "port": args.port,
        "sampling_calibration": str(args.sampling_calibration.resolve()),
        "sampling_mode": args.sampling_mode,
        "maximum_replay_slowdown": args.maximum_replay_slowdown,
        "source_metrics": {
            "mean_jct_ms": native_baseline["mean_jct_ms"],
            "replay_duration_ms": quality["duration_seconds"] * 1000.,
        },
        "native_baseline": native_baseline,
        "gpu_replay_qualified": False, "performance_accepted": False,
        "scope": "Auxiliary same-work causal controls; retain all tails and source guard qualifications.",
    }
    if args.maximum_replay_slowdown is not None:
        source_speed_check(plan["source_metrics"], plan["source_metrics"], args.maximum_replay_slowdown)
    predecessor_ready(plan)
    plan["runtime_cpu_preflight"] = {}
    for label in order:
        settings, _, _ = environment(
            {"arms": {"full": plan["reference_environment"]}}, label, checkout,
            frozen_engine, args.run_root / label, args.port, args.run_root / "plugin",
            sampling_mode=plan["sampling_mode"],
            max_running_requests=plan["max_running_requests"],
            execution_frontier_restore=plan["execution_frontier_restore"],
            frontier_cold_reclaim=plan["frontier_cold_reclaim"],
            tool_restore_mode=plan["tool_restore_mode"],
        )
        plan["runtime_cpu_preflight"][label] = preflight_runtime(
            settings, plan["source_server_parameters"]["model_path"],
        )
    comparison.verify_sources(plan)
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_causal_comparison.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, env={
                **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
            },
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "order": plan["order"], "code_commit": revision, "performance_accepted": False,
    }, indent=2))


def watch(registration):
    plan = load(registration)
    state_path, run = registration.with_suffix(".state.json"), Path(plan["run_root"])
    if state_path.exists():
        raise FileExistsError("preserve previous comparison monitor outcome")
    state = {
        "status": "preflight", "watcher": reservation.process(os.getpid()),
        "run_root": str(run), "completed_arms": [], "original_exit_codes": {},
        "performance_accepted": False, "gpu_replay_qualified": False,
    }
    stopped, identities, arm = False, [], run

    def signal_stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, signal_stop)
    signal.signal(signal.SIGINT, signal_stop)

    def pulse():
        nonlocal identities
        if stopped:
            raise ValueError("comparison supervisor received a stop request")
        fresh.check_hold(plan, released=True)
        identities = owned_processes(identities)
        state.update(checked_at=reservation.now().isoformat(), owned=identities)
        reservation.write_json(state_path, state)

    with registration.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            comparison.verify_sources(plan)
            if (
                plan.get("native_baseline") is not None
                and plan["native_baseline"].get("metric_boundary") != METRIC_BOUNDARY
            ):
                raise ValueError("native baseline and replay require identical workflow event boundaries")
            while not predecessor_ready(plan):
                state["status"] = "waiting_previous_causal_comparison"
                pulse()
                time.sleep(10.)
            expected_work = predecessor_work(plan)
            comparison.verify_sources(plan)
            if not qualified.resources(plan["port"])["ready"]:
                raise ValueError("GPU, port or disk is occupied before comparison")
            fresh.check_hold(plan, released=True)
            run.mkdir(parents=True, exist_ok=False)
            with (run / "plugin_install.log").open("x") as output:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                     "--no-build-isolation", "--target", str(run / "plugin"),
                     str(ROOT / "configs/causal_replay_plugin")],
                    check=True, stdout=output, stderr=subprocess.STDOUT, timeout=60,
                )
            results = {}
            for label in plan["order"]:
                state.update(status="waiting_resources_" + label, current_arm=label)
                deadline = time.monotonic() + 60.
                while not qualified.resources(plan["port"])["ready"]:
                    pulse()
                    if time.monotonic() >= deadline:
                        raise ValueError("resources were not released for the next cold start")
                    time.sleep(2.)
                pulse()
                comparison.verify_sources(plan)
                arm = run / label
                for name in ("server", "opportunities"):
                    (arm / name).mkdir(parents=True, exist_ok=False)
                env, flags, event_socket = environment(
                    {"arms": {"full": plan["reference_environment"]}}, label, ROOT,
                    Path(plan["engine_checkout"]), arm, plan["port"], run / "plugin",
                    sampling_mode=plan.get("sampling_mode", "legacy"),
                    max_running_requests=plan.get("max_running_requests", 8),
                    execution_frontier_restore=plan.get("execution_frontier_restore", False),
                    frontier_cold_reclaim=plan.get("frontier_cold_reclaim", False),
                    tool_restore_mode=plan.get("tool_restore_mode", "speculative"),
                )
                if event_socket is not None and event_socket.exists():
                    raise ValueError("preserve the existing runtime event socket")
                launch = {
                    "chat_token_block_cache": True, "internal_comparison_mode": env["BELIEFKV_INTERNAL_COMPARISON_MODE"],
                    "server_random_seed": 21, "python_hash_seed": "21",
                    "prefetch_residency_protection_requested": env["PREFETCH_RESIDENCY_PROTECTION"] == "1",
                    "prefill_scheduling_policy_requested": env["PREFILL_SCHEDULING_POLICY"],
                    "tool_restore_mode_requested": env["TOOL_RESTORE_MODE"],
                    "tool_prefetch_requested": env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "1",
                    "tool_prefetch_priority_ms_requested": float(env["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"]),
                    "idle_prepare_policy_requested": env["IDLE_PREPARE_POLICY"],
                    "restore_tiers_requested": env["RESTORE_TIERS"],
                    "join_last_child_priority_requested": False,
                    "execution_frontier_restore_requested": env["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "1",
                    "frontier_cold_reclaim_requested": env["BELIEFKV_FRONTIER_COLD_RECLAIM"] == "1",
                    "tool_continuity_requested": env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "1",
                    "auxiliary_causal_replay": True,
                    "replay_sampling_mode": plan.get("sampling_mode", "legacy"),
                    "delayed_sampling_common_to_all_arms": env["SGLANG_ENABLE_DELAY_SAMPLE"] == "1",
                    "subagent_retention_requested": env["BELIEFKV_SUBAGENT_RETENTION"],
                }
                reservation.write_json(arm / "server/launch_configuration.json", launch)
                with (arm / "server.log").open("x") as output:
                    server = subprocess.Popen(
                        ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                        cwd=ROOT, env=qualified.driver_environment({"environment": env}, os.environ),
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                identities = [reservation.process(server.pid)]
                state.update(status="starting_" + label, server=identities[0])
                deadline = time.monotonic() + 480.
                while True:
                    pulse()
                    if server.poll() is not None:
                        raise ValueError("native server exited during startup")
                    ready = arm / "server/native_telemetry_ready.json"
                    census = arm / "server/native_capacity_census.json"
                    try:
                        health = httpx.get(f"http://127.0.0.1:{plan['port']}/health", timeout=2.)
                        available = health.is_success and ready.is_file() and census.is_file()
                    except httpx.HTTPError:
                        available = False
                    if available:
                        break
                    if time.monotonic() >= deadline:
                        raise ValueError("native server did not become ready")
                    time.sleep(1.)
                info = httpx.get(f"http://127.0.0.1:{plan['port']}/get_server_info", timeout=10.).json()
                required = {**plan["source_server_parameters"],
                            "enable_beliefkv_admission": label != "native_matched",
                            "enable_custom_logit_processor": True, "enable_session_radix_cache": True}
                if any(info.get(key) != value for key, value in required.items()):
                    raise ValueError("actual server parameters differ from the common source")
                reservation.write_json(arm / "server/server_info.json", info)
                scheduler = reservation.process(load(ready)["scheduler_pid"])
                identities = owned_processes(identities)
                if not scheduler or not any(
                    row["pid"] == scheduler["pid"]
                    and row["start_ticks"] == scheduler["start_ticks"]
                    for row in identities
                ):
                    raise ValueError("scheduler is not an owned native server descendant")
                apps = qualified.command(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"])
                if str(scheduler["pid"]) not in apps.splitlines():
                    raise ValueError("native scheduler has no actual GPU context")
                observed_configuration(plan, label, arm)
                command = [
                    sys.executable, str(ROOT / "scripts/run_causal_gpu_replay.py"),
                    "--source-arm", plan["source_arm"], "--output-dir", str(arm / "replay"),
                    "--base-url", f"http://127.0.0.1:{plan['port']}",
                ]
                if event_socket is not None:
                    command += ["--event-socket", str(event_socket)]
                with (arm / "replay.log").open("x") as output:
                    driver = subprocess.Popen(
                        command, cwd=ROOT, env={
                            **os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                            "PYTHONHASHSEED": "21",
                        },
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                identities.append(reservation.process(driver.pid))
                state.update(status="running_" + label, driver=identities[-1])
                while driver.poll() is None:
                    pulse()
                    if server.poll() is not None:
                        raise ValueError("native server exited during causal replay")
                    observed_configuration(plan, label, arm)
                    time.sleep(10.)
                state["original_exit_codes"][label] = driver.wait()
                if state["original_exit_codes"][label]:
                    raise ValueError("causal replay driver failed; preserve its failure evidence")
                result = load(arm / "replay/summary.json")
                work = validate_result(plan, result, arm, live_control=event_socket is not None)
                event_metrics = replay_event_metrics(result, arm, plan["workflow_ids"])
                if expected_work is not None and work != expected_work:
                    raise ValueError("native request work changed between comparison arms")
                expected_work = work
                state["status"] = "waiting_telemetry_" + label
                wait_for_telemetry_drain(arm, server=server, pulse=pulse)
                observed_configuration(plan, label, arm, final=True)
                cleanup(arm, identities, "complete_same_work_arm")
                identities = []
                if event_socket is not None:
                    event_socket.unlink(missing_ok=True)
                elapsed = result["workflow_elapsed_ms"]
                eligible = [value for key, value in elapsed.items() if key not in plan["quality_exclusions"]]
                results[label] = {
                    "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
                    "output_tokens": sum(row[1] for row in work.values()),
                    **event_metrics,
                    "source_unguarded_mean_jct_ms": statistics.mean(eligible),
                    "replay_duration_ms": result["finished_monotonic_ms"] - result["started_monotonic_ms"],
                    "same_request_work_verified": True,
                }
                baseline = plan.get("native_baseline")
                event_seconds = event_metrics["workflow_event_makespan_ms"] / 1000.
                throughput = results[label]["output_tokens"] / event_seconds
                results[label].update(
                    whole_run_output_tokens_per_second=throughput,
                    replay_driver_wall_output_tokens_per_second=(
                        results[label]["output_tokens"] * 1000. / results[label]["replay_duration_ms"]
                    ),
                )
                if baseline is not None:
                    results[label].update(
                        captured_native_mean_jct_change_percent=100. * (
                            results[label]["mean_jct_ms"] / baseline["mean_jct_ms"] - 1.
                        ),
                        captured_native_output_throughput_change_percent=100. * (
                            throughput / baseline["whole_run_output_tokens_per_second"] - 1.
                        ) if baseline["whole_run_output_tokens_per_second"] > 0 else None,
                    )
                speed = None
                if label == "native_matched" and plan.get("maximum_replay_slowdown") is not None:
                    speed = source_speed_check(
                        plan["source_metrics"], results[label], plan["maximum_replay_slowdown"],
                    )
                    results[label]["source_speed_calibration"] = speed
                    reservation.write_json(arm / "source_replay_calibration.json", speed)
                state["completed_arms"].append(label)
                reservation.write_json(run / "causal_comparison.json", {
                    "results": results, "quality_exclusions": plan["quality_exclusions"],
                    "quality_exclusion_details": plan.get("quality_exclusion_details", []),
                    "all_normal_tails_retained": True, "performance_accepted": False,
                    "scope": plan["scope"],
                    "after_registration": plan.get("after_registration"),
                    "native_baseline": plan.get("native_baseline"),
                })
                pulse()
                if speed is not None and not speed["accepted"]:
                    raise ValueError("native replay exceeded the registered source slowdown tolerance")
            state.update(status="collected_performance_review_pending", finished_at=reservation.now().isoformat())
        except BaseException as error:
            state.update(status="stopped_for_investigation", error=repr(error),
                         finished_at=reservation.now().isoformat())
            try:
                cleanup(arm, identities, "causal_comparison_failure_or_stop")
            except BaseException as cleanup_error:
                state["cleanup_error"] = repr(cleanup_error)
        finally:
            state["checked_at"] = reservation.now().isoformat()
            reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    for name in (
        "reference", "source-arm", "gpu-smoke", "sampling-calibration", "cpu-report",
        "cpu-sources", "engine", "engine-report", "engine-sources", "registration", "run-root",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--port", type=int, default=18554)
    parser.add_argument("--arms", nargs="+", choices=ALL_CONTROLS, default=list(DEFAULT_ORDER))
    parser.add_argument("--retention-config", type=Path)
    parser.add_argument("--experiment-config", type=Path)
    parser.add_argument("--tool-prefetch-priority-ms", type=float)
    parser.add_argument("--max-running-requests", type=int)
    parser.add_argument("--after-registration", type=Path)
    parser.add_argument("--sampling-mode", choices=("legacy", "position"), default="legacy")
    parser.add_argument("--maximum-replay-slowdown", type=float)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        if any(value is None for name, value in vars(args).items() if name not in (
            "watch", "port", "arms", "after_registration", "sampling_mode", "maximum_replay_slowdown",
            "retention_config",
            "experiment_config",
            "tool_prefetch_priority_ms",
            "max_running_requests",
        )):
            parser.error("registration requires source, qualifications, engine and fresh output paths")
        if not 1 <= args.port <= 65535:
            parser.error("invalid server port")
        register(args)


if __name__ == "__main__":
    main()
