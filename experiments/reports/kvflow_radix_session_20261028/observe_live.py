"""Seal actual KVFlow work, ownership and isolation after replay has started."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "kvflow_locobench_v230_v311_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
LABEL = "kvflow_observed_graph"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.paper_baselines import validate_state
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import audit
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "live_start.json"
    if output.exists():
        raise FileExistsError("preserve the original sealed startup observation")
    plan, control = causal.load(REGISTRATION), causal.load(CONTROL)
    common_fields = (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "workflow_ids", "quality_exclusions",
        "quality_exclusion_details", "engine_tree",
    )
    for name in common_fields:
        if plan[name] != control[name]:
            raise ValueError("formal baseline changed common work or native contract: " + name)
    overlay, prior_overlay = dict(plan["tool_timing_overlay"]), dict(control["tool_timing_overlay"])
    overlay.pop("path", None)
    prior_overlay.pop("path", None)
    if overlay != prior_overlay:
        raise ValueError("formal baseline changed the synthetic tool timing contract")
    expected = Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"
    result = audit(REGISTRATION, LABEL, expected)
    if not result["exact_completed_receipts"]:
        raise ValueError("the live baseline has no completed receipts yet")
    runtime = result["latest_runtime_snapshot"]
    validate_state(LABEL, runtime)
    if runtime["paper_baseline"]["counts"].get("native_radix_session_requests", 0) <= 0:
        raise ValueError("actual native session ingress has not executed")
    arm = Path(plan["run_root"]) / LABEL
    causal.observed_configuration(plan, LABEL, arm)
    ready = causal.load(arm / "server/native_telemetry_ready.json")
    scheduler = causal.reservation.process(ready["scheduler_pid"])
    if scheduler is None or scheduler["state"] == "Z":
        raise ValueError("native GPU scheduler is not actually alive")
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    owned = causal.owned_processes([state["server"]])
    if not any(
        row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
        for row in owned
    ):
        raise ValueError("native scheduler is not an owned server descendant")
    processes = {**result["actual_live_processes"], "gpu_scheduler": scheduler}
    if any(os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in processes.values()):
        raise ValueError("serving or replay process does not use the common nice-zero setting")
    gpu_pids = [
        int(value) for value in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if value.strip()
    ]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("native GPU process is absent or another workload is concurrent")
    prefix = causal.load(arm / "native_prefix_qualification/summary.json")
    if prefix["status"] != "native_prefix_transfers_qualified":
        raise ValueError("formal startup did not complete its own fresh prefix qualifier")
    result.update(
        matched_V345_work_and_capacity_fields=len(common_fields),
        same_tool_timing_contract=True, actual_owned_nice_zero_processes=processes,
        sole_native_gpu_pid=scheduler["pid"], fresh_session_prefix_qualification=True,
        operator_sha256=sha256_file(Path(__file__)),
        complete_workload_or_performance_verified=False,
    )
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        "expected_requests": result["expected_requests"],
        "native_session_requests_registered": runtime["paper_baseline"]["counts"]["native_radix_session_requests"],
        "sole_native_gpu_pid": scheduler["pid"],
        "complete_workload_or_performance_verified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
