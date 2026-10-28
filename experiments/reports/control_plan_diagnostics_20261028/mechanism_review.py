"""Bind diagnostic physical attempts, native ACKs and complete service evidence."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_reference_join_grants import audit as grant_audit
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_sibling_native_service import inspect as sibling_inspect
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve mechanism review")
    complete_path = HERE / "complete_review.json"
    complete = causal.load(complete_path)
    if not all(complete.get(field) is True for field in (
        "all_normal_tails_retained", "exact_work_and_original_stopping_verified",
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified",
    )):
        raise ValueError("complete work and actual owned exit are required")
    registration = Path(complete["proofs"]["Full (profiled)"]["registration"])
    plan = causal.load(registration)
    arm = Path(plan["run_root"]) / "full"
    helper = ROOT / "experiments/reports/join_blocked_layer_groups_20261028/handoff_ack_review.py"
    spec = importlib.util.spec_from_file_location("diagnostic_native_ack_review", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    native = module.inspect("Full (profiled)", registration, "full")
    if (
        native["code_commit"] != plan["code_commit"]
        or native["prefetch_journal_commands_by_source"].get("tool_wait", 0)
        or not native["native_untagged_demand_plus_handoff_reconciled"]
    ):
        raise ValueError("native work, disabled tool prefetch or byte balance differs")
    grants, reader = grant_audit(arm), SealedRecords()
    siblings = sibling_inspect(*(
        reader(arm / relative) for relative in (
            "opportunities/admission_opportunities.jsonl",
            "replay/runtime_events.replay.jsonl", "replay/native_requests.jsonl",
            "server/runtime_audit.jsonl",
        )
    ))
    siblings["input_jsonl_prefixes"] = reader.prefixes
    inputs = {
        str(path): sha256_file(path)
        for path in (Path(__file__), helper, complete_path, registration)
    }
    for evidence in (native, grants, siblings):
        for path, seal in evidence["input_jsonl_prefixes"].items():
            if (
                Path(path).stat().st_size != seal["complete_prefix_bytes"]
                or sha256_file(Path(path)) != seal["complete_prefix_sha256"]
            ):
                raise ValueError("mechanism input is not complete and stable")
            inputs[path] = seal["complete_prefix_sha256"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete native transfers and service associations; not causal JCT gains.",
        "native_transfer_and_worker_review": native,
        "join_grants": grants, "sibling_service": siblings,
        "physical_attempt_outcomes": complete["diagnostics"]["physical_attempt_outcomes"],
        "inputs_sha256": inputs, "performance_accepted": False,
        "limitations": [
            "Physical release, reserved extents and logical budget remain separate quantities.",
            "Transfers and native service association do not establish hidden DMA or saved JCT.",
            "Reference and diagnostic native engines and profiling differ.",
            "The Host-only-tail repair is absent from this frozen experiment.",
        ],
    }
    causal.reservation.write_json(output, result)
    print({
        "transfers": native["transfers"], "handoffs": native["handoff_events"],
        "join_grants": grants["summary"], "sibling_service": siblings["summary"],
    })


if __name__ == "__main__":
    main()
