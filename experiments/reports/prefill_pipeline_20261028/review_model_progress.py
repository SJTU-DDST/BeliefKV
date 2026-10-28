"""Bind the failed model fixture's physical progress without changing its status."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ARM = ROOT / "experiments/raw/demand_prefill_model_20261028_r1/arm"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_staged_native_restore import inspect, service_evidence, transfer_key


def main():
    output = HERE / "model_progress_review.json"
    if output.exists():
        raise FileExistsError("preserve earlier physical model evidence")
    paths = {
        "journals": ARM / "opportunities/admission_opportunities.jsonl",
        "workers": ARM / "server/runtime_audit.jsonl",
        "transfers": ARM / "server/transfer_telemetry.jsonl",
    }
    seals = {name: sha256_file(path) for name, path in paths.items()}
    records = {}
    for name, path in paths.items():
        rows = []
        with path.open() as source:
            for line in source:
                if not line.endswith("\n"):
                    raise ValueError("model evidence ends in an incomplete JSONL record")
                rows.append(json.loads(line))
        records[name] = rows
    stage = inspect(records["journals"], records["transfers"], bytes_per_token=98304)
    service = service_evidence(stage["episodes"], records["journals"], records["workers"])
    if stage["counts"] != {
        "matched_staging_episodes": 1, "native_selected_requests": 1,
        "native_full_tokens": 20487, "native_h2d_bytes": 2013954048,
    } or service["counts"] != {"members_with_subsequent_native_service": 1}:
        raise ValueError("the original model physical cohort changed")
    episode = stage["episodes"][0]
    key = episode["native_submit_ts_ms"], tuple(episode["native_node_ids"]), episode["native_h2d_bytes"]
    accepted = []
    for row in records["journals"]:
        if row.get("event") != "native_demand_restore_decode_completed_pending":
            continue
        if transfer_key({
            "submit_ts_ms": row["native_submit_ts_ms"], "node_ids": row["native_node_ids"],
            "actual_bytes": row["native_bytes"],
        }) != key or row["monotonic_clock_domain"] != episode["monotonic_clock_domain"]:
            raise ValueError("model pending completion belongs to a foreign transfer")
        if row.get("ack_pending_at_completed_batch_observation") is not True:
            raise ValueError("model pending completion lacks its native ACK observation")
        if row["batch_launch_monotonic_ms"] < episode["staged_monotonic_ms"]:
            continue
        if row["completion_observed_monotonic_ms"] < row["batch_launch_monotonic_ms"]:
            raise ValueError("model completion precedes its native batch launch")
        members = [
            member for member in row["completed_decode_requests"]
            if member["request_id"] == "demand-model:a_resident" and member["output_tokens"] > 0
        ]
        workers = [
            worker for worker in records["workers"]
            if worker.get("event") == "gpu_service_sample" and worker.get("phase") == "decode"
            and worker.get("forward_iter") == row["forward_iter"]
            and worker.get("monotonic_clock_domain") == episode["monotonic_clock_domain"]
            and len(members) == 1 and any(
                all(sample.get(field) == members[0][field] for field in (
                    "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch",
                )) and sample.get("token_delta", 0) > 0
                for sample in worker.get("request_samples", ())
            )
        ]
        if len(workers) != 1:
            raise ValueError("completed resident model result lacks its exact native worker")
        accepted.append({
            "forward_iter": row["forward_iter"], "output_tokens": members[0]["output_tokens"],
            "launch_monotonic_ms": row["batch_launch_monotonic_ms"],
            "result_processed_monotonic_ms": row["completion_observed_monotonic_ms"],
        })
    if len(accepted) != 11 or len({row["forward_iter"] for row in accepted}) != 11:
        raise ValueError("the original positive model progress cohort changed")
    if any(sha256_file(paths[name]) != digest for name, digest in seals.items()):
        raise ValueError("model physical inputs changed during review")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "staged_native_ack": stage, "subsequent_native_prefill": service,
        "resident_completed_batches_while_ack_pending": accepted,
        "input_sha256": {str(paths[name]): digest for name, digest in seals.items()},
        "operator_sha256": sha256_file(Path(__file__)),
        "original_model_remains_failed": True, "model_qualified": False,
        "performance_accepted": False,
        "scope": "Actual normal H2D and processed model results; no JCT gain or pure kernel time.",
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("Verified exact native H2D, later prefill and 11 completed resident model batches.")


if __name__ == "__main__":
    main()
