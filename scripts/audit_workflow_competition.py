"""Audit measured short/long recovery competition on a complete fixed workload."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import capacity_competition, population_fingerprint
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import EVENT, inspect, inspect_choice
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


def inspect_competition(records, population):
    by_workflow = {row["workflow_id"]: row for row in population}
    if (
        len(by_workflow) != len(population)
        or len({row["project"] for row in population}) != len(population)
        or any(row.get("eligible") is not True for row in population)
    ):
        raise ValueError("competition requires complete, project-disjoint workflows")
    counts, episodes = Counter(), []
    buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
    for row in records:
        if row.get("event") != EVENT:
            continue
        comparison = inspect_choice(row)
        counts["controls"] += 1
        if not row["ordered"]:
            counts["empty_candidate_controls"] += 1
            continue
        counts["nonempty_controls"] += 1
        ready = row.get("logical_ready_paused_before_resume")
        if ready is None:
            counts["controls_without_readiness_snapshot"] += 1
            continue
        owners = {item[0] for item in row["ordered"]}
        if ready.keys() != owners or any(type(value) is not bool for value in ready.values()):
            raise ValueError("readiness must describe every paused candidate before grant")
        metadata = {item["key"]["invocation_id"]: item for item in row["candidates"]}
        candidates = []
        for owner, category, _rank, tokens, sequence, _group in row["ordered"]:
            workflow = metadata[owner]["key"]["root_workflow_id"]
            if workflow not in by_workflow:
                raise ValueError("recovery candidate is outside the measured population")
            candidates.append({
                "owner": owner, "workflow_id": workflow, "category": category,
                "tokens": tokens, "sequence": sequence, "ready_paused": ready[owner],
                "size_tier": by_workflow[workflow]["size_tier"],
            })
        opportunities = capacity_competition(
            candidates, capacity=row["capacity_tokens"], buffer_tokens=buffer,
        )
        counts["controls_with_ready_short_long_capacity_competition"] += bool(opportunities)
        counts["competing_controls_with_changed_selected_set"] += (
            bool(opportunities) and comparison["selected_set_changed"]
        )
        for opportunity in opportunities:
            episodes.append({
                "monotonic_s": row["monotonic_s"], **opportunity,
                "selected_set_changed_from_local_reference": comparison["selected_set_changed"],
                "actual_selected": row["selected_in_priority_order"],
                "selected_after_pressure": [
                    owner for owner in row["selected_in_priority_order"]
                    if metadata[owner].get("active_after_pressure") is True
                ],
            })
    return {
        "counts": dict(counts), "episodes": episodes,
        "readiness_coverage_complete": bool(counts["nonempty_controls"]) and not counts["controls_without_readiness_snapshot"],
        "scope": {
            "size_tiers": "complete_initial_work_not_remaining_path_or_service_seconds",
            "same_category_and_reference_budget_after_higher_categories": True,
            "unready_tool_and_JOIN_waiters_excluded_from_competing_set": True,
            "logical_fit_is_not_physical_HBM_fit": True,
            "native_fit_counterfactual_verified": False,
            "local_selected_set_change_is_not_causal_JCT_gain": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve the earlier competition audit")
    plan = causal.load(args.registration)
    if "full" not in plan["order"]:
        raise ValueError("this reader requires the explicit Full workflow-rank journal")
    work = causal.predecessor_work({**plan, "after_registration": str(args.registration)})
    population = causal.load(args.population)
    rows = population["workflows"]
    if (
        population["population_sha256"] != population_fingerprint(rows)
        or {row["workflow_id"] for row in rows} != set(plan["workflow_ids"])
        or any(
            Path(row["source_capture"]).resolve() != Path(plan["source_arm"]).resolve()
            or row["token_source_sha256"] != plan["token_source_sha256"]
            for row in rows
        )
        or len(work) != plan["source_plan"]["requests"]
    ):
        raise ValueError("measured population and complete comparison sources differ")
    arm = Path(plan["run_root"]) / "full"
    cleanup, drain = (
        causal.load(arm / name) for name in ("owned_process_cleanup.json", "telemetry_drain.json")
    )
    if (
        cleanup["remaining_processes"]
        or any(causal.reservation.alive(item) for item in cleanup["identities"])
        or not drain["previous_zero_snapshot_ts_ms"] < drain["drained_status"]["snapshot_ts_ms"]
    ):
        raise ValueError("require actual owned exit and fresh telemetry drain")
    reader = SealedRecords()
    choices = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    result = inspect_competition(choices, rows)
    result["selection_and_following_native_service"] = inspect(
        choices, reader(arm / "server/runtime_audit.jsonl"),
    )
    result.update(
        population_sha256=population["population_sha256"],
        inputs_sha256={
            str(path): sha256_file(path) for path in (args.registration, args.population, Path(__file__))
        },
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
