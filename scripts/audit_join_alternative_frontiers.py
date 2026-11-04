"""Check whether a branch alternative was its own observed JOIN completion frontier."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import (
    EventConfidence, RelationType, RuntimeEvent, RuntimeEventKind as Kind, is_unobserved_join_event,
)
from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def own_frontiers(competitor, now_ms, joins, returns, roots):
    result = []
    for identity, group in joins.items():
        if (
            competitor not in group["members"] or group["wait_ms"] is None
            or group["wait_ms"] > now_ms
            or group["satisfied_ms"] is not None and group["satisfied_ms"] <= now_ms
        ):
            continue
        missing = sorted(set(group["members"]) - returns.keys())
        peers = [member for member in group["members"] if member != competitor]
        item = {"JOIN": identity, "workflow": group["workflow"], "missing_returns": missing}
        if missing:
            item["status"] = "unclosed_JOIN_unknown"
        elif not peers:
            item["status"] = "singleton_JOIN"
        else:
            own = returns[competitor]
            other = max(returns[member] for member in peers)
            item.update(
                status="closed_multi_member_JOIN",
                competitor_was_actual_last_return=own >= other,
                other_last_minus_competitor_return_s=(other - own) / 1000.,
                isolated_advance_ceiling_s=max(0., (own - max(other, now_ms)) / 1000.),
            )
        result.append(item)
    if not result:
        result.append({"status": "root_continuation" if competitor in roots else "no_current_observed_JOIN"})
    return result


def read_events(path):
    joins, returns, roots = {}, {}, set()
    with path.open() as stream:
        for line in stream:
            if not line.endswith("\n"):
                raise ValueError("completion-frontier analysis requires a sealed event stream")
            event = RuntimeEvent.from_dict(json.loads(line))
            if event.confidence is EventConfidence.INFERRED or is_unobserved_join_event(event):
                continue
            if event.kind is Kind.INVOCATION_CREATE and event.relation_type is RelationType.ROOT:
                roots.add(event.invocation_id)
            elif event.kind is Kind.JOIN_CREATE and event.attributes.get("mode") == "all":
                joins[event.join_id] = {
                    "members": tuple(event.member_invocation_ids), "workflow": event.workflow_id,
                    "wait_ms": None, "satisfied_ms": None,
                }
            elif event.kind is Kind.JOIN_WAIT and event.join_id in joins:
                joins[event.join_id]["wait_ms"] = event.ts_ms
            elif event.kind is Kind.JOIN_SATISFIED and event.join_id in joins:
                joins[event.join_id]["satisfied_ms"] = event.ts_ms
            elif event.kind is Kind.RETURN:
                if event.invocation_id in returns:
                    raise ValueError("duplicate invocation RETURN in the sealed execution")
                returns[event.invocation_id] = event.ts_ms
    return joins, returns, roots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("oracle", "robustness", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve previous completion-frontier evidence")
    oracle, robustness = (json.loads(path.read_bytes()) for path in (args.oracle, args.robustness))
    matches = [
        (Path(name), binding) for name, binding in oracle["input_jsonl_prefixes"].items()
        if name.endswith("/replay/runtime_events.replay.jsonl")
    ]
    if len(matches) != 1:
        raise ValueError("require the exact observed client publication stream")
    events, binding = matches[0]
    if (
        events.stat().st_size != binding["complete_prefix_bytes"]
        or sha256_file(events) != binding["complete_prefix_sha256"]
        or robustness["inputs_sha256"].get(str(args.oracle.resolve())) != sha256_file(args.oracle)
    ):
        raise ValueError("the original branch opportunity population changed")
    joins, returns, roots = read_events(events)
    boundaries = {
        group["selection_sequence"]: group["monotonic_s"] * 1000.
        for group in oracle["group_observations"]
    }
    counts, rows = Counter(), []
    for alternative in robustness["alternatives"]:
        if alternative["intrinsic_margin_s"] < 0:
            continue
        frontiers = own_frontiers(
            alternative["competitor"], boundaries[alternative["selection_sequence"]],
            joins, returns, roots,
        )
        counts["positive_intrinsic_alternatives"] += 1
        for frontier in frontiers:
            counts[frontier["status"]] += 1
            if frontier.get("competitor_was_actual_last_return"):
                counts["positive_alternative_with_actual_last_return"] += 1
        rows.append({**alternative, "competitor_own_frontiers": frontiers})
    result = {
        "counts": dict(counts), "alternatives": rows,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.oracle, args.robustness, events, Path(__file__),
        )},
        "scope": {
            "only_already_observed_blocked_ALL_JOINs": True,
            "future_observed_RETURNs_are_retrospective_labels_only": True,
            "advance_ceiling_holds_all_peer_timestamps_fixed": True,
            "a_noncritical_branch_might_have_indirect_resource_benefit": True,
            "not_a_bound_on_dynamic_system_JCT_gain": True,
            "no_alternative_was_actually_admitted": True,
        },
        "GPU_action": False, "online_policy_changed": False,
    }
    write(args.output, result)
    print(json.dumps({
        "counts": dict(counts), "measured_alternatives": [
            row for row in rows if row["measured_competitor"]
        ],
    }), flush=True)


if __name__ == "__main__":
    main()
