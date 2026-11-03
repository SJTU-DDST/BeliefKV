"""Check the deployed benefit selector against sealed denominator-audit choices."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, choose_combination,
)
from scripts.train_workflow_work import write


def inspect(report, profile):
    decisions = {row["raw_journal_sequence"]: row for row in report["decisions"]}
    if len(decisions) != len(report["decisions"]):
        raise ValueError("duplicate sealed decision identity")
    matches = [
        (Path(path), prefix) for path, prefix in report["input_jsonl_prefixes"].items()
        if path.endswith("/opportunities/admission_opportunities.jsonl")
    ]
    if len(matches) != 1:
        raise ValueError("require one exact sealed admission journal")
    path, prefix = matches[0]
    if (
        path.stat().st_size != prefix["complete_prefix_bytes"]
        or sha256_file(path) != prefix["complete_prefix_sha256"]
    ):
        raise ValueError("sealed admission history changed")
    costs = CombinationCosts(**profile["costs"])
    if costs.selection_objective != "benefit_per_cost":
        raise ValueError("historical parity requires the original ratio cost profile")
    checked, changed = set(), 0
    with path.open() as handle:
        for sequence, line in enumerate(handle):
            if sequence not in decisions:
                continue
            expected, row = decisions[sequence], json.loads(line)
            if (
                row["cost_identity"] != profile["identity"]
                or row["monotonic_s"] != expected["monotonic_s"]
                or row["source"] != expected["source"] or not row["decision"]["search_complete"]
            ):
                raise ValueError("denominator audit belongs to another decision or incomplete search")
            candidates = []
            for value in row["candidates"]:
                fields = dict(value)
                fields["missing_extents"] = tuple(
                    (tuple(identity) if isinstance(identity, list) else identity, tokens)
                    for identity, tokens in fields["missing_extents"]
                )
                candidates.append(CombinationCandidate(**fields))
            outputs = {}
            for objective in ("benefit_per_cost", "benefit"):
                outputs[objective] = choose_combination(
                    candidates, row["decision"]["reference"], CombinationResources(**row["resources"]),
                    replace(costs, selection_objective=objective), required=expected["required_JOIN_members"],
                    clock=lambda: 0, allow_feasible_resource_alternatives=True,
                )
            cached = expected["selections"]["cached"]
            if (
                list(outputs["benefit_per_cost"]["selected"]) != cached["ratio_selected"]
                or list(outputs["benefit"]["selected"]) != cached["benefit_only_selected"]
                or list(outputs["benefit_per_cost"]["selected"]) != row["decision"]["selected"]
            ):
                raise ValueError("production selection differs from the sealed ratio or benefit-only audit")
            checked.add(sequence)
            changed += set(outputs["benefit"]["selected"]) != set(outputs["benefit_per_cost"]["selected"])
    if checked != decisions.keys() or changed != report["objective_comparisons"]["cached"]["changed_sets"]:
        raise ValueError("incomplete historical selector coverage")
    return {
        "complete_searches": len(checked), "changed_sets": changed,
        "ratio_choices_exactly_preserved": True,
        "benefit_choices_match_independent_offline_audit": True,
        "journal": str(path), "sealed_prefix": prefix,
        "fixed_history_not_a_service_or_JCT_counterfactual": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audits", type=Path, nargs="+", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous deployed-selector parity evidence")
    pins = {str(path.resolve()): sha256_file(path) for path in (
        *args.audits, args.model, Path(__file__), ROOT / "beliefkv/policy/workflow_combination.py",
    )}
    profile = json.loads(args.model.read_bytes())["admission_cost_profile"]
    reports = {str(path.resolve()): inspect(json.loads(path.read_bytes()), profile) for path in args.audits}
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a historical parity input changed")
    write(args.output, {
        "reports": reports, "inputs_sha256": pins, "GPU_action": False,
        "online_deadline_coverage_or_JCT_gain_proven": False,
    })
    print(json.dumps(reports), flush=True)


if __name__ == "__main__":
    main()
