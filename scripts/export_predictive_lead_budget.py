#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Mapping


def quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = min(
        len(ordered) - 1,
        max(0, int(math.ceil(probability * len(ordered))) - 1),
    )
    return ordered[rank]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def load_events(path: Path) -> dict[str, list[Mapping[str, object]]]:
    events: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            intent_id = str(
                item.get("predictive_intent_id") or item.get("intent_id") or ""
            )
            if intent_id:
                events[intent_id].append(item)
    return events


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export action-aligned predictive deadline lead budgets."
    )
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-run-id")
    args = parser.parse_args()

    events = load_events(args.audit)
    prepare_dispatch: list[float] = []
    prefetch_dispatch: list[float] = []
    service_readiness: list[float] = []
    with args.telemetry.open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            intent_id = str(item.get("predictive_intent_id") or "")
            if not intent_id or item.get("status") != "completed":
                continue
            published = next(
                (
                    event
                    for event in reversed(events[intent_id])
                    if event.get("event")
                    in {
                        "predictive_semantic_intent_published",
                        "predictive_wait_shadow_intent_published",
                    }
                ),
                None,
            )
            if published is None:
                continue
            submit_ts = float(item["submit_ts_ms"])
            published_ts = float(published["ts_ms"])
            if item.get("direction") == "d2h":
                source_ts = published.get("source_observation_ts_ms")
                if source_ts is not None:
                    prepare_dispatch.append(submit_ts - float(source_ts))
            elif item.get("direction") == "h2d":
                prefetch_dispatch.append(submit_ts - published_ts)
                useful = next(
                    (
                        event
                        for event in events[intent_id]
                        if event.get("event") == "predictive_action_outcome"
                        and event.get("state") == "useful"
                    ),
                    None,
                )
                if useful is not None:
                    service_readiness.append(
                        float(useful["ts_ms"]) - float(item["complete_ts_ms"])
                    )

    def action(
        values: list[float],
        *,
        fallback_ms: float,
        minimum_ms: float,
        maximum_ms: float,
    ) -> dict[str, object]:
        return {
            "fallback_ms": fallback_ms,
            "offline_ms": clamp(
                quantile(values, 0.95), minimum_ms, maximum_ms
            ),
            "minimum_ms": minimum_ms,
            "maximum_ms": maximum_ms,
            "quantile": 0.95,
            "minimum_samples": 8,
            "offline_sample_count": len(values),
            "offline_quantiles_ms": {
                "p50": quantile(values, 0.50),
                "p90": quantile(values, 0.90),
                "p95": quantile(values, 0.95),
                "p99": quantile(values, 0.99),
                "max": max(values) if values else 0.0,
            },
        }

    payload = {
        "schema_version": 1,
        "provenance": {
            "source_run_id": args.source_run_id,
            "source_audit": str(args.audit),
            "source_telemetry": str(args.telemetry),
            "selection_policy": "p95_clamped_pre_queue_fix",
            "note": (
                "Offline priors come from v58 before predictive DMA queue "
                "repair; online samples override after minimum support."
            ),
        },
        "actions": {
            "prepare_dispatch": action(
                prepare_dispatch,
                fallback_ms=250.0,
                minimum_ms=25.0,
                maximum_ms=1000.0,
            ),
            "prefetch_dispatch": action(
                prefetch_dispatch,
                fallback_ms=100.0,
                minimum_ms=50.0,
                maximum_ms=1000.0,
            ),
            "prefetch_service_readiness": action(
                service_readiness,
                fallback_ms=100.0,
                minimum_ms=50.0,
                maximum_ms=1000.0,
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
