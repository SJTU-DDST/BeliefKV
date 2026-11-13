"""Attach the unstarted Full and run all five matched methods under one GPU lease."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

LABELS = ("full", "oracle", "learned", "thunderagent_acting_decay", "native_matched")
NAMES = ("Full", "Oracle", "R5", "ThunderAgent acting-decay", "Native SGLang")


def load(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command_with(command, replacements):
    result = list(command)
    for flag, value in replacements.items():
        result[result.index(flag) + 1] = str(value)
    return result


def baseline_stage(template, label, configuration, output, port):
    stage = deepcopy(template)
    prefix = Path(stage["registration"]).name.replace("_full_", "_" + label + "_")
    stage.update(
        registration=str(Path(stage["registration"]).with_name(prefix)),
        run_root=stage["run_root"].replace("_full_", "_" + label + "_"),
        port=port, configuration=str(configuration), review=str(output / (label + "_review")),
    )
    stage["command"] = command_with(stage["command"], {
        "--registration": stage["registration"], "--run-root": stage["run_root"],
        "--port": port, "--experiment-config": configuration, "--arms": label,
    })
    stage.pop("existing_watcher", None)
    return stage


def serving_ready(state, arm, alive):
    return state.get("status") == "running_" + arm and all(
        state.get(name) and alive(state[name]) for name in ("watcher", "server", "driver")
    )


def verify_serving_owner(owner, current, registration):
    if any(current.get(key) != owner.get(key) for key in ("pid", "start_ticks")):
        raise ValueError("serving owner identity changed")
    expected = ["--watch", str(registration)]
    argv = current.get("argv", [])
    if not any(argv[index:index + 2] == expected for index in range(len(argv) - 1)):
        raise ValueError("serving supervisor does not watch its registered plan")
    if owner.get("argv") and argv != owner["argv"]:
        raise ValueError("serving supervisor command changed")
    return current


def matching_work(proofs):
    if not proofs or "full" not in proofs:
        raise ValueError("Full execution proof is required")
    for label, proof in proofs.items():
        if proof["work"] != proofs["full"]["work"]:
            raise ValueError("complete measured native work changed: " + label)


def services(operator):
    sys.path.insert(0, str(operator))
    from scripts import run_causal_comparison as causal
    from scripts import run_arrival_work_ranking_comparison as ranking
    from scripts import finish_model_work_oracle as finish
    return causal, ranking, finish


def register(args):
    for name in (
        "execution_plan", "baselines_preparation", "output_dir", "device_state", "device_owner_file",
    ):
        setattr(args, name, getattr(args, name).resolve())
    previous = load(args.execution_plan)
    causal, ranking, finish = services(Path(previous["operator"]))
    finish.verify_pins(previous["inputs_sha256"])
    preparation = load(args.baselines_preparation)
    finish.verify_pins(preparation["inputs_sha256"])
    if preparation["source_execution_plan"] != str(args.execution_plan.resolve()):
        raise ValueError("baselines must bind the same current execution plan")
    full = load(previous["stages"]["full"]["registration"])
    current = load(Path(previous["stages"]["full"]["registration"]).with_suffix(".state.json"))
    if (
        not causal.reservation.alive(current["watcher"])
        or current.get("completed_arms")
        or current["run_root"] != previous["stages"]["full"]["run_root"]
        or full["code_commit"] != "8fbd746251b9863d5063f921dff01f108f16add6"
    ):
        raise ValueError("attach only the live unchanged Full")
    old_state = load(
        args.replace_coordinator_state.resolve() if args.replace_coordinator_state
        else args.execution_plan.parent / "state.json"
    )
    old_owner = old_state["process"]
    if not causal.reservation.alive(old_owner):
        raise ValueError("old coordinator must still own the pending campaign")
    replacing_controller = bool(args.replace_coordinator_state)
    if replacing_controller and (
        old_state.get("current_stage") != "full" or old_state.get("completed_stages")
        or old_state.get("GPU_registered_stages") != ["full"]
        or not any(Path(arg).name == Path(__file__).name for arg in old_owner["argv"])
    ):
        raise ValueError("replace only this CPU coordinator while its sole Full remains live")
    ranking.released()
    if (args.output_dir / "plan.json").exists():
        raise FileExistsError("preserve the existing continuous campaign")
    stages = deepcopy(previous["stages"])
    stages["full"]["existing_watcher"] = current["watcher"]
    for label, port in zip(LABELS[3:], (19044, 19045), strict=True):
        entry = preparation["configurations"][label]
        if digest(entry["path"]) != entry["sha256"]:
            raise ValueError("prepared baseline configuration changed")
        stages[label] = baseline_stage(
            previous["stages"]["full"], label, entry["path"], args.output_dir, port,
        )
    for label, stage in stages.items():
        if label != "full" and any(Path(stage[name]).exists() for name in ("registration", "run_root")):
            raise FileExistsError("later stage already has execution evidence: " + label)
    paths = [
        Path(__file__).resolve(), args.execution_plan, args.baselines_preparation,
        Path(previous["stages"]["full"]["registration"]),
    ]
    for flag in ("--gpu-smoke", "--sampling-calibration"):
        command = previous["stages"]["full"]["command"]
        paths.append(Path(command[command.index(flag) + 1]))
    pins = {**previous["inputs_sha256"], **preparation["inputs_sha256"]}
    pins.update({str(path.resolve()): digest(path) for path in paths})
    pins.update({stage["configuration"]: digest(stage["configuration"]) for stage in stages.values()})
    plan = {
        **previous, "stages": stages, "GPU_stage_order": list(LABELS),
        "previous_execution_plan": str(args.execution_plan.resolve()),
        "previous_coordinator": old_owner,
        "previous_coordinator_only_handoff": replacing_controller,
        "device_state": str(args.device_state.resolve()),
        "device_owner_file": str(args.device_owner_file.resolve()),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "all_five_methods_explicitly_requested": True,
        "workload_diagnostic_failure_retained_without_cancelling_matched_controls": True,
        "actual_work_capacity_drain_and_exit_checks_required": True,
        "inputs_sha256": pins, "goal_complete": False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = args.output_dir / "plan.json"
    write(plan_path, plan)
    with (args.output_dir / "campaign.log").open("x") as stream:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(plan_path)],
            cwd=previous["operator"], env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
    owner = causal.reservation.process(child.pid)
    write(args.output_dir / "campaign_owner.json", owner)
    print(json.dumps({"plan": str(plan_path), "watcher": owner}), flush=True)


def watch(plan_path):
    plan, output = load(plan_path), plan_path.parent
    causal, ranking, finish = services(Path(plan["operator"]))
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("keep the campaign owner nice0 and CUDA hidden")
    state = {
        "status": "taking_over_coordinator", "process": causal.reservation.process(os.getpid()),
        "GPU_stage_order": list(LABELS), "GPU_registered_stages": [],
        "completed_stages": [], "goal_complete": False, "performance_accepted": False,
    }
    proofs = {}

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        write(output / "state.json", state)

    def wait():
        ranking.released()
        lease = load(plan["device_state"])
        if lease["status"] != "held" or not causal.reservation.alive(lease["guard"]):
            raise RuntimeError("continuous GPU access guard is not held by a live process")
        if time.time() - datetime.fromisoformat(lease["checked_at"]).timestamp() > 15:
            raise RuntimeError("continuous GPU access guard heartbeat is stale")
        pulse()
        time.sleep(2)

    def cpu(name, arguments):
        with (output / (name + ".log")).open("x") as stream:
            child = subprocess.Popen(
                ["taskset", "-c", "0", "nice", "-n", "19", sys.executable, *map(str, arguments)],
                cwd=plan["operator"], env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            )
            state["CPU_child"] = causal.reservation.process(child.pid)
            while child.poll() is None:
                wait()
        state.pop("CPU_child", None)
        if child.returncode:
            raise RuntimeError("CPU execution review failed: " + name)

    def run_stage(label):
        stage = plan["stages"][label]
        state.update(current_stage=label, measured_completed=None, observed_arrivals=None)
        finish.verify_pins(plan["inputs_sha256"])
        ranking.released()
        owner = stage.get("existing_watcher")
        if owner is None:
            state["status"] = "waiting_resources_" + label
            while not causal.qualified.resources(stage["port"])["ready"]:
                wait()
            result = subprocess.run(
                stage["command"], cwd=plan["operator"], capture_output=True, text=True, timeout=240,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            )
            write(output / (label + "_registration_result.json"), {
                "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
            })
            if result.returncode:
                raise RuntimeError("GPU registration failed: " + label)
            owner = json.loads(result.stdout)["watcher"]
        state["serving_watcher"] = owner
        state["GPU_registered_stages"].append(label)
        registration = Path(stage["registration"])
        arm = load(stage["configuration"])["order"][0]
        deadline = None
        while True:
            if not causal.reservation.alive(owner):
                raise RuntimeError("serving supervisor exited before replay: " + label)
            serving_path = registration.with_suffix(".state.json")
            serving = load(serving_path) if serving_path.exists() else {}
            if serving:
                owner = verify_serving_owner(owner, serving.get("watcher", {}), registration)
                state["serving_watcher"] = owner
            if serving_ready(serving, arm, causal.reservation.alive):
                break
            status = serving.get("status", "")
            if status.startswith(("stopped", "completed", "interrupted")):
                raise RuntimeError("serving stopped before replay: " + str(serving.get("error")))
            waiting = status.startswith("waiting_")
            deadline = None if waiting else deadline or time.monotonic() + 1800
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("actual model startup timed out: " + label)
            state.update(status="waiting_actual_replay_" + label, serving_status=status)
            wait()
        comparison = load(registration)
        frozen = Path(comparison["execution_checkout"])
        observer_path = registration.with_name(
            registration.name.replace(".registration.json", ".cohort.registration.json")
        )
        if observer_path.exists():
            observed_registration = load(observer_path)
            if (
                observed_registration["comparison"] != str(registration)
                or observed_registration["cohort"] != plan["cohort"]
                or observed_registration["analysis_output"] != stage["review"]
            ):
                raise ValueError("existing fixed-cohort observer does not match")
            finish.verify_pins(observed_registration["inputs_sha256"])
            observer_owner = load(observer_path.with_suffix(".state.json"))["process"]
            if not causal.reservation.alive(observer_owner):
                raise RuntimeError("existing cohort observer exited unexpectedly")
        else:
            write(observer_path, {
                "schema_version": 1, "comparison": str(registration), "cohort": plan["cohort"],
                "execution_checkout": str(frozen), "code_commit": comparison["code_commit"],
                "passing_CPU_cases": comparison["passing_cpu_cases"], "poll_seconds": 2,
                "minimum_model_work_p80_p20": plan["minimum_model_work_p80_p20"],
                "output_dir": str(Path(stage["run_root"]) / "cohort_termination"),
                "analysis_output": stage["review"],
                "inputs_sha256": {str(path): digest(path) for path in (
                    registration, Path(plan["cohort"]), frozen / "scripts/stop_after_measured_cohort.py",
                    frozen / "scripts/verify_measured_execution.py",
                )},
                "GPU_followup_registered": False, "performance_accepted": False,
            })
            with observer_path.with_suffix(".log").open("x") as stream:
                child = subprocess.Popen(
                    ["taskset", "-c", "0", "nice", "-n", "19", sys.executable,
                     str(frozen / "scripts/stop_after_measured_cohort.py"), "--watch", str(observer_path)],
                    cwd=frozen, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                )
            observer_owner = causal.reservation.process(child.pid)
        state.update(status="waiting_fixed_18_" + label, cohort_observer=observer_owner)
        finish.wait_observer_publication(observer_path.with_suffix(".state.json"), observer_owner, pulse)
        while causal.reservation.alive(observer_owner):
            observed = load(observer_path.with_suffix(".state.json"))
            state.update(
                measured_completed=observed.get("measured_completed"),
                observed_arrivals=observed.get("observed_arrivals"),
            )
            wait()
        observed = load(observer_path.with_suffix(".state.json"))
        if observed["status"] != "measured_cohort_review_complete":
            raise RuntimeError("fixed-cohort execution/review failed: " + str(observed.get("error")))
        proof_path = output / (label + "_execution_proof.json")
        cpu(label + "_execution_proof", [
            "-c",
            "from pathlib import Path\n"
            "import sys\n"
            "from scripts.verify_measured_execution import verify_execution, review_matches_execution\n"
            "from scripts.run_causal_comparison import load\n"
            "from scripts.train_workflow_work import write\n"
            "proof=verify_execution(Path(sys.argv[1]),Path(sys.argv[2]))\n"
            "review_matches_execution(load(Path(sys.argv[3])),proof)\n"
            "write(Path(sys.argv[4]),proof)\n",
            registration, plan["cohort"], Path(stage["review"]) / "review.json", proof_path,
        ])
        proofs[label] = load(proof_path)
        matching_work(proofs)
        state.setdefault("workload_diagnostics", {})[label] = {
            "accepted_for_oracle_diagnostic": observed.get("accepted_for_oracle_diagnostic"),
            "failed_checks": observed.get("failed_checks"),
        }
        state["completed_stages"].append(label)
        state.update(status="completed_" + label)
        pulse()

    def terminate(signum, frame):
        raise KeyboardInterrupt(f"campaign received signal {signum}")

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        finish.verify_pins(plan["inputs_sha256"])
        write(plan["device_owner_file"], state["process"])
        pulse()
        old = plan["previous_coordinator"]
        if causal.reservation.alive(old):
            # A replacement must not run the old controller's serving-abort handler.
            handoff_signal = signal.SIGKILL if plan.get("previous_coordinator_only_handoff") else signal.SIGTERM
            os.kill(old["pid"], handoff_signal)
            deadline = time.monotonic() + 20
            while causal.reservation.alive(old):
                if time.monotonic() > deadline:
                    raise RuntimeError("previous coordinator did not exit")
                wait()
        with (Path(plan["previous_execution_plan"]).parent / "coordinator.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            write(output / "coordinator_handoff.json", {
                "old_coordinator_exited": True, "old_owner": old, "new_owner": state["process"],
                "Full_supervisor_preserved": plan["stages"]["full"]["existing_watcher"],
                "only_CPU_coordinator_replaced": plan.get("previous_coordinator_only_handoff", False),
                "GPU_stage_order": list(LABELS),
            })
            for label in LABELS:
                run_stage(label)
        state.update(status="all_five_matched_executions_verified")
        pulse()
        for label in LABELS[1:3]:
            from scripts.review_matched_work_ranking import evaluate_pair_cohorts
            result = evaluate_pair_cohorts(
                load(Path(plan["stages"]["full"]["review"]) / "cohort.json"),
                load(Path(plan["stages"][label]["review"]) / "cohort.json"),
                load(plan["configurations"][label])["comparison_contract"], minimum_jct_gain=.01,
            )
            write(output / (label + "_comparison.json"), {
                **result, "complete_measured_work_fresh_drain_and_actual_exit": True,
                "one_run_does_not_establish_repeatability": True, "GPU_action": False,
            })
        for label, script, flag in (
            ("thunderagent_acting_decay", "review_predictive_thunder_cohort.py", "--thunder"),
            ("native_matched", "review_predictive_native_cohort.py", "--native"),
        ):
            cpu(label + "_comparison", [
                Path(plan["operator"]) / "scripts" / script,
                "--predictive", plan["stages"]["learned"]["registration"],
                flag, plan["stages"][label]["registration"],
                "--predictive-review-dir", plan["stages"]["learned"]["review"],
                flag + "-review-dir", plan["stages"][label]["review"],
                "--cohort", plan["cohort"], "--output", output / (label + "_comparison.json"),
            ])
        cpu("figures", [
            Path(plan["operator"]) / "scripts/plot_workflow_cohort_comparison.py",
            *[value for label, name in zip(LABELS, NAMES, strict=True) for value in (
                "--report", name + "=" + str(Path(plan["stages"][label]["review"]) / "cohort.json"),
            )],
            "--output-dir", output / "figures",
        ])
        state.update(status="complete_five_method_comparison", measured_completed=18)
    except BaseException as error:
        state.update(status="stopped_preserve_five_method_comparison", error=repr(error))
        owner = state.get("serving_watcher")
        if owner and causal.reservation.alive(owner):
            os.kill(owner["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 360
            while causal.reservation.alive(owner) and time.monotonic() < deadline:
                pulse()
                time.sleep(2)
            state["serving_supervisor_still_alive"] = causal.reservation.alive(owner)
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        pulse()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--execution-plan", type=Path)
    parser.add_argument("--baselines-preparation", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--device-state", type=Path)
    parser.add_argument("--device-owner-file", type=Path)
    parser.add_argument("--replace-coordinator-state", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch.resolve())
    else:
        if any(getattr(args, name) is None for name in (
            "execution_plan", "baselines_preparation", "output_dir", "device_state", "device_owner_file",
        )):
            parser.error("registration requires the execution plan, baselines, output and live device lease")
        if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 0:
            raise ValueError("register with CUDA hidden and nice0")
        register(args)


if __name__ == "__main__":
    main()
