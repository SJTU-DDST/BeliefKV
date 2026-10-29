"""Observe the actual control without treating its live prefix as a result."""

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "warm_start_thunder_20261029_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
LABEL = "thunderagent_acting_decay"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.paper_baselines import validate_state
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import audit
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "live_start.json")
    output = parser.parse_args().output
    if output.exists():
        raise FileExistsError("preserve the original sealed observation")
    plan, control = causal.load(REGISTRATION), causal.load(CONTROL)
    common = (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "workflow_ids", "quality_exclusions",
        "quality_exclusion_details", "engine_tree",
    )
    for field in common:
        if plan[field] != control[field]:
            raise ValueError("changed common work or native contract: " + field)
    overlay, previous = dict(plan["tool_timing_overlay"]), dict(control["tool_timing_overlay"])
    overlay.pop("path", None)
    previous.pop("path", None)
    if overlay != previous:
        raise ValueError("changed tool timing contract")
    expected = Path(control["run_root"]) / LABEL / "replay/native_requests.jsonl"
    result = audit(REGISTRATION, LABEL, expected)
    if result["exact_completed_receipts"] <= 0:
        raise ValueError("no completed request has been verified")
    validate_state(LABEL, result["latest_runtime_snapshot"])
    arm = Path(plan["run_root"]) / LABEL
    causal.observed_configuration(plan, LABEL, arm)
    scheduler = causal.reservation.process(
        causal.load(arm / "server/native_telemetry_ready.json")["scheduler_pid"],
    )
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if scheduler is None or scheduler["state"] == "Z" or not any(
        row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
        for row in causal.owned_processes([state["server"]])
    ):
        raise ValueError("native GPU scheduler is not a live owned descendant")
    processes = {**result["actual_live_processes"], "gpu_scheduler": scheduler}
    if any(os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in processes.values()):
        raise ValueError("control does not use nice zero")
    gpu_pids = [
        int(value) for value in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if value.strip()
    ]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("another GPU workload is concurrent or the scheduler is absent")
    reader = SealedRecords()
    events = reader(arm / "replay/runtime_events.replay.jsonl")
    expected_arrivals = {
        row["workflow_id"]: row["offset_seconds"] * 1000.
        for row in plan["workflow_arrivals"]["arrival_schedule"]
    }
    arrivals, origin = [], None
    seen = set()
    for event in events:
        if event.get("kind") != "workflow_start":
            continue
        identity, attrs, when = event["workflow_id"], event["attributes"], event["ts_ms"]
        delivered, lag = attrs.get("arrival_delivery_monotonic_ms"), attrs.get("arrival_delivery_lag_ms")
        if (
            identity in seen or identity not in expected_arrivals
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in (when, delivered, lag))
        ):
            raise ValueError("invalid actual arrival")
        if origin is None:
            origin = when - expected_arrivals[identity]
        if (
            abs(when - (origin + expected_arrivals[identity])) > 1e-6
            or attrs.get("scheduled_arrival_monotonic_ms") != when
            or delivered < when or lag != delivered - when
        ):
            raise ValueError("actual arrival does not match the frozen table")
        seen.add(identity)
        arrivals.append({
            "workflow_id": identity, "offset_seconds": expected_arrivals[identity] / 1000.,
            "scheduled_monotonic_ms": when, "delivery_lag_ms": lag,
        })
    if len([row for row in arrivals if row["offset_seconds"] == 0.]) != 10:
        raise ValueError("the ten initial roots were not actually delivered")
    result.update(
        observed_at_shanghai=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        same_complete_work_and_capacity_fields=len(common), same_tool_timing_contract=True,
        actual_owned_nice_zero_processes=processes, sole_native_gpu_pid=scheduler["pid"],
        actual_arrivals=arrivals, observed_arrival_origin_monotonic_ms=origin,
        arrival_schedule_sha256=plan["workflow_arrivals"]["arrival_schedule_sha256"],
        arrival_jsonl_prefixes=reader.prefixes, operator_sha256=sha256_file(Path(__file__)),
        burst_control_is_not_a_matched_arrival_result=True,
        complete_workload_or_performance_verified=False,
    )
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        "expected_requests": result["expected_requests"],
        "actual_workflow_arrivals": len(arrivals), "sole_native_gpu_pid": scheduler["pid"],
        "complete_workload_or_performance_verified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
