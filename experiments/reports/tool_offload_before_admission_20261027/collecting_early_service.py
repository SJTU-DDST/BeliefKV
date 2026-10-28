"""Associate observed early releases with native request service, not saved time."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_join_reentry_service import identity
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve the original early-release service prefix")
    registration = Path("/home/longhao/experiment/ops/tool_offload_before_admission_20261027_r1.registration.json")
    control_path = Path("/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json")
    plan, control = causal.load(registration), causal.load(control_path)
    causal.comparison.verify_sources(plan)
    arm = Path(plan["run_root"]) / "full"
    unit = plan["source_capacity"]["host_full_bytes"] // plan["source_capacity"]["host_full_tokens"]
    reader = SealedRecords()
    releases = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "predicted_tool_kv_evicted"
        and row.get("dispatch_stage") == "before_native_admission"
    ]
    if not releases:
        raise ValueError("no actual early release is observed")
    expected = causal.receipt_work(
        Path(control["run_root"]) / "thunderagent_acting_decay/replay/native_requests.jsonl",
        sampling_mode="position",
    )
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    completed = validate_receipts(receipts, expected)
    receipts = {row["request_id"]: row for row in receipts}
    submits = {}
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        if row.get("kind") == "llm_submit":
            rid = row["attributes"]["request_id"]
            if rid in submits:
                raise ValueError("ambiguous native submission")
            submits[rid] = row
    consumers = {}
    for release in releases:
        rid = release["beneficiary_request_id"]
        if (
            release["evidence"] != "current_prediction;settled_host;native_unlocked_private_leaf"
            or type(release["freed_bytes"]) is not int or release["freed_bytes"] <= 0
            or release["freed_units"] != {"0": release["freed_bytes"] // unit}
            or release["freed_bytes"] % unit
        ):
            raise ValueError("early release bytes or native evidence do not reconcile")
        row = consumers.setdefault(rid, {
            "request_id": rid, "context_id": release["beneficiary_context_id"],
            "release_records": 0, "cumulative_associated_released_bytes": 0,
            "first_release_ts_ms": release["ts_ms"], "last_release_ts_ms": release["ts_ms"],
            "first_native_service_ts_ms": None, "status": "completion_or_service_unproven",
        })
        if row["context_id"] != release["beneficiary_context_id"]:
            raise ValueError("beneficiary context changed within one request")
        row["release_records"] += 1
        row["cumulative_associated_released_bytes"] += release["freed_bytes"]
        row["first_release_ts_ms"] = min(row["first_release_ts_ms"], release["ts_ms"])
        row["last_release_ts_ms"] = max(row["last_release_ts_ms"], release["ts_ms"])
    for batch in reader(arm / "server/runtime_audit.jsonl"):
        if batch.get("event") != "gpu_service_sample":
            continue
        for sample in batch.get("request_samples", ()):
            rid = sample.get("request_id")
            row, submit = consumers.get(rid), submits.get(rid)
            if (
                row is None or submit is None or identity(submit) is None
                or identity(sample) != identity(submit)
            ):
                continue
            if (
                submit["context_id"] != row["context_id"]
                or submit["ts_ms"] > row["first_release_ts_ms"]
            ):
                raise ValueError("early release does not bind to an already queued consumer")
            previous, started = row["first_native_service_ts_ms"], batch["service_start_ts_ms"]
            row["first_native_service_ts_ms"] = started if previous is None else min(previous, started)
            row["native_service_identity"] = list(identity(sample))
            row["native_service_clock_domain"] = batch["monotonic_clock_domain"]
    for rid, row in consumers.items():
        receipt, submit = receipts.get(rid), submits.get(rid)
        native = (receipt or {}).get("native_receipt") or {}
        started = row["first_native_service_ts_ms"]
        if (
            receipt is None or submit is None or started is None
            or native.get("request_id") != rid
            or native.get("identity") != row["native_service_identity"]
            or native.get("clock_domain") != row["native_service_clock_domain"]
            or type(native.get("session_id")) is not str or not native["session_id"]
            or type(native.get("session_generation")) is not int or native["session_generation"] < 0
        ):
            continue
        if started < row["last_release_ts_ms"]:
            raise ValueError("a claimed pre-admission release followed actual first service")
        row.update(
            status="completed_request_native_service_after_early_release",
            last_release_to_native_run_batch_entry_ms=started - row["last_release_ts_ms"],
            native_session_id=native.get("session_id"),
            native_session_generation=native.get("session_generation"),
            native_host_hit_tokens=submit["attributes"].get("cached_tokens_host"),
            attempt_identity_at_release_verified=False,
            freed_page_allocation_to_consumer_verified=False,
        )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "arm": str(arm),
        "exact_completed_receipts": completed,
        "summary": {
            "early_node_release_events": len(releases),
            "early_node_released_bytes": sum(row["freed_bytes"] for row in releases),
            "distinct_consumer_requests": len(consumers),
            "consumer_statuses": dict(Counter(row["status"] for row in consumers.values())),
        },
        "consumers": list(consumers.values()), "releases": releases,
        "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "complete_work_verified": False,
        "limitations": [
            "Each JSONL is independently sealed; this is not an atomic cross-file snapshot.",
            "The action names a unique request and context; it does not log its full epoch/session/attempt key.",
            "Native service requires exact completed receipt identity and native clock domain, but does not prove the release-time attempt.",
            "No freed physical index list binds those exact pages to the consumer; released bytes are cumulative associations.",
            "Run-batch entry is not CUDA kernel start; no transfer time hidden or removable JCT is inferred.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
