"""Replay bounded capacity holds and link exact targets to cached native progress."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys


POLICY_FILES = (
    "beliefkv/policy/workflow_capacity_hold.py",
    "scripts/audit_workflow_combination_choices.py",
    "scripts/audit_periodic_admission_decisions.py",
)
GRANT_EVENTS = frozenset((
    "agent_periodic_admission_decision", "agent_dispatch_free_capacity_admitted",
    "agent_continuation_free_capacity_admitted", "agent_join_free_capacity_admitted",
    "agent_join_siblings_coadmitted", "agent_join_blocked_capacity_admitted",
    "agent_initial_registration_decision",
))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_completion(progress, proof, cohort, registration_sha, cohort_sha):
    if (
        progress["registration_sha256"] != registration_sha
        or proof["registration_sha256"] != registration_sha
        or proof["cohort_sha256"] != cohort_sha
        or proof["cohort"] != cohort
        or proof["protocol"] != "all_fixed_measured_workflows_completed"
        or proof["actual_owned_exit_verified"] is not True
        or len(set(cohort["measured_workflow_ids"])) != 18
    ):
        raise ValueError("require completed fixed18 work and actual native exit")


def load_policy(root):
    sys.path.insert(0, str(root))
    policy = importlib.import_module("scripts.audit_workflow_combination_choices")
    if Path(policy.__file__).resolve() != (root / POLICY_FILES[1]).resolve():
        raise ValueError("another policy tree is already imported")
    return policy


def grant_members(row):
    event = row["event"]
    if event == "agent_periodic_admission_decision":
        selected = set(row["resumed_program_ids"])
        return [
            item["key"] for item in row["programs"]
            if item["invocation_id"] in selected and item["active_after_pressure"] is True
        ]
    if event == "agent_initial_registration_decision":
        return [row["key"]] if row["active_after_arrival"] is True else []
    return [item["key"] for item in row["programs"]]


def positive_progress(episode):
    retained = episode.get("selection_retained", episode.get("active_after_pressure"))
    values = [episode.get("first_new_useful_" + name + "_ms") for name in (
        "launch", "service", "completion",
    )]
    if any(value is not None and (not finite(value) or value < 0) for value in values):
        raise ValueError("invalid cached positive-progress clock")
    if all(value is not None for value in values) and not values[0] <= values[1] <= values[2]:
        raise ValueError("cached native progress clocks are out of order")
    return retained is True and all(value is not None for value in values)


def inspect(records, progress, measured, policy):
    """Preserve sequence IDs; a hold removes grants and never proves service itself."""
    episodes = {}
    for episode in progress["ready_grant_episodes"]:
        identifier = tuple(episode["key"]), episode["journal_sequence"]
        if identifier in episodes:
            raise ValueError("duplicate exact native grant/progress episode")
        episodes[identifier] = episode
    cycles, by_cycle, grants = [], {}, defaultdict(list)
    counts = Counter({name: 0 for name in (
        "hold_decisions_replayed", "cycles", "measured_focus_cycles",
        "focus_cycles_with_later_grant", "focus_cycles_with_later_positive_service",
        "withheld_request_cycles", "withheld_request_cycles_with_later_positive_service",
    )})
    cpu_ns = wall_ns = 0
    last_started = last_decision = None
    for sequence, row in records:
        event = row["event"]
        if event in GRANT_EVENTS:
            if not finite(row.get("monotonic_s")):
                raise ValueError("grant lacks a monotonic clock")
            for member in grant_members(row):
                key = policy.identity(member, grant=True)
                grants[key].append({
                    "key": key, "journal_sequence": sequence, "event": event,
                    "monotonic_s": row["monotonic_s"],
                })
        if event != "workflow_combination_selection" or (
            row["decision"]["reason"] != "ready_capacity_accumulation"
        ):
            continue
        decision = policy.replay_capacity_hold(row)
        hold = row["capacity_accumulation"]
        if row.get("selection_policy") != "maximal_benefit" or row.get("source") not in (
            "periodic_decay_resume", "undiscounted_free_dispatch",
        ) or row.get("physical_capacity_credit") is not False or (
            hold["max_wait_ms"] > 2000
            or last_decision is not None and hold["decision_monotonic_s"] < last_decision
        ):
            raise ValueError("invalid capacity authority or hold clock order")
        for name in ("metadata_and_selection_cpu_ns", "metadata_and_selection_wall_ns"):
            if type(row.get(name)) is not int or row[name] < 0:
                raise ValueError("hold lacks observed selection cost")
        cpu_ns += row["metadata_and_selection_cpu_ns"]
        wall_ns += row["metadata_and_selection_wall_ns"]
        last_decision = hold["decision_monotonic_s"]
        focus = policy.identity(hold["keys"][decision["focus"]], grant=True)
        identifier = focus, hold["started_monotonic_s"], hold["deadline_monotonic_s"]
        cycle = by_cycle.get(identifier)
        if cycle is None:
            if decision["continuing_existing_hold"]:
                raise ValueError("continuing hold has no observed start")
            if last_started is not None and (
                hold["started_monotonic_s"] - last_started < hold["cooldown_ms"] / 1000. - 1e-9
            ):
                raise ValueError("capacity accumulation restarted before its cooldown")
            last_started = hold["started_monotonic_s"]
            cycle = {
                "focus": focus, "focus_measured": focus[1] in measured,
                "started_monotonic_s": hold["started_monotonic_s"],
                "deadline_monotonic_s": hold["deadline_monotonic_s"],
                "first_journal_sequence": sequence, "decisions": [], "withheld": {},
            }
            by_cycle[identifier] = cycle
            cycles.append(cycle)
        elif not decision["continuing_existing_hold"]:
            raise ValueError("duplicate capacity-hold start")
        for owner in decision["held_reference"]:
            key = policy.identity(hold["keys"][owner], grant=True)
            cycle["withheld"].setdefault(key, {
                "key": key, "measured": key[1] in measured,
                "first_held_monotonic_s": hold["decision_monotonic_s"],
                "first_held_journal_sequence": sequence,
            })
        cycle["decisions"].append({
            "journal_sequence": sequence, "source": row["source"],
            "decision_monotonic_s": hold["decision_monotonic_s"],
            "reference": decision.get("reference", hold["reference"]),
            "selected": decision["selected"], "held_reference": decision["held_reference"],
            "logical_capacity_tokens": hold["logical_capacity_tokens"],
            "focus_logical_gap_tokens": decision["focus_logical_gap_tokens"],
            "held_reference_tokens": decision["held_reference_tokens"],
            "running_width": hold["running_width"],
        })
        counts["hold_decisions_replayed"] += 1
    grant_sequences = {
        key: [row["journal_sequence"] for row in values] for key, values in grants.items()
    }

    def follow(key, after_sequence, origin):
        values = grants.get(key, ())
        start = bisect_right(grant_sequences.get(key, ()), after_sequence)
        following = values[start:]
        result = {
            "first_later_grant": None, "first_later_positive_progress": None,
            "unobserved_progress_grants": 0,
            "progress_attribution": "later exact service; no exclusive causal attribution",
        }
        for grant in following:
            when = grant["monotonic_s"]
            if result["first_later_grant"] is None:
                result["first_later_grant"] = {
                    **grant, "observed_delay_from_hold_ms": (when - origin) * 1000.,
                }
            episode = episodes.get((key, grant["journal_sequence"]))
            if episode is None:
                result["unobserved_progress_grants"] += 1
                continue
            if abs(episode["selection_ms"] - when * 1000.) > 1e-4:
                raise ValueError("cached progress belongs to another grant clock")
            if not positive_progress(episode):
                result["unobserved_progress_grants"] += 1
                continue
            result["first_later_positive_progress"] = {
                **grant,
                "hold_to_positive_service_ms": (
                    when - origin
                ) * 1000. + episode["first_new_useful_service_ms"],
                "grant_to_positive_service_ms": episode["first_new_useful_service_ms"],
                "grant_service_within_observed_boundary": episode.get(
                    "new_service_before_next_control_or_intervention",
                ),
                "progress_in_original_first_later_grant": (
                    grant["journal_sequence"]
                    == result["first_later_grant"]["journal_sequence"]
                ),
            }
            break
        return result

    for cycle in cycles:
        cycle["focus_followup"] = follow(
            cycle["focus"], cycle["first_journal_sequence"], cycle["started_monotonic_s"],
        )
        withheld = []
        for key, row in cycle.pop("withheld").items():
            row["followup"] = follow(
                key, row["first_held_journal_sequence"], row["first_held_monotonic_s"],
            )
            withheld.append(row)
        cycle["withheld_requests"] = withheld
        counts["cycles"] += 1
        counts["measured_focus_cycles"] += cycle["focus_measured"]
        counts["focus_cycles_with_later_grant"] += (
            cycle["focus_followup"]["first_later_grant"] is not None
        )
        counts["focus_cycles_with_later_positive_service"] += (
            cycle["focus_followup"]["first_later_positive_progress"] is not None
        )
        counts["withheld_request_cycles"] += len(withheld)
        counts["withheld_request_cycles_with_later_positive_service"] += sum(
            row["followup"]["first_later_positive_progress"] is not None for row in withheld
        )
    counts["distinct_focus_request_attempts"] = len({tuple(row["focus"]) for row in cycles})
    return {
        "counts": dict(counts), "cycles": cycles,
        "hold_selection_thread_cpu_ms": cpu_ns / 1e6,
        "hold_selection_wall_ms": wall_ns / 1e6,
        "scope": {
            "replays_executed_hold_policy": True,
            "fixed_start_deadline_and_global_cooldown_verified": True,
            "all_recorded_admission_paths_observed": True,
            "positive_service_cache_covers_periodic_and_free_grants_only": True,
            "missing_grant_or_progress_is_unknown": True,
            "hold_deadline_not_assumed_to_be_actual_release_time": True,
            "following_service_not_exclusive_grant_causality": True,
            "withheld_wait_not_counterfactual_added_delay": True,
            "migration_recomputation_and_total_control_CPU_not_derived": True,
            "hold_occurrences_are_not_JCT_savings": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }


def sealed_records(path, prefix):
    size, digest = 0, hashlib.sha256()
    interesting = tuple(name.encode() for name in GRANT_EVENTS) + (
        b"workflow_combination_selection",
    )
    with path.open("rb") as stream:
        for sequence, raw in enumerate(stream):
            size += len(raw)
            digest.update(raw)
            if any(
                raw.startswith(b'{"event":' + gap + b'"' + event + b'"')
                for gap in (b"", b" ") for event in interesting
            ):
                yield sequence, json.loads(raw)
    if size != prefix["complete_prefix_bytes"] or digest.hexdigest() != prefix["complete_prefix_sha256"]:
        raise ValueError("grant journal differs from the exact native service cache")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "registration", "progress", "proof", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior capacity-service audit")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("require CPU0 nice19 with CUDA hidden")
    registration, progress, proof, cohort = (
        json.loads(path.read_bytes()) for path in (
            args.registration, args.progress, args.proof, args.cohort,
        )
    )
    registration_sha = sha256(args.registration)
    validate_completion(progress, proof, cohort, registration_sha, sha256(args.cohort))
    certificate = Path(proof["certificate"])
    if sha256(certificate) != proof["certificate_sha256"]:
        raise ValueError("fixed18 completion certificate changed")
    for name in POLICY_FILES:
        if sha256(args.source_root / name) != registration["source_sha256"].get(name):
            raise ValueError("replayed policy differs from executed source")
    journal = Path(registration["run_root"]) / "full/opportunities/admission_opportunities.jsonl"
    prefixes = [
        value for name, value in progress["input_jsonl_prefixes"].items()
        if Path(name).resolve() == journal.resolve()
    ]
    if len(prefixes) != 1 or journal.stat().st_size != prefixes[0]["complete_prefix_bytes"]:
        raise ValueError("require a unique complete grant journal seal")
    inputs = {
        str(path.resolve()): sha256(path) for path in (
            Path(__file__), args.registration, args.progress, args.proof, args.cohort,
            certificate,
            *(args.source_root / name for name in POLICY_FILES),
        )
    }
    policy = load_policy(args.source_root)
    result = inspect(
        sealed_records(journal, prefixes[0]), progress,
        set(cohort["measured_workflow_ids"]), policy,
    )
    if any(sha256(name) != expected for name, expected in inputs.items()):
        raise ValueError("audit inputs changed while reading")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(), inputs_sha256=inputs,
        registration=str(args.registration.resolve()),
        journal=str(journal), journal_prefix=prefixes[0],
    )
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"counts": result["counts"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
