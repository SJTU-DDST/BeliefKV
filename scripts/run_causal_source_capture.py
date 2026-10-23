#!/usr/bin/env python3
"""Record one predeclared native trajectory; never count it as a performance arm."""

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
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_eos_pair_after_modules as pair
from scripts.audit_causal_replay import audit as audit_graph
from scripts.audit_replay_token_capture import audit as audit_tokens

fresh, comparison = pair.fresh, pair.comparison
qualified, reservation, load = pair.qualified, pair.reservation, pair.load


def reference_complete(plan, state, *, alive=reservation.alive):
    if (
        state.get("status") != "collected_performance_review_pending"
        or state.get("completed_arms") != plan["order"]
        or any(alive(state[name]) for name in ("watcher", "driver"))
    ):
        raise ValueError("reference pair must finish and release its verified processes")


def environment(reference, checkout: Path, engine: Path, run: Path, port: int):
    run = run.resolve()
    base = reference["arms"]["native_matched"]
    required = {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21", "PYTHONHASHSEED": "21",
        "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
        "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.95",
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "FANOUT_PROFILE": "native_in_graph_nested_2to4",
    }
    if any(base.get(name) != value for name, value in required.items()):
        raise ValueError("capture must preserve the existing seeded 36-root workload")
    relative = Path(base["WORKLOAD_MANIFEST"]).relative_to(Path(reference["execution_checkout"]))
    target = checkout / relative
    if sha256_file(target) != sha256_file(Path(base["WORKLOAD_MANIFEST"])):
        raise ValueError("capture workload manifest changed")
    result = arm_environment(dict(base), "native_matched")
    result.update(
        PYTHONPATH=str(checkout), WORKLOAD_MANIFEST=str(target),
        SGLANG_SOURCE_CHECKOUT=str(engine), RUN_ROOT=str(run / "native_capture"),
        PORT=str(port), CUDA_VISIBLE_DEVICES="0", BELIEFKV_REPLAY_TOKEN_CAPTURE="1",
    )
    return result


def engine_evidence(engine: Path, report: Path, sources: Path) -> dict:
    proof = load(sources)
    observed = git_source_state(
        engine, canonical_patch=ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )
    cases = list(ET.parse(report).getroot().iter("testcase"))
    xml = ET.parse(report).getroot()
    if (
        not cases or any(list(xml.iter(tag)) for tag in ("failure", "error", "skipped"))
        or len({(row.get("classname"), row.get("name")) for row in cases}) != len(cases)
        or not proof["canonical_patch"]["matches_worktree"]
        or observed["state_sha256"] != proof["state_sha256"]
    ):
        raise ValueError("engine source tree or CPU evidence changed")
    return {
        "report": str(report.resolve()), "report_sha256": sha256_file(report),
        "sources": str(sources.resolve()), "sources_sha256": sha256_file(sources),
        "tree": observed["canonical_patch"]["actual_tree"],
        "passing_cases": len(cases), "gpu_validation_pending": True,
    }


def observe(arm: Path, *, final=False):
    path = arm / "server/native_telemetry_status.json"
    if not path.exists():
        if final:
            raise ValueError("capture lacks final native telemetry")
        return
    state = load(path)
    if (
        state.get("replay_token_capture") is not True
        or state.get("writer_error") is not None or state.get("failed_records") != 0
    ):
        raise ValueError("exact token recording is disabled or its writer failed")
    launch = arm / "server/launch_configuration.json"
    if final and not launch.is_file():
        raise ValueError("capture lacks final launch configuration")
    if launch.exists():
        config = load(launch)
        if (
            config.get("native_policy_baseline") is not True
            or config.get("server_random_seed") != 21 or config.get("python_hash_seed") != "21"
        ):
            raise ValueError("capture policy or seed differs from the native source")


def balanced_source(summary):
    workflows = summary.get("workflows", [])
    durations = sorted(
        row["duration_seconds"] for row in workflows
        if type(row.get("duration_seconds")) in (int, float)
        and math.isfinite(row["duration_seconds"]) and row["duration_seconds"] > 0
    )
    duration = summary.get("duration_seconds", 0)
    valid_duration = (
        type(duration) in (int, float) and math.isfinite(duration) and duration > 0
    )
    reasons = []
    if (
        len(workflows) != 36 or len(durations) != 36
        or summary.get("completed_workflows") != 36
        or not valid_duration
    ):
        reasons.append("incomplete_workflow_population")
    joins = summary.get("delegation_round_count", 0)
    if type(joins) is not int or joins < 80:
        reasons.append("fewer_than_80_joins")
    if any(row.get("native_agent_jct_eligible") is not True for row in workflows):
        reasons.append("source_guard_or_ineligible_workflow")
    gap = durations[-1] - durations[-2] if len(durations) >= 2 else None
    ratio = durations[-1] / statistics.median(durations) if durations else None
    if gap is None or not valid_duration or gap / duration > .05:
        reasons.append("final_completion_gap_exceeds_five_percent")
    if ratio is None or ratio > 1.8:
        reasons.append("maximum_jct_exceeds_1_8_times_median")
    return {
        "profile": "balanced_join", "accepted": not reasons, "reasons": reasons,
        "workflow_count": len(workflows), "joins": joins,
        "final_completion_gap_seconds": gap, "duration_seconds": duration,
        "maximum_to_median_jct": ratio,
        "criteria": {
            "workflow_count": 36, "minimum_joins": 80, "require_all_source_unguarded": True,
            "maximum_final_gap_fraction": .05, "maximum_to_median_jct": 1.8,
        },
        "scope": "Predeclared source selection only; no Full/native speedup is used.",
    }


def wait_for_resources(plan, state, path, interrupted):
    while True:
        fresh.check_hold(plan, released=True)
        if interrupted():
            raise ValueError("capture cancelled while waiting for the GPU")
        previous_alive = False
        if plan.get("after_comparison"):
            previous = load(Path(plan["after_comparison"]).with_suffix(".state.json"))
            identities = [
                *previous.get("owned", []),
                *(previous[name] for name in ("watcher", "server", "driver") if previous.get(name)),
            ]
            if not previous.get("watcher"):
                raise ValueError("previous comparison lacks its supervisor identity")
            previous_alive = any(reservation.alive(row) for row in identities)
        available = (
            {"ready": False, "reason": "previous_comparison_has_live_processes"}
            if previous_alive else qualified.resources(plan["port"])
        )
        if available["ready"]:
            return
        if not plan.get("wait_for_gpu"):
            raise ValueError("resources changed before capture")
        state.update(
            status="waiting_gpu_capture", resources=available,
            checked_at=reservation.now().isoformat(),
        )
        reservation.write_json(path, state)
        time.sleep(10.)


def register(args):
    for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    ):
        if path.exists():
            raise FileExistsError("preserve existing capture registration and artifacts")
    if not 1 <= args.port <= 65535:
        raise ValueError("invalid capture port")
    reference = load(args.reference)
    fresh.verify(reference)
    reference_complete(reference, load(args.reference.with_suffix(".state.json")))
    fresh.check_hold(reference, released=True)
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    if not {
        "scripts/run_causal_source_capture.py", "tests/test_causal_source_capture.py",
        "beliefkv/runtime/replay_token_capture.py",
        "beliefkv/experiments/causal_replay.py",
    } <= sources.keys():
        raise ValueError("capture execution was not CPU-qualified")
    fresh._require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("capture must freeze main")
    evidence = engine_evidence(args.engine, args.engine_report, args.engine_sources)
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    label = args.run_root.name.rsplit("_", 1)[-1]
    checkout = comparison.EXPERIMENTS / ".worktrees" / f"beliefkv-{label}-capture-{revision[:12]}"
    engine = comparison.EXPERIMENTS / ".worktrees" / f"sglang-{label}-capture-{evidence['tree'][:12]}"
    if checkout.exists() or engine.exists():
        raise FileExistsError("preserve existing frozen capture sources")
    if not args.wait_for_gpu and not qualified.resources(args.port)["ready"]:
        raise ValueError("GPU, port or disk is occupied")
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
                   check=True, capture_output=True)
    comparison.verify_cpu_snapshot(checkout, sources)
    base = qualified.command(["git", "-C", str(args.engine), "rev-parse", "HEAD"])
    subprocess.run(["git", "-C", str(args.engine), "worktree", "add", "--detach", str(engine), base],
                   check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(engine), "apply",
        str(checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
    ], check=True, capture_output=True)
    tree = git_source_state(
        engine, canonical_patch=checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if not tree["matches_worktree"] or tree["actual_tree"] != evidence["tree"]:
        raise ValueError("frozen engine differs from the tested capture engine")
    env = environment(reference, checkout, engine, args.run_root, args.port)
    extra = [
        args.reference, args.reference.with_suffix(".state.json"),
        args.cpu_report, args.cpu_sources, args.engine_report, args.engine_sources,
    ]
    if args.after_comparison:
        extra.append(args.after_comparison)
    plan = {
        "schema_version": 1, "purpose": "predeclared_native_exact_trajectory_capture",
        "registered_at": reservation.now().isoformat(),
        "reference_registration": str(args.reference.resolve()),
        "code_commit": revision, "development_checkout": str(ROOT),
        "execution_checkout": str(checkout), "engine_checkout": str(engine),
        "engine_tree": tree["actual_tree"], "source_sha256": sources,
        "passing_cpu_cases": count, "engine_evidence": evidence,
        "environment": env, "run_root": str(args.run_root.resolve()), "port": args.port,
        "hold_file": reference["hold_file"],
        "external_inputs": [
            *reference["external_inputs"],
            *({"path": str(path.resolve()), "sha256": sha256_file(path)} for path in extra),
        ],
        "performance_accepted": False, "gpu_replay_qualified": False,
        "source_profile": args.source_profile,
        "wait_for_gpu": args.wait_for_gpu,
        "after_comparison": str(args.after_comparison.resolve()) if args.after_comparison else None,
    }
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_causal_source_capture.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "purpose": plan["purpose"], "performance_accepted": False,
    }, indent=2))


def watch(registration: Path):
    plan = load(registration)
    path = registration.with_suffix(".state.json")
    if path.exists():
        raise FileExistsError("preserve the previous capture monitor outcome")
    run = Path(plan["run_root"])
    arm = run / "native_capture"
    state = {
        "status": "capture_preflight", "watcher": reservation.process(os.getpid()),
        "current_arm": "native_capture", "performance_accepted": False,
        "gpu_replay_qualified": False, "run_root": str(run),
    }
    driver = None
    interrupted = False

    def stop_requested(*_):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    with registration.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            state["checked_at"] = reservation.now().isoformat()
            reservation.write_json(path, state)
            comparison.verify_sources(plan)
            fresh.check_hold(plan, released=True)
            wait_for_resources(plan, state, path, lambda: interrupted)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
                 "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
                check=True, capture_output=True, timeout=30,
            )
            fresh.check_hold(plan, released=True)
            if interrupted or not qualified.resources(plan["port"])["ready"]:
                raise ValueError("capture cancelled or resources changed during preflight")
            qualified.machine_helper("start_v88_after_0600_20261020").clear_stale_socket(plan["port"])
            run.mkdir(parents=True, exist_ok=False)
            reservation.write_json(run / "capture_plan.json", plan)
            with (run / "native_capture.launch.log").open("x") as output:
                driver = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/run_qwen35_native_regime_probe.sh")],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True,
                    env=qualified.driver_environment(plan, os.environ),
                )
            state.update(status="recording_native", driver=reservation.process(driver.pid))
            while driver.poll() is None:
                state["checked_at"] = reservation.now().isoformat()
                reservation.write_json(path, state)
                fresh.check_hold(plan, released=True)
                if interrupted:
                    raise ValueError("capture monitor was stopped")
                observe(arm)
                time.sleep(15)
            state["original_exit_code"] = driver.wait()
            observe(arm, final=True)
            state["outcome"] = fresh.completed_arm(
                load(arm / "client_36/summary.json"), state["original_exit_code"], 36,
            )
            token_report, graph_report = audit_tokens(arm), audit_graph(arm)
            reservation.write_json(run / "token_evidence.json", token_report)
            reservation.write_json(run / "causal_evidence.json", graph_report)
            if not token_report["token_evidence_complete"] or not graph_report["causal_graph_compiled"]:
                raise ValueError("captured token or dependency evidence is incomplete")
            state.update(
                status="source_captured_gpu_replay_pending",
                source_requests=token_report["native_requests"],
            )
            if plan.get("source_profile") == "balanced_join":
                selection = balanced_source(load(arm / "client_36/summary.json"))
                reservation.write_json(run / "source_selection.json", selection)
                state["source_selection"] = selection
                if not selection["accepted"]:
                    state["status"] = "source_captured_selection_rejected"
        except Exception as error:
            if driver is not None and driver.poll() is None:
                identities = reservation.owned_runs().get(run, [])
                if not any(item["pid"] == state["driver"]["pid"] for item in identities):
                    identities.append(state["driver"])
                arm.mkdir(parents=True, exist_ok=True)
                reservation.write_json(arm / "server_reservation_cutoff.json", {
                    "reason": "causal_source_capture_interrupted",
                    "stop_started_at": reservation.now().isoformat(), "run": str(arm),
                    "observation_cutoff_monotonic_ms": time.monotonic() * 1000.,
                    "identities": identities, "collection_at_cutoff": {},
                    "administratively_interrupted": False, "errors": [],
                })
                reservation.stop_run(arm, identities, state)
                state["stopped_driver_exit_code"] = driver.wait(timeout=60)
            state.update(status="capture_failed", error=repr(error))
        finally:
            state["finished_at"] = reservation.now().isoformat()
            reservation.write_json(path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--cpu-sources", type=Path)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--engine-report", type=Path)
    parser.add_argument("--engine-sources", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--port", type=int, default=18546)
    parser.add_argument("--source-profile", choices=("balanced_join",))
    parser.add_argument("--wait-for-gpu", action="store_true")
    parser.add_argument("--after-comparison", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch.resolve())
    elif all((args.reference, args.cpu_report, args.cpu_sources, args.engine,
              args.engine_report, args.engine_sources, args.registration, args.run_root)):
        register(args)
    else:
        parser.error("register requires the reference, source checks and new output paths")


if __name__ == "__main__":
    main()
