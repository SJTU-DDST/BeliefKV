"""Audit feasible short-first and JOIN group choices at sealed R5 boundaries."""

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, _evaluate,
)
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.stop_after_measured_cohort import replay_arm
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def candidate(raw):
    value = dict(raw)
    value["missing_extents"] = tuple(
        (tuple(identity) if isinstance(identity, list) else identity, tokens)
        for identity, tokens in value["missing_extents"]
    )
    return CombinationCandidate(**value)


def inspect_choice(row, costs):
    decision = row["decision"]
    rows = [candidate(value) for value in row["candidates"]]
    selected, required = set(decision["selected"]), set(decision.get("required_reference_members", ()))
    by_id = {value.identity: value for value in rows}
    result = {
        "monotonic_s": row["monotonic_s"], "source": row["source"],
        "reason": decision["reason"], "candidates": len(rows),
        "selected": list(decision["selected"]), "short_priority_inversions": [],
        "feasible_incomplete_sibling_groups": [], "class_split_sibling_groups": [],
        "forecast_mean_order_inversions": [],
        "metadata_complete": selected <= by_id.keys() and required <= by_id.keys(),
    }
    if row["resources"] is None or not result["metadata_complete"]:
        return result
    resources = CombinationResources(**row["resources"])
    for shorter in rows:
        if shorter.identity in selected:
            continue
        earlier = [
            peer for peer in rows if peer.identity in selected and (
                peer.identity in required or peer.weight >= shorter.weight
            )
        ]
        if _evaluate([*earlier, shorter], resources, costs) is None:
            continue
        for longer in rows:
            if (
                longer.identity in selected - required and longer.workflow != shorter.workflow
                and longer.weight < shorter.weight
            ):
                result["short_priority_inversions"].append([shorter.identity, longer.identity])
    groups = defaultdict(list)
    for value in rows:
        if value.unfinished_members > 1:
            groups[value.workflow, value.group].append(value)
    for (workflow, group), peers in groups.items():
        omitted = [peer.identity for peer in peers if peer.identity not in selected]
        if not omitted or not any(peer.identity in selected for peer in peers):
            continue
        if len({peer.category for peer in peers}) > 1:
            result["class_split_sibling_groups"].append(group)
        anchors = [
            peer for peer in rows if peer.identity in selected and (
                peer.identity in required or peer.weight > peers[0].weight
            ) and peer not in peers
        ]
        if _evaluate([*anchors, *peers], resources, costs) is not None:
            result["feasible_incomplete_sibling_groups"].append({
                "workflow": workflow, "group": group, "omitted": omitted,
                "ready_members_in_window": len(peers),
            })
    forecasts = row.get("predictions", {})
    for left in rows:
        first = forecasts.get(left.workflow) or {}
        a = first.get("log1p_remaining_work")
        if first.get("evidence") != "model" or type(a) not in (int, float) or not math.isfinite(a):
            continue
        for right in rows:
            second = forecasts.get(right.workflow) or {}
            b = second.get("log1p_remaining_work")
            if (
                left.workflow != right.workflow and second.get("evidence") == "model"
                and type(b) in (int, float) and math.isfinite(b) and a < b
                and left.weight < right.weight
            ):
                result["forecast_mean_order_inversions"].append([left.identity, right.identity])
    replay = choose_shortest_work_first(
        rows, decision.get("reference", ()), resources, costs, required=required,
        strict_remaining_work=True, clock=lambda: 0,
    )
    result["unlimited_CPU_choice"] = list(replay["selected"])
    result["unlimited_CPU_choice_changed"] = set(replay["selected"]) != selected
    result["selection_was_complete"] = decision.get("search_complete") is True
    return result


def totals(rows):
    counts = Counter({"boundaries": len(rows)})
    reasons = Counter()
    for row in rows:
        counts["nonempty"] += row["candidates"] > 0
        counts["fully_described_selections"] += row["metadata_complete"]
        for name in (
            "short_priority_inversions", "feasible_incomplete_sibling_groups",
            "class_split_sibling_groups", "forecast_mean_order_inversions",
        ):
            counts[name + "_boundaries"] += bool(row[name])
            counts[name + "_items"] += len(row[name])
        counts["unlimited_CPU_choice_changed"] += row.get("unlimited_CPU_choice_changed", False)
        reasons[row["reason"]] += 1
    return {"counts": dict(counts), "reasons": dict(reasons)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("require a fresh CPU-only output")
    plan = json.loads(args.registration.read_bytes())
    proof = verify_execution(args.registration, args.cohort)
    config = json.loads(Path(plan["experiment_configuration"]).read_bytes())
    path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    costs = CombinationCosts(**raw["admission_cost_profile"]["costs"])
    reader, decisions = SealedRecords(), []
    source = replay_arm(plan) / "opportunities/admission_opportunities.jsonl"
    for row in reader(source):
        if row.get("event") == "workflow_combination_selection":
            decisions.append(inspect_choice(row, costs))
    result = {
        **totals(decisions), "decisions": decisions,
        "complete_measured_requests": proof["measured_requests"],
        "complete_measured_outputs": proof["measured_output_tokens"],
        "registration": str(args.registration), "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(value.resolve()): sha256_file(value) for value in (
                args.registration, args.cohort, path, Path(__file__),
                ROOT / "beliefkv/policy/workflow_shortest_frontier.py",
            )
        },
        "scope": {
            "recorded_candidate_window_and_planning_resources_only": True,
            "required_JOIN_rights_preserved": True,
            "counts_are_boundaries_not_independent_workflows": True,
            "same_state_alternatives_are_not_native_capacity_or_JCT_certificates": True,
            "missing_metadata_is_unknown_not_a_violation": True,
        },
        "GPU_action": False,
    }
    write(args.output, result)
    print(json.dumps({**totals(decisions), "output": str(args.output)}))


if __name__ == "__main__":
    main()
