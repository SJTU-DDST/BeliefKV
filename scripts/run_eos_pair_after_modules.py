#!/usr/bin/env python3
"""Continue the selected seeded V108/EOS ablations, then run Full and native."""

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

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.metrics.workload_compatibility import compare_workload
from scripts import run_module_ablations_after_full as modules

fresh, comparison = modules.fresh, modules.comparison
qualified, reservation, load = modules.qualified, modules.reservation, modules.load
PAIR_ORDER = ("full", "native_matched")
REMAINING_MODULES = (
    "no_join_prefetch", "no_background_prepare", "no_tool_duration_model", "no_child_reclaim",
)
ORDER = (*REMAINING_MODULES, *PAIR_ORDER)


def overrides(label: str) -> dict:
    if label == "no_join_prefetch":
        return {"final_stage_prefetch": False}
    if label == "no_background_prepare":
        return {"background_prepare": False}
    if label == "no_tool_duration_model":
        return {"tool_prefetch": False}
    if label == "no_child_reclaim":
        return {"terminal_child_reclaim": False}
    return {}


def reference_ready(plan: dict, state: dict, *, alive=reservation.alive) -> bool:
    expected = plan["reference_watcher"]
    if (
        type(expected.get("pid")) is not int or expected["pid"] <= 0
        or not isinstance(expected.get("start_ticks"), str)
        or not expected["start_ticks"].isdecimal()
        or
        any(state.get("watcher", {}).get(name) != expected.get(name)
            for name in ("pid", "start_ticks"))
        or state.get("order") != plan["reference_order"]
    ):
        raise ValueError("reference matrix identity, order or outcome changed")
    if plan.get("reference_cancel_record"):
        if state.get("status") == "stopped_for_investigation":
            if (
                state.get("completed_arms") != ["schedule_only", "kv_only"]
                or state.get("current_arm") != "no_tool_continuity"
                or state.get("original_exit_codes", {}).get("no_tool_continuity") not in (143, -15, 137, -9)
            ):
                raise ValueError("administrative cancellation did not preserve expected arm exits")
            return not any(identity and alive(identity)
                           for identity in (state.get("watcher"), state.get("driver")))
    if state.get("error"):
        raise ValueError("reference matrix failed without the expected administrative stop")
    if state.get("status") == "collected_performance_review_pending":
        if state.get("completed_arms") != plan["reference_order"]:
            raise ValueError("reference matrix did not collect all arms")
        return not any(identity and alive(identity)
                       for identity in (state.get("watcher"), state.get("driver")))
    if state.get("status", "").startswith(("running_", "postprocessing_", "waiting_")):
        if not alive(state["watcher"]):
            raise ValueError("reference matrix exited before completing")
        return False
    raise ValueError("reference matrix did not finish normally")


def environments(reference: dict, spec: dict, checkout: Path, port: int) -> dict:
    if spec.get("policy_profile") != "v108_eos":
        raise ValueError("pair requires the latest V108/EOS policy")
    common = fresh.campaign_environment(
        reference["arms"]["no_tool_continuity"]["environment"], spec,
        checkout, Path(reference["engine_checkout"]), port,
    )
    common["PYTHONHASHSEED"] = "21"
    result = {mode: arm_environment(common, mode) for mode in PAIR_ORDER}
    for label in REMAINING_MODULES:
        environment = arm_environment(common, "full")
        if label == "no_join_prefetch":
            environment["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] = "0"
        elif label == "no_background_prepare":
            environment["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] = "0"
        elif label == "no_tool_duration_model":
            environment.update(
                ENABLE_TOOL_TIMING="0", ONLINE_TOOL_TIMING="0",
                BELIEFKV_ONLINE_TOOL_TIMING="0", BELIEFKV_TOOL_TIMING_ARTIFACT="",
                BELIEFKV_TOOL_TIMING_SHA256="", BELIEFKV_ENABLE_TOOL_PREFETCH="0",
            )
        elif label == "no_child_reclaim":
            environment["BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM"] = "0"
        result[label] = environment
    return {label: result[label] for label in ORDER}


def verify_completed_reference(plan: dict, state: dict) -> dict:
    reference = load(Path(plan["reference_registration"]))
    fresh.verify(reference)
    if not reference_ready(plan, state):
        raise ValueError("reference matrix is still active")
    root = Path(reference["run_root"])
    outcomes = {}
    for label in state["completed_arms"]:
        arm = root / label
        outcomes[label] = fresh.completed_arm(
            load(arm / "client_36/summary.json"), state["original_exit_codes"][label], 36,
        )
        if outcomes[label] != state["arm_outcomes"][label]:
            raise ValueError("reference completion evidence changed")
    report = root / "module_comparison.json"
    return {"outcomes": outcomes, "comparison": str(report), "sha256": sha256_file(report)}


def verify_seeds(arm: Path, *, final: bool = False) -> None:
    launch = arm / "server/launch_configuration.json"
    if launch.is_file():
        row = load(launch)
        if row.get("server_random_seed") != 21 or row.get("python_hash_seed") != "21":
            raise ValueError("server seed or process hash seed drifted")
    elif final:
        raise ValueError("server seed evidence is missing")
    manifest = arm / "client_36/manifest.json"
    if manifest.is_file():
        row = load(manifest)
        randomness = row.get("randomness", {})
        if (
            row["config"].get("sampling_seed") != 21
            or randomness.get("seed") != 21
            or randomness.get("python_hash_seed") != "21"
            or randomness.get("configured") is not True
            or randomness.get("cuda_initialized") is not False
            or any(randomness.get(name) != 21
                   for name in ("python_random", "numpy_legacy_random", "torch_cpu"))
        ):
            raise ValueError("client RNG initialization or hash seed drifted")
    elif final:
        raise ValueError("client seed evidence is missing")


def observe(plan: dict, label: str, *, final: bool = False) -> None:
    arm = Path(plan["run_root"]) / label
    mode = plan["arms"][label]["BELIEFKV_INTERNAL_COMPARISON_MODE"]
    fresh.observe(mode, arm, final=final, runtime_overrides=overrides(label))
    verify_seeds(arm, final=final)
    if label in REMAINING_MODULES:
        runtime = qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        if runtime is not None:
            modules.modules.verify_activity(label, runtime)


def register(args) -> None:
    if not 1 <= args.port <= 65535:
        raise ValueError("invalid experiment port")
    if any(path.exists() for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    )):
        raise FileExistsError("preserve existing pair registration and results")
    reference = load(args.reference_registration)
    fresh.verify(reference)
    fresh.check_hold(reference, released=True)
    state_path = args.reference_registration.with_suffix(".state.json")
    state = load(state_path)
    cancel_record = load(args.cancel_record)
    if (
        cancel_record.get("registration") != str(args.reference_registration.resolve())
        or cancel_record.get("cancelled_active_arm") != "no_tool_continuity"
        or cancel_record.get("remaining_modules") != list(REMAINING_MODULES)
    ):
        raise ValueError("administrative cancellation belongs to another experiment")
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    required = {
        "scripts/run_eos_pair_after_modules.py", "tests/test_eos_pair_after_modules.py",
        "beliefkv/runtime/experiment_randomness.py", "tests/test_experiment_randomness.py",
        "scripts/audit_paired_realized_work.py", "tests/test_paired_realized_work.py",
        str(args.spec.resolve().relative_to(ROOT)),
    }
    if not required <= sources.keys():
        raise ValueError("CPU qualification omits the pair or seed controls")
    fresh._require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("freeze the qualified main branch")
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    label = args.run_root.name.rsplit("_", 1)[-1]
    checkout = comparison.EXPERIMENTS / ".worktrees" / (
        f"beliefkv-{label}-eos-pair-frozen-{revision[:12]}-20261022"
    )
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    link = checkout / "third_party/sglang-v0.5.20"
    if not link.exists():
        link.symlink_to(reference["engine_checkout"], target_is_directory=True)
    if link.resolve() != Path(reference["engine_checkout"]).resolve():
        raise ValueError("pair points to another engine")
    spec_path = checkout / args.spec.resolve().relative_to(ROOT)
    spec = load(spec_path)
    inputs = {row["path"]: row["sha256"] for row in reference["external_inputs"]}
    for path in (
        args.reference_registration.resolve(), args.cpu_report.resolve(),
        args.cpu_sources.resolve(), spec_path,
        args.cancel_record.resolve(),
    ):
        inputs[str(path)] = sha256_file(path)
    plan = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        "development_checkout": str(ROOT), "code_commit": revision,
        "execution_checkout": str(checkout), "engine_checkout": reference["engine_checkout"],
        "engine_tree": reference["engine_tree"],
        "source_sha256": sources, "passing_cpu_cases": count,
        "cpu_report": str(args.cpu_report.resolve()),
        "external_inputs": [{"path": name, "sha256": digest} for name, digest in sorted(inputs.items())],
        "reference_registration": str(args.reference_registration.resolve()),
        "reference_state": str(state_path.resolve()), "reference_watcher": state["watcher"],
        "reference_order": reference["order"],
        "reference_cancel_record": str(args.cancel_record.resolve()),
        "hold_file": reference["hold_file"], "gpu_not_before": reference["gpu_not_before"],
        "run_root": str(args.run_root.resolve()), "port": args.port, "order": list(ORDER),
        "arms": environments(reference, spec, checkout, args.port),
        "performance_accepted": False, "rerun_on_workload_mismatch": False,
        "comparison_scope": (
            "Four remaining seeded V108/EOS ablations followed by one Full/native pair. "
            "Keep complete JCT, per-workflow work, original exits and guard quality. "
            "Fixed seeds do not guarantee equal live agent trajectories."
        ),
    }
    if "engine_cpu_qualification" in reference:
        plan["engine_cpu_qualification"] = reference["engine_cpu_qualification"]
    reference_ready(plan, state)
    fresh.verify(plan)
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("xb") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_eos_pair_after_modules.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "order": plan["order"], "code_commit": revision, "performance_accepted": False,
    }, indent=2))


def watch(registration: Path) -> None:
    plan = load(registration)
    path, run = registration.with_suffix(".state.json"), Path(plan["run_root"])
    state = {
        "status": "waiting_previous_supervisor_exit", "watcher": reservation.process(os.getpid()),
        "completed_arms": [], "original_exit_codes": {}, "arm_outcomes": {},
        "registration": str(registration), "run_root": str(run),
        "order": plan["order"], "performance_accepted": False,
    }
    driver = None
    with registration.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            while True:
                fresh.check_hold(plan, released=True)
                fresh.verify(plan)
                state["checked_at"] = reservation.now().isoformat()
                previous = load(Path(plan["reference_state"]))
                reservation.write_json(path, state)
                if reference_ready(plan, previous):
                    state["reference_evidence"] = verify_completed_reference(plan, previous)
                    state["main_head_at_launch_check"] = modules.full.verify_candidate(plan)
                    break
                time.sleep(15)
            for label in plan["order"]:
                mode = plan["arms"][label]["BELIEFKV_INTERNAL_COMPARISON_MODE"]
                state["current_arm"] = label
                modules.wait_resources(plan, state, path)
                subprocess.run(
                    [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
                     "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
                    check=True, capture_output=True, timeout=30,
                )
                fresh.check_hold(plan, released=True)
                fresh.verify(plan)
                if not qualified.resources(plan["port"])["ready"]:
                    raise ValueError("resources changed before paired launch")
                qualified.machine_helper("start_v88_after_0600_20261020").clear_stale_socket(plan["port"])
                if not run.exists():
                    run.mkdir(parents=True, exist_ok=False)
                    reservation.write_json(run / "pair_plan.json", plan)
                environment = {
                    **plan["arms"][label], "RUN_ROOT": str(run / label), "CUDA_VISIBLE_DEVICES": "0",
                }
                with (run / f"{label}.launch.log").open("x") as output:
                    driver = subprocess.Popen(
                        ["bash", str(ROOT / "scripts/run_qwen35_native_regime_probe.sh")],
                        cwd=ROOT, env=qualified.driver_environment({"environment": environment}, os.environ),
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                state.update(status="running_" + label, driver=reservation.process(driver.pid))
                state["arm_started_at"] = reservation.now().isoformat()
                while driver.poll() is None:
                    state["checked_at"] = reservation.now().isoformat()
                    reservation.write_json(path, state)
                    fresh.check_hold(plan, released=True)
                    observe(plan, label)
                    time.sleep(15)
                code = driver.wait()
                driver = None
                state["original_exit_codes"][label] = code
                observe(plan, label, final=True)
                state["arm_outcomes"][label] = fresh.completed_arm(
                    load(run / label / "client_36/summary.json"), code, 36,
                )
                state["status"] = "postprocessing_" + label
                reservation.write_json(path, state)
                comparison.postprocess(plan, label, run, runtime_mode=mode)
                state["completed_arms"].append(label)
                reservation.write_json(path, state)
            arguments = [sys.executable, str(ROOT / "scripts/compare_native_policy_runs.py")]
            for mode in plan["order"]:
                arguments.extend((
                    "--arm", f"{mode}={run / mode}",
                    "--timeline", f"{mode}={run / 'timelines' / (mode + '.json.gz')}",
                ))
            subprocess.run(
                [*arguments, "--output", str(run / "pair_comparison.json")],
                check=True, capture_output=True,
            )
            arm_arguments = [
                item for mode in plan["order"] for item in ("--arm", f"{mode}={run / mode}")
            ]
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/compare_native_policy_runs.py"),
                 *arm_arguments, "--busy-window", "600:1800",
                 "--output", str(run / "pair_busy_window.json")],
                check=True, capture_output=True,
            )
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/audit_paired_realized_work.py"),
                 *arm_arguments, "--output", str(run / "paired_realized_work.json")],
                check=True, capture_output=True,
            )
            metrics = load(run / "pair_comparison.json")["runs"]
            state["workload_compatibility"] = compare_workload(metrics["full"], metrics["native_matched"])
            state.update(
                status="collected_performance_review_pending", finished_at=reservation.now().isoformat(),
            )
        except Exception as error:
            if driver is not None and driver.poll() is None:
                fresh.stop_arm(plan, state, f"eos_pair: {error}")
                state["stopped_driver_exit_code"] = driver.wait(timeout=60)
            state.update(
                status="stopped_for_investigation", error=repr(error),
                finished_at=reservation.now().isoformat(),
            )
        finally:
            reservation.write_json(path, state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--reference-registration", type=Path)
    parser.add_argument("--cancel-record", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--cpu-sources", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--port", type=int, default=18538)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        if any(value is None for name, value in vars(args).items() if name not in ("watch", "port")):
            parser.error("pair registration requires reference, policy, CPU qualification and new paths")
        register(args)


if __name__ == "__main__":
    main()
