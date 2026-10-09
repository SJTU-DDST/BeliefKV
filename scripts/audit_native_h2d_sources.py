#!/usr/bin/env python3
"""Separate JOIN/tool anticipation, demand handoff and native H2D payloads."""

import argparse
from collections import Counter
from collections.abc import Iterable
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.summarize_semantic_h2d_ab import records


CONTROLLED_CATEGORIES = ("predictive", "execution_handoff", "unknown_controlled")


def source_category(source: str | None) -> str:
    if source in ("join_ticket", "tool_wait"):
        return "predictive"
    if source == "execution_handoff":
        return "execution_handoff"
    return "unknown_controlled"


def acknowledged_prefetch_sources(
    arm: Path, acknowledgements: Iterable[dict] | None = None,
) -> dict[str, str | None]:
    rows = (
        records(arm / "server/physical_action_ack.jsonl")
        if acknowledgements is None else acknowledgements
    )
    sources = {
        row["command_id"]: row.get("source")
        for row in rows
        if row.get("action") == "PREFETCH_GPU"
    }
    path = arm / "opportunities/admission_opportunities.jsonl"
    if sources and path.exists():
        with path.open() as stream:
            for line in stream:
                if "prefetch_native_issued" not in line:
                    continue
                row = json.loads(line)
                if row.get("event") == "prefetch_native_issued":
                    command = row["command_id"]
                    if command in sources and row.get("source") is not None:
                        sources[command] = row["source"]
    return sources


def split_h2d_payload(row: dict, sources: dict) -> tuple[Counter, dict[str, Counter]]:
    """Use child receipts for controlled bytes and leave the remainder native."""
    size = row["actual_bytes"]
    byte_counts = Counter({category: 0 for category in CONTROLLED_CATEGORIES})
    pool_units = {category: Counter() for category in (*CONTROLLED_CATEGORIES, "native")}
    for child in row.get("tagged_child_commits") or ():
        category = source_category(sources.get(child["command_id"]))
        byte_counts[category] += child["num_bytes"]
        pool_units[category].update(child["num_tokens_by_pool"])
    byte_counts["native"] = size - sum(byte_counts.values())
    native_units = Counter(row.get("num_tokens_by_pool") or {})
    for category in CONTROLLED_CATEGORIES:
        native_units.subtract(pool_units[category])
    if byte_counts["native"] < 0 or any(units < 0 for units in native_units.values()):
        raise ValueError(f"H2D child receipts exceed batch payload: {row.get('command_id')}")
    pool_units["native"].update({pool: units for pool, units in native_units.items() if units})
    return byte_counts, pool_units


def audit(arm: Path) -> dict:
    sources = acknowledged_prefetch_sources(arm)
    commands = {
        category: {command for command, source in sources.items() if source_category(source) == category}
        for category in CONTROLLED_CATEGORIES
    }
    counts = Counter()
    units = {category: Counter() for category in (*CONTROLLED_CATEGORIES, "native")}
    bytes_by_source = Counter()
    for row in records(arm / "server/transfer_telemetry.jsonl"):
        if row.get("direction") != "h2d":
            continue
        counts["controller_batches"] += 1
        size = row.get("actual_bytes")
        if type(size) is not int:
            counts["unknown_payload_batches"] += 1
            continue
        children = row.get("tagged_child_commits") or ()
        payload, pool_units = split_h2d_payload(row, sources)
        for child in children:
            source = sources.get(child["command_id"])
            category = source_category(source)
            commands[category].add(child["command_id"])
            bytes_by_source[source or "unrecorded"] += child["num_bytes"]
        tagged = sum(payload[category] for category in CONTROLLED_CATEGORIES)
        counts["total_bytes"] += size
        counts["controlled_bytes"] += tagged
        counts["controlled_batches"] += bool(children)
        counts["controlled_only_batches"] += bool(children) and payload["native"] == 0
        counts["native_only_batches"] += not children
        counts["mixed_batches"] += bool(children) and payload["native"] > 0
        counts["mixed_source_batches"] += sum(amount > 0 for amount in payload.values()) > 1
        for category, amount in payload.items():
            counts[f"{category}_bytes"] += amount
            counts[f"{category}_batches"] += amount > 0
        for category, pools in pool_units.items():
            units[category].update(pools)
    controlled_units = Counter()
    for category in CONTROLLED_CATEGORIES:
        controlled_units.update(units[category])
    return {
        "schema_version": 2,
        "scope": "controller payload batches; no invented request-level byte attribution",
        "source_semantics": {
            "predictive": "JOIN/tool anticipatory intent; actual lead and reuse require separate evidence",
            "execution_handoff": "submitted demand request before first GPU service; not pre-boundary prediction",
            "unknown_controlled": "tagged H2D without a known JOIN/tool/handoff source",
            "native": "untagged controller payload remainder",
            "compatibility": (
                "v1 predictive_* combined all ACKed PREFETCH_GPU; v2 predictive_* is "
                "JOIN/tool only and controlled_* includes all tagged H2D"
            ),
        },
        "counts": dict(counts),
        "controlled_commands": sum(len(group) for group in commands.values()),
        **{f"{category}_commands": len(group) for category, group in commands.items()},
        "controlled_bytes_by_source": dict(bytes_by_source),
        "controlled_pool_units": dict(controlled_units),
        **{f"{category}_pool_units": dict(pools) for category, pools in units.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
