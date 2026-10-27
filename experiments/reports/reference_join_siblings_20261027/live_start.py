"""Seal actual owned startup and exact completed work for the sibling candidate."""

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plot-waiter-pid", type=int, required=True)
    parser.add_argument("--plot-waiter-start-ticks", required=True)
    args = parser.parse_args()
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve sibling startup evidence")
    registration = Path("/home/longhao/experiment/ops/reference_join_siblings_20261027_r1.registration.json")
    control_path = Path("/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json")
    predecessor_path = Path("/home/longhao/experiment/ops/reference_join_free_admission_20261027_r1.registration.json")
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    for path in (predecessor_path, control_path):
        previous = causal.load(path)
        if not causal.predecessor_ready({**previous, "after_registration": str(path)}):
            raise ValueError("previous Full or matching control is not complete and exited")
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
        raise ValueError("sibling candidate is not running its actual replay")
    processes = {}
    for name in ("watcher", "server", "driver"):
        identity = state[name]
        if (
            not causal.reservation.alive(identity)
            or os.getpriority(os.PRIO_PROCESS, identity["pid"]) != 0
        ):
            raise ValueError("owned serving process identity or priority changed")
        processes[name] = causal.reservation.process(identity["pid"])
    gpu_pids = {
        int(line.strip()) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owned_gpu = [
        identity for identity in state["owned"]
        if identity["pid"] in gpu_pids and causal.reservation.alive(identity)
    ]
    if len(owned_gpu) != 1 or os.getpriority(os.PRIO_PROCESS, owned_gpu[0]["pid"]) != 0:
        raise ValueError("actual GPU process is not the owned scheduler at nice0")
    processes["gpu_scheduler"] = causal.reservation.process(owned_gpu[0]["pid"])
    waiter = causal.reservation.process(args.plot_waiter_pid)
    if (
        waiter is None or not causal.reservation.alive(waiter)
        or waiter["start_ticks"] != args.plot_waiter_start_ticks
        or os.getpriority(os.PRIO_PROCESS, waiter["pid"]) != 19
        or "scripts/deferred_causal_decode_plots.py" not in waiter["argv"]
        or str(registration) not in waiter["argv"]
    ):
        raise ValueError("declared CPU plotting waiter identity changed")
    reader = SealedRecords()
    arm = Path(plan["run_root"]) / "full"
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    completed = validate_receipts(reader(arm / "replay/native_requests.jsonl"), expected)
    states, grants = [], []
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "admission_runtime_state":
            states.append(row)
        elif row.get("event") in (
            "agent_join_free_capacity_admitted", "agent_join_siblings_coadmitted",
        ):
            if (
                row["remaining_after_tokens"] < 0
                or row["remaining_before_tokens"] - row["required_tokens"] != row["remaining_after_tokens"]
                or sum(item["complete_budget_tokens"] for item in row["programs"]) != row["required_tokens"]
                or row.get("native_capacity_authority_preserved") is not True
            ):
                raise ValueError("executed grant journal does not reconcile")
            grants.append(row)
    if not states or any(row.get("physical_disabled") is not False for row in states):
        raise ValueError("native physical control is unhealthy")
    latest = states[-1]
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
    )
    if latest["agent_admission"].get("extra_join_grant_accounting") != (
        "refresh_observed_native_outputs_before_each_capacity_check"
    ):
        raise ValueError("new grant streaming-budget refresh is not effective")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual_owned_start_and_sealed_prefix_not_complete_performance",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_v345": list(common),
        "previous_free_join_and_control_actual_complete_exit": True,
        "actual_nice0_serving_processes": processes, "actual_nice19_cpu_plot_waiter": waiter,
        "expected_requests": len(expected), "exact_completed_receipts": completed,
        "executed_logical_grants_in_prefix": grants,
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
