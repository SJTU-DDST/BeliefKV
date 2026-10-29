"""Observe the matched live Full without calculating completed-subset JCT."""

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
REGISTRATION = OPS / "warm_start_free_extent_20261029_r1.registration.json"
CONTROL = OPS / "warm_start_thunder_20261029_r1.registration.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import audit
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "full_live_start.json"
    if output.exists():
        raise FileExistsError("preserve the first live observation")
    plan, control = causal.load(REGISTRATION), causal.load(CONTROL)
    fields = (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "workflow_ids", "quality_exclusions",
        "quality_exclusion_details", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests", "workflow_arrivals",
    )
    if any(plan[field] != control[field] for field in fields) or plan["engine_tree"] != FREE_EXTENT_TREE:
        raise ValueError("Full and the completed control differ in work, arrivals or capacity")
    tool = dict(plan["tool_timing_overlay"])
    prior_tool = dict(control["tool_timing_overlay"])
    tool.pop("path", None)
    prior_tool.pop("path", None)
    if tool != prior_tool:
        raise ValueError("Full changed observable categories or actual tool timing")
    expected = Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"
    result = audit(REGISTRATION, "full", expected)
    if result["exact_completed_receipts"] <= 0:
        raise ValueError("no complete Full receipt has been verified yet")
    runtime = result["latest_runtime_snapshot"]
    if any(runtime.get(name) is not expected for name, expected in {
        "tool_prefetch": False, "execution_frontier_restore": False,
        "layer_handoff_enabled": True, "layer_handoff_free_extent": True,
        "demand_prefill_pipeline": True, "demand_prefill_pipeline_installed": True,
    }.items()):
        raise ValueError("actual Full feature configuration differs")
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    scheduler = causal.reservation.process(
        causal.load(arm / "server/native_telemetry_ready.json")["scheduler_pid"],
    )
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if scheduler is None or not any(
        row["pid"] == scheduler["pid"] and row["start_ticks"] == scheduler["start_ticks"]
        for row in causal.owned_processes([state["server"]])
    ):
        raise ValueError("native GPU scheduler is not an actual owned descendant")
    processes = {**result["actual_live_processes"], "gpu_scheduler": scheduler}
    if any(os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in processes.values()):
        raise ValueError("Full is not running at the shared nice-zero setting")
    gpu_pids = [
        int(value) for value in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if value.strip()
    ]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("another GPU workload overlaps Full")
    reader = SealedRecords()
    offsets = {
        row["workflow_id"]: row["offset_seconds"] * 1000.
        for row in plan["workflow_arrivals"]["arrival_schedule"]
    }
    arrivals, origin, seen = [], None, set()
    for row in reader(arm / "replay/runtime_events.replay.jsonl"):
        if row.get("kind") != "workflow_start":
            continue
        identity, when, attrs = row["workflow_id"], row["ts_ms"], row["attributes"]
        if identity in seen or identity not in offsets:
            raise ValueError("invalid observed arrival identity")
        if origin is None:
            origin = when - offsets[identity]
        delivered, lag = attrs["arrival_delivery_monotonic_ms"], attrs["arrival_delivery_lag_ms"]
        if (
            abs(when - (origin + offsets[identity])) > 1e-6
            or attrs["scheduled_arrival_monotonic_ms"] != when
            or delivered < when or lag != delivered - when
        ):
            raise ValueError("actual Full arrival differs from the shared frozen table")
        seen.add(identity)
        arrivals.append({"workflow_id": identity, "offset_ms": offsets[identity], "delivery_lag_ms": lag})
    if sum(row["offset_ms"] == 0. for row in arrivals) != 10:
        raise ValueError("the ten initial Full roots have not actually arrived")
    result.update(
        observed_at_shanghai=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        matched_control=str(CONTROL), shared_contract_fields=len(fields),
        actual_owned_nice_zero_processes=processes, sole_native_gpu_pid=scheduler["pid"],
        actual_arrivals=arrivals, arrival_jsonl_prefixes=reader.prefixes,
        operator_sha256=sha256_file(Path(__file__)),
        complete_performance_verified=False, compared_completed_subset_jct=False,
    )
    causal.reservation.write_json(output, result)
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        "expected_requests": result["expected_requests"],
        "observed_arrivals": len(arrivals), "sole_native_gpu_pid": scheduler["pid"],
        "complete_performance_verified": False,
    }))


if __name__ == "__main__":
    main()
