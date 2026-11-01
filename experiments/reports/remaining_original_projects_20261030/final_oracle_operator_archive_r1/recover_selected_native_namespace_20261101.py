"""Recover only the unallocated native entry with a unique calibration path."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-final-followup-a013dbd7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "selected_native_namespace_recovery_r1"
NAME = "workflow_final_selected_original56_nonranking_native"
ENTRY_DIR = REPORTS / ("gpu_entry_" + NAME + "_r1")
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation


def main():
    if HERE.exists() or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("preserve recovery attempts and retain nice0 GPU ownership")
    entry_path = ENTRY_DIR / "entry.json"
    failed_state_path = OPS / "final_oracle_native_20261031_r1.state.json"
    entry, failed = causal.load(entry_path), causal.load(failed_state_path)
    if (
        failed["status"] != "stopped_preserve_final_comparisons"
        or failed["workflow_size_scheduling_paused"] is not True
        or failed["Oracle_direction_promising"] is not False
        or failed["native_name"] != NAME
        or causal.reservation.alive(failed["process"])
        or entry["returncode"] == 0
        or "worktree" not in entry["stderr"]
        or "exit status 128" not in entry["stderr"]
    ):
        raise ValueError("adopt only the identified pre-GPU namespace collision")
    if git_source_state(ROOT) != entry["application"]:
        raise ValueError("the qualified native entry operator changed")
    for filename, digest in entry["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("the failed entry's qualified inputs changed: " + filename)
    command = list(entry["command"])
    old_run = Path(command[command.index("--run-root") + 1])
    registration = Path(command[command.index("--registration") + 1])
    comparison = Path(command[command.index("--comparison-registration") + 1])
    formal_run = Path(command[command.index("--comparison-root") + 1])
    if any(path.exists() for path in (registration, comparison, old_run, formal_run)):
        raise ValueError("a registered or partially allocated GPU run cannot use this recovery")
    collision = MAIN.parent / ".worktrees" / (
        "beliefkv-r1-position-" + entry["application"]["commit"][:12]
    )
    revision = subprocess.check_output(
        ["git", "-C", str(collision), "rev-parse", "HEAD"], text=True,
    ).strip()
    if revision != entry["application"]["commit"]:
        raise ValueError("the existing calibration checkout has another identity")
    new_run = old_run.with_name(old_run.name + "_native56")
    label = new_run.name.rsplit("_", 1)[-1]
    new_checkout = MAIN.parent / ".worktrees" / (
        "beliefkv-" + label + "-position-" + revision[:12]
    )
    if new_run.exists() or new_checkout.exists():
        raise ValueError("the recovery calibration namespace is not fresh")
    command[command.index("--run-root") + 1] = str(new_run)
    port = int(command[command.index("--port") + 1])
    authorization, resources = check_reservation(), causal.qualified.resources(port)
    if not resources["ready"] or Path(f"/tmp/bkv-causal-{port}.sock").exists():
        raise ValueError("the native recovery resources or socket are unavailable")
    pins = {
        str(path): sha256_file(path) for path in (
            entry_path, failed_state_path, Path(__file__),
            OPS / "arm_final_matched_cohort_20261031.py",
        )
    }
    HERE.mkdir(parents=True, exist_ok=False)
    causal.reservation.write_json(HERE / "plan.json", {
        "purpose": "continue_only_selected_native_after_preallocation_namespace_collision",
        "old_coordinator_failure_preserved": True,
        "failed_entry_preserved": str(entry_path),
        "previous_run_root": str(old_run), "recovered_run_root": str(new_run),
        "only_registration_argument_changed": "--run-root",
        "original_command": entry["command"], "recovered_command": command,
        "inputs_sha256": pins, "authorization": authorization, "resources": resources,
        "no_Oracle_Full_or_Thunder_reexecution": True,
        "no_new_policy_or_workload": True, "performance_accepted": False,
    })
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    with (HERE / "registration.log").open("x") as log:
        log.write(completed.stdout)
        log.write(completed.stderr)
    if completed.returncode:
        causal.reservation.write_json(HERE / "state.json", {
            "status": "stopped_preserve_native_recovery",
            "registration_returncode": completed.returncode,
            "no_automatic_retry": True, "performance_accepted": False,
        })
        raise RuntimeError("preserve the failed native recovery registration")
    registered = causal.load(registration)
    if Path(registered["run_root"]) != new_run:
        raise ValueError("the actual calibration registered a different recovered namespace")
    submitted = json.loads(completed.stdout)
    if not causal.reservation.alive(submitted["watcher"]):
        raise ValueError("the actually registered calibration watcher is not alive")
    observer_command = [
        "nice", "-n", "19", sys.executable,
        str(OPS / "arm_final_matched_cohort_20261031.py"),
        "--checkout", str(ROOT), "--comparison", str(comparison),
        "--cohort", str(REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json"),
        "--cpu", str(REPORTS / "final_oracle_native_entry_bound_cpu_r3/evidence.json"),
        "--configuration", str(ENTRY_DIR / "configuration.json"), "--name", NAME,
    ]
    with (HERE / "observer_bootstrap.log").open("x") as log:
        observer = subprocess.Popen(
            observer_command, cwd=ROOT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                 "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    causal.reservation.write_json(HERE / "state.json", {
        "status": "selected_native_position_registered",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "position_registration": str(registration), "comparison_registration": str(comparison),
        "position_watcher": submitted["watcher"],
        "observer_bootstrap": causal.reservation.process(observer.pid),
        "original_coordinator_stopped_state_preserved": True,
        "performance_accepted": False,
    })
    print({"position_registration": str(registration), "comparison_registration": str(comparison),
           "observer_bootstrap_pid": observer.pid}, flush=True)


if __name__ == "__main__":
    main()
