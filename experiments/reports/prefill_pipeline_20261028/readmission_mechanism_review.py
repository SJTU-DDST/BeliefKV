"""Bind completed repaired Full demand, capacity, sibling and EOS evidence."""

from collections import defaultdict
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "readmission_complete"
sys.path.insert(0, str(ROOT))


def helper(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REVIEW = helper(HERE / "readmission_complete_review.py", "readmission_mechanism_contract")
BASE = REVIEW.BASE

from scripts.audit_demand_prefill_pipeline import audit as demand_audit
from scripts.audit_eos_trigger_lifecycle import audit as eos_audit
from scripts.audit_reference_join_grants import audit as grant_audit
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_sibling_native_service import inspect as sibling_inspect
from scripts import audit_tool_eos_guard_cases as guard


def main():
    output = OUTPUT / "mechanism_review.json"
    detail_path = OUTPUT / "mechanism_detail.json.gz"
    if output.exists() or detail_path.exists():
        raise FileExistsError("preserve earlier repaired Full mechanism evidence")
    complete_path = OUTPUT / "complete_review.json"
    complete = BASE.causal.load(complete_path)
    if (
        any(complete.get(name) is not True for name in (
            "exact_work_and_original_stopping_verified", "all_normal_tails_retained",
            "two_fresh_idle_snapshots_and_actual_owned_exit_verified",
            "main_admission_retry_fix_included",
        ))
        or (complete["requests"], complete["prompt_tokens"], complete["output_tokens"])
        != (3674, 145565966, 549671)
    ):
        raise ValueError("mechanisms require the complete repaired Full and actual owned exit")
    for source, digest in complete["inputs_sha256"].items():
        if BASE.sha256_file(Path(source)) != digest:
            raise ValueError("complete repaired Full evidence changed")
    plan = BASE.causal.load(REVIEW.REGISTRATION)
    REVIEW.same_work(plan)
    BASE.completed(plan, REVIEW.REGISTRATION, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    arm = Path(plan["run_root"]) / "full"
    prefetch_path = OUTPUT / "complete_wait_prefetch.json.gz"
    warm_path = OUTPUT / "complete_wait_prefetch_summary.json"
    warm = BASE.causal.load(warm_path)
    if (
        warm["collection_code_commit"] != plan["code_commit"]
        or warm["summary"]["tool_commands"]
    ):
        raise ValueError("prefetch proof has another source or enabled tool prefetch")
    demand = demand_audit(REVIEW.REGISTRATION, "full")
    if (
        not demand["byte_balance"]["exact_physical_bytes_reconciled"]
        or not demand["staged_native_service"][
            "all_members_have_native_service_or_explicit_cancellation"
        ]
        or not demand["staged_native_service"]["native_completion_receipts_required"]
    ):
        raise ValueError("repaired demand lacks exact ACKs, bytes or subsequent native service")
    grants = grant_audit(arm, prefetch_report=prefetch_path)
    reader = SealedRecords()
    opportunities = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    sibling = sibling_inspect(
        opportunities, reader(arm / "replay/runtime_events.replay.jsonl"),
        reader(arm / "replay/native_requests.jsonl"), reader(arm / "server/runtime_audit.jsonl"),
    )
    physical_helper = ROOT / "experiments/reports/full_only_pressure_index_20261028/complete_review.py"
    physical = helper(physical_helper, "repaired_physical_plan_review").physical_review(opportunities)
    if not physical["journal_release_totals_reconciled"]:
        raise ValueError("repaired logical/physical release journals do not reconcile")
    sibling["input_jsonl_prefixes"] = reader.prefixes
    multi = [
        row for row in sibling["records"]
        if row["status"] == "native_batch_join_and_completed_sessions_verified"
        and row["members"] >= 2
    ]
    if len(multi) != sibling["summary"]["verified_multi_member_batches"]:
        raise ValueError("legacy multi-member journal count differs")
    forwards = defaultdict(list)
    for row in multi:
        if type(row["forward_iter"]) is not int or row["phase"] != "prefill":
            raise ValueError("sibling journal lacks its actual native forward identity")
        forwards[(row["forward_iter"], row["worker_sample_id"])].append(row["join_id"])
    ready = [
        row for row in opportunities if row.get("event") == "native_demand_restore_ready"
    ]
    if any(type(row.get("retry_native_capacity_check")) is not bool for row in ready):
        raise ValueError("repaired ACK-ready event is missing its actual retry observation")
    retried = [row for row in ready if row["retry_native_capacity_check"]]
    if (
        len(ready) != complete["ack_restore_ready_events"]
        or len(retried) != complete["ack_native_capacity_retry_events"]
    ):
        raise ValueError("complete retry and mechanism journals disagree")
    previous = BASE.causal.load(BASE.FULL)
    expected = Path(previous["run_root"]) / "full/replay/native_requests.jsonl"
    eos = eos_audit(REVIEW.REGISTRATION, expected)
    case_path = ROOT / (
        "experiments/reports/reference_join_siblings_20261027/tool_closer_trigger_audit.json"
    )
    cases = BASE.causal.load(case_path)
    original_guard_path = HERE / "complete_guard_cases.json"
    original_guard = BASE.causal.load(original_guard_path)
    if (
        original_guard["summary"]["all_cases_verified"] is not True
        or original_guard["inputs_sha256"][str(case_path)] != BASE.sha256_file(case_path)
    ):
        raise ValueError("original reviewed guard cases changed")
    case_rows = [
        row for row in cases["rows"]
        if row["status"] == "intermediate_request_observed"
        and row["native_tool_delimiter_evidence"] == "closer_without_opener"
    ]
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    count = guard.validate_receipts(
        receipts, BASE.causal.receipt_work(expected, sampling_mode="position"),
    )
    known = guard.inspect(
        case_rows, receipts, opportunities,
        opener_ids=set(cases["opener_token_ids"]), closer_ids=set(cases["closer_token_ids"]),
        clock_domain=guard.local_monotonic_clock_domain(),
    )
    tokenizer = Path(plan["source_server_parameters"]["model_path"]) / "tokenizer_config.json"
    if (
        count != 3674 or len(case_rows) != 4 or not known["summary"]["all_cases_verified"]
        or not eos["summary"]["all_triggered_requests_observed_final"]
        or BASE.sha256_file(tokenizer) != cases["tokenizer_config_sha256"]
    ):
        raise ValueError("complete repaired EOS, guard or tokenizer evidence failed")
    inputs = {
        str(path): BASE.sha256_file(path) for path in (
            Path(__file__), HERE / "readmission_complete_review.py", complete_path,
            REVIEW.REGISTRATION, BASE.FULL, prefetch_path, warm_path, case_path,
            original_guard_path, expected, tokenizer, physical_helper,
            ROOT / "scripts/audit_demand_prefill_pipeline.py",
            ROOT / "scripts/audit_staged_native_restore.py",
            ROOT / "scripts/audit_reference_join_grants.py",
            ROOT / "scripts/audit_sibling_native_service.py",
            ROOT / "scripts/audit_eos_trigger_lifecycle.py", Path(guard.__file__),
        )
    }
    for evidence in (warm, demand, demand["native_legacy_accounting_scope"], grants, sibling, eos):
        for source, seal in evidence["input_jsonl_prefixes"].items():
            if Path(source).stat().st_size != seal["complete_prefix_bytes"] or (
                BASE.sha256_file(Path(source)) != seal["complete_prefix_sha256"]
            ):
                raise ValueError("complete repaired mechanism input is not stable: " + source)
            inputs[source] = seal["complete_prefix_sha256"]
    detail = {
        "demand": demand, "join_grants": grants, "sibling_service": sibling,
        "physical_plan_review": physical, "final_eos": eos, "known_guards": known,
        "retry_events": retried, "unique_multi_member_forwards": [
            {"forward_iter": key[0], "worker_sample_id": key[1], "join_ids": value}
            for key, value in sorted(forwards.items())
        ],
    }
    raw = json.dumps(detail, indent=2, allow_nan=False).encode() + b"\n"
    with detail_path.open("xb") as stream:
        stream.write(gzip.compress(raw, mtime=0))
    if gzip.decompress(detail_path.read_bytes()) != raw:
        raise ValueError("repaired mechanism archive differs from original bytes")
    inputs[str(detail_path)] = BASE.sha256_file(detail_path)
    native = demand["native_legacy_accounting_scope"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete repaired-source native demand/service, capacity, JOIN, sibling and EOS.",
        "exact_completed_receipts": count, "all_normal_tails_retained": True,
        "byte_balance": demand["byte_balance"],
        "staged_native_demand": demand["staged_native_demand"]["counts"],
        "staged_native_service": demand["staged_native_service"]["counts"],
        "native_transfers": native["transfers"], "handoff_events": native["handoff_events"],
        "handoff_consumer_h2d_bytes": native["handoff_consumer_h2d_bytes"],
        "physical_plan_review": physical, "join_prefetch_summary": warm["summary"],
        "join_grants": grants["summary"], "sibling_service": sibling["summary"],
        "multi_member_journal_records": len(multi),
        "distinct_multi_member_native_forwards": len(forwards),
        "final_eos": eos["summary"], "known_guards": known["summary"],
        "ack_restore_ready_events": len(ready), "ack_native_capacity_retry_events": len(retried),
        "production_guard_or_classifier_changed": False,
        "main_admission_retry_fix_included": True,
        "inputs_sha256": inputs, "performance_accepted": False,
        "limitations": [
            "Exact ACK/service and copy bytes do not identify hidden DMA or causal JCT gains.",
            "Ready retries represent native rechecks, not capacity credits or guaranteed admission.",
            "Zero retries must remain visible and cannot explain a performance change through this repair.",
            "Legacy journal and distinct native-forward counts are separate; unknown grant identities remain unknown.",
            "The unchanged four-case classifier remains offline and adds no future trace information online.",
        ],
    }
    BASE.causal.reservation.write_json(output, result)
    print(json.dumps({
        name: result[name] for name in (
            "byte_balance", "staged_native_demand", "staged_native_service",
            "ack_restore_ready_events", "ack_native_capacity_retry_events",
            "multi_member_journal_records", "distinct_multi_member_native_forwards",
            "final_eos", "known_guards",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
