"""Preserve the first formal native-hook failure without a performance claim."""

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REGISTRATION = ROOT.parent / "ops/tokencake_locobench_v230_v311_20261028_r1.registration.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.qualify_paper_prefix_transfers import gpu_processes


def main():
    output = HERE / "formal_r1_failure.json"
    if output.exists():
        raise FileExistsError("preserve first formal failure review")
    plan = causal.load(REGISTRATION)
    state_path = REGISTRATION.with_suffix(".state.json")
    state = causal.load(state_path)
    arm = Path(plan["run_root"]) / "tokencake_paper_observed"
    cleanup_path = arm / "owned_process_cleanup.json"
    cleanup = causal.load(cleanup_path)
    if (
        state["status"] != "stopped_for_investigation"
        or state["completed_arms"]
        or state["original_exit_codes"] != {"tokencake_paper_observed": 1}
        or any(causal.reservation.alive(row) for row in (
            state["watcher"], state["server"], state["driver"], *cleanup["identities"],
        ))
        or cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at")
        or gpu_processes()
    ):
        raise ValueError("failed TokenCake processes have not actually exited and cleaned")
    causal.comparison.verify_sources(plan)
    scheduler_path = Path(plan["engine_checkout"]) / "python/sglang/srt/managers/scheduler.py"
    scheduler = ast.parse(scheduler_path.read_bytes())
    method = next(
        node for node in ast.walk(scheduler)
        if isinstance(node, ast.FunctionDef) and node.name == "_get_new_batch_prefill_raw"
    )
    called = {
        node.attr for node in ast.walk(method) if isinstance(node, ast.Attribute)
    } | {
        argument.value for node in ast.walk(method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "getattr" for argument in node.args[1:2]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    }
    if "allow_native_prefill_candidate" in called or "on_prefill_candidate_result" not in called:
        raise ValueError("failed native scheduler does not reproduce the missing guard binding")
    log_path = arm / "server.log"
    if "TokenCake native admission bypassed its capacity guard" not in log_path.read_text():
        raise ValueError("original scheduler exception was not retained")
    reader = SealedRecords()
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "preserved_formal_native_admission_hook_failure",
        "original_returncode": 1, "completed_receipts": len(receipts),
        "all_owned_processes_exited": True, "GPU_compute_processes_absent": True,
        "native_guard_hook_present": False, "native_result_callback_present": True,
        "root_cause": "The common scheduler reports native admission but never calls TokenCake's sequential capacity guard.",
        "prior_cpu_and_gpu_scope": "Direct adapter and PrefillAdder components did not execute the actual scheduler candidate loop.",
        "complete_baseline_or_performance_result": False, "performance_accepted": False,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                Path(__file__), REGISTRATION, state_path, cleanup_path, log_path,
                scheduler_path, HERE / "formal_registration_entry.json",
            )
        },
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        name: result[name] for name in (
            "status", "completed_receipts", "all_owned_processes_exited",
            "native_guard_hook_present", "complete_baseline_or_performance_result",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
