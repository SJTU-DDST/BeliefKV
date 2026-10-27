#!/usr/bin/env python3
"""Verify known tool/EOS counterexamples after their native observation window."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_native_system_costs import finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def inspect(cases, receipts, opportunities, *, opener_ids, closer_ids, clock_domain):
    if not cases or not opener_ids or not closer_ids:
        raise ValueError("known cases and actual native tool delimiters are required")
    targets = {row["child_request_id"]: row for row in cases}
    if len(targets) != len(cases):
        raise ValueError("known counterexamples repeat a request")
    following = {row.get("next_observed_request_id") for row in cases}
    if any(type(request) is not str or not request for request in following):
        raise ValueError("known intermediate responses require a following request")
    completed, triggers = {}, defaultdict(list)
    latest = None
    for row in receipts:
        request = row["request_id"]
        if request not in targets and request not in following:
            continue
        if request in completed:
            raise ValueError("duplicate counterexample completion")
        completed[request] = row
    for row in opportunities:
        if row.get("event") == "admission_runtime_state":
            if row.get("physical_disabled") is not False:
                raise ValueError("native physical control is unhealthy")
            if not finite_number(row.get("monotonic_ms")):
                raise ValueError("invalid native runtime frontier")
            if latest is not None and row["monotonic_ms"] <= latest["monotonic_ms"]:
                raise ValueError("native runtime frontier is not increasing")
            latest = row
        elif row.get("event") == "final_stage_latest_start" and (
            row.get("child_request_id") in targets
            and row.get("trigger_kind") == "observed_no_tool_eos"
        ):
            triggers[row["child_request_id"]].append(row)
    results = []
    for request, case in targets.items():
        row = {
            "request_id": request, "identity": case["identity"],
            "status": "request_not_completed_in_prefix",
            "observed_final_eos_trigger_count": len(triggers[request]),
            "trigger_records": triggers[request],
        }
        receipt = completed.get(request)
        if receipt is not None:
            native = receipt.get("native_receipt") or {}
            tokens = native.get("output_token_ids")
            if (
                receipt.get("exact_tokens_verified") is not True
                or receipt.get("output_sha256") != case["output_sha256"]
                or receipt.get("output_tokens") != case["output_tokens"]
                or native.get("request_id") != request or native.get("identity") != case["identity"]
                or native.get("native_finish_reason", {}).get("type") != "stop"
                or type(tokens) is not list or len(tokens) != case["output_tokens"]
                or any(token in opener_ids for token in tokens)
                or not any(token in closer_ids for token in tokens)
            ):
                raise ValueError("counterexample output, native identity or stop changed")
            completed_at = native.get("complete_monotonic_ms")
            window = latest.get("eos_prefetch_evidence_window_ms") if latest else None
            row.update(
                status="awaiting_following_request_completion",
                native_complete_monotonic_ms=completed_at,
                runtime_frontier_monotonic_ms=latest["monotonic_ms"] if latest else None,
                eos_evidence_window_ms=window,
                native_clock_domain=native.get("clock_domain"),
                output_sha256=receipt["output_sha256"],
            )
            if triggers[request]:
                row["status"] = "false_final_eos_trigger_recorded"
            elif clock_domain is None or native.get("clock_domain") != clock_domain:
                row["status"] = "native_clock_domain_unproven"
            elif not finite_number(completed_at) or completed_at < 0:
                raise ValueError("invalid native completion clock")
            else:
                successor = completed.get(case["next_observed_request_id"])
                if successor is not None:
                    next_native = successor.get("native_receipt") or {}
                    next_identity = next_native.get("identity")
                    next_at = next_native.get("complete_monotonic_ms")
                    if (
                        successor.get("exact_tokens_verified") is not True
                        or next_native.get("request_id") != case["next_observed_request_id"]
                        or type(next_identity) is not list or len(next_identity) != 4
                        or next_identity[:3] != case["identity"][:3]
                        or type(next_identity[3]) is not int or next_identity[3] <= case["identity"][3]
                    ):
                        raise ValueError("following completion changed native context identity")
                    if next_native.get("clock_domain") != clock_domain:
                        row["status"] = "native_clock_domain_unproven"
                    elif not finite_number(next_at) or next_at < completed_at:
                        raise ValueError("following native completion clock is invalid")
                    else:
                        row.update(
                            status="awaiting_later_runtime_frontier",
                            following_request_id=case["next_observed_request_id"],
                            following_native_complete_monotonic_ms=next_at,
                        )
                        if (
                            latest is not None and finite_number(window) and window > 0
                            and latest["monotonic_ms"] > next_at + window
                        ):
                            row["status"] = "completed_tool_response_without_final_eos_trigger"
        results.append(row)
    return {
        "summary": {
            "known_cases": len(cases),
            "statuses": dict(Counter(row["status"] for row in results)),
            "all_cases_verified": all(
                row["status"] == "completed_tool_response_without_final_eos_trigger"
                for row in results
            ),
        },
        "cases": results,
    }


def audit(registration, case_report):
    plan, prior = causal.load(registration), causal.load(case_report)
    previous = causal.load(Path(prior["registration"]))
    causal.comparison.verify_sources(plan)
    common = (
        "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
        "workflow_ids", "quality_exclusions", "quality_exclusion_details", "engine_tree",
        "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
        "request_timeout_s", "max_running_requests",
    )
    if any(plan.get(name) != previous.get(name) for name in common):
        raise ValueError("guard audit changed common work, timing or serving parameters")
    cases = [
        row for row in prior["rows"]
        if row["status"] == "intermediate_request_observed"
        and row["native_tool_delimiter_evidence"] == "closer_without_opener"
    ]
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    # A completed successor proves the old result callback has run. A later
    # FIFO runtime snapshot settles its EOS window and queued observations.
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    expected = causal.receipt_work(
        Path(previous["run_root"]) / "full/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    count = validate_receipts(receipts, expected)
    domain = local_monotonic_clock_domain()
    result = inspect(
        cases, receipts, reader(arm / "opportunities/admission_opportunities.jsonl"),
        opener_ids=set(prior["opener_token_ids"]), closer_ids=set(prior["closer_token_ids"]),
        clock_domain=domain,
    )
    tokenizer = Path(plan["source_server_parameters"]["model_path"]) / "tokenizer_config.json"
    if sha256_file(tokenizer) != prior["tokenizer_config_sha256"]:
        raise ValueError("actual serving tool-delimiter vocabulary changed")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "completed known counterexamples and sealed native frontier; not complete performance",
        "registration": str(registration.resolve()), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "case_report": str(case_report.resolve()),
        "case_report_sha256": sha256_file(case_report),
        "same_common_fields_as_prior": list(common), "exact_completed_receipts": count,
        "reader_monotonic_clock_domain": domain, **result,
        "input_jsonl_prefixes": reader.prefixes, "auditor_sha256": sha256_file(Path(__file__)),
        "complete_work_verified": False, "performance_accepted": False,
        "semantics": [
            "Known cases are offline regression targets, not online future-request or RETURN features.",
            "Every completed receipt in the sealed prefix matches the prior complete work and native stopping.",
            "A missing request remains unobserved; an absent trigger alone does not pass a case.",
            "A completed same-context successor bounds the old result callback; a later snapshot beyond its EOS window settles same-writer FIFO observations.",
            "Monotonic comparisons require the same Linux boot/time-namespace clock domain.",
            "This verifies the known delimiter guard, not all tool formats or an end-to-end speedup.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--case-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous guard-case evidence")
    result = audit(args.registration, args.case_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "exact_completed_receipts": result["exact_completed_receipts"],
        **result["summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
