"""Compare aging on exact recorded candidate sets, without simulating JCT."""

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, _evaluate, prediction_weight,
)
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from scripts.audit_workflow_combination_choices import replay_choice
from scripts.train_workflow_work import write


def base_weights(row):
    result = {}
    for candidate in row["candidates"]:
        workflow = candidate["workflow"]
        value = row["predictions"].get(workflow)
        if not isinstance(value, dict) or value.get("evidence") not in ("oracle", "model"):
            raise ValueError("the recorded candidate lacks a supported work prediction")
        weight = prediction_weight(SimpleNamespace(**value))
        if not math.isfinite(weight) or not 0 <= weight <= 1.:
            raise ValueError("invalid recorded base weight")
        result[workflow] = weight
    return result


def compare(row, costs, cost_identity, settings):
    if row.get("selection_policy") != "maximal_benefit":
        raise ValueError("this diagnostic requires the actual maximal benefit selector")
    actual = replay_choice(
        row, costs, cost_identity,
        allow_feasible_resource_alternatives=True, plan_device_capacity=True,
    )
    if not actual["search_replayed"]:
        return None
    base = base_weights(row)
    priority = row["ready_wait_priority"]
    if "kind" in priority:
        old = ReadyWaitSettings(
            priority["scale_ms"], priority["grace_ms"], priority["max_boost"],
        )
        if priority["kind"] != old.record()["kind"]:
            raise ValueError("unknown observed aging rule")
        old_weight = old.weight
    else:
        scale = priority["scale_ms"]
        if type(scale) is not int or scale <= 0:
            raise ValueError("invalid recorded legacy scale")
        old_weight = lambda weight, age: min(1., weight * (1. + age / scale))
    ages = priority["workflow_age_ms"]
    candidates, adjusted = [], []
    for raw in row["candidates"]:
        values = dict(raw)
        values["missing_extents"] = tuple(
            (tuple(identity) if isinstance(identity, list) else identity, tokens)
            for identity, tokens in raw["missing_extents"]
        )
        candidate = CombinationCandidate(**values)
        age = ages[candidate.workflow]
        if type(age) not in (int, float) or not math.isfinite(age) or age < 0:
            raise ValueError("invalid recorded ready wait")
        if not math.isclose(
            old_weight(base[candidate.workflow], age), candidate.weight, rel_tol=1e-12, abs_tol=1e-15,
        ):
            raise ValueError("recorded weight does not match the observed aging rule")
        candidates.append(candidate)
        adjusted.append(replace(candidate, weight=settings.weight(base[candidate.workflow], age)))
    resources = CombinationResources(**row["resources"])
    required = row["decision"]["required_reference_members"]
    ticks = -1
    cutoff = (
        row["decision"]["deadline_checks"]
        if row["decision"]["reason"] == "soft_deadline_maximal_reference_fallback" else None
    )

    def clock():
        nonlocal ticks
        ticks += 1
        return 500_000 if cutoff is not None and ticks >= cutoff else 0

    alternative = choose_maximal_admission(
        adjusted, row["decision"]["reference"], resources, costs,
        required=required, selection_objective="benefit", clock=clock,
    )
    required_rows = [value for value in candidates if value.identity in required]
    feasible = {
        value.identity: _evaluate(
            required_rows + ([value] if value.identity not in required else []), resources, costs,
        ) is not None for value in candidates
    }
    by_id = {value.identity: value for value in adjusted}
    inversions, seen_pairs, exposed, seen_exposed = [], set(), [], set()
    selected = set(row["decision"]["selected"])
    alternative_selected = set(alternative["selected"])
    for long in candidates:
        for short in candidates:
            pair = long.workflow, short.workflow
            if (
                long.workflow == short.workflow
                or base[long.workflow] >= base[short.workflow] / (1. + costs.relative_tie)
                or long.weight <= short.weight * (1. + costs.relative_tie)
            ):
                continue
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                fixed = (
                    by_id[long.identity].weight
                    <= by_id[short.identity].weight * (1. + costs.relative_tie)
                )
                inversions.append({"workflows": pair, "corrected": fixed})
            if (
                pair not in seen_exposed
                and long.identity in selected and short.identity not in selected
                and short.identity not in required and long.identity not in required
                and feasible[long.identity] and feasible[short.identity]
                and _evaluate(
                    required_rows + [long, short], resources, costs,
                ) is None
            ):
                seen_exposed.add(pair)
                exposed.append({
                    "long": long.identity, "short": short.identity,
                    "base_weights": [base[long.workflow], base[short.workflow]],
                    "observed_weights": [long.weight, short.weight],
                    "new_weights": [by_id[long.identity].weight, by_id[short.identity].weight],
                    "wait_ms": [ages[long.workflow], ages[short.workflow]],
                    "short_selected_after": short.identity in alternative_selected,
                    "remaining_work": [
                        row["predictions"][workflow].get("remaining_work")
                        for workflow in (long.workflow, short.workflow)
                    ],
                })
    return {
        "monotonic_s": row["monotonic_s"], "source": row["source"],
        "observed_selected": list(row["decision"]["selected"]),
        "alternative_selected": list(alternative["selected"]),
        "selected_set_changed": selected != alternative_selected,
        "workflow_pairs_inverted_by_observed_aging": len(inversions),
        "workflow_pairs_corrected": sum(value["corrected"] for value in inversions),
        "exposed_competitive_pairs": exposed,
        "legacy_max_multiplier": max(
            value.weight / base[value.workflow]
            for value in candidates if base[value.workflow] > 0
        ) if any(base.values()) else 1.,
    }


def read_choices(evidence):
    journals = [
        (Path(name), bound) for name, bound in evidence["input_jsonl_prefixes"].items()
        if name.endswith("/opportunities/admission_opportunities.jsonl")
    ]
    if len(journals) != 1:
        raise ValueError("one exact sealed admission journal is required")
    path, bound = journals[0]
    digest, consumed, rows = hashlib.sha256(), 0, []
    with path.open("rb") as source:
        while consumed < bound["complete_prefix_bytes"]:
            line = source.readline()
            if not line or not line.endswith(b"\n"):
                raise ValueError("the recorded complete journal prefix is unavailable")
            consumed += len(line)
            if consumed > bound["complete_prefix_bytes"]:
                raise ValueError("the sealed prefix does not end at a complete record")
            digest.update(line)
            row = json.loads(line)
            if (
                row.get("event") == "workflow_combination_selection"
                and row.get("candidates") and "evaluated_subsets" in row["decision"]
            ):
                rows.append(row)
    if digest.hexdigest() != bound["complete_prefix_sha256"]:
        raise ValueError("the sealed journal bytes changed")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scale-ms", type=int, default=120000)
    parser.add_argument("--grace-ms", type=int, default=60000)
    parser.add_argument("--max-boost", type=float, default=1.25)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("use CPU0, nice19 and hidden CUDA")
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier evidence")
    settings = ReadyWaitSettings(args.scale_ms, args.grace_ms, args.max_boost)
    evidence = json.loads(args.evidence.read_bytes())
    artifact = json.loads(args.artifact.read_bytes())
    rows = read_choices(evidence)
    profile = artifact["admission_cost_profile"]
    costs = CombinationCosts(**profile["costs"])
    choices, counts = [], Counter()
    for row in rows:
        value = compare(row, costs, profile["identity"], settings)
        if value is None:
            counts["unsearched_rows"] += 1
            continue
        choices.append(value)
        counts["same_state_choices"] += 1
        counts["changed_sets"] += value["selected_set_changed"]
        counts["decisions_with_aging_inversion"] += value["workflow_pairs_inverted_by_observed_aging"] > 0
        counts["inverted_workflow_pairs"] += value["workflow_pairs_inverted_by_observed_aging"]
        counts["corrected_workflow_pairs"] += value["workflow_pairs_corrected"]
        counts["exposed_competitive_pairs"] += len(value["exposed_competitive_pairs"])
        counts["exposed_short_selected_after"] += sum(
            pair["short_selected_after"] for pair in value["exposed_competitive_pairs"]
        )
    samples = [
        (raw["predictions"][candidate["workflow"]]["effective_weight"],
         raw["ready_wait_priority"]["workflow_age_ms"][candidate["workflow"]])
        for raw in rows for candidate in raw["candidates"]
        if raw["predictions"][candidate["workflow"]].get("effective_weight") is not None
    ]
    timings = []
    for _ in range(20):
        started = time.thread_time_ns()
        for base, age in samples:
            settings.weight(base, age)
        timings.append((time.thread_time_ns() - started) / max(1, len(samples)))
    args.output_dir.mkdir(parents=True)
    inputs = [args.evidence, args.artifact, Path(__file__)] + [
        ROOT / name for name in (
            "beliefkv/runtime/ready_wait_priority.py",
            "beliefkv/policy/workflow_combination.py",
            "beliefkv/policy/workflow_maximal_admission.py",
            "scripts/audit_workflow_combination_choices.py",
        )
    ]
    write(args.output_dir / "observed_choices.json", {
        "scope": "sealed_actual_candidates_resources_JOIN_and_cutoffs",
        "input_jsonl_prefixes": evidence["input_jsonl_prefixes"], "rows": rows,
    })
    write(args.output_dir / "report.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings.record(), "counts": dict(counts),
        "observed_max_multiplier": max((row["legacy_max_multiplier"] for row in choices), default=1.),
        "weight_call_median_thread_cpu_ns": statistics.median(timings),
        "choices": choices,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "scope": "fixed_actual_candidate_windows;not_full_policy_or_JCT_simulation",
        "candidate_window_changes_and_future_service_not_simulated": True,
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({"counts": dict(counts), "output": str(args.output_dir.resolve()), "GPU_action": False}))


if __name__ == "__main__":
    main()
