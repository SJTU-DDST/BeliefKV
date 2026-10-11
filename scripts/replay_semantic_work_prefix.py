#!/usr/bin/env python3
"""Compare work heads on the inputs of sealed, delivered online forecasts."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_report_phase import ReportObservation
from beliefkv.predictor.child_semantic_work import SemanticReportPredictor
from scripts.audit_semantic_h2d_online import error_summary


def prefix_records(path: Path, seals: dict):
    seal = seals.get(str(path))
    limit = seal["read_complete_prefix_bytes"] if seal else path.stat().st_size
    digest = hashlib.sha256()
    consumed = 0
    with path.open("rb") as stream:
        while consumed < limit:
            line = stream.readline(limit - consumed)
            consumed += len(line)
            if not line:
                raise ValueError(f"short source prefix: {path}")
            if not line.endswith(b"\n"):
                if seal:
                    raise ValueError(f"incomplete sealed row: {path}")
                consumed -= len(line)
                break
            digest.update(line)
            if line.strip():
                yield json.loads(line)
    sha = digest.hexdigest()
    if seal and sha != seal["sha256"]:
        raise ValueError(f"sealed source changed: {path}")
    seals[str(path)] = {"read_complete_prefix_bytes": consumed, "sha256": sha}


def work_errors(rows, name):
    groups = defaultdict(list)
    for row in rows:
        if (
            row["natural_return"] and row["lead_to_native_eos_ms"] is not None
            and row["lead_to_native_eos_ms"] > 0 and row["actual_remaining_tokens"] >= 0
        ):
            groups[row["request_id"]].append(row)
    last = [max(group, key=lambda row: row["delivery_ts_ms"]) for group in groups.values()]
    result = {}
    for limit in (None, 16, 32, 64, 128):
        chosen = [row for row in last if limit is None or row["actual_remaining_tokens"] <= limit]
        result["all" if limit is None else str(limit)] = error_summary([
            (
                row[f"{name}_bounds"][1] - row["actual_remaining_tokens"],
                row["actual_remaining_tokens"], row[f"{name}_bounds"][1],
            ) for row in chosen
        ])
    return result


def first_triggers(rows, name, threshold, horizon):
    groups = defaultdict(list)
    for row in rows:
        groups[row["request_id"]].append(row)
    selected = []
    for group in groups.values():
        for row in sorted(group, key=lambda value: value["delivery_ts_ms"]):
            rate, generated = row["tokens_per_second"], row["current_output_tokens"]
            if (
                row["phase_score"] < threshold or not row["notice_active"]
                or type(generated) is not int or generated < 16 or not rate
                or not 0 <= row["observation_age_ms"] <= 1500
                or row["last_service_age_ms"] is None or row["last_service_age_ms"] > 250
            ):
                continue
            if row["lead_to_native_eos_ms"] is not None and row["lead_to_native_eos_ms"] <= 0:
                continue
            advanced = max(0, generated - row["observed_output_tokens"])
            _, center, upper = row[f"{name}_bounds"]
            work = center if center > advanced else upper
            if work <= advanced:
                continue
            remaining = max(1., work - advanced)
            if remaining * 1000. / rate > horizon:
                continue
            selected.append({
                "request_id": row["request_id"], "delivery_ts_ms": row["delivery_ts_ms"],
                "natural_return": row["natural_return"], "known_nonterminal": row["known_nonterminal"],
                "lead_to_native_eos_ms": row["lead_to_native_eos_ms"],
                "predicted_remaining_tokens": remaining,
                "actual_remaining_tokens": row["actual_remaining_at_delivery_tokens"],
                "effective_statistic": "center" if center > advanced else "upper_after_center_overrun",
            })
            break
    natural = [row for row in selected if row["natural_return"]]
    known = [row for row in natural if row["lead_to_native_eos_ms"] is not None]
    return {
        "first_trigger_count": len(selected), "natural_return_trigger_count": len(natural),
        "known_nonterminal_trigger_count": sum(row["known_nonterminal"] for row in selected),
        "unresolved_trigger_count": sum(
            not row["natural_return"] and not row["known_nonterminal"] for row in selected
        ),
        "lead_0_to_500ms_count": sum(0 < row["lead_to_native_eos_ms"] <= 500 for row in known),
        "lead_500_to_2000ms_count": sum(500 < row["lead_to_native_eos_ms"] <= 2000 for row in known),
        "lead_over_2000ms_count": sum(row["lead_to_native_eos_ms"] > 2000 for row in known),
        "rows": selected,
    }


def replay(audit_path, baseline_path, candidate_path):
    audit = json.loads(audit_path.read_text())
    seals = {
        path: seal for path, seal in audit["source_prefix_seals"].items()
        if path.endswith((
            "/admission_opportunities.jsonl", "/runtime_events.sglang.jsonl",
            "/runtime_events.deepagents.jsonl",
        ))
    }
    opportunities = next(Path(path) for path in seals if path.endswith("/admission_opportunities.jsonl"))
    arm = opportunities.parent.parent
    forecasts, clock_offset = [], None
    for row in prefix_records(opportunities, seals):
        if row["event"] == "semantic_child_forecast":
            forecasts.append(row)
        elif row["event"] == "safe_point_census" and clock_offset is None:
            clock_offset = row["ts_ms"] - row["monotonic_ms"]
    if clock_offset is None:
        raise ValueError("missing monotonic clock alignment")
    native = {
        row["attributes"]["request_id"]: row
        for row in prefix_records(arm / "server/runtime_events.sglang.jsonl", seals)
        if row["kind"] == "llm_result" and type(row["attributes"].get("output_tokens")) is int
    }
    terminals, nonterminals = set(), set()
    event_paths = sorted(
        Path(path) for path in seals if path.endswith("/runtime_events.deepagents.jsonl")
    )
    for path in event_paths:
        last, requests = {}, defaultdict(set)
        for row in prefix_records(path, seals):
            attrs, invocation = row.get("attributes") or {}, row.get("invocation_id")
            rid = attrs.get("request_id")
            if row["kind"] == "llm_result" and not attrs.get("runtime_internal") and rid:
                if invocation in last:
                    nonterminals.add(last[invocation])
                last[invocation] = rid
                requests[invocation].add(rid)
                if attrs.get("tool_call_count", 0):
                    nonterminals.add(rid)
            elif row["kind"] == "return" and attrs.get("source") == "deepagents_task":
                if attrs.get("outcome") == "completed" and invocation in last:
                    terminals.add(last[invocation])
                nonterminals.update(requests[invocation] - terminals)
            elif row["kind"] == "invocation_cancel":
                nonterminals.update(requests[invocation] - terminals)
    lookup = {}
    forecast_ids = {row["request_id"] for row in forecasts}
    for events_path in event_paths:
        path = events_path.parent / "child_stream_content.jsonl"
        if not path.exists():
            continue
        for row in prefix_records(path, seals):
            if (
                row.get("event") != "child_stream_content" or row.get("tool_chunk")
                or row.get("finish_reason") or row.get("request_id") not in forecast_ids
                or not row.get("semantic_content_tail")
            ):
                continue
            lookup.setdefault((row["request_id"], row["content_chars"]), row)
    samples, missing, late_content = [], 0, 0
    for forecast in forecasts:
        text = lookup.get((forecast["request_id"], forecast["content_chars"]))
        if text is None:
            missing += 1
            continue
        if text["ts_ms"] > forecast["ts_ms"] - clock_offset:
            late_content += 1
            continue
        if (
            text["context_id"] != forecast["context_id"]
            or text["context_epoch"] != forecast["context_epoch"]
        ):
            raise ValueError("forecast text identity differs")
        observation = ReportObservation(
            forecast["request_id"], text["invocation_id"], text["context_id"],
            text["context_epoch"], text["ts_ms"], text["semantic_content_tail"][-1024:],
            forecast["content_chars"], forecast["observed_output_tokens"],
            notice_active=forecast["notice_active"],
            estimated_report_tokens=forecast["estimated_report_tokens"],
            prior_tool_calls=forecast["prior_tool_calls"],
            prior_model_rounds=forecast["prior_model_rounds"],
        )
        samples.append((observation, forecast))
    baseline, candidate = (
        SemanticReportPredictor.load(path) for path in (baseline_path, candidate_path)
    )
    predictions = {"baseline": [], "candidate": []}
    inference = {"baseline": [], "candidate": []}
    # Alternate model order and use the online worker's maximum batch size.
    for start in range(0, len(samples), 4):
        observed = [row[0] for row in samples[start:start + 4]]
        ordered = (
            (("baseline", baseline), ("candidate", candidate))
            if start % 8 == 0 else (("candidate", candidate), ("baseline", baseline))
        )
        for name, model in ordered:
            before = time.perf_counter()
            predictions[name].extend(model.predict(observed))
            inference[name].append((time.perf_counter() - before) * 1000.)
    rows, phase_difference, score_reproduction, work_reproduction = [], [], [], []
    for (observation, forecast), before, after in zip(
        samples, predictions["baseline"], predictions["candidate"],
    ):
        phase_difference.append(abs(before.final_report_score - after.final_report_score))
        score_reproduction.append(abs(before.final_report_score - forecast["score"]))
        work_reproduction.append(abs(before.conditional_remaining_tokens[1] - forecast["remaining_tokens"]))
        result = native.get(observation.request_id)
        total = result["attributes"]["output_tokens"] if result is not None else None
        rows.append({
            "request_id": observation.request_id, "delivery_ts_ms": forecast["ts_ms"],
            "observed_output_tokens": observation.observed_output_tokens,
            "current_output_tokens": forecast.get("current_output_tokens"),
            "notice_active": observation.notice_active, "phase_score": before.final_report_score,
            "observation_age_ms": forecast["observation_age_ms"],
            "last_service_age_ms": forecast.get("last_service_age_ms"),
            "tokens_per_second": forecast.get("sampled_tokens_per_second"),
            "natural_return": observation.request_id in terminals,
            "known_nonterminal": observation.request_id in nonterminals - terminals,
            "baseline_bounds": list(before.conditional_remaining_tokens),
            "candidate_bounds": list(after.conditional_remaining_tokens),
            "actual_remaining_tokens": total - observation.observed_output_tokens if total is not None else None,
            "actual_remaining_at_delivery_tokens": (
                total - forecast["current_output_tokens"] if total is not None
                and type(forecast.get("current_output_tokens")) is int else None
            ),
            "lead_to_native_eos_ms": result["ts_ms"] - forecast["ts_ms"] if result is not None else None,
        })
    return {
        "scope": (
            "Development replay on delivered V26 inputs, not independent validation or H2D benefit. "
            "EOS/RETURN are labels only. First triggers evaluated at original forecast deliveries "
            "with original service rates and progress; no allocator or Host/capacity replay. "
            "New model latency is measured separately, not simulated into trigger delivery."
        ),
        "audit_path": str(audit_path), "audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "baseline_path": str(baseline_path), "candidate_path": str(candidate_path),
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "forecast_snapshot_count": len(forecasts), "reconstructed_snapshot_count": len(samples),
        "missing_content_count": missing, "content_emitted_after_delivery_count": late_content,
        "max_phase_score_change": max(phase_difference, default=0.),
        "baseline_reproduction_max_phase_score_difference": max(score_reproduction, default=0.),
        "baseline_reproduction_max_work_token_difference": max(work_reproduction, default=0.),
        "last_pre_eos_errors": {name: work_errors(rows, name) for name in predictions},
        "first_delivered_trigger_replay": {
            str(horizon): {
                name: first_triggers(rows, name, audit["threshold"], horizon)
                for name in predictions
            } for horizon in (100., 250., 500.)
        },
        "cpu_inference_batches": {
            name: {
                "batch_count": len(values), "total_ms": sum(values),
                "p50_ms": float(np.median(values)) if values else None,
                "p90_ms": float(np.quantile(values, .9)) if values else None,
            } for name, values in inference.items()
        },
        "source_prefix_seals": seals, "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    report = replay(args.audit.resolve(), args.baseline.resolve(), args.candidate.resolve())
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in (
        "forecast_snapshot_count", "reconstructed_snapshot_count",
        "baseline_reproduction_max_phase_score_difference",
        "baseline_reproduction_max_work_token_difference",
        "max_phase_score_change", "last_pre_eos_errors", "cpu_inference_batches",
    )}, indent=2))
    print(json.dumps({
        horizon: {name: {key: value for key, value in report.items() if key != "rows"}
                  for name, report in models.items()}
        for horizon, models in report["first_delivered_trigger_replay"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
