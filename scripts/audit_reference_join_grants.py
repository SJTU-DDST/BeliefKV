#!/usr/bin/env python3
"""Associate actual JOIN eligibility grants with observed native service."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_join_reentry_service import identity, join_reentries
from scripts.audit_native_system_costs import finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


GRANTS = {
    "agent_join_free_capacity_admitted", "agent_join_siblings_coadmitted",
    "agent_continuation_free_capacity_admitted",
    "agent_join_blocked_capacity_admitted",
}


def nonnegative_integer(value):
    return type(value) is int and value >= 0


def validate_grant(row):
    programs = row.get("programs")
    handoff = row.get("event") == "agent_join_blocked_capacity_admitted"
    continuation = row.get("event") == "agent_continuation_free_capacity_admitted"
    join_id = row.get("join_id")
    if (
        row.get("event") not in GRANTS or not finite_number(row.get("ts_ms"))
        or not finite_number(row.get("monotonic_s"))
        or row["ts_ms"] < 0 or row["monotonic_s"] < 0
        or not (
            continuation and join_id is None
            or type(join_id) is str and bool(join_id)
        )
        or type(programs) is not list or not 1 <= len(programs) <= 24
        or row["event"] in (
            "agent_join_free_capacity_admitted", "agent_continuation_free_capacity_admitted",
            "agent_join_blocked_capacity_admitted",
        ) and len(programs) != 1
        or row.get("native_capacity_authority_preserved") is not True
        or any(not nonnegative_integer(row.get(name)) for name in (
            "required_tokens", "remaining_after_tokens",
        ))
        or type(row.get("remaining_before_tokens")) is not int
        or not handoff and row["remaining_before_tokens"] < 0
    ):
        raise ValueError("invalid complete-budget JOIN grant")
    owners = set()
    workflows = set()
    for program in programs:
        key = program.get("key")
        if (
            type(key) is not dict
            or any(type(key.get(name)) is not str or not key[name] for name in (
                "request_id", "root_workflow_id", "invocation_id", "context_id",
            ))
            or any(not nonnegative_integer(key.get(name)) for name in ("context_epoch", "attempt_id"))
            or not nonnegative_integer(program.get("observed_context_tokens"))
            or program["observed_context_tokens"] == 0
            or not nonnegative_integer(program.get("complete_budget_tokens"))
            or program["complete_budget_tokens"] != program["observed_context_tokens"] + 100
        ):
            raise ValueError("invalid JOIN grant program identity or reference growth budget")
        session, generation = key.get("session_id"), key.get("session_generation")
        if (session is None) != (generation is None) or session is not None and (
            type(session) is not str or not session or not nonnegative_integer(generation)
        ):
            raise ValueError("invalid JOIN grant native session binding")
        owner = key["root_workflow_id"], key["invocation_id"], key["context_id"]
        if owner in owners:
            raise ValueError("JOIN grant repeats an owner")
        owners.add(owner)
        workflows.add(owner[0])
    released = 0
    if handoff:
        donors = row.get("donors")
        if (
            type(donors) is not list or not 1 <= len(donors) <= 64
            or row.get("released_device_tokens") != 0 or row.get("released_host_tokens") != 0
        ):
            raise ValueError("invalid JOIN blocked-budget donor evidence")
        seen = set(owners)
        for donor in donors:
            donor_key = donor.get("key", {})
            owner = tuple(donor_key.get(name) for name in (
                "root_workflow_id", "invocation_id", "context_id",
            ))
            if (
                any(type(value) is not str or not value for value in owner)
                or owner in seen
                or not nonnegative_integer(donor.get("observed_context_tokens"))
                or donor["observed_context_tokens"] <= 0
                or donor.get("complete_budget_tokens") != donor["observed_context_tokens"] + 100
            ):
                raise ValueError("invalid JOIN blocked-budget donor identity")
            seen.add(owner)
            released += donor["complete_budget_tokens"]
    if (
        len(workflows) != 1
        or sum(item["complete_budget_tokens"] for item in programs) != row["required_tokens"]
        or row["remaining_before_tokens"] + released - row["required_tokens"]
        != row["remaining_after_tokens"]
    ):
        raise ValueError("JOIN grant complete budgets do not reconcile")


def inspect(opportunities, replay_events, native_events, receipts, services, *, copies=None):
    grants = []
    states = 0
    for row in opportunities:
        if row.get("event") in GRANTS:
            validate_grant(row)
            grants.append(row)
        elif row.get("event") == "admission_runtime_state":
            state = row.get("agent_admission") or {}
            states += state.get("extra_join_grant_accounting") == (
                "refresh_observed_native_outputs_before_each_capacity_check"
            )
    episodes, _ = join_reentries(replay_events)
    join_requests = {
        (row["workflow_id"], row["join_id"], row["parent_invocation_id"]): row
        for row in episodes
    }
    submits, completions = {}, {}
    for event in native_events:
        rid = (event.get("attributes") or {}).get("request_id")
        if event.get("kind") == "llm_submit":
            if not rid or rid in submits or identity(event) is None:
                raise ValueError("ambiguous native request identity")
            submits[rid] = event
    for receipt in receipts:
        rid = receipt.get("request_id")
        if not rid or rid in completions or receipt.get("exact_tokens_verified") is not True:
            raise ValueError("invalid or duplicate exact completion receipt")
        completions[rid] = receipt
    programs, by_owner, by_request = [], defaultdict(list), defaultdict(list)
    for number, grant in enumerate(grants, 1):
        for program in grant["programs"]:
            key = program["key"]
            owner = key["root_workflow_id"], key["invocation_id"], key["context_id"]
            parent = grant["event"] in (
                "agent_join_free_capacity_admitted", "agent_join_blocked_capacity_admitted",
            )
            episode = join_requests.get((owner[0], grant["join_id"], owner[1])) if parent else None
            rid = key["request_id"]
            if parent:
                rid = episode.get("request_id") if episode else None
            row = {
                "grant_number": number, "grant_event": grant["event"], "join_id": grant["join_id"],
                "grant_ts_ms": grant["ts_ms"], "grant_monotonic_s": grant["monotonic_s"],
                "key": key, "complete_budget_tokens": program["complete_budget_tokens"],
                "service_request_id": rid, "parent_successor": parent,
                "grant_targets_submitted_join_successor": bool(
                    parent and rid == key["request_id"]
                ),
                "status": "join_successor_not_observed" if parent and rid is None else "native_submit_not_observed",
                "first_service_ts_ms": None, "next_service_after_grant_ts_ms": None,
                "copy_evidence_available": copies is not None,
                "verified_reused_copy_bytes_after_grant": 0 if copies is not None else None,
                "associated_copy_command_ids": [], "attempt_identity_verified": False,
            }
            programs.append(row)
            by_owner[owner].append(row)
            if rid is not None:
                by_request[rid].append(row)
    for rows in by_owner.values():
        rows.sort(key=lambda row: (row["grant_ts_ms"], row["grant_number"]))
        for index, row in enumerate(rows):
            row["next_grant_ts_ms"] = rows[index + 1]["grant_ts_ms"] if index + 1 < len(rows) else None
    clock = None
    count = 0
    for service in services:
        if service.get("event") != "gpu_service_sample":
            continue
        count += 1
        start, end = service.get("service_start_ts_ms"), service.get("complete_ts_ms")
        domain = service.get("monotonic_clock_domain")
        if (
            not finite_number(start) or not finite_number(end) or start > end
            or type(domain) is not str or not domain
            or clock is not None and domain != clock
            or service.get("sample_id") != f"native-service-{count:09d}"
        ):
            raise ValueError("invalid native service sequence or clock domain")
        clock = domain
        seen_requests = set()
        for sample in service.get("request_samples") or ():
            request = sample.get("request_id")
            if request in seen_requests:
                raise ValueError("duplicate request in native service sample")
            seen_requests.add(request)
            for row in by_request.get(request, ()):
                key, rid = row["key"], row["service_request_id"]
                submit = submits.get(rid)
                expected = (
                    key["root_workflow_id"], key["invocation_id"], key["context_id"],
                    submit["context_epoch"] if submit else key["context_epoch"],
                )
                if submit is None or identity(sample) != expected or identity(submit) != expected:
                    continue
                # An EOS grant precedes the successor epoch; a grant at actual
                # successor registration must match that exact request epoch.
                same_epoch = (
                    not row["parent_successor"] or row["grant_targets_submitted_join_successor"]
                )
                if not (
                    expected[3] == key["context_epoch"] if same_epoch
                    else expected[3] > key["context_epoch"]
                ):
                    continue
                row["service_identity"] = list(expected)
                previous = row["first_service_ts_ms"]
                row["first_service_ts_ms"] = start if previous is None else min(previous, start)
                row["service_clock_domain"] = domain
                if start >= row["grant_ts_ms"] and (
                    row["next_grant_ts_ms"] is None or start < row["next_grant_ts_ms"]
                ):
                    previous = row["next_service_after_grant_ts_ms"]
                    row["next_service_after_grant_ts_ms"] = start if previous is None else min(previous, start)
    for row in programs:
        rid, key = row["service_request_id"], row["key"]
        submit, receipt = submits.get(rid), completions.get(rid)
        if rid is None:
            continue
        if submit is None:
            continue
        row["status"] = "native_service_identity_unproven"
        if row["first_service_ts_ms"] is None:
            continue
        row["status"] = "completion_or_session_binding_unproven"
        native = (receipt or {}).get("native_receipt") or {}
        if (
            receipt is None or native.get("request_id") != rid
            or native.get("identity") != row["service_identity"]
            or key.get("session_id") is None
            or type(native.get("session_generation")) is not int
            or (native.get("session_id"), native.get("session_generation"))
            != (key["session_id"], key["session_generation"])
            or native.get("clock_domain") != row.get("service_clock_domain")
        ):
            continue
        row["session_binding_verified"] = True
        row["request_had_service_before_grant"] = row["first_service_ts_ms"] < row["grant_ts_ms"]
        row["logical_host_hit_tokens_at_service"] = (submit.get("attributes") or {}).get("cached_tokens_host")
        row["status"] = "service_after_grant_not_observed"
        following = row["next_service_after_grant_ts_ms"]
        if following is not None:
            row["status"] = "service_after_grant_verified"
            row["grant_to_next_native_service_ms"] = following - row["grant_ts_ms"]
    if copies is not None:
        assigned = set()
        for copy in copies:
            if (
                copy.get("source") != "join_ticket" or copy.get("full_first_service_reused") is not True
                or copy.get("full_reuse_proof_version") != 2
                or (copy.get("full_use_witness") or {}).get("full_reuse_proof") != (
                    "acknowledged_allocation_in_materialized_service_prefix"
                )
            ):
                continue
            when, size = copy.get("submit_ts_ms"), (copy.get("pool_bytes") or {}).get("kv")
            if not finite_number(when) or not nonnegative_integer(size):
                continue
            matches = [
                row for row in programs if row["parent_successor"]
                and row["status"] == "service_after_grant_verified"
                and copy.get("first_service_request_id") == row["service_request_id"]
                and (copy.get("workflow_id"), copy.get("invocation_id"), copy.get("context_id"), copy.get("join_id"))
                == (row["key"]["root_workflow_id"], row["key"]["invocation_id"], row["key"]["context_id"], row["join_id"])
                and row["grant_ts_ms"] <= when
                and (row["next_grant_ts_ms"] is None or when < row["next_grant_ts_ms"])
            ]
            if len(matches) > 1:
                raise ValueError("a copy would be credited to multiple grants")
            if matches:
                command = copy["command_id"]
                if command in assigned:
                    raise ValueError("copy command would be credited twice")
                assigned.add(command)
                matches[0]["verified_reused_copy_bytes_after_grant"] += size
                matches[0]["associated_copy_command_ids"].append(command)
    groups = []
    for number, grant in enumerate(grants, 1):
        members = [row for row in programs if row["grant_number"] == number]
        starts = [row["next_service_after_grant_ts_ms"] for row in members if row["status"] == "service_after_grant_verified"]
        groups.append({
            "grant_number": number, "event": grant["event"], "join_id": grant["join_id"],
            "newly_granted_programs": len(members), "verified_served_programs": len(starts),
            "newly_granted_service_start_spread_ms": (
                max(starts) - min(starts) if len(starts) == len(members) and len(starts) >= 2 else None
            ),
        })
    return {
        "summary": {
            "journal_grants": len(grants), "journal_programs": len(programs),
            "grant_events": dict(Counter(row["event"] for row in grants)),
            "program_statuses": dict(Counter(row["status"] for row in programs)),
            "grant_refresh_declaration_snapshots": states,
            "native_service_records": count,
            "verified_reused_copy_bytes_after_grant": (
                sum(row["verified_reused_copy_bytes_after_grant"] for row in programs)
                if copies is not None else None
            ),
        },
        "groups": groups, "programs": programs,
    }


def audit(arm, *, prefetch_report=None):
    reader = SealedRecords()
    copies, report_inputs = None, {}
    if prefetch_report is not None:
        data = prefetch_report.read_bytes()
        prefetch = json.loads(gzip.decompress(data) if prefetch_report.suffix == ".gz" else data)
        if Path(prefetch["arm"]).resolve() != arm.resolve():
            raise ValueError("prefetch evidence belongs to another arm")
        required = {
            arm / name for name in (
                "opportunities/admission_opportunities.jsonl", "replay/runtime_events.replay.jsonl",
                "server/runtime_events.sglang.jsonl", "replay/native_requests.jsonl",
                "server/runtime_audit.jsonl",
            )
        }
        reader.prefixes = {
            str(Path(name).resolve()): prefix
            for name, prefix in prefetch["input_jsonl_prefixes"].items()
            if Path(name).resolve() in required
        }
        copies = prefetch["rows"]
        report_inputs[str(prefetch_report.resolve())] = sha256_file(prefetch_report)
    opportunities = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    result = inspect(
        opportunities, reader(arm / "replay/runtime_events.replay.jsonl"),
        reader(arm / "server/runtime_events.sglang.jsonl"),
        reader(arm / "replay/native_requests.jsonl"), reader(arm / "server/runtime_audit.jsonl"),
        copies=copies,
    )
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arm": str(arm.resolve()),
        **result, "input_jsonl_prefixes": reader.prefixes,
        "report_inputs_sha256": report_inputs, "auditor_sha256": sha256_file(Path(__file__)),
        "complete_run_verified": False, "performance_accepted": False,
        "semantics": [
            "Grant logs describe actual logical eligibility changes, not physical allocation.",
            "Service associations require workflow/invocation/context/epoch and completed native session/clock identity.",
            "Blocked JOIN grants target either a prior EOS context or the exact already-submitted successor request.",
            "Service samples do not expose attempt identity; no per-attempt proof is claimed.",
            "Service start is the recorded native run_batch entry, not a CUDA kernel timestamp.",
            "Only server wall timestamps are subtracted; client JOIN events select identities only.",
            "Later grants bound earlier associations, preventing duplicate credit for the same future service or copy.",
            "Service-start spread describes newly granted members only, not all siblings or JOIN completion.",
            "Host-hit spans are not individual DMA bytes; only separate verified copy evidence is credited.",
            "Concurrent prefixes are independently sealed, not an atomic snapshot or a causal speedup experiment.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--prefetch-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous JOIN grant audits")
    result = audit(args.arm.resolve(), prefetch_report=args.prefetch_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
