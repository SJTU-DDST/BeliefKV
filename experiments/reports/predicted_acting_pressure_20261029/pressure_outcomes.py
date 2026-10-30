"""Audit pressure predictions against tool returns, outside the serving path."""

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from experiments.reports.predicted_acting_pressure_20261029.complete_review import pressure_decisions
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def identity(row):
    return tuple(row.get(name) for name in (
        "workflow_id", "invocation_id", "context_id", "context_epoch",
    ))


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid same-host monotonic timestamp")
    return value


def evaluate(decisions, events, receipts):
    decisions = [row for row in decisions if row.get("event") == "agent_predicted_acting_pressure"]
    logical = pressure_decisions(decisions)
    results, submits, native, tools = {}, defaultdict(list), {}, {}
    domains = set()
    for row in receipts:
        request = row["request_id"]
        if request in native or row.get("exact_tokens_verified") is not True:
            raise ValueError("duplicate or inexact native receipt")
        native[request] = row
        domains.add(row["native_receipt"]["clock_domain"])
    if len(domains) != 1:
        raise ValueError("native receipts do not share one clock domain")
    for row in events:
        kind = row.get("kind")
        if kind not in ("llm_result", "llm_submit", "tool_start", "tool_end"):
            continue
        timestamp(row["ts_ms"])
        attrs = row.get("attributes") or {}
        if kind == "llm_result":
            request = attrs["request_id"]
            if request in results:
                raise ValueError("duplicate model-result boundary")
            results[request] = row
        elif kind == "llm_submit":
            submits[identity(row)[:3]].append(row)
        else:
            tool_id = attrs.get("tool_run_id") or attrs.get("tool_call_id")
            if not isinstance(tool_id, str) or not tool_id:
                raise ValueError("tool lacks its observed execution identity")
            key = row["workflow_id"], row["invocation_id"], tool_id
            pair = tools.setdefault(key, {})
            if kind in pair:
                raise ValueError("duplicate tool execution boundary")
            pair[kind] = row
    for rows in submits.values():
        rows.sort(key=lambda row: row["ts_ms"])
        requests = [row["attributes"]["request_id"] for row in rows]
        if len(requests) != len(set(requests)):
            raise ValueError("duplicate request submission")
    tool_groups = defaultdict(list)
    for key, pair in tools.items():
        if "tool_start" not in pair:
            raise ValueError("tool completion lacks its start")
        if "tool_end" in pair and pair["tool_end"]["ts_ms"] < pair["tool_start"]["ts_ms"]:
            raise ValueError("tool returns before its start")
        tool_groups[key[:2]].append((key, pair))
    counts, rows = Counter(), []
    for decision in decisions:
        evidence = decision.get("evidence")
        if evidence is None:
            continue
        counts["predicted_decisions"] += 1
        key = evidence["key"]
        pause = timestamp(decision["monotonic_s"]) * 1000.
        request = key["request_id"]
        expected = tuple(key[name] for name in (
            "root_workflow_id", "invocation_id", "context_id", "context_epoch",
        ))
        result, receipt = results.get(request), native.get(request)
        observation = {
            "request_id": request, "identity": list(expected),
            "pause_monotonic_ms": pause, "threshold_ms": evidence["threshold_ms"],
            "changed_local_victim": decision["selected"] != decision["reference_selected"],
        }
        rows.append(observation)
        if result is None or receipt is None:
            observation["binding"] = "result_or_receipt_outside_observed_prefix"
            continue
        proof = receipt["native_receipt"]
        if (
            identity(result) != expected or tuple(proof["identity"]) != expected
            or proof.get("session_id") != key["session_id"]
            or proof.get("session_generation") != key["session_generation"]
            or timestamp(proof["complete_monotonic_ms"]) > result["ts_ms"]
        ):
            raise ValueError("pressure request identity, session, or native clock disagrees")
        if pause < result["ts_ms"]:
            observation["binding"] = "pause_precedes_client_result"
            continue
        following = next((
            item for item in submits[expected[:3]]
            if item["ts_ms"] > result["ts_ms"]
            and type(item["context_epoch"]) is int and item["context_epoch"] > expected[3]
        ), None)
        if following is None:
            observation["binding"] = "following_request_outside_observed_prefix"
            continue
        round_tools = [
            (tool_key, pair) for tool_key, pair in tool_groups[expected[:2]]
            if result["ts_ms"] <= pair["tool_start"]["ts_ms"] < following["ts_ms"]
        ]
        if len(round_tools) != 1 or "tool_end" not in round_tools[0][1]:
            observation["binding"] = "single_complete_tool_round_unproven"
            continue
        tool_key, pair = round_tools[0]
        start, end = pair["tool_start"]["ts_ms"], pair["tool_end"]["ts_ms"]
        if end > following["ts_ms"]:
            raise ValueError("following model request precedes the tool return")
        remaining = end - pause
        observation.update({
            "binding": "exact_request_session_single_tool_and_following_request",
            "tool_identity": list(tool_key),
            "tool_start_monotonic_ms": start, "tool_end_monotonic_ms": end,
            "observed_remaining_ms": remaining,
            "observed_survived_threshold": remaining >= evidence["threshold_ms"],
            "pause_within_observed_tool": start <= pause < end,
            "next_request_id": following["attributes"]["request_id"],
            "next_submit_after_tool_return_ms": following["ts_ms"] - end,
            "next_submit_after_pause_ms": following["ts_ms"] - pause,
        })
        next_receipt = native.get(observation["next_request_id"])
        if next_receipt is not None:
            if tuple(next_receipt["native_receipt"]["identity"]) != identity(following):
                raise ValueError("following native receipt belongs to a different request")
            first_output = (next_receipt.get("response_metrics") or {}).get(
                "first_output_monotonic_ms",
            )
            if first_output is not None:
                if timestamp(first_output) < following["ts_ms"]:
                    raise ValueError("first output precedes its actual request submission")
                observation["tool_return_to_next_first_output_ms"] = first_output - end
        counts["fully_bound_decisions"] += 1
        counts["observed_survived_threshold_decisions"] += observation["observed_survived_threshold"]
        counts["pause_outside_observed_tool"] += not observation["pause_within_observed_tool"]
    counts["unbound_decisions"] = counts["predicted_decisions"] - counts["fully_bound_decisions"]
    bound = [row for row in rows if "tool_identity" in row]
    first_by_tool = {}
    for row in sorted(bound, key=lambda item: item["pause_monotonic_ms"]):
        first_by_tool.setdefault(tuple(row["tool_identity"]), row)
    unique = list(first_by_tool.values())
    counts["independent_tool_executions"] = len(unique)
    counts["independent_first_decisions_survived_threshold"] = sum(
        row["observed_survived_threshold"] for row in unique
    )
    return {
        "logical_decisions": logical, "counts": dict(counts),
        "tool_weighted_first_decision_median_remaining_ms": (
            statistics.median(row["observed_remaining_ms"] for row in unique) if unique else None
        ),
        "rows": rows, "clock_domain": next(iter(domains)),
        "scope": (
            "Retrospective same-host tool-return evidence, bound to exact native request, "
            "context epoch and session. Repeated pauses of one tool are not independent "
            "samples. Actual future times are evaluation labels only. Local victim "
            "changes and first-output delays do not establish physical page release, "
            "a reference counterfactual, avoidable migration or causal JCT benefit."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collecting", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("offline analysis must hide CUDA")
    if args.output.exists():
        raise FileExistsError("preserve previous outcome audits")
    plan = causal.load(args.registration)
    if plan["order"] != ["full"]:
        raise ValueError("expected the separately declared Full")
    if not args.collecting and complete_work(args.registration) is None:
        raise ValueError("complete outcome audit requires full work, drain, and owned exit")
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    receipts = reader(arm / "replay/native_requests.jsonl")
    events = reader(arm / "replay/runtime_events.replay.jsonl")
    decisions = reader(arm / "opportunities/admission_opportunities.jsonl")
    report = evaluate(decisions, events, receipts)
    report.update({
        "registration": str(args.registration),
        "registration_sha256": sha256_file(args.registration),
        "operator_sha256": sha256_file(Path(__file__)),
        "input_jsonl_prefixes": reader.prefixes,
        "complete_work_verified": not args.collecting,
        "performance_accepted": False, "GPU_action": False,
    })
    causal.reservation.write_json(args.output, report)
    print(json.dumps(report["counts"], indent=2))


if __name__ == "__main__":
    main()
