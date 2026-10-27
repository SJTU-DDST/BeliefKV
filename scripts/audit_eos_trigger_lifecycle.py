#!/usr/bin/env python3
"""Classify observed final-EOS triggers against actual replay lifecycles."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def classify(triggers, receipts, events, *, opener_ids, closer_ids):
    if not opener_ids or not closer_ids:
        raise ValueError("actual tool delimiter vocabulary is required")
    requested = {row["child_request_id"] for row in triggers}
    completed = {}
    for receipt in receipts:
        request = receipt["request_id"]
        if request not in requested:
            continue
        if request in completed:
            raise ValueError("duplicate triggered-request completion")
        native = receipt.get("native_receipt") or {}
        identity, tokens = native.get("identity"), native.get("output_token_ids")
        if (
            receipt.get("exact_tokens_verified") is not True
            or native.get("request_id") != request
            or type(identity) is not list or len(identity) != 4
            or type(identity[3]) is not int
            or native.get("native_finish_reason", {}).get("type") != "stop"
            or type(tokens) is not list or len(tokens) != receipt.get("output_tokens")
        ):
            raise ValueError("EOS trigger requires an exact native stopped response")
        completed[request] = receipt
    submissions, last_result, final_result = defaultdict(list), {}, {}
    for event in events:
        owner = event["workflow_id"], event.get("invocation_id")
        attrs = event.get("attributes") or {}
        if attrs.get("runtime_internal"):
            continue
        if event["kind"] == "llm_submit":
            submissions[owner].append((
                event["context_id"], event["context_epoch"], attrs["request_id"],
            ))
        elif event["kind"] == "llm_result":
            last_result[owner] = attrs["request_id"]
        elif event["kind"] == "return" and attrs.get("outcome") == "completed":
            final_result[owner] = last_result.get(owner)
    rows = []
    for trigger in triggers:
        request = trigger["child_request_id"]
        owner = trigger["workflow_id"], trigger["child_invocation_id"]
        row = {
            "child_request_id": request, "workflow_id": owner[0],
            "child_invocation_id": owner[1], "join_id": trigger["join_id"],
            "trigger_ts_ms": trigger["ts_ms"], "status": "completion_unobserved",
            "native_tool_delimiter_evidence": "unknown",
        }
        receipt = completed.get(request)
        if receipt is not None:
            native = receipt["native_receipt"]
            identity, tokens = native["identity"], native["output_token_ids"]
            if tuple(identity[:2]) != owner:
                raise ValueError("EOS trigger changed native workflow or invocation")
            following = sorted(
                (epoch, rid) for context, epoch, rid in submissions[owner]
                if context == identity[2] and epoch > identity[3]
            )
            opener = any(token in opener_ids for token in tokens)
            closer = any(token in closer_ids for token in tokens)
            row.update(
                identity=identity, output_sha256=receipt["output_sha256"],
                output_tokens=receipt["output_tokens"],
                status=(
                    "intermediate_request_observed" if following else
                    "final_request_observed" if final_result.get(owner) == request else
                    "later_lifecycle_unresolved"
                ),
                subsequent_model_requests=len(following),
                next_observed_request_id=following[0][1] if following else None,
                actual_final_request_id=final_result.get(owner),
                native_tool_delimiter_evidence=(
                    "opener_present" if opener else
                    "closer_without_opener" if closer else "no_dedicated_delimiter"
                ),
            )
        rows.append(row)
    return {
        "summary": {
            "trigger_records": len(rows),
            "lifecycle_statuses": dict(Counter(row["status"] for row in rows)),
            "delimiter_evidence": dict(Counter(row["native_tool_delimiter_evidence"] for row in rows)),
            "all_triggered_requests_observed_final": bool(rows) and all(
                row["status"] == "final_request_observed" for row in rows
            ),
        },
        "rows": rows,
    }


def audit(registration, expected_receipts):
    plan = causal.load(registration)
    causal.comparison.verify_sources(plan)
    if "full" not in plan["order"]:
        raise ValueError("EOS trigger audit requires a declared Full arm")
    arm = Path(plan["run_root"]) / "full"
    tokenizer = Path(plan["source_server_parameters"]["model_path"]) / "tokenizer_config.json"
    vocabulary = causal.load(tokenizer)["added_tokens_decoder"]
    openers = {
        int(key) for key, value in vocabulary.items()
        if value["content"] in ("<tool_call>", "<|tool_call|>")
    }
    closers = {int(key) for key, value in vocabulary.items() if value["content"] == "</tool_call>"}
    sealed = SealedRecords()
    receipts = list(sealed(arm / "replay/native_requests.jsonl"))
    expected = causal.receipt_work(expected_receipts, sampling_mode="position")
    if len(expected) != plan["source_plan"]["requests"]:
        raise ValueError("expected receipt population differs from registered work")
    count = validate_receipts(receipts, expected)
    triggers = [
        row for row in sealed(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "final_stage_latest_start"
        and row.get("trigger_kind") == "observed_no_tool_eos"
    ]
    result = classify(
        triggers, receipts, sealed(arm / "replay/runtime_events.replay.jsonl"),
        opener_ids=openers, closer_ids=closers,
    )
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "offline observed trigger lifecycle classification on sealed prefixes",
        "registration": str(registration.resolve()), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "exact_completed_receipts": count,
        "expected_receipts": {
            "path": str(expected_receipts.resolve()), "sha256": sha256_file(expected_receipts),
        },
        "tokenizer_config_sha256": sha256_file(tokenizer),
        "opener_token_ids": sorted(openers), "closer_token_ids": sorted(closers),
        **result, "input_jsonl_prefixes": sealed.prefixes,
        "auditor_sha256": sha256_file(Path(__file__)),
        "future_trace_information_used_online": False,
        "complete_work_verified": False, "performance_accepted": False,
        "limitations": [
            "Unobserved successors do not prove finality; an actual completed RETURN is required.",
            "Subsequent requests and RETURN only label recorded triggers offline.",
            "A tool delimiter is conservative negative evidence, not full parser equivalence.",
            "The audit does not establish missed prefetches, hidden transfer time or performance gains.",
            "Independently sealed prefixes are not an atomic cross-file snapshot.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--expected-receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous EOS lifecycle evidence")
    result = audit(args.registration, args.expected_receipts)
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
