"""Private, identity-indexed empirical tool timing for declared causal replays."""

from collections import Counter
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import statistics

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import _tool_key


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def plan_fingerprint(plan):
    return fingerprint({
        "nodes": [
            {
                "node_id": node.node_id, "workflow_id": node.workflow_id,
                "dependencies": sorted(node.dependencies), "delay_ms": node.delay_ms,
                "request_id": node.request_id,
                "event": node.event.to_dict() if node.event is not None else None,
            }
            for node in sorted(plan.nodes, key=lambda item: item.node_id)
        ],
        "request_usage": dict(plan.request_usage),
    })


def duration_summary(values):
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in values
    ):
        raise ValueError("tool durations must be finite and nonnegative")
    ordered = sorted(values)
    if not ordered:
        raise ValueError("tool duration population is empty")
    return {
        "count": len(ordered), "mean_ms": statistics.mean(ordered),
        "min_ms": ordered[0], "max_ms": ordered[-1],
        "p50_ms": ordered[math.ceil(.50 * len(ordered)) - 1],
        "p90_ms": ordered[math.ceil(.90 * len(ordered)) - 1],
        "p99_ms": ordered[math.ceil(.99 * len(ordered)) - 1],
        "over_1s": sum(value > 1000 for value in ordered),
        "over_10s": sum(value > 10_000 for value in ordered),
        "over_60s": sum(value > 60_000 for value in ordered),
        "quantiles": "nearest_rank",
    }


def tracelab_samples(rows, *, provider, tool_names):
    """Use reported internal times only; never substitute inter-record gaps."""
    if not provider or not tool_names or len(set(tool_names)) != len(tool_names):
        raise ValueError("declare a provider and distinct public tool families")
    samples, counts, seen = [], Counter(), {}
    for row in rows:
        counts["rows"] += 1
        if row.get("provider") != provider:
            continue
        for tool in row.get("tools") or ():
            if tool.get("tool_name") not in tool_names:
                continue
            counts["selected_tool_records"] += 1
            value = tool.get("tool_internal_latency_ms")
            if value is None:
                counts["missing_internal_latency"] += 1
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("public internal tool time must be finite and nonnegative")
            key = (provider, row.get("session_id"), tool.get("tool_call_id"))
            if any(type(part) is not str or not part for part in key):
                raise ValueError("public tool time lacks a stable call identity")
            sample = {
                "sample_id": fingerprint(key),
                "provider": provider, "tool_name": tool["tool_name"],
                "duration_ms": float(value),
            }
            if key in seen:
                if seen[key] != sample:
                    raise ValueError("public duplicate tool identity has conflicting timing")
                counts["duplicate_tool_records"] += 1
                continue
            seen[key] = sample
            counts["observed_error_calls"] += tool.get("is_error") is True
            samples.append(sample)
    if not samples:
        raise ValueError("selected public tool family has no reported internal times")
    samples.sort(key=lambda item: item["sample_id"])
    return samples, {
        "counts": dict(counts),
        "selection": {
            "provider": provider, "tool_names": sorted(tool_names),
            "latency_field": "tool_internal_latency_ms", "wall_time_fallback": False,
            "duplicate_identity": ["provider", "session_id", "tool_call_id"],
            "error_calls_removed": False, "zero_times_removed": False,
            "tails_clipped": False,
        },
        "sample_sha256": fingerprint(samples),
        "durations": duration_summary([sample["duration_ms"] for sample in samples]),
    }


def _targets(plan, tool_names):
    if type(tool_names) not in (list, tuple) or not tool_names or any(
        type(name) is not str or not name for name in tool_names
    ):
        raise ValueError("declare target tool names without a catch-all fallback")
    if len(set(tool_names)) != len(tool_names):
        raise ValueError("target tool names must be distinct")
    starts = {}
    for node in plan.nodes:
        if node.event is not None and node.event.kind is Kind.TOOL_START:
            key = _tool_key(node.event)
            if key in starts:
                raise ValueError("tool overlay found a duplicate start")
            starts[key] = node
    selected = {}
    for node in plan.nodes:
        if node.event is None or node.event.kind is not Kind.TOOL_END:
            continue
        key = _tool_key(node.event)
        start = starts.get(key)
        if start is None:
            raise ValueError("tool overlay found an unmatched end")
        name = start.event.attributes.get("tool_name")
        if name not in tool_names:
            continue
        if node.node_id in selected or node.dependencies != (start.node_id,):
            raise ValueError("tool duration must depend only on its matching start")
        selected[node.node_id] = (key, name, node)
    if not selected:
        raise ValueError("declared target tool names match no calls")
    return selected


def build_overlay(plan, samples, *, public_source, sample_audit, target_tool_names, seed=21):
    if type(seed) is not int or seed < 0:
        raise ValueError("tool timing seed must be a nonnegative integer")
    if not isinstance(public_source, dict) or not public_source.get("sha256"):
        raise ValueError("empirical timing requires pinned public source provenance")
    samples = sorted(samples, key=lambda item: item["sample_id"])
    if not samples or fingerprint(samples) != sample_audit.get("sample_sha256"):
        raise ValueError("empirical timing pool differs from its sample audit")
    duration_summary([sample["duration_ms"] for sample in samples])
    entries = []
    for node_id, (key, name, node) in sorted(_targets(plan, target_tool_names).items()):
        index = int(fingerprint(["tool_timing_sha256_mod_v1", seed, key]), 16) % len(samples)
        sample = samples[index]
        entries.append({
            "end_node_id": node_id, "tool_identity": list(key), "tool_name": name,
            "original_duration_ms": node.delay_ms,
            "duration_ms": sample["duration_ms"], "sample_id": sample["sample_id"],
        })
    return {
        "schema_version": 1, "kind": "empirical_tool_timing_overlay",
        "sampling": "tool_timing_sha256_mod_v1", "seed": seed,
        "source_plan_sha256": plan_fingerprint(plan),
        "public_source": dict(public_source), "sample_audit": sample_audit,
        "target_tool_names": sorted(target_tool_names),
        "entries": entries, "entries_sha256": fingerprint(entries),
        "original_target_durations": duration_summary([row["original_duration_ms"] for row in entries]),
        "sampled_target_durations": duration_summary([row["duration_ms"] for row in entries]),
        "synthetic_tool_timing": True, "request_work_changed": False,
        "future_timing_delivered_to_policy": False, "performance_accepted": False,
    }


def apply_overlay(plan, document):
    if (
        document.get("schema_version") != 1
        or document.get("kind") != "empirical_tool_timing_overlay"
        or document.get("sampling") != "tool_timing_sha256_mod_v1"
        or type(document.get("seed")) is not int or document["seed"] < 0
        or document.get("synthetic_tool_timing") is not True
        or document.get("request_work_changed") is not False
        or document.get("future_timing_delivered_to_policy") is not False
        or document.get("source_plan_sha256") != plan_fingerprint(plan)
    ):
        raise ValueError("tool timing overlay is undeclared or belongs to another source graph")
    targets = _targets(plan, document.get("target_tool_names"))
    entries = document.get("entries")
    if not isinstance(entries, list) or document.get("entries_sha256") != fingerprint(entries):
        raise ValueError("tool timing map changed after preparation")
    changes = {}
    for entry in entries:
        node_id = entry.get("end_node_id")
        if node_id not in targets or node_id in changes:
            raise ValueError("tool timing map has a duplicate or foreign call")
        key, name, node = targets[node_id]
        duration = entry.get("duration_ms")
        if (
            entry.get("tool_identity") != list(key) or entry.get("tool_name") != name
            or entry.get("original_duration_ms") != node.delay_ms
            or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0
            or type(entry.get("sample_id")) is not str or len(entry["sample_id"]) != 64
        ):
            raise ValueError("tool timing entry changes identity or has an invalid duration")
        changes[node_id] = float(duration)
    if changes.keys() != targets.keys():
        raise ValueError("tool timing map does not cover the complete declared population")
    return replace(plan, nodes=tuple(
        replace(node, delay_ms=changes[node.node_id]) if node.node_id in changes else node
        for node in plan.nodes
    ))


def load_overlay(plan, path: Path):
    payload = path.read_bytes()
    document = json.loads(payload)
    transformed = apply_overlay(plan, document)
    transformed_hash = plan_fingerprint(transformed)
    if document.get("transformed_plan_sha256", transformed_hash) != transformed_hash:
        raise ValueError("prepared transformed graph differs from its timing map")
    evidence = {
        "path": str(path.resolve()), "sha256": hashlib.sha256(payload).hexdigest(),
        "kind": document["kind"], "seed": document["seed"],
        "public_source": document["public_source"],
        "sample_sha256": document["sample_audit"]["sample_sha256"],
        "entries_sha256": document["entries_sha256"],
        "target_tool_names": document["target_tool_names"], "calls": len(document["entries"]),
        "source_plan_sha256": document["source_plan_sha256"],
        "transformed_plan_sha256": transformed_hash,
        "synthetic_tool_timing": True, "future_timing_delivered_to_policy": False,
    }
    return transformed, evidence
