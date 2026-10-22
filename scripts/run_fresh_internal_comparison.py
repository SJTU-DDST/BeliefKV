#!/usr/bin/env python3
"""Freeze a fresh Full/JOIN/native campaign and wait for its one-time GPU hold."""

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
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_internal_comparison as comparison
from scripts.launch_semantic_h2d_after_pair import _require_clean

qualified = comparison.qualified
reservation = comparison.reservation
MINIMUM_TIME = datetime(2026, 10, 22, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
PAUSE = qualified.PAUSE


class HoldChanged(ValueError):
    pass


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def deadline(hold: dict) -> datetime:
    at = datetime.fromisoformat(hold["gpu_not_before"])
    if at.tzinfo is None or at < MINIMUM_TIME:
        raise HoldChanged("GPU deadline is earlier than the user's October 22 authorization")
    return at


def check_hold(plan: dict, *, released: bool, at: datetime | None = None) -> bool:
    hold = load(Path(plan["hold_file"]))
    at = at or reservation.now()
    if hold.get("automatic_resume_allowed") is not True:
        raise HoldChanged("automatic resume was disabled")
    if released:
        if PAUSE.exists() or at < deadline(hold):
            raise HoldChanged("new user pause or extended GPU reservation")
    else:
        if not PAUSE.is_file() or sha256_file(PAUSE) != plan["pause_sha256"]:
            raise HoldChanged("pause marker changed; preserve the newer instruction")
        stat = PAUSE.stat()
        if [stat.st_dev, stat.st_ino, stat.st_uid] != plan["pause_identity"]:
            raise HoldChanged("pause marker was replaced; preserve the newer instruction")
    return at >= deadline(hold)


def campaign_environment(
    base: dict[str, str], spec: dict, checkout: Path, engine: Path, port: int,
) -> dict[str, str]:
    common = spec["common_environment"]
    required = {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SAMPLING_SEED": "21", "SERVER_RANDOM_SEED": "21",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
        "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.95",
        "HOST_SPLIT": "80:20", "HICACHE_SIZE_GB": "200",
        "FANOUT_PROFILE": "native_in_graph_nested_2to4",
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "JOIN_PREFETCH_TRIGGER": "observed_eos", "EXECUTION_HANDOFF": "0",
        "PREFILL_SCHEDULING_POLICY": "join_priority",
        "PREFETCH_RESIDENCY_PROTECTION": "0",
        "TOOL_RESTORE_MODE": "execution", "IDLE_PREPARE_POLICY": "cold",
        "RESTORE_TIERS": "dependency",
    }
    if any(common.get(name) != value for name, value in required.items()):
        raise ValueError("fresh campaign differs from the qualified batch8 workload and policy")
    if spec["order"] != ["full", "join_priority_only", "native_matched"]:
        raise ValueError("fresh campaign must start with Full and matching JOIN/native controls")
    frontier = common.get(
        "BELIEFKV_ENABLE_FRONTIER_RESTORE",
        common.get("EXECUTION_FRONTIER_RESTORE", "0"),
    )
    if frontier not in ("0", "1"):
        raise ValueError("execution frontier restore must be 0 or 1")
    # Policy controls are reconstructed per arm, never inherited from an old run.
    env = {name: value for name, value in base.items() if not name.startswith("BELIEFKV_")}
    env.update(common)
    env.update(
        PYTHONPATH=str(checkout), SGLANG_SOURCE_CHECKOUT=str(engine), PORT=str(port),
        WORKLOAD_MANIFEST=str(checkout / common["WORKLOAD_MANIFEST"]),
        RUN_ROOT="", RESUME_PENDING="0", CUDA_VISIBLE_DEVICES="0",
        ARRIVAL_RATE_PER_SECOND="0.0", WORKFLOW_ARRIVAL_SCHEDULE="",
        NATIVE_REFERENCE_RUN="", NATIVE_REFERENCE_TIMELINE="", NATIVE_REFERENCE_CACHE="",
        BELIEFKV_EXPERIMENT_PAUSE_FILE=str(PAUSE),
        EXECUTION_FRONTIER_RESTORE=frontier,
    )
    return env


def frozen_campaign_spec(path: Path, checkout: Path) -> tuple[Path, dict]:
    frozen = checkout / path.resolve().relative_to(ROOT.resolve())
    return frozen, load(frozen)


def verify_engine_qualification(path: Path, *, engine: Path, patch_sha256: str) -> dict:
    certificate = load(path)
    if (
        Path(certificate["engine_checkout"]).resolve() != engine.resolve()
        or certificate["canonical_patch_sha256"] != patch_sha256
    ):
        raise ValueError("engine CPU qualification belongs to different sources")
    identities = []
    for name in ("previous_report", "report"):
        report = Path(certificate[name])
        if sha256_file(report) != certificate[name + "_sha256"]:
            raise ValueError("engine CPU report changed")
        root = ET.parse(report).getroot()
        rows = [(row.get("classname"), row.get("name")) for row in root.iter("testcase")]
        if (
            not rows or len(rows) != len(set(rows))
            or any(list(root.iter(tag)) for tag in ("failure", "error", "skipped"))
        ):
            raise ValueError("engine CPU report contains missing, duplicate or unsuccessful checks")
        identities.append(set(rows))
    if not identities[0] <= identities[1]:
        raise ValueError("engine CPU qualification dropped previously required checks")
    return {
        "certificate": str(path), "tree": certificate["tree"],
        "passing_cases": len(identities[1]), "retained_cases": len(identities[0]),
        "gpu_validation_pending": True,
    }


def verify(plan: dict, *, require_latest_main: bool = False) -> None:
    comparison.verify_sources(plan)
    qualification = plan.get("engine_cpu_qualification")
    if qualification is not None:
        verified = verify_engine_qualification(
            Path(qualification["certificate"]), engine=Path(plan["engine_checkout"]),
            patch_sha256=plan["source_sha256"]["patches/sglang-v0.5.20-beliefkv-staging.patch"],
        )
        if verified != qualification or verified["tree"] != plan["engine_tree"]:
            raise ValueError("engine qualification no longer matches the registered tree")
    if require_latest_main:
        development = Path(plan["development_checkout"])
        _require_clean(development)
        if qualified.command(["git", "-C", str(development), "rev-parse", "HEAD"]) != plan["code_commit"]:
            raise ValueError("main advanced after registration; qualify and register its latest revision")
        for name, digest in plan["source_sha256"].items():
            if name not in comparison.REPORT_ONLY_SOURCES and sha256_file(development / name) != digest:
                raise ValueError(f"registered main source changed: {name}")


def completed_arm(summary: dict, exit_code: int, root_count: int) -> dict:
    rows = summary.get("workflows", [])
    if (
        summary.get("workflow_count") != root_count
        or summary.get("completed_workflows") != root_count
        or summary.get("system_jct_eligible_workflows") != root_count
        or summary.get("measurement_valid_workflows") != root_count
        or len(rows) != root_count
        or len({row.get("instance_id") for row in rows}) != root_count
        or any(
            row.get("outcome") != "completed" or row.get("measurement_valid") is not True
            or row.get("system_jct_eligible") is not True for row in rows
        )
    ):
        raise ValueError("arm did not complete the full system-valid workload")
    eligible = summary.get("native_agent_jct_eligible_workflows")
    guarded = summary.get("agent_control", {}).get("guard_intervened_completions", 0)
    exclusions = [
        {
            "instance_id": row["instance_id"],
            "native_agent_jct_exclusion_reasons": row.get("native_agent_jct_exclusion_reasons", []),
        }
        for row in rows
        if row.get("native_agent_jct_eligible") is not True
    ]
    if eligible != root_count - len(exclusions):
        raise ValueError("native eligibility counts disagree with workflow records")
    if exit_code != 0 and not (
        exit_code == 1 and eligible < root_count and guarded > 0
        and all(
            row["native_agent_jct_exclusion_reasons"]
            and set(row["native_agent_jct_exclusion_reasons"]) <= {"guard_intervened_completion"}
            for row in exclusions
        )
    ):
        raise ValueError(f"workload process failed with exit code {exit_code}")
    return {
        "status": "completed_with_eligibility_exclusions" if exclusions else "completed",
        "original_exit_code": exit_code, "completed_workflows": root_count,
        "native_agent_jct_eligible_workflows": eligible,
        "native_agent_jct_exclusions": exclusions, "performance_accepted": False,
    }


def observe(
    mode: str, arm: Path, *, final: bool = False,
    legacy_execution_frontier_restore: bool | None = None,
) -> None:
    options = {"max_running_requests": 8}
    if legacy_execution_frontier_restore is not None:
        options["legacy_execution_frontier_restore"] = legacy_execution_frontier_restore
    comparison.observe_arm(mode, arm, **options)
    if final:
        for relative in (
            "server/server_info.json", "server/launch_configuration.json",
            "server/native_telemetry_status.json",
        ):
            if not (arm / relative).is_file():
                raise ValueError(f"{mode}: missing final configuration/telemetry evidence: {relative}")
        if mode != "native_matched" and qualified.last_runtime_state(
            arm / "opportunities/admission_opportunities.jsonl",
        ) is None:
            raise ValueError(f"{mode}: no effective runtime configuration evidence")


def register(args) -> None:
    paths = (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    )
    if any(path.exists() for path in paths):
        raise FileExistsError("preserve an existing registration or experiment")
    spec = load(args.spec)
    hold_path = Path(spec["hold_file"])
    hold = load(hold_path)
    if deadline(hold) < datetime.fromisoformat(spec["gpu_not_before"]):
        raise HoldChanged("current hold is earlier than the planned authorization")
    marker = PAUSE.stat()
    authorization = {
        "hold_file": str(hold_path), "pause_sha256": sha256_file(PAUSE),
        "pause_text": PAUSE.read_text(),
        "pause_identity": [marker.st_dev, marker.st_ino, marker.st_uid],
    }
    check_hold(authorization, released=False)
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    required = {
        "scripts/run_fresh_internal_comparison.py",
        "tests/test_fresh_internal_comparison.py",
        str(args.spec.resolve().relative_to(ROOT)),
    }
    if not required <= sources.keys():
        raise ValueError("CPU qualification omits the fresh campaign operator")
    _require_clean(ROOT)
    if qualified.command(["git", "-C", str(ROOT), "branch", "--show-current"]) != "main":
        raise ValueError("freeze the qualified development revision from main")
    base = load(args.base_registration)
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    checkout = comparison.EXPERIMENTS / ".worktrees" / (
        f"beliefkv-{args.run_root.name.rsplit('_', 1)[-1]}-frozen-{revision[:12]}-20261022"
    )
    if checkout.exists():
        raise FileExistsError("preserve the existing frozen campaign")
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
        check=True, capture_output=True,
    )
    engine = args.engine_checkout or Path(base["engine_checkout"])
    engine_qualification = None
    if bool(args.engine_checkout) != bool(args.engine_qualification):
        raise ValueError("a replacement engine requires its CPU source qualification")
    if args.engine_qualification is not None:
        engine_qualification = verify_engine_qualification(
            args.engine_qualification.resolve(), engine=engine,
            patch_sha256=sources["patches/sglang-v0.5.20-beliefkv-staging.patch"],
        )
    link = checkout / "third_party/sglang-v0.5.20"
    if not link.exists():
        link.symlink_to(engine, target_is_directory=True)
    if link.resolve() != engine.resolve():
        raise ValueError("frozen checkout points to a different native engine")
    comparison.verify_cpu_snapshot(checkout, sources)
    spec_path, spec = frozen_campaign_spec(args.spec, checkout)
    env = campaign_environment(base["environment"], spec, checkout, engine, args.port)
    workload_paths, workload = qualified.workload_inputs()
    inputs = workload_paths | {
        spec_path.resolve(), args.base_registration.resolve(),
        args.cpu_report.resolve(), args.cpu_sources.resolve(),
        Path(reservation.__file__),
        Path(qualified.machine_helper("start_v88_after_0600_20261020").__file__),
        Path(qualified.machine_helper("start_v96_root_scoped_36root_20261020").__file__),
    }
    inputs.update(Path(env[name]) for name in (
        "H2D_SEED_ARTIFACT", "TOOL_TIMING_ARTIFACT", "TRANSFER_SERVICE_SEED",
    ))
    if engine_qualification is not None:
        certificate = load(args.engine_qualification)
        inputs.update((
            args.engine_qualification.resolve(), Path(certificate["report"]),
            Path(certificate["previous_report"]),
        ))
    inputs.update(Path(env["CONTEXT_TOKENIZER_PATH"]) / name for name in (
        "config.json", "tokenizer_config.json", "tokenizer.json",
    ))
    plan = {
        "schema_version": 1, "registered_at": reservation.now().isoformat(),
        **authorization, "gpu_not_before": deadline(hold).isoformat(),
        "development_checkout": str(ROOT), "code_commit": revision,
        "execution_checkout": str(checkout), "engine_checkout": str(engine),
        "engine_tree": (
            engine_qualification["tree"] if engine_qualification is not None else base["engine_tree"]
        ),
        "cpu_report": str(args.cpu_report.resolve()), "passing_cpu_cases": count,
        "engine_qualification": (
            "replacement engine; independent CPU suite rerun, GPU validation pending"
            if engine_qualification is not None else "unchanged qualified engine; independent suite not rerun"
        ),
        **({"engine_cpu_qualification": engine_qualification} if engine_qualification is not None else {}),
        "source_sha256": sources,
        "external_inputs": [
            {"path": str(path), "sha256": sha256_file(path)} for path in sorted(inputs)
        ],
        "workload": workload, "environment": env, "order": spec["order"],
        "run_root": str(args.run_root.resolve()), "port": args.port,
        "conditional_controls": spec["conditional_controls"],
        "performance_accepted": False, "historical_full_reused": False,
        "comparison_scope": (
            "Fresh same-revision batch8 Full, JOIN-only and frontend-matched native. "
            "Guard exclusions, original process exits and realized work remain explicit. "
            "Postprocessing never automatically accepts performance."
        ),
    }
    verify(plan, require_latest_main=True)
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("xb") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_fresh_internal_comparison.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "not_before": plan["gpu_not_before"], "order": plan["order"],
        "code_commit": revision, "execution_checkout": str(checkout),
    }, indent=2))


def stop_arm(plan: dict, state: dict, reason: str) -> None:
    arm = Path(plan["run_root"]) / state["current_arm"]
    identities = reservation.owned_runs().get(Path(plan["run_root"]), [])
    driver = state.get("driver")
    if driver is not None and reservation.alive(driver) and not any(
        row["pid"] == driver["pid"] for row in identities
    ):
        identities.append(driver)
    reservation.stop_run(arm, identities, state)


def watch(registration: Path) -> None:
    plan = load(registration)
    state_path = registration.with_suffix(".state.json")
    run = Path(plan["run_root"])
    state = {
        "status": "waiting_until_authorized_time",
        "watcher": reservation.process(os.getpid()), "registration": str(registration),
        "run_root": str(run), "gpu_not_before": plan["gpu_not_before"],
        "order": plan["order"], "completed_arms": [], "arm_outcomes": {},
        "performance_accepted": False,
    }
    released = False
    driver = None
    with registration.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            consecutive = 0
            while True:
                state["checked_at"] = reservation.now().isoformat()
                if not check_hold(plan, released=False):
                    consecutive = 0
                    state["status"] = "waiting_until_authorized_time"
                else:
                    verify(plan, require_latest_main=True)
                    available = qualified.resources(plan["port"])
                    state.update(status="waiting_for_idle_resources", resources=available)
                    consecutive = consecutive + 1 if available["ready"] else 0
                    if consecutive >= 2:
                        break
                reservation.write_json(state_path, state)
                time.sleep(15)
            verify(plan, require_latest_main=True)
            if not check_hold(plan, released=False):
                raise HoldChanged("deadline changed before preflight")
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
                 "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
                check=True, capture_output=True, timeout=30,
            )
            if not check_hold(plan, released=False) or not qualified.resources(plan["port"])["ready"]:
                raise HoldChanged("authorization or resources changed before releasing the marker")
            verify(plan, require_latest_main=True)
            run.mkdir(parents=True, exist_ok=False)
            reservation.write_json(run / "internal_plan.json", plan)
            PAUSE.unlink()
            released = True
            for mode in plan["order"]:
                verify(plan)
                check_hold(plan, released=True)
                if not qualified.resources(plan["port"])["ready"]:
                    raise ValueError("GPU or experiment port occupied before the next arm")
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
                state.update(
                    status=f"running_{mode}", current_arm=mode,
                    driver=reservation.process(driver.pid), started_at=reservation.now().isoformat(),
                )
                while driver.poll() is None:
                    state["checked_at"] = reservation.now().isoformat()
                    reservation.write_json(state_path, state)
                    check_hold(plan, released=True)
                    observe(mode, arm)
                    time.sleep(15)
                exit_code = driver.wait()
                driver = None
                state.setdefault("original_exit_codes", {})[mode] = exit_code
                reservation.write_json(state_path, state)
                observe(mode, arm, final=True)
                summary = load(arm / "client_36/summary.json")
                outcome = completed_arm(summary, exit_code, 36)
                state["arm_outcomes"][mode] = outcome
                state.update(status=f"postprocessing_{mode}")
                reservation.write_json(state_path, state)
                comparison.postprocess(plan, mode, run)
                state["completed_arms"].append(mode)
                reservation.write_json(state_path, state)
            arguments = [sys.executable, str(ROOT / "scripts/compare_native_policy_runs.py")]
            for mode in plan["order"]:
                arguments.extend((
                    "--arm", f"{mode}={run / mode}", "--timeline",
                    f"{mode}={run / 'timelines' / (mode + '.json.gz')}",
                ))
            subprocess.run(
                [*arguments, "--output", str(run / "internal_comparison.json")],
                check=True, capture_output=True,
            )
            state.update(
                status="collected_performance_review_pending", finished_at=reservation.now().isoformat(),
            )
        except Exception as error:
            if driver is not None and driver.poll() is None:
                stop_arm(plan, state, f"fresh_comparison: {error}")
                driver.wait(timeout=60)
            state.update(
                status="cancelled_hold_changed" if isinstance(error, HoldChanged)
                else "stopped_for_investigation",
                error=repr(error), finished_at=reservation.now().isoformat(),
            )
            if released and not state["completed_arms"] and not PAUSE.exists():
                reservation.write_json(PAUSE, {
                    "reason": "fresh_campaign_stopped_before_first_qualified_arm",
                    "registration": str(registration), "error": repr(error),
                })
        finally:
            reservation.write_json(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--base-registration", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--cpu-sources", type=Path)
    parser.add_argument("--engine-checkout", type=Path)
    parser.add_argument("--engine-qualification", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--port", type=int, default=18524)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        if any(getattr(args, name) is None for name in (
            "spec", "base_registration", "cpu_report", "cpu_sources", "registration", "run_root",
        )):
            parser.error("registration requires spec, base registration, CPU qualification and new paths")
        register(args)


if __name__ == "__main__":
    main()
