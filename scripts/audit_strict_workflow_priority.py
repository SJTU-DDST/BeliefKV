"""Compare short-priority departures on sealed choices, without simulating JCT."""

import argparse
from collections import Counter
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, _evaluate,
)
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from scripts.audit_ready_wait_aging import base_weights
from scripts.audit_workflow_combination_choices import replay_choice
from scripts.train_workflow_work import write


def inversions(candidates, selected, required, resources, costs):
    selected, required = set(selected), set(required)
    result = []
    for short in candidates:
        if short.identity in selected:
            continue
        higher = [
            row for row in candidates if row.identity in selected
            and (row.identity in required or row.weight >= short.weight)
        ]
        if _evaluate([*higher, short], resources, costs) is None:
            continue
        for long in candidates:
            if (
                long.identity in selected - required
                and long.workflow != short.workflow and long.weight < short.weight
            ):
                result.append({
                    "short": short.identity, "long": long.identity,
                    "short_weight": short.weight, "long_weight": long.weight,
                    "short_fits_with_required_and_selected_higher_priority": True,
                })
    return result


def compare(rows, profile):
    old_costs = CombinationCosts(**profile["costs"])
    linear = replace(old_costs, join_progress="linear")
    counts, details, examples = Counter(), [], []
    variants = {
        name: Counter() for name in (
            "observed", "no_aging_quadratic_utility", "no_aging_linear_utility", "strict_remaining_work",
        )
    }
    for row in rows:
        actual = replay_choice(
            row, old_costs, profile["identity"],
            allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        )
        if not actual["search_replayed"]:
            continue
        counts["exact_actual_searches_replayed"] += 1
        if not actual["search_complete"]:
            counts["actual_incomplete_searches_excluded_from_alternative_selection"] += 1
            continue
        counts["complete_same_state_choices_compared"] += 1
        weights = base_weights(row)
        candidates = []
        for raw in row["candidates"]:
            value = dict(raw)
            value["missing_extents"] = tuple(
                (tuple(identity) if isinstance(identity, list) else identity, count)
                for identity, count in value["missing_extents"]
            )
            value["weight"] = weights[value["workflow"]]
            candidates.append(CombinationCandidate(**value))
        resources = CombinationResources(**row["resources"])
        reference = row["decision"]["reference"]
        required = row["decision"]["required_reference_members"]
        selected = {"observed": row["decision"]["selected"]}
        for name, costs in (
            ("no_aging_quadratic_utility", old_costs), ("no_aging_linear_utility", linear),
        ):
            decision = choose_maximal_admission(
                candidates, reference, resources, costs, required=required,
                selection_objective="benefit", clock=lambda: 0,
            )
            if not decision["search_complete"]:
                raise ValueError("complete fixed-state utility comparison unexpectedly fell back")
            selected[name] = decision["selected"]
        strict = choose_shortest_work_first(
            candidates, reference, resources, linear, required=required,
            strict_remaining_work=True, clock=lambda: 0,
        )
        if not strict["search_complete"]:
            raise ValueError("complete fixed-state priority comparison unexpectedly fell back")
        selected["strict_remaining_work"] = strict["selected"]
        decisions = {}
        for name, owners in selected.items():
            if not set(required) <= set(owners):
                raise ValueError("alternative dropped a protected JOIN right")
            pairs = inversions(candidates, owners, required, resources, linear)
            variants[name]["choices_with_feasible_prefix_priority_inversion"] += bool(pairs)
            variants[name]["feasible_prefix_priority_inversion_pairs"] += len(pairs)
            variants[name]["changed_from_observed"] += set(owners) != set(selected["observed"])
            variants[name]["selected_agents"] += len(owners)
            decisions[name] = {"selected": list(owners), "priority_inversion_pairs": len(pairs)}
            if name == "strict_remaining_work" and pairs:
                raise ValueError("strict ordering skipped a fitting higher-priority candidate")
            if name == "observed" and pairs and len(examples) < 8:
                examples.append({"monotonic_s": row["monotonic_s"], **pairs[0]})
        details.append({
            "monotonic_s": row["monotonic_s"], "source": row["source"], "variants": decisions,
        })
    return {
        "counts": dict(counts), "variants": {name: dict(value) for name, value in variants.items()},
        "examples": examples, "decisions": details,
        "scope": "same_actual_candidates_resources_reference_and_required_JOIN;complete_searches_only",
        "new_shortlists_queue_native_service_migration_or_JCT_not_simulated": True,
        "alternative_clock_is_unbounded_for_policy_comparison_not_runtime_qualification": True,
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--choices", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("use CPU0/nice19 and hidden CUDA")
    if args.output_dir.exists():
        raise FileExistsError("preserve previous evidence")
    cache = json.loads(args.choices.read_bytes())
    artifact = json.loads(args.artifact.read_bytes())
    report = compare(cache["rows"], artifact["admission_cost_profile"])
    inputs = [args.choices, args.artifact, Path(__file__)] + [
        ROOT / name for name in (
            "beliefkv/policy/workflow_shortest_frontier.py",
            "beliefkv/policy/workflow_combination.py",
            "beliefkv/policy/workflow_maximal_admission.py",
            "scripts/audit_workflow_combination_choices.py", "scripts/audit_ready_wait_aging.py",
        )
    ]
    report.update(
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in inputs},
        original_journal_prefixes=cache["input_jsonl_prefixes"],
    )
    write(args.output_dir / "report.json", report)
    print(json.dumps({"counts": report["counts"], "variants": report["variants"], "GPU_action": False}))


if __name__ == "__main__":
    main()
