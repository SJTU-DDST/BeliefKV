"""Audit the completed R2 with the unchanged demand, sibling and EOS classifiers."""

from collections import defaultdict
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "repeat_r2_complete"
sys.path.insert(0, str(ROOT))


def helper(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REVIEW = helper(HERE / "repeat_complete_review.py", "repeat_mechanism_complete")
BASE, REPEAT = REVIEW.BASE, REVIEW.REPEAT

from scripts.audit_demand_prefill_pipeline import audit as demand_audit
from scripts.audit_eos_trigger_lifecycle import audit as eos_audit
from scripts.audit_reference_join_grants import audit as grant_audit
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_sibling_native_service import inspect as sibling_inspect
from scripts import audit_tool_eos_guard_cases as guard


def main():
    complete_path = OUTPUT / "complete_review.json"
    detail_path = OUTPUT / "mechanism_detail.json.gz"
    output = OUTPUT / "mechanism_review.json"
    if output.exists() or detail_path.exists():
        raise FileExistsError("preserve original repeat mechanism evidence")
    complete = BASE.causal.load(complete_path)
    if any(complete.get(name) is not True for name in (
        "exact_work_and_original_stopping_verified", "all_normal_tails_retained",
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified",
    )) or complete["main_admission_retry_fix_included"] is not False:
        raise ValueError("repeat mechanisms require complete unchanged R2 and owned exit")
    for source, digest in complete["inputs_sha256"].items():
        if BASE.sha256_file(Path(source)) != digest:
            raise ValueError("complete repeated performance evidence changed")
    first, second = (BASE.causal.load(path) for path in (REPEAT.PRIOR, REPEAT.TARGET))
    REVIEW.repeat_identity(first, second)
    BASE.completed(second, REPEAT.TARGET, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    arm = Path(second["run_root"]) / "full"
    prefetch_path = OUTPUT / "complete_wait_prefetch.json.gz"
    warm_path = OUTPUT / "complete_wait_prefetch_summary.json"
    warm = BASE.causal.load(warm_path)
    if warm["collection_code_commit"] != second["code_commit"] or warm["summary"]["tool_commands"]:
        raise ValueError("repeat prefetch evidence has another source or tool prefetch")
    demand = demand_audit(REPEAT.TARGET, "full")
    if (
        not demand["byte_balance"]["exact_physical_bytes_reconciled"]
        or not demand["staged_native_service"][
            "all_members_have_native_service_or_explicit_cancellation"
        ]
        or not demand["staged_native_service"]["native_completion_receipts_required"]
    ):
        raise ValueError("repeat native demand lacks exact ACK, bytes or subsequent service")
    grants = grant_audit(arm, prefetch_report=prefetch_path)
    reader = SealedRecords()
    sibling = sibling_inspect(*(
        reader(arm / relative) for relative in (
            "opportunities/admission_opportunities.jsonl", "replay/runtime_events.replay.jsonl",
            "replay/native_requests.jsonl", "server/runtime_audit.jsonl",
        )
    ))
    sibling["input_jsonl_prefixes"] = reader.prefixes
    multi = [
        row for row in sibling["records"]
        if row["status"] == "native_batch_join_and_completed_sessions_verified"
        and row["members"] >= 2
    ]
    if len(multi) != sibling["summary"]["verified_multi_member_batches"]:
        raise ValueError("legacy multi-member journal count does not reconcile")
    forwards = defaultdict(list)
    for row in multi:
        if type(row["forward_iter"]) is not int or row["phase"] != "prefill":
            raise ValueError("multi-member journal lacks an actual native forward identity")
        forwards[(row["forward_iter"], row["worker_sample_id"])].append(row["join_id"])
    expected = Path(first["run_root"]) / "full/replay/native_requests.jsonl"
    eos = eos_audit(REPEAT.TARGET, expected)
    case_path = ROOT / (
        "experiments/reports/reference_join_siblings_20261027/tool_closer_trigger_audit.json"
    )
    cases = BASE.causal.load(case_path)
    original_guard = BASE.causal.load(HERE / "complete_guard_cases.json")
    if (
        original_guard["summary"]["all_cases_verified"] is not True
        or original_guard["inputs_sha256"][str(case_path)] != BASE.sha256_file(case_path)
    ):
        raise ValueError("original reviewed known cases changed")
    case_rows = [
        row for row in cases["rows"]
        if row["status"] == "intermediate_request_observed"
        and row["native_tool_delimiter_evidence"] == "closer_without_opener"
    ]
    guard_reader = SealedRecords()
    receipts = list(guard_reader(arm / "replay/native_requests.jsonl"))
    count = guard.validate_receipts(receipts, BASE.causal.receipt_work(
        expected, sampling_mode="position",
    ))
    known = guard.inspect(
        case_rows, receipts,
        guard_reader(arm / "opportunities/admission_opportunities.jsonl"),
        opener_ids=set(cases["opener_token_ids"]), closer_ids=set(cases["closer_token_ids"]),
        clock_domain=guard.local_monotonic_clock_domain(),
    )
    known["input_jsonl_prefixes"] = guard_reader.prefixes
    tokenizer = Path(second["source_server_parameters"]["model_path"]) / "tokenizer_config.json"
    if (
        count != 3674 or len(case_rows) != 4 or not known["summary"]["all_cases_verified"]
        or not eos["summary"]["all_triggered_requests_observed_final"]
        or BASE.sha256_file(tokenizer) != cases["tokenizer_config_sha256"]
    ):
        raise ValueError("repeat final EOS, known guards or tokenizer failed")
    inputs = {
        str(path): BASE.sha256_file(path) for path in (
            Path(__file__), HERE / "repeat_complete_review.py", complete_path,
            REPEAT.PRIOR, REPEAT.TARGET, prefetch_path, warm_path, case_path,
            HERE / "complete_guard_cases.json", expected, tokenizer,
            ROOT / "scripts/audit_demand_prefill_pipeline.py",
            ROOT / "scripts/audit_staged_native_restore.py",
            ROOT / "scripts/audit_reference_join_grants.py",
            ROOT / "scripts/audit_sibling_native_service.py",
            ROOT / "scripts/audit_eos_trigger_lifecycle.py", Path(guard.__file__),
        )
    }
    for evidence in (
        warm, demand, demand["native_legacy_accounting_scope"], grants, sibling, eos, known,
    ):
        for source, seal in evidence["input_jsonl_prefixes"].items():
            if Path(source).stat().st_size != seal["complete_prefix_bytes"] or (
                BASE.sha256_file(Path(source)) != seal["complete_prefix_sha256"]
            ):
                raise ValueError("complete mechanism input is not stable: " + source)
            inputs[source] = seal["complete_prefix_sha256"]
    detail = {
        "demand": demand, "join_grants": grants, "sibling_service": sibling,
        "final_eos": eos, "known_guards": known,
        "unique_multi_member_forwards": [
            {"forward_iter": key[0], "worker_sample_id": key[1], "join_ids": value}
            for key, value in sorted(forwards.items())
        ],
    }
    raw = json.dumps(detail, indent=2, allow_nan=False).encode() + b"\n"
    with detail_path.open("xb") as stream:
        stream.write(gzip.compress(raw, mtime=0))
    if gzip.decompress(detail_path.read_bytes()) != raw:
        raise ValueError("complete mechanism archive differs from generated evidence")
    inputs[str(detail_path)] = BASE.sha256_file(detail_path)
    native = demand["native_legacy_accounting_scope"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete R2 exact native demand/service, JOIN, sibling and EOS evidence.",
        "exact_completed_receipts": count, "all_normal_tails_retained": True,
        "byte_balance": demand["byte_balance"],
        "staged_native_demand": demand["staged_native_demand"]["counts"],
        "staged_native_service": demand["staged_native_service"]["counts"],
        "native_transfers": native["transfers"],
        "handoff_events": native["handoff_events"],
        "handoff_consumer_h2d_bytes": native["handoff_consumer_h2d_bytes"],
        "join_prefetch_summary": warm["summary"],
        "join_grants": grants["summary"], "sibling_service": sibling["summary"],
        "multi_member_journal_records": len(multi),
        "distinct_multi_member_native_forwards": len(forwards),
        "final_eos": eos["summary"], "known_guards": known["summary"],
        "production_guard_or_classifier_changed": False,
        "main_admission_retry_fix_included": False,
        "inputs_sha256": inputs, "performance_accepted": False,
        "limitations": [
            "Exact ACK and subsequent service do not quantify hidden DMA or causal JCT gains.",
            "Staged native demand remains distinct from speculative prefetch and request Host hits.",
            "Legacy multi-member journal records and distinct native forwards are reported separately.",
            "Unknown grant/service associations remain unknown; normal tails are never excluded.",
            "Four original reviewed cases use unchanged classifiers with the identical R1/R2 engine.",
        ],
    }
    BASE.causal.reservation.write_json(output, result)
    print(json.dumps({
        name: result[name] for name in (
            "byte_balance", "staged_native_demand", "staged_native_service",
            "multi_member_journal_records", "distinct_multi_member_native_forwards",
            "final_eos", "known_guards",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
