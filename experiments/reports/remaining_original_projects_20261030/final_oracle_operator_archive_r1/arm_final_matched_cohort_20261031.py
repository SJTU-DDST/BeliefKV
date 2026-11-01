"""Attach complete fixed-cohort termination to the final declared replay arm."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "comparison", "cohort", "cpu", "configuration"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    root = args.checkout.resolve()
    sys.path.insert(0, str(root))
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts import run_causal_comparison as causal
    import arm_capacity_combination_observers_20261031 as observer

    observer.ROOT = root
    main_root = Path("/home/longhao/experiment/BeliefKV")
    ops = main_root.parent / "ops"
    reports = main_root / "experiments/reports/remaining_original_projects_20261030"
    registration = ops / (args.name + "_measured_completion_20261031_r1.registration.json")
    state_path = ops / (args.name + "_observer_bootstrap_20261031_r1.state.json")
    if registration.exists() or state_path.exists() or (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve observers and use nice19 hidden-CUDA settings")
    state = {"status": "waiting_actual_replay", "process": causal.reservation.process(os.getpid()),
             "GPU_action": False, "performance_accepted": False}

    def publish():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(state_path, state)

    try:
        deadline = time.monotonic() + 1800.
        while True:
            if args.comparison.exists() and args.comparison.with_suffix(".state.json").exists():
                plan = causal.load(args.comparison)
                serving = causal.load(args.comparison.with_suffix(".state.json"))
                if serving["status"] == "running_" + plan["order"][0] and all(
                    causal.reservation.alive(serving.get(name))
                    for name in ("watcher", "server", "driver")
                ):
                    break
                if serving["status"].startswith(("stopped", "interrupted", "completed")):
                    raise RuntimeError("replay exited before fixed-cohort observer")
            if time.monotonic() >= deadline:
                raise TimeoutError("replay did not start within 30 minutes")
            publish()
            time.sleep(5.)
        cpu = causal.load(args.cpu)
        inputs = (
            args.comparison, args.configuration, args.cohort, args.cpu,
            Path(plan["workflow_arrivals_path"]),
            root / "scripts/stop_after_measured_cohort.py",
            root / "scripts/verify_measured_execution.py", Path(__file__),
        )
        cohort_plan = {
            "schema_version": 1,
            "purpose": "complete_fixed_measured_tails_then_cancel_only_background",
            "comparison": str(args.comparison), "cohort": str(args.cohort),
            "execution_checkout": str(root), "code_commit": plan["code_commit"],
            "passing_CPU_cases": cpu["unique_test_identities"],
            "minimum_model_work_p80_p20": 3., "poll_seconds": 2.,
            "output_dir": str(Path(plan["run_root"]) / "cohort_termination"),
            "analysis_output": str(reports / ("measured_cohort_" + args.name + "_r1")),
            "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
            "no_GPU_control_until_all_measured_workflows_complete": True,
            "GPU_followup_registered": False, "performance_accepted": False,
        }
        causal.reservation.write_json(registration, cohort_plan)
        process = observer.launch(registration, [
            sys.executable, str(root / "scripts/stop_after_measured_cohort.py"),
            "--watch", str(registration),
        ])
        state.update(status="fixed_cohort_observer_armed", cohort_observer=process)
    except BaseException as error:
        state.update(status="stopped_preserve_final_observer", error=repr(error))
    publish()
    print(json.dumps(state), flush=True)


if __name__ == "__main__":
    main()
