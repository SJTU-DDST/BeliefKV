"""Bind queued-demand staging journals to exact native H2D ACKs."""

from collections import Counter
import math


def transfer_key(row):
    submitted = row.get("submit_ts_ms")
    nodes = row.get("node_ids")
    size = row.get("actual_bytes")
    if (
        type(submitted) not in (int, float) or not math.isfinite(submitted)
        or not isinstance(nodes, (list, tuple)) or not nodes
        or any(type(node) is not int for node in nodes)
        or len(set(nodes)) != len(nodes) or type(size) is not int or size <= 0
    ):
        raise ValueError("staged demand requires exact native transfer identity")
    return submitted, tuple(nodes), size


def inspect(journals, transfers, *, bytes_per_token):
    """Keep staged copies native-owned; grant no prefetch or cache-hit credit."""
    if type(bytes_per_token) is not int or bytes_per_token <= 0:
        raise ValueError("staged demand requires exact FULL byte geometry")
    acks = {}
    for row in transfers:
        if row.get("direction") != "h2d":
            continue
        # Old native records without an exact transfer identity cannot match a claim.
        if row.get("submit_ts_ms") is None or not row.get("node_ids"):
            continue
        key = transfer_key(row)
        if key in acks:
            raise ValueError("duplicate native H2D transfer identity")
        acks[key] = row
    claimed, rows, counts = set(), [], Counter()
    for journal in journals:
        if journal.get("event") != "native_demand_restore_staged":
            continue
        members = journal.get("requests", [journal])
        batch_size = journal.get("native_batch_size", 1)
        if (
            not isinstance(members, list) or type(batch_size) is not int
            or batch_size <= 0 or len(members) != batch_size
        ):
            raise ValueError("staged demand lacks its complete native selected batch")
        ids = set()
        for member in members:
            if not isinstance(member, dict) or any(
                type(member.get(field)) is not str or not member[field]
                for field in ("request_id", "workflow_id", "invocation_id", "context_id", "session_id")
            ) or any(
                type(member.get(field)) is not int or member[field] < 0
                for field in ("context_epoch", "attempt_id", "session_generation")
            ):
                raise ValueError("staged demand lacks exact request/session identity")
            if member["request_id"] in ids:
                raise ValueError("duplicate staged demand native batch member")
            ids.add(member["request_id"])
        if any(
            journal.get(field) != members[0][field]
            for field in (
                "request_id", "workflow_id", "invocation_id", "context_id", "session_id",
                "context_epoch", "attempt_id", "session_generation",
            )
        ):
            raise ValueError("staged demand head identity disagrees with its native batch")
        key = transfer_key({
            "submit_ts_ms": journal.get("native_submit_ts_ms"),
            "node_ids": journal.get("native_node_ids"),
            "actual_bytes": journal.get("native_bytes"),
        })
        ack = acks.get(key)
        tokens = journal.get("native_full_tokens")
        if (
            key in claimed or ack is None or ack.get("status") != "completed"
            or ack.get("tagged_child_commits")
            or type(tokens) is not int or tokens <= 0
            or tokens * bytes_per_token != key[2]
            or ack.get("num_tokens_by_pool") != {"kv": tokens}
        ):
            raise ValueError("staged demand lacks one exact untagged FULL native ACK")
        submitted, recorded = key[0], journal.get("ts_ms")
        if (
            type(recorded) not in (int, float) or not math.isfinite(recorded)
            or recorded < submitted
        ):
            raise ValueError("staged demand journal precedes its native submission")
        claimed.add(key)
        counts["matched_staging_episodes"] += 1
        counts["native_selected_requests"] += len(members)
        counts["native_full_tokens"] += tokens
        counts["native_h2d_bytes"] += key[2]
        rows.append({
            "request_id": journal["request_id"], "workflow_id": journal["workflow_id"],
            "invocation_id": journal["invocation_id"], "context_id": journal["context_id"],
            "session_id": journal["session_id"], "session_generation": journal["session_generation"],
            "context_epoch": journal["context_epoch"], "attempt_id": journal["attempt_id"],
            "native_batch_size": batch_size, "requests": members,
            "native_submit_ts_ms": submitted, "journal_ts_ms": recorded,
            "native_node_ids": list(key[1]), "native_full_tokens": tokens,
            "native_h2d_bytes": key[2],
        })
    return {
        "counts": dict(counts), "episodes": rows,
        "exact_native_ack_matches_verified": True,
        "scope": "staged native demand only; no speculative or request cache-hit credit",
        "first_service_and_hidden_transfer_time_proven": False,
    }
