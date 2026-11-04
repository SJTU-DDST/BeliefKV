"""Run the latest optimized serving code with only R5 work selection disabled."""

import argparse
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts import start_workflow_control_optimized_candidate as previous
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution

MAIN, BASE, NATIVE, COHORT = previous.MAIN, previous.BASE, previous.NATIVE, previous.COHORT
HERE = BASE / "r5_host400_control_optimized_r1/ranking_disabled_control_r1/qualification_retry_r2"
REG = previous.OPS / "workflow_original73_host400_ranking_disabled_20261104_r1.registration.json"
OBSERVER = previous.OPS / "workflow_original73_host400_ranking_disabled_cohort_20261104_r1.registration.json"
RUN = MAIN / "experiments/raw/workflow_original73_host400_ranking_disabled_20261104_r1"
PORT = 19004
MODEL_FIELDS = {
    prefix + name for prefix in ("", "BELIEFKV_")
    for name in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256")
}


def verify_pair(enabled, disabled):
    environments = [spec["common_environment"] for spec in (enabled, disabled)]
    if {
        key: value for key, value in environments[0].items() if key not in MODEL_FIELDS
    } != {
        key: value for key, value in environments[1].items() if key not in MODEL_FIELDS
    }:
        raise ValueError("the matched control changed a serving or residency parameter")
    artifacts = []
    for env in environments:
        path = Path(env["WORKFLOW_WORK_MODEL"])
        if (
            sha256_file(path) != env["WORKFLOW_WORK_MODEL_SHA256"]
            or str(path) != env["BELIEFKV_WORKFLOW_WORK_MODEL"]
            or env["WORKFLOW_WORK_MODEL_SHA256"] != env["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"]
        ):
            raise ValueError("the matched predictor path or hash changed")
        artifacts.append(causal.load(path))
    if (
        artifacts[0].get("remaining_work_ranking_enabled", True) is not True
        or artifacts[1].get("remaining_work_ranking_enabled") is not False
        or {key: value for key, value in artifacts[0].items()
            if key != "remaining_work_ranking_enabled"}
        != {key: value for key, value in artifacts[1].items()
            if key != "remaining_work_ranking_enabled"}
    ):
        raise ValueError("only the remaining-work selection boolean may differ")
    for name in ("workflow_arrivals", "private_analysis_cohort", "tool_timing_overlay"):
        if enabled[name] != disabled[name]:
            raise ValueError("the matched control changed fixed traffic: " + name)
    if (
        environments[0]["WORKFLOW_WORK_SOURCE"] != "learned"
        or environments[0]["WORKFLOW_COMBINATION_ADMISSION"] != "1"
    ):
        raise ValueError("this control requires the current learned R5 selector")


def checked():
    qualification = causal.load(HERE / "qualification.json")
    if git_source_state(ROOT) != qualification["source"]:
        raise ValueError("the frozen matched-control checkout changed")
    for name, digest in qualification["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("a matched-control input changed: " + name)
    verify_pair(
        causal.load(previous.HERE / "serving_ready/qualified_full.json"),
        causal.load(HERE / "full.json"),
    )
    if (HERE / "STOP").exists():
        raise RuntimeError("the matched control was explicitly stopped")
    return qualification


def prepare():
    if HERE.exists() or git_source_state(ROOT)["dirty"]:
        raise ValueError("preserve existing evidence and commit the operator before preparation")
    source = git_source_state(ROOT)
    enabled_path = previous.HERE / "serving_ready/qualified_full.json"
    enabled = causal.load(enabled_path)
    model = causal.load(Path(enabled["common_environment"]["WORKFLOW_WORK_MODEL"]))
    model["remaining_work_ranking_enabled"] = False
    write(HERE / "model.json", model)
    disabled = deepcopy(enabled)
    for prefix in ("", "BELIEFKV_"):
        disabled["common_environment"].update({
            prefix + "WORKFLOW_WORK_MODEL": str(HERE / "model.json"),
            prefix + "WORKFLOW_WORK_MODEL_SHA256": sha256_file(HERE / "model.json"),
        })
    disabled["purpose"] = "Latest optimized control with only short-workflow set selection disabled"
    disabled["comparison_contract"].update(
        workflow_work_ranking=False, ranking_gain_isolated=True,
        matched_pair_changes_only_remaining_work_ranking=True,
        admission_objective_and_control_optimization_are_joint_change=False,
        predictor_worker_retained_for_matched_control=True,
        disabled_selection_returns_original_reference_set=True,
    )
    disabled["gpu_execution"]["serving_commit"] = source["commit"]
    write(HERE / "full.json", disabled)
    verify_pair(enabled, disabled)
    prior_report = previous.FROZEN / "cpu.xml"
    tests = sorted({
        case.get("classname").replace(".", "/") + ".py"
        for case in ET.parse(prior_report).iter("testcase")
    } | {"tests/test_learned_ranking_disabled.py", "tests/test_ranking_disabled_operator.py"})
    with (HERE / "cpu.log").open("x") as log:
        result = subprocess.run([
            sys.executable, "-m", "pytest", "-q", *tests, "--tb=short",
            "--junitxml=" + str(HERE / "cpu.xml"),
        ], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "SGLANG_SOURCE_CHECKOUT": str(NATIVE)})
    if result.returncode:
        raise ValueError("preserve the failed matched-control CPU qualification")
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/run_internal_comparison.py"),
        "--qualify-cpu-sources", "--previous-cpu-report", str(prior_report),
        "--cpu-report", str(HERE / "cpu.xml"), "--cpu-sources", str(HERE / "sources.json"),
    ], cwd=ROOT, capture_output=True, text=True)
    write(HERE / "source_binding_result.json", {
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    })
    if result.returncode:
        raise ValueError("preserve the failed matched-control source binding")
    native = git_source_state(
        NATIVE, canonical_patch=ROOT / "patches/sglang-layer-free-extent-runtime-20261029.patch",
    )
    if not native["canonical_patch"]["matches_worktree"]:
        raise ValueError("the qualified native engine changed")
    write(HERE / "native_sources.json", native)
    completed = verify_execution(previous.REG, COHORT)
    settings, _, _ = causal.environment(
        {"arms": {"full": disabled["common_environment"]}}, "full", ROOT, NATIVE,
        HERE / "not_a_GPU_server", PORT, ROOT, sampling_mode="position",
        max_running_requests=24, tool_restore_mode=disabled["common_environment"]["TOOL_RESTORE_MODE"],
    )
    constructor = causal.preflight_runtime(settings, disabled["common_environment"]["MODEL_PATH"])
    paths = (
        enabled_path, HERE / "full.json", HERE / "model.json", HERE / "cpu.xml",
        HERE / "sources.json", HERE / "native_sources.json", COHORT, previous.REG,
        previous.HERE / "launch.json", ROOT / "scripts/start_workflow_ranking_disabled.py",
    )
    write(HERE / "qualification.json", {
        "source": source, "actual_constructor": constructor,
        "passing_CPU_cases": len(list(ET.parse(HERE / "cpu.xml").iter("testcase"))),
        "previous_complete_measured_work": completed["work"],
        "only_short_workflow_set_selection_disabled": True,
        "latest_control_optimization_and_prediction_worker_retained": True,
        "new_GPU_component_test": False,
        "inputs_sha256": {str(path): sha256_file(path) for path in paths},
    })
    print(json.dumps({"qualified": True, "source": source["commit"]}), flush=True)


def register():
    checked()
    for _index in range(2):
        check_reservation()
        resources = causal.qualified.resources(PORT)
        if not resources["ready"]:
            raise ValueError("GPU or experiment resources are occupied: " + repr(resources))
        if _index == 0:
            time.sleep(5.)
    if REG.exists() or os.path.lexists(f"/tmp/bkv-causal-{PORT}.sock"):
        raise ValueError("preserve existing registration or runtime socket")
    command = list(causal.load(previous.HERE / "launch.json")["command"])
    command[1] = str(ROOT / "scripts/run_causal_comparison.py")
    for flag, value in {
        "--registration": REG, "--run-root": RUN, "--port": PORT,
        "--experiment-config": HERE / "full.json", "--cpu-report": HERE / "cpu.xml",
        "--cpu-sources": HERE / "sources.json", "--engine-sources": HERE / "native_sources.json",
    }.items():
        command[command.index(flag) + 1] = str(value)
    write(HERE / "launch.json", {
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "command": command, "resources": resources, "only_work_selection_disabled": True,
    })
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    write(HERE / "registration_result.json", {
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    })
    if result.returncode:
        raise ValueError("preserve registration refusal: " + result.stderr[-2500:])
    with (HERE / "observer_bootstrap.log").open("x") as log:
        child = subprocess.Popen(
            ["nice", "-n", "19", sys.executable, str(Path(__file__)), "--observe"],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write(HERE / "observer_bootstrap_process.json", causal.reservation.process(child.pid))
    print(json.dumps({"registered": str(REG), "observer_bootstrap_pid": child.pid}), flush=True)


def observe():
    previous.HERE, previous.SERVING = HERE, ROOT
    previous.REG, previous.OBSERVER, previous.RUN = REG, OBSERVER, RUN
    previous.observe()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for action in ("prepare", "register", "observe"):
        actions.add_argument("--" + action, action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("hide CUDA in this operator; only its registered server owns the GPU")
    previous.select_control_refresh_candidate()
    prepare() if args.prepare else register() if args.register else observe()
