"""Verify actual serving identities and the frozen pre-admission offload flag."""

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
        raise FileExistsError("preserve original pre-admission offload startup")
    ops = Path("/home/longhao/experiment/ops")
    registration = ops / "tool_offload_before_admission_20261027_r1.registration.json"
    control_path = ops / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
    predecessor_path = ops / "reference_free_continuation_20261027_r1.registration.json"
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    causal.fresh.check_hold(plan, released=True)
    for path in (predecessor_path, control_path):
        previous = causal.load(path)
        if not causal.predecessor_ready({**previous, "after_registration": str(path)}):
            raise ValueError("predecessor or complete control has not actually exited")
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "engine_tree",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("pre-admission offload changed common work, timing or serving")
    cpu_path = output.parent / "qualified_shared_cpu.evidence.json"
    cpu = causal.load(cpu_path)
    sources = causal.load(Path(cpu["source_manifest"]["path"]))
    for name in (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/predicted_tool_offload.py",
        "beliefkv/runtime/reference_free_continuation.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/experiments/internal_comparison.py",
        "scripts/run_causal_comparison.py",
    ):
        if plan["source_sha256"][name] != sources[name]:
            raise ValueError("running policy differs from qualified CPU sources")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("the actual Full replay has not started")
    processes = {}
    for name in ("watcher", "server", "driver"):
        identity = state[name]
        if (
            not causal.reservation.alive(identity)
            or os.getpriority(os.PRIO_PROCESS, identity["pid"]) != 0
        ):
            raise ValueError("owned serving identity or nice priority changed")
        processes[name] = causal.reservation.process(identity["pid"])
    gpu_pids = {
        int(line.strip()) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    schedulers = [
        row for row in state["owned"]
        if row["pid"] in gpu_pids and causal.reservation.alive(row)
    ]
    if (
        len(schedulers) != 1 or gpu_pids != {schedulers[0]["pid"]}
        or os.getpriority(os.PRIO_PROCESS, schedulers[0]["pid"]) != 0
    ):
        raise ValueError("GPU ownership is not one actual nice0 scheduler")
    processes["gpu_scheduler"] = causal.reservation.process(schedulers[0]["pid"])
    waiter = causal.load(ops / "tool_offload_before_admission_20261027_r1.plots.launch.json")["process"]
    if (
        not causal.reservation.alive(waiter)
        or os.getpriority(os.PRIO_PROCESS, waiter["pid"]) != 19
    ):
        raise ValueError("CPU plotting waiter is not the actual nice19 process")
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if (
        latest.get("physical_disabled") is not False
        or latest.get("tool_offload_before_admission") is not True
        or latest.get("predicted_tool_offload") is not True
        or latest.get("tool_offload_policy") != "DemandAwareToolOffload"
    ):
        raise ValueError("actual early offload configuration or physical control is unhealthy")
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True,
    )
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    if len(expected) != plan["source_plan"]["requests"]:
        raise ValueError("complete control has different work")
    reader = SealedRecords()
    completed = validate_receipts(reader(arm / "replay/native_requests.jsonl"), expected)
    early = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "predicted_tool_kv_evicted"
        and row.get("dispatch_stage") == "before_native_admission"
    ]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual startup and independently sealed collecting prefixes, not final performance",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_v345": list(common),
        "actual_nice0_serving_processes": processes,
        "actual_nice19_cpu_plot_waiter": causal.reservation.process(waiter["pid"]),
        "cpu_evidence_sha256": sha256_file(cpu_path),
        "exact_completed_receipts": completed,
        "early_node_release_records_in_sealed_prefix": len(early),
        "early_node_release_bytes_in_sealed_prefix": sum(row["freed_bytes"] for row in early),
        "first_early_release": early[0] if early else None,
        "input_jsonl_prefixes": reader.prefixes,
        "latest_runtime_snapshot": latest,
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_work_verified": False, "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "code_commit", "engine_tree", "exact_completed_receipts",
        "early_node_release_records_in_sealed_prefix",
        "early_node_release_bytes_in_sealed_prefix", "performance_accepted",
    )}, indent=2))


if __name__ == "__main__":
    main()
