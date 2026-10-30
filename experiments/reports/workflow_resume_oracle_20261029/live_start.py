"""Seal the actual oracle startup; no complete performance claim."""

from datetime import datetime, timezone
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
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("startup observer must hide CUDA")
    ops = ROOT.parent / "ops"
    registration = ops / "workflow_model_tool_oracle_20261029_r1.registration.json"
    control_path = ops / "prefill2048_mean120_thunder_20261029_r1.registration.json"
    output = ROOT / "experiments/reports/workflow_resume_oracle_20261029/live_start_r1.json"
    if output.exists():
        raise FileExistsError("preserve actual original startup evidence")
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "sampling_mode",
        "stream_response_metrics", "request_timeout_s", "max_running_requests", "workflow_arrivals",
    )
    if any(plan.get(name) != control.get(name) for name in common):
        raise ValueError("oracle changed shared work/serving/arrival settings")
    state = causal.load(registration.with_suffix(".state.json"))
    if state.get("status") != "running_full":
        raise ValueError("oracle Full is not actually running")
    actual = {}
    for name in ("watcher", "server", "driver"):
        row = state[name]
        if not causal.reservation.alive(row) or os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0:
            raise ValueError("actual oracle process or nice priority differs")
        actual[name] = causal.reservation.process(row["pid"])
    gpu_pids = {
        int(line.strip()) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owned = [row for row in state["owned"] if row["pid"] in gpu_pids and causal.reservation.alive(row)]
    if len(owned) != 1 or gpu_pids != {owned[0]["pid"]} or os.getpriority(os.PRIO_PROCESS, owned[0]["pid"]) != 0:
        raise ValueError("expected one actual nice0 owned oracle GPU scheduler")
    actual["gpu_scheduler"] = causal.reservation.process(owned[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True, join_blocked_handoff=True,
        free_dispatch_interval_ms=500, control_interval_ms=2000, workflow_size_resume=True,
        workflow_work_source="oracle",
        workflow_work_model_sha256=plan["reference_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"],
    )
    expected = {
        "agent_free_dispatch_interval_ms": 500, "agent_control_interval_ms": 2000,
        "workflow_size_resume": True, "workflow_work_source": "oracle",
        "tool_prefetch": False, "execution_frontier_restore": False,
        "layer_handoff_enabled": False, "predicted_tool_offload": True,
    }
    if any(latest.get(name) != value for name, value in expected.items()):
        raise ValueError("actual runtime oracle configuration differs")
    reader = SealedRecords()
    reference_work = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    count = validate_receipts(reader(arm / "replay/native_requests.jsonl"), reference_work)
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "actual_nice0_owned_processes": actual, "verified_runtime_fields": expected,
        "exact_completed_receipts_in_prefix": count,
        "predictor": latest["agent_admission"]["workflow_work_predictor"],
        "same_common_fields_as_complete_control": list(common),
        "input_jsonl_prefixes": reader.prefixes, "operator_sha256": sha256_file(Path(__file__)),
        "future_model_and_tool_information_used": True,
        "complete_work_verified": False, "performance_accepted": False,
    })
    print({"exact_completed_receipts": count, "verified_runtime_fields": expected})


if __name__ == "__main__":
    main()
