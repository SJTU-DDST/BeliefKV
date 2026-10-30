"""Audit recorded workflow-size choices without changing a running experiment."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


EVENT = "workflow_size_resume_selection"


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def inspect_choice(row):
    """Replay only one logged local resume decision, before pressure handling."""
    capacity = row.get("capacity_tokens")
    ordered = row.get("ordered")
    selected = row.get("selected_in_priority_order")
    oracle = row.get("rank_kind") == "oracle_remaining_model_tool_critical_path_seconds"
    if (
        row.get("event") != EVENT
        or type(capacity) is not int
        or type(ordered) is not list or type(selected) is not list
        or not finite(row.get("monotonic_s")) or row["monotonic_s"] < 0
        or type(row.get("selected_tokens")) is not int or row["selected_tokens"] < 0
    ):
        raise ValueError("invalid recorded workflow-size selection")
    for item in ordered:
        if (
            type(item) is not list or len(item) != 6
            or type(item[0]) is not str or not item[0]
            or any(type(item[i]) is not int for i in (1, 3, 4))
            or item[1] not in (0, 1, 2)
            or (
                (not finite(item[2]) or item[2] < 0) if oracle
                else (type(item[2]) is not int or item[2] not in (0, 1, 2))
            )
            or item[3] <= 0 or item[4] < 0
            or item[5] is not None and (
                type(item[5]) is not list or len(item[5]) != 3
                or item[5][:2] != item[1:3]
                or type(item[5][2]) is not str or not item[5][2]
            )
        ):
            raise ValueError("invalid resume candidate identity, category or budget")
    if len({item[0] for item in ordered}) != len(ordered):
        raise ValueError("resume journal repeats a candidate")
    buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
    if capacity <= buffer:
        if ordered or selected or row["selected_tokens"]:
            raise ValueError("empty-budget resume must preserve the fast return")
        return {
            "reference_order": [], "reference_selected": [],
            "order_changed": False, "selected_set_changed": False,
        }
    ranks = {item[0]: item[2] for item in ordered}
    groups = {item[0]: item[5][2] for item in ordered if item[5] is not None}
    original = ThunderActingDecayWorkingSet(capacity)
    candidate = WorkflowSizeWorkingSet(
        capacity, rank=lambda owner, now: ranks[owner], sibling_group=groups.get,
    )
    for policy in (original, candidate):
        policy.programs = {
            owner: ThunderProgram(
                owner, tokens, steps=1 if category == 1 else 2,
                acting=category == 2, queue_sequence=sequence,
            )
            for owner, category, _bucket, tokens, sequence, _group in ordered
        }
        policy._resume_now = row["monotonic_s"]
        policy._resume()
    replayed = candidate.last_selection
    # Canonicalize the tuple-based policy journal to the JSON wire format.
    observed_order = [
        [owner, category, bucket, tokens, sequence, list(group) if group else None]
        for owner, category, bucket, tokens, sequence, group in replayed["ordered"]
    ]
    if (
        observed_order != ordered
        or list(replayed["selected_in_priority_order"]) != selected
        or replayed["selected_tokens"] != row["selected_tokens"]
    ):
        raise ValueError("recorded choice does not reproduce its frozen policy")
    reference_order = [
        item[0] for item in sorted(ordered, key=lambda item: (item[1], item[3], item[4]))
    ]
    reference_selected = [
        owner for owner in reference_order if original.programs[owner].active
    ]
    return {
        "reference_order": reference_order, "reference_selected": reference_selected,
        "order_changed": reference_order != [item[0] for item in ordered],
        "selected_set_changed": set(reference_selected) != set(selected),
    }


def inspect(opportunities, batches):
    counts, rows, selected_keys = Counter(), [], set()
    cpu, wall, control_cpu, control_wall = [], [], [], []
    for row in opportunities:
        if row.get("event") != EVENT:
            continue
        comparison = inspect_choice(row)
        for name, destination in (
            ("thread_cpu_us", cpu), ("wall_us", wall),
            ("control_thread_cpu_us", control_cpu), ("control_wall_us", control_wall),
        ):
            value = row.get(name)
            if not finite(value) or value < 0:
                raise ValueError("selection needs measured nonnegative CPU and wall cost")
            destination.append(value)
        counts["control_passes"] += 1
        counts["nonempty_candidate_passes"] += bool(row["ordered"])
        counts["multiple_candidate_passes"] += len(row["ordered"]) > 1
        counts["changed_order_passes"] += comparison["order_changed"]
        counts["changed_selected_set_passes"] += comparison["selected_set_changed"]
        categories = {item[0]: item[1] for item in row["ordered"]}
        metadata = {}
        for item in row.get("candidates", ()):
            owner = item["key"]["invocation_id"]
            if owner in metadata:
                raise ValueError("duplicate candidate metadata")
            metadata[owner] = item
        buffer = ThunderActingDecayWorkingSet(1).buffer_tokens
        counts["candidate_budgets_exceed_remaining_capacity_passes"] += (
            sum(item[3] + buffer for item in row["ordered"]) > row["capacity_tokens"]
        )
        remaining = row["capacity_tokens"]
        competing, distinct_ranks = False, False
        for category in (0, 1, 2):
            peers = sorted(
                (item for item in row["ordered"] if item[1] == category),
                key=lambda item: (item[3], item[4]),
            )
            fitting = [item for item in peers if item[3] + buffer <= remaining]
            workflows = {
                metadata[item[0]]["key"]["root_workflow_id"]
                for item in fitting if item[0] in metadata
            }
            if (
                len(workflows) > 1
                and sum(item[3] + buffer for item in fitting) > remaining
            ):
                competing = True
                distinct_ranks |= len({item[2] for item in fitting}) > 1
            for item in peers:
                if item[3] + buffer <= remaining:
                    remaining -= item[3] + buffer
        counts["same_category_cross_workflow_capacity_competition_passes"] += competing
        counts["capacity_competition_with_different_effective_ranks_passes"] += distinct_ranks
        original = set(comparison["reference_selected"])
        for owner in row["selected_in_priority_order"]:
            counts["selected_agent_episodes"] += 1
            counts["selected_acting_category" if categories[owner] == 2 else "selected_reasoning_category"] += 1
            item = metadata.get(owner)
            if item is None:
                counts["selected_episodes_without_metadata"] += 1
                continue
            key = identity(item["key"], grant=True)
            active, marked = item.get("active_after_pressure"), item.get("marked_for_pause")
            if type(active) is not bool or type(marked) is not bool:
                raise ValueError("selected candidate needs explicit post-pressure status")
            selected_keys.add(key)
            counts["selected_active_after_pressure"] += active
            counts["selected_marked_for_pause"] += marked
            rows.append({
                "key": key, "selection_monotonic_ms": row["monotonic_s"] * 1000.,
                "resume_category": categories[owner],
                "different_from_local_reference": owner not in original,
                "active_after_pressure": active, "marked_for_pause": marked,
            })
    services, domains = defaultdict(list), set()
    last_start = -math.inf
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        timestamp = batch.get("service_start_monotonic_ms")
        domain = batch.get("monotonic_clock_domain")
        if not finite(timestamp) or timestamp < last_start or not domain:
            raise ValueError("native service clock is missing or unordered")
        last_start = timestamp
        domains.add(domain)
        for member in batch.get("request_samples", ()):
            if any(member.get(name) is None for name in (
                "attempt_id", "session_id", "session_generation",
            )):
                continue
            key = identity(member)
            if key in selected_keys:
                services[key].append(timestamp)
    if len(domains) > 1:
        raise ValueError("native service clock domains differ")
    followed, changed_followed, latencies = set(), set(), []
    for row in rows:
        values = services[row["key"]]
        position = bisect_left(values, row["selection_monotonic_ms"])
        if row["active_after_pressure"] and position < len(values):
            row["following_native_service_ms"] = values[position] - row["selection_monotonic_ms"]
            counts["active_selection_episodes_followed_by_service"] += 1
            followed.add(row["key"])
            latencies.append(row["following_native_service_ms"])
            if row["different_from_local_reference"]:
                changed_followed.add(row["key"])
        else:
            row["following_native_service_ms"] = None
    counts["unique_selected_request_attempts"] = len(selected_keys)
    counts["unique_selected_attempts_with_following_service"] = len(followed)
    counts["unique_local_choice_changes_with_following_service"] = len(changed_followed)
    return {
        "counts": dict(counts), "selection_thread_cpu_us": distribution(cpu),
        "selection_wall_us": distribution(wall),
        "whole_control_thread_cpu_us": distribution(control_cpu),
        "whole_control_wall_us": distribution(control_wall),
        "following_service_ms": distribution(latencies), "episodes": rows,
        "scope": {
            "local_resume_replayed_exactly": True,
            "reference_uses_same_recorded_candidates_and_capacity": True,
            "reference_equal_key_ties_preserve_recorded_order": True,
            "competition_uses_reference_budget_after_prior_categories": True,
            "competition_is_logical_not_native_ready_or_physical_feasibility": True,
            "post_resume_pressure_counterfactual_reconstructed": False,
            "native_capacity_or_physical_restoration_counterfactual": False,
            "following_service_excludes_earlier_service": True,
            "intervening_grants_or_pauses_excluded": False,
            "following_service_proves_exclusive_causal_attribution": False,
            "episode_counts_are_not_independent_requests": True,
            "whole_control_cost_is_not_incremental_model_cost": True,
            "latest_status_may_be_newer_than_sealed_selection_prefix": True,
            "JCT_or_throughput_counterfactual": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve the earlier selection audit")
    plan = causal.load(args.registration)
    causal.comparison.verify_sources(plan)
    for name in ("beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py"):
        if sha256_file(ROOT / name) != plan["source_sha256"].get(name):
            raise ValueError("analysis and frozen runtime policy sources differ")
    state = causal.load(args.registration.with_suffix(".state.json"))
    if args.complete:
        if complete_work(args.registration) is None:
            raise ValueError("require exact complete work, fresh drain and owned exit")
    elif state.get("status") != "running_full" or not all(
        state.get(name) and causal.reservation.alive(state[name])
        for name in ("watcher", "server", "driver")
    ):
        raise ValueError("collecting audit requires the actual live Full processes")
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    live = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    result.update({
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(args.registration.resolve()),
        "registration_sha256": sha256_file(args.registration),
        "complete_work": args.complete, "input_jsonl_prefixes": reader.prefixes,
        "latest_predictor_status": live["agent_admission"]["workflow_work_predictor"],
        "analysis_source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_workflow_size_selection.py",
                "beliefkv/policy/workflow_size_resume.py",
                "beliefkv/policy/paper_baselines.py",
            )
        },
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    causal.reservation.write_json(args.output, result)
    print({key: value for key, value in result.items() if key == "counts"})


if __name__ == "__main__":
    main()
