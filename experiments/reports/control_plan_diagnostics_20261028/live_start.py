"""Verify actual profiled Full work, owned processes and unchanged control work."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
FULL = OPS / "control_plan_diagnostics_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
POSITION = OPS / "control_plan_diagnostics_position_20261028_r1.registration.json"


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve original startup observation")
    plan = json.loads(FULL.read_bytes())
    sys.path.insert(0, plan["execution_checkout"])
    from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
    from beliefkv.experiments.source_provenance import sha256_file
    from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
    from scripts.audit_collecting_agent_pipeline import validate_receipts
    from scripts.audit_sealed_wait_prefetch import SealedRecords
    from scripts import run_causal_comparison as causal

    control = causal.load(CONTROL)
    for current in (plan, control):
        causal.comparison.verify_sources(current)
    causal.fresh.check_hold(plan, released=True)
    if (
        plan["order"] != ["full"] or control["order"] != ["thunderagent_acting_decay"]
        or plan["engine_tree"] != SIBLING_CAPACITY_TREE or control["engine_tree"] != COMMON_TREE
        or plan["quality_exclusions"]
        or not causal.predecessor_ready({**control, "after_registration": str(CONTROL)})
    ):
        raise ValueError("unexpected engine pair, controls, exclusions or incomplete predecessor")
    fields = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "sampling_mode",
        "stream_response_metrics", "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(field) != control.get(field) for field in fields):
        raise ValueError("complete work, capacity or serving contract changed")
    overlays = []
    for current in (plan, control):
        overlay = current["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing artifact changed")
        overlays.append({key: value for key, value in overlay.items() if key != "path"})
    if overlays[0] != overlays[1]:
        raise ValueError("tool timing provenance changed")
    manifest_path = HERE / "shared_cpu_r2.sources.json"
    if any(plan["source_sha256"].get(name) != digest
           for name, digest in causal.load(manifest_path).items()):
        raise ValueError("registered application differs from qualified sources")
    model_path = HERE / "model_r1/report.json"
    model_review_path = HERE / "model_review.json"
    model, model_review = causal.load(model_path), causal.load(model_review_path)
    if any(sha256_file(Path(path)) != digest
           for path, digest in model_review["inputs_sha256"].items()):
        raise ValueError("unforced model review input changed")
    if (
        model["status"] != "unforced_layer_handoff_model_qualified"
        or model["configuration_sha256"] != sha256_file(Path(plan["experiment_configuration"]))
        or model_review["frozen_application"]["commit"] != plan["code_commit"]
        or model_review["profile_observed_on_gpu_runtime"] is not True
        or causal.reservation.alive(model["server"])
    ):
        raise ValueError("current model/profile proof or actual model exit is missing")
    position_state = causal.load(POSITION.with_suffix(".state.json"))
    if position_state["status"] != "calibrated_comparison_registered" or any(
        causal.reservation.alive(position_state[name])
        for name in ("watcher", "server") if position_state.get(name)
    ):
        raise ValueError("position predecessor has not actually completed and exited")
    position = causal.load(POSITION)
    smoke_path = Path(position["run_root"]) / "gpu_smoke.json"
    smoke = causal.load(smoke_path)
    if (
        any(smoke.get(name) is not True for name in (
            "native_position_sampling_verified", "native_data_plane_sample_verified",
            "gpu_process_verified",
        ))
        or smoke["requests"] != 24 or smoke["native_stop_requests"] != 24
    ):
        raise ValueError("current complete token/session/stop GPU check is absent")
    state = causal.load(FULL.with_suffix(".state.json"))
    if state["status"] != "running_full":
        raise ValueError("actual complete Full submission has not begun")
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
    owners = [
        row for row in state["owned"] if row["pid"] in gpu_pids and causal.reservation.alive(row)
    ]
    if len(owners) != 1 or gpu_pids != {owners[0]["pid"]}:
        raise ValueError("GPU scheduler is not the sole owned compute process")
    processes["gpu_scheduler"] = causal.reservation.process(owners[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    expected = {
        "physical_disabled": False, "profile_shared_path": True,
        "agent_join_blocked_handoff": True, "tool_offload_min_survival": .75,
        "host_demand_reserve_bytes": 2147483648, "layer_handoff_enabled": True,
        "layer_handoff_d2h_layers": 8, "layer_handoff_h2d_layers": 4,
        "tool_prefetch": False, "tool_prefetch_priority_ms": 0.,
        "tool_offload_before_admission": True, "execution_frontier_restore": False,
    }
    timing = runtime["shared_path_timing"]
    if (
        any(runtime.get(name) != value for name, value in expected.items())
        or timing["schema_version"] != 2 or timing["cpu_clock"] != "thread_time_ns"
        or timing["sample_every"] != 64
        or not timing["phases"]
        or any("thread_cpu_self_ms" not in phase for phase in timing["phases"].values())
    ):
        raise ValueError("actual diagnostic profile or Full policy differs")
    validate_reference_admission(
        runtime["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True, join_blocked_handoff=True,
    )
    reader = SealedRecords()
    work = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    exact = validate_receipts(reader(arm / "replay/native_requests.jsonl"), work)
    if exact == 0:
        raise ValueError("wait for the first exact completed native receipt")
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual profiled Full startup, not complete correctness or performance.",
        "registration": str(FULL), "code_commit": plan["code_commit"],
        "native_engine": SIBLING_CAPACITY_TREE, "control_native_engine": COMMON_TREE,
        "same_work_timing_capacity_fields": list(fields), "actual_nice0_processes": processes,
        "verified_runtime_settings": expected, "profile_snapshot": timing,
        "exact_completed_receipts": exact, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            FULL, CONTROL, POSITION, smoke_path, model_path, model_review_path,
            manifest_path, Path(__file__),
        )},
        "performance_accepted": False,
        "limitations": [
            "The complete V345 control has a different reviewed engine and no new profiling.",
            "Exact receipt prefixes and startup do not establish complete JCT or throughput.",
            "Measured CPU samples are not total control cost, exposed GPU idle or saved JCT.",
        ],
    })
    print(json.dumps({
        "code_commit": plan["code_commit"], "exact_completed_receipts": exact,
        "actual_process_pids": {name: row["pid"] for name, row in processes.items()},
        "profile_shared_path": True, "sample_every": 64,
    }, indent=2))


if __name__ == "__main__":
    main()
