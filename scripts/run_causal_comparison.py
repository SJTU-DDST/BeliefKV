#!/usr/bin/env python3
"""Freeze and supervise selected same-work causal policy controls."""

from __future__ import annotations

import argparse
import fcntl
import json
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
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_gpu_replay import source_plan
from scripts.run_causal_source_capture import (
    comparison, engine_evidence, fresh, load, qualified, reservation,
)

ORDER = ("full", "native_matched", "join_priority_only", "no_background_prepare")
CONTROLS = (*ORDER, "no_tool_prefetch")
SERVER_FIELDS = (
    "PREFETCH_LEAD_MS", "JOIN_PREFETCH_MIN_ADVANCE_MS", "TOOL_PREFETCH_MIN_ADVANCE_MS",
    "PARALLEL_WAIT_PREFETCH", "WAIT_PREFETCH_CONTEXTS",
    "PREDICTION_MAINTENANCE_INTERVAL_MS", "SEMANTIC_WORK_STATISTIC",
    "EOS_PROTOCOL_WINDOW_MS", "PROFILE_SHARED_PATH", "TERMINAL_CACHE_DIAGNOSTICS",
    "H2D_OPPORTUNITY_SAMPLING", "JOIN_PREFETCH_TRIGGER",
    "JOIN_PREDICTION_MIN_FULL_BYTES", "JOIN_PREDICTION_INTERVAL_MS",
    "JOIN_PREDICTION_MAX_CANDIDATES", "ONLINE_TOOL_TIMING",
    "BORROWABLE_PARENT_HEADROOM",
)


def environment(reference, mode, checkout, engine, arm, port, plugin):
    if mode not in CONTROLS:
        raise ValueError("unknown causal comparison control: " + mode)
    policy = "full" if mode in ("no_background_prepare", "no_tool_prefetch") else mode
    required = {
        "MAX_RUNNING_REQUESTS": "8", "SERVER_RANDOM_SEED": "21",
        "SAMPLING_SEED": "21", "PYTHONHASHSEED": "21", "HICACHE_SIZE_GB": "200",
        "HOST_SPLIT": "80:20", "MODEL_CONTEXT_TOKENS": "262144",
        "JOIN_PREFETCH_TRIGGER": "observed_eos", "PREFILL_SCHEDULING_POLICY": "workflow",
        "TOOL_RESTORE_MODE": "speculative", "RESTORE_TIERS": "complete",
        "TOOL_CONTINUITY": "0", "EXECUTION_FRONTIER_RESTORE": "0",
    }
    for name, value in required.items():
        if reference["arms"]["full"].get(name) != value:
            raise ValueError("causal comparison changed the qualified V108/EOS profile: " + name)
    settings = arm_environment(
        reference["arms"]["full"], policy,
        tool_prefetch=False if mode == "no_tool_prefetch" else None,
    )
    settings.update(
        PYTHONPATH=f"{checkout}:{plugin}", CUDA_VISIBLE_DEVICES="0",
        SGLANG_SOURCE_CHECKOUT=str(engine), RUN_ROOT=str(arm), PORT=str(port),
        MEM_FRACTION_STATIC="0.94", CONTEXT_LENGTH="262144",
        CHUNKED_PREFILL_SIZE="4096", ENABLE_SESSION_RADIX_CACHE="1",
        HOST_NUMA_NODE="1", HOST_NUMA_MEMORY_POLICY="preferred",
        HICACHE_WRITE_POLICY="write_back", BELIEFKV_FULL_MAMBA_HOST_SPLIT="80:20",
        BELIEFKV_CAUSAL_REPLAY_BRIDGE="1", SGLANG_PLUGINS="beliefkv_causal_replay",
        SGLANG_ENABLE_DELAY_SAMPLE="1", BELIEFKV_REPLAY_TOKEN_CAPTURE="0",
        BELIEFKV_RECORD_OUTPUT_TOKEN_IDS="1",
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
        if policy == "full":
            path = Path(settings[source])
            settings["BELIEFKV_" + target] = str(path)
            settings["BELIEFKV_" + digest] = sha256_file(path)
    flags = ["--mamba-full-memory-ratio", "0.9", "--enable-custom-logit-processor"]
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


def preflight_runtime(settings, model_path):
    if settings["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "native_matched":
        return {"native_runtime_not_enabled": True}
    with tempfile.TemporaryDirectory(prefix="beliefkv-causal-runtime-") as directory:
        env = qualified.driver_environment({"environment": settings}, os.environ)
        env["CUDA_VISIBLE_DEVICES"] = "99999"
        for name in (
            "BELIEFKV_ADMISSION_OPPORTUNITY_DIR", "BELIEFKV_ADMISSION_TELEMETRY_DIR",
            "BELIEFKV_NATIVE_TELEMETRY_DIR",
        ):
            env.pop(name, None)
        source = (
            "import sys; "
            "from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime; "
            "runtime=NativeAdmissionRuntime(event_socket_path=sys.argv[1],model_path=sys.argv[2]); "
            "runtime.close()"
        )
        result = subprocess.run(
            [sys.executable, "-c", source, str(Path(directory) / "events.sock"), model_path],
            env=env, check=True, capture_output=True, text=True, timeout=45,
        )
    return {"actual_runtime_constructor_accepted": True, "cuda_visible_devices": "99999",
            "output_tail": result.stdout[-1000:]}


def receipt_work(path):
    result = {}
    for raw in path.open():
        row = json.loads(raw)
        request = row["request_id"]
        if row.get("exact_tokens_verified") is not True or request in result:
            raise ValueError("missing exact token verification or duplicate replay request")
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
    work = receipt_work(arm / "replay/native_requests.jsonl")
    if (
        len(work) != result["completed_requests"]
        or sum(row[0] for row in work.values()) != plan["source_plan"]["prompt_tokens"]
        or sum(row[1] for row in work.values()) != plan["source_plan"]["output_tokens"]
    ):
        raise ValueError("replay receipt population differs from completed work")
    return work


def observed_configuration(plan, label, arm, *, final=False):
    mode = "full" if label in ("no_background_prepare", "no_tool_prefetch") else label
    overrides = (
        {"background_prepare": False} if label == "no_background_prepare" else
        {"tool_prefetch": False} if label == "no_tool_prefetch" else None
    )
    comparison.observe_arm(
        mode, arm, max_running_requests=8, runtime_overrides=overrides,
    )
    if final and mode != "native_matched" and qualified.last_runtime_state(
        arm / "opportunities/admission_opportunities.jsonl",
    ) is None:
        raise ValueError("controlled arm has no observed runtime configuration")
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
    for sig, grace in ((signal.SIGTERM, 12.), (signal.SIGKILL, 3.)):
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
    record["remaining_processes"] = [row for row in known if reservation.alive(row)]
    record["finished_at"] = reservation.now().isoformat()
    reservation.write_json(arm / "owned_process_cleanup.json", record)
    if record["remaining_processes"]:
        raise RuntimeError("owned replay processes remain after cleanup")


def register(args):
    order = list(args.arms)
    if not order or len(order) != len(set(order)) or any(label not in CONTROLS for label in order):
        raise ValueError("select distinct supported causal controls")
    if args.after_registration is not None and args.after_registration.resolve() == args.registration.resolve():
        raise ValueError("a causal comparison cannot depend on itself")
    for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    ):
        if path.exists():
            raise FileExistsError("preserve existing causal comparison and registration")
    reference = load(args.reference)
    fresh.verify(reference)
    fresh.check_hold(reference, released=True)
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
        or smoke.get("requests") != 24 or smoke.get("native_stop_requests") != 24
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
    calibration = load(args.sampling_calibration)
    if (
        calibration.get("native_sampler_sha256") != sha256_file(
            args.engine / "python/sglang/srt/layers/sampler.py",
        )
        or calibration.get("benchmark_sha256") != sources["scripts/benchmark_causal_sampling.py"]
        or calibration.get("forcing_sha256") != sources["beliefkv/experiments/causal_replay_sampling.py"]
        or [row["batch_size"] for row in calibration.get("cases", [])] != [1, 8]
    ):
        raise ValueError("native replay sampling costs were not measured for this source")
    graph, expected, evidence = source_plan(args.source_arm)
    with CapturedTokenCorpus(args.source_arm / "server/request_tokens.jsonl", expected) as corpus:
        tokens = corpus.evidence
    if graph.summary()["workflows"] != 36:
        raise ValueError("causal source must retain all 36 original workflows")
    engine = engine_evidence(args.engine, args.engine_report, args.engine_sources)
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
        Path(reservation.__file__),
    ]
    inputs.extend(
        Path(name) if Path(name).is_absolute() else ROOT / name
        for name in evidence["input_jsonl_prefixes"]
    )
    summaries = list(args.source_arm.glob("client_*/summary.json"))
    inputs.extend(summaries)
    if args.after_registration is not None:
        inputs.append(args.after_registration)
    inputs.extend(Path(reference["arms"]["full"][name]) for name in (
        "TOOL_TIMING_ARTIFACT", "H2D_SEED_ARTIFACT", "TRANSFER_SERVICE_SEED",
    ))
    quality = load(summaries[0])
    workflows = sorted({row.event.workflow_id for row in graph.nodes if row.event is not None})
    exclusions = source_quality(quality, workflows)
    plan = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        "development_checkout": str(ROOT), "execution_checkout": str(checkout),
        "code_commit": revision, "engine_checkout": str(frozen_engine),
        "engine_tree": engine["tree"], "engine_evidence": engine,
        "source_sha256": sources, "passing_cpu_cases": count,
        "external_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in sorted(set(inputs))
        ],
        "source_arm": str(args.source_arm.resolve()), "source_plan": graph.summary(),
        "token_source_sha256": tokens["token_source_sha256"],
        "source_capacity": load(args.source_arm / "server/native_capacity_census.json")["capacity"],
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
        "run_root": str(args.run_root.resolve()), "port": args.port,
        "sampling_calibration": str(args.sampling_calibration.resolve()),
        "gpu_replay_qualified": False, "performance_accepted": False,
        "scope": "Auxiliary same-work causal controls; retain all tails and source guard qualifications.",
    }
    predecessor_ready(plan)
    plan["runtime_cpu_preflight"] = {}
    for label in order:
        settings, _, _ = environment(
            {"arms": {"full": plan["reference_environment"]}}, label, checkout,
            frozen_engine, args.run_root / label, args.port, args.run_root / "plugin",
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
                    "idle_prepare_policy_requested": env["IDLE_PREPARE_POLICY"],
                    "restore_tiers_requested": env["RESTORE_TIERS"],
                    "join_last_child_priority_requested": False,
                    "execution_frontier_restore_requested": False, "tool_continuity_requested": False,
                    "auxiliary_causal_replay": True, "delayed_sampling_common_to_all_arms": True,
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
                if expected_work is not None and work != expected_work:
                    raise ValueError("native request work changed between comparison arms")
                expected_work = work
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
                    "workflow_jct_ms": elapsed, "mean_jct_ms": statistics.mean(elapsed.values()),
                    "source_unguarded_mean_jct_ms": statistics.mean(eligible),
                    "replay_duration_ms": result["finished_monotonic_ms"] - result["started_monotonic_ms"],
                    "same_request_work_verified": True,
                }
                state["completed_arms"].append(label)
                reservation.write_json(run / "causal_comparison.json", {
                    "results": results, "quality_exclusions": plan["quality_exclusions"],
                    "quality_exclusion_details": plan.get("quality_exclusion_details", []),
                    "all_normal_tails_retained": True, "performance_accepted": False,
                    "scope": plan["scope"],
                    "after_registration": plan.get("after_registration"),
                })
                pulse()
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
    parser.add_argument("--arms", nargs="+", choices=CONTROLS, default=list(ORDER))
    parser.add_argument("--after-registration", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        if any(value is None for name, value in vars(args).items() if name not in (
            "watch", "port", "arms", "after_registration",
        )):
            parser.error("registration requires source, qualifications, engine and fresh output paths")
        if not 1 <= args.port <= 65535:
            parser.error("invalid server port")
        register(args)


if __name__ == "__main__":
    main()
