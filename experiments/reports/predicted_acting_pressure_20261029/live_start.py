"""Seal actual predicted-pressure startup without claiming complete performance."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import COMMON_TREE, FREE_EXTENT_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("startup reviewer must hide CUDA")
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve original startup evidence")
    ops = Path("/home/longhao/experiment/ops")
    registration = ops / "predicted_acting_pressure_20261029_r1.registration.json"
    control_path = ops / "prefill2048_mean120_thunder_20261029_r1.registration.json"
    original_path = ops / "decay_tick2s_no_handoff_20261030_r1.registration.json"
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    for path in (control_path, original_path):
        previous = causal.load(path)
        if not causal.predecessor_ready({**previous, "after_registration": str(path)}):
            raise ValueError("matched predecessor has not actually exited")
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details",
        "sampling_mode", "stream_response_metrics", "request_timeout_s",
        "max_running_requests", "workflow_arrivals",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("Full changed common work, arrivals, or serving parameters")
    if (
        plan["engine_tree"] != FREE_EXTENT_TREE or control["engine_tree"] != COMMON_TREE
        or {k: v for k, v in plan["tool_timing_overlay"].items() if k != "path"}
        != {k: v for k, v in control["tool_timing_overlay"].items() if k != "path"}
    ):
        raise ValueError("method-specific native tree or tool-timing contract changed")
    cpu_path = ROOT / Path(__file__).relative_to(ROOT).parent / "cpu_r1/evidence.json"
    cpu = causal.load(cpu_path)
    if (
        cpu["returncode"] or cpu["failed_error_skipped_cases"]
        or not cpu["sources_unchanged_during_execution"]
        or not cpu["all_retained_literal_identities_present"]
    ):
        raise ValueError("current CPU qualification is incomplete")
    sources_path = Path(cpu["source_manifest"]["path"])
    if sha256_file(sources_path) != cpu["source_manifest"]["sha256"]:
        raise ValueError("qualified source manifest changed")
    sources = causal.load(sources_path)
    for name in (
        "beliefkv/runtime/predicted_acting_pressure.py",
        "beliefkv/policy/predicted_acting_pressure.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/experiments/internal_comparison.py",
        "scripts/run_causal_comparison.py",
    ):
        if plan["source_sha256"][name] != sources[name]:
            raise ValueError("live policy differs from qualified CPU sources")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("Full is not actually running")
    processes = {}
    for name in ("watcher", "server", "driver"):
        item = state[name]
        actual = causal.reservation.process(item["pid"])
        if (
            not causal.reservation.alive(item) or not actual or not actual["argv"]
            or os.getpriority(os.PRIO_PROCESS, item["pid"]) != 0
        ):
            raise ValueError("actual serving process or nice priority changed")
        processes[name] = actual
    gpu_pids = {
        int(line.strip()) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owned_gpu = [
        item for item in state["owned"]
        if item["pid"] in gpu_pids and causal.reservation.alive(item)
    ]
    if (
        len(owned_gpu) != 1 or gpu_pids != {owned_gpu[0]["pid"]}
        or os.getpriority(os.PRIO_PROCESS, owned_gpu[0]["pid"]) != 0
    ):
        raise ValueError("expected one actual owned nice0 GPU scheduler")
    processes["gpu_scheduler"] = causal.reservation.process(owned_gpu[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(
        arm / "opportunities/admission_opportunities.jsonl",
    )
    expected = {
        "physical_disabled": False, "agent_free_dispatch_interval_ms": 0,
        "agent_control_interval_ms": 2000, "workflow_size_resume": False,
        "tool_prefetch": False, "execution_frontier_restore": False,
        "layer_handoff_enabled": False, "layer_handoff_free_extent": False,
        "predicted_tool_offload": True, "tool_offload_min_survival": .75,
        "demand_prefill_pipeline_installed": True,
    }
    if (
        any(latest.get(key) != value for key, value in expected.items())
        or latest["agent_admission"].get("pressure_pause_policy") != "predicted_acting"
    ):
        raise ValueError("live pressure configuration differs from declaration")
    receipt_work = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    reader = SealedRecords()
    completed = validate_receipts(reader(arm / "replay/native_requests.jsonl"), receipt_work)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "actual startup and sealed completed prefix, not complete performance",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "same_common_fields_as_complete_control": list(common),
        "matched_predecessors_actual_exit_verified": True,
        "actual_nice0_serving_processes": processes,
        "cpu_evidence_sha256": sha256_file(cpu_path),
        "exact_completed_receipts_in_prefix": completed,
        "input_jsonl_prefixes": reader.prefixes,
        "verified_runtime_fields": expected,
        "pressure_counts_in_latest_snapshot": {
            key: value for key, value in latest["agent_admission"]["counts"].items()
            if key.startswith("acting_pressure_")
        },
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_work_verified": False, "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        key: result[key] for key in (
            "code_commit", "exact_completed_receipts_in_prefix",
            "pressure_counts_in_latest_snapshot", "complete_work_verified",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
