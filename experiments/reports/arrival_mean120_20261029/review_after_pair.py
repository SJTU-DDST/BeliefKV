"""Independently review the frozen GPU pair using the existing CPU plot env."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts import run_causal_comparison as causal

PLOT_PYTHON = Path("/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python")
ORDER = ("thunderagent_acting_decay", "full")


def verify(plan):
    if git_source_state(ROOT) != plan["application"]:
        raise ValueError("frozen CPU analysis sources changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("CPU review input changed: " + path)


def register(args):
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_root.exists():
        raise ValueError("use a clean committed analysis checkout and fresh output root")
    queue = causal.load(args.queue)
    state = causal.load(args.queue.with_suffix(".state.json"))
    if queue["order"] != list(ORDER) or not causal.reservation.alive(state["watcher"]):
        raise ValueError("bind the actual live frozen GPU queue")
    reader = Path(__file__).with_name("review_pair.py")
    checked = subprocess.run(
        [str(PLOT_PYTHON), "-c", "import matplotlib,httpx; print(matplotlib.__version__)"],
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
        capture_output=True, text=True, check=True,
    )
    args.output_root.mkdir(parents=True)
    path = args.output_root / "registration.json"
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "gpu_queue": str(args.queue.resolve()),
        "gpu_queue_watcher": state["watcher"], "entries": queue["entries"],
        "reader": str(reader), "plot_python": str(PLOT_PYTHON),
        "matplotlib_version": checked.stdout.strip(),
        "inputs_sha256": {
            str(source.resolve()): sha256_file(source) for source in (
                args.queue, reader, Path(__file__), PLOT_PYTHON.parent.parent / "pyvenv.cfg",
            )
        },
        "GPU_experiment_registered": False, "performance_accepted": False,
    }
    causal.reservation.write_json(path, plan)
    with (args.output_root / "watcher.log").open("x") as log:
        child = subprocess.Popen(
            ["nice", "-n", "19", sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21", "OMP_NUM_THREADS": "1"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({"registration": str(path), "watcher": causal.reservation.process(child.pid)}, indent=2))


def watch(path):
    plan = causal.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve previous CPU review attempts")
    state = {
        "status": "waiting_complete_gpu_pair",
        "watcher": causal.reservation.process(os.getpid()),
        "GPU_experiment_registered": False, "performance_accepted": False,
    }
    try:
        verify(plan)
        while True:
            ready = []
            for label in ORDER:
                registration = Path(plan["entries"][label]["registration"])
                if not registration.with_suffix(".state.json").exists():
                    ready.append(False)
                else:
                    ready.append(complete_work(registration) is not None)
            if all(ready):
                break
            if not causal.reservation.alive(plan["gpu_queue_watcher"]):
                raise ValueError("GPU queue exited before both complete successful comparisons")
            state.update(
                checked_at=causal.reservation.now().isoformat(),
                completed_gpu_arms=[label for label, done in zip(ORDER, ready) if done],
            )
            causal.reservation.write_json(state_path, state)
            time.sleep(20.)
        verify(plan)
        state["status"] = "reviewing_complete_gpu_pair"
        causal.reservation.write_json(state_path, state)
        command = [
            plan["plot_python"], plan["reader"],
            "--thunder", plan["entries"][ORDER[0]]["registration"],
            "--full", plan["entries"]["full"]["registration"],
            "--output-dir", str(path.parent / "complete_review"),
        ]
        with (path.parent / "reader.log").open("x") as log:
            subprocess.run(
                command, cwd=ROOT, check=True, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21", "OMP_NUM_THREADS": "1"},
            )
        verify(plan)
        state.update(status="complete_pair_reviewed", output=str(path.parent / "complete_review"))
    except BaseException as error:
        state.update(status="stopped_without_gpu_action", error=repr(error))
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.queue and args.output_root:
        register(args)
    else:
        parser.error("supply the live queue and a fresh output root, or a CPU review registration")


if __name__ == "__main__":
    main()
