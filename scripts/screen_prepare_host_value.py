"""Screen optional native backups using recorded lifecycle and transfer receipts."""

import argparse
from collections import defaultdict
import gzip
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def category(row):
    if row["ack_evidence"] != "reconciled_full_batch":
        return "unresolved_ACK"
    if row["capacity_releases"] or row["restore_associations"]:
        return "observed_release_or_restore"
    ended = {item["node_id"] for item in row["copy_end_observations"]}
    return (
        "ended_without_observed_release_or_restore"
        if {node[0] for node in row["native_nodes"]} <= ended
        else "right_censored_without_observed_release_or_restore"
    )


def summarize(rows, transfers):
    receipts = {}
    for transfer in transfers:
        command = transfer["command_id"]
        if command in receipts:
            raise ValueError("duplicate native transfer command")
        receipts[command] = transfer
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["source"], category(row)].append(row)
    result = {}
    for (source, classification), members in sorted(grouped.items()):
        stream, ack, first_release = [], [], []
        for row in members:
            if row["ack_evidence"] == "reconciled_full_batch":
                receipt = receipts.get(row.get("ack_transfer_command_id"))
                if receipt is None or (
                    receipt.get("direction") != "d2h" or receipt.get("status") != "completed"
                    or receipt.get("actual_bytes") != row["acknowledged_full_bytes"]
                    or receipt.get("complete_ts_ms") != row["ack_ts_ms"]
                ):
                    raise ValueError("backup lifecycle does not match its completed D2H receipt")
                if receipt.get("transfer_stream_elapsed_ms") is not None:
                    stream.append(receipt["transfer_stream_elapsed_ms"])
                if receipt.get("submit_to_ack_ms") is not None:
                    ack.append(receipt["submit_to_ack_ms"])
                if row["capacity_releases"]:
                    first_release.append(
                        min(item["ts_ms"] for item in row["capacity_releases"]) - row["ack_ts_ms"]
                    )
        result.setdefault(source, {})[classification] = {
            "batches": len(members),
            "acknowledged_bytes": sum(row["acknowledged_full_bytes"] or 0 for row in members),
            "nodes": sum(len(row["native_nodes"]) for row in members),
            "batches_with_observed_release": sum(bool(row["capacity_releases"]) for row in members),
            "batches_with_restore_association": sum(bool(row["restore_associations"]) for row in members),
            "stream_service_ms": distribution(stream),
            "submit_to_ACK_ms": distribution(ack),
            "ACK_to_first_observed_release_ms": distribution(first_release),
        }
    return {
        "by_source_and_lifecycle": result,
        "scope": (
            "Command-level associations before observed Host eviction, later D2H "
            "or node-generation change. No observed benefit does not prove wasted "
            "bytes: native splits, physical lineage and native forward use can be "
            "unobserved. Partially ended batches remain censored. Stream-service "
            "sums are transfer work, not exposed compute delay or a JCT bound. "
            "Submit-to-ACK is an envelope, not an anchored DMA interval."
        ),
        "GPU_action": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or args.output.exists()
    ):
        parser.error("preserve earlier reports and use nice19 hidden-CUDA analysis")
    digest = sha256_file(args.lifecycle)
    report = json.loads(gzip.decompress(args.lifecycle.read_bytes()))
    path = Path(report["arm"]) / "server/transfer_telemetry.jsonl"
    reader = SealedRecords()
    reader.prefixes[str(path)] = report["input_jsonl_prefixes"][str(path)]
    result = summarize(report["native_full_prepare_attribution"]["rows"], list(reader(path)))
    result.update(
        arm=report["arm"], lifecycle=str(args.lifecycle.resolve()),
        lifecycle_sha256=digest, transfer_prefix=reader.prefixes[str(path)],
        operator_sha256=sha256_file(Path(__file__)),
        semantic_prepare_lifetime=report["summary"]["prepare_host_lifetime"],
    )
    if sha256_file(args.lifecycle) != digest:
        raise ValueError("lifecycle report changed during analysis")
    write(args.output, result)
    print(json.dumps({"by_source_and_lifecycle": result["by_source_and_lifecycle"], "GPU_action": False}))


if __name__ == "__main__":
    main()
