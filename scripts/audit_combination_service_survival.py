"""Check actual grant survival and earlier incomplete native service evidence."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_periodic_admission_decisions import useful_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


FIELDS = (
    "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch",
    "attempt_id", "session_id", "session_generation",
)


def queries(report):
    result = []
    for decision in report["decisions"]:
        for member in (*decision["periodic_service"], *decision["free_service"]):
            episode = member["episode"]
            if episode is None or episode["following_native_service_ms"] is None:
                raise ValueError("require a verified exact service for each original new selection")
            key = tuple(member["key"])
            if len(key) != len(FIELDS) or tuple(episode["key"]) != key:
                raise ValueError("grant and service must retain the full request identity")
            begin = episode["selection_ms"]
            end = begin + episode["first_new_useful_service_ms"]
            if not all(math.isfinite(value) for value in (begin, end)) or end < begin:
                raise ValueError("invalid actual grant-to-service interval")
            result.append({
                "key": key, "source": decision["source"],
                "grant_sequence": episode["journal_sequence"],
                "begin_ms": begin, "first_exact_service_ms": end,
                "bounded_service": episode["new_service_before_next_control_or_intervention"],
                "controls_with_same_active_identity": 0,
                "observed_inactive_controls": [], "unknown_control_identity": 0,
                "explicit_pause_records": [], "same_agent_regrants": [],
                "earlier_partial_positive_samples": 0, "earlier_unknown_progress_samples": 0,
                "earliest_partial_positive_ms": None,
                "worker_overlap_ms_by_phase": {"prefill": 0., "decode": 0.},
                "overlapping_prefill_request_counts": Counter(),
            })
    identities = [(row["key"], row["grant_sequence"]) for row in result]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate original grant episode")
    return result


def inspect(report, records, batches):
    rows = queries(report)
    owners, requests = defaultdict(list), defaultdict(list)
    for row in rows:
        owners[row["key"][2]].append(row)
        requests[row["key"][0]].append(row)
    for sequence, record in enumerate(records):
        event = record.get("event")
        when = record.get("monotonic_s")
        if type(when) not in (int, float) or not math.isfinite(when):
            continue
        when *= 1000.
        if event == "agent_periodic_admission_decision":
            members = {item["invocation_id"]: item for item in record["programs"]}
            for owner, episodes in owners.items():
                for row in episodes:
                    if not row["grant_sequence"] < sequence or not row["begin_ms"] <= when < row["first_exact_service_ms"]:
                        continue
                    member = members.get(owner)
                    if member is None or member.get("key") is None:
                        row["unknown_control_identity"] += 1
                        continue
                    if identity(member["key"], grant=True) != row["key"]:
                        row["unknown_control_identity"] += 1
                    elif member["active_before"] and member["active_after_pressure"]:
                        row["controls_with_same_active_identity"] += 1
                    else:
                        row["observed_inactive_controls"].append(when)
        pauses, regrants = [], []
        if event == "agent_full_offload_paused":
            pauses = [record["key"]["invocation_id"]]
        elif event == "workflow_remaining_pressure_pause" and record.get("marked_for_pause") is False:
            pauses = [record["selected"]]
        elif event == "agent_predicted_tool_replacement":
            pauses = [record["donor"]["invocation_id"]]
            regrants = [record["consumer"]["invocation_id"]]
        elif event in (
            "agent_dispatch_free_capacity_admitted", "agent_continuation_free_capacity_admitted",
            "agent_join_free_capacity_admitted", "agent_join_siblings_coadmitted",
            "agent_join_blocked_capacity_admitted",
        ):
            regrants = [item["key"]["invocation_id"] for item in record["programs"]]
        for label, identities in (("explicit_pause_records", pauses), ("same_agent_regrants", regrants)):
            for owner in identities:
                for row in owners.get(owner, ()):
                    if row["grant_sequence"] < sequence and row["begin_ms"] <= when < row["first_exact_service_ms"]:
                        row[label].append({"event": event, "monotonic_ms": when})
    previous_end = -math.inf
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        start, end = batch["service_start_monotonic_ms"], batch["complete_monotonic_ms"]
        if (
            not math.isfinite(start) or not math.isfinite(end) or end < start
            or start < previous_end - .01 or batch["phase"] not in ("prefill", "decode")
        ):
            raise ValueError("invalid or overlapping native worker interval")
        previous_end = end
        for row in rows:
            if row["bounded_service"] is True:
                continue
            overlap = max(0., min(end, row["first_exact_service_ms"]) - max(start, row["begin_ms"]))
            row["worker_overlap_ms_by_phase"][batch["phase"]] += overlap
            if overlap and batch["phase"] == "prefill":
                row["overlapping_prefill_request_counts"].update({
                    member["request_id"] for member in batch["request_samples"]
                    if member.get("phase", batch["phase"]) == "prefill"
                })
        for member in batch["request_samples"]:
            if member.get("request_id") not in requests:
                continue
            for row in requests[member["request_id"]]:
                if (
                    batch["launch_monotonic_ms"] < row["begin_ms"]
                    or not row["begin_ms"] <= batch["service_start_monotonic_ms"] < row["first_exact_service_ms"]
                    or any(
                        member.get(name) is not None and member[name] != value
                        for name, value in zip(FIELDS, row["key"])
                    )
                ):
                    continue
                progress = useful_work(member, member.get("phase", batch["phase"]))
                if progress is False:
                    continue
                if all(member.get(name) is not None for name in FIELDS[5:]) and progress:
                    raise ValueError("original first exact useful service missed an earlier exact sample")
                if progress is None:
                    row["earlier_unknown_progress_samples"] += 1
                else:
                    row["earlier_partial_positive_samples"] += 1
                    when = batch["service_start_monotonic_ms"]
                    row["earliest_partial_positive_ms"] = min(
                        when, row["earliest_partial_positive_ms"] or when,
                    )
    counts = Counter()
    for row in rows:
        counts["new_selection_episodes"] += 1
        counts["service_before_original_next_control_or_intervention"] += row["bounded_service"] is True
        counts["service_outside_original_observation_window"] += row["bounded_service"] is not True
        counts["with_observed_inactive_control"] += bool(row["observed_inactive_controls"])
        counts["with_explicit_pause_before_exact_service"] += bool(row["explicit_pause_records"])
        counts["with_same_agent_regrant_before_exact_service"] += bool(row["same_agent_regrants"])
        counts["with_unknown_control_identity"] += bool(row["unknown_control_identity"])
        counts["with_earlier_partial_positive_service"] += bool(row["earlier_partial_positive_samples"])
        counts["with_earlier_unknown_progress"] += bool(row["earlier_unknown_progress_samples"])
        counts["late_exact_with_earlier_partial_positive_service"] += (
            row["bounded_service"] is not True and bool(row["earlier_partial_positive_samples"])
        )
    return {
        "counts": dict(counts), "episodes": rows,
        "grant_to_first_exact_useful_service_ms": distribution(
            row["first_exact_service_ms"] - row["begin_ms"] for row in rows
        ),
        "scope": [
            "Population is the original new-to-reference R5 selections, not every active agent.",
            "Next global control is an observation cutoff, not proof of eligibility revocation.",
            "Observed active snapshots and absent pause records do not prove continuously active physical residency.",
            "Partial identity is compatible evidence only; no missing attempt or session is inferred.",
            "Worker overlaps describe service during late waits, not GPU kernel busy time or removable delay.",
            "Prefill request counts may share a worker batch and do not attribute its duration to each request.",
            "Launches before a grant, foreign identities and zero progress are not credited.",
            "Regrants and pauses are separately reported and never attributed to the initial selection.",
            "Observed service is not an exclusive causal JCT gain or a counterfactual scheduling result.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve previous evidence and use nice19 CPU-only execution")
    report = json.loads(args.service_analysis.read_bytes())
    if not report["complete_measured_execution_verified"]:
        raise ValueError("require the completed original actual-service audit")
    for name, digest in report["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError(f"original service-analysis input changed: {name}")
    reader = SealedRecords()
    reader.prefixes.update(report["input_jsonl_prefixes"])
    paths = list(reader.prefixes)
    opportunities = next(Path(name) for name in paths if name.endswith("/opportunities/admission_opportunities.jsonl"))
    batches = next(Path(name) for name in paths if name.endswith("/server/runtime_audit.jsonl"))
    result = inspect(report, reader(opportunities), reader(batches))
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        original_service_analysis=str(args.service_analysis.resolve()),
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.service_analysis, Path(__file__),
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_free_dispatch_service.py",
        )},
        GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps({"counts": result["counts"], "waits": result["grant_to_first_exact_useful_service_ms"]}))


if __name__ == "__main__":
    main()
