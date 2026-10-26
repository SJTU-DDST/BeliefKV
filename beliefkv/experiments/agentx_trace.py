"""Retrospective structural evidence from anonymized AgentX/Weka traces.

These helpers do not establish task correctness or execute tools. Their inferred
dependencies belong to an offline replay driver, never an online policy.
"""

from __future__ import annotations

from collections import defaultdict
import math


def _seconds(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("finite nonnegative " + name + " required")
    return float(value)


def request_end(request):
    return _seconds(request["t"], "request time") + _seconds(
        request["api_time"], "recorded API duration",
    )


def child_request_time(child, request):
    """Match the author's normalization of legacy relative inner timestamps."""
    spawn = _seconds(child["t"], "spawn time")
    start = _seconds(request["t"], "child request time")
    return spawn + start if start + 1e-6 < spawn else start


def explicit_join_groups(trace, *, main_outer_indices=None):
    """Group explicit child markers using the public loader's tiered JOIN rule.

    Markers are observed, but JOIN boundaries are inferred from recorded ends and
    later parent requests. No nested or model-decided semantics are fabricated.
    """
    if trace.get("hash_id_scope") != "local":
        raise ValueError("only the declared per-trace hash namespace is supported")
    entries = trace["requests"]
    normals = [
        (index, row) for index, row in enumerate(entries)
        if row["type"] in ("n", "s")
        and (main_outer_indices is None or index in main_outer_indices)
    ]
    for _, row in normals:
        request_end(row)
    groups = defaultdict(list)
    unjoined = []
    seen = set()
    for index, child in enumerate(entries):
        if child["type"] != "subagent":
            continue
        identity = child["agent_id"]
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("unique nonempty explicit child identities required")
        seen.add(identity)
        start = _seconds(child["t"], "spawn time")
        duration = child.get("duration_ms")
        calls = child["requests"]
        if duration is None:
            end = max((
                child_request_time(child, call)
                + _seconds(call["api_time"], "child API duration")
                for call in calls
            ), default=start)
        else:
            end = start + _seconds(duration, "child duration") / 1000.
        preceding = max((pos for pos, _ in normals if pos < index), default=None)
        following = next((
            pos for pos, row in normals if pos > index and row["t"] + 1e-6 >= end
        ), None)
        observed_call_end = max((
            child_request_time(child, call)
            + _seconds(call["api_time"], "child API duration")
            for call in calls
        ), default=start)
        evidence = {
            "agent_id": identity, "source_outer_index": index,
            "spawn_seconds": start, "end_seconds": end, "requests": len(calls),
            "status": child["status"],
            "model_served_completed": child["status"] == "completed" and bool(calls),
            "recorded_calls_exceed_declared_end": observed_call_end > end + 1e-6,
        }
        if preceding is None or following is None:
            evidence["reason"] = (
                "no_preceding_parent_request" if preceding is None
                else "background_no_later_parent_request"
            )
            unjoined.append(evidence)
        else:
            groups[preceding, following].append(evidence)
    joined = [{
        "spawn_after_outer_index": key[0], "join_before_outer_index": key[1],
        "members": members,
        "successful_served_members": sum(
            item["model_served_completed"] for item in members
        ),
        "evidence": "explicit_markers_with_author_timestamp_inferred_join",
    } for key, members in sorted(groups.items())]
    return joined, unjoined


def residual_ready_gap_seconds(previous, current, *, dependency_end_seconds=()):
    """Do not count old model service or a required child twice as replay delay."""
    end = max(
        request_end(previous),
        *(_seconds(value, "dependency end") for value in dependency_end_seconds),
    ) if dependency_end_seconds else request_end(previous)
    return max(0., _seconds(current["t"], "request time") - end)
