"""Queue a new background workload through position qualification and matched policies."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts import run_arrival_work_ranking_comparison as runner
from scripts import run_causal_comparison as causal
from scripts.finish_model_work_oracle import verify_pins, wait_observer_publication
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def command_with(command, values):
    result = list(command)
    for key, value in values.items():
        if result.count(key) != 1 or result.index(key) + 1 == len(result):
            raise ValueError("require one unambiguous option: " + key)
        result[result.index(key) + 1] = str(value)
    return result


def validate_preparation(prep, arrivals, cohort, source):
    validate_arrival_plan(arrivals, [row["workflow_id"] for row in source["selected_workflows"]])
    validate_cohort(cohort, arrivals)
    if (
        prep.get("kind") != "matched_background_extension_work_ranking_preparation"
        or prep["workflows"] != len(arrivals["arrival_schedule"])
        or prep["initial_workflows"] != arrivals["initial_workflows"]
        or prep["mean_interval_seconds"] != 1. / arrivals["rate_per_second"]
        or prep["fixed_measured_workflows"] != cohort["measured_workflow_ids"]
        or len(cohort["measured_workflow_ids"]) != 18
        or prep["GPU_stage_order"] != list(runner.LABELS)
        or prep["static_completion_coverage_screen"]["candidate_schedule_covers_static_tail"] is not True
        or source["population"]["model_work_units"]["p80_p20"] < prep["minimum_model_work_p80_p20"]
    ):
        raise ValueError("prepared population, arrivals, cohort or static tail coverage differ")


def start(args):
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError("preserve previous queued comparisons")
    if not args.run_suffix or any(
        ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in args.run_suffix
    ):
        raise ValueError("use an explicit lowercase run identity")
    causal.fresh._require_clean(ROOT)
    prep, cpu, previous = (
        causal.load(path) for path in (
            args.preparation, args.cpu_evidence, args.previous_execution,
        )
    )
    verify_pins(prep["inputs_sha256"])
    if (
        cpu.get("returncode") != 0 or cpu.get("failed_error_skipped_cases") != 0
        or cpu.get("all_retained_literal_identities_present") is not True
        or cpu.get("sources_unchanged_during_execution") is not True
    ):
        raise ValueError("require passing source-bound CPU qualification")
    source_path = Path(prep["source_registration"])
    source = causal.load(source_path)
    causal.comparison.verify_sources(source)
    causal.comparison.verify_cpu_snapshot(ROOT, causal.load(Path(cpu["source_manifest"]["path"])))
    validate_preparation(
        prep, causal.load(Path(prep["arrivals"])), causal.load(Path(prep["cohort"])),
        causal.load(Path(prep["source_arm"]) / "composite_source.json"),
    )
    verify_execution(args.previous_registration, args.previous_cohort)
    runner.released()
    template = previous["stages"]["full"]["command"]
    engine = Path(template[template.index("--engine") + 1])
    engine_report = Path(template[template.index("--engine-report") + 1])
    old = causal.load(Path(template[template.index("--engine-sources") + 1]))
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, old["canonical_patch"]["actual_tree"]))
    normalized = []
    for item in (old, native):
        item = deepcopy(item)
        item.pop("state_sha256")
        item["canonical_patch"].pop("path")
        normalized.append(item)
    if normalized[0] != normalized[1]:
        raise ValueError("qualified native engine changed beyond the canonical patch pathname")
    prefix = f"workflow_original{prep['workflows']}_host400_mean{prep['mean_interval_seconds']:g}"
    stages = {}
    for index, label in enumerate(runner.LABELS):
        stem = f"{prefix}_{label}_{args.run_suffix}"
        reg = runner.OPS / (stem + ".registration.json")
        run = runner.MAIN / "experiments/raw" / stem
        port = args.port + index
        if any(path.exists() for path in (reg, reg.with_suffix(".state.json"), run)) or (
            os.path.lexists(f"/tmp/bkv-causal-{port}.sock")
            or not causal.qualified.resources(port)["idle_port"]
        ):
            raise FileExistsError("comparison identity, socket or port is already occupied")
        stages[label] = {
            "registration": str(reg), "run_root": str(run), "port": port,
            "configuration": prep["configurations"][label],
            "review": str(output / (label + "_review")),
        }
    position_stem = f"{prefix}_position_{args.run_suffix}"
    position = runner.OPS / (position_stem + ".registration.json")
    position_run = runner.MAIN / "experiments/raw" / position_stem
    if any(path.exists() for path in (position, position.with_suffix(".state.json"), position_run)):
        raise FileExistsError("position qualification identity already exists")
    output.mkdir(parents=True, exist_ok=False)
    native_path = output / "native_sources.json"
    write(native_path, native)
    causal.engine_evidence(engine, engine_report, native_path)
    for stage in stages.values():
        command = runner.stage_command(template, stage, prep, cpu, native_path)
        stage["command"] = command_with(command, {
            "--source-arm": prep["source_arm"],
            "--gpu-smoke": position_run / "gpu_smoke.json",
            "--sampling-calibration": position_run / "sampling_calibration.json",
            "--tool-duration-overlay": causal.load(Path(stage["configuration"]))["tool_timing_overlay"]["path"],
        })
    command = [
        sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py"),
        "--source-registration", str(source_path),
        "--reference", template[template.index("--reference") + 1],
        "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(engine), "--engine-report", str(engine_report),
        "--engine-sources", str(native_path),
        "--registration", str(position), "--run-root", str(position_run),
        "--comparison-registration", stages["full"]["registration"],
        "--comparison-root", stages["full"]["run_root"],
        "--port", str(args.port), "--experiment-config", stages["full"]["configuration"],
    ]
    pins = [
        args.preparation, args.cpu_evidence, Path(cpu["report"]["path"]),
        Path(cpu["source_manifest"]["path"]), args.previous_execution,
        args.previous_registration, args.previous_cohort, source_path, native_path, Path(__file__),
    ]
    plan = {
        **prep, "operator": str(ROOT), "stages": stages, "GPU_action": True,
        "position_registration": str(position), "position_command": command,
        "previous_execution": str(args.previous_execution.resolve()),
        "previous_fixed_cohort_completed_and_actual_exit_verified": True,
        "inputs_sha256": {
            **prep["inputs_sha256"],
            **{str(path.resolve()): sha256_file(path) for path in pins},
        },
    }
    plan_path = output / "execution_plan.json"
    write(plan_path, plan)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=240)
    write(output / "position_registration_result.json", {
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    })
    if result.returncode:
        raise RuntimeError("preserve rejected position registration: " + result.stderr[-3000:])
    owner = json.loads(result.stdout)["watcher"]
    write(output / "position_owner.json", owner)
    with (output / "execution.log").open("x") as stream:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(plan_path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {"execution_plan": str(plan_path), "position_watcher": owner,
            "coordinator": causal.reservation.process(child.pid)}


def watch(plan_path):
    output = plan_path.parent
    plan = causal.load(plan_path)
    position = Path(plan["position_registration"])
    owner = causal.load(output / "position_owner.json")
    state = {
        "status": "waiting_position_qualification",
        "process": causal.reservation.process(os.getpid()), "position_watcher": owner,
        "GPU_registered_stages": ["position"], "goal_complete": False,
    }

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(output / "state.json", state)

    with (output / "coordinator.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            while True:
                runner.released()
                pulse()
                if position.with_suffix(".state.json").is_file():
                    current = causal.load(position.with_suffix(".state.json"))
                    if current.get("status") == "calibrated_comparison_registered":
                        break
                    if current.get("status", "").startswith(("stopped", "interrupted")):
                        raise RuntimeError("position qualification stopped: " + str(current.get("error")))
                    state["position_status"] = current.get("status")
                if not causal.reservation.alive(owner):
                    raise RuntimeError("position watcher exited without publishing a successful handoff")
                time.sleep(5.)
            while causal.reservation.alive(owner):
                pulse()
                time.sleep(.2)
            verify_pins(plan["inputs_sha256"])
            stage = plan["stages"]["full"]
            position_run = Path(causal.load(position)["run_root"])
            full_owner = causal.load(position_run / "comparison_registration.log")["watcher"]
            full_state_path = Path(stage["registration"]).with_suffix(".state.json")
            wait_observer_publication(full_state_path, full_owner, pulse)
            full_state = causal.load(full_state_path)
            if not causal.reservation.alive(full_owner) or any(
                full_owner[key] != full_state["watcher"].get(key) for key in ("pid", "start_ticks", "argv")
            ):
                raise RuntimeError("new Full supervisor is not the live registered owner")
            stage["existing_watcher"] = full_owner
            write(output / "position_to_full_handoff.json", {
                "position_actual_exit_verified": True, "full_existing_watcher": full_state["watcher"],
                "duplicate_full_registration": False,
            })
            runner.watch_locked(plan, output)
        except BaseException as error:
            state.update(status="stopped_preserve_background_work_ranking_comparison", error=repr(error))
            pulse()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("preparation", "cpu-evidence", "previous-execution", "previous-registration",
                 "previous-cohort", "output-dir"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--run-suffix", default="20261112_r1")
    parser.add_argument("--port", type=int, default=19041)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("hide CUDA in the coordinator and keep serving parents at nice0")
    if args.watch:
        watch(args.watch)
    elif all(getattr(args, key) is not None for key in (
        "preparation", "cpu_evidence", "previous_execution", "previous_registration",
        "previous_cohort", "output_dir",
    )):
        print(json.dumps(start(args)))
    else:
        parser.error("provide preparation, CPU evidence, previous complete execution and output")


if __name__ == "__main__":
    main()
