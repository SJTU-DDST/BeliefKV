#!/usr/bin/env python3
"""Check observed delegation before spending GPU time on a matched native arm."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.delegation_policy import NESTED_IN_GRAPH_PROFILE, delegation_contract
from scripts.audit_sealed_wait_prefetch import SealedRecords


def workflow_shape(path: Path, sealed: SealedRecords) -> dict:
    rows = list(sealed(path))
    roots = {
        row["invocation_id"] for row in rows
        if row.get("kind") == "invocation_create"
        and row.get("relation_type", "root") == "root"
        and not row.get("parent_invocation_id")
        and not (row.get("attributes") or {}).get("runtime_internal")
    }
    joins = {row["join_id"]: row for row in rows if row.get("kind") == "join_create"}
    root_joins = list(dict.fromkeys(
        row["join_id"] for row in rows
        if row.get("kind") == "join_wait" and row.get("invocation_id") in roots
    ))
    spawns = [
        row for row in rows if row.get("kind") == "spawn"
        and not (row.get("attributes") or {}).get("runtime_internal")
    ]
    depths = {root: 0 for root in roots}
    unresolved = {
        row["target_invocation_id"]: row["invocation_id"] for row in spawns
    }
    while unresolved:
        resolved = [child for child, parent in unresolved.items() if parent in depths]
        if not resolved:
            break
        for child in resolved:
            depths[child] = depths[unresolved.pop(child)] + 1
    fanouts = [
        len(joins[join]["member_invocation_ids"]) if join in joins else None
        for join in root_joins
    ]
    result_path = path.parent / "result.json"
    result = json.loads(result_path.read_bytes()) if result_path.exists() else None
    observed = {
        "root_delegation_rounds": len(root_joins),
        "dynamic_children": len(spawns),
        "nested_spawns": sum(depth >= 2 for depth in depths.values()),
        "maximum_spawn_depth": max(depths.values(), default=0),
    }
    matches = result is None or all(
        result["trace"].get(key) == observed[value] for key, value in (
            ("root_delegation_round_count", "root_delegation_rounds"),
            ("dynamic_subagent_count", "dynamic_children"),
            ("nested_spawn_count", "nested_spawns"),
            ("maximum_spawn_depth", "maximum_spawn_depth"),
        )
    )
    return {
        "instance_id": path.parent.name, **observed,
        "root_round_fanouts": fanouts,
        "initial_root_fanout": fanouts[0] if fanouts else None,
        "trace_valid": len(roots) == 1 and not unresolved
        and all(fanout is not None for fanout in fanouts) and matches,
        "terminal_outcome": result["outcome"] if result is not None else None,
    }


def audit(run_root: Path, arm: str = "predictive_h2d") -> dict:
    plan = json.loads((run_root / "ab_plan.json").read_bytes())
    expected = plan["workload_instance_ids_in_manifest_order"]
    if len(expected) != len(set(expected)) or len(expected) != plan["root_count"]:
        raise ValueError("the frozen workload identities are not unique and complete")
    client = run_root / arm / f"client_{plan['root_count']}"
    manifest_path = client / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes()) if manifest_path.exists() else {}
    sealed = SealedRecords()
    workflows = [
        workflow_shape(path, sealed) for path in sorted(
            (client / "workflows").glob("*/runtime_events.deepagents.jsonl"),
        )
    ]
    outcomes = Counter(
        row["terminal_outcome"] for row in workflows if row["terminal_outcome"] is not None
    )
    contract = delegation_contract(NESTED_IN_GRAPH_PROFILE)
    checks = {
        "restored_profile_and_contract": (
            plan.get("fanout_profile") == NESTED_IN_GRAPH_PROFILE
            and plan.get("delegation_contract") == contract
            and manifest.get("config", {}).get("subagent_fanout_profile") == NESTED_IN_GRAPH_PROFILE
            and manifest.get("delegation_contract") == contract
        ),
        "all_expected_workflows_observed": (
            {row["instance_id"] for row in workflows} == set(expected)
        ),
        "valid_trace_identities_and_summary_counts": (
            bool(workflows) and all(row["trace_valid"] for row in workflows)
        ),
        "every_root_has_delegated": (
            len(workflows) == len(expected)
            and all(row["root_delegation_rounds"] >= 1 for row in workflows)
        ),
        "initial_root_fanout_in_2_to_4": (
            len(workflows) == len(expected)
            and all(row["initial_root_fanout"] in (2, 3, 4) for row in workflows)
        ),
        "multiple_root_rounds_observed": any(
            row["root_delegation_rounds"] >= 2 for row in workflows
        ),
        "nested_spawn_observed": any(row["nested_spawns"] > 0 for row in workflows),
        "maximum_spawn_depth_within_contract": (
            bool(workflows)
            and max(row["maximum_spawn_depth"] for row in workflows) <= 2
        ),
        "terminal_results_complete": (
            len(workflows) == len(expected) and sum(outcomes.values()) == len(expected)
        ),
    }
    return {
        "status": "qualified" if all(checks.values()) else "not_qualified",
        "run_root": str(run_root), "arm": arm, "code_commit": plan["code_commit"],
        "checks": checks, "terminal_outcomes": dict(outcomes),
        "roots_with_delegation": sum(row["root_delegation_rounds"] > 0 for row in workflows),
        "roots_with_multiple_rounds": sum(row["root_delegation_rounds"] > 1 for row in workflows),
        "dynamic_children": sum(row["dynamic_children"] for row in workflows),
        "nested_spawns": sum(row["nested_spawns"] for row in workflows),
        "workflows": workflows, "input_jsonl_prefixes": sealed.prefixes,
        "scope": (
            "Offline workload-shape qualification only, not task quality, natural-return "
            "qualification, useful-prefetch volume, or performance acceptance. Later-round "
            "fanout and per-root round counts remain observations, not runtime RETURN gates."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--arm", default="predictive_h2d")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-qualified", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve the existing observation; choose a new output")
    result = audit(args.run_root, args.arm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "status", "checks", "terminal_outcomes", "roots_with_delegation",
        "roots_with_multiple_rounds", "dynamic_children", "nested_spawns",
    )}, indent=2))
    if args.require_qualified and result["status"] != "qualified":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
