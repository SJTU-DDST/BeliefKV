"""Verify current-source Full submission, exact prefix work and actual GPU ownership."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
FULL = OPS / "full_only_pressure_index_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
POSITION = OPS / "full_only_pressure_index_position_20261028_r1.registration.json"


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve original startup evidence")
    plan = json.loads(FULL.read_bytes())
    sys.path.insert(0, plan["execution_checkout"])
    from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
    from beliefkv.experiments.source_provenance import sha256_file
    from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
    from scripts.audit_collecting_agent_pipeline import validate_receipts
    from scripts.audit_sealed_wait_prefetch import SealedRecords
    from scripts import run_causal_comparison as causal

    control = causal.load(CONTROL)
    for candidate in (plan, control):
        causal.comparison.verify_sources(candidate)
    causal.fresh.check_hold(plan, released=True)
    if (
        plan["order"] != ["full"] or control["order"] != ["thunderagent_acting_decay"]
        or plan["engine_tree"] != SIBLING_CAPACITY_TREE or control["engine_tree"] != COMMON_TREE
        or plan["quality_exclusions"]
        or not causal.predecessor_ready({**control, "after_registration": str(CONTROL)})
    ):
        raise ValueError("unexpected engine pair, excluded work or incomplete control")
    fields = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "sampling_mode",
        "stream_response_metrics", "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(field) != control.get(field) for field in fields):
        raise ValueError("comparison work, capacity, stopping or serving contract differs")
    overlays = []
    for candidate in (plan, control):
        overlay = candidate["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing artifact changed")
        overlays.append({key: value for key, value in overlay.items() if key != "path"})
    if overlays[0] != overlays[1]:
        raise ValueError("tool timing provenance differs")
    manifest_path = HERE / "shared_cpu_r1.sources.json"
    if any(plan["source_sha256"].get(name) != digest
           for name, digest in causal.load(manifest_path).items()):
        raise ValueError("registered sources differ from CPU-qualified application")
    model_path, model_review_path = HERE / "model_r1/report.json", HERE / "model_review.json"
    model, model_review = causal.load(model_path), causal.load(model_review_path)
    if (
        model_review["model_qualified"] is not True
        or model_review["frozen_application"]["commit"] != plan["code_commit"]
        or model["status"] != "unforced_layer_handoff_model_qualified"
        or model["configuration_sha256"] != sha256_file(Path(plan["experiment_configuration"]))
        or causal.reservation.alive(model["server"])
        or any(sha256_file(Path(path)) != digest
               for path, digest in model_review["inputs_sha256"].items())
    ):
        raise ValueError("model proof, frozen application or actual model exit differs")
    position = causal.load(POSITION)
    position_state = causal.load(POSITION.with_suffix(".state.json"))
    smoke_path = Path(position["run_root"]) / "gpu_smoke.json"
    smoke = causal.load(smoke_path)
    if (
        position_state["status"] != "calibrated_comparison_registered"
        or any(causal.reservation.alive(position_state[name])
               for name in ("watcher", "server") if position_state.get(name))
        or smoke["requests"] != 24 or smoke["native_stop_requests"] != 24
        or any(smoke.get(name) is not True for name in (
            "native_position_sampling_verified", "native_data_plane_sample_verified",
            "gpu_process_verified",
        ))
    ):
        raise ValueError("position proof or actual predecessor exit is unverified")
    state = causal.load(FULL.with_suffix(".state.json"))
    if state["status"] != "running_full":
        raise ValueError("Full native submission has not begun")
    processes = {}
    for name in ("watcher", "server", "driver"):
        row = state[name]
        if not causal.reservation.alive(row) or os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0:
            raise ValueError("owned serving process is absent or not nice0")
        processes[name] = causal.reservation.process(row["pid"])
    gpu_pids = {
        int(line) for line in causal.qualified.command([
            "nvidia-smi", "--id=0", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owners = [row for row in state["owned"]
              if row["pid"] in gpu_pids and causal.reservation.alive(row)]
    if len(owners) != 1 or gpu_pids != {owners[0]["pid"]}:
        raise ValueError("sole GPU scheduler is not a current owned process")
    processes["gpu_scheduler"] = causal.reservation.process(owners[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    expected = {
        "physical_disabled": False, "profile_shared_path": False,
        "agent_join_blocked_handoff": True, "tool_offload_min_survival": .75,
        "host_demand_reserve_bytes": 2147483648, "layer_handoff_enabled": True,
        "layer_handoff_d2h_layers": 8, "layer_handoff_h2d_layers": 4,
        "tool_prefetch": False, "tool_prefetch_priority_ms": 0.,
        "tool_offload_before_admission": True, "execution_frontier_restore": False,
    }
    if any(runtime.get(name) != value for name, value in expected.items()):
        raise ValueError("actual Full configuration differs")
    validate_reference_admission(
        runtime["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True, join_blocked_handoff=True,
    )
    reader = SealedRecords()
    exact = validate_receipts(
        reader(arm / "replay/native_requests.jsonl"),
        causal.receipt_work(
            Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
            sampling_mode="position",
        ),
    )
    if exact == 0:
        raise ValueError("wait for first exact complete receipt")
    paths = (FULL, CONTROL, POSITION, smoke_path, manifest_path, model_path,
             model_review_path, Path(__file__))
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual current-source Full startup; not complete correctness or performance.",
        "registration": str(FULL), "code_commit": plan["code_commit"],
        "native_engine": SIBLING_CAPACITY_TREE, "control_native_engine": COMMON_TREE,
        "same_work_timing_capacity_fields": fields, "actual_nice0_processes": processes,
        "verified_runtime_settings": expected, "exact_completed_receipts": exact,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in paths},
        "performance_accepted": False,
        "limitations": [
            "An exact receipt prefix is not a complete performance comparison.",
            "Both implementation fixes changed together; native engines differ from V345.",
            "CPU publication speedup and model numerical equality do not establish JCT benefit.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        "code_commit": result["code_commit"], "exact_completed_receipts": exact,
        "actual_process_pids": {name: row["pid"] for name, row in processes.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
