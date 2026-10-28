"""Verify actual Full startup, current policy bindings and exact control work."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
REPORTS = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "sibling_service_capacity_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve previous startup evidence")
    plan = json.loads(REGISTRATION.read_bytes())
    sys.path.insert(0, plan["execution_checkout"])
    from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
    from beliefkv.experiments.source_provenance import sha256_file
    from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
    from scripts.audit_collecting_agent_pipeline import validate_receipts
    from scripts.audit_sealed_wait_prefetch import SealedRecords
    from scripts import run_causal_comparison as causal

    control = causal.load(CONTROL)
    causal.comparison.verify_sources(plan)
    causal.comparison.verify_sources(control)
    causal.fresh.check_hold(plan, released=True)
    if not causal.predecessor_ready({**control, "after_registration": str(CONTROL)}):
        raise ValueError("matching complete control has not actually exited")
    fields = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "sampling_mode",
        "stream_response_metrics", "request_timeout_s", "max_running_requests",
    )
    if (
        plan["order"] != ["full"] or control["order"] != ["thunderagent_acting_decay"]
        or plan["engine_tree"] != SIBLING_CAPACITY_TREE
        or control["engine_tree"] != COMMON_TREE
        or any(plan.get(field) != control.get(field) for field in fields)
    ):
        raise ValueError("work, serving or explicit native-engine pair differs")
    overlays = {}
    for label, current in (("full", plan), ("control", control)):
        overlay = current["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing artifact changed")
        overlays[label] = {key: value for key, value in overlay.items() if key != "path"}
    if overlays["full"] != overlays["control"]:
        raise ValueError("tool timing contents or provenance differ")
    manifest = causal.load(REPORTS / "qualified_shared_cpu_r2.sources.json")
    if any(plan["source_sha256"].get(name) != digest for name, digest in manifest.items()):
        raise ValueError("registered Full differs from CPU-qualified application")
    model_path = REPORTS / "model_r1/report.json"
    model = causal.load(model_path)
    if (
        model["status"] != "unforced_layer_handoff_model_qualified"
        or model["configuration_sha256"] != sha256_file(Path(plan["experiment_configuration"]))
        or model["model"]["maximum_logprob_delta"] != {"b_handoff": 0., "a_recovered": 0.}
    ):
        raise ValueError("current unforced-model proof does not match")
    smoke_path = ROOT / "experiments/raw/sibling_service_capacity_position_20261028_r1/gpu_smoke.json"
    smoke = causal.load(smoke_path)
    if any(smoke.get(key) is not True for key in (
        "native_position_sampling_verified", "native_data_plane_sample_verified",
        "gpu_process_verified",
    )) or smoke["requests"] != 24 or smoke["native_stop_requests"] != 24:
        raise ValueError("current native tree lacks complete position/session/stop evidence")
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if state["status"] != "running_full":
        raise ValueError("Full has not started actual measured work")
    processes = {}
    for name in ("watcher", "server", "driver"):
        row = state[name]
        if not causal.reservation.alive(row) or os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0:
            raise ValueError("owned serving process exited or lost nice0")
        processes[name] = causal.reservation.process(row["pid"])
    gpu_pids = {
        int(line) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owners = [
        row for row in state["owned"]
        if row["pid"] in gpu_pids and causal.reservation.alive(row)
    ]
    if len(owners) != 1 or gpu_pids != {owners[0]["pid"]}:
        raise ValueError("GPU compute context is not the sole owned scheduler")
    processes["gpu_scheduler"] = causal.reservation.process(owners[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    expected = {
        "physical_disabled": False, "agent_join_blocked_handoff": True,
        "tool_offload_min_survival": .75, "host_demand_reserve_bytes": 2147483648,
        "layer_handoff_enabled": True, "layer_handoff_d2h_layers": 8,
        "layer_handoff_h2d_layers": 4, "tool_prefetch": False,
        "tool_prefetch_priority_ms": 0., "tool_offload_before_admission": True,
        "execution_frontier_restore": False,
    }
    if any(latest.get(name) != value for name, value in expected.items()):
        raise ValueError("observed Full settings differ from the declaration")
    admission = latest["agent_admission"]
    validate_reference_admission(
        admission, join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True, join_blocked_handoff=True,
    )
    if (
        admission["sibling_native_batch_preference"] is not True
        or admission["sibling_admission_fitting_subset"] is not True
        or admission["sibling_native_batch_max_members"] != 4
        or admission["sibling_admission_update_scope"]
        != "registration_join_events_and_bounded_release_retry"
    ):
        raise ValueError("new sibling selection and bounded retry are not effective")
    reader = SealedRecords()
    expected_work = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    receipts = validate_receipts(reader(arm / "replay/native_requests.jsonl"), expected_work)
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual current Full startup; no complete-work or performance claim.",
        "registration": str(REGISTRATION), "registration_sha256": sha256_file(REGISTRATION),
        "code_commit": plan["code_commit"], "native_engine": SIBLING_CAPACITY_TREE,
        "control_native_engine": COMMON_TREE, "same_common_fields": list(fields),
        "same_tool_timing_sha256": overlays["full"]["sha256"],
        "actual_nice0_processes": processes, "verified_runtime_settings": expected,
        "sibling_native_batch_preference_effective": True,
        "sibling_fitting_subset_effective": True,
        "exact_completed_receipts": receipts, "input_jsonl_prefixes": reader.prefixes,
        "latest_runtime_snapshot": latest, "model_proof_sha256": sha256_file(model_path),
        "position_smoke_sha256": sha256_file(smoke_path),
        "operator_sha256": sha256_file(Path(__file__)), "performance_accepted": False,
    })
    print(json.dumps({
        "code_commit": plan["code_commit"], "exact_completed_receipts": receipts,
        "actual_process_pids": {name: row["pid"] for name, row in processes.items()},
        "performance_accepted": False,
    }, indent=2))


if __name__ == "__main__":
    main()
