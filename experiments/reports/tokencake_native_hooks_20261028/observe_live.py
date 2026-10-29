"""Seal the repaired TokenCake's actual native-loop startup and matched work."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "tokencake_locobench_native_hooks_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
LABEL = "tokencake_paper_observed"
sys.path.insert(0, json.loads(REGISTRATION.read_bytes())["execution_checkout"])

from beliefkv.experiments.native_engine_variant import COMMON_TREE, TOKENCAKE_TREE, engine_patch
from beliefkv.experiments.paper_baselines import (
    tokencake_parameters, validate_state, validate_tokencake_parameters,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tokencake_engine import verify_native_hooks
from scripts.audit_collecting_agent_pipeline import audit
from scripts import run_causal_comparison as causal
from scripts.qualify_paper_prefix_transfers import validate_report as validate_prefix
from scripts.qualify_tokencake_runtime import validate_report as validate_lifecycle


def main():
    output = HERE / "live_start.json"
    if output.exists():
        raise FileExistsError("preserve original repaired TokenCake observation")
    plan, control = causal.load(REGISTRATION), causal.load(CONTROL)
    common = (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "workflow_ids", "quality_exclusions",
        "quality_exclusion_details", "sampling_mode", "stream_response_metrics",
        "request_timeout_s",
    )
    if any(plan[name] != control[name] for name in common):
        raise ValueError("repaired TokenCake changed common work, serving or capacity")
    if plan["engine_tree"] != TOKENCAKE_TREE or control["engine_tree"] != COMMON_TREE:
        raise ValueError("unreviewed TokenCake/control native engine pair")
    overlays = [
        {name: value for name, value in item["tool_timing_overlay"].items() if name != "path"}
        for item in (plan, control)
    ]
    if overlays[0] != overlays[1]:
        raise ValueError("repaired TokenCake changed synthetic tool timers")
    for name in ("SERVER_RANDOM_SEED", "SAMPLING_SEED", "PYTHONHASHSEED"):
        if plan["reference_environment"][name] != control["reference_environment"][name]:
            raise ValueError("repaired TokenCake changed a common seed")
    expected = Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"
    result = audit(REGISTRATION, LABEL, expected)
    if not result["exact_completed_receipts"]:
        raise ValueError("repaired TokenCake has no completed real model requests")
    runtime = result["latest_runtime_snapshot"]
    validate_state(LABEL, runtime)
    parameters = tokencake_parameters(plan["reference_environment"])
    validate_tokencake_parameters(runtime["paper_baseline"], parameters)
    arm, frozen = Path(plan["run_root"]) / LABEL, Path(plan["execution_checkout"])
    causal.observed_configuration(plan, LABEL, arm)
    hooks = verify_native_hooks(Path(plan["engine_checkout"]))
    patch = engine_patch(frozen, TOKENCAKE_TREE)
    qualifiers = {}
    for name, relative, validator in (
        ("prefix", "native_prefix_qualification", validate_prefix),
        ("lifecycle", "native_tokencake_qualification", validate_lifecycle),
    ):
        path = arm / relative / "summary.json"
        validator(
            causal.load(path), engine_tree=TOKENCAKE_TREE,
            patch_sha256=sha256_file(patch), root=frozen,
        )
        qualifiers[name] = {"path": str(path), "sha256": sha256_file(path)}
    ready = causal.load(arm / "server/native_telemetry_ready.json")
    scheduler = causal.reservation.process(ready["scheduler_pid"])
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if scheduler is None or not any(
        row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
        for row in causal.owned_processes([state["server"]])
    ):
        raise ValueError("TokenCake native scheduler is not an actual owned descendant")
    processes = {**result["actual_live_processes"], "gpu_scheduler": scheduler}
    if any(os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in processes.values()):
        raise ValueError("repaired TokenCake changed serving or replay CPU priority")
    gpu_pids = [
        int(value) for value in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if value.strip()
    ]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("actual TokenCake GPU process is absent or concurrent")
    result.update(
        actual_owned_nice_zero_processes=processes,
        sole_native_gpu_pid=scheduler["pid"],
        actual_declared_tokencake_parameters=parameters,
        exact_optional_native_hooks_projection=hooks,
        fresh_prefix_and_twelve_native_loop_qualifiers=qualifiers,
        same_complete_V230_V311_work_capacity_seeds=True,
        explicit_native_engine_difference_preserved=True,
        operator_sha256=sha256_file(Path(__file__)),
        complete_workload_or_performance_verified=False,
    )
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        "expected_requests": result["expected_requests"],
        "sole_native_gpu_pid": scheduler["pid"],
        "actual_parameters": parameters,
        "complete_workload_or_performance_verified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
