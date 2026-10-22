#!/usr/bin/env python3
"""Run only Full/no-frontier after a complete frozen Full/JOIN/native campaign."""

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
from scripts import run_fresh_internal_comparison as fresh

comparison = fresh.comparison
qualified = fresh.qualified
reservation = fresh.reservation
load = fresh.load
REFERENCE_ORDER = ("full", "join_priority_only", "native_matched")


def stable_identity(identity: dict) -> tuple:
    return identity.get("pid"), identity.get("start_ticks")


def reference_ready(plan: dict, state: dict, *, alive=reservation.alive) -> bool:
    pid, ticks = stable_identity(plan["reference_watcher"])
    if type(pid) is not int or pid <= 0 or not isinstance(ticks, str) or not ticks.isdecimal():
        raise ValueError("reference supervisor has no stable process identity")
    if stable_identity(state.get("watcher", {})) != stable_identity(plan["reference_watcher"]):
        raise ValueError("reference supervisor identity changed")
    if tuple(state.get("order", ())) != REFERENCE_ORDER:
        raise ValueError("reference campaign order changed")
    status = state.get("status", "")
    if status == "collected_performance_review_pending":
        if tuple(state.get("completed_arms", ())) != REFERENCE_ORDER or state.get("error"):
            raise ValueError("reference campaign has missing or failed arms")
        return not any(
            identity and alive(identity)
            for identity in (state.get("watcher"), state.get("driver"))
        )
    if status.startswith(("running_", "postprocessing_")) and not state.get("error"):
        if not alive(state["watcher"]):
            raise ValueError("reference supervisor exited before complete collection")
        return False
    raise ValueError(f"reference campaign did not finish normally: {status}")


def candidate_environment(
    reference: dict, checkout: Path, port: int, *, tool_continuity: bool = False,
) -> dict[str, str]:
    if tuple(reference["order"]) != REFERENCE_ORDER:
        raise ValueError("reference must contain Full, JOIN-only and matched native")
    common = dict(reference["environment"])
    # Reconstruct historical controls independently of newer runtime defaults.
    if "BELIEFKV_ENABLE_FRONTIER_RESTORE" not in common:
        common.setdefault("EXECUTION_FRONTIER_RESTORE", "1")
    if "BELIEFKV_ENABLE_TOOL_CONTINUITY" not in common:
        common.setdefault("TOOL_CONTINUITY", "0")
    manifest = Path(common["WORKLOAD_MANIFEST"]).relative_to(reference["execution_checkout"])
    common["WORKLOAD_MANIFEST"] = str(manifest)
    spec = {"common_environment": common, "order": list(REFERENCE_ORDER)}
    environment = fresh.campaign_environment(
        {}, spec, checkout, Path(reference["engine_checkout"]), port,
    )
    baseline = arm_environment(environment, "full")
    if (
        baseline["BELIEFKV_ENABLE_FRONTIER_RESTORE"] != "1"
        or baseline["BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY"] != "0"
        or environment.get("PARALLEL_WAIT_PREFETCH") != "1"
        or environment.get("WAIT_PREFETCH_CONTEXTS") != "2"
    ):
        raise ValueError("reference does not use the qualified parent-only frontier policy")
    candidate = arm_environment({
        **environment,
        "EXECUTION_FRONTIER_RESTORE": "0", "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0",
        "TOOL_CONTINUITY": str(int(tool_continuity)),
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": str(int(tool_continuity)),
    }, "full")
    changed = {
        name for name in baseline.keys() | candidate.keys()
        if baseline.get(name) != candidate.get(name)
    }
    expected_changes = {"EXECUTION_FRONTIER_RESTORE", "BELIEFKV_ENABLE_FRONTIER_RESTORE"}
    if tool_continuity:
        expected_changes.update(("TOOL_CONTINUITY", "BELIEFKV_ENABLE_TOOL_CONTINUITY"))
    if changed != expected_changes:
        raise ValueError("frontier ablation changed another policy control")
    return candidate


def verify_candidate(plan: dict) -> str:
    fresh.verify(plan)
    development = Path(plan["development_checkout"])
    fresh._require_clean(development)
    paths = qualified.command([
        "git", "-C", str(development), "ls-files", "--",
        "beliefkv", "scripts", "configs", "patches", "tests",
    ]).splitlines()
    if set(paths) != set(plan["source_sha256"]):
        raise ValueError("main source set changed after qualification")
    comparison.verify_cpu_snapshot(development, plan["source_sha256"])
    # Documentation-only commits may advance main without changing qualified code.
    return qualified.command(["git", "-C", str(development), "rev-parse", "HEAD"])


def verify_reference(plan: dict, state: dict) -> dict:
    reference = load(Path(plan["reference_registration"]))
    fresh.verify(reference)
    if not reference_ready(plan, state):
        raise ValueError("reference processes have not exited")
    root = Path(reference["run_root"])
    outcomes = {}
    for mode in REFERENCE_ORDER:
        arm = root / mode
        fresh.observe(mode, arm, final=True)
        outcomes[mode] = fresh.completed_arm(
            load(arm / "client_36/summary.json"), state["original_exit_codes"][mode], 36,
        )
        if outcomes[mode] != state["arm_outcomes"][mode]:
            raise ValueError("reference completion evidence changed")
        if not (root / "timelines" / f"{mode}.json.gz").is_file():
            raise ValueError("reference timeline is missing")
    runtime = qualified.last_runtime_state(
        root / "full/opportunities/admission_opportunities.jsonl",
    )
    if (
        runtime.get("execution_frontier_restore") is not True
        or runtime.get("tool_restore_mode") != "execution"
        or runtime.get("join_last_child_priority", False) is not False
        or runtime.get("resident_first") is not False
        or runtime.get("parallel_wait_prefetch") is not True
        or runtime.get("wait_prefetch_contexts") != 2
    ):
        raise ValueError("reference Full effective controls differ from the ablation")
    report = root / "internal_comparison.json"
    paths = {report, *(root / "timelines" / f"{mode}.json.gz" for mode in REFERENCE_ORDER)}
    for mode in REFERENCE_ORDER:
        arm = root / mode
        paths.update(path for path in (arm / "server").iterdir() if path.suffix in (".json", ".jsonl"))
        paths.update((arm / "opportunities").glob("*.jsonl"))
        client = arm / "client_36"
        paths.update(path for path in client.iterdir() if path.suffix in (".json", ".jsonl", ".csv"))
        paths.update((client / "workflows").glob("*/runtime_events.deepagents.jsonl"))
    return {
        "arm_outcomes": outcomes,
        "comparison": str(report), "comparison_sha256": sha256_file(report),
        "timelines": {
            mode: {
                "path": str(root / "timelines" / f"{mode}.json.gz"),
                "sha256": sha256_file(root / "timelines" / f"{mode}.json.gz"),
            } for mode in REFERENCE_ORDER
        },
        "input_sha256": {str(path): sha256_file(path) for path in sorted(paths)},
        "performance_accepted": False,
    }


def register(args) -> None:
    if not 1 <= args.port <= 65535:
        raise ValueError("a valid experiment port is required")
    if any(path.exists() for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    )):
        raise FileExistsError("preserve an existing registration or run")
    reference = load(args.reference_registration)
    state_path = args.reference_registration.with_suffix(".state.json")
    state = load(state_path)
    if state.get("status") not in (
        "running_native_matched", "postprocessing_native_matched",
        "collected_performance_review_pending",
    ):
        raise ValueError("queue the ablation behind the reference campaign native arm")
    if state.get("status") != "collected_performance_review_pending" and not reservation.alive(
        state.get("watcher", {}),
    ):
        raise ValueError("reference supervisor is not live")
    fresh.verify(reference)
    fresh.check_hold(reference, released=True)
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    if not {
        "scripts/run_frontier_ablation_after_campaign.py",
        "tests/test_frontier_ablation_after_campaign.py",
        "tests/test_frontier_restore_ablation.py",
    } <= sources.keys():
        raise ValueError("CPU qualification omits the ablation operator or runtime checks")
    continuity = getattr(args, "tool_continuity", False)
    if continuity and not {
        "beliefkv/runtime/tool_continuity.py", "tests/test_tool_continuity.py",
    } <= sources.keys():
        raise ValueError("CPU qualification omits tool continuation execution")
    fresh._require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("freeze the qualified candidate from main")
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    label = args.run_root.name.rsplit("_", 1)[-1]
    checkout = comparison.EXPERIMENTS / ".worktrees" / f"beliefkv-{label}-frozen-{revision[:12]}-20261022"
    if checkout.exists():
        raise FileExistsError("preserve the frozen candidate")
    environment = candidate_environment(
        reference, checkout, args.port, tool_continuity=continuity,
    )
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    link = checkout / "third_party/sglang-v0.5.20"
    if not link.exists():
        link.symlink_to(reference["engine_checkout"], target_is_directory=True)
    if link.resolve() != Path(reference["engine_checkout"]).resolve():
        raise ValueError("candidate points to another engine")
    comparison.verify_cpu_snapshot(checkout, sources)
    inputs = {
        row["path"]: row["sha256"] for row in reference["external_inputs"]
    }
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
        "source_sha256": sources,
        "external_inputs": [{"path": name, "sha256": digest} for name, digest in sorted(inputs.items())],
        "cpu_report": str(args.cpu_report.resolve()), "passing_cpu_cases": count,
        "reference_registration": str(args.reference_registration.resolve()),
        "reference_state": str(state_path.resolve()), "reference_watcher": state["watcher"],
        "reference_run": reference["run_root"], "reference_code_commit": reference["code_commit"],
        "hold_file": reference["hold_file"], "gpu_not_before": reference["gpu_not_before"],
        "environment": environment, "order": ["full"], "port": args.port,
        "candidate_label": "full_tool_continuity" if continuity else "full_no_frontier",
        "run_root": str(args.run_root.resolve()),
        "performance_accepted": False,
        "comparison_scope": (
            "Latest qualified Full: ordinary frontier disabled; bounded short-tool "
            "queue-age inheritance enabled. Reuse completed V122 controls as descriptive "
            "references; code revisions and realized work differ, not a causal KV ablation."
        ) if continuity else (
            "Development ablation: latest qualified Full without ordinary execution-frontier "
            "restore; reuse the completed V122 Full/JOIN-only/native controls. Application "
            "revisions and dynamic realized work differ; not a strict same-revision causal effect."
        ),
    }
    reference_ready(plan, state)
    verify_candidate(plan)
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("xb") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_frontier_ablation_after_campaign.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "execution_checkout": str(checkout), "order": ["full"], "performance_accepted": False,
    }, indent=2))


def compare(plan: dict) -> None:
    run, reference = Path(plan["run_root"]), Path(plan["reference_run"])
    arguments = [sys.executable, str(ROOT / "scripts/compare_native_policy_runs.py")]
    for name, arm, timeline in (
        (plan.get("candidate_label", "full_no_frontier"),
         run / "full", run / "timelines/full.json.gz"),
        *((mode, reference / mode, reference / "timelines" / f"{mode}.json.gz")
          for mode in REFERENCE_ORDER),
    ):
        arguments.extend(("--arm", f"{name}={arm}", "--timeline", f"{name}={timeline}"))
    subprocess.run([
        *arguments, "--output", str(run / "internal_comparison.json"),
    ], check=True, capture_output=True)


def watch(registration: Path) -> None:
    plan = load(registration)
    state_path, run = registration.with_suffix(".state.json"), Path(plan["run_root"])
    state = {
        "status": "waiting_reference_campaign", "watcher": reservation.process(os.getpid()),
        "registration": str(registration), "run_root": str(run), "current_arm": "full",
        "order": ["full"], "completed_arms": [], "performance_accepted": False,
    }
    driver = None
    with registration.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            consecutive = 0
            while True:
                state["checked_at"] = reservation.now().isoformat()
                fresh.check_hold(plan, released=True)
                reference_state = load(Path(plan["reference_state"]))
                if not reference_ready(plan, reference_state):
                    consecutive = 0
                    state["status"] = "waiting_reference_campaign"
                else:
                    if "reference_evidence" not in state:
                        state["reference_evidence"] = verify_reference(plan, reference_state)
                    state["main_head_at_launch_check"] = verify_candidate(plan)
                    available = qualified.resources(plan["port"])
                    state.update(status="waiting_for_idle_resources", resources=available)
                    consecutive = consecutive + 1 if available["ready"] else 0
                    if consecutive >= 2:
                        break
                reservation.write_json(state_path, state)
                time.sleep(15)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
                 "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
                check=True, capture_output=True, timeout=30,
            )
            fresh.check_hold(plan, released=True)
            verify_candidate(plan)
            if verify_reference(plan, load(Path(plan["reference_state"]))) != state["reference_evidence"]:
                raise ValueError("reference evidence changed before launch")
            if not qualified.resources(plan["port"])["ready"]:
                raise ValueError("GPU or experiment port changed before launch")
            qualified.machine_helper("start_v88_after_0600_20261020").clear_stale_socket(plan["port"])
            run.mkdir(parents=True, exist_ok=False)
            reservation.write_json(run / "internal_plan.json", plan)
            environment = {**plan["environment"], "RUN_ROOT": str(run / "full"), "CUDA_VISIBLE_DEVICES": "0"}
            with (run / "full.launch.log").open("x") as output:
                driver = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/run_qwen35_native_regime_probe.sh")],
                    cwd=ROOT, env=qualified.driver_environment({"environment": environment}, os.environ),
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            state.update(
                status="running_" + plan.get("candidate_label", "full_no_frontier"),
                driver=reservation.process(driver.pid),
                started_at=reservation.now().isoformat(),
            )
            while driver.poll() is None:
                state["checked_at"] = reservation.now().isoformat()
                reservation.write_json(state_path, state)
                fresh.check_hold(plan, released=True)
                fresh.observe("full", run / "full")
                time.sleep(15)
            exit_code = driver.wait()
            driver = None
            state["original_exit_code"] = exit_code
            fresh.observe("full", run / "full", final=True)
            state["arm_outcomes"] = {"full": fresh.completed_arm(
                load(run / "full/client_36/summary.json"), exit_code, 36,
            )}
            state["status"] = "postprocessing_full"
            reservation.write_json(state_path, state)
            comparison.postprocess(plan, "full", run)
            state["completed_arms"].append("full")
            if verify_reference(plan, load(Path(plan["reference_state"]))) != state["reference_evidence"]:
                raise ValueError("reference evidence changed during the ablation")
            compare(plan)
            state.update(
                status="collected_performance_review_pending", finished_at=reservation.now().isoformat(),
            )
        except Exception as error:
            if driver is not None and driver.poll() is None:
                fresh.stop_arm(plan, state, f"frontier_ablation: {error}")
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
    parser.add_argument("--port", type=int, default=18528)
    parser.add_argument("--tool-continuity", action="store_true")
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        if any(getattr(args, name) is None for name in (
            "reference_registration", "cpu_report", "cpu_sources", "registration", "run_root",
        )):
            parser.error("registration requires reference, CPU qualification and new run paths")
        register(args)


if __name__ == "__main__":
    main()
