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
            "staged_monotonic_ms": journal.get("monotonic_ms"),
            "monotonic_clock_domain": journal.get("monotonic_clock_domain"),
        })
    return {
        "counts": dict(counts), "episodes": rows,
        "exact_native_ack_matches_verified": True,
        "scope": "staged native demand only; no speculative or request cache-hit credit",
        "first_service_and_hidden_transfer_time_proven": False,
    }


def service_evidence(episodes, journals, workers, *, receipts=None):
    """Bind later native service or explicit cancellation; do not infer KV reuse."""
    fields = (
        "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch",
        "attempt_id", "session_id", "session_generation",
    )
    required = {
        member["request_id"] for episode in episodes for member in episode["requests"]
    }
    indexed = {request: [] for request in required}
    seen = set()
    for row in workers:
        if row.get("event") != "gpu_service_sample" or row.get("phase") != "prefill":
            continue
        identity = row.get("monotonic_clock_domain"), row.get("forward_iter")
        if identity in seen:
            raise ValueError("staged service audit found duplicate native forward identity")
        seen.add(identity)
        for member in row.get("request_samples", ()):
            if member.get("request_id") in indexed:
                indexed[member["request_id"]].append((row, member))
    cancellations = {}
    for row in journals:
        if row.get("event") == "native_demand_restore_cancelled":
            key = transfer_key({
                "submit_ts_ms": row.get("native_submit_ts_ms"),
                "node_ids": row.get("native_node_ids"),
                "actual_bytes": row.get("native_bytes"),
            })
            if key in cancellations:
                raise ValueError("duplicate staged native cancellation identity")
            cancellations[key] = row
    completed = None
    if receipts is not None:
        completed = {}
        for row in receipts:
            request = row.get("request_id")
            if request in completed:
                raise ValueError("duplicate native completion receipt")
            completed[request] = row["native_receipt"]
    rows, counts = [], Counter()
    for episode in episodes:
        domain, staged = episode["monotonic_clock_domain"], episode["staged_monotonic_ms"]
        if (
            type(domain) is not str or not domain or type(staged) not in (int, float)
            or not math.isfinite(staged)
        ):
            raise ValueError("staged service requires an exact monotonic clock")
        for member in episode["requests"]:
            matches = [
                (worker, sample) for worker, sample in indexed[member["request_id"]]
                if all(
                    type(sample.get(name)) is type(member[name])
                    and sample[name] == member[name] for name in fields
                )
                and worker.get("monotonic_clock_domain") == domain
            ]
            if not matches:
                cancel = cancellations.get((
                    episode["native_submit_ts_ms"], tuple(episode["native_node_ids"]),
                    episode["native_h2d_bytes"],
                ))
                at = cancel.get("monotonic_ms") if cancel is not None else None
                if (
                    cancel is None or cancel.get("monotonic_clock_domain") != domain
                    or type(at) not in (int, float) or not math.isfinite(at) or at < staged
                    or not any(
                        all(candidate.get(name) == member[name] for name in fields)
                        for candidate in cancel.get("requests", ())
                    )
                ):
                    raise ValueError("staged member lacks exact native service or cancellation")
                counts["cancelled_members_without_service"] += 1
                rows.append({**member, "status": "explicitly_cancelled_without_service"})
                continue
            if any(
                type(worker.get("launch_monotonic_ms")) not in (int, float)
                or not math.isfinite(worker["launch_monotonic_ms"])
                for worker, _ in matches
            ):
                raise ValueError("staged service lacks a finite native launch boundary")
            worker, _ = min(matches, key=lambda pair: pair[0]["launch_monotonic_ms"])
            launch, done = worker["launch_monotonic_ms"], worker.get("complete_monotonic_ms")
            if (
                launch < staged or type(done) not in (int, float) or not math.isfinite(done)
                or done < launch or type(worker.get("forward_iter")) is not int
            ):
                raise ValueError("staged native service has invalid chronology")
            if completed is not None:
                receipt = completed.get(member["request_id"])
                if (
                    receipt is None or receipt.get("request_id") != member["request_id"]
                    or receipt.get("identity") != [
                        member[name] for name in (
                            "workflow_id", "invocation_id", "context_id", "context_epoch",
                        )
                    ]
                    or receipt.get("clock_domain") != domain
                    or (receipt.get("session_id"), receipt.get("session_generation")) != (
                        member["session_id"], member["session_generation"],
                    )
                    or type(receipt.get("complete_monotonic_ms")) not in (int, float)
                    or not math.isfinite(receipt["complete_monotonic_ms"])
                    or receipt["complete_monotonic_ms"] < done
                ):
                    raise ValueError("staged native service lacks its exact completed receipt")
            counts["members_with_subsequent_native_service"] += 1
            rows.append({
                **member, "status": "subsequent_native_service_verified",
                "first_native_forward_iter": worker["forward_iter"],
                "first_launch_monotonic_ms": launch,
                "staged_to_first_native_launch_ms": launch - staged,
                "native_completion_receipt_verified": completed is not None,
            })
    return {
        "counts": dict(counts), "members": rows,
        "all_members_have_native_service_or_explicit_cancellation": True,
        "native_completion_receipts_required": completed is not None,
        "native_kv_reuse_or_hidden_dma_proven": False,
        "counting_unit": "staging episode/member association, not unique request",
        "timing_boundary": "native run_batch entry/completed worker; not CUDA kernel time",
    }
