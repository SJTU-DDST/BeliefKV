"""Qualify and run the matched 2048-token profiles sequentially."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import (
    complete_work, validate_cpu, validate_references,
)
from experiments.reports.prefill2048_mean120_20261029.prepare import BASES, ORDER, validate_profiles
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation

PREVIOUS = {
    ORDER[0]: OPS / "arrival_mean120_thunder_20261029_r1.registration.json",
    ORDER[1]: OPS / "arrival_mean120_full_20261029_r1.registration.json",
}
LAST = OPS / "hbm_dispatch_mean120_20261029_r2.registration.json"
PLOT_PYTHON = Path("/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python")


def validate_native_rebinding(current, qualified):
    if any(
        {key: value for key, value in left.items() if key not in omitted}
        != {key: value for key, value in right.items() if key not in omitted}
        for left, right, omitted in (
            (current, qualified, ("canonical_patch", "state_sha256")),
            (current["canonical_patch"], qualified["canonical_patch"], ("path",)),
        )
    ):
        raise ValueError("canonical path rebinding requires identical native bytes and metadata")


def method_complete(entry):
    position_path = Path(entry["position_registration"]).with_suffix(".state.json")
    comparison_path = Path(entry["comparison_registration"]).with_suffix(".state.json")
    if not position_path.exists():
        return None
    position = causal.load(position_path)
    if position["status"] == "stopped_for_investigation":
        raise ValueError("position qualification failed; retain this attempt")
    if not comparison_path.exists():
        if not causal.reservation.alive(position.get("watcher")):
            raise ValueError("position supervisor exited without registering its full comparison")
        return None
    comparison = causal.load(comparison_path)
    if comparison["status"] == "collected_performance_review_pending":
        if causal.reservation.alive(position.get("watcher")):
            return None
        return complete_work(Path(entry["comparison_registration"]))
    if not causal.reservation.alive(comparison.get("watcher")):
        raise ValueError("formal comparison exited without complete drained work")
    return None


def verify(plan):
    authorization = check_reservation()
    if (
        git_source_state(ROOT) != plan["application"]
        or authorization["hold_sha256"] != plan["authorization"]["hold_sha256"]
    ):
        raise ValueError("frozen queue or GPU authorization changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("queued input changed: " + path)


def register(args):
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("GPU launch queues must inherit nice0")
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_dir.exists():
        raise ValueError("require clean committed sources and a fresh queue directory")
    authorization = check_reservation()
    cpu = validate_cpu(args.cpu_evidence)
    specifications = {label: causal.load(HERE / (label + ".json")) for label in ORDER}
    validate_profiles(specifications, {label: causal.load(path) for label, path in BASES.items()})
    previous = {label: causal.load(path) for label, path in PREVIOUS.items()}
    validate_references(previous)
    work = {label: complete_work(path) for label, path in PREVIOUS.items()}
    latest_work = complete_work(LAST)
    if latest_work is None or any(value is None or value != latest_work for value in work.values()):
        raise ValueError("all predecessors must complete the same full work and actual cleanup")
    args.output_dir.mkdir(parents=True)
    inputs = [
        args.cpu_evidence, Path(cpu["report"]["path"]), Path(cpu["source_manifest"]["path"]),
        LAST, *PREVIOUS.values(), *BASES.values(),
        Path(specifications["full"]["workflow_arrivals"]["path"]),
    ]
    entries, commands = {}, {}
    for ordinal, label in enumerate(ORDER):
        old = previous[label]
        engine = Path(old["engine_checkout"])
        native = git_source_state(engine, canonical_patch=engine_patch(ROOT, old["engine_tree"]))
        qualified = causal.load(Path(old["engine_evidence"]["sources"]))
        validate_native_rebinding(native, qualified)
        native_path = args.output_dir / (label + ".native_sources.json")
        causal.reservation.write_json(native_path, native)
        evidence = causal.engine_evidence(engine, Path(old["engine_evidence"]["report"]), native_path)
        short, suffix = ("thunder", "common") if ordinal == 0 else ("full", "full")
        position = OPS / f"prefill2048_position_20261029_r1_{suffix}.registration.json"
        position_run = MAIN / f"experiments/raw/prefill2048_position_20261029_r1_{suffix}"
        comparison = OPS / f"prefill2048_mean120_{short}_20261029_r1.registration.json"
        comparison_run = MAIN / f"experiments/raw/prefill2048_mean120_20261029_r1_{suffix}"
        port = args.port + ordinal
        if any(path.exists() for path in (
            position, position.with_suffix(".state.json"), position.with_suffix(".log"),
            comparison, comparison.with_suffix(".state.json"), comparison.with_suffix(".log"),
            position_run, comparison_run, Path(f"/tmp/bkv-causal-{port}.sock"),
        )):
            raise FileExistsError("preserve prior attempts; use fresh paths and sockets")
        specification = HERE / (label + ".json")
        command = [
            sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py"),
            "--source-registration", str(OPS / "v230_locobench_parallel_retry_source_20261026.registration.json"),
            "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
            "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
            "--engine", str(engine), "--engine-report", evidence["report"],
            "--engine-sources", str(native_path), "--registration", str(position),
            "--run-root", str(position_run), "--comparison-registration", str(comparison),
            "--comparison-root", str(comparison_run), "--port", str(port),
            "--experiment-config", str(specification),
        ]
        commands[label] = command
        entries[label] = {
            "position_registration": str(position), "position_run": str(position_run),
            "comparison_registration": str(comparison), "comparison_run": str(comparison_run),
            "port": port,
        }
        inputs.extend((
            specification, native_path, Path(evidence["report"]),
            Path(old["engine_evidence"]["sources"]),
        ))
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "authorization": authorization, "order": list(ORDER),
        "entries": entries, "commands": commands,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "native_capture_repeated": False, "automatic_retry_after_failure": False,
        "performance_accepted": False,
    }
    path = args.output_dir / "registration.json"
    causal.reservation.write_json(path, plan)
    verify(plan)
    with (args.output_dir / "watcher.log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            start_new_session=True,
        )
    print({"registration": str(path), "watcher": causal.reservation.process(watcher.pid)})


def watch(path):
    plan = causal.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve every previous queue attempt")
    state = {
        "status": "waiting_resources", "watcher": causal.reservation.process(os.getpid()),
        "completed_arms": [], "performance_accepted": False,
    }

    def pulse():
        verify(plan)
        state["checked_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)

    try:
        reference_work = None
        for label in plan["order"]:
            entry = plan["entries"][label]
            state.update(status="waiting_resources", current_arm=label)
            while not causal.qualified.resources(entry["port"])["ready"]:
                pulse()
                time.sleep(10.)
            pulse()
            completed = subprocess.run(
                plan["commands"][label], cwd=ROOT, capture_output=True, text=True, timeout=180.,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            )
            causal.reservation.write_json(path.parent / (label + ".entry.json"), {
                "command": plan["commands"][label], "returncode": completed.returncode,
                "stdout": completed.stdout, "stderr": completed.stderr,
            })
            if completed.returncode:
                raise RuntimeError("position registration refused; do not start another arm")
            state["status"] = "waiting_position_then_complete_formal_work"
            missing_state_checks = 0
            while True:
                pulse()
                if not Path(entry["position_registration"]).with_suffix(".state.json").exists():
                    missing_state_checks += 1
                    if missing_state_checks > 3:
                        raise ValueError("position supervisor did not publish its initial state")
                work = method_complete(entry)
                if work is not None:
                    if reference_work is not None and work != reference_work:
                        raise ValueError("complete paired work differs")
                    reference_work = work
                    break
                time.sleep(10.)
            state["completed_arms"].append(label)
        pulse()
        state["status"] = "reviewing_complete_pair"
        causal.reservation.write_json(state_path, state)
        command = [
            "nice", "-n", "19", str(PLOT_PYTHON),
            str(ROOT / "experiments/reports/arrival_mean120_20261029/review_pair.py"),
            "--thunder", plan["entries"][ORDER[0]]["comparison_registration"],
            "--full", plan["entries"][ORDER[1]]["comparison_registration"],
            "--output-dir", str(path.parent / "complete_review"),
        ]
        with (path.parent / "reader.log").open("x") as output:
            subprocess.run(
                command, cwd=ROOT, check=True, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1"},
            )
        verify(plan)
        state["status"] = "complete_pair_reviewed"
    except BaseException as error:
        state.update(status="stopped_no_next_gpu_action", error=repr(error))
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--port", type=int, default=18881)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.cpu_evidence and args.output_dir and 1 <= args.port <= 65534:
        register(args)
    else:
        parser.error("require current CPU evidence and a fresh queue directory, or --watch")


if __name__ == "__main__":
    main()
