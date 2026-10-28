"""Separate complete transfer, handoff-size and worker observations."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_join_reentry_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal

OPS = ROOT.parent / "ops"
RUNS = {
    "JointFull": ("host_demand_layer_handoff_20261028_r1", "full"),
    "PreviousFull": ("tool_online_refresh_20261027_r1", "full"),
    "ThunderAgent": ("v345_locobench_categorized_thunder_decay_20261027", "thunderagent_acting_decay"),
}


def inspect(name, registration, label):
    plan = causal.load(registration)
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("require complete work and actual owned exit")
    causal.comparison.verify_sources(plan)
    arm = Path(plan["run_root"]) / label
    summary = causal.load(arm / "replay/summary.json")
    causal.validate_result(plan, summary, arm, live_control=True)
    reader = SealedRecords()
    handoffs, last, issued = [], None, Counter()
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        event = row.get("event")
        if event == "layer_kv_handoff_submitted":
            handoffs.append(row)
        elif event == "admission_runtime_state":
            last = row
        elif event == "prefetch_native_issued":
            issued[row.get("source", "unknown")] += 1
    if last is None or last["physical_disabled"]:
        raise ValueError("missing healthy runtime observation")
    unit, remainder = divmod(
        plan["source_capacity"]["host_full_bytes"], plan["source_capacity"]["host_full_tokens"],
    )
    if remainder or unit != 98304:
        raise ValueError("require observed exact FULL-only bytes per token")
    transfers = list(reader(arm / "server/transfer_telemetry.jsonl"))
    copied = Counter()
    for row in transfers:
        direction = row["direction"]
        if row["status"] != "completed" or direction not in ("h2d", "d2h"):
            raise ValueError("incomplete transfer")
        tagged = sum(
            child["num_bytes"] for child in row.get("tagged_child_commits", ())
            if child.get("command_id") is not None
        )
        if not 0 <= tagged <= row["actual_bytes"]:
            raise ValueError("invalid tagged bytes")
        copied[direction + "_bytes"] += row["actual_bytes"]
        copied[direction + "_tagged_bytes"] += tagged
        copied[direction + "_operations"] += 1
        copied[direction + "_stream_ms_not_exposed_wait"] += row["transfer_stream_elapsed_ms"]
    submits = {}
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        if row.get("kind") != "llm_submit":
            continue
        rid = row["attributes"]["request_id"]
        if rid in submits:
            raise ValueError("duplicate native request")
        submits[rid] = row
    receipts = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
    }
    by_request = {row["beneficiary_request_id"]: row for row in handoffs}
    if len(by_request) != len(handoffs):
        raise ValueError("repeated handoff consumer needs explicit episode handling")
    first, workers, samples = {}, Counter(), 0
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        samples += 1
        if row["sample_id"] != f"native-service-{samples:09d}":
            raise ValueError("native service gap")
        phase = row["phase"]
        workers[phase + "_batches"] += 1
        workers[phase + "_worker_ms"] += row["service_elapsed_ms"]
        for sample in row["request_samples"]:
            rid = sample["request_id"]
            if rid in by_request and rid not in first:
                receipt = receipts[rid]["native_receipt"]
                if (
                    identity(sample) != tuple(receipt["identity"])
                    or row["monotonic_clock_domain"] != receipt["clock_domain"]
                ):
                    raise ValueError("handoff consumer native identity differs")
                first[rid] = row["service_start_ts_ms"]
    status_path = arm / "server/native_telemetry_status.json"
    status = causal.load(status_path)
    if samples != status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]:
        raise ValueError("incomplete worker samples")
    used, observations, previous = set(), [], float("-inf")
    for handoff in sorted(handoffs, key=lambda row: row["ts_ms"]):
        rid = handoff["beneficiary_request_id"]
        if (
            handoff["capacity_effect"] != "reserved_extent;no_free_page_credit"
            or handoff["logical_admission_changed"] is not False
            or handoff["donor_tokens"] != handoff["reused_tokens"] + handoff["reserved_tail_tokens"]
            or handoff["reused_tokens"] != (
                handoff["checkpoint_missing_tokens"] + handoff["restored_decode_tail_tokens"]
            )
        ):
            raise ValueError("invalid layer handoff extent accounting")
        sides = {}
        for direction, node, tokens, nodes in (
            ("d2h", handoff["donor_node_id"], handoff["donor_tokens"], handoff["donor_nodes"]),
            ("h2d", handoff["consumer_node_id"], handoff["reused_tokens"], handoff["consumer_nodes"]),
        ):
            candidates = [
                row for row in transfers
                if row["direction"] == direction
                and len(row["node_ids"]) == len(set(row["node_ids"])) == nodes
                and row["node_ids"][-1] == node
                and previous < row["submit_ts_ms"] <= handoff["ts_ms"]
                and row["actual_bytes"] == tokens * unit
                and row.get("num_tokens_by_pool") == {"kv": tokens}
                and not row.get("tagged_child_commits") and not row.get("node_commits")
                and row.get("enqueue_to_submit_ms") == 0.
            ]
            if len(candidates) != 1:
                raise ValueError("handoff lacks a unique exact native ACK side")
            side = candidates[0]
            if side["command_id"] in used:
                raise ValueError("handoff transfer would be counted twice")
            used.add(side["command_id"])
            sides[direction] = side
        if sides["h2d"]["submit_ts_ms"] != sides["d2h"]["submit_ts_ms"]:
            raise ValueError("handoff ACK pair has different native submission")
        native = receipts[rid]["native_receipt"]
        submit = submits[rid]
        if (
            identity(submit) != (
                native["identity"][0], handoff["beneficiary_invocation_id"],
                handoff["beneficiary_context_id"], handoff["beneficiary_context_epoch"],
            )
            or native["identity"] != list(identity(submit))
            or (native["session_id"], native["session_generation"]) != (
                handoff["beneficiary_session_id"], handoff["beneficiary_session_generation"],
            )
            or first[rid] < handoff["ts_ms"]
        ):
            raise ValueError("handoff does not bind to later same-session consumer service")
        observations.append({
            "request_id": rid, "donor_tokens": handoff["donor_tokens"],
            "consumer_tokens": handoff["reused_tokens"],
            "donor_node_id": handoff["donor_node_id"],
            "consumer_node_id": handoff["consumer_node_id"],
            "published_donor_node_ids": sides["d2h"]["node_ids"],
            "published_consumer_node_ids": sides["h2d"]["node_ids"],
            "donor_bytes": sides["d2h"]["actual_bytes"],
            "consumer_bytes": sides["h2d"]["actual_bytes"],
            "native_pair_submit_ts_ms": sides["h2d"]["submit_ts_ms"],
            "journal_ts_ms": handoff["ts_ms"], "first_native_run_batch_ts_ms": first[rid],
            "journal_to_first_native_run_batch_ms": first[rid] - handoff["ts_ms"],
            "h2d_stream_ms_including_dependencies": sides["h2d"]["transfer_stream_elapsed_ms"],
            "d2h_stream_ms": sides["d2h"]["transfer_stream_elapsed_ms"],
            "h2d_ack_complete_ts_ms": sides["h2d"]["complete_ts_ms"],
            "d2h_ack_complete_ts_ms": sides["d2h"]["complete_ts_ms"],
            "native_run_batch_before_h2d_ack": first[rid] < sides["h2d"]["complete_ts_ms"],
            "native_run_batch_before_d2h_ack": first[rid] < sides["d2h"]["complete_ts_ms"],
        })
        previous = handoff["ts_ms"]
    host = sum(row["attributes"].get("cached_tokens_host", 0) for row in submits.values()) * unit
    handoff_bytes = sum(row["consumer_bytes"] for row in observations)
    unexplained = copied["h2d_bytes"] - copied["h2d_tagged_bytes"] - host - handoff_bytes
    for path, seal in reader.prefixes.items():
        if Path(path).stat().st_size != seal["complete_prefix_bytes"] or sha256_file(Path(path)) != seal["complete_prefix_sha256"]:
            raise ValueError("complete audit input changed")
    return {
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "transfers": dict(copied), "workers": dict(workers),
        "native_request_host_hit_bytes": host,
        "handoff_consumer_h2d_bytes": handoff_bytes,
        "untagged_h2d_unexplained_after_request_hits_and_handoff_bytes": unexplained,
        "native_untagged_demand_plus_handoff_reconciled": unexplained == 0,
        "handoff_events": len(observations), "handoffs": observations,
        "handoff_consumer_tokens": distribution(row["consumer_tokens"] for row in observations),
        "handoff_donor_tokens": distribution(row["donor_tokens"] for row in observations),
        "handoff_h2d_stream_ms_including_dependencies": distribution(
            row["h2d_stream_ms_including_dependencies"] for row in observations
        ),
        "prefetch_journal_commands_by_source": dict(issued),
        "last_recorded_counts_not_shutdown_final": last["counts"],
        "last_recorded_idle_prepare": last["idle_full_prepare"],
        "summary_sha256": sha256_file(arm / "replay/summary.json"),
        "status_sha256": sha256_file(status_path),
        "input_jsonl_prefixes": reader.prefixes,
    }


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    results = {
        name: inspect(name, OPS / (registration + ".registration.json"), label)
        for name, (registration, label) in RUNS.items()
    }
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": results,
        "operator_sha256": sha256_file(Path(__file__)),
        "limitations": [
            "Joint changes and different engine trees prevent isolated layer attribution.",
            "Native run_batch entry precedes kernels and is not proof of compute-copy overlap.",
            "Transfer-stream intervals include stream dependencies and are not exclusive DMA or JCT loss.",
            "Handoff bytes bind exact paired ACKs and consumer session service, not every GPU page read.",
            "Published path length, anchor and bytes bind the pair; per-node path generations are not journaled.",
            "Each configuration has one execution; no repeated causal module effect is inferred.",
            "An unexplained untagged byte balance remains unknown, not forced into demand or handoff.",
        ],
        "performance_accepted": False,
    })
    print(json.dumps({
        name: {key: row[key] for key in (
            "transfers", "workers", "handoff_events", "handoff_consumer_h2d_bytes",
            "native_untagged_demand_plus_handoff_reconciled",
            "untagged_h2d_unexplained_after_request_hits_and_handoff_bytes",
        )} for name, row in results.items()
    }, indent=2))


if __name__ == "__main__":
    main()
