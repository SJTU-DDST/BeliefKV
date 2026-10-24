"""Read-only tool-loop evidence using physical calls and unmodified result hashes."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.audit_agent_work_redundancy import dependency_topology, tool_pairs
from scripts.audit_sealed_wait_prefetch import SealedRecords

READ_TOOLS = frozenset(("grep", "read_file", "ls", "glob"))


def digest(value):
    return (
        type(value) is str and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def link_raw_results(calls, feedback):
    by_call = defaultdict(list)
    for call in calls:
        by_call[call["call_id"]].append(call)
    linked, unknown = {}, Counter()
    for row in feedback:
        if row.get("event") != "agent_tool_repeated_result_feedback":
            continue
        matches = by_call.get(row.get("tool_call_id"), ())
        if len(matches) != 1:
            unknown["missing_or_ambiguous_completed_call"] += 1
            continue
        call = matches[0]
        if (
            row.get("physical_execution") is not True
            or row.get("suppression_enabled") is not False
            or row.get("tool_name") != call["tool"]
            or not digest(row.get("source_result_sha256"))
            or not digest(row.get("signature"))
            or type(row.get("identical_result_count")) is not int
            or row["identical_result_count"] < 2
        ):
            unknown["invalid_raw_result_evidence"] += 1
            continue
        key = call["workflow"], call["invocation"], call["call_id"]
        proof = {
            "raw_result_sha256": row["source_result_sha256"],
            "feedback_signature": row["signature"],
            "reported_identical_result_count": row["identical_result_count"],
        }
        if key in linked:
            raise ValueError("duplicate or conflicting raw-result evidence for one call")
        linked[key] = proof
    return linked, dict(unknown)


def serial_runs(calls, raw_results=None):
    grouped = defaultdict(list)
    for call in calls:
        grouped[(call["workflow"], call["invocation"])].append(call)
    found = []
    for identity, rows in grouped.items():
        previous, current = None, []
        for call in sorted(rows, key=lambda row: (row["start_ms"], row["call_id"])):
            signature = None
            if call["tool"] in READ_TOOLS and call["input_sha256"]:
                signature = call["tool"], call["input_sha256"]
                if raw_results is not None:
                    proof = raw_results.get((*identity, call["call_id"]))
                    signature = (
                        (*signature, proof["raw_result_sha256"], call["status"])
                        if proof is not None else None
                    )
            follows = (
                signature is not None and previous is not None
                and signature == previous[0] and previous[1] <= call["start_ms"]
            )
            if follows:
                current.append(call)
            else:
                if current:
                    found.append(current)
                current = [call] if signature is not None else []
            previous = (signature, call["end_ms"]) if signature is not None else None
        if current:
            found.append(current)
    return found


def analyze(arm):
    sealed = SealedRecords()
    events, calls, links = [], [], {}
    unknown, counts = Counter(), Counter()
    files = sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"))
    if not files:
        raise ValueError("source has no workflow runtime events")
    pending = 0
    for path in files:
        local = list(sealed(path))
        paired, unfinished = tool_pairs(local)
        proofs, missing = link_raw_results(
            paired, sealed(path.parent / "sandbox_audit.jsonl"),
        )
        events.extend(local)
        calls.extend(paired)
        for key, value in proofs.items():
            if key in links:
                raise ValueError("raw-result call identity crosses workflow files")
            links[key] = value
        unknown.update(missing)
        pending += unfinished
        counts.update(row["kind"] for row in local)
    input_runs, raw_runs = serial_runs(calls), serial_runs(calls, links)
    longest = max(raw_runs, key=len, default=[])
    return {
        "arm": str(arm.resolve()), "observed_at": datetime.now(timezone.utc).isoformat(),
        "workflow_files": len(files), "event_counts": dict(counts),
        "dependency_topology": dependency_topology(events),
        "completed_tool_pairs": len(calls), "unfinished_tool_starts": pending,
        "max_serial_identical_read_input_calls": max(map(len, input_runs), default=0),
        "max_serial_identical_raw_read_results_proven": len(longest),
        "max_reported_identical_result_count": max(
            (row["reported_identical_result_count"] for row in links.values()), default=0,
        ),
        "raw_result_matched_completed_calls": len(links),
        "raw_result_unknown_counts": dict(unknown),
        "longest_raw_result_run": [{
            **{name: call[name] for name in (
                "workflow", "invocation", "call_id", "tool", "input_sha256",
                "status", "start_ms", "end_ms",
            )},
            **links[(call["workflow"], call["invocation"], call["call_id"])],
        } for call in longest],
        "input_jsonl_prefixes": sealed.prefixes,
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "performance_accepted": False,
        "scope": [
            "Offline analysis only; no tool suppression, agent completion, launch rejection or trace change.",
            "Callback output hashes include ToolMessage IDs and changing feedback and are not raw-result hashes.",
            "Raw result hashes are linked from physical, unsuppressed feedback records by unambiguous completed tool-call identity within the workflow.",
            "The first occurrence normally lacks a raw-result feedback record; missing raw coverage stays unknown and is not proof of different results.",
            "Consecutive runs require the same invocation and input, unchanged raw hash/status when supplied, and completion before the next call starts.",
            "Feedback counts can include intervening calls and are reported separately from consecutive runs. Neither proves complete external-state equivalence.",
            "Each live JSONL prefix is independently sealed. No complete-source, task-quality or performance qualification is inferred.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous source-health evidence")
    result = analyze(args.arm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({name: result[name] for name in (
        "workflow_files", "event_counts", "completed_tool_pairs",
        "max_serial_identical_read_input_calls", "max_serial_identical_raw_read_results_proven",
        "max_reported_identical_result_count", "raw_result_matched_completed_calls",
        "raw_result_unknown_counts",
    )}, indent=2))


if __name__ == "__main__":
    main()
