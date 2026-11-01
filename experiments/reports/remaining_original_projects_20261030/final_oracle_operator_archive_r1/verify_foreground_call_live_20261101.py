"""Verify the actual sole-GPU CALL candidate and its unchanged Full settings."""

import argparse
import csv
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-foreground-call-a73fdcb7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
NAME = "workflow_original56_foreground_call"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.replay_token_capture import encode_tokens
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from audit_final82_live_tail_20261031 import prefix_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve live proof and use hidden-CUDA nice19")
    registration = OPS / (NAME + "_20261031_r1.registration.json")
    control_path = OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json"
    plan, control = causal.load(registration), causal.load(control_path)
    state = causal.load(registration.with_suffix(".state.json"))
    if plan["code_commit"] != "a73fdcb72f577c76d9fca016c5932d314fc93f9f" or state["status"] != "running_full":
        raise ValueError("require the actual frozen CALL-only Full candidate")
    for name in (
        "source_arm", "token_source_sha256", "source_capacity", "workflow_arrivals",
        "tool_timing_overlay", "max_running_requests", "engine_tree",
    ):
        if plan[name] != control[name]:
            raise ValueError("matched candidate traffic/resources changed: " + name)
    handles = {name: state[name] for name in ("watcher", "server", "driver")}
    if not all(causal.reservation.alive(row) and os.getpriority(os.PRIO_PROCESS, row["pid"]) == 0
               for row in handles.values()):
        raise ValueError("actual GPU owners must be alive and nice0")
    processes = list(csv.reader(io.StringIO(subprocess.check_output([
        "nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader,nounits",
    ], text=True))))
    if len(processes) != 1 or processes[0][1].strip() != "sglang::scheduler":
        raise ValueError("require one actual native GPU scheduler")
    gpu = causal.reservation.process(int(processes[0][0]))
    ancestry, parent = [], gpu
    while parent and parent["pid"] != handles["server"]["pid"] and len(ancestry) < 16:
        ancestry.append(parent)
        parent = causal.reservation.process(parent["ppid"])
    if not parent or parent["pid"] != handles["server"]["pid"] or os.getpriority(os.PRIO_PROCESS, gpu["pid"]) != 0:
        raise ValueError("the GPU scheduler is not the actual registered nice0 server child")
    observers = {
        name: causal.load(OPS / filename)["process"] for name, filename in (
            ("fixed_cohort", NAME + "_measured_completion_20261031_r1.registration.state.json"),
            ("coordinator", "native_then_foreground_call_20261101_r1.state.json"),
        )
    }
    if not all(causal.reservation.alive(row) for row in observers.values()) or (
        os.getpriority(os.PRIO_PROCESS, observers["fixed_cohort"]["pid"]) != 19
    ):
        raise ValueError("actual cohort observer and coordinator must be alive")
    arm = Path(plan["run_root"]) / "full"
    reference_arm = Path(control["run_root"]) / "full"
    capacity = causal.load(arm / "server/native_capacity_census.json")["capacity"]
    if capacity != causal.load(reference_arm / "server/native_capacity_census.json")["capacity"]:
        raise ValueError("actual KV pool capacities differ")
    reader = SealedRecords()
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    if not receipts or len({row["request_id"] for row in receipts}) != len(receipts):
        raise ValueError("require unique completed native receipts")
    needed = {row["request_id"] for row in receipts}
    source = {}
    for row in reader(Path(plan["source_arm"]) / "server/request_tokens.jsonl"):
        if row["request_id"] in needed:
            key = row["request_id"], row["event"]
            if key in source:
                raise ValueError("duplicate source token boundary")
            source[key] = row["token_count"], row["token_sha256"]
    for row in receipts:
        rid, native = row["request_id"], row["native_receipt"]
        if (
            row["exact_tokens_verified"] is not True or native["native_sampler_preserved"] is not True
            or native["sampling_path"] != "position" or native["request_id"] != rid
            or (row["prompt_tokens"], row["prompt_sha256"]) != source[rid, "request_prompt"]
            or (row["output_tokens"], row["output_sha256"]) != source[rid, "request_output"]
            or encode_tokens(native["output_token_ids"])["token_sha256"] != row["output_sha256"]
        ):
            raise ValueError("candidate receipt differs from original exact work")
    latest = None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "admission_runtime_state":
            latest = row
    reference = causal.qualified.last_runtime_state(reference_arm / "opportunities/admission_opportunities.jsonl")
    if latest is None or not 0 <= time.monotonic() * 1000. - latest["monotonic_ms"] < 30_000:
        raise ValueError("require a fresh actual runtime snapshot")
    fields = (
        "internal_comparison_mode", "tool_prefetch", "layer_handoff_enabled",
        "join_prefetch_trigger", "prepare_host", "predicted_tool_offload", "background_prepare",
        "agent_control_interval_ms", "agent_free_dispatch_interval_ms", "workflow_combination_admission",
    )
    agent_fields = (
        "workflow_oracle_remaining_work_ranking_enabled", "workflow_oracle_future_work_used_for_decisions",
        "residency_pause", "tool_event_replacement", "coadmit_join_children",
        "sibling_native_batch_preference", "acting_token_decay", "decay_half_life_s",
    )
    if any(latest[name] != reference[name] for name in fields) or any(
        latest["agent_admission"][name] != reference["agent_admission"][name] for name in agent_fields
    ):
        raise ValueError("actual candidate policy or native settings changed")
    if latest["agent_admission"]["workflow_oracle_remaining_work_ranking_enabled"] is not False:
        raise ValueError("workflow-size ranking must remain disabled")
    runtime_path = Path(plan["execution_checkout"]) / "beliefkv/runtime/sglang_v0520_runtime.py"
    if sha256_file(runtime_path) != plan["source_sha256"]["beliefkv/runtime/sglang_v0520_runtime.py"]:
        raise ValueError("the actually registered CALL runtime source changed")
    telemetry = causal.load(arm / "server/native_telemetry_status.json")
    if telemetry["writer_error"] is not None or telemetry.get("dropped_records", 0):
        raise ValueError("native telemetry is unhealthy")
    for filename, prefix in reader.prefixes.items():
        if prefix_digest(Path(filename), prefix["complete_prefix_bytes"]) != prefix["complete_prefix_sha256"]:
            raise ValueError("sealed live candidate observations changed")
    write(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(), "registration": str(registration),
        "actual_registered_handles": handles, "actual_GPU_scheduler": gpu,
        "verified_server_ancestry": ancestry, "actual_observers": observers,
        "exact_original_receipts_verified": len(receipts), "actual_capacity_equal": capacity,
        "actual_runtime_settings": {name: latest[name] for name in fields},
        "actual_agent_settings": {name: latest["agent_admission"][name] for name in agent_fields},
        "actual_CALL_runtime_source_sha256": sha256_file(runtime_path),
        "registered_inputs_sha256": {str(path): sha256_file(path) for path in (registration, control_path, Path(__file__))},
        "input_jsonl_prefixes": reader.prefixes, "GPU_measurement_unchanged": True,
        "complete_measured_JCT_available": False, "performance_accepted": False,
    })
    print({"exact_receipts": len(receipts), "actual_GPU_scheduler": gpu["pid"], "actual_policy_equal": True})


if __name__ == "__main__":
    main()
