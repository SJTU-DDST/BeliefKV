"""Seal actual TokenCake startup, declared parameters, ownership and exact work."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "tokencake_locobench_v230_v311_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
LABEL = "tokencake_paper_observed"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.paper_baselines import (
    tokencake_parameters, validate_state, validate_tokencake_parameters,
)
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import audit
from scripts import run_causal_comparison as causal
from scripts.qualify_paper_prefix_transfers import validate_report as validate_prefix
from scripts.qualify_tokencake_runtime import validate_report as validate_lifecycle


def main():
    output = HERE / "live_start.json"
    if output.exists():
        raise FileExistsError("preserve original live TokenCake observation")
    plan, control = causal.load(REGISTRATION), causal.load(CONTROL)
    common = (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "workflow_ids", "quality_exclusions",
        "quality_exclusion_details", "engine_tree",
    )
    if any(plan[name] != control[name] for name in common):
        raise ValueError("TokenCake common work, capacity or engine changed")
    overlays = [
        {name: value for name, value in item["tool_timing_overlay"].items() if name != "path"}
        for item in (plan, control)
    ]
    if overlays[0] != overlays[1]:
        raise ValueError("TokenCake tool timing contract changed")
    expected = Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"
    result = audit(REGISTRATION, LABEL, expected)
    if not result["exact_completed_receipts"]:
        raise ValueError("TokenCake has not completed a real native request")
    runtime = result["latest_runtime_snapshot"]
    validate_state(LABEL, runtime)
    parameters = tokencake_parameters(plan["reference_environment"])
    validate_tokencake_parameters(runtime["paper_baseline"], parameters)
    arm = Path(plan["run_root"]) / LABEL
    causal.observed_configuration(plan, LABEL, arm)
    frozen = Path(plan["execution_checkout"])
    patch = frozen / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    qualifiers = {}
    for name, relative, validator in (
        ("prefix", "native_prefix_qualification", validate_prefix),
        ("lifecycle", "native_tokencake_qualification", validate_lifecycle),
    ):
        path = arm / relative / "summary.json"
        validator(
            causal.load(path), engine_tree=plan["engine_tree"],
            patch_sha256=sha256_file(patch), root=frozen,
        )
        qualifiers[name] = {"path": str(path), "sha256": sha256_file(path)}
    ready = causal.load(arm / "server/native_telemetry_ready.json")
    scheduler = causal.reservation.process(ready["scheduler_pid"])
    if scheduler is None or scheduler["state"] == "Z":
        raise ValueError("TokenCake GPU scheduler is not live")
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if not any(
        row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
        for row in causal.owned_processes([state["server"]])
    ):
        raise ValueError("TokenCake GPU scheduler is not an owned descendant")
    processes = {**result["actual_live_processes"], "gpu_scheduler": scheduler}
    if any(os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in processes.values()):
        raise ValueError("TokenCake serving or replay priority differs")
    gpu_pids = [
        int(value) for value in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if value.strip()
    ]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("TokenCake GPU scheduler is absent or concurrent")
    result.update(
        actual_owned_nice_zero_processes=processes, sole_native_gpu_pid=scheduler["pid"],
        same_complete_V230_V311_work_and_capacity=True,
        actual_declared_tokencake_parameters=parameters,
        fresh_prefix_and_twelve_lifecycle_scene_qualifiers=qualifiers,
        operator_sha256=sha256_file(Path(__file__)),
        complete_workload_or_performance_verified=False,
    )
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        "expected_requests": result["expected_requests"],
        "sole_native_gpu_pid": scheduler["pid"], "actual_parameters": parameters,
        "complete_workload_or_performance_verified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
