"""Seal actual owned startup and an exact receipt prefix for the JOIN candidate."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from scripts import run_causal_comparison as causal
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve JOIN startup evidence")
    registration = Path("/home/longhao/experiment/ops/reference_join_free_admission_20261027_r1.registration.json")
    control_path = Path("/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json")
    predecessor_path = Path("/home/longhao/experiment/ops/reference_consumer_fix_20261027_r1.registration.json")
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    predecessor = causal.load(predecessor_path)
    if not causal.predecessor_ready({**predecessor, "after_registration": str(predecessor_path)}):
        raise ValueError("R1 predecessor still owns a live process")
    if not causal.predecessor_ready({**control, "after_registration": str(control_path)}):
        raise ValueError("matched control is incomplete")
    causal.fresh.check_hold(plan, released=True)
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "engine_tree",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("new Full changed common work or serving parameters")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("new candidate is not running its actual replay")
    processes = {}
    for name in ("watcher", "server", "driver"):
        expected = state[name]
        if not causal.reservation.alive(expected) or os.getpriority(os.PRIO_PROCESS, expected["pid"]) != 0:
            raise ValueError("owned serving process identity or priority changed")
        processes[name] = causal.reservation.process(expected["pid"])
    gpu_pids = {
        int(line.strip()) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owned_gpu = [
        item for item in state["owned"]
        if item["pid"] in gpu_pids and causal.reservation.alive(item)
    ]
    if len(owned_gpu) != 1 or os.getpriority(os.PRIO_PROCESS, owned_gpu[0]["pid"]) != 0:
        raise ValueError("actual GPU process is not the owned scheduler at nice0")
    processes["gpu_scheduler"] = causal.reservation.process(owned_gpu[0]["pid"])
    plot_waiter = causal.reservation.process(231428)
    if (
        plot_waiter is None or not causal.reservation.alive(plot_waiter)
        or os.getpriority(os.PRIO_PROCESS, plot_waiter["pid"]) != 19
    ):
        raise ValueError("declared CPU plotting waiter is not live at nice19")
    reader = SealedRecords()
    arm = Path(plan["run_root"]) / "full"
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    completed = validate_receipts(reader(arm / "replay/native_requests.jsonl"), expected)
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    if not states or any(row.get("physical_disabled") is not False for row in states):
        raise ValueError("native physical control is unhealthy")
    latest = states[-1]
    validate_reference_admission(latest["agent_admission"], join_free_admission=True)
    if latest.get("agent_join_free_admission") is not True:
        raise ValueError("new JOIN admission switch is not effective")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual_owned_start_and_sealed_prefix_not_complete_performance",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_v345": list(common),
        "r1_predecessor_complete_and_actual_exit": True,
        "actual_nice0_serving_processes": processes,
        "actual_nice19_cpu_plot_waiter": plot_waiter,
        "expected_requests": len(expected), "exact_completed_receipts": completed,
        "latest_runtime_snapshot": latest, "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_work_verified": False, "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        key: result[key] for key in (
            "code_commit", "expected_requests", "exact_completed_receipts",
            "complete_work_verified", "performance_accepted",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
