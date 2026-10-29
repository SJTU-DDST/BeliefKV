"""Observe the repaired Full's actual configuration, process and receipt prefix."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "demand_prefill_ack_retry_20261028_r1.registration.json"
POSITION = OPS / "demand_prefill_ack_retry_position_20261028_r1.registration.json"
PRIOR = OPS / "demand_prefill_pipeline_20261028_r1.registration.json"


def main():
    output = HERE / "readmission_live_start.json"
    if output.exists():
        raise FileExistsError("preserve earlier repaired Full startup evidence")
    plan = json.loads(REGISTRATION.read_bytes())
    sys.path.insert(0, plan["execution_checkout"])
    from scripts import run_causal_comparison as causal
    from scripts.audit_collecting_agent_pipeline import validate_receipts
    from scripts.audit_sealed_wait_prefetch import SealedRecords

    entry = causal.load(HERE / "readmission_position_entry.json")
    for source, digest in entry["inputs_sha256"].items():
        if causal.sha256_file(Path(source)) != digest:
            raise ValueError("repaired-source position entry changed")
    causal.comparison.verify_sources(plan)
    causal.fresh.check_hold(plan, released=True)
    if (
        plan["code_commit"] != entry["application"]["commit"]
        or plan["engine_tree"] != entry["native_registration_evidence"]["tree"]
        or plan["order"] != ["full"] or plan["quality_exclusions"]
        or plan["passing_cpu_cases"] != 4102
    ):
        raise ValueError("actual Full source, native tree, population or CPU proof differs")
    position = causal.load(POSITION.with_suffix(".state.json"))
    if (
        position["status"] != "calibrated_comparison_registered"
        or causal.reservation.alive(position["watcher"])
        or position["comparison_registration"] != str(REGISTRATION)
    ):
        raise ValueError("position supervisor has not actually finished and exited")
    position_plan = causal.load(POSITION)
    position_root = Path(position_plan["run_root"])
    smoke_path = position_root / "gpu_smoke.json"
    calibration_path = position_root / "sampling_calibration.json"
    smoke, calibration = causal.load(smoke_path), causal.load(calibration_path)
    smoke_work = causal.receipt_work(
        position_root / "smoke/native_requests.jsonl", sampling_mode="position",
    )
    cleanup = causal.load(position_root / "owned_process_cleanup.json")
    if (
        position_plan["code_commit"] != plan["code_commit"]
        or position_plan["engine_tree"] != plan["engine_tree"]
        or plan["sampling_calibration"] != str(calibration_path)
        or len(smoke_work) != 24 or smoke["requests"] != 24
        or smoke["native_stop_requests"] != 24
        or sum(row[4]["type"] == "stop" for row in smoke_work.values()) != 24
        or smoke["native_position_sampling_verified"] is not True
        or calibration["position_dispatch_included"] is not True
        or [row["batch_size"] for row in calibration["cases"]] != [1, 8, 24]
        or cleanup["remaining_processes"]
        or any(causal.reservation.alive(row) for row in cleanup["identities"])
    ):
        raise ValueError("fresh same-source position tokens, native stops, calibration or exit differs")
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if state["status"] != "running_full":
        raise ValueError("wait for actual repaired Full requests")
    processes = {}
    for name in ("watcher", "server", "driver"):
        row = state[name]
        if not causal.reservation.alive(row) or os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0:
            raise ValueError("actual repaired Full process is absent or is not nice0")
        processes[name] = causal.reservation.process(row["pid"])
    gpu_pids = {
        int(line) for line in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if line.strip().isdigit()
    }
    owners = [
        row for row in state["owned"] if row["pid"] in gpu_pids and causal.reservation.alive(row)
    ]
    if (
        len(owners) != 1 or gpu_pids != {owners[0]["pid"]}
        or os.getpriority(os.PRIO_PROCESS, owners[0]["pid"]) != 0
    ):
        raise ValueError("repaired Full scheduler is not the sole owned nice0 GPU process")
    processes["gpu_scheduler"] = causal.reservation.process(owners[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    expected = {
        "physical_disabled": False, "profile_shared_path": False,
        "demand_prefill_pipeline": True, "demand_prefill_pipeline_installed": True,
        "demand_prefill_pipeline_progress_probe": False, "tool_prefetch": False,
        "tool_prefetch_priority_ms": 0., "execution_frontier_restore": False,
        "layer_handoff_enabled": True,
        "layer_handoff_d2h_layers": 8, "layer_handoff_h2d_layers": 4,
    }
    if any(type(runtime.get(name)) is not type(value) or runtime[name] != value
           for name, value in expected.items()):
        raise ValueError("actual helper, probe, profile or migration settings differ")
    info = causal.load(arm / "server/server_info.json")
    math = {
        "enable_deterministic_inference": False, "attention_backend": "fa3",
        "sampling_backend": "flashinfer", "enable_custom_logit_processor": True,
    }
    if any(info.get(name) != value for name, value in math.items()):
        raise ValueError("formal repair run changed numerical or native stopping mode")
    previous = causal.load(PRIOR)
    reader = SealedRecords()
    count = validate_receipts(
        reader(arm / "replay/native_requests.jsonl"),
        causal.receipt_work(
            Path(previous["run_root"]) / "full/replay/native_requests.jsonl",
            sampling_mode="position",
        ),
    )
    if count == 0:
        raise ValueError("wait for a complete repaired Full receipt")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual repaired Full startup and exact completed prefix, not complete performance.",
        "registration": str(REGISTRATION), "code_commit": plan["code_commit"],
        "engine_tree": plan["engine_tree"], "main_admission_retry_fix_included": True,
        "actual_nice0_processes": processes, "actual_pipeline_settings": expected,
        "actual_formal_math": math, "exact_completed_receipts": count,
        "recorded_ack_native_capacity_retries": runtime["counts"].get(
            "demand_restore_native_capacity_retries", 0,
        ),
        "position_supervisor_actual_exit_verified": True,
        "fresh_position_exact_requests_and_native_stops": 24,
        "position_dispatch_increment_mean_wall_ms": {
            str(row["batch_size"]): row["native_dispatch_increment_mean_wall_ms"]
            for row in calibration["cases"]
        },
        "input_jsonl_prefixes": reader.prefixes,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "inputs_sha256": {
            str(path): causal.sha256_file(path) for path in (
                Path(__file__), REGISTRATION, POSITION, PRIOR,
                HERE / "readmission_position_entry.json", HERE / "readmission_gpu_review.json",
                smoke_path, calibration_path, position_root / "owned_process_cleanup.json",
                position_root / "smoke/native_requests.jsonl",
            )
        },
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print({
        "exact_completed_receipts": count, "code_commit": plan["code_commit"],
        "recorded_ack_native_capacity_retries": result["recorded_ack_native_capacity_retries"],
        "actual_process_pids": {name: row["pid"] for name, row in processes.items()},
    })


if __name__ == "__main__":
    main()
