"""Finish the last Oracle pair, enforce the stop gate, then run matched native."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--cpu", type=Path, required=True)
    args = parser.parse_args()
    root = args.checkout.resolve()
    sys.path.insert(0, str(root))
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_comparison as causal
    from scripts.verify_measured_execution import verify_execution

    main_root = Path("/home/longhao/experiment/BeliefKV")
    ops = main_root.parent / "ops"
    reports = main_root / "experiments/reports/remaining_original_projects_20261030"
    traffic = reports / "final_oracle_original82_traffic_r1"
    prepared = reports / "final82_completed_control_oracle_r1"
    results = reports / "final82_oracle_and_selected_native_r1"
    plan_path = ops / "final_oracle_native_20261031_r1.plan.json"
    state_path = ops / "final_oracle_native_20261031_r1.state.json"
    control_name = "workflow_final82_initial12_mean70_predictive_control"
    oracle_name = "workflow_final82_initial12_mean70_capacity_oracle"
    reference_position = ops / "workflow_original69_initial12_mean70_predictive_control_position_20261031_r1.registration.json"
    source82 = ops / "workflow_final_oracle_original82_source_20261031_r1.registration.json"
    cohort82 = traffic / "analysis_cohort.json"
    helpers = [
        ops / name for name in (
            "start_final_matched_arm_20261031.py", "arm_final_matched_cohort_20261031.py",
            "prepare_final82_calibrated_oracle_20261031.py", "prepare_final_matched_native_20261031.py",
            "arm_capacity_combination_observers_20261031.py", "plot_workflow_cohort_comparison_20261031.py",
        )
    ]
    if plan_path.exists() or state_path.exists() or results.exists() or (
        os.getpriority(os.PRIO_PROCESS, 0) != 0 or git_source_state(root)["dirty"]
    ):
        raise ValueError("preserve the final coordinator and keep future GPU owners nice0")
    pins = {str(path.resolve()): sha256_file(path) for path in (
        args.cpu, source82, traffic / "full.json", cohort82, traffic / "arrivals.json",
        traffic / "preparation.json", *helpers, Path(__file__),
    )}
    causal.reservation.write_json(plan_path, {
        "purpose": "last_new_workflow_Oracle_then_exact_selected_workload_native",
        "execution_checkout": str(root), "inputs_sha256": pins,
        "GPU_order": ["already_started_final82_Full", "final82_v6_Oracle", "selected_workload_native"],
        "no_concurrent_GPU_experiments": True,
        "gate": {"mean_JCT_reduction_fraction": .05, "output_throughput_fraction": .98},
        "fallback": "completed_original56_initial12_mean70_nonranking_Full",
        "no_workload_or_policy_grid_search_after_pair": True,
        "fresh_hold_resources_and_actual_exit_before_each_GPU_job": True,
        "handoff_or_other_optimization_GPU_jobs_authorized_here": False,
        "performance_accepted": False,
    })
    state = {
        "status": "waiting_complete_final82_nonranking_control",
        "process": causal.reservation.process(os.getpid()), "performance_accepted": False,
    }

    def publish():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(state_path, state)

    def check_pins():
        for filename, digest in pins.items():
            if sha256_file(Path(filename)) != digest:
                raise ValueError("final coordinator input changed: " + filename)

    def registration(name):
        return ops / (name + "_20261031_r1.registration.json")

    def review_dir(name):
        return reports / ("measured_cohort_" + name + "_r1")

    def wait_complete(name, cohort):
        deadline = time.monotonic() + 4 * 3600.
        observer_path = ops / (name + "_measured_completion_20261031_r1.registration.state.json")
        last = 0.
        while True:
            check_pins()
            if observer_path.exists():
                observed = causal.load(observer_path)
                state["current_measured_completed"] = observed.get("measured_completed")
                if observed["status"] == "measured_cohort_review_complete":
                    return verify_execution(registration(name), cohort)
                if observed["status"].startswith("stopped"):
                    raise RuntimeError("fixed-cohort review failed: " + str(observed.get("error")))
            serving_path = registration(name).with_suffix(".state.json")
            if serving_path.exists():
                serving = causal.load(serving_path)
                state["current_serving_status"] = serving["status"]
                if serving["status"].startswith(("stopped", "interrupted")) and (
                    not observer_path.exists()
                    or causal.load(observer_path)["status"] != "stopping_completed_measured_cohort"
                ):
                    certificate = Path(causal.load(registration(name))["run_root"]) / "cohort_termination/completion.json"
                    if not certificate.exists() and "measured" not in serving.get("reason", ""):
                        # The cohort watcher can be in its bounded shutdown
                        # before its certificate is written. It remains the
                        # authority for distinguishing completion and failure.
                        if not observer_path.exists() or not causal.reservation.alive(
                            causal.load(observer_path).get("process")
                        ):
                            raise RuntimeError("serving exited without a live completion observer")
            if time.monotonic() >= deadline:
                raise TimeoutError("the declared arm did not complete its measured cohort within four hours")
            if time.monotonic() - last >= 30.:
                publish()
                last = time.monotonic()
            time.sleep(5.)

    def run(command, logfile, *, cpu=True):
        check_pins()
        env = os.environ.copy()
        if cpu:
            env.update(CUDA_VISIBLE_DEVICES="99999", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
            command = ["nice", "-n", "19", *command]
        with logfile.open("x") as log:
            result = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError("preserve failed command: " + str(logfile))

    def start(name, config, source, cohort, port):
        run([
            sys.executable, str(ops / "start_final_matched_arm_20261031.py"),
            "--checkout", str(root), "--configuration", str(config),
            "--source", str(source), "--cohort", str(cohort),
            "--cpu", str(args.cpu.resolve()), "--reference-position", str(reference_position),
            "--name", name, "--port", str(port),
        ], results / (name + "_start.log"), cpu=False)

    try:
        publish()
        control = wait_complete(control_name, cohort82)
        results.mkdir(parents=True, exist_ok=False)
        state["status"] = "preparing_last_matching_v6_Oracle"
        publish()
        run([
            sys.executable, str(ops / "prepare_final82_calibrated_oracle_20261031.py"),
            "--checkout", str(root),
        ], results / "oracle_preparation.log")
        state["status"] = "starting_last_matching_v6_Oracle"
        publish()
        start(oracle_name, prepared / "full.json", source82, cohort82, 18954)
        state["status"] = "waiting_complete_last_v6_Oracle"
        publish()
        oracle = wait_complete(oracle_name, cohort82)
        if oracle["work"] != control["work"]:
            raise ValueError("last Oracle pair changed complete measured work")
        run([
            sys.executable, str(root / "scripts/review_matched_work_ranking.py"),
            "--control", str(registration(control_name)), "--oracle", str(registration(oracle_name)),
            "--control-review-dir", str(review_dir(control_name)),
            "--oracle-review-dir", str(review_dir(oracle_name)),
            "--arrivals", str(traffic / "arrivals.json"), "--cohort", str(cohort82),
            "--pair-kind", "calibrated_combination", "--output", str(results / "oracle_comparison.json"),
        ], results / "oracle_comparison.log")
        comparison = causal.load(results / "oracle_comparison.json")
        promising = all(comparison["checks"][name] for name in (
            "fixed_measured_mean_JCT_gain", "common_window_output_throughput_noninferior",
        ))
        if promising:
            selected, cohort, predictive_review = (
                registration(oracle_name), cohort82, review_dir(oracle_name)
            )
            selected_name = "original82_oracle"
        else:
            selected = ops / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json"
            cohort = reports / "sustained56_initial12_mean70_r1/analysis_cohort.json"
            predictive_review = reports / "measured_cohort_complete_toolaware_initial12_mean70_control_r1"
            selected_name = "original56_nonranking"
        state.update(
            status="preparing_selected_native",
            Oracle_direction_promising=promising,
            workflow_size_scheduling_paused=not promising,
            selected_predictive=str(selected), selected_cohort=str(cohort),
            oracle_mean_JCT_gain_percent=comparison["groups"]["measured"]["mean_JCT_reduction_percent"],
            oracle_output_throughput_gain_percent=comparison["common_window_output_throughput"]["gain_percent"],
        )
        publish()
        native_prepared = results / ("native_" + selected_name + "_preparation")
        run([
            sys.executable, str(ops / "prepare_final_matched_native_20261031.py"),
            "--checkout", str(root), "--predictive", str(selected),
            "--cohort", str(cohort), "--output-dir", str(native_prepared),
        ], results / "native_preparation.log")
        native_config = native_prepared / "native.json"
        native_name = "workflow_final_selected_" + selected_name + "_native"
        source = Path(causal.load(native_config)["source_registration"])
        state.update(status="starting_selected_native", native_name=native_name)
        publish()
        start(native_name, native_config, source, cohort, 18956)
        state["status"] = "waiting_complete_selected_native"
        publish()
        wait_complete(native_name, cohort)
        run([
            sys.executable, str(root / "scripts/review_predictive_native_cohort.py"),
            "--predictive", str(selected), "--native", str(registration(native_name)),
            "--predictive-review-dir", str(predictive_review),
            "--native-review-dir", str(review_dir(native_name)),
            "--cohort", str(cohort), "--output", str(results / "native_comparison.json"),
        ], results / "native_comparison.log")
        native_result = causal.load(results / "native_comparison.json")
        for stem, reference, candidate, reference_label, candidate_label in (
            ("oracle", review_dir(control_name), review_dir(oracle_name), "Predictive control", "Oracle v6"),
            ("native", review_dir(native_name), predictive_review, "Native SGLang",
             "Predictive Oracle" if promising else "Predictive"),
        ):
            run([
                str(ops / "cpu_plotting_20261027/bin/python"),
                str(ops / "plot_workflow_cohort_comparison_20261031.py"),
                "--reference", str(reference / "cohort.json"),
                "--candidate", str(candidate / "cohort.json"),
                "--reference-label", reference_label, "--candidate-label", candidate_label,
                "--output-dir", str(results / (stem + "_figures")),
            ], results / (stem + "_figures.log"))
        state.update(
            status="complete_final_Oracle_and_selected_native",
            native_mean_JCT_gain_percent=native_result["groups"]["measured"]["mean_JCT_reduction_percent"],
            native_output_throughput_gain_percent=native_result["common_window_output_throughput"]["gain_percent"],
            complete_measured_work_and_actual_exits_verified=True,
            no_more_workload_or_policy_search=True,
        )
    except BaseException as error:
        state.update(status="stopped_preserve_final_comparisons", error=repr(error))
    publish()
    print(json.dumps(state), flush=True)


if __name__ == "__main__":
    main()
