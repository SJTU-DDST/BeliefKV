"""Audit expanded D2H backup paths and anchor-only H2D layer-transaction ACKs."""

from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_join_reentry_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def match_side(handoff, transfers, *, direction, unit, previous):
    donor = direction == "d2h"
    node = handoff["donor_node_id" if donor else "consumer_node_id"]
    tokens = handoff["donor_tokens" if donor else "reused_tokens"]
    matches = [
        row for row in transfers
        if row["direction"] == direction and row["status"] == "completed"
        and row["node_ids"][-1:] == [node]
        and (
            len(row["node_ids"]) == len(set(row["node_ids"])) == handoff["donor_nodes"]
            if donor else row["node_ids"] == [node]
        )
        and previous < row["submit_ts_ms"] <= handoff["ts_ms"]
        and row["actual_bytes"] == tokens * unit
        and row.get("num_tokens_by_pool") == {"kv": tokens}
        and not row.get("tagged_child_commits") and not row.get("node_commits")
        and row.get("enqueue_to_submit_ms") == 0.
    ]
    if len(matches) != 1:
        raise ValueError("handoff lacks one exact anchor/bytes/time native ACK")
    return matches[0]


def inspect(name, registration, label):
    plan = causal.load(registration)
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("require complete work and actual owned exit")
    causal.comparison.verify_sources(plan)
    arm = Path(plan["run_root"]) / label
    causal.validate_result(plan, causal.load(arm / "replay/summary.json"), arm, live_control=True)
    reader, handoffs, issued, last = SealedRecords(), [], Counter(), None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "layer_kv_handoff_submitted":
            handoffs.append(row)
        elif row.get("event") == "admission_runtime_state":
            last = row
        elif row.get("event") == "prefetch_native_issued":
            issued[row.get("source", "unknown")] += 1
    if last is None or last["physical_disabled"]:
        raise ValueError("missing healthy runtime observation")
    unit, remainder = divmod(
        plan["source_capacity"]["host_full_bytes"], plan["source_capacity"]["host_full_tokens"],
    )
    if remainder or unit != 98304:
        raise ValueError("unexpected exact FULL bytes per token")
    transfers, copied = list(reader(arm / "server/transfer_telemetry.jsonl")), Counter()
    for row in transfers:
        if row["status"] != "completed" or row["direction"] not in ("h2d", "d2h"):
            raise ValueError("incomplete native transfer")
        tagged = sum(
            item["num_bytes"] for item in row.get("tagged_child_commits", ())
            if item.get("command_id") is not None
        )
        if not 0 <= tagged <= row["actual_bytes"]:
            raise ValueError("invalid tagged bytes")
        direction = row["direction"]
        copied[direction + "_bytes"] += row["actual_bytes"]
        copied[direction + "_tagged_bytes"] += tagged
        copied[direction + "_operations"] += 1
        copied[direction + "_stream_ms_not_exposed_wait"] += row["transfer_stream_elapsed_ms"]
    submits = {}
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        if row.get("kind") == "llm_submit":
            rid = row["attributes"]["request_id"]
            if rid in submits:
                raise ValueError("duplicate native submit")
            submits[rid] = row
    receipts = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    consumers = {row["beneficiary_request_id"]: row for row in handoffs}
    if len(consumers) != len(handoffs):
        raise ValueError("repeated consumer requires explicit episode accounting")
    workers, first, samples = Counter(), {}, 0
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        samples += 1
        if row["sample_id"] != f"native-service-{samples:09d}":
            raise ValueError("incomplete native service sequence")
        workers[row["phase"] + "_batches"] += 1
        workers[row["phase"] + "_worker_ms"] += row["service_elapsed_ms"]
        for sample in row["request_samples"]:
            rid = sample["request_id"]
            if rid in consumers and rid not in first:
                native = receipts[rid]["native_receipt"]
                if identity(sample) != tuple(native["identity"]) or (
                    row["monotonic_clock_domain"] != native["clock_domain"]
                ):
                    raise ValueError("consumer service identity or clock differs")
                first[rid] = row["service_start_ts_ms"]
    status = causal.load(arm / "server/native_telemetry_status.json")
    if samples != status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]:
        raise ValueError("service observations have not fully drained")
    observations, used, previous = [], set(), float("-inf")
    for handoff in sorted(handoffs, key=lambda row: row["ts_ms"]):
        rid = handoff["beneficiary_request_id"]
        if (
            handoff["capacity_effect"] != "reserved_extent;no_free_page_credit"
            or handoff["logical_admission_changed"] is not False
            or handoff["donor_tokens"] != handoff["reused_tokens"] + handoff["reserved_tail_tokens"]
            or handoff["reused_tokens"] != handoff["checkpoint_missing_tokens"] + handoff["restored_decode_tail_tokens"]
        ):
            raise ValueError("handoff reserved-extent accounting differs")
        sides = {
            direction: match_side(handoff, transfers, direction=direction, unit=unit, previous=previous)
            for direction in ("d2h", "h2d")
        }
        if sides["d2h"]["submit_ts_ms"] != sides["h2d"]["submit_ts_ms"]:
            raise ValueError("native ACK sides do not share the atomic pair timestamp")
        for side in sides.values():
            if side["command_id"] in used:
                raise ValueError("native ACK would be credited twice")
            used.add(side["command_id"])
        native = receipts[rid]["native_receipt"]
        if (
            identity(submits[rid]) != (
                native["identity"][0], handoff["beneficiary_invocation_id"],
                handoff["beneficiary_context_id"], handoff["beneficiary_context_epoch"],
            )
            or native["identity"] != list(identity(submits[rid]))
            or (native["session_id"], native["session_generation"]) != (
                handoff["beneficiary_session_id"], handoff["beneficiary_session_generation"],
            )
            or first[rid] < handoff["ts_ms"]
        ):
            raise ValueError("handoff lacks subsequent same-session consumer service")
        observations.append({
            "request_id": rid, "donor_tokens": handoff["donor_tokens"],
            "consumer_tokens": handoff["reused_tokens"],
            "donor_bytes": sides["d2h"]["actual_bytes"],
            "consumer_bytes": sides["h2d"]["actual_bytes"],
            "published_donor_anchor": sides["d2h"]["node_ids"],
            "published_consumer_anchor": sides["h2d"]["node_ids"],
            "declared_donor_path_nodes": handoff["donor_nodes"],
            "declared_consumer_path_nodes": handoff["consumer_nodes"],
            "donor_ack_node_publication": "expanded_backup_path",
            "consumer_ack_node_publication": "anchor_only_not_individual_path_generations",
            "native_pair_submit_ts_ms": sides["h2d"]["submit_ts_ms"],
            "journal_ts_ms": handoff["ts_ms"], "first_native_run_batch_ts_ms": first[rid],
            "journal_to_first_native_run_batch_ms": first[rid] - handoff["ts_ms"],
            "h2d_stream_ms_including_dependencies": sides["h2d"]["transfer_stream_elapsed_ms"],
            "d2h_stream_ms": sides["d2h"]["transfer_stream_elapsed_ms"],
            "native_run_batch_before_h2d_ack": first[rid] < sides["h2d"]["complete_ts_ms"],
            "native_run_batch_before_d2h_ack": first[rid] < sides["d2h"]["complete_ts_ms"],
        })
        previous = handoff["ts_ms"]
    host = sum(row["attributes"].get("cached_tokens_host", 0) for row in submits.values()) * unit
    handoff_bytes = sum(row["consumer_bytes"] for row in observations)
    unexplained = copied["h2d_bytes"] - copied["h2d_tagged_bytes"] - host - handoff_bytes
    for path, prefix in reader.prefixes.items():
        if Path(path).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("complete native audit input changed")
    return {
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
        "transfers": dict(copied), "workers": dict(workers),
        "native_request_host_hit_bytes": host, "handoff_consumer_h2d_bytes": handoff_bytes,
        "untagged_h2d_unexplained_after_request_hits_and_handoff_bytes": unexplained,
        "native_untagged_demand_plus_handoff_reconciled": unexplained == 0,
        "handoff_events": len(observations), "handoffs": observations,
        "handoff_consumer_tokens": distribution(row["consumer_tokens"] for row in observations),
        "handoff_donor_tokens": distribution(row["donor_tokens"] for row in observations),
        "prefetch_journal_commands_by_source": dict(issued),
        "last_recorded_counts_not_shutdown_final": last["counts"],
        "last_recorded_idle_prepare": last["idle_full_prepare"],
        "multi_node_anchor_only_consumers": sum(row["consumer_nodes"] > 1 for row in handoffs),
        "per_node_path_generation_proven": False,
        "input_jsonl_prefixes": reader.prefixes,
    }
