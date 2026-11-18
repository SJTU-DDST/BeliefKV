"""Compare ratio and benefit-only admission on complete, sealed serving decisions."""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCosts, CombinationResources, _evaluate, choose_combination,
)
from scripts import run_causal_comparison as causal
from scripts.audit_free_dispatch_service import identity
from scripts.audit_learned_combination_prediction import combination_candidates
from scripts.audit_live_work_prediction import audit, observation_timeline
from scripts.audit_native_system_costs import distribution
from scripts.audit_periodic_admission_decisions import EXTRA_GRANTS, useful_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_combination_choices import EVENT, inspect as inspect_choices
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


JOURNAL_EVENTS = EXTRA_GRANTS | {
    EVENT, "agent_periodic_admission_decision", "workflow_oracle_coalesced_dispatch",
    "agent_predicted_tool_replacement", "agent_full_offload_paused",
    "workflow_remaining_pressure_pause", "agent_initial_registration_decision",
}


def benefit_only(candidates, reference, resources, costs, required=()):
    """Change only the objective; keep reference/enumeration order and relative ties."""
    by_id, required = {row.identity: row for row in candidates}, set(required)
    if not required <= set(reference):
        raise ValueError("required rights must belong to the recorded reference")
    baseline = _evaluate([by_id[owner] for owner in reference], resources, costs)
    selected, best = tuple(reference), baseline
    for mask in range(1, 1 << len(candidates)):
        rows = [row for index, row in enumerate(candidates) if mask & (1 << index)]
        if not required <= {row.identity for row in rows}:
            continue
        value = _evaluate(rows, resources, costs)
        if value is not None and (
            best is None or value["benefit"] > best["benefit"] * (1. + costs.relative_tie)
        ):
            selected, best = tuple(row.identity for row in rows), value
    if not required <= set(selected):
        raise ValueError("benefit-only diagnostic revoked required JOIN rights")
    return {
        "selected": selected, "selected_evaluation": best,
        "reference_evaluation": baseline, "objective": "benefit_only",
    }


class NativeEvidence:
    """Index actual new launches and completed output for exact recorded attempts."""

    def __init__(self, targets):
        self.targets = targets
        self.launches, self.outputs = defaultdict(list), defaultdict(list)
        self.domains = set()
        self.missing_identity_samples = 0

    def collect(self, batches):
        for batch in batches:
            if batch.get("event") != "gpu_service_sample":
                continue
            self.domains.add(batch["monotonic_clock_domain"])
            start, end = batch["service_start_monotonic_ms"], batch["complete_monotonic_ms"]
            launch = batch["launch_monotonic_ms"]
            if not all(math.isfinite(v) for v in (launch, start, end)) or not 0 <= launch <= start <= end:
                raise ValueError("native launch/service clocks are inconsistent")
            for member in batch["request_samples"]:
                if member.get("request_id") not in self.targets:
                    continue
                if any(member.get(name) is None for name in (
                    "attempt_id", "session_id", "session_generation",
                )):
                    self.missing_identity_samples += 1
                    continue
                key = identity(member)
                if useful_work(member, member.get("phase", batch["phase"])):
                    self.launches[key].append((launch, start, end))
                output = member.get("output_tokens_after")
                if type(output) is not int or output < 0:
                    raise ValueError("target service lacks observed cumulative output")
                self.outputs[key].append((end, output))
            yield batch
        if len(self.domains) != 1:
            raise ValueError("require a single native monotonic clock domain")
        for table in (self.launches, self.outputs):
            for values in table.values():
                values.sort()

    def after(self, key, when):
        values = self.launches.get(key, ())
        position = bisect_left(values, (when, -math.inf, -math.inf))
        if position >= len(values):
            return None
        launch, start, end = values[position]
        return {
            "new_launch_wait_ms": launch - when,
            "new_service_wait_ms": start - when,
            "new_completion_wait_ms": end - when,
            "earlier_positive_batches": position,
        }

    def output_before(self, key, when):
        values = self.outputs.get(key, ())
        position = bisect_right(values, (when, math.inf))
        return values[position - 1][1] if position else 0


def components(rows, resources, costs):
    value = _evaluate(rows, resources, costs)
    if value is None:
        return None
    before = costs.decode_cost(resources.active_width, resources.active_context_tokens)
    after = costs.decode_cost(
        resources.active_width + len(rows),
        resources.active_context_tokens + sum(row.context_tokens for row in rows),
    )
    return {
        **value,
        "H2D_proxy_ms": value["missing_unique_tokens"] * costs.h2d_ms_per_token,
        "prefill_proxy_ms": sum(costs.prefill_cost(row.input_tokens, row.context_tokens) for row in rows),
        "decode_before_ms": before, "decode_after_ms": after,
        "context_tokens": sum(row.context_tokens for row in rows),
    }


def candidate_detail(candidate, row, observed, labels, native):
    key = row["keys"][candidate.identity]
    exact = identity(key, grant=True)
    request = labels[key["request_id"]]
    if any(request[name] != key[target] for name, target in (
        ("workflow_id", "root_workflow_id"), ("invocation_id", "invocation_id"),
        ("context_id", "context_id"),
    )):
        raise ValueError("request work label has another owner")
    output = request["output_tokens"]
    remaining_output = output - native.output_before(exact, row["monotonic_s"] * 1000.)
    if remaining_output < 0:
        raise ValueError("observed output exceeds the complete request")
    return {
        "key": key, "weight": candidate.weight,
        "remaining_model_work": observed[candidate.identity]["observed_remaining_model_work"],
        "input_tokens": candidate.input_tokens, "context_tokens": candidate.context_tokens,
        "recorded_request_output_tokens": output,
        "observed_remaining_request_output_tokens": remaining_output,
        "group": candidate.group, "unfinished_members": candidate.unfinished_members,
        "active_ready_members": candidate.active_ready_members,
        "following_exact_native_progress": native.after(exact, row["monotonic_s"] * 1000.),
    }


def reversals(candidates, row, observed, resources, costs, no_cost, labels, native, grant, measured):
    """Isolate equal-cardinality, independent singleton swaps; JOIN credit stays fixed."""
    by_id = {item.identity: item for item in candidates}
    actual = set(row["decision"]["selected"])
    required = set(row["decision"]["required_reference_members"])
    protected_workflows = {by_id[owner].workflow for owner in required}
    base = components([by_id[owner] for owner in row["decision"]["selected"]], resources, costs)
    if base is None:
        return []
    services = {
        service["invocation_id"]: service for service in grant[
            "all_periodic_service" if row["source"] == "periodic_decay_resume" else "all_free_service"
        ]
    }
    result = []
    for short in candidates:
        if short.identity in actual or short.workflow in protected_workflows:
            continue
        for owner in row["decision"]["selected"]:
            long = by_id[owner]
            if (
                owner in required or long.workflow in protected_workflows
                or short.workflow == long.workflow
                or short.unfinished_members != 1 or long.unfinished_members != 1
                or short.active_ready_members or long.active_ready_members
                or any(by_id[other].workflow in (short.workflow, long.workflow)
                       for other in actual - {owner})
            ):
                continue
            if observed[short.identity]["observed_remaining_model_work"] >= (
                observed[owner]["observed_remaining_model_work"] - 1e-6
            ):
                continue
            swapped = [
                short if item == owner else by_id[item]
                for item in row["decision"]["selected"]
            ]
            value = components(swapped, resources, costs)
            if value is None or value["objective_cost_ms"] <= base["objective_cost_ms"]:
                continue
            if not (
                value["benefit"] > base["benefit"] * (1. + costs.relative_tie)
                and value["score"] <= base["score"] * (1. + costs.relative_tie)
            ):
                continue
            service = services.get(owner)
            granted = bool(service) and (
                grant.get("following_periodic_grant_verified", False)
                if row["source"] == "periodic_decay_resume" else service["actual_grant_verified"]
            )
            separate = bool(service and service.get("separate_tool_replacement"))
            episode = service.get("episode") if service else None
            retained = (
                episode.get("selection_retained", episode.get("active_after_pressure"))
                if episode else None
            )
            a = candidate_detail(short, row, observed, labels, native)
            b = candidate_detail(long, row, observed, labels, native)
            sa, sb = a["following_exact_native_progress"], b["following_exact_native_progress"]
            result.append({
                "shorter_workflow_expensive_request": a,
                "longer_workflow_cheaper_request": b,
                "actual_set_evaluation": base, "one_for_one_swap_evaluation": value,
                "numerator_only_selects_short_request": short.identity in no_cost["selected"],
                "selected_long_request_actual_direct_grant": granted and not separate,
                "selected_long_request_retained_at_grant": retained,
                "both_workflows_measured": {short.workflow, long.workflow} <= measured,
                "short_workflow_measured": short.workflow in measured,
                "both_followed_by_exact_new_progress": sa is not None and sb is not None,
                "selected_long_served_first": (
                    sb["new_service_wait_ms"] < sa["new_service_wait_ms"]
                    if sa is not None and sb is not None else None
                ),
                "actual_short_minus_long_service_gap_ms": (
                    sa["new_service_wait_ms"] - sb["new_service_wait_ms"]
                    if sa is not None and sb is not None else None
                ),
                "short_request_has_more_remaining_output": (
                    a["observed_remaining_request_output_tokens"]
                    > b["observed_remaining_request_output_tokens"]
                ),
            })
    return result


def summarize_reversals(decisions):
    entries = [
        {**entry, "monotonic_s": decision["monotonic_s"],
         "source": decision["source"], "raw_journal_sequence": decision["raw_journal_sequence"]}
        for decision in decisions for entry in decision["strict_singleton_cost_reversals"]
    ]
    unique = {}
    for entry in entries:
        key = tuple(identity(entry[name]["key"], grant=True) for name in (
            "shorter_workflow_expensive_request", "longer_workflow_cheaper_request",
        ))
        unique.setdefault(key, entry)
    values = list(unique.values())
    gaps = [
        entry["actual_short_minus_long_service_gap_ms"] for entry in values
        if entry["selected_long_served_first"] is True
        and entry["selected_long_request_actual_direct_grant"]
        and entry["selected_long_request_retained_at_grant"] is True
    ]
    return {
        "boundary_pair_observations": len(entries),
        "boundaries": sum(bool(row["strict_singleton_cost_reversals"]) for row in decisions),
        "unique_exact_request_pairs": len(values),
        "unique_workflow_pairs": len({
            tuple(entry[name]["key"]["root_workflow_id"] for name in (
                "shorter_workflow_expensive_request", "longer_workflow_cheaper_request",
            )) for entry in values
        }),
        "unique_request_pairs_both_measured": sum(row["both_workflows_measured"] for row in values),
        "unique_request_pairs_short_workflow_measured": sum(row["short_workflow_measured"] for row in values),
        "unique_request_pairs_numerator_selects_short": sum(
            row["numerator_only_selects_short_request"] for row in values
        ),
        "unique_request_pairs_selected_long_served_first": sum(
            row["selected_long_served_first"] is True for row in values
        ),
        "unique_request_pairs_short_request_has_more_remaining_output": sum(
            row["short_request_has_more_remaining_output"] for row in values
        ),
        "direct_retained_long_first_unique_pair_gaps_ms": distribution(gaps),
        "unique_request_pair_examples": values,
    }


def inspect(records, original_sequences, observations, costs, profile, thresholds, measured, labels, native):
    choices = inspect_choices(
        records, (), costs, profile["identity"], allow_feasible_resource_alternatives=True,
        plan_device_capacity=True, include_all_selected_service=True,
    )
    counts, totals, decisions = Counter(), defaultdict(Counter), []
    if len(choices["decisions"]) != len(observations):
        raise ValueError("each recorded choice requires one exact observed boundary")
    for choice, observation in zip(choices["decisions"], observations):
        counts["entries"] += 1
        if not choice["search_replayed"]:
            counts["metadata_unavailable"] += 1
            continue
        if not choice["search_complete"]:
            counts["incomplete_searches_excluded"] += 1
            continue
        row = records[choice["journal_sequence"]]
        if observation["monotonic_s"] != row["monotonic_s"]:
            raise ValueError("observation belongs to another decision boundary")
        candidates = combination_candidates(row)
        observed = {item["key"]["invocation_id"]: item for item in observation["candidates"]}
        if any(
            candidate.identity not in observed
            or observed[candidate.identity]["key"] != row["keys"][candidate.identity]
            or "observed_remaining_model_work" not in observed[candidate.identity]
            for candidate in candidates
        ):
            counts["missing_exact_observed_inputs"] += 1
            continue
        remaining = {
            owner: item["observed_remaining_model_work"] for owner, item in observed.items()
        }
        if any(not math.isfinite(value) or value < -1e-6 for value in remaining.values()):
            raise ValueError("invalid complete-work diagnostic label")
        resources = CombinationResources(**row["resources"])
        reference, required = row["decision"]["reference"], set(row["decision"]["required_reference_members"])
        required_rows = [candidate for candidate in candidates if candidate.identity in required]
        fitting = [
            candidate for candidate in candidates if candidate.identity not in required
            and _evaluate(required_rows + [candidate], resources, costs) is not None
        ]
        competition = len({candidate.workflow for candidate in fitting}) >= 2 and (
            _evaluate(required_rows + fitting, resources, costs) is None
        )
        counts["complete_searches_with_labels"] += 1
        counts["planning_ordinary_cross_workflow_capacity_competitions"] += competition
        selections = {}
        for name in ("cached", "truth_three_bins", "truth_continuous"):
            alternative = [
                replace(candidate, weight=(
                    candidate.weight if name == "cached" else
                    (1., .5, .25)[bisect_right(thresholds, max(0., remaining[candidate.identity]))]
                    if name == "truth_three_bins" else
                    1. / (1. + max(0., remaining[candidate.identity]) / thresholds[0])
                )) for candidate in candidates
            ]
            ratio = choose_combination(
                alternative, reference, resources, costs, required=required,
                clock=lambda: 0, allow_feasible_resource_alternatives=True,
            )
            if name == "cached" and set(ratio["selected"]) != set(choice["selected"]):
                raise ValueError("cached full search disagrees with the executed selection")
            numerator = benefit_only(alternative, reference, resources, costs, required)
            actual_set, new_set = set(ratio["selected"]), set(numerator["selected"])
            stats = totals[name]
            stats["complete_searches"] += 1
            stats["changed_sets"] += new_set != actual_set
            stats["larger_sets"] += len(new_set) > len(actual_set)
            stats["smaller_sets"] += len(new_set) < len(actual_set)
            stats["equal_size_changed_sets"] += new_set != actual_set and len(new_set) == len(actual_set)
            stats["ratio_strict_reference_shrinks"] += actual_set < set(reference)
            stats["benefit_only_strict_reference_shrinks"] += new_set < set(reference)
            stats["ratio_selected_agent_observations"] += len(actual_set)
            stats["benefit_only_selected_agent_observations"] += len(new_set)
            changed_measured = any(
                candidate.workflow in measured and candidate.identity in actual_set ^ new_set
                for candidate in candidates
            )
            stats["changed_sets_touch_measured_workflow"] += changed_measured
            protected = {candidate.workflow for candidate in required_rows}
            ordinary = [candidate for candidate in fitting if candidate.workflow not in protected]
            if competition and ordinary:
                minimum = min(remaining[candidate.identity] for candidate in ordinary)
                shortest = {
                    candidate.identity for candidate in ordinary
                    if remaining[candidate.identity] <= minimum + 1e-6
                }
                stats["competitions_with_unprotected_shortest"] += 1
                stats["ratio_includes_shortest"] += bool(shortest & actual_set)
                stats["benefit_only_includes_shortest"] += bool(shortest & new_set)
            selections[name] = {
                "ratio_selected": ratio["selected"], "benefit_only_selected": numerator["selected"],
                "ratio_evaluation": ratio["selected_evaluation"],
                "benefit_only_evaluation": numerator["selected_evaluation"],
            }
        no_cost = {
            "selected": selections["cached"]["benefit_only_selected"],
        }
        pairs = reversals(
            candidates, row, observed, resources, costs, no_cost, labels, native, choice, measured,
        )
        decisions.append({
            "monotonic_s": row["monotonic_s"], "source": row["source"],
            "raw_journal_sequence": original_sequences[choice["journal_sequence"]],
            "reference": reference, "required_JOIN_members": sorted(required),
            "planning_capacity_competition": competition, "selections": selections,
            "candidates": [
                candidate_detail(candidate, row, observed, labels, native)
                for candidate in candidates
            ],
            "strict_singleton_cost_reversals": pairs,
        })
    return {
        "counts": dict(counts), "objective_comparisons": {key: dict(value) for key, value in totals.items()},
        "strict_cost_reversals": summarize_reversals(decisions), "decisions": decisions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "control", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior denominator diagnostics")
    plan, control = (causal.load(path) for path in (args.registration, args.control))
    for item in (plan, control):
        causal.comparison.verify_sources(item)
    configuration = Path(plan["experiment_configuration"])
    spec = causal.load(configuration)
    model_path = Path(spec["common_environment"]["WORKFLOW_WORK_MODEL"])
    if sha256_file(model_path) != spec["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("executed model artifact changed")
    model = causal.load(model_path)
    profile = model["admission_cost_profile"]
    if profile["kind"] != "r5_frozen_batch_cost_proxy_capacity_v6":
        raise ValueError("require the declared complete-pool planning profile")
    label_path = Path(control["reference_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"])
    labels = causal.load(label_path)
    if set(labels["workflows"]) != set(plan["workflow_ids"]):
        raise ValueError("complete-work labels belong to another workload population")
    policy = ROOT / "beliefkv/policy/workflow_combination.py"
    if sha256_file(policy) != plan["source_sha256"][str(policy.relative_to(ROOT))]:
        raise ValueError("analysis policy differs from the executed policy")
    pins = {str(path.resolve()): sha256_file(path) for path in (
        args.registration, args.control, args.cohort, configuration, model_path, label_path,
        Path(__file__), policy, ROOT / "scripts/audit_workflow_combination_choices.py",
        ROOT / "scripts/audit_live_work_prediction.py",
        ROOT / "scripts/audit_periodic_admission_decisions.py",
        ROOT / "scripts/audit_free_dispatch_service.py",
    )}
    execution = verify_execution(args.registration, args.cohort)
    print(json.dumps({"stage": "complete_measured_execution_verified",
                      "requests": execution["measured_requests"]}), flush=True)
    reader, arm = SealedRecords(), Path(plan["run_root"]) / "full"
    records, sequences = [], []
    for sequence, row in enumerate(reader(arm / "opportunities/admission_opportunities.jsonl")):
        if row.get("event") in JOURNAL_EVENTS:
            records.append(row)
            sequences.append(sequence)
    rows = [row for row in records if row.get("event") == EVENT]
    observations = [{
        "monotonic_s": row["monotonic_s"], "candidates": [
            {"key": row["keys"][candidate["identity"]]} for candidate in row.get("candidates", ())
        ],
    } for row in rows]
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    native = NativeEvidence({
        candidate["key"]["request_id"] for row in observations for candidate in row["candidates"]
    })
    timeline = observation_timeline(
        events, native.collect(reader(arm / "server/runtime_audit.jsonl")), labels["requests"],
    )
    totals = {
        workflow: value["total_work_units"] / 100. for workflow, value in labels["workflows"].items()
    }
    audit(observations, timeline, labels["requests"], totals, {})
    print(json.dumps({"stage": "sealed_history_and_observed_work_reconstructed",
                      "combination_entries": len(rows)}), flush=True)
    result = inspect(
        records, sequences, observations, CombinationCosts(**profile["costs"]), profile,
        model["work_thresholds"], set(causal.load(args.cohort)["measured_workflow_ids"]),
        labels["requests"], native,
    )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("an immutable diagnostic input changed")
    result.update(
        inputs_sha256=pins, input_jsonl_prefixes=reader.prefixes,
        complete_measured_execution_verified=True,
        measured_requests=execution["measured_requests"],
        measured_output_tokens=execution["measured_output_tokens"],
        complete_execution_certificate=execution["certificate"],
        analysis_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        scope={
            "same_observed_candidates_resources_JOIN_rights_and_cost_estimates": True,
            "benefit_only_changes_only_selection_objective": True,
            "reference_enumeration_order_and_one_percent_relative_ties_preserved": True,
            "actual_search_including_soft_deadlines_reproduced_first": True,
            "only_complete_searches_enter_objective_counterfactual": True,
            "true_remaining_work_is_offline_label_not_an_online_input": True,
            "remaining_model_work_excludes_tool_waits_and_queueing": True,
            "current_request_output_length_is_diagnostic_only": True,
            "strict_reversals_preserve_cardinality_and_equal_singleton_coverage": True,
            "request_pairs_deduplicated_by_full_attempt_session_identity": True,
            "all_grant_and_intervention_events_retained_in_filtered_journal": True,
            "native_new_service_is_worker_interval_not_kernel_start": True,
            "later_native_service_does_not_prove_hypothetical_admission_feasibility": True,
            "full_pool_planning_fit_is_not_immediate_physical_fit": True,
            "observed_wait_gaps_are_not_removable_JCT_or_a_JCT_upper_bound": True,
            "right_censored_nonmeasured_workflows_retained": True,
            "whole_run_counterfactual_JCT_proven": False,
        },
        native_missing_identity_samples=native.missing_identity_samples,
        GPU_action=False, online_policy_changed=False,
    )
    write(args.output, result)
    print(json.dumps({
        "counts": result["counts"], "objective_comparisons": result["objective_comparisons"],
        "strict_cost_reversals": {
            key: value for key, value in result["strict_cost_reversals"].items()
            if key != "unique_request_pair_examples"
        },
    }), flush=True)


if __name__ == "__main__":
    main()
