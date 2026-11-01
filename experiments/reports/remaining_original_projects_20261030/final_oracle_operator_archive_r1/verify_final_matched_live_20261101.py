"""Verify actual final Oracle/native owners, capacity and exact live receipts."""

import argparse
import csv
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-final-followup-a013dbd7")
OPS = Path("/home/longhao/experiment/ops")
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.replay_token_capture import encode_tokens
from scripts import run_causal_comparison as causal
from scripts import stop_after_measured_cohort as stop
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from audit_final82_live_tail_20261031 import prefix_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve observations and use nice19 hidden-CUDA verification")
    inputs = [
        args.registration, args.reference, Path(__file__),
        OPS / "audit_final82_live_tail_20261031.py",
    ]
    if args.oracle:
        inputs.append(args.oracle)
    pins = {str(path.resolve()): sha256_file(path) for path in inputs}
    plan, reference = causal.load(args.registration), causal.load(args.reference)
    if plan["code_commit"] != "a013dbd72988f1e2d6a9213b5cef660e681b69e6":
        raise ValueError("verify the frozen final experiment operator explicitly")
    for name in (
        "source_arm", "token_source_sha256", "source_capacity",
        "tool_timing_overlay", "workflow_arrivals", "max_running_requests",
    ):
        if plan[name] != reference[name]:
            raise ValueError("matched workload or resources changed: " + name)
    arm = stop.replay_arm(plan)
    state = causal.load(args.registration.with_suffix(".state.json"))
    expected_status = "running_" + arm.name
    if state["status"] != expected_status:
        raise ValueError("the declared formal replay is not running")
    handles = {name: state[name] for name in ("watcher", "server", "driver")}
    if not all(causal.reservation.alive(row) for row in handles.values()) or any(
        os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0 for row in handles.values()
    ):
        raise ValueError("actual GPU experiment owners must be alive and nice0")
    reference_arm = stop.replay_arm(reference)
    capacity = causal.load(arm / "server/native_capacity_census.json")["capacity"]
    if capacity != causal.load(reference_arm / "server/native_capacity_census.json")["capacity"]:
        raise ValueError("actual native resource pools differ from the control")
    processes = list(csv.reader(io.StringIO(subprocess.check_output([
        "nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader,nounits",
    ], text=True))))
    if len(processes) != 1 or processes[0][1].strip() != "sglang::scheduler":
        raise ValueError("require one actual native GPU scheduler")
    scheduler = causal.reservation.process(int(processes[0][0]))
    if not causal.reservation.alive(scheduler) or (
        os.getpriority(os.PRIO_PROCESS, scheduler["pid"]) != 0
    ):
        raise ValueError("actual GPU scheduler must be alive and nice0")
    ancestry, parent = [], scheduler
    while parent and parent["pid"] != handles["server"]["pid"] and len(ancestry) < 16:
        ancestry.append(parent)
        parent = causal.reservation.process(parent["ppid"])
    if not parent or parent["pid"] != handles["server"]["pid"]:
        raise ValueError("the GPU scheduler does not belong to the registered server")
    observer, coordinator = (
        causal.load(path) for path in (
            args.observer, OPS / "final_oracle_native_20261031_r1.state.json",
        )
    )
    observers = {
        "fixed_cohort": observer["process"], "sequential_coordinator": coordinator["process"],
    }
    if not all(causal.reservation.alive(row) for row in observers.values()) or (
        os.getpriority(os.PRIO_PROCESS, observers["fixed_cohort"]["pid"]) != 19
    ):
        raise ValueError("the actual fixed-cohort observer and coordinator must be alive")
    reader = SealedRecords()
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    if not receipts or len({row["request_id"] for row in receipts}) != len(receipts):
        raise ValueError("require unique completed native receipts")
    needed = {row["request_id"] for row in receipts}
    recorded = {}
    for row in reader(Path(plan["source_arm"]) / "server/request_tokens.jsonl"):
        if row.get("request_id") in needed:
            key = row["request_id"], row["event"]
            if key in recorded:
                raise ValueError("duplicate source token boundary")
            recorded[key] = row["token_count"], row["token_sha256"]
    for row in receipts:
        identity, native = row["request_id"], row["native_receipt"]
        if (
            row["exact_tokens_verified"] is not True
            or native["native_sampler_preserved"] is not True
            or native["sampling_path"] != "position"
            or (row["prompt_tokens"], row["prompt_sha256"]) != recorded[identity, "request_prompt"]
            or (row["output_tokens"], row["output_sha256"]) != recorded[identity, "request_output"]
            or native["request_id"] != identity
            or native["prompt_sha256"] != row["prompt_sha256"]
            or len(native["output_token_ids"]) != row["output_tokens"]
            or encode_tokens(native["output_token_ids"])["token_sha256"] != row["output_sha256"]
        ):
            raise ValueError("live receipt differs from original exact tokens")
    runtime_proof = {}
    if args.oracle:
        profile = causal.load(args.oracle)["admission_cost_profile"]
        if profile["kind"] != "r5_frozen_batch_cost_proxy_capacity_v6":
            raise ValueError("the final Oracle must use the declared v6 cost profile")
        journal = arm / "opportunities/admission_opportunities.jsonl"
        latest, selections = None, []
        for row in reader(journal):
            if row.get("event") == "admission_runtime_state":
                latest = row
            elif row.get("event") == "workflow_combination_selection":
                selections.append(row)
        if latest is None or not 0 <= time.monotonic() * 1000. - latest["monotonic_ms"] < 30_000:
            raise ValueError("a fresh actual runtime snapshot is required")
        expected_agent = {
            "workflow_combination_admission": True,
            "workflow_combination_cost_identity": profile["identity"],
            "workflow_oracle_coalescing_window_ms": 2000,
            "tool_event_replacement": True,
            "pressure_pause_policy": "workflow_tool_aware",
            "residency_pause": True,
            "future_information_used": True,
            "deployable": False,
        }
        if any(latest["agent_admission"].get(name) != value for name, value in expected_agent.items()):
            raise ValueError("actual Oracle admission settings differ")
        expected_runtime = {
            "internal_comparison_mode": "full", "workflow_combination_admission": True,
            "agent_control_interval_ms": 2000, "agent_free_dispatch_interval_ms": 500,
            "tool_prefetch": False, "layer_handoff_enabled": False,
            "join_prefetch_trigger": "observed_eos",
        }
        if any(latest.get(name) != value for name, value in expected_runtime.items()):
            raise ValueError("actual Oracle runtime settings differ")
        if not selections or any(row["cost_identity"] != profile["identity"] for row in selections):
            raise ValueError("actual Oracle selections did not use the frozen cost profile")
        runtime_proof = {
            "agent_settings": expected_agent, "runtime_settings": expected_runtime,
            "selection_entries": len(selections),
            "device_fit_scopes": sorted({str(row["device_fit_scope"]) for row in selections}),
            "profile_kind": profile["kind"],
        }
    else:
        configuration = Path(plan["experiment_configuration"])
        inputs.append(configuration)
        pins[str(configuration)] = sha256_file(configuration)
        if not stop.native_baseline_declared(plan) or any(
            value in handles["server"]["argv"] for value in (
                "--enable-beliefkv-admission", "--beliefkv-event-socket-path",
            )
        ):
            raise ValueError("native requires the explicit frozen baseline contract")
    telemetry = causal.load(arm / "server/native_telemetry_status.json")
    if telemetry["writer_error"] is not None or telemetry.get("dropped_records", 0):
        raise ValueError("native telemetry is unhealthy")
    for filename, prefix in reader.prefixes.items():
        if prefix_digest(Path(filename), prefix["complete_prefix_bytes"]) != prefix["complete_prefix_sha256"]:
            raise ValueError("independently sealed live evidence changed")
    if any(sha256_file(Path(filename)) != digest for filename, digest in pins.items()):
        raise ValueError("live verifier inputs changed")
    write(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(args.registration), "status": state["status"],
        "application_commit": plan["code_commit"], "actual_registered_handles": handles,
        "actual_GPU_scheduler": scheduler, "verified_server_ancestry": ancestry,
        "actual_observers": observers,
        "actual_handles_alive_at_exit": {
            name: causal.reservation.alive(row) for name, row in {
                **handles, **observers, "GPU_scheduler": scheduler,
            }.items()
        },
        "native_prefix_receipts_verified": len(receipts),
        "actual_native_capacity_equal_to_control": capacity,
        "runtime_verification": runtime_proof,
        "input_jsonl_prefixes": reader.prefixes, "inputs_sha256": pins,
        "GPU_measurement_unchanged": True, "complete_measured_JCT_available": False,
        "performance_accepted": False,
    })
    print({
        "status": state["status"], "verified_exact_receipts": len(receipts),
        "actual_GPU_scheduler": scheduler["pid"], "runtime": runtime_proof,
    }, flush=True)


if __name__ == "__main__":
    main()
