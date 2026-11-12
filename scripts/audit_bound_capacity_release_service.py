"""Audit explicitly paired tool releases, grants and bounded native service."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_periodic_admission_decisions import inspect, observation_phases
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution

RELEASE_EVENTS = {
    "predicted_tool_kv_evicted": "freed_units",
    "parent_pressure_demoted": "freed_units",
    "terminal_child_cache_reclaimed": "freed_device_units",
    "terminal_workflow_root_cache_reclaimed": "freed_device_units",
}
DONOR_FIELDS = (
    ("workflow_id", "root_workflow_id"), ("invocation_id", "invocation_id"),
    ("context_id", "context_id"), ("context_epoch", "context_epoch"),
    ("session_id", "session_id"), ("session_generation", "session_generation"),
)


def full_units(record):
    values = record.get(RELEASE_EVENTS[record["event"]], {})
    units = values.get("0", values.get(0, 0))
    if type(units) is not int or units < 0:
        raise ValueError("physical release needs a nonnegative native FULL count")
    return units


class Observations:
    def __init__(self):
        self.releases = []
        self.grants = {}
        self.replacements = {}
        self.offsets = []
        self.periodic_publication_offsets = []
        self.counts = Counter()

    def records(self, records):
        for sequence, record in enumerate(records):
            event = record.get("event")
            if event in RELEASE_EVENTS:
                units = full_units(record)
                self.counts[event + "_records"] += 1
                self.counts[event + "_FULL_tokens"] += units
                if units:
                    self.releases.append((sequence, record))
            if event == "agent_dispatch_free_capacity_admitted":
                self.grants[sequence] = record
            elif event == "agent_predicted_tool_replacement":
                self.replacements[sequence] = record
            if event in ("agent_periodic_admission_decision", "agent_dispatch_free_capacity_admitted"):
                wall, mono = record.get("ts_ms"), record.get("monotonic_s")
                if not finite_number(wall) or not finite_number(mono):
                    raise ValueError("grant and control clocks are required")
                if event == "agent_dispatch_free_capacity_admitted":
                    self.offsets.append(wall - mono * 1000.)
                else:
                    self.periodic_publication_offsets.append(wall - mono * 1000.)
            yield record


def paired_paths(progress, observed):
    """Use validated replacement links, never the next unrelated grant."""
    index = defaultdict(list)
    for sequence, row in observed.releases:
        if row["event"] == "predicted_tool_kv_evicted":
            index[row.get("beneficiary_request_id"), row.get("beneficiary_context_id")].append(
                (sequence, row)
            )
    episodes = {
        (tuple(row["key"]), row["journal_sequence"]): row
        for row in progress["ready_grant_episodes"] if row.get("grant_kind") == "free_budget"
    }
    consumer_identities = defaultdict(set)
    for episode in progress["ready_grant_episodes"]:
        key = tuple(episode["key"])
        consumer_identities[key[0], key[3]].add(key)
    rows, used = [], set()
    for link in progress["published_grant_interventions"]["links"]:
        grant_sequence = link["grant_journal_sequence"]
        grant = observed.grants[grant_sequence]
        marker = observed.replacements[link["replacement_journal_sequence"]]
        key = identity(marker["consumer"], grant=True)
        if tuple(link["consumer_key"]) != key:
            raise ValueError("replacement link changed its exact consumer")
        grant_ms = link["grant_monotonic_s"] * 1000.
        if grant["monotonic_s"] != link["grant_monotonic_s"]:
            raise ValueError("replacement link changed its actual grant time")
        result = {
            "consumer_key": key, "grant_journal_sequence": grant_sequence,
            "grant_monotonic_ms": grant_ms, "release_record_matched": False,
        }
        if len(consumer_identities[key[0], key[3]]) != 1:
            result["status"] = "ambiguous_consumer_attempt_or_session_identity"
            rows.append(result)
            continue
        candidates = [
            (sequence, row)
            for sequence, row in index.get((key[0], key[3]), ())
            if sequence < grant_sequence and sequence not in used
            and row.get("dispatch_stage") == "event_tool_replacement"
            and all(
                type(row.get(left)) is type(marker["donor"].get(right))
                and row.get(left) == marker["donor"].get(right)
                for left, right in DONOR_FIELDS
            )
        ]
        if not candidates:
            result["status"] = "no_new_exact_donor_consumer_release_record"
            rows.append(result)
            continue
        release_sequence, release = max(candidates, key=lambda pair: pair[0])
        wall = release.get("ts_ms")
        if not finite_number(wall) or wall > grant["ts_ms"]:
            raise ValueError("release publication must precede the paired grant")
        used.update(sequence for sequence, _row in candidates)
        episode = episodes.get((key, grant_sequence))
        if episode is None:
            raise ValueError("paired replacement lacks its exact free-grant progress episode")
        delay = grant["ts_ms"] - wall
        result.update(
            release_record_matched=True, release_journal_sequence=release_sequence,
            release_ts_ms=wall, last_release_record_to_grant_ms=delay,
            last_record_freed_FULL_tokens=full_units(release),
            last_record_freed_bytes=release.get("freed_bytes"),
            grant_to_new_service_ms=episode["first_new_useful_service_ms"],
            grant_to_new_launch_ms=episode["first_new_useful_launch_ms"],
            grant_to_new_completion_ms=episode["first_new_useful_completion_ms"],
            observation_window_end_ms=episode["observation_window_end_ms"],
            observation_window_end_reason=episode["observation_window_end_reason"],
            service_before_next_control_or_intervention=episode[
                "new_service_before_next_control_or_intervention"
            ],
            completion_before_next_control_or_intervention=episode[
                "new_completion_before_next_control_or_intervention"
            ],
        )
        bounded = result["service_before_next_control_or_intervention"] is True
        result["status"] = "bounded_new_service_verified" if bounded else "new_service_outside_grant_window"
        result["last_release_record_to_new_service_ms"] = (
            delay + result["grant_to_new_service_ms"] if bounded else None
        )
        rows.append(result)
    return rows


def summarize(rows):
    matched = [row for row in rows if row["release_record_matched"]]
    bounded = [row for row in matched if row["service_before_next_control_or_intervention"] is True]
    return {
        "linked_replacements": len(rows), "matched_physical_release_records": len(matched),
        "bounded_new_service_verified": len(bounded),
        "service_not_verified_in_grant_window": len(matched) - len(bounded),
        "release_to_grant_ms": distribution([row["last_release_record_to_grant_ms"] for row in matched]),
        "bounded_grant_to_service_ms": distribution([row["grant_to_new_service_ms"] for row in bounded]),
        "bounded_release_to_service_ms": distribution([
            row["last_release_record_to_new_service_ms"] for row in bounded
        ]),
    }


def audit(registration_path, cohort_path, output):
    if output.exists():
        raise FileExistsError("preserve previous capacity-release audits")
    registration = json.loads(registration_path.read_bytes())
    arrivals_path = Path(registration["workflow_arrivals_path"])
    cohort, arrivals = (json.loads(path.read_bytes()) for path in (cohort_path, arrivals_path))
    proof = verify_execution(registration_path, cohort_path)
    if arrivals != registration["workflow_arrivals"]:
        raise ValueError("arrival table differs from actual serving")
    arm = Path(registration["run_root"]) / "full"
    reader, observed = SealedRecords(), Observations()
    progress = inspect(
        observed.records(reader(arm / "opportunities/admission_opportunities.jsonl")),
        reader(arm / "server/runtime_audit.jsonl"), include_free_grants=True,
    )
    phases = observation_phases(
        progress, arrivals, cohort, reader(arm / "replay/runtime_events.replay.jsonl"),
    )
    offsets = observed.offsets
    if not offsets or max(offsets) - min(offsets) > 5.:
        raise ValueError("wall-to-monotonic admission clocks changed beyond the declared 5 ms limit")
    rows = paired_paths(progress, observed)
    origin = phases["origin_monotonic_ms"]
    common = cohort["throughput_window_offset_seconds"]
    end = phases["last_measured_completion_offset_s"]
    selected = set(cohort["measured_workflow_ids"])
    summaries = {"all_observed": summarize(rows)}
    for name, window in (
        ("fixed_common_throughput_window", common),
        ("first_measured_arrival_to_last_completion", (common[0], end)),
    ):
        subset = [
            row for row in rows if window[0] <= (row["grant_monotonic_ms"] - origin) / 1000. < window[1]
        ]
        summaries[name] = {
            **summarize(subset),
            "measured_consumers": summarize([row for row in subset if row["consumer_key"][1] in selected]),
        }
    for name, prefix in reader.prefixes.items():
        if Path(name).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(name)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed input changed during the release audit")
    source_paths = (
        Path(__file__), ROOT / "scripts/audit_periodic_admission_decisions.py",
        ROOT / "scripts/audit_free_dispatch_service.py", ROOT / "scripts/audit_sealed_wait_prefetch.py",
        ROOT / "scripts/verify_measured_execution.py",
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration_path.resolve()), "measured_execution": proof,
        "summaries": summaries, "paired_paths": rows,
        "all_recorded_release_counts": dict(observed.counts),
        "free_and_periodic_progress": progress,
        "observation_phases": phases,
        "admission_clock_offset_ms": {
            "count": len(offsets), "median": statistics.median(offsets),
            "range_ms": max(offsets) - min(offsets),
            "source": "immediate_free_grant_time_pair",
        },
        "periodic_publication_offset_relative_to_free_clock_ms": distribution([
            value - statistics.median(offsets) for value in observed.periodic_publication_offsets
        ]),
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (registration_path, cohort_path, arrivals_path, *source_paths)
        },
        "input_jsonl_prefixes": reader.prefixes,
        "scope": {
            "only_explicit_consumer_donor_and_adjacent_validated_grant_links": True,
            "last_positive_allocator_release_publication_is_the_start_not_first_release_or_DMA": True,
            "release_clock_uses_local_grant_wall_to_monotonic_anchor": True,
            "periodic_start_to_later_publication_pair_is_not_clock_alignment": True,
            "same_attempt_session_and_positive_native_progress_required": True,
            "new_launch_and_service_must_precede_next_control_or_same_agent_intervention": True,
            "service_clock_is_worker_interval_not_CUDA_kernel": True,
            "physical_pages_exclusively_assigned_to_this_consumer_proven": False,
            "other_release_paths_or_global_release_to_service_coverage_complete": False,
            "latencies_not_removable_JCT_or_summed_workflow_critical_path": True,
            "no_scheduler_policy_or_workload_acceptance_change": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    write(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("use nice19 and hidden CUDA")
    result = audit(args.registration, args.cohort, args.output)
    print(json.dumps({"summaries": result["summaries"], "GPU_action": False}))


if __name__ == "__main__":
    main()
