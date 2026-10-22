#!/usr/bin/env python3
"""Queue frozen component ablations after V124b finishes and releases the GPU."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments import module_ablation as modules
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_frontier_ablation_after_campaign as full

fresh, comparison = full.fresh, full.comparison
qualified, reservation = full.qualified, full.reservation
load = fresh.load


def reference_ready(plan: dict, state: dict, *, alive=reservation.alive) -> bool:
    expected = full.stable_identity(plan["reference_watcher"])
    if (
        type(expected[0]) is not int or expected[0] <= 0
        or not isinstance(expected[1], str) or not expected[1].isdecimal()
        or full.stable_identity(state.get("watcher", {})) != expected
        or state.get("order") != ["full"] or state.get("error")
    ):
        raise ValueError("Full reference identity, order or outcome changed")
    status = state.get("status", "")
    if status == "collected_performance_review_pending":
        if state.get("completed_arms") != ["full"]:
            raise ValueError("Full collection is incomplete")
        return not any(
            item and alive(item) for item in (state.get("watcher"), state.get("driver"))
        )
    if status.startswith(("running_", "postprocessing_")):
        if not alive(state["watcher"]):
            raise ValueError("Full supervisor exited without final collection")
        return False
    raise ValueError(f"Full did not finish normally: {status}")


def environments(reference: dict, checkout: Path, port: int) -> dict[str, dict]:
    common = dict(reference["environment"])
    manifest = Path(common["WORKLOAD_MANIFEST"]).relative_to(reference["execution_checkout"])
    common.update(
        PYTHONPATH=str(checkout), WORKLOAD_MANIFEST=str(checkout / manifest),
        PORT=str(port), RUN_ROOT="", RESUME_PENDING="0", CUDA_VISIBLE_DEVICES="0",
    )
    return {
        label: {
            "environment": modules.environment(common, label),
            "runtime_overrides": modules.runtime_overrides(label),
        }
        for label in modules.ORDER
    }


def verify_reference(plan: dict) -> dict:
    reference = load(Path(plan["reference_registration"]))
    fresh.verify(reference)
    state = load(Path(plan["reference_state"]))
    if not reference_ready(plan, state):
        raise ValueError("Full processes have not exited")
    arm = Path(reference["run_root"]) / "full"
    fresh.observe("full", arm, final=True)
    outcome = fresh.completed_arm(load(arm / "client_36/summary.json"), state["original_exit_code"], 36)
    if outcome != state.get("arm_outcomes", {}).get("full"):
        raise ValueError("Full completion evidence changed")
    runtime = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if (
        runtime.get("execution_frontier_restore") is not False
        or runtime.get("tool_continuity") is not True
        or runtime.get("prefill_scheduling_policy") != "join_priority"
        or runtime.get("tool_restore_mode") != "execution"
    ):
        raise ValueError("Full controls differ from the module reference")
    paths = {
        Path(reference["run_root"]) / "timelines/full.json.gz",
        Path(reference["run_root"]) / "internal_comparison.json",
    }
    paths.update(
        path for path in arm.rglob("*")
        if path.is_file() and path.suffix in (".json", ".jsonl", ".csv")
    )
    return {
        "outcome": outcome,
        "sha256": {str(path): sha256_file(path) for path in sorted(paths)},
        "performance_accepted": False,
    }


def observe(plan: dict, label: str, *, final: bool = False) -> None:
    settings = plan["arms"][label]
    mode = settings["environment"]["BELIEFKV_INTERNAL_COMPARISON_MODE"]
    arm = Path(plan["run_root"]) / label
    fresh.observe(mode, arm, final=final, runtime_overrides=settings["runtime_overrides"])
    runtime = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if runtime is not None:
        modules.verify_activity(label, runtime)


def compare(plan: dict, completed: list[str]) -> None:
    if not completed:
        return
    run = Path(plan["run_root"])
    reference = load(Path(plan["reference_registration"]))
    base = Path(reference["run_root"])
    arguments = [
        sys.executable, str(ROOT / "scripts/compare_native_policy_runs.py"),
        "--arm", f"full={base / 'full'}",
        "--timeline", f"full={base / 'timelines/full.json.gz'}",
    ]
    for label in completed:
        arguments.extend((
            "--arm", f"{label}={run / label}",
            "--timeline", f"{label}={run / 'timelines' / (label + '.json.gz')}",
        ))
    subprocess.run(
        [*arguments, "--output", str(run / "module_comparison.json")],
        check=True, capture_output=True,
    )


def register(args) -> None:
    if not 1 <= args.port <= 65535:
        raise ValueError("invalid experiment port")
    if any(path.exists() for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    )):
        raise FileExistsError("preserve previous module registrations and results")
    reference = load(args.reference_registration)
    fresh.verify(reference)
    fresh.check_hold(reference, released=True)
    state_path = args.reference_registration.with_suffix(".state.json")
    state = load(state_path)
    reference_ready({"reference_watcher": state["watcher"]}, state)
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    if not {
        "beliefkv/experiments/module_ablation.py", "scripts/run_module_ablations_after_full.py",
        "tests/test_module_ablation.py",
    } <= sources.keys():
        raise ValueError("CPU qualification omits module execution")
    fresh._require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("freeze the qualified main branch")
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    label = args.run_root.name.rsplit("_", 1)[-1]
    checkout = comparison.EXPERIMENTS / ".worktrees" / f"beliefkv-{label}-frozen-{revision[:12]}-20261022"
    if checkout.exists():
        raise FileExistsError("preserve the frozen ablation checkout")
    arms = environments(reference, checkout, args.port)
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    link = checkout / "third_party/sglang-v0.5.20"
    if not link.exists():
        link.symlink_to(reference["engine_checkout"], target_is_directory=True)
    if link.resolve() != Path(reference["engine_checkout"]).resolve():
        raise ValueError("module ablations point to another engine")
    comparison.verify_cpu_snapshot(checkout, sources)
    inputs = {row["path"]: row["sha256"] for row in reference["external_inputs"]}
    for path in (
        args.reference_registration.resolve(), args.cpu_report.resolve(), args.cpu_sources.resolve(),
    ):
        inputs[str(path)] = sha256_file(path)
    plan = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        "development_checkout": str(ROOT), "code_commit": revision,
        "execution_checkout": str(checkout), "engine_checkout": reference["engine_checkout"],
        "engine_tree": reference["engine_tree"],
        **({"engine_cpu_qualification": reference["engine_cpu_qualification"]}
           if "engine_cpu_qualification" in reference else {}),
        "source_sha256": sources, "passing_cpu_cases": count,
        "cpu_report": str(args.cpu_report.resolve()),
        "external_inputs": [{"path": name, "sha256": digest} for name, digest in sorted(inputs.items())],
        "reference_registration": str(args.reference_registration.resolve()),
        "reference_state": str(state_path.resolve()), "reference_watcher": state["watcher"],
        "hold_file": reference["hold_file"], "gpu_not_before": reference["gpu_not_before"],
        "run_root": str(args.run_root.resolve()), "port": args.port,
        "order": list(modules.ORDER), "arms": arms, "performance_accepted": False,
        "comparison_scope": (
            "Development component ablations after V124b Full; same workload, seed, engine, "
            "capacity and default serving behavior. Latest code adds default-preserving module "
            "gates and operator validation. Full was frozen earlier; not an exact-code replay "
            "or a causal decomposition of dynamic trajectories. Keep complete JCT, guard "
            "eligibility and realized-work differences; removal effects do not add independently."
        ),
    }
    fresh.verify(plan)
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("xb") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_module_ablations_after_full.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "order": plan["order"], "waiting_for": str(args.reference_registration),
        "code_commit": revision, "performance_accepted": False,
    }, indent=2))


def wait_resources(plan: dict, state: dict, state_path: Path) -> None:
    consecutive = 0
    while True:
        fresh.check_hold(plan, released=True)
        fresh.verify(plan)
        available = qualified.resources(plan["port"])
        state.update(
            status="waiting_for_idle_resources", checked_at=reservation.now().isoformat(),
            resources=available,
        )
        consecutive = consecutive + 1 if available["ready"] else 0
        reservation.write_json(state_path, state)
        if consecutive >= 2:
            return
        time.sleep(15)


def watch(registration: Path) -> None:
    plan = load(registration)
    state_path, run = registration.with_suffix(".state.json"), Path(plan["run_root"])
    state = {
        "status": "waiting_full_reference", "watcher": reservation.process(os.getpid()),
        "registration": str(registration), "run_root": str(run), "order": plan["order"],
        "completed_arms": [], "original_exit_codes": {}, "arm_outcomes": {},
        "performance_accepted": False,
    }
    driver = None
    with registration.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            while True:
                fresh.check_hold(plan, released=True)
                state["checked_at"] = reservation.now().isoformat()
                reference = load(Path(plan["reference_state"]))
                if reference_ready(plan, reference):
                    state["reference_evidence"] = verify_reference(plan)
                    break
                reservation.write_json(state_path, state)
                time.sleep(15)
            for label in plan["order"]:
                state["current_arm"] = label
                wait_resources(plan, state, state_path)
                subprocess.run(
                    [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
                     "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
                    check=True, capture_output=True, timeout=30,
                )
                fresh.check_hold(plan, released=True)
                fresh.verify(plan)
                if not qualified.resources(plan["port"])["ready"]:
                    raise ValueError("resources changed before module launch")
                qualified.machine_helper("start_v88_after_0600_20261020").clear_stale_socket(plan["port"])
                if not run.exists():
                    run.mkdir(parents=True, exist_ok=False)
                    reservation.write_json(run / "module_plan.json", plan)
                environment = {
                    **plan["arms"][label]["environment"],
                    "RUN_ROOT": str(run / label), "CUDA_VISIBLE_DEVICES": "0",
                }
                with (run / f"{label}.launch.log").open("x") as output:
                    driver = subprocess.Popen(
                        ["bash", str(ROOT / "scripts/run_qwen35_native_regime_probe.sh")],
                        cwd=ROOT, env=qualified.driver_environment({"environment": environment}, os.environ),
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                state.update(
                    status="running_" + label, driver=reservation.process(driver.pid),
                    arm_started_at=reservation.now().isoformat(),
                )
                while driver.poll() is None:
                    state["checked_at"] = reservation.now().isoformat()
                    reservation.write_json(state_path, state)
                    fresh.check_hold(plan, released=True)
                    observe(plan, label)
                    time.sleep(15)
                exit_code = driver.wait()
                driver = None
                state["original_exit_codes"][label] = exit_code
                observe(plan, label, final=True)
                state["arm_outcomes"][label] = fresh.completed_arm(
                    load(run / label / "client_36/summary.json"), exit_code, 36,
                )
                state["status"] = "postprocessing_" + label
                reservation.write_json(state_path, state)
                comparison.postprocess(
                    plan, label, run,
                    runtime_mode=plan["arms"][label]["environment"]["BELIEFKV_INTERNAL_COMPARISON_MODE"],
                )
                state["completed_arms"].append(label)
                compare(plan, state["completed_arms"])
                reservation.write_json(state_path, state)
            if verify_reference(plan) != state["reference_evidence"]:
                raise ValueError("Full reference evidence changed during module collection")
            state.update(
                status="collected_performance_review_pending", finished_at=reservation.now().isoformat(),
            )
        except Exception as error:
            if driver is not None and driver.poll() is None:
                fresh.stop_arm(plan, state, f"module_ablation: {error}")
                state["stopped_driver_exit_code"] = driver.wait(timeout=60)
            state.update(
                status="cancelled_hold_changed" if isinstance(error, fresh.HoldChanged)
                else "stopped_for_investigation",
                error=repr(error), finished_at=reservation.now().isoformat(),
            )
        finally:
            reservation.write_json(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--reference-registration", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--cpu-sources", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--port", type=int, default=18532)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        if any(getattr(args, name) is None for name in (
            "reference_registration", "cpu_report", "cpu_sources", "registration", "run_root",
        )):
            parser.error("registration requires Full, CPU reports and new output paths")
        register(args)


if __name__ == "__main__":
    main()
