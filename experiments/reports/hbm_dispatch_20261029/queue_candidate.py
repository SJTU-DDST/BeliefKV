"""Queue a changed Full policy after complete work without a same-policy resume."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work, validate_cpu
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation


def replace_option(command, name, value):
    if command.count(name) != 1:
        raise ValueError("require one exact command option: " + name)
    command[command.index(name) + 1] = str(value)


def build_command(template, output):
    command = list(template)
    registration = Path(command[command.index("--registration") + 1])
    index = command.index("--after-registration")
    del command[index:index + 2]
    command[1] = str(ROOT / "scripts/run_causal_comparison.py")
    replace_option(command, "--experiment-config", ROOT / "experiments/reports/hbm_dispatch_20261029/full.json")
    replace_option(command, "--registration", registration.with_name("hbm_dispatch_mean120_20261029_r2.registration.json"))
    replace_option(command, "--engine-sources", output / "native_sources.json")
    if command[command.index("--arms") + 1] != "full":
        raise ValueError("queue only the changed Full candidate")
    return command


def verify(plan):
    check_reservation()
    if git_source_state(ROOT) != plan["application"]:
        raise ValueError("frozen queue sources changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("queued input changed: " + path)


def register(args):
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("candidate launch queue requires the shared nice0 setting")
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_root.exists():
        raise ValueError("require a clean frozen queue and fresh output")
    check_reservation()
    template = causal.load(args.template)["command"]
    cpu = Path(template[template.index("--cpu-report") + 1]).with_name("evidence.json")
    validate_cpu(cpu)
    old_sources = Path(template[template.index("--engine-sources") + 1])
    old = causal.load(old_sources)
    native = git_source_state(
        Path(template[template.index("--engine") + 1]),
        canonical_patch=engine_patch(ROOT, old["canonical_patch"]["actual_tree"]),
    )
    if any(
        {key: value for key, value in left.items() if key not in omitted}
        != {key: value for key, value in right.items() if key not in omitted}
        for left, right, omitted in (
            (native, old, ("canonical_patch", "state_sha256")),
            (native["canonical_patch"], old["canonical_patch"], ("path",)),
        )
    ):
        raise ValueError("native bytes differ from the qualified exact engine")
    args.output_root.mkdir(parents=True)
    native_path = args.output_root / "native_sources.json"
    causal.reservation.write_json(native_path, native)
    command = build_command(template, args.output_root)
    predecessor = Path(template[template.index("--after-registration") + 1])
    inputs = (
        args.template, cpu, Path(causal.load(cpu)["report"]["path"]),
        Path(causal.load(cpu)["source_manifest"]["path"]), native_path, predecessor,
        Path(command[command.index("--experiment-config") + 1]),
    )
    path = args.output_root / "registration.json"
    causal.reservation.write_json(path, {
        "registered_at": datetime.now(timezone.utc).isoformat(), "application": application,
        "predecessor": str(predecessor), "command": command,
        "inputs_sha256": {str(source.resolve()): sha256_file(source) for source in inputs},
        "candidate_registration": command[command.index("--registration") + 1],
        "status": "cpu_queue_only_no_gpu_child",
    })
    with (args.output_root / "watcher.log").open("x") as log:
        watcher = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print({"registration": str(path), "watcher": causal.reservation.process(watcher.pid)})


def launch_if_complete(plan, output):
    if complete_work(Path(plan["predecessor"])) is None:
        return False
    verify(plan)
    completed = subprocess.run(
        plan["command"], cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
        capture_output=True, text=True,
    )
    causal.reservation.write_json(output / "candidate_entry.json", {
        "command": plan["command"], "returncode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
    })
    if completed.returncode:
        raise RuntimeError("candidate registration refused; preserve the entry")
    reviewer = ROOT / "experiments/reports/hbm_dispatch_20261029/review_after_candidate.py"
    command = [
        sys.executable, str(reviewer),
        "--thunder", str(Path(plan["predecessor"]).with_name("arrival_mean120_thunder_20261029_r1.registration.json")),
        "--original", plan["predecessor"], "--candidate", plan["candidate_registration"],
        "--output-root", str(output.parent / "review_worker_r1"),
    ]
    reviewed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    causal.reservation.write_json(output / "reviewer_entry.json", {
        "command": command, "returncode": reviewed.returncode,
        "stdout": reviewed.stdout, "stderr": reviewed.stderr,
    })
    if reviewed.returncode:
        raise RuntimeError("candidate is registered; separate CPU reviewer failed")
    return True


def watch(path):
    plan = causal.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve previous queue state")
    state = {
        "status": "waiting_original_full_owned_exit",
        "watcher": causal.reservation.process(os.getpid()), "GPU_experiment_started": False,
    }
    try:
        while True:
            verify(plan)
            state["checked_at"] = causal.reservation.now().isoformat()
            causal.reservation.write_json(state_path, state)
            if launch_if_complete(plan, path.parent):
                state["status"] = "candidate_and_cpu_reviewer_registered"
                break
            predecessor = causal.load(Path(plan["predecessor"]).with_suffix(".state.json"))
            if not causal.reservation.alive(predecessor.get("watcher")):
                raise ValueError("predecessor exited without complete drained work")
            time.sleep(10.)
    except BaseException as error:
        state.update(status="stopped_no_new_gpu_action", error=repr(error))
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("template", "output-root", "watch"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.template and args.output_root:
        register(args)
    else:
        parser.error("require a registration template and fresh output root, or --watch")


if __name__ == "__main__":
    main()
