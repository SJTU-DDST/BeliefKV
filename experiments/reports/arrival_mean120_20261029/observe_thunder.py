"""Independently verify the live mean120 control and actual scheduled releases."""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "arrival_mean120_thunder_20261029_r1.registration.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.paper_baselines import validate_state
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import audit
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "thunder_live_start.json"
    if output.exists():
        raise FileExistsError("preserve the first live mean120 observation")
    plan = causal.load(REGISTRATION)
    label = "thunderagent_acting_decay"
    if (
        plan["order"] != [label] or plan["workflow_arrivals"]["initial_workflows"] != 10
        or plan["workflow_arrivals"]["rate_per_second"] != 1 / 120
        or plan["workflow_arrivals"]["seed"] != 21
    ):
        raise ValueError("actual live control differs from requested mean120 arrivals")
    expected = ROOT / "experiments/raw/warm_start_thunder_20261029_r1/thunderagent_acting_decay/replay/native_requests.jsonl"
    result = audit(REGISTRATION, label, expected)
    runtime = result["latest_runtime_snapshot"]
    validate_state(label, runtime)
    if any(runtime.get(name, False) is not False for name in (
        "tool_prefetch", "execution_frontier_restore", "layer_handoff_enabled",
        "layer_handoff_free_extent", "demand_prefill_pipeline",
    )):
        raise ValueError("the paper control inherited a Full policy")
    arm = Path(plan["run_root"]) / label
    causal.observed_configuration(plan, label, arm)
    ready = causal.load(arm / "server/native_telemetry_ready.json")
    scheduler = causal.reservation.process(ready["scheduler_pid"])
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if scheduler is None or not any(
        row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
        for row in causal.owned_processes([state["server"]])
    ):
        raise ValueError("GPU scheduler is not an actual owned descendant")
    processes = {**result["actual_live_processes"], "gpu_scheduler": scheduler}
    if any(os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in processes.values()):
        raise ValueError("live comparison processes must use the shared nice0 setting")
    gpu_pids = [
        int(value) for value in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if value.strip()
    ]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("a foreign GPU workload overlaps the control")
    offsets = {
        row["workflow_id"]: row["offset_seconds"] * 1000.
        for row in plan["workflow_arrivals"]["arrival_schedule"]
    }
    seen, observed, origin, reader = set(), [], None, SealedRecords()
    for row in reader(arm / "replay/runtime_events.replay.jsonl"):
        if row.get("kind") != "workflow_start":
            continue
        identity, timestamp, attrs = row["workflow_id"], row["ts_ms"], row["attributes"]
        if identity in seen or identity not in offsets:
            raise ValueError("duplicate or foreign actual workflow arrival")
        if origin is None:
            origin = timestamp - offsets[identity]
        delivered, lag = attrs["arrival_delivery_monotonic_ms"], attrs["arrival_delivery_lag_ms"]
        if (
            abs(timestamp - origin - offsets[identity]) > 1e-6
            or attrs["scheduled_arrival_monotonic_ms"] != timestamp
            or delivered < timestamp or lag != delivered - timestamp
        ):
            raise ValueError("actual release changed the planned arrival/JCT clock")
        seen.add(identity)
        observed.append({"workflow_id": identity, "offset_ms": offsets[identity], "delivery_lag_ms": lag})
    if sum(row["offset_ms"] == 0. for row in observed) != 10:
        raise ValueError("initial ten workflows have not all actually arrived")
    result.update(
        observed_at_shanghai=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        actual_arrivals=observed, arrival_jsonl_prefixes=reader.prefixes,
        actual_owned_nice_zero_processes=processes, sole_native_gpu_pid=scheduler["pid"],
        operator_sha256=sha256_file(Path(__file__)),
        complete_performance_verified=False, compared_completed_subset_jct=False,
    )
    causal.reservation.write_json(output, result)
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        "expected_requests": result["expected_requests"], "actual_arrivals": len(observed),
        "sole_gpu_pid": scheduler["pid"], "complete_performance_verified": False,
    }))


if __name__ == "__main__":
    main()
