"""Finish native, then run exactly one qualified CALL-only matched candidate."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-foreground-call-a73fdcb7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "foreground_call_matched_result_r1"
NAME = "workflow_original56_foreground_call"
NATIVE_STATE = OPS / "selected_native_after_recovery_20261101_r2.state.json"
NATIVE = OPS / "workflow_final_selected_original56_nonranking_native_20261031_r1.registration.json"
CONTROL = OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json"
COHORT = REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json"
CONFIG = REPORTS / "foreground_call_candidate_prepare_r1/full.json"
CPU = REPORTS / "foreground_call_bound_cpu_r1/evidence.json"
CANDIDATE = OPS / (NAME + "_20261031_r1.registration.json")
OBSERVER = OPS / (NAME + "_measured_completion_20261031_r1.registration.state.json")
STATE = OPS / "native_then_foreground_call_20261101_r1.state.json"
PLAN = OPS / "native_then_foreground_call_20261101_r1.plan.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.verify_measured_execution import verify_execution


def alive(handle):
    return (
        isinstance(handle, dict) and {"pid", "start_ticks"}.issubset(handle)
        and causal.reservation.alive(handle)
    )


def main():
    if STATE.exists() or PLAN.exists() or HERE.exists() or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("preserve attempts and retain nice0 GPU child ownership")
    native = causal.load(NATIVE_STATE)
    if native["status"] != "waiting_selected_native_recovery" or not alive(native["process"]):
        raise ValueError("adopt only the actual selected native followup")
    paths = (
        Path(__file__), CONFIG, CPU, CONTROL, COHORT,
        REPORTS / "foreground_call_bound_cpu_r1/clean_freeze_r1.json",
        OPS / "start_final_matched_arm_20261031.py",
        OPS / "arm_final_matched_cohort_20261031.py",
        OPS / "review_foreground_call_candidate_20261101.py",
        OPS / "audit_foreground_call_capacity_20261101.py",
        OPS / "plot_workflow_cohort_comparison_20261031.py",
    )
    pins = {str(path): sha256_file(path) for path in paths}
    HERE.mkdir(parents=True, exist_ok=False)
    causal.reservation.write_json(PLAN, {
        "purpose": "selected_native_then_one_CALL_coverage_repair_only",
        "candidate_configuration": str(CONFIG), "selected_nonranking_Full": str(CONTROL),
        "cohort": str(COHORT), "native_first": str(NATIVE),
        "source_checkout": str(ROOT), "inputs_sha256": pins,
        "no_Oracle_workload_or_parameter_search": True,
        "maximum_new_GPU_comparisons": 1, "no_automatic_GPU_retry": True,
        "performance_accepted": False,
    })
    state = {
        "status": "waiting_selected_native_completion",
        "process": causal.reservation.process(os.getpid()),
        "candidate_registration": str(CANDIDATE), "performance_accepted": False,
    }

    def publish():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(STATE, state)

    def check_pins():
        if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
            raise ValueError("a declared candidate input changed")

    def execute(command, path, *, cpu=False):
        check_pins()
        if cpu:
            command = ["nice", "-n", "19", "taskset", "-c", "0", *command]
        with path.open("x") as log:
            result = subprocess.run(
                command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, **({"CUDA_VISIBLE_DEVICES": "99999",
                     "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"} if cpu else {})},
            )
        if result.returncode:
            raise RuntimeError("preserve failed entry or analysis: " + str(path))

    try:
        publish()
        deadline = time.monotonic() + 4 * 3600.
        while True:
            check_pins()
            native = causal.load(NATIVE_STATE)
            state["native_followup_status"] = native["status"]
            state["native_measured_completed"] = native.get("measured_completed")
            if native["status"] == "complete_selected_native_after_namespace_recovery":
                break
            if native["status"].startswith("stopped") or not alive(native.get("process")):
                raise RuntimeError("preserve native followup failure before candidate")
            if time.monotonic() >= deadline:
                raise TimeoutError("selected native completion deadline exceeded; no GPU started")
            publish()
            time.sleep(10.)
        verify_execution(NATIVE, COHORT)
        state.update(status="starting_one_foreground_CALL_candidate", native_complete_and_owned_exit_verified=True)
        publish()
        execute([
            sys.executable, str(OPS / "start_final_matched_arm_20261031.py"),
            "--checkout", str(ROOT), "--configuration", str(CONFIG),
            "--source", str(OPS / "workflow_sustained_original56_source_20261030_r1.registration.json"),
            "--cohort", str(COHORT), "--cpu", str(CPU),
            "--reference-position", str(
                OPS / "workflow_final_selected_original56_nonranking_native_position_20261031_r1.registration.json"
            ), "--name", NAME, "--port", "18958",
        ], HERE / "candidate_entry.log")
        state["status"] = "waiting_foreground_CALL_position_and_measured_cohort"
        publish()
        position_state = OPS / (NAME + "_position_20261031_r1.registration.state.json")
        while True:
            check_pins()
            if OBSERVER.exists():
                observer = causal.load(OBSERVER)
                state["candidate_measured_completed"] = observer.get("measured_completed")
                state["candidate_observer_status"] = observer["status"]
                state["candidate_observer_alive"] = alive(observer.get("process"))
                if observer["status"] == "measured_cohort_review_complete":
                    break
                if observer["status"].startswith("stopped"):
                    raise RuntimeError("preserve candidate cohort failure: " + str(observer.get("error")))
            if position_state.exists():
                position = causal.load(position_state)
                state["position_status"] = position["status"]
                if position["status"] == "stopped_for_investigation":
                    raise RuntimeError("preserve candidate position failure: " + str(position.get("error")))
            formal_path = CANDIDATE.with_suffix(".state.json")
            if formal_path.exists():
                formal = causal.load(formal_path)
                state["candidate_status"] = formal["status"]
                state["candidate_live_handles"] = {
                    name: alive(formal.get(name)) for name in ("watcher", "server", "driver")
                }
                if formal["status"].startswith(("stopped", "interrupted")):
                    certificate = Path(causal.load(CANDIDATE)["run_root"]) / "cohort_termination/completion.json"
                    stopping = OBSERVER.exists() and causal.load(OBSERVER)["status"] == "stopping_completed_measured_cohort"
                    if not certificate.exists() and not stopping:
                        raise RuntimeError("candidate exited without fixed-cohort completion")
            if time.monotonic() >= deadline:
                raise TimeoutError("candidate followup deadline exceeded; owned observer remains authoritative")
            publish()
            time.sleep(10.)
        verify_execution(CANDIDATE, COHORT)
        candidate_review = REPORTS / ("measured_cohort_" + NAME + "_r1")
        control_review = REPORTS / "measured_cohort_complete_toolaware_initial12_mean70_control_r1"
        execute([
            sys.executable, str(OPS / "review_foreground_call_candidate_20261101.py"),
            "--checkout", str(ROOT), "--control", str(CONTROL), "--candidate", str(CANDIDATE),
            "--control-review", str(control_review), "--candidate-review", str(candidate_review),
            "--cohort", str(COHORT), "--output", str(HERE / "comparison.json"),
        ], HERE / "comparison.log", cpu=True)
        execute([
            str(OPS / "cpu_plotting_20261027/bin/python"),
            str(OPS / "plot_workflow_cohort_comparison_20261031.py"),
            "--reference", str(control_review / "cohort.json"),
            "--candidate", str(candidate_review / "cohort.json"),
            "--reference-label", "Predictive control", "--candidate-label", "Foreground CALL reclaim",
            "--output-dir", str(HERE / "figures"),
        ], HERE / "figures.log", cpu=True)
        result = causal.load(HERE / "comparison.json")
        state.update(
            status="complete_native_and_one_foreground_CALL_candidate",
            complete_measured_work_and_actual_exits_verified=True,
            candidate_mean_JCT_gain_percent=result["groups"]["measured"]["mean_JCT_reduction_percent"],
            candidate_output_gain_percent=result["common_window_output_throughput"]["gain_percent"],
            engineering_promotion_signal=result["engineering_promotion_signal"],
            no_further_GPU_jobs_queued=True,
        )
    except BaseException as error:
        state.update(status="stopped_preserve_foreground_CALL_followup", error=repr(error))
    publish()


if __name__ == "__main__":
    main()
