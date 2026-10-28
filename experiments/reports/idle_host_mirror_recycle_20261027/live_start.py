"""Bind the actual Full startup to CPU/GPU evidence and the complete control."""

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
        raise FileExistsError("preserve the original mirror-recycle startup observation")
    ops = Path("/home/longhao/experiment/ops")
    registration = ops / "idle_host_mirror_recycle_20261027_r1.registration.json"
    control_path = ops / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
    previous_path = ops / "tool_offload_before_admission_20261027_r1.registration.json"
    plan, control, previous = map(causal.load, (registration, control_path, previous_path))
    causal.comparison.verify_sources(plan)
    causal.fresh.check_hold(plan, released=True)
    for path, prior in ((previous_path, previous), (control_path, control)):
        if not causal.predecessor_ready({**prior, "after_registration": str(path)}):
            raise ValueError("the previous Full or complete control has not actually exited")
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "engine_tree",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("Full changed common work, timing, capacity or serving")
    changed = {
        name: (previous["reference_environment"].get(name), value)
        for name, value in plan["reference_environment"].items()
        if previous["reference_environment"].get(name) != value
    }
    if changed != {
        "IDLE_HOST_MIRROR_RECYCLE": (None, "1"),
        "BELIEFKV_IDLE_HOST_MIRROR_RECYCLE": (None, "1"),
    } or previous["reference_environment"].keys() - plan["reference_environment"].keys():
        raise ValueError("the new Full changes more than the declared mirror switches")
    directory = output.parent
    cpu_path, gpu_path = directory / "shared_cpu.evidence.json", directory / "gpu_components_r3.json"
    cpu, gpu = causal.load(cpu_path), causal.load(gpu_path)
    manifest_path = directory / "shared_cpu.sources.json"
    sources = causal.load(manifest_path)
    if (
        gpu["status"] != "native_gpu_host_mirror_components_qualified"
        or cpu["source_manifest"]["sha256"] != sha256_file(manifest_path)
        or gpu["cpu_qualification_sha256"] != sha256_file(cpu_path)
        or gpu["cpu_source_manifest_sha256"] != sha256_file(manifest_path)
        or gpu["operator_sha256"] != sha256_file(directory / "gpu_components.py")
        or any(plan["source_sha256"].get(name) != digest for name, digest in sources.items())
        or any(sources.get(name) != digest for name, digest in gpu["source_sha256"].items())
    ):
        raise ValueError("the running policy is not bound to its CPU/GPU qualification")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("actual Full replay has not started")
    processes = {}
    for name in ("watcher", "server", "driver"):
        identity = state[name]
        if (
            not causal.reservation.alive(identity)
            or os.getpriority(os.PRIO_PROCESS, identity["pid"]) != 0
        ):
            raise ValueError("owned process identity or nice priority changed")
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
    waiter = causal.load(ops / "idle_host_mirror_recycle_20261027_r1.plots.launch.json")["process"]
    if (
        not causal.reservation.alive(waiter)
        or os.getpriority(os.PRIO_PROCESS, waiter["pid"]) != 19
    ):
        raise ValueError("the CPU plotting waiter is not the actual nice19 process")
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if (
        latest.get("physical_disabled") is not False
        or latest.get("idle_host_mirror_recycle") is not True
        or latest.get("tool_offload_before_admission") is not True
        or latest.get("predicted_tool_offload") is not True
        or latest.get("tool_offload_policy") != "DemandAwareToolOffload"
    ):
        raise ValueError("actual migration configuration or physical control is unhealthy")
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True,
    )
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    if len(expected) != plan["source_plan"]["requests"]:
        raise ValueError("complete control has different request work")
    reader = SealedRecords()
    completed = validate_receipts(reader(arm / "replay/native_requests.jsonl"), expected)
    mirrors, backups = [], []
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "idle_host_mirror_recycled":
            mirrors.append(row)
        elif row.get("event") == "idle_cold_full_prepare":
            backups.append(row)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual startup and sealed collecting prefixes, not complete performance",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_v345": list(common),
        "environment_changes_from_previous_full": changed,
        "actual_nice0_serving_processes": processes,
        "actual_nice19_cpu_plot_waiter": causal.reservation.process(waiter["pid"]),
        "cpu_evidence_sha256": sha256_file(cpu_path),
        "gpu_components_sha256": sha256_file(gpu_path),
        "exact_completed_receipts": completed,
        "mirror_release_records_in_sealed_prefix": len(mirrors),
        "mirror_released_host_bytes_in_sealed_prefix": sum(
            row["released_host_bytes"] for row in mirrors
        ),
        "idle_backup_records_in_sealed_prefix": len(backups),
        "first_mirror_release": mirrors[0] if mirrors else None,
        "input_jsonl_prefixes": reader.prefixes,
        "latest_runtime_snapshot": latest,
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_work_verified": False, "performance_accepted": False,
        "progressive_layer_handoff_implemented": False,
        "limitations": [
            "Mirror releases and backup submissions alone do not prove completed copies or HBM release.",
            "Cumulative bytes are not simultaneous occupancy, saved time or causal JCT gains.",
            "Independent JSONL prefixes are not an atomic cross-file snapshot.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "code_commit", "engine_tree", "exact_completed_receipts",
        "mirror_release_records_in_sealed_prefix",
        "mirror_released_host_bytes_in_sealed_prefix", "performance_accepted",
    )}, indent=2))


if __name__ == "__main__":
    main()
