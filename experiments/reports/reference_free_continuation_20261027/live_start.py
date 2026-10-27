"""Verify the frozen free-continuation Full's actual startup and shared work."""

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
        raise FileExistsError("preserve original continuation startup evidence")
    registration = Path("/home/longhao/experiment/ops/reference_free_continuation_20261027_r1.registration.json")
    control_path = Path("/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json")
    predecessor_path = Path("/home/longhao/experiment/ops/reference_tool_eos_guard_20261027_r1.registration.json")
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    for path in (predecessor_path, control_path):
        previous = causal.load(path)
        if not causal.predecessor_ready({**previous, "after_registration": str(path)}):
            raise ValueError("previous comparison or matched control has not actually exited")
    causal.fresh.check_hold(plan, released=True)
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "engine_tree",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("continuation Full changed common work, timing or serving parameters")
    cpu_path = output.parent / "shared_cpu.evidence.json"
    cpu = causal.load(cpu_path)
    sources = causal.load(Path(cpu["source_manifest"]["path"]))
    for name in (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/reference_free_continuation.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/runtime/paper_baseline.py", "beliefkv/policy/paper_baselines.py",
    ):
        if plan["source_sha256"][name] != sources[name]:
            raise ValueError("running admission differs from its qualified sources")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("continuation Full has not started its actual replay")
    processes = {}
    for name in ("watcher", "server", "driver"):
        item = state[name]
        if not causal.reservation.alive(item) or os.getpriority(os.PRIO_PROCESS, item["pid"]) != 0:
            raise ValueError("owned serving process identity or nice priority changed")
        processes[name] = causal.reservation.process(item["pid"])
    gpu_pids = {
        int(line.strip()) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    schedulers = [
        item for item in state["owned"]
        if item["pid"] in gpu_pids and causal.reservation.alive(item)
    ]
    if len(schedulers) != 1 or os.getpriority(os.PRIO_PROCESS, schedulers[0]["pid"]) != 0:
        raise ValueError("actual GPU scheduler is not the owned nice0 process")
    processes["gpu_scheduler"] = causal.reservation.process(schedulers[0]["pid"])
    waiter = causal.reservation.process(320361)
    if (
        waiter is None or waiter["start_ticks"] != "731406698"
        or not causal.reservation.alive(waiter)
        or os.getpriority(os.PRIO_PROCESS, waiter["pid"]) != 19
        or str(registration) not in waiter["argv"]
        or waiter["argv"][-1] != "/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python"
    ):
        raise ValueError("CPU plot waiter identity or dedicated interpreter changed")
    arm = Path(plan["run_root"]) / "full"
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if (
        latest.get("physical_disabled") is not False
        or latest.get("agent_free_continuation_admission") is not True
    ):
        raise ValueError("actual physical control or new admission flag is not healthy")
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True,
    )
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    if len(expected) != plan["source_plan"]["requests"]:
        raise ValueError("control completed a different request population")
    receipt_path = arm / "replay/native_requests.jsonl"
    reader = SealedRecords()
    completed = validate_receipts(reader(receipt_path), expected)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual startup and sealed completed prefix, not complete performance",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_v345": list(common),
        "previous_complete_actual_exit": True,
        "actual_nice0_serving_processes": processes, "actual_nice19_cpu_plot_waiter": waiter,
        "cpu_evidence_sha256": sha256_file(cpu_path),
        "exact_completed_receipts_in_prefix": completed,
        "native_requests_prefix_present": receipt_path.is_file(),
        "input_jsonl_prefixes": reader.prefixes,
        "latest_runtime_snapshot": latest,
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_work_verified": False, "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        key: result[key] for key in (
            "code_commit", "engine_tree", "exact_completed_receipts_in_prefix",
            "complete_work_verified", "performance_accepted",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
