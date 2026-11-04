"""Find recorded capacity choices where a JOIN branch value could change a set."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts, CombinationResources, _evaluate
from scripts.audit_learned_combination_prediction import combination_candidates
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, replay_choice
from scripts.train_workflow_work import write


def feasible_frontier(candidates, resources, costs, required=()):
    required = set(required)
    identities = {row.identity for row in candidates}
    if not required <= identities:
        raise ValueError("required JOIN member absent from candidates")
    feasible = []
    for mask in range(1, 1 << len(candidates)):
        rows = [row for index, row in enumerate(candidates) if mask & (1 << index)]
        members = frozenset(row.identity for row in rows)
        if required <= members and _evaluate(rows, resources, costs) is not None:
            feasible.append(members)
    maximal = [
        selected for selected in feasible
        if not any(selected < other for other in feasible)
    ]
    return feasible, maximal


def inspect(rows, costs, profile, measured):
    counts, details = Counter(), []
    for sequence, row in enumerate(rows):
        replayed = replay_choice(
            row, costs, profile["identity"],
            allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        )
        counts["combination_entries"] += 1
        if not replayed["search_replayed"]:
            counts["metadata_or_no_search_entries"] += 1
            continue
        if not replayed["search_complete"]:
            counts["incomplete_searches_excluded"] += 1
            continue
        counts["complete_replayed_searches"] += 1
        candidates = combination_candidates(row)
        groups = defaultdict(list)
        for candidate in candidates:
            if not candidate.group.startswith("single:") and candidate.unfinished_members > 1:
                groups[candidate.group].append(candidate)
        if not groups:
            continue
        counts["searches_with_unfinished_multi_member_JOIN"] += 1
        counts["searches_with_two_or_more_same_JOIN_candidates"] += any(
            len(members) >= 2 for members in groups.values()
        )
        resources = CombinationResources(**row["resources"])
        if _evaluate(candidates, resources, costs) is not None:
            counts["JOIN_searches_where_all_candidates_fit_planning_bounds"] += 1
            continue
        required = set(row["decision"]["required_reference_members"])
        feasible, maximal = feasible_frontier(candidates, resources, costs, required)
        actual = set(row["decision"]["selected"])
        if _evaluate([value for value in candidates if value.identity in actual], resources, costs) is None:
            counts["JOIN_capacity_search_with_unsupported_actual_reference"] += 1
            continue
        changed_groups, same_cardinality_groups = [], []
        for group, members in groups.items():
            identities = {member.identity for member in members}
            actual_members = actual & identities
            alternatives = {
                tuple(sorted(selected & identities)) for selected in maximal
                if selected & identities != actual_members
            }
            same_count = {
                tuple(sorted(selected & identities)) for selected in maximal
                if len(selected) == len(actual) and selected & identities != actual_members
            }
            if alternatives:
                changed_groups.append({
                    "group": group, "workflow_id": members[0].workflow,
                    "unfinished_members": members[0].unfinished_members,
                    "active_ready_members": members[0].active_ready_members,
                    "candidate_members": sorted(identities), "actual_members": sorted(actual_members),
                    "alternative_JOIN_member_sets": sorted(alternatives),
                    "measured_workflow": members[0].workflow in measured,
                })
            if same_count:
                same_cardinality_groups.append(group)
        counts["JOIN_capacity_searches"] += 1
        counts["JOIN_capacity_searches_with_alternative_maximal_set"] += bool(changed_groups)
        counts["JOIN_capacity_searches_with_same_cardinality_alternative"] += bool(same_cardinality_groups)
        counts["JOIN_capacity_searches_with_measured_alternative"] += any(
            group["measured_workflow"] for group in changed_groups
        )
        details.append({
            "selection_sequence": sequence, "monotonic_s": row["monotonic_s"],
            "source": row["source"], "actual_selected": sorted(actual),
            "required_JOIN_members": sorted(required),
            "feasible_subsets": len(feasible), "maximal_feasible_sets": [
                sorted(value) for value in maximal
            ],
            "changed_JOIN_groups": changed_groups,
            "same_cardinality_alternative_groups": same_cardinality_groups,
            "resources": row["resources"],
            "candidate_metadata": row["candidates"], "keys": row["keys"],
        })
    return {"counts": dict(counts), "capacity_choices": details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only execution")
    registration = json.loads(args.registration.read_bytes())
    config_path = Path(registration["experiment_configuration"])
    config = json.loads(config_path.read_bytes())
    model_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(model_path.read_bytes())
    profile = raw["admission_cost_profile"]
    if profile["kind"] != "r5_frozen_batch_cost_proxy_capacity_v7":
        parser.error("this audit explicitly targets the current benefit-only v7 candidate")
    policy = ROOT / "beliefkv/policy/workflow_combination.py"
    if sha256_file(policy) != registration["source_sha256"].get("beliefkv/policy/workflow_combination.py"):
        raise ValueError("audit must use the actual serving selection policy")
    inputs = {
        str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.cohort, config_path, model_path, policy,
            Path(__file__), ROOT / "scripts/audit_workflow_combination_choices.py",
            ROOT / "scripts/audit_learned_combination_prediction.py",
        )
    }
    reader = SealedRecords()
    rows = [
        row for row in reader(Path(registration["run_root"]) / "full/opportunities/admission_opportunities.jsonl")
        if row.get("event") == EVENT
    ]
    result = inspect(
        rows, CombinationCosts(**profile["costs"]), profile,
        set(json.loads(args.cohort.read_bytes())["measured_workflow_ids"]),
    )
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("immutable audit input changed during execution")
    result.update(
        observed_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        inputs_sha256=inputs, input_jsonl_prefixes=reader.prefixes,
        GPU_action=False, automatic_GPU_followup=False, performance_accepted=False,
        scope=[
            "Every retained actual decision first reproduces its recorded bounded search.",
            "Metadata fallbacks and incomplete searches remain excluded from alternative-set claims.",
            "Alternative sets keep actual capacity, slots, growth, cost profile and required JOIN rights.",
            "Only JOIN groups with at least two unfinished members are screened.",
            "All-candidate-fit states are excluded from capacity-selection opportunities.",
            "Maximal means no feasible strict superset in the enumerated candidate universe.",
            "No branch oracle, predicted benefit or downstream JCT effect is inferred from the existence of alternatives.",
            "The full-pool planning bound is not proof of immediate native allocation or physical residency.",
            "The sealed prefix includes warmup/background work and may precede fixed measured completion.",
        ],
    )
    write(args.output, result)
    print(json.dumps({"counts": result["counts"], "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
