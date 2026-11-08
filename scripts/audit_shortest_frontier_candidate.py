"""Replay a shortest-frontier CPU candidate on complete registered choices."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationResources, _evaluate, choose_combination
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_frontier
from beliefkv.predictor.workflow_continuous_work import remaining_work_weight
from beliefkv.runtime.workflow_work_oracle import (
    CONTINUOUS_COMBINATION_WORK_KIND, WorkflowWorkOracleArtifact,
)
from scripts.audit_learned_combination_prediction import combination_candidates, planning_costs
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution, finite_number, union_ms
from scripts.audit_periodic_admission_decisions import useful_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, replay_choice
from scripts.verify_measured_execution import verify_execution


def continuous_oracle_evidence(row, costs, reference_work):
    """Read the synchronous truth actually recorded at this boundary."""
    if row["decision"].get("search_complete") is not True:
        raise ValueError("continuous frontier evidence requires a complete actual search")
    remaining_work_weight(0., reference_work)
    candidates = combination_candidates(row)
    resources = CombinationResources(**row["resources"])
    required = set(row["decision"]["required_reference_members"])
    by_id = {candidate.identity: candidate for candidate in candidates}
    if len(by_id) != len(candidates) or not required <= by_id.keys():
        raise ValueError("continuous frontier has duplicate or missing JOIN identities")
    labels = {}
    for candidate in candidates:
        prediction = (row.get("predictions") or {}).get(candidate.workflow)
        if prediction is None:
            return None
        work, units = prediction.get("remaining_work"), prediction.get("remaining_work_units")
        if (
            prediction.get("evidence") != "oracle"
            or not finite_number(work) or work < 0
            or type(units) is not int or units < 0
            or not math.isclose(work * 100., units, rel_tol=0., abs_tol=1e-6)
            or prediction.get("reference_work") != reference_work
            or any(prediction.get(name) != 0. for name in (
                "remaining_path_s", "model_only_path_s", "tool_and_fixed_delay_path_s",
            ))
            or any(
                not finite_number(weight) or not math.isclose(
                    weight, remaining_work_weight(work, reference_work), rel_tol=0., abs_tol=1e-12,
                ) for weight in (candidate.weight, prediction.get("effective_weight"))
            )
        ):
            raise ValueError("recorded continuous oracle work or weight differs from its bound readout")
        label = {
            "workflow": candidate.workflow, "remaining_model_work": work,
            "weights": {"actual_cached": candidate.weight},
        }
        if candidate.workflow in labels and labels[candidate.workflow] != label:
            raise ValueError("same-workflow continuous oracle labels disagree")
        labels[candidate.workflow] = label
    required_rows = [by_id[owner] for owner in required]
    protected = {candidate.workflow for candidate in required_rows}
    fitting = [
        candidate for candidate in candidates if candidate.identity not in required
        and _evaluate([*required_rows, candidate], resources, costs) is not None
    ]
    ordinary = {
        candidate.workflow: labels[candidate.workflow]
        for candidate in fitting if candidate.workflow not in protected
    }
    return {
        "monotonic_s": row["monotonic_s"], "source": row["source"],
        "planning_capacity_competition": len(ordinary) >= 2 and (
            _evaluate([*required_rows, *fitting], resources, costs) is None
        ),
        "ordinary_workflows": list(ordinary.values()),
        "choices": {"actual_combination": {"selected": row["decision"]["selected"]}},
    }


def omitted_service_exposure(decisions, batches, measured):
    """Measure historical service delays for alternatives, without inventing grants."""
    queries, requests, seen = [], defaultdict(list), set()
    measured = set(measured)
    for decision in decisions:
        for raw in decision["newly_selected_keys"]:
            key = identity(raw, grant=True)
            begin = decision["monotonic_s"] * 1000.
            if not finite_number(begin) or begin < 0 or (key, begin) in seen:
                raise ValueError("alternative service query has duplicate or invalid ownership/clock")
            seen.add((key, begin))
            query = {
                "key": key, "begin_ms": begin, "source": decision["source"],
                "measured_workflow": key[1] in measured,
                "earlier_exact_services": 0, "prior_launches_excluded": 0,
                "unknown_progress_samples": 0, "first_exact_service_ms": None,
                "first_new_useful_service_ms": None,
            }
            queries.append(query)
            requests[key[0]].append(query)
    domains, counts = set(), Counter()
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        launch, start, end = (batch.get(name) for name in (
            "launch_monotonic_ms", "service_start_monotonic_ms", "complete_monotonic_ms",
        ))
        domain = batch.get("monotonic_clock_domain")
        if (
            not all(finite_number(value) for value in (launch, start, end))
            or not 0 <= launch <= start <= end or not domain
        ):
            raise ValueError("historical alternative service has an invalid native clock")
        domains.add(domain)
        if len(domains) > 1:
            raise ValueError("historical alternative services cross monotonic clock domains")
        for member in batch.get("request_samples", ()):
            targets = requests.get(member.get("request_id"))
            if not targets:
                continue
            if any(member.get(name) is None for name in ("attempt_id", "session_id", "session_generation")):
                counts["target_samples_with_unknown_attempt_or_session"] += 1
                continue
            key = identity(member)
            for query in targets:
                if key != query["key"]:
                    continue
                if start < query["begin_ms"]:
                    query["earlier_exact_services"] += 1
                    continue
                if query["first_exact_service_ms"] is None or start < query["first_exact_service_ms"]:
                    query["first_exact_service_ms"] = start
                progress = useful_work(member, member.get("phase", batch.get("phase")))
                if progress is None:
                    query["unknown_progress_samples"] += 1
                elif progress and launch < query["begin_ms"]:
                    query["prior_launches_excluded"] += 1
                elif progress and (
                    query["first_new_useful_service_ms"] is None
                    or start < query["first_new_useful_service_ms"]
                ):
                    query["first_new_useful_service_ms"] = start

    def summarize(rows):
        intervals = defaultdict(list)
        for row in rows:
            end = row["first_new_useful_service_ms"]
            if end is not None:
                intervals[row["key"][1]].append((row["begin_ms"], end))
        return {
            "alternative_request_episodes": len(rows),
            "distinct_exact_request_attempts": len({row["key"] for row in rows}),
            "distinct_workflows": len({row["key"][1] for row in rows}),
            "with_following_exact_service": sum(row["first_exact_service_ms"] is not None for row in rows),
            "with_following_new_useful_service": sum(
                row["first_new_useful_service_ms"] is not None for row in rows
            ),
            "historical_useful_service_delay_ms": distribution([
                row["first_new_useful_service_ms"] - row["begin_ms"]
                for row in rows if row["first_new_useful_service_ms"] is not None
            ]),
            "within_workflow_useful_wait_union_sum_ms": sum(union_ms(spans) for spans in intervals.values()),
        }

    return {
        "all": summarize(queries),
        "measured": summarize([row for row in queries if row["measured_workflow"]]),
        "nonmeasured": summarize([row for row in queries if not row["measured_workflow"]]),
        "counts": dict(counts), "episodes": queries,
        "semantics": {
            "actual_historical_service_of_omitted_alternatives_only": True,
            "no_actual_grant_to_the_counterfactual_choice_is_claimed": True,
            "complete_attempt_and_session_identity_required": True,
            "new_useful_service_excludes_preboundary_launches": True,
            "repeated_and_parallel_waits_unioned_within_workflow": True,
            "avoidable_JCT_or_whole_system_speedup_established": False,
        },
    }


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
            counts["original_includes_true_shortest_workflow"] += row[
                "original_includes_true_shortest_workflow"
            ]
            counts["all_required_JOIN_members_preserved"] += row["required_JOIN_preserved"]
            counts["planning_feasible"] += row["planning_feasible"]
            counts["joint_ready_sibling_frontiers"] += len(row["choice"]["shortest_frontier_members"]) > 1
            counts["unsupported_summary_fallbacks"] += row["choice"]["reason"] == "unsupported_summary_reference_fallback"
        result[population] = dict(counts)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--frontier-audit", type=Path)
    source.add_argument("--continuous-oracle", action="store_true")
    parser.add_argument("--observe-native-service", action="store_true")
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve previous evidence and require nice19 CPU-only execution")
    registration = json.loads(args.registration.read_bytes())
    audit = json.loads(args.frontier_audit.read_bytes()) if args.frontier_audit is not None else None
    cohort = json.loads(args.cohort.read_bytes())
    config_path = Path(registration["experiment_configuration"])
    config = json.loads(config_path.read_bytes())
    model_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    model = json.loads(model_path.read_bytes())
    profile = model["admission_cost_profile"]
    costs = planning_costs(profile)
    policy_path = ROOT / "beliefkv/policy/workflow_combination.py"
    if sha256_file(policy_path) != registration["source_sha256"].get(str(policy_path.relative_to(ROOT))):
        raise ValueError("actual R5 selection core changed")
    paths = (
        args.registration, args.cohort, config_path, model_path,
        policy_path, ROOT / "beliefkv/policy/workflow_shortest_frontier.py", Path(__file__),
        ROOT / "scripts/audit_learned_combination_prediction.py",
        ROOT / "scripts/audit_workflow_combination_choices.py",
        ROOT / "scripts/audit_sealed_wait_prefetch.py",
        ROOT / "scripts/audit_free_dispatch_service.py",
        ROOT / "scripts/audit_native_system_costs.py",
        ROOT / "scripts/audit_periodic_admission_decisions.py",
    )
    if args.frontier_audit is not None:
        paths = (*paths, args.frontier_audit)
    else:
        paths = (
            *paths, ROOT / "beliefkv/runtime/workflow_work_oracle.py",
            ROOT / "beliefkv/predictor/workflow_continuous_work.py",
            ROOT / "scripts/verify_measured_execution.py",
        )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    expected, execution, reference_work = None, None, None
    if audit is not None:
        for path in (args.registration, args.cohort, config_path, model_path, policy_path):
            if audit["inputs_sha256"].get(str(path.resolve())) != pins[str(path.resolve())]:
                raise ValueError("the prior diagnostic uses a different registered population or model")
        expected = {row["monotonic_s"]: row for row in audit["decisions"]}
        if len(expected) != audit["complete_boundaries"]:
            raise ValueError("ambiguous complete decision boundaries")
    else:
        common = config["common_environment"]
        artifact = WorkflowWorkOracleArtifact.load(model_path, pins[str(model_path.resolve())])
        if (
            registration["order"] != ["full"]
            or artifact.raw["kind"] != CONTINUOUS_COMBINATION_WORK_KIND
            or any(common.get(prefix + name) != value for prefix in ("", "BELIEFKV_")
                   for name, value in (
                       ("WORKFLOW_WORK_SOURCE", "oracle"),
                       ("WORKFLOW_WORK_MODEL", str(model_path)),
                       ("WORKFLOW_WORK_MODEL_SHA256", pins[str(model_path.resolve())]),
                   ))
        ):
            raise ValueError("require the explicitly bound continuous model-work oracle execution")
        reference_work = artifact.continuous_reference["reference_work"]
        verified = verify_execution(args.registration, args.cohort)
        execution = {
            name: verified[name] for name in (
                "protocol", "registration_sha256", "cohort_sha256",
                "certificate", "certificate_sha256", "measured_requests",
                "measured_output_tokens", "actual_owned_exit_verified",
            )
        }
    reader = SealedRecords()
    opportunity_path = Path(registration["run_root"]) / "full/opportunities/admission_opportunities.jsonl"
    if audit is not None:
        reader.prefixes[str(opportunity_path)] = audit["input_jsonl_prefixes"][str(opportunity_path)]
    measured = set(cohort["measured_workflow_ids"])
    decisions, benchmark_rows, coverage, seen = [], [], Counter(), set()
    for row in reader(opportunity_path):
        if row.get("event") != EVENT or expected is not None and row.get("monotonic_s") not in expected:
            continue
        coverage["combination_entries"] += 1
        actual = replay_choice(
            row, costs, profile["identity"],
            allow_feasible_resource_alternatives=True, plan_device_capacity=True,
        )
        if not actual["search_replayed"] or row["decision"]["search_complete"] is not True:
            if expected is not None:
                raise ValueError("require exact complete actual search replay")
            coverage["unsearched_entries" if not actual["search_replayed"] else "incomplete_searches"] += 1
            continue
        evidence = (
            expected[row["monotonic_s"]] if expected is not None
            else continuous_oracle_evidence(row, costs, reference_work)
        )
        if evidence is None:
            coverage["missing_recorded_oracle_predictions"] += 1
            continue
        if row["monotonic_s"] in seen:
            raise ValueError("ambiguous complete decision timestamp")
        seen.add(row["monotonic_s"])
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
            "original_includes_true_shortest_workflow": any(
                by_id[owner].workflow in shortest for owner in original
            ),
            "ordinary_workflows": labels,
            "newly_selected_keys": [
                row["keys"][owner] for owner in choice["selected"] if owner not in original
            ],
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
    service = None
    if args.observe_native_service:
        service = omitted_service_exposure(
            decisions, reader(Path(registration["run_root"]) / "full/server/runtime_audit.jsonl"), measured,
        )
    if (expected is not None and len(decisions) != len(expected)) or any(
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
        "coverage": dict(coverage),
        "continuous_oracle_reference_work": reference_work,
        "complete_measured_execution": execution,
        "omitted_alternative_service_exposure": service,
        "inputs_sha256": pins, "input_jsonl_prefixes": reader.prefixes,
        "scope": {
            "same_complete_recorded_candidates_budgets_costs_and_required_JOIN": True,
            "existing_cached_weights_only": audit is not None,
            "actual_recorded_weights_only": True,
            "exact_continuous_oracle_weights_checked": args.continuous_oracle,
            "unbounded_choices_are_diagnostic": True,
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
