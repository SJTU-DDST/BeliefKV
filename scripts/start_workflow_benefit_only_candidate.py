"""Qualify and queue one matched Host400 benefit-only admission experiment."""

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
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.review_learned_work_ranking import verify_learned_combination_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
BASE = MAIN / "experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1"
OLD = BASE / "r5_host400_horizon64_r1"
HERE = BASE / "r5_host400_benefit_only_r1"
NATIVE = MAIN.parent / ".worktrees/sglang-bg25r1-capture-434c35dd6621"
CONTROL = OPS / "workflow_original73_host400_registered_copy_full_20261102_r1.registration.json"
PREDECESSOR = OPS / "workflow_original73_host400_R5_horizon64_confirmation_20261102_r1.registration.json"
REG = OPS / "workflow_original73_host400_benefit_only_20261102_r1.registration.json"
OBSERVER = OPS / "workflow_original73_host400_benefit_only_cohort_20261102_r1.registration.json"
RUN = MAIN / "experiments/raw/workflow_original73_host400_benefit_only_20261102_r1"
COHORT = BASE / "host400_initial20_mean70_prepare_r1/analysis_cohort.json"
PORT = 19000


def now():
    return datetime.now(timezone.utc).isoformat()


def checked_qualification():
    qualification = causal.load(HERE / "qualification.json")
    if git_source_state(ROOT) != qualification["source"]:
        raise ValueError("the qualified benefit-only checkout changed")
    for path, digest in qualification["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("a qualified objective-only input changed")
    return qualification


def qualify():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("hide CUDA and preserve prior candidate evidence")
    source = git_source_state(ROOT)
    previous_source = causal.load(OLD / "source_compatibility.json")["source"]
    changed = subprocess.check_output([
        "git", "diff", "--name-only", previous_source["commit"], "HEAD", "--", "beliefkv",
    ], cwd=ROOT, text=True).splitlines()
    if source["dirty"] or set(changed) != {
        "beliefkv/policy/workflow_combination.py", "beliefkv/runtime/workflow_combination.py",
    }:
        raise ValueError("freeze only the objective selector change over the qualified serving code")
    completed = verify_execution(PREDECESSOR, COHORT)
    prepared = subprocess.run([
        sys.executable, str(ROOT / "scripts/prepare_workflow_benefit_only.py"),
        "--base", str(OLD / "serving_ready/full.json"),
        "--evaluation", str(OLD / "serving_ready/evaluation.json"),
        "--output-dir", str(HERE / "serving_ready"),
    ], cwd=ROOT, capture_output=True, text=True)
    write(HERE / "preparation_result.json", {
        "returncode": prepared.returncode, "stdout": prepared.stdout, "stderr": prepared.stderr,
    })
    if prepared.returncode:
        raise ValueError("preserve failed objective-only preparation: " + prepared.stderr[-2000:])
    original_command = causal.load(OLD / "launch.json")["command"]
    previous_report = Path(original_command[original_command.index("--cpu-report") + 1])
    tests = {
        case.get("classname").replace(".", "/") + ".py"
        for case in ET.parse(previous_report).iter("testcase")
    }
    tests.update((
        "tests/test_workflow_benefit_only.py", "tests/test_learned_work_ranking_review.py",
        "tests/test_combination_choice_audit.py", "tests/test_combination_deep_prefix.py",
        "tests/test_combination_resource_fallback.py", "tests/test_combination_context_costs.py",
        "tests/test_workflow_benefit_operator.py",
    ))
    report, sources = HERE / "frozen_cpu.xml", HERE / "frozen_sources.json"
    with (HERE / "frozen_cpu.log").open("x") as log:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *sorted(tests), "--tb=short", "--junitxml=" + str(report)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "SGLANG_SOURCE_CHECKOUT": str(NATIVE)},
        )
    if result.returncode:
        raise ValueError("preserve failed frozen objective qualification")
    bound = subprocess.run([
        sys.executable, str(ROOT / "scripts/run_internal_comparison.py"),
        "--qualify-cpu-sources", "--previous-cpu-report", str(previous_report),
        "--cpu-report", str(report), "--cpu-sources", str(sources),
    ], cwd=ROOT, capture_output=True, text=True)
    if bound.returncode:
        raise ValueError("objective CPU source binding failed: " + bound.stderr[-2000:])
    count = causal.comparison.qualify_report(report, sources)
    parity = subprocess.run([
        sys.executable, str(ROOT / "scripts/verify_workflow_benefit_replay.py"),
        "--audits", *(str(OLD / "cost_denominator_audit_20261103_r1" / name)
                      for name in ("first.json", "confirmation.json")),
        "--model", str(OLD / "serving_ready/model.json"),
        "--output", str(HERE / "historical_choice_parity.json"),
    ], cwd=ROOT, capture_output=True, text=True)
    write(HERE / "historical_choice_parity_result.json", {
        "returncode": parity.returncode, "stdout": parity.stdout, "stderr": parity.stderr,
    })
    if parity.returncode:
        raise ValueError("preserve deployed benefit-selector parity failure")
    native_path = Path(original_command[original_command.index("--engine-sources") + 1])
    native = git_source_state(
        NATIVE, canonical_patch=ROOT / "patches/sglang-layer-free-extent-runtime-20261029.patch",
    )
    compared = []
    for value in (causal.load(native_path), native):
        fields = deepcopy(value)
        fields.pop("state_sha256")
        fields["canonical_patch"].pop("path")
        compared.append(fields)
    if compared[0] != compared[1] or not native["canonical_patch"]["matches_worktree"]:
        raise ValueError("the native engine or copy/data-plane proof changed")
    write(HERE / "native_sources.json", native)
    compatibility = HERE / "source_compatibility.json"
    write(compatibility, {
        "source": source, "previous_source": previous_source, "changed_admission_modules": changed,
        "prediction_worker_native_hooks_sampling_and_Host_components_unchanged": True,
        "position_sampling_and_400GB_copy_evidence_reused": True,
        "source_bound_CPU_cases": count, "same_fixed18_complete_predecessor": completed["work"],
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            OLD / "launch.json", OLD / "serving_ready/model.json", previous_report,
            PREDECESSOR, report, sources, Path(__file__),
        )},
        "GPU_action": False,
    })
    prepared_config = HERE / "serving_ready/full.json"
    config_path = HERE / "serving_ready/qualified_full.json"
    config = causal.load(prepared_config)
    config["gpu_execution"].update(
        serving_commit=source["commit"], serving_compatibility_report=str(compatibility),
        serving_compatibility_report_sha256=sha256_file(compatibility),
        requires_same_qualified_serving_and_engine_identity=False,
        requires_source_bound_objective_qualification=True,
    )
    write(config_path, config)
    verify_learned_combination_pair(causal.load(Path(causal.load(CONTROL)["experiment_configuration"])), config)
    env = config["common_environment"]
    settings, _, _ = causal.environment(
        {"arms": {"full": env}}, "full", ROOT, NATIVE, HERE / "not_a_GPU_server", PORT, ROOT,
        sampling_mode="position", max_running_requests=24, tool_restore_mode=env["TOOL_RESTORE_MODE"],
    )
    constructor = causal.preflight_runtime(settings, env["MODEL_PATH"])
    if git_source_state(ROOT) != source:
        raise ValueError("the frozen objective source changed during qualification")
    write(HERE / "qualification.json", {
        "source": source, "passing_CPU_cases": count, "actual_constructor": constructor,
        "selection_objective": "benefit", "new_GPU_or_Host_component_run": False,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            config_path, prepared_config, HERE / "serving_ready/model.json", HERE / "serving_ready/evaluation.json",
            compatibility, report, sources, HERE / "native_sources.json", OLD / "launch.json",
            HERE / "historical_choice_parity.json",
            PREDECESSOR, COHORT, Path(__file__),
        )},
        "GPU_action": False,
    })
    print(json.dumps({"qualified": True, "CPU_cases": count, "source": source["commit"]}), flush=True)


def queue():
    checked_qualification()
    if os.getpriority(os.PRIO_PROCESS, 0) != 0 or REG.exists():
        raise ValueError("keep the serving supervisor at nice0 and preserve existing experiments")
    check_reservation()
    with (HERE / "resource_waiter.log").open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--wait-and-start"], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    process = causal.reservation.process(child.pid)
    write(HERE / "resource_waiter_process.json", process)
    print(json.dumps(process), flush=True)


def wait_and_start():
    state = {"status": "waiting_idle_GPU", "process": causal.reservation.process(os.getpid())}
    try:
        qualification = checked_qualification()
        ready_observations = 0
        while True:
            check_reservation()
            if (HERE / "STOP").exists():
                raise RuntimeError("candidate resource waiter explicitly stopped")
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
        command = list(causal.load(OLD / "launch.json")["command"])
        command[1] = str(ROOT / "scripts/run_causal_comparison.py")
        for flag, value in {
            "--registration": REG, "--run-root": RUN, "--port": PORT,
            "--experiment-config": HERE / "serving_ready/qualified_full.json",
            "--cpu-report": HERE / "frozen_cpu.xml", "--cpu-sources": HERE / "frozen_sources.json",
            "--engine-sources": HERE / "native_sources.json",
        }.items():
            command[command.index(flag) + 1] = str(value)
        write(HERE / "launch.json", {
            "observed_at": now(), "command": command, "resources": resources,
            "only_new_benefit_objective_candidate": True, "NUMA_balance_required": False,
            "unchanged_complete_Full_oracle_Thunder_and_ratio_controls_reused": True,
            "inputs_sha256": qualification["inputs_sha256"],
        })
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        write(HERE / "registration_result.json", {
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        })
        if result.returncode:
            raise RuntimeError("preserve registration refusal: " + result.stderr[-2500:])
        with (HERE / "observer_bootstrap.log").open("x") as log:
            child = subprocess.Popen(
                ["nice", "-n", "19", sys.executable, str(Path(__file__)), "--observe"], cwd=ROOT,
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
                    causal.reservation.alive(current.get(name)) for name in ("watcher", "server", "driver")
                ):
                    break
                if not causal.reservation.alive(current.get("watcher")):
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
            "execution_checkout": str(ROOT), "code_commit": git_source_state(ROOT)["commit"],
            "serving_passing_CPU_cases": causal.load(HERE / "qualification.json")["passing_CPU_cases"],
            "minimum_model_work_p80_p20": 2.93141753477007, "poll_seconds": 2.,
            "output_dir": str(RUN / "cohort_termination"), "analysis_output": str(HERE / "review"),
            "inputs_sha256": {str(path): sha256_file(path) for path in (
                REG, COHORT, Path(plan["workflow_arrivals_path"]), Path(__file__),
                ROOT / "scripts/stop_after_measured_cohort.py", ROOT / "scripts/verify_measured_execution.py",
            )},
            "GPU_followup_registered": False, "performance_accepted": False,
        })
        with OBSERVER.with_suffix(".log").open("x") as log:
            child = subprocess.Popen(
                ["taskset", "-c", "0", sys.executable, str(ROOT / "scripts/stop_after_measured_cohort.py"),
                 "--watch", str(OBSERVER)], cwd=ROOT, stdin=subprocess.DEVNULL,
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
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("the control-plane operator must hide CUDA; only its registered server owns the GPU")
    qualify() if args.qualify else queue() if args.queue else wait_and_start() if args.wait_and_start else observe()
