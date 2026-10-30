"""Seal observed Full startup for its registered population and control cadence."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal
from scripts.run_causal_gpu_replay import source_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve earlier observations")
    plan = causal.load(args.registration)
    state = causal.load(args.registration.with_suffix(".state.json"))
    causal.comparison.verify_sources(plan)
    if state["status"] != "running_full" or plan["order"] != ["full"]:
        raise ValueError("require an actually running Full")
    processes = {}
    for name in ("watcher", "server", "driver"):
        row = state.get(name)
        if not row or not causal.reservation.alive(row) or os.getpriority(os.PRIO_PROCESS, row["pid"]):
            raise ValueError("missing live nice0 measured process: " + name)
        processes[name] = causal.reservation.process(row["pid"])
    gpu_pids = {
        int(line) for line in causal.qualified.command([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ]).splitlines() if line.strip().isdigit()
    }
    owned = [row for row in state["owned"] if row["pid"] in gpu_pids and causal.reservation.alive(row)]
    if len(owned) != 1 or gpu_pids != {owned[0]["pid"]}:
        raise ValueError("the model is not the sole owned GPU job")
    processes["GPU_scheduler"] = causal.reservation.process(owned[0]["pid"])
    if os.getpriority(os.PRIO_PROCESS, owned[0]["pid"]):
        raise ValueError("GPU scheduler must remain nice0")
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    latest = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    common = plan["reference_environment"]
    oracle = common["BELIEFKV_WORKFLOW_WORK_SOURCE"] == "oracle"
    dispatch = int(common.get("BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS", "0"))
    validate_reference_admission(
        latest["agent_admission"], join_free_admission=True, coadmit_join_children=True,
        free_continuation_admission=True, join_blocked_handoff=True,
        free_dispatch_interval_ms=dispatch, control_interval_ms=2000, workflow_size_resume=oracle,
        workflow_work_source="oracle" if oracle else "learned",
        workflow_work_model_sha256=common.get("BELIEFKV_WORKFLOW_WORK_MODEL_SHA256", ""),
    )
    expected = {
        "agent_free_dispatch_interval_ms": dispatch, "agent_control_interval_ms": 2000,
        "workflow_size_resume": oracle, "tool_prefetch": False,
        "execution_frontier_restore": False, "layer_handoff_enabled": False,
        "predicted_tool_offload": True,
    }
    if any(latest.get(name) != value for name, value in expected.items()):
        raise ValueError("actual fixed policy differs")
    reader = SealedRecords()
    graph, _expected, _evidence = source_plan(Path(plan["source_arm"]))
    usage = graph.request_usage
    if (
        graph.summary() != plan["source_plan"]
        or {node.workflow_id for node in graph.nodes} != set(plan["workflow_ids"])
    ):
        raise ValueError("source work differs from the declaration")
    seen = set()
    for row in reader(arm / "replay/native_requests.jsonl"):
        request = row["request_id"]
        if (
            request in seen or request not in usage
            or row["exact_tokens_verified"] is not True
            or row["native_receipt"]["sampling_path"] != "position"
            or (row["prompt_tokens"], row["output_tokens"]) != usage[request]
        ):
            raise ValueError("completed prefix receipts differ from the new source")
        seen.add(request)
    if not seen:
        raise ValueError("require actual completed model output")
    causal.reservation.write_json(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(args.registration.resolve()),
        "registration_sha256": sha256_file(args.registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "nice0_owned_processes": processes, "sole_GPU_owner": True,
        "observed_policy": expected, "completed_prefix_receipts": len(seen),
        "input_jsonl_prefixes": reader.prefixes, "operator_sha256": sha256_file(Path(__file__)),
        "future_information_used": oracle, "complete_work_verified": False,
        "receipt_scope": "source counts and native exact-token flags; final source validation remains required",
        "performance_accepted": False,
    })
    print({"completed_prefix_receipts": len(seen), "oracle": oracle, "processes_nice0": True})


if __name__ == "__main__":
    main()
