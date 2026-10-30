"""Trace workflow ranking opportunities through logical grants to positive GPU work."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import population_fingerprint, summary
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts.audit_free_dispatch_service import identity
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_competition import inspect_competition
from scripts.audit_workflow_size_selection import EVENT, inspect_choice
from scripts import run_causal_comparison as causal


def competing_categories(row, metadata, *, ignore_categories=False):
    """Count actual remaining-rank alternatives, independent of initial size tiers."""
    ready = row.get("logical_ready_paused_before_resume", {})
    remaining = row["capacity_tokens"]
    buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
    groups = [row["ordered"]] if ignore_categories else [
        [item for item in row["ordered"] if item[1] == category] for category in (0, 1, 2)
    ]
    result = []
    for peers in groups:
        fitting = [
            item for item in peers if ready.get(item[0])
            and item[3] + buffer <= remaining
        ]
        workflows = {metadata[item[0]]["key"]["root_workflow_id"] for item in fitting}
        if len(workflows) > 1 and sum(item[3] + buffer for item in fitting) > remaining:
            result.append({
                "category": None if ignore_categories else peers[0][1],
                "remaining_tokens": remaining,
                "owners": [item[0] for item in fitting],
                "rank_min": min(item[2] for item in fitting),
                "rank_max": max(item[2] for item in fitting),
            })
        for item in sorted(peers, key=lambda item: (item[3], item[4])):
            if item[3] + buffer <= remaining:
                remaining -= item[3] + buffer
    return result


def inspect(opportunities, services, population):
    counts, grant_counts, kinds = Counter(), Counter(), Counter()
    rows, changed, keys = [], [], set()
    cpu_us, controls_cpu_us = [], []
    final = None
    buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
    for row in opportunities:
        event = row.get("event")
        kinds[event] += 1
        if event == "admission_runtime_state":
            final = row
        if event and event.startswith("agent_") and "programs" in row:
            grant_counts[event] += len(row["programs"])
        if event != EVENT:
            continue
        comparison = inspect_choice(row)
        rows.append(row)
        counts["periodic_controls"] += 1
        cpu_us.append(row["thread_cpu_us"])
        controls_cpu_us.append(row["control_thread_cpu_us"])
        ordered, capacity = row["ordered"], row["capacity_tokens"]
        if not ordered:
            counts["empty_no_budget" if capacity <= buffer else "empty_with_positive_budget"] += 1
            continue
        counts["nonempty_controls"] += 1
        readiness = row.get("logical_ready_paused_before_resume")
        if readiness is None or readiness.keys() != {item[0] for item in ordered}:
            raise ValueError("a complete pre-grant readiness snapshot is required")
        metadata = {item["key"]["invocation_id"]: item for item in row["candidates"]}
        if set(metadata) != readiness.keys():
            raise ValueError("candidate metadata and readiness identities differ")
        ready = [item for item in ordered if readiness[item[0]]]
        fitting = [item for item in ready if item[3] + buffer <= capacity]
        workflows = {metadata[item[0]]["key"]["root_workflow_id"] for item in ready}
        counts["no_ready_candidate"] += not ready
        counts["one_ready_candidate"] += len(ready) == 1
        counts["multiple_ready_candidates"] += len(ready) >= 2
        counts["multiple_ready_workflows"] += len(workflows) >= 2
        counts["all_ready_fit_together"] += bool(ready) and sum(item[3] + buffer for item in ready) <= capacity
        counts["no_ready_candidate_fits_individually"] += bool(ready) and not fitting
        unrestricted = competing_categories(row, metadata, ignore_categories=True)
        categories = competing_categories(row, metadata)
        counts["capacity_competition_before_category_restriction"] += bool(unrestricted)
        counts["capacity_competition_within_reference_category"] += bool(categories)
        counts["within_category_competition_with_distinct_remaining_ranks"] += any(
            group["rank_min"] != group["rank_max"] for group in categories
        )
        counts["changed_order"] += comparison["order_changed"]
        counts["changed_selected_set"] += comparison["selected_set_changed"]
        original, selected = set(comparison["reference_selected"]), set(row["selected_in_priority_order"])
        winners, losers = selected - original, original - selected
        for owner in selected:
            item = metadata[owner]
            counts["selected_episodes"] += 1
            counts["selected_active_after_pressure"] += item["active_after_pressure"]
            counts["selected_marked_for_pause"] += item["marked_for_pause"]
        if not winners and not losers:
            continue
        owner_rows = {item[0]: item for item in ordered}
        episode = {
            "selection_monotonic_ms": row["monotonic_s"] * 1000.,
            "capacity_tokens": capacity, "competition": categories,
            "winners": [], "reference_only_losers": [],
        }
        for owners, target in ((winners, "winners"), (losers, "reference_only_losers")):
            for owner in sorted(owners):
                item = metadata[owner]
                key = identity(item["key"], grant=True)
                keys.add(key)
                episode[target].append({
                    "key": key, "category": owner_rows[owner][1],
                    "remaining_rank": owner_rows[owner][2],
                    "context_tokens": owner_rows[owner][3],
                    "logical_ready_before_resume": readiness[owner],
                    "active_after_pressure": item["active_after_pressure"],
                    "marked_for_pause": item["marked_for_pause"],
                })
        changed.append(episode)
    starts, decode_starts, domains = defaultdict(list), defaultdict(list), set()
    prior = -math.inf
    for batch in services:
        if batch.get("event") != "gpu_service_sample":
            continue
        instant = batch["service_start_monotonic_ms"]
        if instant < prior or not batch.get("monotonic_clock_domain"):
            raise ValueError("native service clock is missing or unordered")
        prior = instant
        domains.add(batch["monotonic_clock_domain"])
        for member in batch["request_samples"]:
            if any(member.get(name) is None for name in ("attempt_id", "session_id", "session_generation")):
                continue
            key = identity(member)
            if key not in keys or member.get("token_delta", 0) <= 0:
                continue
            starts[key].append(instant)
            if member.get("phase") == "decode":
                decode_starts[key].append(instant)
    if len(domains) > 1:
        raise ValueError("native service clock domains differ")
    changed_latencies, service_leads = [], []
    winner_keys, served_winner_keys = set(), set()
    for episode in changed:
        when = episode["selection_monotonic_ms"]
        for target in ("winners", "reference_only_losers"):
            for member in episode[target]:
                key = member["key"]
                values = starts[key]
                at = bisect_left(values, when)
                member["positive_native_service_before_choice"] = bool(at)
                member["first_positive_service_after_choice_ms"] = (
                    values[at] - when if at < len(values) else None
                )
                decode = decode_starts[key]
                pos = bisect_left(decode, when)
                member["first_positive_decode_after_choice_ms"] = (
                    decode[pos] - when if pos < len(decode) else None
                )
                if target == "winners":
                    winner_keys.add(key)
                    counts["changed_winners"] += 1
                    counts["changed_winners_served_before_choice"] += bool(at)
                    counts["changed_winners_revoked_by_immediate_pressure"] += not member["active_after_pressure"]
                    if member["active_after_pressure"] and at < len(values):
                        counts["changed_winners_with_following_positive_service"] += 1
                        changed_latencies.append(values[at] - when)
                        served_winner_keys.add(key)
        for winner in episode["winners"]:
            if not winner["active_after_pressure"]:
                continue
            for loser in episode["reference_only_losers"]:
                first = winner["first_positive_service_after_choice_ms"]
                other = loser["first_positive_service_after_choice_ms"]
                if first is not None and other is not None:
                    counts["changed_winner_loser_pairs"] += 1
                    counts["winner_served_before_reference_only_loser_pairs"] += first < other
                    service_leads.append(other - first)
    counts["unique_changed_winner_attempts"] = len(winner_keys)
    counts["unique_changed_winner_attempts_with_positive_service"] = len(served_winner_keys)
    if final is None:
        raise ValueError("final runtime status is missing")
    admission = final["agent_admission"]
    return {
        "counts": dict(counts),
        "initial_size_tier_competition": inspect_competition(rows, population)["counts"],
        "grant_episodes_by_path": {
            "periodic_resume": counts["selected_episodes"], **dict(grant_counts),
            "eager_first_registration": None,
        },
        "final_admission_counts": admission["counts"],
        "declared_ranking_scope": admission["workflow_size_resume_scope"],
        "other_admission_paths": admission["workflow_size_other_admission_paths"],
        "rank_thread_cpu_us": {"sum": sum(cpu_us), **summary(cpu_us)},
        "whole_control_thread_cpu_us": {"sum": sum(controls_cpu_us), **summary(controls_cpu_us)},
        "changed_winner_positive_service_delay_ms": summary(changed_latencies),
        "actual_winner_lead_over_reference_only_loser_ms": summary(service_leads),
        "changed_decisions": changed,
        "event_counts": dict(kinds),
        "scope": {
            "complete_recorded_choices_reproduced": True,
            "positive_native_progress_required": True,
            "same_request_attempt_context_and_session_required": True,
            "competition_uses_current_remaining_ranks": True,
            "initial_size_tier_competition_is_a_stricter_separate_metric": True,
            "first_registration_grants_not_explicitly_journaled": True,
            "event_grant_competing_candidates_not_journaled": True,
            "removing_category_restrictions_is_only_an_offline_diagnostic": True,
            "native_physical_fit_of_unchosen_candidates_verified": False,
            "future_intervening_revocations_excluded": False,
            "service_order_in_this_run_is_not_a_counterfactual_JCT_gain": True,
            "GPU_action": False,
        },
    }


def compare_propagation(funnel, candidate_summary, control_summary, candidate_receipts, control_receipts):
    """Compare fixed logical requests across arms without equating their native sessions."""
    winners = {
        tuple(member["key"])[0]: member
        for episode in funnel["changed_decisions"] for member in episode["winners"]
    }
    arms = {}
    for label, summary_row, receipts in (
        ("candidate", candidate_summary, candidate_receipts),
        ("control", control_summary, control_receipts),
    ):
        anchors = {
            row["workflow_id"]: summary_row["workflow_arrival_origin_monotonic_ms"]
            + row["offset_seconds"] * 1000.
            for row in summary_row["workflow_arrivals"]["arrival_schedule"]
        }
        bound = {}
        for receipt in receipts:
            request = receipt["request_id"]
            if request not in winners:
                continue
            if request in bound or receipt.get("exact_tokens_verified") is not True:
                raise ValueError("propagation requires unique exact completed requests")
            native = receipt["native_receipt"]
            logical = winners[request]["key"][1:5]
            if (
                tuple(native["identity"]) != tuple(logical)
                or native["clock_domain"] != summary_row["workflow_arrival_clock_domain"]
            ):
                raise ValueError("propagation request identity or native arrival clock differs")
            workflow = logical[0]
            first = receipt.get("response_metrics", {}).get("first_output_monotonic_ms")
            if type(first) not in (float, int) or not math.isfinite(first):
                raise ValueError("propagation needs observed first output")
            bound[request] = {
                "first_output_s": (first - anchors[workflow]) / 1000.,
                "request_complete_s": (receipt["native_complete_monotonic_ms"] - anchors[workflow]) / 1000.,
                "workflow_jct_s": summary_row["workflow_elapsed_ms"][workflow] / 1000.,
                "token_work": tuple(receipt[name] for name in (
                    "prompt_tokens", "output_tokens", "prompt_sha256", "output_sha256",
                )),
            }
        if set(bound) != set(winners):
            raise ValueError("propagation lacks a completed selected request")
        arms[label] = bound
    if (
        candidate_summary["workflow_arrivals"] != control_summary["workflow_arrivals"]
        or candidate_summary["workflow_elapsed_ms"].keys() != control_summary["workflow_elapsed_ms"].keys()
    ):
        raise ValueError("propagation requires matched arrivals and whole workflows")
    records = []
    for request, member in winners.items():
        actual, original = arms["candidate"][request], arms["control"][request]
        if actual["token_work"] != original["token_work"]:
            raise ValueError("propagation changed exact request work")
        records.append({
            "request_id": request, "workflow_id": member["key"][1],
            "candidate": {k: v for k, v in actual.items() if k != "token_work"},
            "control": {k: v for k, v in original.items() if k != "token_work"},
            "advancement_s": {
                name: original[name] - actual[name]
                for name in ("first_output_s", "request_complete_s", "workflow_jct_s")
            },
        })
    selected_workflows = {member["key"][1] for member in winners.values()}
    gains = {
        workflow: (
            control_summary["workflow_elapsed_ms"][workflow]
            - candidate_summary["workflow_elapsed_ms"][workflow]
        ) / 1000.
        for workflow in candidate_summary["workflow_elapsed_ms"]
    }
    return {
        "requests": records,
        "summary": {
            "changed_winner_requests": len(records),
            "requests_with_earlier_first_output_than_control": sum(
                row["advancement_s"]["first_output_s"] > 0 for row in records
            ),
            "requests_with_earlier_completion_than_control": sum(
                row["advancement_s"]["request_complete_s"] > 0 for row in records
            ),
            "unique_selected_workflows": len(selected_workflows),
            "selected_workflow_JCT_advancement_sum_s": sum(gains[w] for w in selected_workflows),
            "other_workflow_JCT_advancement_sum_s": sum(v for w, v in gains.items() if w not in selected_workflows),
            "whole_population_JCT_advancement_sum_s": sum(gains.values()),
            "whole_population_mean_JCT_advancement_s": sum(gains.values()) / len(gains),
        },
        "scope": {
            "positive_advancement_means_candidate_earlier": True,
            "cross_arm_identity_is_logical_request_not_native_session": True,
            "all_workflows_included_in_population_sums": True,
            "one_pair_not_exclusive_causal_attribution_to_individual_choices": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve every earlier funnel audit")
    if complete_work(args.registration) is None:
        raise ValueError("require complete work, fresh drain and actual owned exit")
    plan, population = causal.load(args.registration), causal.load(args.population)
    if (
        population.get("population_sha256") != population_fingerprint(population["workflows"])
        or {row["workflow_id"] for row in population["workflows"]} != set(plan["workflow_ids"])
        or any(
            Path(row["source_capture"]).resolve() != Path(plan["source_arm"]).resolve()
            or row["token_source_sha256"] != plan["token_source_sha256"]
            for row in population["workflows"]
        )
    ):
        raise ValueError("audit population differs from the complete comparison")
    for name in ("beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py"):
        if sha256_file(ROOT / name) != plan["source_sha256"].get(name):
            raise ValueError("analysis policy differs from the frozen comparison")
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"), population["workflows"],
    )
    additional = []
    if args.control is not None:
        if complete_work(args.control) != complete_work(args.registration):
            raise ValueError("propagation needs exact complete matched control work")
        control = causal.load(args.control)
        for name in ("workflow_arrivals", "token_source_sha256", "tool_timing_overlay"):
            if plan[name] != control[name]:
                raise ValueError("propagation control changes arrivals, tokens or tools")
        control_arm = Path(control["run_root"]) / "full"
        summaries = [arm / "replay/summary.json", control_arm / "replay/summary.json"]
        result["matched_control_propagation"] = compare_propagation(
            result, *(causal.load(path) for path in summaries),
            reader(arm / "replay/native_requests.jsonl"),
            reader(control_arm / "replay/native_requests.jsonl"),
        )
        additional = [args.control, *summaries]
    result.update(
        registration=str(args.registration.resolve()),
        inputs_sha256={
            str(path.resolve()): sha256_file(path)
            for path in (args.registration, args.population, Path(__file__), *additional)
        },
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, performance_accepted=False,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    causal.reservation.write_json(args.output, result)
    print(result["counts"])


if __name__ == "__main__":
    main()
