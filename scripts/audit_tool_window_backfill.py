"""Screen completion-oriented tool-window backfill using complete observed events."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries
from scripts.train_workflow_work import write


def interval_union_ms(spans):
    result, last_left, last_right = 0., None, None
    for left, right in sorted(spans):
        if left < 0 or right < left:
            raise ValueError("invalid observed tool interval")
        if last_left is None:
            last_left, last_right = left, right
        elif left <= last_right:
            last_right = max(last_right, right)
        else:
            result += last_right - last_left
            last_left, last_right = left, right
    return result + (last_right - last_left if last_left is not None else 0.)


def inspect(registration, cohort_path):
    plan = json.loads(registration.read_bytes())
    measured = set(json.loads(cohort_path.read_bytes())["measured_workflow_ids"])
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    episodes, _, diagnostics = tool_reentries(events)
    rounds = {row["origin_request_id"]: row for row in episodes}
    terminal, tool_spans, returns = {}, defaultdict(list), Counter()
    for event in events:
        owner = event["workflow_id"], event.get("invocation_id")
        if event["kind"] in ("return", "invocation_cancel"):
            returns[owner] += 1
            if returns[owner] > 1:
                raise ValueError("a single invocation has multiple observed terminal events")
            terminal[owner] = event
    for row in episodes:
        if "tool_completed_ts_ms" in row:
            tool_spans[row["workflow_id"], row["invocation_id"]].append((
                row["tool_wait_start_ts_ms"], row["tool_completed_ts_ms"],
            ))
    rows, counts = [], Counter()
    for event in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if event.get("event") != "agent_predicted_tool_replacement":
            continue
        when = event["monotonic_s"] * 1000.
        donor, consumer = event["donor"], event["consumer"]
        source = rounds.get(donor["request_id"])
        counts["actual_event_replacements"] += 1
        if source is None or source.get("workflow_id") != donor["root_workflow_id"] or (
            source["invocation_id"] != donor["invocation_id"]
            or "client_ready_ts_ms" not in source
            or not source["tool_wait_start_ts_ms"] <= when < source["client_ready_ts_ms"]
        ):
            counts["donor_tool_window_not_exactly_resolved"] += 1
            continue
        owner = consumer["root_workflow_id"], consumer["invocation_id"]
        complete = terminal.get(owner)
        remaining = source["client_ready_ts_ms"] - when
        item = {
            "consumer": consumer, "donor": donor, "replacement_monotonic_ms": when,
            "observed_donor_tool_ready_monotonic_ms": source["client_ready_ts_ms"],
            "observed_remaining_tool_window_ms": remaining,
            "consumer_is_measured": owner[0] in measured,
            "consumer_terminal_kind": complete["kind"] if complete else None,
            "consumer_return_observed": bool(complete and complete["kind"] == "return"),
        }
        counts["exact_donor_tool_windows"] += 1
        if complete is not None:
            end = complete["ts_ms"]
            if end < when:
                raise ValueError("consumer was already terminal at replacement")
            item["consumer_observed_remaining_wall_ms"] = end - when
            item["consumer_observed_terminal_within_tool_window"] = end <= source["client_ready_ts_ms"]
            counts["consumer_terminal_observed"] += 1
            if complete["kind"] == "return":
                counts["consumer_return_within_tool_window"] += item[
                    "consumer_observed_terminal_within_tool_window"
                ]
        else:
            counts["consumer_terminal_censored"] += 1
        # For a completed invocation, its own subsequent tool rounds are serial
        # with its model turns; union counts concurrent tools only once.
        if complete is not None and complete["kind"] == "return":
            spans = [
                (max(left, when), min(right, complete["ts_ms"]))
                for left, right in tool_spans[owner]
                if right > when and left < complete["ts_ms"]
            ]
            future_tools = interval_union_ms(spans)
            item["observed_future_own_tool_union_ms"] = future_tools
            item["own_tools_alone_exceed_donor_window"] = future_tools > remaining
            counts["complete_return_with_own_tool_accounting"] += 1
            counts["own_tools_alone_exceed_donor_window"] += future_tools > remaining
        rows.append(item)
    for filename, prefix in reader.prefixes.items():
        if (
            Path(filename).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed tool-window evidence changed")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "counts": dict(counts), "donor_tool_round_diagnostics": diagnostics,
        "observed_remaining_tool_window_ms": distribution(
            row["observed_remaining_tool_window_ms"] for row in rows
        ),
        "consumer_observed_remaining_wall_ms": distribution(
            row["consumer_observed_remaining_wall_ms"] for row in rows
            if "consumer_observed_remaining_wall_ms" in row
        ),
        "observed_future_own_tool_union_ms": distribution(
            row["observed_future_own_tool_union_ms"] for row in rows
            if "observed_future_own_tool_union_ms" in row
        ),
        "rows": rows,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                registration, cohort_path, Path(__file__),
                ROOT / "scripts/audit_tool_reentry_service.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "Only actual event-triggered tool replacements are audited, not every physical tool eviction.",
            "Tool membership and successor boundaries reuse the existing round auditor.",
            "A resolved donor window is the current tool round, not guaranteed future agent readiness.",
            "Consumer wall time includes contention and is not intrinsic work or an alternative-policy forecast.",
            "Observed future tool union is descriptive; unresolved rounds and nested-child paths can make it incomplete.",
            "Return does not by itself prove physical KV retirement or fit of alternative consumers.",
            "Ready alternatives and their physical feasibility were not reconstructed.",
            "No workload or stopping rule is changed, and no GPU experiment is started.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve prior evidence and use nice19 CPU-only analysis")
    result = inspect(args.registration, args.cohort)
    write(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "counts", "observed_remaining_tool_window_ms",
        "consumer_observed_remaining_wall_ms", "observed_future_own_tool_union_ms",
    )}))


if __name__ == "__main__":
    main()
