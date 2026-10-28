"""Audit mirror releases against their actual native cold-backup submissions."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def inspect(records, transfers, unit):
    if type(unit) is not int or unit <= 0:
        raise ValueError("invalid native FULL bytes per token")
    releases, prepares, snapshots = [], [], []
    for position, row in enumerate(records):
        if row.get("event") == "admission_runtime_state":
            snapshots.append(row)
        elif row.get("event") == "idle_cold_full_prepare":
            prepares.append((position, row))
        elif row.get("event") == "idle_host_mirror_recycled":
            nodes = row["released_host_nodes"]
            if (
                not 1 <= len(nodes) <= 32
                or len({item[0] for item in nodes}) != len(nodes)
                or any(
                    type(node) is not int or node < 0
                    or type(created) not in (int, float) or not math.isfinite(created)
                    or type(tokens) is not int or tokens <= 0
                    for node, created, tokens in nodes
                )
                or row["released_device_tokens"] != 0
                or row["released_host_tokens"] != sum(item[2] for item in nodes)
                or row["released_host_bytes"] != row["released_host_tokens"] * unit
                or row["host_free_after"] - row["host_free_before"] != row["released_host_tokens"]
                or row["planned_backup_tokens"] <= 0
                or row["evidence"] != "current_cold_owner;device_pinned_mirror;native_host_dma_guards"
            ):
                raise ValueError("mirror release geometry or authority does not reconcile")
            releases.append((position, row))
    transfers = [
        row for row in transfers
        if row.get("event") == "transfer_telemetry"
        and row.get("direction") == "d2h" and row.get("status") == "completed"
    ]
    command_ids = [row["command_id"] for row in transfers]
    if len(set(command_ids)) != len(command_ids):
        raise ValueError("duplicate native D2H completion")
    result, used = [], set()
    for position, release in releases:
        after = next((
            row for index, row in prepares
            if index > position and any(
                item[:2] == [release["cold_node_id"], release["cold_creation_time"]]
                for item in row["native_nodes"]
            )
        ), None)
        row = {
            "cold_node_id": release["cold_node_id"],
            "cold_creation_time": release["cold_creation_time"],
            "release_ts_ms": release["ts_ms"],
            "released_host_bytes": release["released_host_bytes"],
            "released_host_nodes": len(release["released_host_nodes"]),
            "planned_backup_tokens": release["planned_backup_tokens"],
            "status": "no_later_same_generation_prepare_in_sealed_prefix",
            "confirmed_backup_bytes": 0,
            "downstream_hbm_release_or_service_verified": False,
        }
        if after is not None:
            nodes = [
                item for item in after["native_nodes"]
                if item[:2] == [release["cold_node_id"], release["cold_creation_time"]]
            ]
            if len(nodes) != 1 or not 0 < nodes[0][2] <= release["planned_backup_tokens"]:
                raise ValueError("same-generation prepare has invalid cold extent size")
            tokens = nodes[0][2]
            matches = [
                ack for ack in transfers
                if ack["node_ids"] == [release["cold_node_id"]]
                and ack["actual_bytes"] == tokens * unit
                and ack.get("num_tokens_by_pool") == {"kv": tokens}
                and release["ts_ms"] <= ack["submit_ts_ms"] <= after["ts_ms"]
                and ack["complete_ts_ms"] >= ack["submit_ts_ms"]
            ]
            row.update(
                status="prepare_without_unique_completed_d2h_in_sealed_prefix",
                prepare_ts_ms=after["ts_ms"], actual_prepared_tokens=tokens,
            )
            if len(matches) == 1:
                ack = matches[0]
                if ack["command_id"] in used:
                    raise ValueError("a native D2H is attributed to two mirror releases")
                used.add(ack["command_id"])
                row.update(
                    status="same_generation_prepare_and_native_d2h_verified",
                    command_id=ack["command_id"],
                    confirmed_backup_bytes=ack["actual_bytes"],
                    native_submit_ts_ms=ack["submit_ts_ms"],
                    native_complete_ts_ms=ack["complete_ts_ms"],
                )
        result.append(row)
    counts = Counter(row["status"] for row in result)
    latest = snapshots[-1] if snapshots else None
    return {
        "summary": {
            "mirror_release_records": len(result),
            "released_host_nodes": sum(row["released_host_nodes"] for row in result),
            "released_host_bytes": sum(row["released_host_bytes"] for row in result),
            "verified_native_cold_backup_bytes": sum(
                row["confirmed_backup_bytes"] for row in result
            ),
            "statuses": dict(counts),
        },
        "releases": result,
        "latest_recorded_recycle_counts": {
            name: value for name, value in (latest or {}).get("counts", {}).items()
            if name.startswith("idle_host_mirror_recycle_")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve the original lifecycle evidence")
    registration = Path(
        "/home/longhao/experiment/ops/idle_host_mirror_recycle_20261027_r1.registration.json"
    )
    plan = causal.load(registration)
    causal.comparison.verify_sources(plan)
    arm = Path(plan["run_root"]) / "full"
    census = arm / "server/native_capacity_census.json"
    capacity = causal.load(census)["capacity"]
    unit, remainder = divmod(capacity["host_full_bytes"], capacity["host_full_tokens"])
    if remainder or capacity["pool_layout"] != "static_full_only" or unit != 98304:
        raise ValueError("unexpected native capacity geometry")
    reader = SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/transfer_telemetry.jsonl"), unit,
    )
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(registration), registration_sha256=sha256_file(registration),
        arm=str(arm), code_commit=plan["code_commit"],
        capacity_census_sha256=sha256_file(census),
        input_jsonl_prefixes=reader.prefixes, operator_sha256=sha256_file(Path(__file__)),
        performance_accepted=False,
        limitations=[
            "Independent sealed file prefixes are not an atomic snapshot.",
            "Native D2H association requires exact node, extent bytes and the submission window.",
            "A confirmed Host backup is not evidence of later HBM release or model service.",
            "Cumulative released/copied bytes are not unique occupancy, hidden time or JCT savings.",
            "Normal tails and complete matched JCT/throughput require the final run audit.",
            "Progressive layer-capacity handoff remains unimplemented.",
        ],
    )
    causal.reservation.write_json(args.output, result)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
