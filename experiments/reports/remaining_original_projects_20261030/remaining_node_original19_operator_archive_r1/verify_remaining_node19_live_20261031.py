"""Seal a bounded live-source proof without treating prefixes as complete work."""

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-original19-source-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "remaining_node_original19_gpu_entry_r1"
REGISTRATION = OPS / "workflow_remaining_node19_source_20261031_r1.registration.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def main():
    output = HERE / "live_start_r1.json"
    if output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve live proofs and use nice19 CPU-only observation")
    plan, state = causal.load(REGISTRATION), causal.load(REGISTRATION.with_suffix(".state.json"))
    if state.get("status") != "recording_native":
        raise ValueError("the source is not actually recording native")
    capture.comparison.verify_sources(plan)
    owners = {}
    for name in ("watcher", "driver"):
        identity = state[name]
        if not capture.reservation.alive(identity) or os.getpriority(os.PRIO_PROCESS, identity["pid"]) != 0:
            raise ValueError("registered source owner is absent or not nice0")
        owners[name] = capture.reservation.process(identity["pid"])
    arm = Path(plan["run_root"]) / "native_capture"
    capacity_path = arm / "server/native_capacity_census.json"
    capacity = causal.load(capacity_path)
    scheduler = capture.reservation.process(capacity["scheduler_pid"])
    server = capture.reservation.process(scheduler["ppid"])
    if any(item is None or os.getpriority(os.PRIO_PROCESS, item["pid"]) != 0 for item in (scheduler, server)):
        raise ValueError("native GPU scheduler/server is absent or not nice0")
    if server["ppid"] != owners["driver"]["pid"]:
        raise ValueError("native GPU owner is not a child of the registered source driver")
    gpu_pids = [int(line.strip()) for line in subprocess.check_output([
        "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
    ], text=True).splitlines() if line.strip()]
    if gpu_pids != [scheduler["pid"]]:
        raise ValueError("GPU ownership differs from the exact source scheduler")
    owners.update(server=server, scheduler=scheduler)
    manifest_path = arm / "client_19/manifest.json"
    manifest = causal.load(manifest_path)
    config = manifest["config"]
    required = {
        "max_workflows": 19, "concurrency": 12, "sampling_seed": 21,
        "child_work_budget_warning_calls": 4, "child_work_budget_target_calls": 8,
        "root_work_budget_warning_calls": 8, "root_work_budget_target_calls": 16,
        "subagent_fanout_profile": "native_in_graph_parallel_workstreams",
        "native_policy_baseline": True, "native_radix_sessions": True,
        "sampling_profile": "qwen3_coder",
    }
    if any(config.get(name) != value for name, value in required.items()):
        raise ValueError("actual model client differs from the qualified source")
    workload_path = Path(config["workload_manifest"])
    workload = capture.project_disjoint_workload(workload_path, expected_workflows=19)
    if manifest["workload_manifest_sha256"] != sha256_file(workload_path):
        raise ValueError("actual source client uses another workload")
    launch_path = arm / "server/launch_configuration.json"
    launch = causal.load(launch_path)
    if launch["internal_comparison_mode"] != "native_matched" or launch["server_random_seed"] != 21:
        raise ValueError("native server comparison mode or seed differs")
    actual_capacity = capacity["capacity"]
    if any(actual_capacity[name] != value for name, value in {
        "device_full_tokens": 805198, "host_full_tokens": 2034506,
        "device_mamba_slots": 0, "host_mamba_slots": 0,
    }.items()):
        raise ValueError("actual native pool geometry differs")
    sampling_path = Path(plan["sampling_preflight_path"])
    sampling = causal.load(sampling_path)
    if sampling["gpu_sampling_verified"] is not True or sampling["native_kernel_verified"] is not True:
        raise ValueError("fresh real sampling prerequisite did not pass")
    status_path = arm / "server/native_telemetry_status.json"
    status = causal.load(status_path)
    if status.get("replay_token_capture") is not True or any(
        status.get(name) for name in ("writer_error", "failed_records", "dropped_records")
    ):
        raise ValueError("exact-token telemetry is unhealthy")
    reader, counts = SealedRecords(), Counter()
    for row in reader(arm / "server/request_tokens.jsonl"):
        counts[row["event"]] += 1
    if counts["request_output"] <= 0:
        raise ValueError("no actual model completion has been captured")
    observer_path = REPORTS / "remaining_node_original19_finish_r1/state.json"
    observer = causal.load(observer_path)
    identity = observer["observer"]
    if observer["status"] != "waiting_complete_native_source" or not capture.reservation.alive(identity) or (
        os.getpriority(os.PRIO_PROCESS, identity["pid"]) != 19
    ):
        raise ValueError("the CPU source reviewer is not actually waiting")
    write(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "capture_registration": str(REGISTRATION), "source_status": state["status"],
        "original_projects": len(workload["workloads"]), "actual_client_config": required,
        "actual_capacity": actual_capacity, "live_nice0_owned_processes": owners,
        "sole_GPU_owner": scheduler["pid"], "fresh_sampling_verified": True,
        "captured_token_record_prefix_counts": dict(counts),
        "healthy_native_writer": True, "CPU_reviewer": identity,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            REGISTRATION, workload_path, manifest_path, capacity_path, launch_path,
            sampling_path, Path(__file__),
        )},
        "complete_work_verified": False, "formal_workload_accepted": False,
        "performance_accepted": False, "GPU_action": False,
    })
    print(json.dumps({
        "native_source_live": True, "projects": 19, "source_window": 12,
        "sole_GPU_owner": scheduler["pid"], "captured_outputs": counts["request_output"],
        "CPU_reviewer_alive": True, "performance_accepted": False,
    }), flush=True)


if __name__ == "__main__":
    main()
