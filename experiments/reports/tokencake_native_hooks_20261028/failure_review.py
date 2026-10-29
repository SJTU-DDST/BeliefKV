"""Seal the failed native-hook run without turning its prefix into a result."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REGISTRATION = ROOT.parent / "ops/tokencake_locobench_native_hooks_20261028_r1.registration.json"
CONTROL = ROOT.parent / "ops/v345_locobench_categorized_thunder_decay_20261027.registration.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as controls


def main():
    output = HERE / "formal_native_hooks_failure.json"
    if output.exists():
        raise FileExistsError("preserve original failed TokenCake review")
    plan = controls.load(REGISTRATION)
    state = controls.load(REGISTRATION.with_suffix(".state.json"))
    controls.comparison.verify_sources(plan)
    arm = Path(plan["run_root"]) / "tokencake_paper_observed"
    cleanup_path = arm / "owned_process_cleanup.json"
    cleanup = controls.load(cleanup_path)
    identities = [
        *(state.get(name) for name in ("watcher", "server", "driver") if state.get(name)),
        *state.get("owned", ()), *cleanup.get("identities", ()),
    ]
    if (
        state["status"] != "stopped_for_investigation" or state["completed_arms"]
        or state["original_exit_codes"] != {"tokencake_paper_observed": 1}
        or cleanup["remaining_processes"] or not cleanup.get("finished_at")
        or any(controls.reservation.alive(row) for row in identities)
    ):
        raise ValueError("failed TokenCake ownership or terminal state is unverified")
    log_path = arm / "server.log"
    log = log_path.read_text()
    traceback = log[log.index("Scheduler hit an exception: Traceback"):].split(
        "\n[2026-", 1,
    )[0]
    if (
        "TokenCake could not prove its native prefix path" not in traceback
        or "waiting_pressure" not in traceback
        or "tokencake_capacity.py" not in traceback
    ):
        raise ValueError("native failure differs from the recorded capacity-path error")
    execution_path = arm / "replay/execution_failure.json"
    execution = controls.load(execution_path)
    if execution["error_type"] != "ConnectionError" or "socket is unavailable" not in execution["error"]:
        raise ValueError("driver failure does not follow the native scheduler disconnect")
    reader = SealedRecords()
    receipts_path = arm / "replay/native_requests.jsonl"
    receipts = list(reader(receipts_path))
    reference = controls.load(CONTROL)
    expected_path = Path(reference["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl"
    expected = controls.receipt_work(expected_path, sampling_mode="position")
    count = validate_receipts(receipts, expected)
    if not 0 < count < 3674 or (arm / "replay/summary.json").exists():
        raise ValueError("failed TokenCake was mistaken for a complete workload")
    runtime_path = arm / "opportunities/admission_opportunities.jsonl"
    runtime = None
    for row in reader(runtime_path):
        if row.get("event") == "admission_runtime_state":
            runtime = row
    if runtime is None:
        raise ValueError("failed native run has no actual policy snapshot")
    inputs = (
        Path(__file__), REGISTRATION, REGISTRATION.with_suffix(".state.json"),
        cleanup_path, log_path, execution_path, expected_path,
        arm / "replay/cleanup_failure.json", arm / "replay.log",
        ROOT / "experiments/reports/agentix_radix_sessions_20261028/gpu_native_consumers_r2/summary.json",
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed_partial_native_hook_tokencake_not_a_performance_result",
        "actual_owned_exit_and_cleanup_verified": True,
        "expected_requests": 3674, "exact_completed_receipts": count,
        "native_scheduler_failure_traceback": traceback,
        "driver_disconnect_is_secondary": True,
        "last_paper_state": runtime["paper_baseline"],
        "native_path_depth_refusals": runtime["paper_baseline"]["transfer_counts"].get("path_depth_declined", 0),
        "exact_failed_node_identity_unrecorded": True,
        "no_complete_jct_or_throughput_claim": True,
        "dependent_agentix_waiter_stopped_without_gpu_children": True,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in inputs},
        "performance_accepted": False,
    }
    controls.reservation.write_json(output, result)
    print(json.dumps({
        name: result[name] for name in (
            "status", "exact_completed_receipts", "native_path_depth_refusals",
            "actual_owned_exit_and_cleanup_verified",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
