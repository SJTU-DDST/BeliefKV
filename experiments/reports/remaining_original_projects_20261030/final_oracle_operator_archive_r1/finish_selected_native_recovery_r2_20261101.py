"""Adopt the running native; preserve both stopped CPU followups."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-final-followup-a013dbd7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
RESULTS = REPORTS / "final82_oracle_and_selected_native_r1"
RECOVERY = REPORTS / "selected_native_namespace_recovery_r1"
NAME = "workflow_final_selected_original56_nonranking_native"
REGISTRATION = OPS / (NAME + "_20261031_r1.registration.json")
POSITION = OPS / (NAME + "_position_20261031_r1.registration.json")
OBSERVER = OPS / (NAME + "_measured_completion_20261031_r1.registration.state.json")
STATE = OPS / "selected_native_after_recovery_20261101_r2.state.json"
PLAN = OPS / "selected_native_after_recovery_20261101_r2.plan.json"
FAILED_FOLLOWUP = OPS / "selected_native_after_recovery_20261101_r1.state.json"
PREDICTIVE = OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json"
COHORT = REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json"
PREDICTIVE_REVIEW = REPORTS / "measured_cohort_complete_toolaware_initial12_mean70_control_r1"
NATIVE_REVIEW = REPORTS / ("measured_cohort_" + NAME + "_r1")
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.verify_measured_execution import verify_execution


def alive(handle):
    return (
        isinstance(handle, dict)
        and {"pid", "start_ticks"}.issubset(handle)
        and causal.reservation.alive(handle)
    )


def main():
    if (
        STATE.exists() or PLAN.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve CPU followups and use hidden-CUDA nice19")
    old_state = OPS / "final_oracle_native_20261031_r1.state.json"
    failed = causal.load(old_state)
    failed_followup = causal.load(FAILED_FOLLOWUP)
    recovery = causal.load(RECOVERY / "state.json")
    formal_state_path = REGISTRATION.with_suffix(".state.json")
    formal = causal.load(formal_state_path) if formal_state_path.exists() else {}
    if (
        failed["status"] != "stopped_preserve_final_comparisons"
        or alive(failed["process"])
        or failed["workflow_size_scheduling_paused"] is not True
        or failed["selected_predictive"] != str(PREDICTIVE)
        or failed_followup["status"] != "stopped_preserve_native_followup"
        or failed_followup["error"] != 'TypeError("\'NoneType\' object is not subscriptable")'
        or alive(failed_followup["process"])
        or recovery["status"] != "selected_native_position_registered"
        or formal.get("status") != "running_native_matched"
        or not all(alive(formal.get(name)) for name in ("watcher", "server", "driver"))
    ):
        raise ValueError("adopt only the actual selected native recovery")
    paths = (
        old_state, FAILED_FOLLOWUP, REGISTRATION,
        OPS / "finish_selected_native_recovery_20261101.py",
        RESULTS / "oracle_comparison.json", RECOVERY / "plan.json",
        RECOVERY / "state.json", POSITION, PREDICTIVE, COHORT,
        PREDICTIVE_REVIEW / "cohort.json", Path(__file__),
        OPS / "plot_workflow_cohort_comparison_20261031.py",
        ROOT / "scripts/review_predictive_native_cohort.py",
    )
    pins = {str(path): sha256_file(path) for path in paths}
    causal.reservation.write_json(PLAN, {
        "purpose": "finish_already_registered_selected_native_only",
        "native_registration": str(REGISTRATION),
        "selected_predictive": str(PREDICTIVE), "cohort": str(COHORT),
        "source_checkout": str(ROOT), "inputs_sha256": pins,
        "original_failed_coordinator_state_preserved": str(old_state),
        "failed_CPU_followup_state_preserved": str(FAILED_FOLLOWUP),
        "repair": "missing startup handles are not live processes; no GPU restart",
        "workflow_size_scheduling_paused": True,
        "GPU_jobs_launched_by_this_followup": [],
        "performance_accepted": False,
    })
    state = {
        "status": "waiting_selected_native_recovery",
        "process": causal.reservation.process(os.getpid()),
        "workflow_size_scheduling_paused": True,
        "selected_predictive": str(PREDICTIVE),
        "native_registration": str(REGISTRATION), "performance_accepted": False,
    }

    def publish():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(STATE, state)

    def check_pins():
        for filename, digest in pins.items():
            if sha256_file(Path(filename)) != digest:
                raise ValueError("native followup input changed: " + filename)

    def run(command, log):
        check_pins()
        with log.open("x") as output:
            result = subprocess.run(
                command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                     "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
            )
        if result.returncode:
            raise RuntimeError("preserve failed CPU postprocessing: " + str(log))

    def plot(stem, reference, candidate, reference_label, candidate_label):
        run([
            str(OPS / "cpu_plotting_20261027/bin/python"),
            str(OPS / "plot_workflow_cohort_comparison_20261031.py"),
            "--reference", str(reference / "cohort.json"),
            "--candidate", str(candidate / "cohort.json"),
            "--reference-label", reference_label, "--candidate-label", candidate_label,
            "--output-dir", str(RESULTS / (stem + "_figures")),
        ], RESULTS / (stem + "_figures.log"))

    try:
        publish()
        deadline, last_publish = time.monotonic() + 4 * 3600., 0.
        while True:
            check_pins()
            if OBSERVER.exists():
                observed = causal.load(OBSERVER)
                state["measured_completed"] = observed.get("measured_completed")
                state["cohort_observer_status"] = observed["status"]
                state["cohort_observer_actual_alive"] = alive(observed.get("process"))
                if observed["status"] == "measured_cohort_review_complete":
                    break
                if observed["status"].startswith("stopped"):
                    raise RuntimeError("preserve native cohort failure: " + str(observed.get("error")))
            position_state = POSITION.with_suffix(".state.json")
            if position_state.exists():
                position = causal.load(position_state)
                state["position_status"] = position["status"]
                state["position_watcher_actual_alive"] = alive(position.get("watcher"))
                if position["status"] == "stopped_for_investigation":
                    raise RuntimeError("preserve native position failure: " + str(position.get("error")))
            formal_state = REGISTRATION.with_suffix(".state.json")
            if formal_state.exists():
                serving = causal.load(formal_state)
                state["serving_status"] = serving["status"]
                state["actual_serving_handles_alive"] = {
                    name: alive(serving.get(name))
                    for name in ("watcher", "server", "driver")
                }
                if serving["status"].startswith(("stopped", "interrupted")):
                    run_root = Path(causal.load(REGISTRATION)["run_root"])
                    certificate = run_root / "cohort_termination/completion.json"
                    stopping = OBSERVER.exists() and (
                        causal.load(OBSERVER)["status"] == "stopping_completed_measured_cohort"
                        and alive(causal.load(OBSERVER).get("process"))
                    )
                    if not certificate.exists() and not stopping:
                        raise RuntimeError("native serving exited without measured completion evidence")
            if time.monotonic() >= deadline:
                raise TimeoutError("the selected native did not complete within the declared four-hour followup")
            if time.monotonic() - last_publish >= 30.:
                publish()
                last_publish = time.monotonic()
            time.sleep(5.)
        native, predictive = (
            verify_execution(path, COHORT) for path in (REGISTRATION, PREDICTIVE)
        )
        if native["work"] != predictive["work"]:
            raise ValueError("completed native and predictive measured work differ")
        state["status"] = "reviewing_completed_selected_native"
        publish()
        run([
            sys.executable, str(ROOT / "scripts/review_predictive_native_cohort.py"),
            "--predictive", str(PREDICTIVE), "--native", str(REGISTRATION),
            "--predictive-review-dir", str(PREDICTIVE_REVIEW),
            "--native-review-dir", str(NATIVE_REVIEW),
            "--cohort", str(COHORT), "--output", str(RESULTS / "native_comparison.json"),
        ], RESULTS / "native_comparison.log")
        plot("native", NATIVE_REVIEW, PREDICTIVE_REVIEW, "Native SGLang", "Predictive")
        comparison = causal.load(RESULTS / "native_comparison.json")
        state.update(
            status="complete_selected_native_after_namespace_recovery",
            complete_measured_work_and_actual_exits_verified=True,
            native_mean_JCT_gain_percent=comparison["groups"]["measured"]["mean_JCT_reduction_percent"],
            native_output_throughput_gain_percent=comparison["common_window_output_throughput"]["gain_percent"],
            no_more_workload_or_policy_search=True,
        )
    except BaseException as error:
        state.update(status="stopped_preserve_native_followup", error=repr(error))
    publish()


if __name__ == "__main__":
    main()
