"""Collect complete benefit admission results, with an optional qualified CPU optimization."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts import start_workflow_benefit_only_candidate as candidate
from scripts.review_learned_work_ranking import control_optimization_scope, verify_learned_combination_pair
from scripts.review_workflow_objective_pair import verify_objective_pair
from scripts.train_workflow_work import write


OUTPUT = candidate.HERE / "final_results_r1"
PLAN = OUTPUT / "plan.json"
STATE = OUTPUT / "watcher.state.json"


def select_control_optimized_candidate():
    global candidate, OUTPUT, PLAN, STATE
    from scripts import start_workflow_control_optimized_candidate

    candidate = start_workflow_control_optimized_candidate
    OUTPUT = candidate.HERE / "final_results_r1"
    PLAN, STATE = OUTPUT / "plan.json", OUTPUT / "watcher.state.json"


def prepare():
    if PLAN.exists() or git_source_state(ROOT)["dirty"]:
        raise ValueError("preserve earlier analysis plans and freeze the analysis source")
    waiter_path = candidate.HERE / "resource_waiter_process.json"
    waiter = causal.load(waiter_path)
    qualification_path = candidate.HERE / "qualification.json"
    qualification = causal.load(qualification_path)
    serving = Path(qualification.get("serving_checkout", Path(waiter["argv"][1]).resolve().parents[1]))
    if git_source_state(serving) != qualification["source"]:
        raise ValueError("the queued qualified serving source changed")
    spec_path = candidate.HERE / "serving_ready/qualified_full.json"
    spec = causal.load(spec_path)
    reference_root = candidate.BASE / "host400_registered_copy_pressure_r1/capacity_review_correction_r1"
    references = {
        "full": (candidate.CONTROL, reference_root),
        "ratio_first": (
            candidate.OPS / "workflow_original73_host400_R5_horizon64_20261102_r1.registration.json",
            candidate.OLD / "review",
        ),
        "ratio_confirmation": (candidate.PREDECESSOR, candidate.OLD / "confirmation/review"),
        "thunder": (
            candidate.OPS / "workflow_original73_host400_thunder_decay_20261102_r1.registration.json",
            reference_root / "thunderagent400_matching_r1/review",
        ),
    }
    pins = {str(path.resolve()): sha256_file(path) for path in (
        waiter_path, qualification_path, spec_path, candidate.COHORT, Path(__file__),
        ROOT / "scripts/review_workflow_objective_pair.py",
    )}
    configs = {}
    for name, (registration, directory) in references.items():
        plan = causal.load(registration)
        config_path = Path(plan["experiment_configuration"])
        configs[name] = causal.load(config_path)
        for path in (registration, config_path, *(directory / part for part in (
            "review.json", "cohort.json", "workload_decision.json",
        ))):
            pins[str(path.resolve())] = sha256_file(path)
    verify_learned_combination_pair(configs["full"], spec)
    for name in ("ratio_first", "ratio_confirmation"):
        verify_objective_pair(configs[name], spec)
    optimization = control_optimization_scope(spec)
    commands = []
    common = [
        "--cohort", str(candidate.COHORT),
    ]
    commands.append(("versus_full", [
        str(ROOT / "scripts/review_learned_work_ranking.py"),
        "--control", str(references["full"][0]), "--learned", str(candidate.REG),
        "--control-review-dir", str(references["full"][1]),
        "--learned-review-dir", str(candidate.HERE / "review"),
        *common, "--minimum-jct-gain", ".01", "--output", str(OUTPUT / "versus_full.json"),
    ]))
    for name in ("ratio_first", "ratio_confirmation"):
        commands.append(("versus_" + name, [
            str(ROOT / "scripts/review_workflow_objective_pair.py"),
            "--reference", str(references[name][0]), "--candidate", str(candidate.REG),
            "--reference-review-dir", str(references[name][1]),
            "--candidate-review-dir", str(candidate.HERE / "review"),
            *common, "--minimum-jct-gain", ".01", "--output", str(OUTPUT / ("versus_" + name + ".json")),
        ]))
    commands.append(("versus_thunder", [
        str(ROOT / "scripts/review_predictive_thunder_cohort.py"),
        "--predictive", str(candidate.REG), "--thunder", str(references["thunder"][0]),
        "--predictive-review-dir", str(candidate.HERE / "review"),
        "--thunder-review-dir", str(references["thunder"][1]),
        *common, "--output", str(OUTPUT / "versus_thunder.json"),
    ]))
    commands.append(("actual_admission_service", [
        str(serving / "scripts/audit_workflow_combination_choices.py"),
        "--registration", str(candidate.REG), "--control", str(references["full"][0]),
        *common, "--complete", "--policy-kind", "learned",
        "--output", str(OUTPUT / "actual_admission_service.json"),
    ]))
    if optimization is not None:
        commands.append(("synchronous_control_costs", [
            str(ROOT / "scripts/audit_synchronous_control_costs.py"),
            "--registration", str(candidate.REG),
            "--registration", str(references["ratio_first"][0]),
            "--registration", str(references["ratio_confirmation"][0]),
            "--cohort", str(candidate.COHORT),
            "--output", str(OUTPUT / "synchronous_control_costs.json"),
        ]))
    for _name, command in commands:
        pins[command[0]] = sha256_file(Path(command[0]))
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a complete-control analysis input changed during preparation")
    write(PLAN, {
        "analysis_source": git_source_state(ROOT), "comparison": str(candidate.REG),
        "resource_waiter": waiter, "cohort_observer": str(candidate.OBSERVER),
        "review_dir": str(candidate.HERE / "review"), "run_root": str(candidate.RUN),
        "commands": commands, "inputs_sha256": pins,
        "fixed18_complete_JCT_and_common_window_only": True,
        "both_ratio_runs_retained_without_reference_cherry_picking": True,
        "synchronous_control_optimization": optimization,
        "only_objective_changed": optimization is None,
        "ranking_gain_isolated": False,
        "GPU_action": False, "GPU_followup_registered": False,
    })
    print(json.dumps({"prepared": str(PLAN), "CPU_only_steps": len(commands)}), flush=True)


def readiness(plan):
    registration = Path(plan["comparison"])
    if not registration.exists():
        if not causal.reservation.alive(plan["resource_waiter"]) and not registration.exists():
            raise RuntimeError("the resource waiter exited before comparison registration")
        return "waiting_resource_waiter"
    state_path = registration.with_suffix(".state.json")
    if not state_path.exists():
        return "waiting_supervisor_state"
    state = causal.load(state_path)
    owners = [state.get(name) for name in ("watcher", "server", "driver")]
    observer_path = Path(plan["cohort_observer"]).with_suffix(".state.json")
    observer = causal.load(observer_path) if observer_path.exists() else {}
    complete = Path(plan["run_root"]) / "cohort_termination/completion.json"
    reviews = [Path(plan["review_dir"]) / name for name in (
        "review.json", "cohort.json", "workload_decision.json",
    )]
    if complete.exists() and all(path.exists() for path in reviews) and not any(
        causal.reservation.alive(owner) for owner in owners
    ):
        return "complete_review_ready"
    if causal.reservation.alive(observer.get("process")):
        return "waiting_measured_cohort_or_review"
    if any(causal.reservation.alive(owner) for owner in owners):
        return "waiting_complete_measured_work"
    if observer.get("status", "").startswith("stopped_"):
        raise RuntimeError("preserve incomplete cohort review: " + repr(observer.get("error")))
    if any(owner is not None for owner in owners):
        raise RuntimeError("actual comparison owners exited without complete measured review")
    return "waiting_observable_owner_handles"


def watch():
    plan = causal.load(PLAN)
    if git_source_state(ROOT) != plan["analysis_source"]:
        raise ValueError("the frozen results observer changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("a pinned complete-control result or analysis input changed")
    state = {
        "status": "waiting_complete_benefit_comparison",
        "process": causal.reservation.process(os.getpid()), "steps": [],
        "GPU_action": False, "GPU_followup_registered": False,
    }

    def publish():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(STATE, state)

    try:
        while True:
            try:
                status = readiness(plan)
            except (FileNotFoundError, json.JSONDecodeError) as error:
                state.update(status="retrying_transient_observation", observation_error=repr(error))
                publish()
                time.sleep(10.)
                continue
            state["status"] = status
            publish()
            if status == "complete_review_ready":
                break
            time.sleep(10.)
        for name, command in plan["commands"]:
            state.update(status="collecting_complete_results", current_step=name)
            publish()
            with (OUTPUT / (name + ".log")).open("x") as log:
                child = subprocess.Popen(
                    [sys.executable, *command], cwd=Path(command[0]).parent.parent,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                )
                while child.poll() is None:
                    try:
                        child.wait(timeout=30.)
                    except subprocess.TimeoutExpired:
                        publish()
            state["steps"].append({"name": name, "returncode": child.returncode})
            if child.returncode:
                raise RuntimeError("preserve failed complete result step: " + name)
        state.update(status="complete_requires_goal_and_repeatability_review", performance_accepted=False)
    except BaseException as error:
        state.update(status="stopped_preserve_analysis_failure", error=repr(error))
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    publish()


def queue():
    if STATE.exists() or git_source_state(ROOT) != causal.load(PLAN)["analysis_source"]:
        raise ValueError("preserve prior observer execution and its frozen source")
    with (OUTPUT / "watcher.log").open("x") as log:
        options = (
            ["--control-optimized"]
            if candidate.__name__.endswith("start_workflow_control_optimized_candidate") else []
        )
        child = subprocess.Popen(
            ["taskset", "-c", "0", sys.executable, str(Path(__file__)), "--watch", *options], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    process = causal.reservation.process(child.pid)
    write(OUTPUT / "watcher_process.json", process)
    print(json.dumps(process), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ("prepare", "queue", "watch"):
        modes.add_argument("--" + name, action="store_true")
    parser.add_argument("--control-optimized", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("complete-results preparation and observation require hidden CUDA and nice19")
    if args.control_optimized:
        select_control_optimized_candidate()
    prepare() if args.prepare else queue() if args.queue else watch()
