"""Hold only the CPU coordinator once one specified GPU stage actually replays."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_continuous_matched_campaign as campaign


def same_process(expected, actual):
    return bool(
        expected and actual and actual.get("state") not in ("Z", "X")
        and all(expected.get(key) == actual.get(key) for key in ("pid", "start_ticks"))
    )


def ready_for_hold(state, serving, stage, process, cohort_registration):
    if state.get("status") != "waiting_fixed_18_" + stage or state.get("current_stage") != stage:
        return False
    observer = state.get("cohort_observer")
    if not observer:
        return False
    actual_observer = process(observer["pid"])
    if not same_process(observer, actual_observer):
        return False
    expected = ["--watch", str(cohort_registration)]
    argv = actual_observer.get("argv", [])
    if not any(argv[index:index + 2] == expected for index in range(len(argv) - 1)):
        return False
    arm = "full" if stage in campaign.LABELS[:3] else stage
    return serving.get("status") == "running_" + arm and all(
        same_process(serving.get(name), process(serving[name]["pid"]))
        for name in ("watcher", "server", "driver") if serving.get(name)
    ) and all(serving.get(name) for name in ("watcher", "server", "driver"))


def watch(registration):
    spec = campaign.load(registration)
    if (
        campaign.digest(Path(__file__).resolve()) != spec["source_sha256"]
        or campaign.digest(spec["plan"]) != spec["plan_sha256"]
    ):
        raise ValueError("hold observer source or campaign plan changed")
    plan = campaign.load(spec["plan"])
    causal, _ranking, _finish = campaign.services(Path(plan["operator"]))
    process = causal.reservation.process
    stage, owner = spec["stage"], spec["coordinator"]
    stage_registration = Path(plan["stages"][stage]["registration"])
    cohort_registration = stage_registration.with_name(stage_registration.name.replace(
        ".registration.json", ".cohort.registration.json",
    ))
    output = registration.parent / "state.json"
    state = {"status": "waiting_target_replay", "process": process(os.getpid()), **spec}
    deadline = time.monotonic() + 3 * 3600
    while True:
        actual = process(owner["pid"])
        if not same_process(owner, actual):
            raise RuntimeError("registered CPU coordinator exited or changed identity")
        current = campaign.load(Path(spec["plan"]).parent / "state.json")
        if campaign.LABELS.index(current["current_stage"]) > campaign.LABELS.index(stage):
            raise RuntimeError("target stage was passed; preserve the actual campaign state")
        serving_path = stage_registration.with_suffix(".state.json")
        serving = campaign.load(serving_path) if serving_path.exists() else {}
        if ready_for_hold(current, serving, stage, process, cohort_registration):
            if not same_process(owner, process(owner["pid"])):
                raise RuntimeError("coordinator identity changed immediately before hold")
            os.kill(owner["pid"], signal.SIGSTOP)
            stop_deadline = time.monotonic() + 2.
            while True:
                held = process(owner["pid"])
                if not same_process(owner, held):
                    raise RuntimeError("coordinator identity changed while observing stop")
                if held["state"] in ("T", "t"):
                    break
                if time.monotonic() >= stop_deadline:
                    raise RuntimeError("CPU coordinator stop was not observed")
                time.sleep(.02)
            state.update(
                status="coordinator_held_GPU_replay_continues", held_coordinator=held,
                serving=serving, independent_cohort_observer=current["cohort_observer"],
                goal_paused=False, GPU_or_serving_signal_sent=False,
            )
            state["checked_at"] = datetime.now(timezone.utc).isoformat()
            campaign.write(output, state)
            return
        if time.monotonic() > deadline:
            raise TimeoutError("target replay did not start within the bounded observation")
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        campaign.write(output, state)
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--stage", choices=campaign.LABELS[1:])
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch.resolve())
        return
    if not args.plan or not args.stage or not args.output_dir:
        parser.error("provide a live plan, target stage and fresh output directory")
    plan_path, output = args.plan.resolve(), args.output_dir.resolve()
    plan = campaign.load(plan_path)
    causal, _ranking, _finish = campaign.services(Path(plan["operator"]))
    state = campaign.load(plan_path.parent / "state.json")
    owner = state["process"]
    if not same_process(owner, causal.reservation.process(owner["pid"])):
        raise ValueError("attach only to the actual registered CPU coordinator")
    output.mkdir(parents=True, exist_ok=False)
    registration = output / "registration.json"
    campaign.write(registration, {
        "plan": str(plan_path), "stage": args.stage, "coordinator": owner,
        "only_CPU_coordinator_may_be_signalled": True,
        "source_sha256": campaign.digest(Path(__file__).resolve()),
        "plan_sha256": campaign.digest(plan_path),
    })
    with (output / "watcher.log").open("x") as stream:
        child = subprocess.Popen(
            ["taskset", "-c", "0", "nice", "-n", "19", sys.executable,
             str(Path(__file__).resolve()), "--watch", str(registration)],
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"}, cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({"registration": str(registration), "watcher": causal.reservation.process(child.pid)}))


if __name__ == "__main__":
    main()
