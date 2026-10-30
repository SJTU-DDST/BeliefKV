"""Run CPU mechanism audits only after the exact mixed pair and queue exit."""

import argparse
from datetime import datetime, timezone
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

ORDER = ("thunderagent_acting_decay", "full")


def validate_physical_scope(physical):
    if physical["handoff_events"] or (
        set(physical["prefetch_journal_commands_by_source"]) - {"join_ticket"}
    ):
        raise ValueError("user-disabled physical handoff or ordinary/tool prefetch was observed")


def pair_ready(queue, state):
    if state["status"] == "stopped_no_next_gpu_action":
        raise ValueError("preserve the failed mixed queue; no mechanism follow-up")
    live = causal.reservation.alive(state.get("watcher"))
    if state["status"] != "complete_pair_reviewed":
        if not live:
            raise ValueError("mixed queue exited without its complete pair review")
        return False
    if live:
        return False
    work = [complete_work(Path(queue["entries"][name]["comparison_registration"])) for name in ORDER]
    if any(row is None for row in work) or work[0] != work[1]:
        raise ValueError("mixed pair lacks equal complete work or actual owned exit")
    return True


def commands(queue, output):
    entries = queue["entries"]
    result = {}
    arms = {}
    for label, short in zip(ORDER, ("thunder", "full")):
        registration = entries[label]["comparison_registration"]
        arms[short] = Path(entries[label]["comparison_run"]) / label
        result[short + "_output"] = [
            "scripts/audit_mixed_output_prefix.py", "--registration", registration,
            "--output", str(output / (short + "_output.json")), "--complete",
        ]
        result[short + "_decode_gaps"] = [
            "scripts/audit_prefill_decode_gaps.py", "--registration", registration,
            "--output", str(output / (short + "_decode_gaps.json")), "--complete",
        ]
    full = entries["full"]["comparison_registration"]
    result["causal_jct"] = [
        "scripts/audit_causal_jct_paths.py", "--arm", "Thunder=" + str(arms["thunder"]),
        "--arm", "Full=" + str(arms["full"]), "--output", str(output / "causal_jct.json"),
    ]
    result["full_native_demand"] = [
        "scripts/audit_demand_prefill_pipeline.py", "--registration", full,
        "--output", str(output / "full_native_demand.json"),
    ]
    result["full_prefetch"] = [
        "scripts/audit_prefetch_lifecycle.py", "--arm", str(arms["full"]),
        "--output", str(output / "full_prefetch.json"), "--summary-only",
    ]
    result["full_siblings"] = [
        "scripts/audit_sibling_native_service.py", "--arm", str(arms["full"]),
        "--output", str(output / "full_siblings.json"),
    ]
    result["full_eos"] = [
        "scripts/audit_eos_trigger_lifecycle.py", "--registration", full,
        "--expected-receipts", str(arms["thunder"] / "replay/native_requests.jsonl"),
        "--output", str(output / "full_eos.json"),
    ]
    return result


def verify(plan):
    if git_source_state(ROOT) != plan["application"]:
        raise ValueError("frozen CPU mechanism reviewer changed")
    for name, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("CPU mechanism input changed: " + name)


def register(args):
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_dir.exists():
        raise ValueError("freeze committed CPU reviewer and preserve earlier outputs")
    queue = causal.load(args.queue)
    if queue["order"] != list(ORDER):
        raise ValueError("expected the declared matched mixed methods")
    state = causal.load(args.queue.with_suffix(".state.json"))
    pair_ready(queue, state)
    args.output_dir.mkdir(parents=True)
    actions = commands(queue, args.output_dir.resolve())
    sources = {
        args.queue.resolve(), Path(__file__).resolve(),
        ROOT / "scripts/audit_staged_native_restore.py",
        ROOT / "experiments/reports/join_blocked_layer_groups_20261028/handoff_ack_review.py",
    } | {ROOT / command[0] for command in actions.values()}
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "queue": str(args.queue.resolve()),
        "actions": actions,
        "inputs_sha256": {str(path): sha256_file(path) for path in sorted(sources)},
        "GPU_experiment_registered": False, "performance_accepted": False,
    }
    path = args.output_dir / "registration.json"
    causal.reservation.write_json(path, plan)
    with (args.output_dir / "watcher.log").open("x") as output:
        worker = subprocess.Popen(
            ["nice", "-n", "19", sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
                 "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
            start_new_session=True,
        )
    print({"registration": str(path), "watcher": causal.reservation.process(worker.pid)})


def watch(path):
    plan = causal.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve every CPU mechanism review attempt")
    state = {
        "status": "waiting_complete_pair_and_actual_exit",
        "watcher": causal.reservation.process(os.getpid()),
        "completed_actions": [], "GPU_experiment_registered": False,
        "performance_accepted": False,
    }
    try:
        verify(plan)
        while True:
            state["checked_at"] = causal.reservation.now().isoformat()
            causal.reservation.write_json(state_path, state)
            queue = causal.load(Path(plan["queue"]))
            queue_state = causal.load(Path(plan["queue"]).with_suffix(".state.json"))
            if pair_ready(queue, queue_state):
                break
            time.sleep(20.)
        verify(plan)
        for label, command in plan["actions"].items():
            state.update(status="reviewing_complete_pair_mechanisms", current_action=label)
            causal.reservation.write_json(state_path, state)
            completed = subprocess.run(
                [sys.executable, *command], cwd=ROOT, capture_output=True, text=True, timeout=600.,
            )
            causal.reservation.write_json(path.parent / (label + ".entry.json"), {
                "command": command, "returncode": completed.returncode,
                "stdout": completed.stdout, "stderr": completed.stderr,
            })
            if completed.returncode:
                raise ValueError("CPU mechanism audit failed: " + label)
            state["completed_actions"].append(label)
        native = causal.load(path.parent / "full_native_demand.json")
        physical = native["native_legacy_accounting_scope"]
        validate_physical_scope(physical)
        verify(plan)
        state["status"] = "complete_pair_mechanisms_reviewed"
    except BaseException as error:
        state.update(status="stopped_without_GPU_action", error=repr(error))
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.queue and args.output_dir:
        register(args)
    else:
        parser.error("require the exact queue and fresh output directory, or --watch")


if __name__ == "__main__":
    main()
