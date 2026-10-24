#!/usr/bin/env python3
"""Qualify position replay on a selected source, then start same-work controls."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as controls
from scripts import run_causal_source_capture as capture
from scripts.benchmark_causal_sampling import calibration_batch_sizes

comparison, fresh, qualified, reservation, load = (
    controls.comparison, controls.fresh, controls.qualified, controls.reservation, controls.load,
)
SERVER_FIELDS = (
    "model_path", "dtype", "kv_cache_dtype", "mem_fraction_static", "context_length",
    "max_running_requests", "chunked_prefill_size", "page_size", "mamba_ssm_dtype",
    "mamba_full_memory_ratio", "mamba_radix_cache_strategy", "mamba_track_interval",
    "mamba_max_states_per_path", "hicache_size", "hicache_mem_layout",
    "hicache_io_backend", "hicache_host_memory_mode", "hicache_write_policy",
    "disable_overlap_schedule", "enable_mixed_chunk", "radix_cache_backend",
    "radix_eviction_policy", "schedule_policy", "enable_beliefkv_admission",
)
SAMPLING_MODULES = (
    "beliefkv/experiments/causal_replay_native.py",
    "beliefkv/experiments/causal_replay_sampling.py",
    "beliefkv/runtime/clock_evidence.py", "beliefkv/runtime/replay_token_capture.py",
)


def source_root_count(source):
    value = source.get("environment", {}).get("ROOT_COUNT", "36")
    if (
        type(value) is not str or not value.isascii() or not value.isdigit()
        or not 1 <= int(value) <= 156
    ):
        raise ValueError("source has an invalid declared root population")
    return int(value)


def complete_source_metrics(registration):
    original = load(registration)
    state = load(registration.with_suffix(".state.json"))
    run = Path(original["run_root"])
    roots = source_root_count(original)
    summary_path = run / f"native_capture/client_{roots}/summary.json"
    summary = load(summary_path)
    workflows = summary.get("workflows", [])
    joins = summary.get("delegation_round_count")
    durations = [summary.get("duration_seconds"), *(row.get("duration_seconds") for row in workflows)]
    if (
        state.get("run_root") != original["run_root"]
        or state.get("status") not in (
            "source_captured_gpu_replay_pending", "source_captured_selection_rejected",
        )
        or state.get("original_exit_code") != 0
        or summary.get("workflow_count") != roots or summary.get("completed_workflows") != roots
        or len(workflows) != roots
        or len({row.get("workflow_id") for row in workflows}) != roots
        or any(type(row.get("workflow_id")) is not str or not row["workflow_id"] for row in workflows)
        or any(row.get("native_agent_jct_eligible") is not True for row in workflows)
        or any(
            type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            for value in durations
        )
        or type(joins) is not int or joins <= 0
        or load(run / "token_evidence.json").get("token_evidence_complete") is not True
        or load(run / "causal_evidence.json").get("causal_graph_compiled") is not True
    ):
        raise ValueError("complete source lacks successful unguarded work or exact causal evidence")
    return {
        "client_summary_sha256": sha256_file(summary_path),
        "workflow_count": roots, "joins": joins,
        "original_selection": (
            state.get("source_selection") if original.get("source_profile") == "nested_oneshot"
            else capture.balanced_source(summary)
        ),
        "all_normal_tails_retained": True,
    }


def source_ready(plan):
    path = Path(plan["source_registration"])
    original = load(path)
    state = load(path.with_suffix(".state.json"))
    if state.get("run_root") != original["run_root"]:
        raise ValueError("source state belongs to another registration")
    if state["status"] in (
        "source_captured_gpu_replay_pending", "source_captured_selection_rejected",
    ):
        if state.get("original_exit_code") != 0:
            raise ValueError("selected source has a nonzero original exit code")
        if plan.get("source_selection_policy", "balanced_join") == "complete":
            if complete_source_metrics(path) != plan["reviewed_source_metrics"]:
                raise ValueError("reviewed complete source changed after registration")
        elif (
            state["status"] != "source_captured_gpu_replay_pending"
            or not state.get("source_selection", {}).get("accepted")
        ):
            raise ValueError("selected source did not pass the predeclared profile")
        return not any(
            reservation.alive(state[name]) for name in ("watcher", "driver") if state.get(name)
        )
    if state["status"] not in ("capture_preflight", "waiting_gpu_capture", "recording_native"):
        raise ValueError("source capture did not qualify: " + state["status"])
    if not state.get("watcher") or not reservation.alive(state["watcher"]):
        raise ValueError("source supervisor exited without a qualified completion")
    return False


def smoke_result(plan, summary, arm, server_parameters, server_identity, scheduler):
    if not server_identity or not scheduler:
        raise ValueError("position GPU check lacks a live server or scheduler identity")
    work = controls.receipt_work(arm / "smoke/native_requests.jsonl", sampling_mode="position")
    count = len(work)
    stop_count = sum(row[4]["type"] == "stop" for row in work.values())
    if (
        count != 24 or summary.get("verified_requests") != 24
        or summary.get("native_data_plane_sample_verified") is not True
        or not 1 <= stop_count <= 24 or summary.get("native_stop_requests") != stop_count
        or sha256_file(arm / "smoke/native_requests.jsonl") != summary.get("native_requests_sha256")
    ):
        raise ValueError("complete native token, stop and position sampling evidence is missing")
    server_registration = arm / "server_registration.json"
    client_registration = arm / "client_registration.json"
    reservation.write_json(server_registration, {
        **plan, "source_server_parameters": server_parameters,
        "actual_server": server_identity, "actual_scheduler": scheduler,
    })
    reservation.write_json(client_registration, {
        "unchanged_server_modules": {name: plan["source_sha256"][name] for name in SAMPLING_MODULES},
        "sampling_mode": "position", "source_arm": plan["source_arm"],
        "source_registration": plan["source_registration"],
        "source_selection_policy": plan.get("source_selection_policy", "balanced_join"),
        "reviewed_source_metrics": plan.get("reviewed_source_metrics"),
    })
    return {
        "gpu_process_verified": True, "native_data_plane_sample_verified": True,
        "native_position_sampling_verified": True, "sampling_mode": "position",
        "requests": count, "native_stop_requests": stop_count,
        "native_requests": str(arm / "smoke/native_requests.jsonl"),
        "native_requests_sha256": summary["native_requests_sha256"],
        "server_registration": str(server_registration),
        "server_registration_sha256": sha256_file(server_registration),
        "client_registration": str(client_registration),
        "client_registration_sha256": sha256_file(client_registration),
        "performance_accepted": False,
        "scope": "Bounded exact token/stop/session GPU check, not a complete JCT result.",
    }


def comparison_command(plan, run, arm, development):
    command = [
        sys.executable, str(development / "scripts/run_causal_comparison.py"),
        "--reference", plan["reference_registration"], "--source-arm", str(arm),
        "--gpu-smoke", str(run / "gpu_smoke.json"),
        "--sampling-calibration", str(run / "sampling_calibration.json"),
        "--cpu-report", plan["cpu_report"], "--cpu-sources", plan["cpu_sources"],
        "--engine", plan["engine_cpu_checkout"],
        "--engine-report", plan["engine_evidence"]["report"],
        "--engine-sources", plan["engine_evidence"]["sources"],
        "--registration", plan["comparison_registration"], "--run-root", plan["comparison_root"],
        "--port", str(plan["port"]), "--sampling-mode", "position",
        "--max-running-requests", str(plan.get("max_running_requests", 8)),
    ]
    if plan.get("experiment_config"):
        command += ["--experiment-config", plan["experiment_config"]]
        common = load(Path(plan["experiment_config"]))["common_environment"]
        if common.get("TOOL_PREFETCH") == "1" and float(common.get("TOOL_PREFETCH_PRIORITY_MS", "0")) > 0:
            command += ["--tool-prefetch-priority-ms", common["TOOL_PREFETCH_PRIORITY_MS"]]
    else:
        command += ["--maximum-replay-slowdown", "0.05"]
    command += ["--arms", *plan.get("comparison_order", [
        "native_matched", "no_tool_prefetch", "join_priority_only",
    ])]
    return command


def register(args):
    for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root, args.comparison_registration,
        args.comparison_registration.with_suffix(".state.json"), args.comparison_root,
    ):
        if path.exists():
            raise FileExistsError("preserve the existing calibration or comparison")
    source = load(args.source_registration)
    spec_path = getattr(args, "experiment_config", None)
    spec = load(spec_path) if spec_path else None
    if source.get("source_profile") != ("nested_oneshot" if spec else "balanced_join"):
        raise ValueError("calibration source differs from the predeclared profile")
    roots = source_root_count(source)
    capture.project_disjoint_workload(
        Path(source["environment"]["WORKLOAD_MANIFEST"]), expected_workflows=roots,
    )
    policy = getattr(args, "source_selection", "balanced_join")
    if spec is not None:
        policy = "predeclared"
    reason = getattr(args, "source_selection_reason", None)
    if policy == "complete" and (type(reason) is not str or not reason.strip()):
        raise ValueError("complete source selection requires a disclosed review reason")
    metrics = complete_source_metrics(args.source_registration) if policy == "complete" else None
    comparison.verify_sources(source)
    fresh.check_hold(source, released=True)
    reference = load(args.reference)
    fresh.verify(reference)
    running = 8
    if spec is not None:
        reference = controls.experiment_reference(reference, spec)
        common = spec["common_environment"]
        running = int(common["MAX_RUNNING_REQUESTS"])
        if (
            common["ROOT_COUNT"] != str(roots)
            or source["environment"]["MAX_RUNNING_REQUESTS"] != str(running)
            or source["environment"].get("SAMPLING_PROFILE", "legacy_greedy")
            != common.get("SAMPLING_PROFILE", "legacy_greedy")
            or source["environment"].get("CUDA_HOME") != common.get("CUDA_HOME")
            or any(
                source["environment"].get(name) != common.get(name)
                for name in ("MODEL_PATH", "SERVED_MODEL_NAME", "MODEL_CACHE_LAYOUT", "CONTEXT_TOKENIZER_PATH")
                if name in common
            )
            or sha256_file(ROOT / common["WORKLOAD_MANIFEST"]) != sha256_file(
                Path(source["environment"]["WORKLOAD_MANIFEST"])
            )
            or not spec.get("order") or any(
                label not in controls.CONTROLS or label == "native_matched"
                for label in spec["order"]
            )
            or len(set(spec["order"])) != len(spec["order"])
        ):
            raise ValueError("predeclared source population, batch, tasks or control order differs")
        if source.get("capture_capacity") is not None:
            controls.source_reference(reference, roots, running, source["capture_capacity"])
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    if not {
        "scripts/run_position_replay_calibration.py", "tests/test_position_replay_calibration.py",
        "tests/test_causal_replay_position_sampling.py", "scripts/run_causal_comparison.py",
    } <= sources.keys():
        raise ValueError("position calibration operator lacks CPU qualification")
    evidence = capture.engine_evidence(args.engine, args.engine_report, args.engine_sources)
    if evidence["tree"] != source["engine_tree"]:
        raise ValueError("calibration and source use different native engines")
    fresh._require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("freeze qualified main")
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    label = args.run_root.name.rsplit("_", 1)[-1]
    checkout = comparison.EXPERIMENTS / ".worktrees" / f"beliefkv-{label}-position-{revision[:12]}"
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    comparison.verify_cpu_snapshot(checkout, sources)
    paths = (
        args.source_registration, args.reference, args.cpu_report, args.cpu_sources,
        args.engine_report, args.engine_sources, Path(reservation.__file__),
    )
    if spec_path is not None:
        paths += (spec_path, ROOT / spec["common_environment"]["WORKLOAD_MANIFEST"])
    if metrics is not None:
        source_root = Path(source["run_root"])
        paths += tuple(source_root / name for name in (
            f"native_capture/client_{roots}/summary.json", "token_evidence.json",
            "causal_evidence.json", "source_selection.json",
        ))
    plan = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        "code_commit": revision, "development_checkout": str(ROOT),
        "execution_checkout": str(checkout), "source_sha256": sources,
        "passing_cpu_cases": count, "external_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)} for path in paths
        ],
        "source_registration": str(args.source_registration.resolve()),
        "source_arm": str((Path(source["run_root"]) / "native_capture").resolve()),
        "reference_registration": str(args.reference.resolve()),
        "hold_file": source["hold_file"], "engine_checkout": source["engine_checkout"],
        "engine_tree": source["engine_tree"], "engine_evidence": evidence,
        "engine_cpu_checkout": str(args.engine.resolve()),
        "cpu_report": str(args.cpu_report.resolve()), "cpu_sources": str(args.cpu_sources.resolve()),
        "run_root": str(args.run_root.resolve()), "port": args.port,
        "comparison_registration": str(args.comparison_registration.resolve()),
        "comparison_root": str(args.comparison_root.resolve()),
        "sampling_mode": "position", "performance_accepted": False,
        "source_selection_policy": policy,
        "source_selection_reason": reason,
        "reviewed_source_metrics": metrics,
        "experiment_config": str(spec_path.resolve()) if spec_path else None,
        "root_count": roots, "max_running_requests": running,
        "comparison_order": spec["order"] if spec else [
            "native_matched", "no_tool_prefetch", "join_priority_only",
        ],
    }
    comparison.verify_sources(plan)
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("x") as log:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_position_replay_calibration.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "comparison_registration": plan["comparison_registration"],
    }, indent=2))


def watch(registration):
    plan = load(registration)
    path, run = registration.with_suffix(".state.json"), Path(plan["run_root"])
    if path.exists():
        raise FileExistsError("preserve the previous calibration outcome")
    state = {
        "status": "waiting_selected_source", "watcher": reservation.process(os.getpid()),
        "run_root": str(run), "performance_accepted": False,
    }
    interrupted, identities = False, []

    def stop(*_args):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def pulse():
        nonlocal identities
        if interrupted:
            raise ValueError("position calibration was stopped")
        fresh.check_hold(plan, released=True)
        identities = controls.owned_processes(identities)
        state.update(checked_at=reservation.now().isoformat(), owned=identities)
        reservation.write_json(path, state)

    def process(command, environment, log_path):
        with log_path.open("x") as output:
            child = subprocess.Popen(
                command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            )
        identities.append(reservation.process(child.pid))
        return child

    def complete(child, *, server=None, limit=480.):
        deadline = time.monotonic() + limit
        while child.poll() is None:
            pulse()
            if server is not None and server.poll() is not None:
                raise ValueError("calibration server exited during the GPU check")
            if time.monotonic() >= deadline:
                raise ValueError("bounded calibration process exceeded its deadline")
            time.sleep(2.)
        if child.wait() != 0:
            raise ValueError("calibration process failed; preserve its output")

    with registration.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            comparison.verify_sources(plan)
            while not source_ready(plan):
                pulse()
                time.sleep(10.)
            source = load(Path(plan["source_registration"]))
            comparison.verify_sources(source)
            arm = Path(plan["source_arm"])
            roots = source_root_count(source)
            summary = load(arm / f"client_{roots}/summary.json")
            reviewed = plan.get("source_selection_policy", "balanced_join") == "complete"
            predeclared = plan.get("source_selection_policy") == "predeclared"
            if not reviewed and not predeclared and not capture.balanced_source(summary)["accepted"]:
                raise ValueError("source selection changed after capture")
            graph, expected, _evidence = controls.source_plan(arm)
            joins = graph.summary()["event_counts"]["join_create"]
            if predeclared:
                complete_source_metrics(Path(plan["source_registration"]))
                if not capture.oneshot_source(summary, graph, expected_workflows=roots)["accepted"]:
                    raise ValueError("predeclared one-shot source is incomplete or guarded")
            if (
                reviewed and joins != plan["reviewed_source_metrics"]["joins"]
                or not reviewed and not predeclared and joins < 80
            ):
                raise ValueError("actual causal source has too few JOINs")
            with controls.CapturedTokenCorpus(arm / "server/request_tokens.jsonl", expected):
                pass
            while not qualified.resources(plan["port"])["ready"]:
                state["status"] = "waiting_gpu_calibration"
                pulse()
                time.sleep(10.)
            run.mkdir(parents=True, exist_ok=False)
            (run / "server").mkdir()
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
                 "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
                check=True, capture_output=True, timeout=30,
            )
            pulse()
            if not qualified.resources(plan["port"])["ready"]:
                raise ValueError("GPU resources changed before calibration")
            with (run / "plugin_install.log").open("x") as output:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                     "--no-build-isolation", "--target", str(run / "plugin"),
                     str(ROOT / "configs/causal_replay_plugin")],
                    check=True, stdout=output, stderr=subprocess.STDOUT, timeout=60,
                )
            reference = load(Path(plan["reference_registration"]))
            running = plan.get("max_running_requests", 8)
            if plan.get("experiment_config"):
                reference = controls.experiment_reference(
                    reference, load(Path(plan["experiment_config"])),
                )
            source_capacity = load(arm / "server/native_capacity_census.json")["capacity"]
            reference = controls.source_reference(reference, roots, running, source_capacity)
            settings, flags, _socket = controls.environment(
                reference, "native_matched", ROOT, Path(plan["engine_checkout"]),
                run, plan["port"], run / "plugin", sampling_mode="position",
                max_running_requests=running,
            )
            server = process(
                ["bash", str(ROOT / "scripts/launch_qwen35_native_v0520.sh"), *flags],
                qualified.driver_environment({"environment": settings}, os.environ), run / "server.log",
            )
            state.update(status="starting_position_gpu_check", server=identities[0])
            deadline = time.monotonic() + 480.
            ready = run / "server/native_telemetry_ready.json"
            while True:
                pulse()
                if server.poll() is not None:
                    raise ValueError("position GPU server exited at startup")
                try:
                    health = httpx.get(f"http://127.0.0.1:{plan['port']}/health", timeout=2.)
                    available = health.is_success and ready.is_file()
                except httpx.HTTPError:
                    available = False
                if available:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("position GPU server did not become ready")
                time.sleep(1.)
            info = httpx.get(f"http://127.0.0.1:{plan['port']}/server_info", timeout=10.).json()
            source_info = load(arm / "server/server_info.json")
            parameters = {name: source_info[name] for name in SERVER_FIELDS}
            required = {**parameters, "enable_custom_logit_processor": True, "enable_session_radix_cache": True}
            if any(info.get(name) != value for name, value in required.items()):
                raise ValueError("actual calibration server differs from the native source")
            if load(run / "server/native_capacity_census.json")["capacity"] != load(
                arm / "server/native_capacity_census.json",
            )["capacity"]:
                raise ValueError("actual calibration pools differ from the native source")
            reservation.write_json(run / "server/server_info.json", info)
            scheduler = reservation.process(load(ready)["scheduler_pid"])
            identities = controls.owned_processes(identities)
            if not scheduler or not any(
                row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
                for row in identities
            ):
                raise ValueError("calibration scheduler is not an owned descendant")
            gpu_pids = qualified.command(
                ["nvidia-smi", "--id=0", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
            )
            if str(scheduler["pid"]) not in gpu_pids.splitlines():
                raise ValueError("calibration scheduler has no verified GPU context")
            state["status"] = "checking_position_tokens_sessions_stop"
            smoke = process(
                [sys.executable, str(ROOT / "scripts/run_causal_token_smoke.py"),
                 "--source-arm", str(arm), "--output-dir", str(run / "smoke"),
                 "--base-url", f"http://127.0.0.1:{plan['port']}", "--contexts", "8", "--turns", "3"],
                {**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
                run / "smoke.log",
            )
            complete(smoke, server=server)
            if server.poll() is not None:
                raise ValueError("position server exited before verification")
            result = smoke_result(
                plan, load(run / "smoke/summary.json"), run, parameters,
                reservation.process(server.pid), scheduler,
            )
            reservation.write_json(run / "gpu_smoke.json", result)
            controls.cleanup(run, identities, "position_gpu_microcheck_complete")
            identities = []
            while not qualified.resources(plan["port"])["ready"]:
                pulse()
                time.sleep(2.)
            cuda = Path(settings.get("CUDA_HOME") or (
                Path(sys.prefix) / "lib/python3.11/site-packages/nvidia/cu13"
            ))
            env = {
                **os.environ, "CUDA_VISIBLE_DEVICES": "0", "PYTHONHASHSEED": "21",
                "CUDA_HOME": str(cuda), "LIBRARY_PATH": str(cuda / "lib"),
                "LD_LIBRARY_PATH": str(cuda / "lib"), "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
            }
            state["status"] = "benchmarking_position_sampling"
            benchmark = process(
                [sys.executable, str(ROOT / "scripts/benchmark_causal_sampling.py"),
                 "--engine", plan["engine_checkout"], "--model", parameters["model_path"],
                 "--output", str(run / "sampling_calibration.json"), "--position",
                 "--sampling-profile", source["environment"].get("SAMPLING_PROFILE", "legacy_greedy"),
                 "--batch-sizes", *(str(value) for value in calibration_batch_sizes(running))],
                env, run / "sampling_calibration.log",
            )
            complete(benchmark)
            identities = []
            comparison.verify_sources(plan)
            development = Path(plan["development_checkout"])
            fresh._require_clean(development)
            comparison.verify_cpu_snapshot(development, plan["source_sha256"])
            state["status"] = "registering_calibrated_controls"
            pulse()
            command = comparison_command(plan, run, arm, development)
            with (run / "comparison_registration.log").open("x") as output:
                subprocess.run(
                    command, cwd=development,
                    env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
                    check=True, stdout=output, stderr=subprocess.STDOUT, timeout=180.,
                )
            state.update(
                status="calibrated_comparison_registered",
                comparison_registration=plan["comparison_registration"],
            )
        except BaseException as error:
            state.update(status="stopped_for_investigation", error=repr(error))
            if identities:
                try:
                    controls.cleanup(run, identities, "position_calibration_failure_or_stop")
                except BaseException as cleanup_error:
                    state["cleanup_error"] = repr(cleanup_error)
        finally:
            state.update(finished_at=reservation.now().isoformat(), checked_at=reservation.now().isoformat())
            reservation.write_json(path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    for name in (
        "source-registration", "reference", "cpu-report", "cpu-sources", "engine",
        "engine-report", "engine-sources", "registration", "run-root",
        "comparison-registration", "comparison-root",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--port", type=int, default=18560)
    parser.add_argument("--source-selection", choices=("balanced_join", "complete"), default="balanced_join")
    parser.add_argument("--source-selection-reason")
    parser.add_argument("--experiment-config", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch.resolve())
    elif any(
        value is None for name, value in vars(args).items()
        if name not in ("watch", "port", "source_selection", "source_selection_reason", "experiment_config")
    ):
        parser.error("registration requires the source capture, CPU evidence and fresh output paths")
    elif not 1 <= args.port <= 65535:
        parser.error("invalid GPU calibration port")
    else:
        register(args)


if __name__ == "__main__":
    main()
