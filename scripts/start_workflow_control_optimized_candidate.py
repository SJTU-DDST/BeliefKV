"""Queue the qualified tool-control optimization with benefit-only admission."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.prefill2048_mean120_20261029.queue_pair import validate_native_rebinding
from scripts import run_causal_comparison as causal
from scripts import start_workflow_benefit_only_candidate as previous
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.review_learned_work_ranking import (
    CONTROL_OPTIMIZATION_MODULES, verify_learned_combination_pair,
)
from scripts.review_workflow_objective_pair import verify_objective_pair
from scripts.train_workflow_work import write


MAIN, OPS, BASE = previous.MAIN, previous.OPS, previous.BASE
OLD, CONTROL, PREDECESSOR, COHORT, NATIVE = (
    previous.OLD, previous.CONTROL, previous.PREDECESSOR, previous.COHORT, previous.NATIVE,
)
FAILED_ATTEMPT = BASE / "r5_host400_control_optimized_r1"
HERE = FAILED_ATTEMPT / "registration_retry_r2"
SERVING = MAIN.parent / ".worktrees/beliefkv-tool-checks-host400-20261103"
FROZEN = BASE / "synchronous_control_audit_20261103_r1/replacement_prefilter_frozen_r1"
OPERATOR_CPU = BASE / (
    "synchronous_control_audit_20261103_r1/control_optimized_operator_r1/"
    "registration_retry_cpu_r2.xml"
)
REG = OPS / "workflow_original73_host400_control_optimized_20261103_r2.registration.json"
OBSERVER = OPS / "workflow_original73_host400_control_optimized_cohort_20261103_r2.registration.json"
RUN = MAIN / "experiments/raw/workflow_original73_host400_control_optimized_20261103_r2"
PORT = 19000
CONTROL_SCOPE = "tool_replacement_feasibility_prefilter_and_same_check_headroom"
CHANGED_MODULES = frozenset((
    "beliefkv/runtime/predicted_tool_offload.py",
    "beliefkv/runtime/reference_tool_replacement.py",
))
CONTROL_REFRESH = False
INTERRUPTED_HERE = HERE
INTERRUPTED_REG = REG


def select_control_refresh_candidate():
    global HERE, SERVING, FROZEN, OPERATOR_CPU, REG, OBSERVER, RUN, PORT
    global CONTROL_SCOPE, CHANGED_MODULES, CONTROL_REFRESH
    CONTROL_REFRESH = True
    HERE = FAILED_ATTEMPT / "control_refresh_r3"
    SERVING = MAIN.parent / ".worktrees/beliefkv-latest-tool-control400-20261104"
    FROZEN = BASE / "synchronous_control_audit_20261103_r1/control400_serving_frozen_r1"
    OPERATOR_CPU = BASE / "synchronous_control_audit_20261103_r1/control_refresh_operator_frozen_r1.xml"
    REG = OPS / "workflow_original73_host400_control_refresh_20261104_r1.registration.json"
    OBSERVER = OPS / "workflow_original73_host400_control_refresh_cohort_20261104_r1.registration.json"
    RUN = MAIN / "experiments/raw/workflow_original73_host400_control_refresh_20261104_r1"
    PORT = 19002
    CONTROL_SCOPE = "latest_predictor_and_tool_control_400ms_retry"
    CHANGED_MODULES = CONTROL_OPTIMIZATION_MODULES[CONTROL_SCOPE]


def candidate_options():
    return ["--control-refresh"] if CONTROL_REFRESH else []


def verify_interrupted_attempt():
    review = causal.load(INTERRUPTED_HERE / "user_pause_r1/pause_review.json")
    verify_pins(review["evidence_sha256"])
    state = causal.load(INTERRUPTED_REG.with_suffix(".state.json"))
    cleanup = causal.load(Path(state["run_root"]) / "full/owned_process_cleanup.json")
    request = causal.load(INTERRUPTED_HERE / "user_pause_r1/request.json")
    handles = [
        state["watcher"], *cleanup["identities"],
        request["last_cohort_observer_state"]["process"],
        request["results_observer_state"]["process"],
    ]
    if (
        review.get("status") != "user_requested_pause_verified"
        or review.get("completed_comparison") is not False
        or review.get("automatic_resume_allowed") is not False
        or review.get("remaining_owned_processes") != []
        or cleanup.get("remaining_processes") != []
        or not (INTERRUPTED_HERE / "STOP").is_file()
        or any(causal.reservation.alive(handle) for handle in handles)
    ):
        raise ValueError("preserve the interrupted comparison and require actual owned exit")


def now():
    return datetime.now(timezone.utc).isoformat()


def verify_pins(pins):
    for path, digest in pins.items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("a qualified control-optimization input changed: " + path)


def verify_retired_waiters():
    for path, key in (
        (previous.HERE / "resource_waiter.state.json", "process"),
        (previous.HERE / "final_results_r1/watcher.state.json", "process"),
    ):
        state = causal.load(path)
        if not state["status"].startswith("stopped_") or causal.reservation.alive(state.get(key)):
            raise ValueError("the old candidate or result observer is still active")
    if previous.REG.exists() or previous.OBSERVER.exists():
        raise ValueError("the old GPU candidate registered; preserve its actual execution")


def verify_failed_attempt():
    for path in (
        FAILED_ATTEMPT / "resource_waiter.state.json",
        FAILED_ATTEMPT / "final_results_r1/watcher.state.json",
    ):
        state = causal.load(path)
        if not state["status"].startswith("stopped_") or causal.reservation.alive(state["process"]):
            raise ValueError("the failed registration still owns a live process")
    failure = causal.load(FAILED_ATTEMPT / "registration_result.json")
    original = OPS / "workflow_original73_host400_control_optimized_20261103_r1.registration.json"
    if (
        failure["returncode"] == 0 or original.exists()
        or "engine source tree or CPU evidence changed" not in failure["stderr"]
    ):
        raise ValueError("preserve the original registration; this retry only repairs native proof binding")


def checked_qualification():
    qualification = causal.load(HERE / "qualification.json")
    if (
        git_source_state(ROOT) != qualification["operator_source"]
        or git_source_state(SERVING) != qualification["source"]
    ):
        raise ValueError("the qualified operator or serving checkout changed")
    verify_pins(qualification["inputs_sha256"])
    verify_retired_waiters()
    verify_failed_attempt()
    if CONTROL_REFRESH:
        verify_interrupted_attempt()
    if (HERE / "STOP").exists():
        raise RuntimeError("control-optimized candidate explicitly stopped")
    return qualification


def optimized_configuration(original):
    updated = deepcopy(original)
    updated["purpose"] = "Benefit-only admission with bounded tool-control CPU optimization"
    updated["comparison_contract"].update(
        only_combination_selection_objective_changed=False,
        synchronous_control_optimization=CONTROL_SCOPE,
        admission_objective_and_control_optimization_are_joint_change=True,
        ranking_gain_isolated=False,
    )
    if CONTROL_REFRESH:
        updated["common_environment"]["BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS"] = "400"
        export_path = Path(causal.load(FROZEN / "qualification.json")["predictor_export_path"])
        export = deepcopy(causal.load(export_path))
        original_model = causal.load(Path(original["common_environment"]["WORKFLOW_WORK_MODEL"]))
        if {k: v for k, v in export.items() if k not in (
            "admission_cost_profile", "conditional_progress_tree_head", "metadata",
        )} != {k: v for k, v in original_model.items() if k not in (
            "admission_cost_profile", "conditional_progress_tree_head", "metadata",
        )}:
            raise ValueError("the qualified conditional head changed the retained prediction backbone")
        export["admission_cost_profile"] = original_model["admission_cost_profile"]
        model_path = HERE / "serving_ready/model.json"
        write(model_path, export)
        for prefix in ("", "BELIEFKV_"):
            updated["common_environment"][prefix + "WORKFLOW_WORK_MODEL"] = str(model_path)
            updated["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(model_path)
        updated["comparison_contract"].update(
            tool_offload_retry_interval_ms=400,
            tool_offload_control_cost_margin_ms=400,
            real_tool_request_and_transfer_events_wake_immediately=True,
            joint_predictor_optimization="qualified_conditional_progress_tree",
            prediction_parameters_and_support_unchanged=False,
        )
    qualification = FROZEN / "qualification.json"
    updated["gpu_execution"].update(
        serving_commit=causal.load(qualification)["source"]["commit"],
        serving_compatibility_report=str(HERE / "source_compatibility.json"),
        control_optimization_qualification=str(qualification),
        control_optimization_qualification_sha256=sha256_file(qualification),
        requires_source_bound_control_qualification=True,
    )
    return updated


def qualify():
    if HERE.exists() or git_source_state(ROOT)["dirty"]:
        raise ValueError("preserve previous evidence and freeze the operator first")
    verify_retired_waiters()
    verify_failed_attempt()
    if CONTROL_REFRESH:
        verify_interrupted_attempt()
    frozen_path = FROZEN / "qualification.json"
    frozen = causal.load(frozen_path)
    verify_pins(frozen["inputs_sha256"])
    if git_source_state(SERVING) != frozen["source"]:
        raise ValueError("the complete serving CPU qualification changed")
    baseline = causal.load(previous.HERE / "qualification.json")
    verify_pins(baseline["inputs_sha256"])
    if baseline["source"] != frozen["baseline_source"]:
        raise ValueError("the qualified predecessor identity differs")
    changed = subprocess.check_output([
        "git", "diff", "--name-only", baseline["source"]["commit"], "HEAD", "--", "beliefkv",
    ], cwd=SERVING, text=True).splitlines()
    if set(changed) != CHANGED_MODULES or set(frozen["changed_runtime_modules"]) != CHANGED_MODULES:
        raise ValueError("the candidate changed outside the qualified control modules")
    report, sources = FROZEN / "cpu.xml", FROZEN / "sources.json"
    document = ET.parse(report)
    cases = list(document.iter("testcase"))
    if (
        len(cases) != frozen["passing_CPU_cases"]
        or any(list(document.iter(tag)) for tag in ("failure", "error", "skipped"))
        or frozen.get("complete_prior_1179_cases_retained") is not True
    ):
        raise ValueError("the complete passing serving CPU qualification is required")
    causal.comparison.verify_cpu_snapshot(SERVING, causal.load(sources))
    operator_tests = ET.parse(OPERATOR_CPU)
    operator_cases = list(operator_tests.iter("testcase"))
    if (
        not any(case.get("classname") == "tests.test_workflow_control_operator" for case in operator_cases)
        or any(list(operator_tests.iter(tag)) for tag in ("failure", "error", "skipped"))
    ):
        raise ValueError("the frozen control operator needs its passing CPU checks")
    native = causal.load(previous.HERE / "native_sources.json")
    actual = git_source_state(
        NATIVE, canonical_patch=SERVING / "patches/sglang-layer-free-extent-runtime-20261029.patch",
    )
    validate_native_rebinding(actual, native)
    if not actual["canonical_patch"]["matches_worktree"]:
        raise ValueError("the qualified data plane changed")
    native_path = HERE / "native_sources_for_registration.json"
    write(native_path, actual)
    compatibility = HERE / "source_compatibility.json"
    write(compatibility, {
        "source": frozen["source"], "previous_source": baseline["source"],
        "changed_runtime_modules": changed, "source_bound_CPU_cases": len(cases),
        "control_qualification": str(frozen_path),
        "predictor_artifact_and_policy_unchanged_from_unstarted_benefit_candidate": not CONTROL_REFRESH,
        "joint_predictor_optimization": "qualified_conditional_progress_tree" if CONTROL_REFRESH else None,
        "previous_interrupted_run_preserved": CONTROL_REFRESH,
        "native_transfers_position_sampling_and_Host400_evidence_reused": True,
        "new_GPU_or_Host_component_run": False, "GPU_action": False,
    })
    config = optimized_configuration(causal.load(previous.HERE / "serving_ready/qualified_full.json"))
    config["gpu_execution"]["serving_compatibility_report_sha256"] = sha256_file(compatibility)
    spec = HERE / "serving_ready/qualified_full.json"
    write(spec, config)
    verify_learned_combination_pair(
        causal.load(Path(causal.load(CONTROL)["experiment_configuration"])), config,
    )
    verify_objective_pair(causal.load(OLD / "serving_ready/full.json"), config)
    command = causal.load(OLD / "launch.json")["command"]
    engine_report = Path(command[command.index("--engine-report") + 1])
    # Exercise the exact registrar's proof check before waiting for GPU resources.
    code = (
        "import json,sys; from pathlib import Path; "
        "from scripts import run_causal_comparison as c; "
        "spec=c.load(Path(sys.argv[1])); env=spec['common_environment']; "
        "root=Path.cwd(); settings,_,_=c.environment({'arms':{'full':env}},'full',"
        "root,Path(sys.argv[2]),Path(sys.argv[3]),int(sys.argv[4]),root,"
        "sampling_mode='position',max_running_requests=24,"
        "tool_restore_mode=env['TOOL_RESTORE_MODE']); "
        "engine=c.engine_evidence(Path(sys.argv[2]),Path(sys.argv[5]),Path(sys.argv[6])); "
        "print(json.dumps({'runtime':c.preflight_runtime(settings,env['MODEL_PATH']),"
        "'native_evidence':engine}))"
    )
    result = subprocess.run([
        sys.executable, "-c", code, str(spec), str(NATIVE), str(HERE / "not_a_GPU_server"), str(PORT),
        str(engine_report), str(native_path),
    ], cwd=SERVING, capture_output=True, text=True)
    write(HERE / "constructor_result.json", {
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    })
    if result.returncode:
        raise ValueError("preserve serving constructor refusal: " + result.stderr[-2000:])
    operator_source = git_source_state(ROOT)
    paths = (
        frozen_path, report, sources, spec, compatibility, previous.HERE / "qualification.json",
        previous.HERE / "native_sources.json", previous.HERE / "serving_ready/qualified_full.json",
        native_path, engine_report, FAILED_ATTEMPT / "registration_result.json",
        FAILED_ATTEMPT / "resource_waiter.state.json",
        FAILED_ATTEMPT / "final_results_r1/watcher.state.json",
        previous.HERE / "resource_waiter.state.json",
        previous.HERE / "final_results_r1/watcher.state.json",
        OLD / "launch.json", OLD / "serving_ready/full.json", COHORT, Path(__file__),
        OPERATOR_CPU, ROOT / "tests/test_workflow_control_operator.py",
        ROOT / "scripts/review_learned_work_ranking.py",
        ROOT / "scripts/review_workflow_objective_pair.py",
        ROOT / "scripts/watch_workflow_benefit_results.py",
        ROOT / "experiments/reports/prefill2048_mean120_20261029/queue_pair.py",
    )
    if CONTROL_REFRESH:
        paths += (
            INTERRUPTED_HERE / "user_pause_r1/pause_review.json",
            INTERRUPTED_HERE / "user_pause_r1/request.json",
            INTERRUPTED_HERE / "STOP", INTERRUPTED_REG,
        )
    pins = {str(path): sha256_file(path) for path in paths}
    pins.update(baseline["inputs_sha256"])
    pins.update(frozen["inputs_sha256"])
    verify_pins(pins)
    write(HERE / "qualification.json", {
        "source": frozen["source"], "operator_source": operator_source,
        "serving_checkout": str(SERVING), "passing_CPU_cases": len(cases),
        "actual_constructor": json.loads(result.stdout)["runtime"],
        "actual_registrar_native_evidence": json.loads(result.stdout)["native_evidence"],
        "passing_operator_CPU_cases": len(operator_cases),
        "selection_objective": "benefit", "synchronous_control_optimization": CONTROL_SCOPE,
        "old_candidate_superseded_before_registration": not CONTROL_REFRESH,
        "separate_resume_after_user_interrupted_run": CONTROL_REFRESH,
        "only_objective_changed": False, "ranking_gain_isolated": False,
        "new_GPU_or_Host_component_run": False, "GPU_action": False,
        "inputs_sha256": pins,
    })
    print(json.dumps({"qualified": True, "CPU_cases": len(cases), "source": frozen["source"]["commit"]}), flush=True)


def queue():
    checked_qualification()
    if os.getpriority(os.PRIO_PROCESS, 0) != 0 or REG.exists():
        raise ValueError("keep the serving supervisor at nice0 and preserve existing experiments")
    check_reservation()
    with (HERE / "resource_waiter.log").open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--wait-and-start", *candidate_options()], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    process = causal.reservation.process(child.pid)
    write(HERE / "resource_waiter_process.json", process)
    print(json.dumps(process), flush=True)


def launch_command():
    command = list(causal.load(OLD / "launch.json")["command"])
    command[1] = str(SERVING / "scripts/run_causal_comparison.py")
    for flag, value in {
        "--registration": REG, "--run-root": RUN, "--port": PORT,
        "--experiment-config": HERE / "serving_ready/qualified_full.json",
        "--cpu-report": FROZEN / "cpu.xml", "--cpu-sources": FROZEN / "sources.json",
        "--engine-sources": HERE / "native_sources_for_registration.json",
    }.items():
        command[command.index(flag) + 1] = str(value)
    return command


def wait_and_start():
    state = {"status": "waiting_idle_GPU", "process": causal.reservation.process(os.getpid())}
    try:
        qualification = checked_qualification()
        ready_observations = 0
        while True:
            check_reservation()
            if (HERE / "STOP").exists():
                raise RuntimeError("control-optimized candidate explicitly stopped")
            resources = causal.qualified.resources(PORT)
            ready_observations = ready_observations + 1 if resources["ready"] else 0
            state.update(checked_at=now(), resources=resources, consecutive_ready=ready_observations)
            causal.reservation.write_json(HERE / "resource_waiter.state.json", state)
            if ready_observations >= 2:
                break
            time.sleep(5.)
        checked_qualification()
        if REG.exists() or os.path.lexists(f"/tmp/bkv-causal-{PORT}.sock"):
            raise ValueError("preserve the existing registration or runtime socket")
        command = launch_command()
        write(HERE / "launch.json", {
            "observed_at": now(), "command": command, "resources": resources,
            "benefit_objective_and_control_optimization_are_joint_change": True,
            "unchanged_completed_controls_reused": True, "NUMA_balance_required": False,
            "serving_source": qualification["source"], "inputs_sha256": qualification["inputs_sha256"],
        })
        result = subprocess.run(command, cwd=SERVING, capture_output=True, text=True)
        write(HERE / "registration_result.json", {
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        })
        if result.returncode:
            raise RuntimeError("preserve registration refusal: " + result.stderr[-2500:])
        with (HERE / "observer_bootstrap.log").open("x") as log:
            child = subprocess.Popen(
                ["nice", "-n", "19", sys.executable, str(Path(__file__)), "--observe", *candidate_options()], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        state.update(status="comparison_registered", observer_bootstrap=causal.reservation.process(child.pid))
    except BaseException as error:
        state.update(status="stopped_preserve_failure", error=repr(error))
    state["checked_at"] = now()
    causal.reservation.write_json(HERE / "resource_waiter.state.json", state)


def observe():
    state = {"status": "waiting_actual_full", "process": causal.reservation.process(os.getpid())}
    try:
        deadline = time.monotonic() + 1800.
        while True:
            path = REG.with_suffix(".state.json")
            if path.exists():
                current = causal.load(path)
                if current["status"] == "running_full" and all(
                    current.get(name) and causal.reservation.alive(current[name])
                    for name in ("watcher", "server", "driver")
                ):
                    break
                if current.get("watcher") and not causal.reservation.alive(current["watcher"]):
                    raise RuntimeError("actual supervisor exited before replay")
                state["comparison_status"] = current["status"]
            state["checked_at"] = now()
            causal.reservation.write_json(HERE / "observer_bootstrap.state.json", state)
            if time.monotonic() > deadline:
                raise TimeoutError("preserve comparison; CPU observer bootstrap timed out")
            time.sleep(2.)
        plan = causal.load(REG)
        write(OBSERVER, {
            "schema_version": 1, "comparison": str(REG), "cohort": str(COHORT),
            "execution_checkout": str(SERVING), "code_commit": git_source_state(SERVING)["commit"],
            "serving_passing_CPU_cases": causal.load(HERE / "qualification.json")["passing_CPU_cases"],
            "minimum_model_work_p80_p20": 2.93141753477007, "poll_seconds": 2.,
            "output_dir": str(RUN / "cohort_termination"), "analysis_output": str(HERE / "review"),
            "inputs_sha256": {str(path): sha256_file(path) for path in (
                REG, COHORT, Path(plan["workflow_arrivals_path"]), Path(__file__),
                SERVING / "scripts/stop_after_measured_cohort.py", SERVING / "scripts/verify_measured_execution.py",
            )},
            "GPU_followup_registered": False, "performance_accepted": False,
        })
        with OBSERVER.with_suffix(".log").open("x") as log:
            child = subprocess.Popen(
                ["taskset", "-c", "0", sys.executable, str(SERVING / "scripts/stop_after_measured_cohort.py"),
                 "--watch", str(OBSERVER)], cwd=SERVING, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        state.update(status="fixed18_observer_started", observer=causal.reservation.process(child.pid))
    except BaseException as error:
        state.update(status="stopped_preserve_observer_failure", error=repr(error))
    state["checked_at"] = now()
    causal.reservation.write_json(HERE / "observer_bootstrap.state.json", state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ("qualify", "queue", "wait-and-start", "observe"):
        modes.add_argument("--" + name, action="store_true")
    parser.add_argument("--control-refresh", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("hide CUDA in the operator; only its registered server owns the GPU")
    if args.control_refresh:
        select_control_refresh_candidate()
    qualify() if args.qualify else queue() if args.queue else wait_and_start() if args.wait_and_start else observe()
