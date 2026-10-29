"""Verify actual R2 execution is a source-identical repeat, not main's new repair."""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
PRIOR = OPS / "demand_prefill_pipeline_20261028_r1.registration.json"
REPEAT = OPS / "demand_prefill_pipeline_20261028_r2.registration.json"


def main():
    output = HERE / "repeat_r2_live_start.json"
    if output.exists():
        raise FileExistsError("preserve earlier repeat startup evidence")
    original, plan = (json.loads(path.read_bytes()) for path in (PRIOR, REPEAT))
    sys.path.insert(0, plan["execution_checkout"])
    from scripts import run_causal_comparison as causal
    from scripts.audit_collecting_agent_pipeline import validate_receipts
    from scripts.audit_sealed_wait_prefetch import SealedRecords

    helper = HERE / "repeat_registration.py"
    spec = importlib.util.spec_from_file_location("repeat_live_metadata_gate", helper)
    repeat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(repeat)
    changed = repeat.validate_metadata(original, plan)
    causal.comparison.verify_sources(plan)
    causal.fresh.check_hold(plan, released=True)
    if (
        plan["after_registration"] != str(PRIOR)
        or plan["repeat_of"]["registration_sha256"] != causal.sha256_file(PRIOR)
        or plan["repeat_of"]["main_admission_retry_fix_included"] is not False
        or not causal.predecessor_ready(plan)
    ):
        raise ValueError("repeat predecessor or exact-source declaration changed")
    state = causal.load(REPEAT.with_suffix(".state.json"))
    if state["status"] != "running_full":
        raise ValueError("wait for actual repeat request submission")
    processes = {}
    for name in ("watcher", "server", "driver"):
        row = state[name]
        if not causal.reservation.alive(row) or os.getpriority(os.PRIO_PROCESS, row["pid"]) != 0:
            raise ValueError("repeat process is absent or is not nice0")
        processes[name] = causal.reservation.process(row["pid"])
    gpu_pids = {
        int(line) for line in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if line.strip().isdigit()
    }
    owners = [
        row for row in state["owned"] if row["pid"] in gpu_pids and causal.reservation.alive(row)
    ]
    if len(owners) != 1 or gpu_pids != {owners[0]["pid"]}:
        raise ValueError("repeat scheduler is not the sole owned GPU process")
    processes["gpu_scheduler"] = causal.reservation.process(owners[0]["pid"])
    arm = Path(plan["run_root"]) / "full"
    causal.observed_configuration(plan, "full", arm)
    runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    expected = {
        "physical_disabled": False, "profile_shared_path": False,
        "demand_prefill_pipeline": True, "demand_prefill_pipeline_installed": True,
        "demand_prefill_pipeline_progress_probe": False, "tool_prefetch": False,
        "tool_prefetch_priority_ms": 0., "execution_frontier_restore": False,
    }
    if any(runtime.get(name) != value for name, value in expected.items()):
        raise ValueError("actual repeat helper, probe, tool or profile settings differ")
    info = causal.load(arm / "server/server_info.json")
    math = {
        "enable_deterministic_inference": False, "attention_backend": "fa3",
        "sampling_backend": "flashinfer", "enable_custom_logit_processor": True,
    }
    if any(info.get(name) != value for name, value in math.items()):
        raise ValueError("repeat changed formal numerical or stopping mode")
    reader = SealedRecords()
    count = validate_receipts(
        reader(arm / "replay/native_requests.jsonl"),
        causal.receipt_work(
            Path(original["run_root"]) / "full/replay/native_requests.jsonl",
            sampling_mode="position",
        ),
    )
    if count == 0:
        raise ValueError("wait for a complete repeat receipt")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual source-identical repeat startup and exact receipt prefix, not complete performance.",
        "registration": str(REPEAT), "code_commit": plan["code_commit"],
        "engine_tree": plan["engine_tree"], "changed_metadata_only": changed,
        "actual_nice0_processes": processes, "actual_pipeline_settings": expected,
        "actual_formal_math": math, "exact_completed_receipts": count,
        "input_jsonl_prefixes": reader.prefixes,
        "main_admission_retry_fix_included": False,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "inputs_sha256": {
            str(path): causal.sha256_file(path)
            for path in (Path(__file__), helper, PRIOR, REPEAT, HERE / "repeat_r2_entry_check.json")
        },
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print({
        "exact_completed_receipts": count, "code_commit": plan["code_commit"],
        "actual_process_pids": {name: row["pid"] for name, row in processes.items()},
    })


if __name__ == "__main__":
    main()
