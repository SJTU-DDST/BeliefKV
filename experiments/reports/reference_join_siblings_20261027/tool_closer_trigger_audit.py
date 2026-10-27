"""Classify observed EOS triggers using completed output and later replay evidence."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve observed EOS trigger classification")
    registration = Path("/home/longhao/experiment/ops/reference_join_siblings_20261027_r1.registration.json")
    plan = json.loads(registration.read_bytes())
    arm = Path(plan["run_root"]) / "full"
    tokenizer = Path(plan["source_server_parameters"]["model_path"]) / "tokenizer_config.json"
    vocabulary = json.loads(tokenizer.read_bytes())["added_tokens_decoder"]
    openers = {
        int(key) for key, value in vocabulary.items()
        if value["content"] in ("<tool_call>", "<|tool_call|>")
    }
    closers = {int(key) for key, value in vocabulary.items() if value["content"] == "</tool_call>"}
    reader = SealedRecords()
    triggers = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "final_stage_latest_start"
        and row.get("trigger_kind") == "observed_no_tool_eos"
    ]
    requests = {row["child_request_id"] for row in triggers}
    completed = {}
    for receipt in reader(arm / "replay/native_requests.jsonl"):
        if receipt["request_id"] not in requests:
            continue
        if receipt.get("exact_tokens_verified") is not True or receipt["request_id"] in completed:
            raise ValueError("trigger receipt is not exact and unique")
        native = receipt["native_receipt"]
        tokens = native["output_token_ids"]
        completed[receipt["request_id"]] = {
            "identity": native["identity"], "output_sha256": receipt["output_sha256"],
            "output_tokens": len(tokens), "has_opener": any(token in openers for token in tokens),
            "has_closer": any(token in closers for token in tokens),
            "finish_type": native["native_finish_reason"]["type"],
        }
    submissions, last_result, final_result = defaultdict(list), {}, {}
    for event in reader(arm / "replay/runtime_events.replay.jsonl"):
        owner = event["workflow_id"], event.get("invocation_id")
        attrs = event.get("attributes") or {}
        if event["kind"] == "llm_submit" and not attrs.get("runtime_internal"):
            submissions[owner].append({
                "request_id": attrs["request_id"], "context_id": event["context_id"],
                "context_epoch": event["context_epoch"],
            })
        elif event["kind"] == "llm_result" and not attrs.get("runtime_internal"):
            last_result[owner] = attrs["request_id"]
        elif event["kind"] == "return" and attrs.get("outcome") == "completed":
            final_result[owner] = last_result.get(owner)
    rows = []
    for trigger in triggers:
        rid = trigger["child_request_id"]
        owner = trigger["workflow_id"], trigger["child_invocation_id"]
        observed = completed.get(rid)
        row = {
            "child_request_id": rid, "join_id": trigger["join_id"],
            "workflow_id": owner[0], "child_invocation_id": owner[1],
            "trigger_ts_ms": trigger["ts_ms"], "status": "completion_unobserved",
            "native_tool_delimiter_evidence": "unknown",
        }
        if observed is not None:
            if tuple(observed["identity"][:2]) != owner or observed["finish_type"] != "stop":
                raise ValueError("EOS trigger changed child or native stop identity")
            following = [
                item["request_id"] for item in submissions[owner]
                if item["context_id"] == observed["identity"][2]
                and item["context_epoch"] > observed["identity"][3]
            ]
            status = (
                "intermediate_request_observed" if following else
                "final_request_observed" if final_result.get(owner) == rid else
                "later_lifecycle_unresolved"
            )
            row.update(
                status=status, subsequent_model_requests=len(following),
                next_observed_request_id=following[0] if following else None,
                actual_final_request_id=final_result.get(owner),
                native_tool_delimiter_evidence=(
                    "opener_present" if observed["has_opener"] else
                    "closer_without_opener" if observed["has_closer"] else "no_dedicated_delimiter"
                ),
                **observed,
            )
        rows.append(row)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "offline observed EOS trigger classification; collecting prefixes only",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "collection_code_commit": plan["code_commit"],
        "tokenizer_config_sha256": sha256_file(tokenizer),
        "opener_token_ids": sorted(openers), "closer_token_ids": sorted(closers),
        "summary": {
            "trigger_records": len(rows),
            "lifecycle_statuses": dict(Counter(row["status"] for row in rows)),
            "delimiter_evidence": dict(Counter(row["native_tool_delimiter_evidence"] for row in rows)),
        },
        "rows": rows, "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "future_trace_information_used_online": False,
        "complete_work_verified": False, "performance_accepted": False,
        "limitations": [
            "Later requests and RETURN are offline labels, never scheduler features.",
            "No cross-process wall/monotonic timestamp subtraction is used.",
            "Absent later requests do not prove a final response without an observed completed RETURN.",
            "A tool delimiter is conservative negative evidence, not full tool-parser equivalence.",
            "Request hashes and native output were already verified by replay; this is not another whole-work comparison.",
            "No saved migration, removed JCT or throughput effect is inferred.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
