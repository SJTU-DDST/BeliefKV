"""Finish one existing model-work oracle on CPU without restarting its GPU work."""

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

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.runtime.workflow_work_oracle import (
    COMBINATION_WORK_KIND, CONTINUOUS_COMBINATION_WORK_KIND, CONTINUOUS_PRESSURE_WORK_KIND,
    TOOL_BAND_PRESSURE_ORDER,
)
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint, remaining_work_weight
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import inspect
from scripts.review_matched_work_ranking import evaluate_pair_cohorts
from scripts.review_predictive_thunder_cohort import matching_serving_traffic
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def require_cpu():
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("use CPU0, nice19 and hidden CUDA for this finisher")


def verify_pins(pins):
    for name, digest in pins.items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("CPU finish input changed: " + name)


def wait_observer_publication(path, owner, pulse, *, timeout_s=30.):
    deadline = time.monotonic() + timeout_s
    while not path.is_file():
        if not causal.reservation.alive(owner):
            raise ValueError("cohort observer exited before its first state publication")
        if time.monotonic() >= deadline:
            raise TimeoutError("cohort observer did not publish its first state within the bounded wait")
        pulse()
        time.sleep(.1)


def verify_same_objective(learned, candidate, model, truth, *, allow_tool_band_pressure=False):
    allowed = {
        prefix + name for prefix in ("", "BELIEFKV_")
        for name in ("WORKFLOW_WORK_SOURCE", "WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256")
    }
    old, new = learned["common_environment"], candidate["common_environment"]
    modes = {
        COMBINATION_WORK_KIND: "three_bins", CONTINUOUS_COMBINATION_WORK_KIND: "continuous",
    }
    pressure = truth.get("kind") == CONTINUOUS_PRESSURE_WORK_KIND
    if allow_tool_band_pressure:
        modes[CONTINUOUS_PRESSURE_WORK_KIND] = "continuous"
    order = "r5_combination_and_tool_band_pressure" if pressure else "r5_combination_only"
    if (
        learned.get("order") != ["full"] or candidate.get("order") != ["full"]
        or old.keys() != new.keys()
        or any(old[name] != new[name] for name in old.keys() - allowed)
        or old.get("WORKFLOW_WORK_SOURCE") != "learned"
        or new.get("WORKFLOW_WORK_SOURCE") != "oracle"
        or new.get("WORKFLOW_COMBINATION_ADMISSION") != "1"
        or any(learned.get(name) != candidate.get(name) for name in (
            "source_registration", "workflow_arrivals", "private_analysis_cohort",
            "tool_timing_overlay", "stream_response_metrics", "workload_acceptance",
        ))
        or any(new[name] != new.get("BELIEFKV_" + name) for name in (
            "WORKFLOW_WORK_SOURCE", "WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256",
        ))
        or truth.get("kind") not in modes
        or truth.get("combination_work_readout") != modes.get(truth.get("kind"))
        or truth.get("admission_order") != order
        or (pressure and (
            truth.get("pressure_work_order") != TOOL_BAND_PRESSURE_ORDER
            or old.get("AGENT_PRESSURE_PAUSE_POLICY") != "workflow_tool_aware"
        ))
        or (not pressure and "pressure_work_order" in truth)
        or truth.get("remaining_work_ranking_enabled") is not True
        or truth.get("work_thresholds") != model.get("work_thresholds")
        or truth.get("admission_cost_profile") != model.get("admission_cost_profile")
        or truth.get("free_dispatch_coalescing_window_ms") != model.get(
            "free_dispatch_coalescing_window_ms"
        )
    ):
        raise ValueError("the diagnostic differs from learned R5 beyond the declared work provider")
    if modes[truth["kind"]] == "continuous":
        reference = truth.get("combination_work_reference") or {}
        if (
            reference.get("forecast_sha256") != forecast_fingerprint(model)
            or reference.get("work_thresholds") != model.get("work_thresholds")
            or reference.get("reference_and_initial_priors_use_training_labels_only") is not True
            or truth.get("combination_reference_source_model_sha256") != old.get("WORKFLOW_WORK_MODEL_SHA256")
        ):
            raise ValueError("continuous work diagnostic differs from its retained training reference")
        remaining_work_weight(0., reference.get("reference_work"))


def comparison_scope(truth):
    pressure = truth["kind"] == CONTINUOUS_PRESSURE_WORK_KIND
    return {
        "same_R5_selector_and_three_bin_utility_verified": truth["kind"] == COMBINATION_WORK_KIND,
        "same_R5_selector_and_declared_work_readout_verified": True,
        "same_noncombination_policy_verified": not pressure,
        "work_used_for_tool_band_pressure": pressure,
        "pressure_work_order": truth.get("pressure_work_order"),
        "not_a_pure_prediction_error_comparison": truth["kind"] != COMBINATION_WORK_KIND,
    }


def method_label(truth):
    return {
        COMBINATION_WORK_KIND: "Model-work Oracle",
        CONTINUOUS_COMBINATION_WORK_KIND: "Continuous-work Oracle",
        CONTINUOUS_PRESSURE_WORK_KIND: "Tool-band Pressure Oracle",
    }[truth["kind"]]


def register(execution_path, output):
    require_cpu()
    execution_path, output = execution_path.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError("preserve previous CPU finish registrations")
    execution = causal.load(execution_path)
    previous = causal.load(execution_path.parent / "execution_state.json")
    reg_path = Path(execution["registration"])
    serving = causal.load(reg_path.with_suffix(".state.json"))
    observer_path = reg_path.with_name(reg_path.name.replace(
        ".registration.json", ".cohort.registration.json",
    ))
    observer = causal.load(observer_path.with_suffix(".state.json"))
    previous_process = previous["process"]
    owners = [serving[name] for name in ("watcher", "server", "driver")]
    if (
        previous.get("status") != "waiting_fixed_18_model_work_oracle"
        or serving.get("status") != "running_full"
        or not all(causal.reservation.alive(owner) for owner in (
            previous_process, observer["process"], *owners,
        ))
        or previous_process["pid"] in {owner["pid"] for owner in owners + [observer["process"]]}
        or not any(Path(arg).name == "start_r5_model_work_oracle_retry_20261108.py"
                   for arg in previous_process["argv"])
    ):
        raise ValueError("replace only the actual live CPU coordinator after its cohort observer is armed")
    verify_pins(execution["inputs_sha256"])
    paths = [
        execution_path, reg_path, observer_path, Path(execution["cohort"]),
        Path(__file__).resolve(), ROOT / "scripts/plot_workflow_cohort_comparison.py",
        ROOT / "scripts/audit_workflow_combination_choices.py",
        ROOT / "beliefkv/policy/workflow_combination.py",
        ROOT / "scripts/verify_measured_execution.py",
        ROOT / "scripts/review_matched_work_ranking.py",
        ROOT / "scripts/audit_measured_request_interactivity.py",
    ]
    output.mkdir(parents=True, exist_ok=False)
    plan_path = output / "plan.json"
    write(plan_path, {
        "execution_plan": str(execution_path), "analysis_root": str(ROOT),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "superseded_CPU_coordinator": previous_process, "cohort_observer": observer["process"],
        "unchanged_serving_owners": owners, "GPU_action": False,
        "reason": "recognize the explicit Model-work Oracle label and preserve complete CPU analysis",
        "inputs_sha256": {str(path): sha256_file(path) for path in paths},
    })
    with (output / "watch.log").open("x") as stream:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(plan_path)],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
    if not causal.reservation.alive(previous_process):
        raise ValueError("the CPU coordinator exited before the explicit replacement")
    os.kill(previous_process["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 10.
    while causal.reservation.alive(previous_process) and time.monotonic() < deadline:
        time.sleep(.1)
    if causal.reservation.alive(previous_process) or not all(
        causal.reservation.alive(owner) for owner in owners + [observer["process"]]
    ):
        raise ValueError("CPU replacement failed or changed an unrelated live owner")
    write(output / "supersession.json", {
        "previous_CPU_coordinator_exited": True,
        "all_serving_and_cohort_observer_identities_still_alive": True,
        "previous_execution_state_preserved": True, "GPU_action": False,
        "new_CPU_process": causal.reservation.process(child.pid),
    })
    print(json.dumps({"plan": str(plan_path), "process": causal.reservation.process(child.pid),
                      "GPU_action": False}))


def watch(plan_path):
    require_cpu()
    plan, output = causal.load(plan_path), plan_path.parent
    execution = causal.load(Path(plan["execution_plan"]))
    reg_path, cohort_path = Path(execution["registration"]), Path(execution["cohort"])
    observer_path = reg_path.with_name(reg_path.name.replace(
        ".registration.json", ".cohort.registration.json",
    )).with_suffix(".state.json")
    state_path = output / "state.json"
    state = {
        "status": "waiting_existing_cohort_observer",
        "process": causal.reservation.process(os.getpid()), "GPU_action": False,
        "goal_complete": False,
    }

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(state_path, state)

    def cpu(name, arguments):
        verify_pins(plan["inputs_sha256"])
        with (output / (name + ".log")).open("x") as stream:
            child = subprocess.Popen(
                [sys.executable, *map(str, arguments)], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            )
            state.update(status="running_" + name, CPU_child=causal.reservation.process(child.pid))
            while child.poll() is None:
                pulse()
                time.sleep(5.)
        if child.returncode:
            raise RuntimeError("preserve failed CPU finish: " + name)

    try:
        while plan.get("superseded_CPU_coordinator") and causal.reservation.alive(
            plan["superseded_CPU_coordinator"]
        ):
            pulse()
            time.sleep(1.)
        wait_observer_publication(observer_path, plan["cohort_observer"], pulse)
        while causal.reservation.alive(plan["cohort_observer"]):
            observed = causal.load(observer_path)
            state.update(measured_completed=observed.get("measured_completed"),
                         observed_arrivals=observed.get("observed_arrivals"))
            pulse()
            time.sleep(10.)
        observed = causal.load(observer_path)
        if observed["status"] != "measured_cohort_review_complete":
            raise ValueError("existing cohort observer did not complete: " + str(observed.get("error")))
        verify_pins(plan["inputs_sha256"])
        reg = causal.load(reg_path)
        causal.comparison.verify_sources(reg)
        candidate_config = causal.load(Path(reg["experiment_configuration"]))
        artifact_path = Path(candidate_config["common_environment"]["WORKFLOW_WORK_MODEL"])
        artifact = causal.load(artifact_path)
        continuous = artifact.get("combination_work_readout") == "continuous"
        pressure = execution.get("pressure_work_order") == TOOL_BAND_PRESSURE_ORDER
        learned_config = causal.load(Path(causal.load(
            Path(execution["reference"]["learned"]["registration"])
        )["experiment_configuration"]))
        model_path = Path(learned_config["common_environment"]["WORKFLOW_WORK_MODEL"])
        verify_same_objective(
            learned_config, candidate_config, causal.load(model_path), artifact,
            allow_tool_band_pressure=pressure,
        )
        if pressure != (artifact.get("kind") == CONTINUOUS_PRESSURE_WORK_KIND):
            raise ValueError("execution pressure scope differs from its explicit oracle kind")
        candidate_execution = verify_execution(reg_path, cohort_path)
        candidate_directory = Path(causal.load(
            reg_path.with_name(reg_path.name.replace(".registration.json", ".cohort.registration.json"))
        )["analysis_output"])
        candidate_report = causal.load(candidate_directory / "cohort.json")
        review_matches_execution(causal.load(candidate_directory / "review.json"), candidate_execution)
        results = {}
        input_paths = [
            plan_path, reg_path, cohort_path, artifact_path, model_path,
            candidate_directory / "review.json", candidate_directory / "cohort.json",
        ]
        for label, reference in execution["reference"].items():
            old_path = Path(reference["registration"])
            old = causal.load(old_path)
            causal.comparison.verify_sources(old)
            matching_serving_traffic(old, reg)
            complete = verify_execution(old_path, cohort_path)
            if complete["work"] != candidate_execution["work"]:
                raise ValueError("complete measured native work differs from the reference")
            old_directory = Path(reference["review"])
            review_matches_execution(causal.load(old_directory / "review.json"), complete)
            results[label] = evaluate_pair_cohorts(
                causal.load(old_directory / "cohort.json"), candidate_report,
                candidate_config["comparison_contract"], minimum_jct_gain=.01,
            )
            input_paths.extend((old_path, old_directory / "cohort.json", old_directory / "review.json"))
        write(output / "comparison.json", {
            "results": results, "complete_measured_work_fresh_drain_and_actual_exit": True,
            **comparison_scope(artifact),
            "work_readout": artifact["combination_work_readout"],
            "continuous_training_reference": artifact.get("combination_work_reference"),
            "one_new_run_does_not_establish_repeatability": True,
            "GPU_action": False, "goal_complete": False,
            "inputs_sha256": {str(path): sha256_file(path) for path in input_paths},
        })
        profile, reader = artifact["admission_cost_profile"], SealedRecords()
        arm = Path(reg["run_root"]) / "full"
        if sha256_file(ROOT / "beliefkv/policy/workflow_combination.py") != reg["source_sha256"][
            "beliefkv/policy/workflow_combination.py"
        ]:
            raise ValueError("analysis policy differs from actual serving")
        choices = inspect(
            list(reader(arm / "opportunities/admission_opportunities.jsonl")),
            reader(arm / "server/runtime_audit.jsonl"),
            CombinationCosts(**profile["costs"]), profile["identity"],
            allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        )
        write(output / "complete_choices.json", {
            **choices, "input_jsonl_prefixes": reader.prefixes,
            "inputs_sha256": {str(path): sha256_file(path) for path in input_paths},
            "complete_measured_execution_verified": True, "GPU_action": False,
        })
        cpu("interactivity", [
            ROOT / "scripts/audit_measured_request_interactivity.py",
            "--registration", reg_path, "--cohort", cohort_path, "--output", output / "interactivity.json",
        ])
        reports = [
            ROOT / "scripts/plot_workflow_cohort_comparison.py",
            "--report", "Nonranking Full=" + str(Path(execution["reference"]["full"]["review"]) / "cohort.json"),
            "--report", "Learned R5=" + str(Path(execution["reference"]["learned"]["review"]) / "cohort.json"),
        ]
        if continuous and "binned_truth" in execution["reference"]:
            reports.extend((
                "--report", "Model-work Oracle=" + str(
                    Path(execution["reference"]["binned_truth"]["review"]) / "cohort.json"
                ),
            ))
        if pressure and "continuous_truth" in execution["reference"]:
            reports.extend((
                "--report", "Continuous-work Oracle=" + str(
                    Path(execution["reference"]["continuous_truth"]["review"]) / "cohort.json"
                ),
            ))
        cpu("figures", [
            *reports,
            "--report", method_label(artifact) + "=" + str(candidate_directory / "cohort.json"),
            "--output-dir", output / "figures",
        ])
        state.update(status="complete_model_work_oracle_CPU_analysis", measured_completed=18,
                     comparisons=results, choice_counts=choices["counts"])
    except BaseException as error:
        state.update(
            status="stopped_preserve_model_work_oracle_CPU_analysis", error=repr(error),
            error_filename=getattr(error, "filename", None),
        )
    pulse()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--execution-plan", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.execution_plan and args.output_dir:
        register(args.execution_plan, args.output_dir)
    else:
        parser.error("supply --watch or --execution-plan and --output-dir")
