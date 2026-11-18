"""Replay parameter alternatives on sealed candidate sets, not future JCT."""

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources,
)
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from scripts.audit_ready_wait_aging import base_weights
from scripts.audit_workflow_combination_choices import replay_choice
from scripts.train_workflow_work import write


def counterfactual(row, costs, settings):
    bases = base_weights(row)
    candidates = []
    for raw in row["candidates"]:
        value = dict(raw)
        value["missing_extents"] = tuple(
            (tuple(identity) if isinstance(identity, list) else identity, count)
            for identity, count in value["missing_extents"]
        )
        value["weight"] = settings.weight(
            bases[value["workflow"]],
            row["ready_wait_priority"]["workflow_age_ms"][value["workflow"]],
        )
        candidates.append(CombinationCandidate(**value))
    observed = row["decision"]
    cutoff = (
        observed["deadline_checks"]
        if observed["reason"] == "soft_deadline_maximal_reference_fallback" else None
    )
    ticks = -1

    def clock():
        nonlocal ticks
        ticks += 1
        return 500_000 if cutoff is not None and ticks >= cutoff else 0

    result = choose_maximal_admission(
        candidates, observed["reference"], CombinationResources(**row["resources"]), costs,
        required=observed["required_reference_members"],
        selection_objective="benefit", clock=clock,
    )
    if not set(observed["required_reference_members"]) <= set(result["selected"]):
        raise ValueError("a parameter variant dropped a protected JOIN right")
    return result


def selection_counts(row, selected):
    selected = set(selected)
    groups = Counter(
        (value["workflow"], value["group"]) for value in row["candidates"]
        if value["identity"] in selected and value["unfinished_members"] > 1
    )
    return {
        "selected_agents": len(selected),
        "selected_workflows": len({
            value["workflow"] for value in row["candidates"] if value["identity"] in selected
        }),
        "selected_join_groups": len(groups),
        "jointly_selected_sibling_groups": sum(count > 1 for count in groups.values()),
    }


def inspect(rows, profile):
    original = CombinationCosts(**profile["costs"])
    bounded = ReadyWaitSettings(120000)
    linear = replace(original, join_progress="linear")
    variants = {
        "bounded_quadratic": (original, bounded),
        "bounded_linear": (linear, bounded),
        "linear_boost_1": (linear, replace(bounded, max_boost=1.)),
        "linear_boost_1_1": (linear, replace(bounded, max_boost=1.1)),
        "linear_boost_1_5": (linear, replace(bounded, max_boost=1.5)),
        "linear_tie_zero": (replace(linear, relative_tie=0.), bounded),
        "linear_tie_0_1_percent": (replace(linear, relative_tie=.001), bounded),
        "linear_different_unused_cost_horizon": (
            replace(linear, decode_steps=1, epsilon_ms=1000.), bounded,
        ),
    }
    counts = {name: Counter() for name in variants}
    details, total = [], Counter()
    for row in rows:
        actual = replay_choice(
            row, original, profile["identity"],
            allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        )
        if not actual["search_replayed"]:
            continue
        total["exact_actual_searches_replayed"] += 1
        has_join = any(value["unfinished_members"] > 1 for value in row["candidates"])
        total["searches_with_join_candidates"] += has_join
        chosen = {
            name: counterfactual(row, costs, settings)
            for name, (costs, settings) in variants.items()
        }
        baseline = set(chosen["bounded_quadratic"]["selected"])
        linear_baseline = set(chosen["bounded_linear"]["selected"])
        observed = set(row["decision"]["selected"])
        record = {
            "monotonic_s": row["monotonic_s"], "source": row["source"],
            "has_JOIN": has_join, "observed_selected": row["decision"]["selected"],
            "variants": {},
        }
        for name, result in chosen.items():
            selected = set(result["selected"])
            counts[name]["changed_from_observed"] += selected != observed
            counts[name]["changed_from_bounded_quadratic"] += selected != baseline
            counts[name]["changed_from_bounded_linear"] += selected != linear_baseline
            counts[name].update(selection_counts(row, selected))
            record["variants"][name] = {
                "selected": list(result["selected"]), "reason": result["reason"],
            }
        details.append(record)
    return {
        "counts": dict(total),
        "variants": {
            name: {"counts": dict(counts[name]), "costs": asdict(costs), "aging": settings.record()}
            for name, (costs, settings) in variants.items()
        },
        "decisions": details,
        "scope": "fixed_actual_candidates_resources_JOIN_reference_and_search_cutoffs",
        "future_candidate_windows_native_service_and_JCT_not_simulated": True,
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
        raise FileExistsError("preserve earlier sensitivity evidence")
    cache = json.loads(args.choices.read_bytes())
    artifact = json.loads(args.artifact.read_bytes())
    report = inspect(cache["rows"], artifact["admission_cost_profile"])
    paths = [args.choices, args.artifact, Path(__file__)] + [
        ROOT / value for value in (
            "beliefkv/policy/workflow_combination.py",
            "beliefkv/policy/workflow_maximal_admission.py",
            "beliefkv/runtime/ready_wait_priority.py",
            "scripts/audit_ready_wait_aging.py",
            "scripts/audit_workflow_combination_choices.py",
        )
    ]
    report.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in paths},
        original_journal_prefixes=cache["input_jsonl_prefixes"],
    )
    write(args.output_dir / "report.json", report)
    print(json.dumps({
        "output": str(args.output_dir.resolve()), "counts": report["counts"],
        "variants": {name: value["counts"] for name, value in report["variants"].items()},
        "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
