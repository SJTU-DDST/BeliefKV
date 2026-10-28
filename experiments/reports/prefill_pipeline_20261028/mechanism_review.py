"""Bind staged demand accounting, JOIN grants and sibling service after completion."""

from datetime import datetime, timezone
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
    output = HERE / "mechanism_review.json"
    if output.exists():
        raise FileExistsError("preserve original complete mechanism evidence")
    complete_path = HERE / "complete_review.json"
    complete = causal.load(complete_path)
    if not all(complete.get(field) is True for field in (
        "all_normal_tails_retained", "exact_work_and_original_stopping_verified",
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified",
    )):
        raise ValueError("complete exact work, tails and actual owned exit are required")
    for source, digest in complete["inputs_sha256"].items():
        if sha256_file(Path(source)) != digest:
            raise ValueError("complete comparison inputs changed")
    proof = complete["proofs"]["Full (demand pipeline)"]
    registration = Path(proof["registration"])
    plan = causal.load(registration)
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("the completed supervisor or an owned child is still live")
    arm = Path(plan["run_root"]) / "full"
    demand_path = HERE / "complete_demand_h2d_service.json"
    demand = causal.load(demand_path)
    native = demand["native_legacy_accounting_scope"]
    if (
        demand["registration_sha256"] != sha256_file(registration)
        or native["code_commit"] != proof["code_commit"]
        or native["engine_tree"] != proof["engine_tree"]
        or native["prefetch_journal_commands_by_source"].get("tool_wait", 0)
        or demand["byte_balance"]["exact_physical_bytes_reconciled"] is not True
        or demand["staged_native_service"][
            "all_members_have_native_service_or_explicit_cancellation"
        ] is not True
        or demand["staged_native_service"]["native_completion_receipts_required"] is not True
    ):
        raise ValueError("demand ACKs, exact native service, bytes or no-tool-prefetch contract differ")
    prefetch = HERE / "complete_wait_prefetch.json.gz"
    grants = grant_audit(arm, prefetch_report=prefetch)
    reader = SealedRecords()
    siblings = sibling_inspect(*(
        reader(arm / relative) for relative in (
            "opportunities/admission_opportunities.jsonl",
            "replay/runtime_events.replay.jsonl", "replay/native_requests.jsonl",
            "server/runtime_audit.jsonl",
        )
    ))
    siblings["input_jsonl_prefixes"] = reader.prefixes
    inputs = {
        str(path): sha256_file(path) for path in (
            Path(__file__), complete_path, demand_path, registration, prefetch,
            ROOT / "scripts/audit_demand_prefill_pipeline.py",
            ROOT / "scripts/audit_staged_native_restore.py",
            ROOT / "scripts/audit_reference_join_grants.py",
            ROOT / "scripts/audit_sibling_native_service.py",
        )
    }
    for evidence in (native, demand, grants, siblings):
        for source, seal in evidence["input_jsonl_prefixes"].items():
            if Path(source).stat().st_size != seal["complete_prefix_bytes"] or (
                sha256_file(Path(source)) != seal["complete_prefix_sha256"]
            ):
                raise ValueError("complete native/service input is not stable")
            inputs[source] = seal["complete_prefix_sha256"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete exact demand ACK/service, JOIN and sibling evidence; not causal gains.",
        "native_transfer_and_worker_review": native,
        "staged_demand_accounting": demand["byte_balance"],
        "staged_native_demand": demand["staged_native_demand"],
        "staged_native_service": demand["staged_native_service"],
        "join_grants": grants, "sibling_service": siblings,
        "physical_plan_review": complete["physical_plan_review"],
        "inputs_sha256": inputs, "performance_accepted": False,
        "limitations": [
            "Legacy unexplained untagged bytes remain visible and must reconcile as exact staged demand.",
            "Staging requests is not speculative prefetch or a new request Host-hit counter.",
            "Exact copies and subsequent service do not establish hidden DMA or saved JCT.",
            "Logical eligibility, native releases and reserved extents are separate quantities.",
            "Unknown grant/session service associations remain unknown.",
        ],
    }
    causal.reservation.write_json(output, result)
    print({
        "staged_demand": demand["staged_native_demand"]["counts"],
        "staged_native_service": demand["staged_native_service"]["counts"],
        "native_bytes": demand["byte_balance"],
        "join_grants": grants["summary"], "sibling_service": siblings["summary"],
    })


if __name__ == "__main__":
    main()
