"""Verify frozen EOS-guard startup, actual process ownership and shared work."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve EOS-guard startup evidence")
    registration = Path("/home/longhao/experiment/ops/reference_tool_eos_guard_20261027_r1.registration.json")
    control_path = Path("/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json")
    predecessor_path = Path("/home/longhao/experiment/ops/reference_join_siblings_20261027_r1.registration.json")
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    for path in (predecessor_path, control_path):
        previous = causal.load(path)
        if not causal.predecessor_ready({**previous, "after_registration": str(path)}):
            raise ValueError("predecessor or matched control is not complete and exited")
    causal.fresh.check_hold(plan, released=True)
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "engine_tree",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("guard candidate changed common work or serving parameters")
    cpu_path = ROOT / "experiments/reports/reference_join_siblings_20261027/tool_closer_shared_cpu.evidence.json"
    cpu = causal.load(cpu_path)
    sources = causal.load(Path(cpu["source_manifest"]["path"]))
    runtime_name = "beliefkv/runtime/sglang_v0520_runtime.py"
    if plan["source_sha256"][runtime_name] != sources[runtime_name]:
        raise ValueError("running EOS guard differs from its qualified sources")
    model = Path(plan["source_server_parameters"]["model_path"])
    for name, digest in cpu["context_tokenizer"]["files_sha256"].items():
        if sha256_file(model / name) != digest:
            raise ValueError("actual serving tokenizer differs from real-tokenizer CPU tests")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("guard candidate is not running its actual replay")
    processes = {}
    for name in ("watcher", "server", "driver"):
        item = state[name]
        if not causal.reservation.alive(item) or os.getpriority(os.PRIO_PROCESS, item["pid"]) != 0:
            raise ValueError("owned serving process identity or priority changed")
        processes[name] = causal.reservation.process(item["pid"])
    gpu_pids = {
        int(line.strip()) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owned_gpu = [
        item for item in state["owned"]
        if item["pid"] in gpu_pids and causal.reservation.alive(item)
    ]
    if len(owned_gpu) != 1 or os.getpriority(os.PRIO_PROCESS, owned_gpu[0]["pid"]) != 0:
        raise ValueError("actual GPU process is not the owned nice0 scheduler")
    processes["gpu_scheduler"] = causal.reservation.process(owned_gpu[0]["pid"])
    waiter = causal.reservation.process(281239)
    if (
        waiter is None or waiter["start_ticks"] != "730784077"
        or not causal.reservation.alive(waiter)
        or os.getpriority(os.PRIO_PROCESS, waiter["pid"]) != 19
        or str(registration) not in waiter["argv"]
        or waiter["argv"][-1] != "/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python"
    ):
        raise ValueError("CPU plotting waiter or its dedicated interpreter changed")
    arm = Path(plan["run_root"]) / "full"
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    if latest.get("physical_disabled") is not False:
        raise ValueError("native physical control is unhealthy")
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual startup and source identity, not completed work or performance",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_v345": list(common),
        "predecessor_complete_actual_exit": True,
        "actual_nice0_serving_processes": processes, "actual_nice19_cpu_plot_waiter": waiter,
        "qualified_runtime_source_sha256": sources[runtime_name],
        "real_serving_tokenizer_files_verified": cpu["context_tokenizer"],
        "latest_runtime_snapshot": latest, "cpu_evidence_sha256": sha256_file(cpu_path),
        "operator_sha256": sha256_file(Path(__file__)),
        "false_trigger_case_reached_and_verified": False,
        "complete_work_verified": False, "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        key: result[key] for key in (
            "code_commit", "engine_tree", "false_trigger_case_reached_and_verified",
            "complete_work_verified", "performance_accepted",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
