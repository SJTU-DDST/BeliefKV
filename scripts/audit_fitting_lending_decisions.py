#!/usr/bin/env python3
"""Check actual fitting decisions and their local budget/owner-count tradeoff."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords


def inspect(records):
    rows, totals = [], Counter()
    for event in records:
        if event.get("event") != "agent_blocked_budget_lent":
            continue
        if event.get("lending_selection") != "fit":
            raise ValueError("mixed lending selections")
        eligible = event.get("eligible_donors", ())
        names = [row["invocation_id"] for row in eligible]
        credits = [row["budget_tokens"] for row in eligible]
        required = event["target_tokens"] + event["buffer_tokens_per_agent"] - event["available_tokens_before"]
        if (
            not 1 <= len(eligible) <= 8 or len(set(names)) != len(names)
            or any(type(value) is not int or value <= 0 for value in credits)
            or required <= 0 or required != event["required_budget_tokens"]
            or event.get("backing_proof") != "observed_demand"
            or event.get("native_capacity_authority_preserved") is not True
            or any(not row.get("blocked") for row in eligible)
        ):
            raise ValueError("invalid recorded proven donor set")
        possible = [
            (sum(credits[index] for index in members), len(members), members)
            for count in range(1, len(eligible) + 1)
            for members in combinations(range(len(eligible)), count)
            if sum(credits[index] for index in members) >= required
        ]
        if not possible:
            raise ValueError("executed fitting decision has no adequate donor set")
        expected_credit, expected_count, expected = min(possible)
        if (
            event["donor_budget_tokens"] != expected_credit
            or event["donor_invocation_ids"] != [names[index] for index in expected]
        ):
            raise ValueError("executed selection differs from the independent optimum")
        old_credit, old_count = 0, 0
        for credit in credits:
            old_credit += credit
            old_count += 1
            if old_credit >= required:
                break
        if expected_credit > old_credit:
            raise ValueError("fitting exceeds the adequate original-order prefix")
        totals["decisions"] += 1
        totals["needed_tokens"] += required
        totals["selected_tokens"] += expected_credit
        totals["original_order_prefix_tokens"] += old_credit
        totals["selected_owners"] += expected_count
        totals["original_order_prefix_owners"] += old_count
        totals["decisions_with_strictly_smaller_budget"] += expected_credit < old_credit
        totals["decisions_with_more_selected_owners"] += expected_count > old_count
        totals["decisions_with_fewer_selected_owners"] += expected_count < old_count
        totals["decisions_with_equal_selected_owners"] += expected_count == old_count
        rows.append({
            "ts_ms": event["ts_ms"], "target_invocation_id": event["target_invocation_id"],
            "required_tokens": required,
            "selected_tokens": expected_credit, "selected_owners": expected_count,
            "original_order_prefix_tokens": old_credit,
            "original_order_prefix_owners": old_count,
            "budget_difference_tokens": old_credit - expected_credit,
            "owner_count_difference": expected_count - old_count,
        })
    if not rows:
        raise ValueError("no actual fitting decisions")
    return {
        "counts": dict(totals),
        "selected_over_needed": distribution([
            row["selected_tokens"] / row["required_tokens"] for row in rows
        ]),
        "original_prefix_over_needed": distribution([
            row["original_order_prefix_tokens"] / row["required_tokens"] for row in rows
        ]),
        "owner_count_difference": distribution([row["owner_count_difference"] for row in rows]),
        "decisions": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous decision audits")
    reader = SealedRecords()
    result = inspect(reader(args.arm / "opportunities/admission_opportunities.jsonl"))
    result.update({
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arm": str(args.arm.resolve()), "input_jsonl_prefixes": reader.prefixes,
        "auditor_sha256": sha256_file(Path(__file__)),
        "scope": [
            "Independently verified serialized logical selection over recorded currently proven candidates.",
            "The original-order prefix is a local counterfactual on this same decision, not a simulation or measured result of the old policy.",
            "Smaller logical budget can require more owners; neither quantity proves physical page release, fewer H2D bytes or improved JCT.",
            "Recorded blocking/proof flags are not an independent native lock or physical ownership check.",
            "A sealed growing prefix does not establish full work, repeated gains or performance acceptance.",
        ],
        "performance_accepted": False,
    })
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        name: result[name] for name in (
            "counts", "selected_over_needed", "original_prefix_over_needed",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
