#!/usr/bin/env python3
"""Freeze and run internal policy controls after a completed comparison."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import (
    ORDER, SCHEDULING_ONLY, arm_environment, validate_runtime,
)
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import launch_qualified_after_hold as qualified
from scripts.launch_semantic_h2d_after_pair import _require_clean, validate_completed_pair

EXPERIMENTS = ROOT.parent.parent if ROOT.parent.name == ".worktrees" else ROOT.parent
qualified.OPS = EXPERIMENTS / "ops"
qualified.HOLD = qualified.OPS / "gpu_hold_until_20261021_0600.json"
reservation = qualified.machine_helper("server_reservation")
REPORT_ONLY_SOURCES = frozenset(("scripts/render_native_policy_overview_zh.py",))


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def previous_ready(state: dict, *, alive=reservation.alive) -> bool:
    if state["status"] == "completed":
        if state.get("exit_code") != 0:
            raise ValueError("previous comparison completed with a failure")
        return not any(alive(state[name]) for name in ("watcher", "driver"))
    if state["status"].startswith(("running_", "waiting_")):
        if not alive(state["watcher"]):
            raise ValueError("previous comparison supervisor has exited without completion")
        return False
    raise ValueError(f"previous comparison did not finish normally: {state['status']}")


def cancellation() -> str | None:
    if qualified.PAUSE.exists():
        return "new_user_pause"
    hold = load(qualified.HOLD)
    if not hold.get("automatic_resume_allowed"):
        return "automatic_resume_disabled"
    if reservation.now() < datetime.fromisoformat(hold["gpu_not_before"]):
        return "new_gpu_hold"
    return None


def verify_sources(plan: dict) -> None:
    checkout = Path(plan["execution_checkout"])
    _require_clean(checkout)
    for path, digest in plan["source_sha256"].items():
        if sha256_file(checkout / path) != digest:
            raise ValueError(f"frozen source changed: {path}")
    for row in plan["external_inputs"]:
        if sha256_file(Path(row["path"])) != row["sha256"]:
            raise ValueError(f"frozen input changed: {row['path']}")
    engine = git_source_state(
        Path(plan["engine_checkout"]),
        canonical_patch=checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if not engine["matches_worktree"] or engine["actual_tree"] != plan["engine_tree"]:
        raise ValueError("frozen native engine changed")


def qualify_report(path: Path, source_path: Path) -> int:
    report = ET.parse(path).getroot()
    cases = list(report.iter("testcase"))
    if not cases or any(list(report.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError("internal comparison CPU checks must all pass")
    sources = load(source_path)
    for name, digest in sources.items():
        if name not in REPORT_ONLY_SOURCES and sha256_file(ROOT / name) != digest:
            raise ValueError(f"CPU-qualified source changed: {name}")
    required = {
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/experiments/internal_comparison.py",
        "scripts/run_internal_comparison.py",
        "scripts/run_qwen35_native_regime_probe.sh",
        "tests/test_internal_comparison.py",
    }
    if not required <= sources.keys():
        raise ValueError("CPU qualification omits comparison execution sources")
    return len(cases)


def verify_cpu_snapshot(checkout: Path, sources: dict[str, str]) -> None:
    for name, digest in sources.items():
        # This standalone overview is not invoked by collection or its audits.
        if name not in REPORT_ONLY_SOURCES and sha256_file(checkout / name) != digest:
            raise ValueError(f"frozen source differs from CPU qualification: {name}")


def qualify_sources(args) -> None:
    report = ET.parse(args.cpu_report).getroot()
    cases = list(report.iter("testcase"))
    if not cases or any(list(report.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError("CPU qualification contains a failed or skipped case")
    previous = ET.parse(args.previous_cpu_report).getroot()
    identities = {(row.get("classname"), row.get("name")) for row in cases}
    retained = {(row.get("classname"), row.get("name")) for row in previous.iter("testcase")}
    if len(identities) != len(cases) or not retained <= identities:
        raise ValueError("CPU qualification must retain the previous complete case set")
    if not any(row.get("classname") == "tests.test_internal_comparison" for row in cases):
        raise ValueError("internal controls were not tested")
    paths = qualified.command([
        "git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard",
        "--", "beliefkv", "scripts", "configs", "patches", "tests",
    ]).splitlines()
    reservation.write_json(args.cpu_sources, {
        path: sha256_file(ROOT / path) for path in sorted(set(paths))
    })
    print(json.dumps({
        "passing_cases": len(cases), "retained_cases": len(retained),
        "additional_cases": len(identities - retained), "cpu_sources": str(args.cpu_sources),
        "gpu_performance_accepted": False,
    }, indent=2))


def register(args) -> None:
    if any(path.exists() for path in (
        args.registration, args.registration.with_suffix(".state.json"), args.run_root,
        args.registration.with_suffix(".log"),
    )):
        raise FileExistsError("preserve existing comparison registration or run")
    if cancellation():
        raise ValueError("a user hold prevents registration")
    count = qualify_report(args.cpu_report, args.cpu_sources)
    previous = load(args.after_state)
    if previous["status"] not in ("running_native", "completed"):
        raise ValueError("register this internal matrix behind the current native comparison")
    candidate = previous["candidate"]
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    run_label = args.run_root.name.rsplit("_", 1)[-1]
    checkout = ROOT.parent / f".worktrees/beliefkv-internal-{run_label}-frozen-{revision[:12]}-20261021"
    if checkout.exists():
        raise FileExistsError("preserve the existing frozen internal controls")
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    engine = Path(candidate["engine_checkout"])
    link = checkout / "third_party/sglang-v0.5.20"
    if not link.exists():
        link.symlink_to(engine, target_is_directory=True)
    if link.resolve() != engine.resolve():
        raise ValueError("frozen checkout has a different native engine")
    verify_cpu_snapshot(checkout, load(args.cpu_sources))
    paths = qualified.command([
        "git", "-C", str(checkout), "ls-files", "--", "beliefkv", "scripts", "configs", "patches",
    ]).splitlines()
    environment = {
        **candidate["environment"], "PYTHONPATH": str(checkout),
        "WORKLOAD_MANIFEST": str(checkout / "configs/workloads/locobench_agent_long_36root_20261020.json"),
        "SGLANG_SOURCE_CHECKOUT": str(engine), "RESUME_PENDING": "0",
        "PORT": str(args.port),
    }
    if any(environment[name] != value for name, value in {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "12",
        "SAMPLING_SEED": "21", "SERVER_RANDOM_SEED": "21",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
        "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.95", "HOST_SPLIT": "80:20",
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "ARRIVAL_RATE_PER_SECOND": "0.0",
    }.items()):
        raise ValueError("internal comparisons must keep the current workload and capacity")
    inputs = {args.cpu_report, args.cpu_sources}
    inputs.update(Path(row["path"]) for row in candidate["external_inputs"]
                  if not Path(row["path"]).is_relative_to(ROOT)
                  and not Path(row["path"]).is_relative_to(ROOT.parent / "ops"))
    # Keep artifacts actually used by this matrix, not changing live state files.
    inputs.update(Path(environment[name]) for name in (
        "H2D_SEED_ARTIFACT", "TOOL_TIMING_ARTIFACT", "TRANSFER_SERVICE_SEED",
    ))
    inputs.update((
        Path(reservation.__file__),
        Path(qualified.machine_helper("start_v88_after_0600_20261020").__file__),
    ))
    plan = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        "code_commit": revision, "reference_code_commit": candidate["code_commit"],
        "execution_checkout": str(checkout),
        "engine_checkout": str(engine), "engine_tree": candidate["engine"]["actual_tree"],
        "after_state": str(args.after_state), "previous_run": previous["run_root"],
        "run_root": str(args.run_root), "port": args.port, "order": list(ORDER),
        "full_reference_arm": str(Path(previous["run_root"]) / "predictive_h2d"),
        "full_reference_timeline": str(Path(previous["run_root"]) / "timelines/predictive_h2d.json.gz"),
        "full_reference_is_rerun": False,
        "cpu_report": str(args.cpu_report), "passing_cpu_cases": count,
        "source_sha256": {path: sha256_file(checkout / path) for path in paths},
        "external_inputs": [
            {"path": str(path), "sha256": sha256_file(path)} for path in sorted(inputs)
        ],
        "environment": environment,
        "policy_configuration": {
            mode: arm_environment({
                "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION": environment.get(
                    "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION",
                    environment.get("PREFETCH_RESIDENCY_PROTECTION", "1"),
                ),
                "BELIEFKV_PREFILL_SCHEDULING_POLICY": environment.get(
                    "BELIEFKV_PREFILL_SCHEDULING_POLICY",
                    environment.get("PREFILL_SCHEDULING_POLICY", "workflow"),
                ),
            }, mode) for mode in ORDER
        },
        "scope": (
            "User-authorized development comparison: reuse V109 Full without rerunning "
            "it; run four controls on latest qualified main including newer CPU "
            "optimizations. Record the application-version difference, not a strict "
            "same-version causal ablation. Match data plane, frontend cache, tasks, "
            "prompt, capacity, arrivals and seeds. Dynamic work can differ; retain "
            "untouched V109 native separately."
        ),
        "performance_accepted": False,
    }
    verify_sources(plan)
    reservation.write_json(args.registration, plan)
    log = args.registration.with_suffix(".log")
    with log.open("xb") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_internal_comparison.py"),
             "--watch", str(args.registration)],
            cwd=checkout, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registered": str(args.registration), "watcher": reservation.process(watcher.pid),
        "state": str(args.registration.with_suffix(".state.json")),
        "order": list(ORDER), "starts_only_after_current_pair_finishes": True,
    }, indent=2))


def observe_arm(mode: str, arm: Path, *, max_running_requests: int = 12) -> None:
    protection = None
    scheduling = None
    tool_restore, idle_prepare, restore_tiers = "speculative", None, None
    last_child = None
    frontier = None
    continuity = None
    info = arm / "server/server_info.json"
    if info.exists():
        observed = load(info)
        expected = {
            "max_running_requests": max_running_requests,
            "random_seed": 21, "context_length": 262144,
            "enable_beliefkv_admission": mode != "native_matched",
        }
        for field, value in expected.items():
            if observed.get(field) != value:
                raise ValueError(f"{mode}: actual server {field} differs")
    launch = arm / "server/launch_configuration.json"
    if launch.exists():
        observed = load(launch)
        if observed.get("chat_token_block_cache") is not True or observed.get("internal_comparison_mode") != mode:
            raise ValueError(f"{mode}: actual frontend/comparison configuration differs")
        protection = observed.get("prefetch_residency_protection_requested")
        scheduling = observed.get("prefill_scheduling_policy_requested")
        tool_restore = observed.get("tool_restore_mode_requested", "speculative")
        idle_prepare = observed.get("idle_prepare_policy_requested")
        restore_tiers = observed.get("restore_tiers_requested")
        last_child = observed.get("join_last_child_priority_requested")
        frontier = observed.get("execution_frontier_restore_requested")
        continuity = observed.get("tool_continuity_requested")
    runtime = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if runtime is not None or mode == "native_matched":
        validate_runtime(
            mode, runtime or {}, residency_protection=protection,
            scheduling_policy=scheduling,
            tool_restore_mode=tool_restore, idle_prepare_policy=idle_prepare,
            restore_tiers=restore_tiers,
            join_last_child_priority=last_child,
            execution_frontier_restore=frontier,
            tool_continuity=continuity,
        )
    telemetry = arm / "server/native_telemetry_status.json"
    if telemetry.exists() and any(load(telemetry).get(name) for name in (
        "writer_error", "dropped_records", "failed_records",
    )):
        raise ValueError(f"{mode}: telemetry failed")
    if mode in SCHEDULING_ONLY:
        for path in (arm / "server").glob("physical_action_*.jsonl"):
            if path.stat().st_size:
                raise ValueError(f"{mode}: custom physical-action telemetry is not empty")


def postprocess(plan: dict, mode: str, run: Path) -> None:
    arm = run / mode
    script_root = Path(plan["execution_checkout"]) / "scripts"
    for script, output in (
        ("audit_native_h2d_sources.py", "native_h2d_sources.json"),
        ("audit_prefetch_lifecycle.py", "prefetch_lifecycle.json"),
    ):
        subprocess.run(
            [sys.executable, str(script_root / script), "--arm", str(arm),
             "--output", str(arm / output)], check=True, capture_output=True,
        )
    if mode in ("full", "kv_only"):
        subprocess.run(
            [sys.executable, str(script_root / "audit_prepare_background.py"),
             "--arm", str(arm), "--lifecycle", str(arm / "prefetch_lifecycle.json"),
             "--output", str(arm / "prepare_background.json")],
            check=True, capture_output=True,
        )
    for kind in ("join", "tool"):
        subprocess.run(
            [sys.executable, str(script_root / f"audit_{kind}_reentry_service.py"),
             "--arm", f"{mode}={arm}", "--output", str(arm / f"{kind}_reentry_service.json")],
            check=True, capture_output=True,
        )
    with (run / f"{mode}.timeline_export.json").open("x") as output:
        subprocess.run(
            [sys.executable, str(script_root / "render_p6_execution_timeline.py"),
             "--run-dir", str(arm), "--arm", mode,
             "--output-html", str(run / "timelines" / f"{mode}.html")],
            check=True, stdout=output,
        )
    with (run / f"{mode}.workspace_cleanup.json").open("x") as output:
        subprocess.run(
            [sys.executable, str(script_root / "summarize_semantic_h2d_ab.py"),
             "--cleanup-arm", str(arm)], check=True, stdout=output,
        )


def watch(registration: Path) -> None:
    plan = load(registration)
    state_path = registration.with_suffix(".state.json")
    run = Path(plan["run_root"])
    state = {
        "status": "waiting_previous_comparison", "watcher": reservation.process(os.getpid()),
        "registration": str(registration), "run_root": str(run), "order": plan["order"],
        "completed_arms": [], "performance_accepted": False,
    }
    driver = None
    with registration.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            while True:
                state["checked_at"] = reservation.now().isoformat()
                if reason := cancellation():
                    state.update(status="cancelled", reason=reason)
                    reservation.write_json(state_path, state)
                    return
                previous = load(Path(plan["after_state"]))
                reservation.write_json(state_path, state)
                if previous_ready(previous):
                    previous_run = Path(plan["previous_run"])
                    validate_completed_pair(previous_run, load(previous_run / "ab_plan.json"))
                    break
                time.sleep(15)
            consecutive = 0
            while consecutive < 2:
                if reason := cancellation():
                    raise ValueError(reason)
                available = qualified.resources(plan["port"])
                state.update(status="waiting_idle_gpu", resources=available,
                             checked_at=reservation.now().isoformat())
                reservation.write_json(state_path, state)
                consecutive = consecutive + 1 if available["ready"] else 0
                if consecutive < 2:
                    time.sleep(15)
            verify_sources(plan)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
                 "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
                check=True, capture_output=True, timeout=30,
            )
            if cancellation() or not qualified.resources(plan["port"])["ready"]:
                raise ValueError("hold or resources changed before internal comparison")
            run.mkdir(parents=True, exist_ok=False)
            reservation.write_json(run / "internal_plan.json", plan)
            for mode in plan["order"]:
                verify_sources(plan)
                if reason := cancellation():
                    raise ValueError(reason)
                if not qualified.resources(plan["port"])["ready"]:
                    raise ValueError("GPU or port occupied before next internal arm")
                qualified.machine_helper("start_v88_after_0600_20261020").clear_stale_socket(plan["port"])
                arm = run / mode
                environment = arm_environment(plan["environment"], mode)
                environment.update(RUN_ROOT=str(arm), CUDA_VISIBLE_DEVICES="0")
                with (run / f"{mode}.launch.log").open("x") as output:
                    driver = subprocess.Popen(
                        ["bash", str(ROOT / "scripts/run_qwen35_native_regime_probe.sh")],
                        cwd=ROOT, env=qualified.driver_environment({"environment": environment}, os.environ),
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                state.update(status=f"running_{mode}", driver=reservation.process(driver.pid),
                             current_arm=mode, started_at=reservation.now().isoformat())
                while driver.poll() is None:
                    reservation.write_json(state_path, state)
                    if reason := cancellation():
                        raise ValueError(reason)
                    observe_arm(mode, arm)
                    state["checked_at"] = reservation.now().isoformat()
                    time.sleep(15)
                if driver.wait() != 0:
                    raise ValueError(f"{mode}: workload process failed")
                observe_arm(mode, arm)
                summary = load(arm / "client_36/summary.json")
                if summary["workflow_count"] != 36:
                    raise ValueError(f"{mode}: incomplete arrival table")
                if mode != "native_matched" and qualified.last_runtime_state(
                    arm / "opportunities/admission_opportunities.jsonl",
                ) is None:
                    raise ValueError(f"{mode}: no effective runtime configuration evidence")
                state.update(status=f"postprocessing_{mode}")
                reservation.write_json(state_path, state)
                postprocess(plan, mode, run)
                state["completed_arms"].append(mode)
                driver = None
            arguments = [sys.executable, str(ROOT / "scripts/compare_native_policy_runs.py")]
            arguments.extend((
                "--arm", "full=" + plan["full_reference_arm"],
                "--timeline", "full=" + plan["full_reference_timeline"],
            ))
            for mode in plan["order"]:
                arguments.extend(("--arm", f"{mode}={run / mode}", "--timeline",
                                  f"{mode}={run / 'timelines' / (mode + '.json.gz')}"))
            subprocess.run(
                [*arguments, "--output", str(run / "internal_comparison.json")],
                check=True, capture_output=True,
            )
            state.update(status="completed", finished_at=reservation.now().isoformat())
        except Exception as error:
            if driver is not None and driver.poll() is None:
                qualified.RUN = run / state["current_arm"]
                qualified.stop_owned(state, f"internal_comparison: {error}")
                driver.wait(timeout=60)
            state.update(status="stopped_for_investigation", error=repr(error),
                         finished_at=reservation.now().isoformat())
        finally:
            reservation.write_json(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--qualify-cpu-sources", action="store_true")
    parser.add_argument("--previous-cpu-report", type=Path)
    parser.add_argument("--after-state", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--cpu-sources", type=Path)
    parser.add_argument("--port", type=int, default=18520)
    args = parser.parse_args()
    if args.qualify_cpu_sources:
        if not args.cpu_report or not args.cpu_sources or not args.previous_cpu_report:
            parser.error("CPU qualification requires reports, previous report and output source map")
        qualify_sources(args)
    elif args.watch:
        watch(args.watch)
    else:
        if any(getattr(args, name) is None for name in (
            "after_state", "registration", "run_root", "cpu_report", "cpu_sources",
        )):
            parser.error("registration requires previous state, run path and CPU qualification")
        register(args)


if __name__ == "__main__":
    main()
