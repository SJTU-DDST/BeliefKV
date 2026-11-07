"""Replay a shortest-frontier CPU candidate on complete registered R5 states."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationResources, _evaluate, choose_combination
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_frontier
from scripts.audit_learned_combination_prediction import combination_candidates, planning_costs
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, replay_choice


def summarize(decisions):
    result = {}
    for population, rows in (
        ("complete_searches", decisions),
        ("ordinary_capacity_competitions", [row for row in decisions if row["planning_capacity_competition"]]),
    ):
        counts = Counter({"boundaries": len(rows)})
        for row in rows:
            counts["changed_sets"] += row["set_changed_from_actual"]
            counts["changes_touch_measured"] += row["changes_touch_measured"]
            counts["fewer_selected_agents"] += row["set_changed_from_actual"] and row["selected_count_delta"] < 0
            counts["more_selected_agents"] += row["set_changed_from_actual"] and row["selected_count_delta"] > 0
            counts["same_count_changed_sets"] += row["set_changed_from_actual"] and row["selected_count_delta"] == 0
            counts["includes_true_shortest_workflow"] += row["includes_true_shortest_workflow"]
            counts["all_required_JOIN_members_preserved"] += row["required_JOIN_preserved"]
            counts["planning_feasible"] += row["planning_feasible"]
            counts["joint_ready_sibling_frontiers"] += len(row["choice"]["shortest_frontier_members"]) > 1
            counts["unsupported_summary_fallbacks"] += row["choice"]["reason"] == "unsupported_summary_reference_fallback"
        result[population] = dict(counts)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "frontier-audit", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve previous evidence and require nice19 CPU-only execution")
    registration = json.loads(args.registration.read_bytes())
    audit = json.loads(args.frontier_audit.read_bytes())
    cohort = json.loads(args.cohort.read_bytes())
    config_path = Path(registration["experiment_configuration"])
    config = json.loads(config_path.read_bytes())
    model_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    profile = json.loads(model_path.read_bytes())["admission_cost_profile"]
    costs = planning_costs(profile)
    policy_path = ROOT / "beliefkv/policy/workflow_combination.py"
    if sha256_file(policy_path) != registration["source_sha256"].get(str(policy_path.relative_to(ROOT))):
        raise ValueError("actual R5 selection core changed")
    paths = (
        args.registration, args.frontier_audit, args.cohort, config_path, model_path,
        policy_path, ROOT / "beliefkv/policy/workflow_shortest_frontier.py", Path(__file__),
        ROOT / "scripts/audit_learned_combination_prediction.py",
        ROOT / "scripts/audit_workflow_combination_choices.py",
        ROOT / "scripts/audit_sealed_wait_prefetch.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    for path in (args.registration, args.cohort, config_path, model_path, policy_path):
        if audit["inputs_sha256"].get(str(path.resolve())) != pins[str(path.resolve())]:
            raise ValueError("the prior diagnostic uses a different registered population or model")
    expected = {row["monotonic_s"]: row for row in audit["decisions"]}
    if len(expected) != audit["complete_boundaries"]:
        raise ValueError("ambiguous complete decision boundaries")
    reader = SealedRecords()
    opportunity_path = Path(registration["run_root"]) / "full/opportunities/admission_opportunities.jsonl"
    reader.prefixes[str(opportunity_path)] = audit["input_jsonl_prefixes"][str(opportunity_path)]
    measured = set(cohort["measured_workflow_ids"])
    decisions, benchmark_rows = [], []
    for row in reader(opportunity_path):
        if row.get("event") != EVENT or row.get("monotonic_s") not in expected:
            continue
        evidence = expected[row["monotonic_s"]]
        actual = replay_choice(
            row, costs, profile["identity"],
            allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        )
        if not actual["search_replayed"] or row["decision"]["search_complete"] is not True:
            raise ValueError("require exact complete actual search replay")
        if evidence["source"] != row["source"] or set(evidence["choices"]["actual_combination"]["selected"]) != (
            set(row["decision"]["selected"])
        ):
            raise ValueError("the exact original decision changed")
        candidates = combination_candidates(row)
        resources = CombinationResources(**row["resources"])
        reference, required = row["decision"]["reference"], row["decision"]["required_reference_members"]
        choice = choose_shortest_frontier(
            candidates, reference, resources, costs, required=required, clock=lambda: 0,
        )
        selected, original = set(choice["selected"]), set(row["decision"]["selected"])
        by_id = {candidate.identity: candidate for candidate in candidates}
        labels = evidence["ordinary_workflows"]
        minimum = min((item["remaining_model_work"] for item in labels), default=None)
        shortest = {
            item["workflow"] for item in labels
            if item["remaining_model_work"] <= minimum + 1e-6
        } if minimum is not None else set()
        feasible = _evaluate([by_id[owner] for owner in choice["selected"]], resources, costs)
        if feasible is None or not set(required) <= selected:
            raise ValueError("the CPU candidate lost actual planning feasibility or a required JOIN")
        decisions.append({
            "monotonic_s": row["monotonic_s"], "source": row["source"],
            "planning_capacity_competition": evidence["planning_capacity_competition"],
            "choice": choice, "original_selected": row["decision"]["selected"],
            "set_changed_from_actual": selected != original,
            "changes_touch_measured": any(
                by_id[owner].workflow in measured for owner in selected ^ original
            ),
            "selected_count_delta": len(selected) - len(original),
            "includes_true_shortest_workflow": any(by_id[owner].workflow in shortest for owner in selected),
            "planning_feasible": feasible is not None, "required_JOIN_preserved": set(required) <= selected,
        })
        for repeat in range(2):
            names = ("actual", "candidate") if (len(decisions) + repeat) % 2 else ("candidate", "actual")
            for name in names:
                started, cpu = time.perf_counter_ns(), time.thread_time_ns()
                timed = (
                    choose_combination(
                        candidates, reference, resources, costs, required=required,
                        allow_feasible_resource_alternatives=True,
                    ) if name == "actual" else
                    choose_shortest_frontier(candidates, reference, resources, costs, required=required)
                )
                elapsed, cpu_elapsed = time.perf_counter_ns() - started, time.thread_time_ns() - cpu
                nested = timed.get("combination", {}) if name == "candidate" else timed
                deadline = timed["reason"] == "frontier_soft_deadline_reference_fallback" or nested.get(
                    "reason", ""
                ).startswith("soft_deadline_")
                benchmark_rows.append({
                    "boundary_monotonic_s": row["monotonic_s"], "repeat": repeat, "method": name,
                    "wall_us": elapsed / 1000., "thread_CPU_us": cpu_elapsed / 1000.,
                    "explicit_soft_deadline": deadline, "reason": timed["reason"],
                    "set_differs_from_own_unbounded_choice": set(timed["selected"]) != (
                        original if name == "actual" else selected
                    ),
                })
    if len(decisions) != len(expected) or any(
        sha256_file(Path(path)) != digest for path, digest in pins.items()
    ):
        raise ValueError("a bound input or exact complete decision changed")
    benchmarks = {
        name: {
            "calls": sum(row["method"] == name for row in benchmark_rows),
            "wall_us": distribution([row["wall_us"] for row in benchmark_rows if row["method"] == name]),
            "thread_CPU_us": distribution([
                row["thread_CPU_us"] for row in benchmark_rows if row["method"] == name
            ]),
            "explicit_soft_deadlines": sum(
                row["method"] == name and row["explicit_soft_deadline"] for row in benchmark_rows
            ),
            "set_differs_from_own_unbounded_choice": sum(
                row["method"] == name and row["set_differs_from_own_unbounded_choice"]
                for row in benchmark_rows
            ),
        } for name in ("actual", "candidate")
    }
    output = {
        "observed_at": datetime.now(timezone.utc).isoformat(), **summarize(decisions),
        "decisions": decisions, "bounded_CPU_benchmark": benchmarks,
        "bounded_CPU_benchmark_raw": benchmark_rows,
        "inputs_sha256": pins, "input_jsonl_prefixes": reader.prefixes,
        "scope": {
            "same_complete_recorded_candidates_budgets_costs_and_required_JOIN": True,
            "existing_cached_weights_only": True, "unbounded_choices_are_diagnostic": True,
            "CPU_benchmark_excludes_native_metadata_and_whole_control": True,
            "new_policy_not_integrated_into_serving_or_GPU_qualified": True,
            "planning_feasible_is_not_immediate_native_fit": True,
            "no_counterfactual_JCT_or_throughput_estimate": True,
            "no_trace_work_or_tool_duration_changed": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
    print(json.dumps({"choices": summarize(decisions), "bounded_CPU_benchmark": benchmarks}), flush=True)


if __name__ == "__main__":
    main()
