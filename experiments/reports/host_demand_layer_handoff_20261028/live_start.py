"""Verify the actual combined Full, retaining the explicit native-engine delta."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import COMMON_TREE, LAYER_TREE
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    ops = Path("/home/longhao/experiment/ops")
    path = ops / "host_demand_layer_handoff_20261028_r1.registration.json"
    control_path = ops / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
    plan, control = causal.load(path), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    causal.fresh.check_hold(plan, released=True)
    if not causal.predecessor_ready({**control, "after_registration": str(control_path)}):
        raise ValueError("the reused control has not actually exited")
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if plan["order"] != ["full"] or any(plan.get(key) != control.get(key) for key in common):
        raise ValueError("work, timing or serving differs")
    if plan["engine_tree"] != LAYER_TREE or control["engine_tree"] != COMMON_TREE:
        raise ValueError("undeclared native-engine pair")
    directory = output.parent
    cpu = causal.load(directory / "shared_cpu.evidence.json")
    for name, digest in causal.load(directory / "shared_cpu.sources.json").items():
        if plan["source_sha256"].get(name) != digest:
            raise ValueError("Full differs from current CPU evidence: " + name)
    model_path = directory / "combined_model/report.json"
    model = causal.load(model_path)
    if (
        model["status"] != "unforced_layer_handoff_model_qualified"
        or model["configuration_sha256"] != sha256_file(Path(plan["experiment_configuration"]))
        or not model["model"]["handoffs"]
        or any(row["minimum_remaining_survival"] != .75 for row in model["model"]["handoffs"])
    ):
        raise ValueError("combined model proof is incomplete")
    native = Path(plan["engine_checkout"])
    if subprocess.check_output(
        ["git", "-C", str(native), "rev-parse", model["native_engine"]["commit"] + "^{tree}"],
        text=True,
    ).strip() != LAYER_TREE:
        raise ValueError("model and replay native trees differ")
    smoke_path = (
        ROOT / "experiments/raw/host_demand_layer_position_20261028_r1/gpu_smoke.json"
    )
    smoke = causal.load(smoke_path)
    if not (
        smoke["requests"] == 24 and smoke["native_data_plane_sample_verified"]
        and smoke["native_position_sampling_verified"] and smoke["gpu_process_verified"]
    ):
        raise ValueError("new-engine replay smoke is incomplete")
    state = causal.load(path.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("Full is not running")
    processes = {}
    for name in ("watcher", "server", "driver"):
        process = state[name]
        if not causal.reservation.alive(process) or os.getpriority(os.PRIO_PROCESS, process["pid"]) != 0:
            raise ValueError("serving process exited or lost nice0")
        processes[name] = causal.reservation.process(process["pid"])
    gpu = {
        int(line) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    schedulers = [row for row in state["owned"] if row["pid"] in gpu and causal.reservation.alive(row)]
    if len(schedulers) != 1 or gpu != {schedulers[0]["pid"]}:
        raise ValueError("unexpected GPU owner")
    processes["gpu_scheduler"] = causal.reservation.process(schedulers[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if any(latest.get(key) != expected for key, expected in (
        ("physical_disabled", False), ("tool_offload_min_survival", .75),
        ("host_demand_reserve_bytes", 2147483648), ("layer_handoff_enabled", True),
        ("tool_offload_before_admission", True),
    )):
        raise ValueError("actual combined configuration differs")
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True,
    )
    reader = SealedRecords()
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    completed = validate_receipts(reader(arm / "replay/native_requests.jsonl"), expected)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(path), "registration_sha256": sha256_file(path),
        "scope": "live_combined_full_not_isolated_module_or_complete_performance",
        "same_common_fields": list(common),
        "native_engine": LAYER_TREE, "control_native_engine": COMMON_TREE,
        "engine_delta_is_explicit_not_identical_engine_ablation": True,
        "code_commit": plan["code_commit"], "actual_nice0_processes": processes,
        "exact_completed_receipts": completed, "input_jsonl_prefixes": reader.prefixes,
        "model_proof_sha256": sha256_file(model_path),
        "new_engine_24_request_smoke_sha256": sha256_file(smoke_path),
        "cpu_passing_cases": cpu["passing_cases_including_subtests"],
        "latest_runtime_snapshot": latest, "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "code_commit", "native_engine", "exact_completed_receipts", "performance_accepted",
    )}, indent=2))


if __name__ == "__main__":
    main()
