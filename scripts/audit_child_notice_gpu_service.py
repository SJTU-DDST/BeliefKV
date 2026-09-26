#!/usr/bin/env python3
"""Training-only decomposition of the final child request after completion intent."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_child_return_intent_timing import load_episodes


STAGES = (
    "submit_to_first_service_ms",
    "first_service_to_first_decode_ms",
    "first_decode_to_result_ms",
    "submit_to_result_ms",
)


def audit(
    episodes: list[dict], server_events: Path, service_audit: Path,
) -> dict:
    targets = {}
    for row in episodes:
        post_notice = row.get("post_notice") or {}
        rid = post_notice.get("final_request_id")
        if rid:
            if rid in targets:
                raise ValueError(f"duplicate final request ID: {rid}")
            targets[rid] = row
    timestamps: dict[str, dict[str, float]] = defaultdict(dict)
    with server_events.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = orjson.loads(line)
            rid = (event.get("attributes") or {}).get("request_id")
            if rid not in targets:
                continue
            kind = event.get("kind")
            if kind not in ("llm_submit", "llm_result"):
                continue
            if kind in timestamps[rid]:
                raise ValueError(f"duplicate {kind} for {rid}")
            timestamps[rid][kind] = float(event["ts_ms"])

    service: dict[str, dict[str, float]] = defaultdict(dict)
    with service_audit.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = orjson.loads(line)
            if event.get("event") != "gpu_service_sample":
                continue
            ts_ms = float(event["ts_ms"])
            for sample in event.get("request_samples") or ():
                rid = sample.get("request_id")
                if rid not in targets:
                    continue
                stages = service[rid]
                stages.setdefault("first", ts_ms)
                stages["last"] = ts_ms
                if sample.get("phase") == "decode":
                    stages.setdefault("decode", ts_ms)

    matched = []
    excluded = Counter()
    post_result_service = []
    for rid, row in targets.items():
        clocks = timestamps.get(rid, {})
        stages = service.get(rid, {})
        if not all(key in clocks for key in ("llm_submit", "llm_result")):
            excluded["missing_server_boundary"] += 1
            continue
        if not all(key in stages for key in ("first", "decode", "last")):
            excluded["missing_gpu_service"] += 1
            continue
        submit, result = clocks["llm_submit"], clocks["llm_result"]
        first, decode, last = stages["first"], stages["decode"], stages["last"]
        if first < submit:
            excluded["first_service_before_submit"] += 1
            continue
        if decode < first or last < decode:
            excluded["non_monotonic_service"] += 1
            continue
        if result < decode:
            excluded["first_decode_after_result"] += 1
            continue
        if result < last:
            post_result_service.append(last - result)
        matched.append({
            "project": row["project"],
            "submit_to_first_service_ms": first - submit,
            "first_service_to_first_decode_ms": decode - first,
            "first_decode_to_result_ms": result - decode,
            "submit_to_result_ms": result - submit,
        })

    def summary(rows: list[dict]) -> dict:
        return {
            "requests": len(rows),
            "stages": {
                stage: {
                    "p50_ms": _quantile([item[stage] for item in rows], .5),
                    "p90_ms": _quantile([item[stage] for item in rows], .9),
                }
                for stage in STAGES
            },
        }

    projects = sorted({row["project"] for row in episodes})
    return {
        "status": "training_only_posthoc_gpu_service_not_prediction_eligible",
        "valid_child_intents": len(episodes),
        "identified_final_requests": len(targets),
        "excluded": dict(sorted(excluded.items())),
        "post_result_service_lag_ms": {
            "p50": _quantile(post_result_service, .5),
            "p90": _quantile(post_result_service, .9),
            "max": max(post_result_service, default=None),
            "over_100ms": sum(lag > 100 for lag in post_result_service),
            "over_1000ms": sum(lag > 1000 for lag in post_result_service),
        },
        "pooled": summary(matched),
        "by_project": {
            project: summary([
                item for item in matched if item["project"] == project
            ])
            for project in projects
        },
        "limitation": (
            "All GPU service timestamps and stage durations are posthoc "
            "labels, not features observable at completion intent. Server "
            "events and service samples share the server clock; client "
            "monotonic timestamps are not subtracted from server time. "
            "First service includes prefill, not necessarily first output. "
            "Some final service sample callbacks are timestamped after the "
            "result event; they are counted separately, not interpreted as "
            "useful GPU service after request completion."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workloads", required=True, type=Path)
    parser.add_argument("--server-events", required=True, type=Path)
    parser.add_argument("--service-audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require_complete_batch(args.workloads / "workflows")
    episodes, _ = load_episodes(args.workloads)
    report = audit(episodes, args.server_events, args.service_audit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
