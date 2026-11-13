"""Collect completed-stage scheduling evidence without controlling GPU processes."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

LABELS = ("full", "oracle", "learned", "thunderagent_acting_decay", "native_matched")
DISPLAY = dict(zip(LABELS, (
    "Nonranking Full", "Continuous-work Oracle", "Learned R5", "ThunderAgent", "SGLang native",
)))


def load(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tasks(plan, output):
    operator, cohort = Path(plan["operator"]), plan["cohort"]
    result = []

    def add(name, required, script, arguments):
        result.append({
            "name": name, "required_completed_stages": list(required),
            "command": [sys.executable, str(operator / "scripts" / script), *map(str, arguments)],
        })

    for label in LABELS[:3]:
        registration = plan["stages"][label]["registration"]
        add(label + "_periodic", (label,), "audit_periodic_admission_decisions.py", (
            "--registration", registration, "--cohort", cohort, "--complete",
            "--output", output / (label + "_periodic.json"),
        ))
        add(label + "_competition", (label,), "summarize_measured_admission_competition.py", (
            "--periodic-report", output / (label + "_periodic.json"), "--cohort", cohort,
            "--output", output / (label + "_competition.json"),
        ))
        add(label + "_release_service", (label,), "audit_bound_capacity_release_service.py", (
            "--registration", registration, "--cohort", cohort,
            "--output", output / (label + "_release_service.json"),
        ))
        if label == "full":
            add("full_oracle_opportunities", (label,), "audit_control_oracle_opportunities.py", (
                "--registration", registration, "--cohort", cohort,
                "--output", output / "full_oracle_opportunities.json",
            ))
            add("full_service_order", (label,), "audit_control_oracle_service_order.py", (
                "--registration", registration, "--cohort", cohort, "--complete",
                "--output", output / "full_service_order.json",
            ))
        else:
            add(label + "_choices", ("full", label), "audit_workflow_combination_choices.py", (
                "--registration", registration, "--control", plan["stages"]["full"]["registration"],
                "--cohort", cohort, "--complete", "--policy-kind", "learned" if label == "learned" else "oracle",
                "--output", output / (label + "_choices.json"),
            ))
            add(label + "_service_survival", ("full", label), "audit_combination_service_survival.py", (
                "--service-analysis", output / (label + "_choices.json"),
                "--output", output / (label + "_service_survival.json"),
            ))
    add("paused_curves", LABELS[:3], "plot_paused_agent_timeline.py", (
        "--cohort", cohort,
        *[argument for label in LABELS[:3] for argument in (
            "--report", DISPLAY[label] + "=" + plan["stages"][label]["registration"],
        )],
        "--output-dir", output / "paused_curves",
    ))
    add("five_method_figures", LABELS, "plot_workflow_cohort_comparison.py", (
        *[argument for label in LABELS for argument in (
            "--report", DISPLAY[label] + "=" + str(Path(plan["stages"][label]["review"]) / "cohort.json"),
        )],
        "--output-dir", output / "five_method_figures",
    ))
    return result


def ready_task(pending, completed):
    return next((
        task for task in pending if set(task["required_completed_stages"]) <= completed
    ), None)


def watch(registration):
    registration = registration.resolve()
    spec, output = load(registration), registration.parent
    plan = load(spec["campaign_plan"])
    sys.path.insert(0, plan["operator"])
    from scripts import run_causal_comparison as causal
    from scripts.verify_measured_execution import verify_execution

    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("run analysis on CPU0 with nice19, one thread and CUDA hidden")
    for name, digest in spec["inputs_sha256"].items():
        if sha256(name) != digest:
            raise ValueError("analysis source or campaign plan changed: " + name)
    state = {
        "status": "waiting_completed_stage", "process": causal.reservation.process(os.getpid()),
        "completed_stage_verifications": [], "completed_tasks": [],
        "GPU_action": False, "GPU_followup_registered": False, "goal_complete": False,
    }

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        write(output / "state.json", state)

    pending, proofs = list(spec["tasks"]), {}
    with (output / "collector.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            while pending:
                for label in LABELS:
                    advertised = Path(spec["campaign_plan"]).parent / (label + "_execution_proof.json")
                    if label in proofs or not advertised.is_file():
                        continue
                    # The file is a trigger; independently recheck work, drain and actual exit.
                    proof = verify_execution(Path(plan["stages"][label]["registration"]), Path(plan["cohort"]))
                    if proofs and proof["work"] != next(iter(proofs.values()))["work"]:
                        raise ValueError("complete measured work differs between methods")
                    proofs[label] = proof
                    write(output / (label + "_independent_execution.json"), proof)
                    state["completed_stage_verifications"].append(label)
                task = ready_task(pending, set(proofs))
                if task is None:
                    current = load(Path(spec["campaign_plan"]).parent / "state.json")
                    owner = current["process"]
                    if not causal.reservation.alive(owner):
                        raise RuntimeError("campaign owner exited before remaining methods completed")
                    state.update(status="waiting_completed_stage", campaign_owner=owner)
                    pulse()
                    time.sleep(20)
                    continue
                state.update(status="analyzing", current_task=task["name"])
                pulse()
                with (output / (task["name"] + ".log")).open("x") as stream:
                    child = subprocess.Popen(
                        task["command"], cwd=plan["operator"], stdin=subprocess.DEVNULL,
                        stdout=stream, stderr=subprocess.STDOUT,
                    )
                    state["CPU_child"] = causal.reservation.process(child.pid)
                    while child.poll() is None:
                        pulse()
                        time.sleep(2)
                state.pop("CPU_child", None)
                if child.returncode:
                    raise RuntimeError("preserve failed CPU analysis: " + task["name"])
                state["completed_tasks"].append(task["name"])
                pending.remove(task)
                pulse()
            state.update(
                status="completed_five_method_CPU_evidence",
                complete_matched_measured_work_fresh_drain_and_actual_exit=True,
                single_run_does_not_establish_repeatability=True, performance_accepted=False,
            )
        except BaseException as error:
            state.update(status="stopped_preserve_CPU_evidence", error=repr(error))
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        pulse()


def register(args):
    args.campaign_plan, args.output_dir = args.campaign_plan.resolve(), args.output_dir.resolve()
    plan = load(args.campaign_plan)
    sys.path.insert(0, plan["operator"])
    from scripts import run_causal_comparison as causal
    from scripts.plot_workflow_cohort_comparison import COLORS

    current = load(args.campaign_plan.parent / "state.json")
    if not causal.reservation.alive(current["process"]):
        raise ValueError("attach to the actual live campaign owner")
    if args.output_dir.exists():
        raise FileExistsError("preserve existing CPU collection attempts")
    if not set(DISPLAY.values()) <= COLORS.keys():
        raise ValueError("figure labels do not match the qualified plotting palette")
    planned = tasks(plan, args.output_dir)
    script_paths = {Path(task["command"][1]) for task in planned}
    if any(not path.is_file() for path in script_paths):
        raise ValueError("analysis script missing from frozen qualified operator")
    args.output_dir.mkdir(parents=True)
    spec = {
        "campaign_plan": str(args.campaign_plan), "tasks": planned,
        "original_GPU_coordinator_untouched": True, "GPU_action": False,
        "inputs_sha256": {
            str(path): sha256(path) for path in (
                args.campaign_plan, Path(__file__).resolve(), *sorted(script_paths),
            )
        },
    }
    registration = args.output_dir / "registration.json"
    write(registration, spec)
    with (args.output_dir / "collector.log").open("x") as stream:
        child = subprocess.Popen(
            ["taskset", "-c", "0", "nice", "-n", "19", sys.executable,
             str(Path(__file__).resolve()), "--watch", str(registration)],
            env={
                **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
            }, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({"registration": str(registration), "CPU_collector": causal.reservation.process(child.pid)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--campaign-plan", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.campaign_plan and args.output_dir:
        register(args)
    else:
        parser.error("provide a campaign plan and fresh CPU output directory")


if __name__ == "__main__":
    main()
