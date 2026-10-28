"""Verify the live, frozen online-refresh Full without claiming performance."""

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve the original live observation")
    ops = Path("/home/longhao/experiment/ops")
    name = "tool_online_refresh_20261027_r1"
    registration = ops / (name + ".registration.json")
    control_path = ops / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
    previous_path = ops / "tool_prediction_curve_repair_20261027_r1.registration.json"
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    causal.fresh.check_hold(plan, released=True)
    for path in (control_path, previous_path):
        prior = causal.load(path)
        if not causal.predecessor_ready({**prior, "after_registration": str(path)}):
            raise ValueError("the prior Full or reused control has not actually exited")
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "engine_tree",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("Full changed common work, timing, engine or serving")
    cpu_path = output.parent / "shared_cpu.evidence.json"
    cpu = causal.load(cpu_path)
    sources = causal.load(Path(cpu["source_manifest"]["path"]))
    for path in (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/predicted_tool_offload.py",
        "beliefkv/predictor/online_workload_timing.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/experiments/internal_comparison.py",
        "scripts/run_causal_comparison.py",
    ):
        if plan["source_sha256"][path] != sources[path]:
            raise ValueError("the running policy differs from its CPU qualification")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("Full is not currently running")
    processes = {}
    for label in ("watcher", "server", "driver"):
        identity = state[label]
        if not causal.reservation.alive(identity) or os.getpriority(os.PRIO_PROCESS, identity["pid"]) != 0:
            raise ValueError("an actual serving identity or priority changed")
        processes[label] = causal.reservation.process(identity["pid"])
    gpu = {
        int(line) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    schedulers = [item for item in state["owned"] if item["pid"] in gpu and causal.reservation.alive(item)]
    if len(schedulers) != 1 or gpu != {schedulers[0]["pid"]}:
        raise ValueError("GPU ownership is not the one registered scheduler")
    identity = schedulers[0]
    if os.getpriority(os.PRIO_PROCESS, identity["pid"]) != 0:
        raise ValueError("the GPU scheduler is not nice0")
    processes["gpu_scheduler"] = causal.reservation.process(identity["pid"])
    waiter = causal.load(ops / (name + ".plots.launch.json"))["process"]
    if not causal.reservation.alive(waiter) or os.getpriority(os.PRIO_PROCESS, waiter["pid"]) != 19:
        raise ValueError("the CPU plotting waiter is not actually live at nice19")
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if (
        latest["physical_disabled"] is not False
        or latest["online_tool_timing"] is not True
        or latest["tool_timing_only"] is not False
        or latest["layer_handoff_enabled"] is not False
        or latest["tool_offload_before_admission"] is not True
    ):
        raise ValueError("actual online-refresh configuration is unhealthy")
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True,
    )
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    if len(expected) != plan["source_plan"]["requests"]:
        raise ValueError("the complete control does not have the declared work")
    reader = SealedRecords()
    completed = validate_receipts(reader(arm / "replay/native_requests.jsonl"), expected)
    forecasts = Counter()
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "tool_wait_forecast":
            if row["expires_monotonic_ms"] <= row["observed_monotonic_ms"]:
                raise ValueError("an online forecast has a nonpositive lifetime")
            forecasts[(
                row["workflow_id"], row["invocation_id"], row["context_id"],
                row["context_epoch"], row["invocation_revision_ts_ms"],
            )] += 1
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual live serving and independently sealed prefixes; not complete performance.",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_v345": list(common),
        "actual_nice0_serving_processes": processes,
        "actual_nice19_cpu_plot_waiter": causal.reservation.process(waiter["pid"]),
        "qualified_cpu_evidence_sha256": sha256_file(cpu_path),
        "exact_completed_receipts": completed,
        "forecast_records": sum(forecasts.values()),
        "same_observed_waits_with_repeated_forecasts": sum(value > 1 for value in forecasts.values()),
        "input_jsonl_prefixes": reader.prefixes,
        "latest_runtime_snapshot": latest,
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_work_verified": False, "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "code_commit", "engine_tree", "exact_completed_receipts", "forecast_records",
        "same_observed_waits_with_repeated_forecasts", "performance_accepted",
    )}, indent=2))


if __name__ == "__main__":
    main()
