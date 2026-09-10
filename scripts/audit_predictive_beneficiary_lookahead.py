#!/usr/bin/env python3
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable


HBM_REJECTION_RESULTS = {
    "NO_TOKEN",
    "NO_CAPACITY",
    "ALLOCATOR_CAPACITY",
    "OUT_OF_MEMORY",
}


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
            if isinstance(payload, dict):
                yield payload


def _events_between(
    events: list[tuple[float, Any]],
    start_ms: float,
    end_ms: float,
) -> list[tuple[float, Any]]:
    index = bisect_left(events, (start_ms,))
    result: list[tuple[float, Any]] = []
    while index < len(events) and events[index][0] <= end_ms:
        result.append(events[index])
        index += 1
    return result


def audit(audit_path: Path, *, horizon_ms: float) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    request_starts: dict[str, list[tuple[float, str]]] = defaultdict(list)
    admission_outcomes: dict[str, list[tuple[float, str]]] = defaultdict(list)
    resources: list[tuple[float, dict[str, Any]]] = []
    max_running_requests = 0

    for event in _read_jsonl(audit_path):
        kind = str(event.get("event") or "")
        ts_ms = float(event.get("ts_ms") or 0.0)
        if kind == "predictive_beneficiary_hint_published":
            classification = event.get("beneficiary_opportunity_classification")
            request_id = str(event.get("request_id") or "")
            if request_id and classification:
                probes.append(event)
        elif kind == "request_started":
            request_id = str(event.get("request_id") or "")
            if request_id:
                request_starts[request_id].append((ts_ms, "request_started"))
        elif kind == "admission_ticket_epoch_finished":
            for field in ("selected", "native_rejected"):
                for item in event.get(field, ()) or ():
                    if not isinstance(item, dict):
                        continue
                    request_id = str(item.get("request_id") or "")
                    outcome = str(
                        item.get("native_result")
                        or item.get("result")
                        or item.get("reason")
                        or "unknown"
                    )
                    if request_id:
                        admission_outcomes[request_id].append((ts_ms, outcome))
        elif kind == "resource_snapshot":
            resources.append((ts_ms, event))
            max_running_requests = max(
                max_running_requests,
                int(event.get("running_request_count") or 0),
            )

    for values in (*request_starts.values(), *admission_outcomes.values()):
        values.sort()
    resources.sort(key=lambda item: item[0])

    rows: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    strict_false_negative = 0
    proxy_false_negative = 0
    for probe in probes:
        request_id = str(probe["request_id"])
        start_ms = float(probe["ts_ms"])
        deadline_ms = start_ms + horizon_ms
        starts = _events_between(
            request_starts.get(request_id, []), start_ms, deadline_ms
        )
        admissions = _events_between(
            admission_outcomes.get(request_id, []), start_ms, deadline_ms
        )
        snapshots = _events_between(resources, start_ms, deadline_ms)
        signature = tuple(probe.get("hint_signature") or ())
        immediate_required_bytes = (
            int(signature[3]) + int(signature[4]) if len(signature) >= 5 else 0
        )
        hbm_rejections = [
            (ts, outcome)
            for ts, outcome in admissions
            if outcome.upper() in HBM_REJECTION_RESULTS
        ]
        min_free_bytes = min(
            (
                max(
                    0,
                    int(snapshot.get("hbm_capacity_bytes") or 0)
                    - int(snapshot.get("hbm_used_bytes") or 0),
                )
                for _, snapshot in snapshots
            ),
            default=None,
        )
        capacity_crossed = bool(
            min_free_bytes is not None
            and immediate_required_bytes > min_free_bytes
        )
        slot_saturated = any(
            int(snapshot.get("running_request_count") or 0)
            >= max_running_requests
            for _, snapshot in snapshots
        ) if max_running_requests else False
        if starts:
            outcome = "service_started"
        elif hbm_rejections:
            outcome = "native_hbm_rejected"
        elif slot_saturated:
            outcome = "slot_saturated_no_service"
        elif admissions:
            outcome = "admission_non_hbm_outcome"
        else:
            outcome = "unresolved_no_direct_evidence"
        outcome_counts[outcome] += 1

        predicted_hbm_risk = bool(
            probe.get("beneficiary_hbm_blocked")
            or probe.get("beneficiary_slot_then_hbm_blocked")
            or int(probe.get("beneficiary_predicted_deficit_bytes") or 0) > 0
        )
        strict_fn = not predicted_hbm_risk and bool(hbm_rejections)
        proxy_fn = (
            not predicted_hbm_risk
            and not starts
            and capacity_crossed
        )
        strict_false_negative += int(strict_fn)
        proxy_false_negative += int(proxy_fn)
        rows.append(
            {
                "request_id": request_id,
                "probe_ts_ms": start_ms,
                "deadline_ts_ms": deadline_ms,
                "predicted_classification": probe.get(
                    "beneficiary_opportunity_classification"
                ),
                "observed_outcome": outcome,
                "service_started_ts_ms": starts[0][0] if starts else None,
                "native_admission_outcomes": [item[1] for item in admissions],
                "resource_snapshot_count": len(snapshots),
                "slot_saturated_observed": slot_saturated,
                "immediate_required_bytes": immediate_required_bytes,
                "minimum_observed_free_hbm_bytes": min_free_bytes,
                "physical_capacity_crossed": capacity_crossed,
                "strict_false_negative": strict_fn,
                "capacity_proxy_false_negative": proxy_fn,
            }
        )

    return {
        "schema_version": 1,
        "source_audit": str(audit_path.resolve()),
        "horizon_ms": horizon_ms,
        "probe_count": len(rows),
        "unique_request_count": len({item["request_id"] for item in rows}),
        "predicted_classification_counts": dict(
            sorted(
                Counter(
                    str(item["predicted_classification"])
                    for item in rows
                ).items()
            )
        ),
        "observed_outcome_counts": dict(sorted(outcome_counts.items())),
        "strict_hbm_false_negative_count": strict_false_negative,
        "capacity_proxy_false_negative_count": proxy_false_negative,
        "strict_hbm_false_negative_rate": (
            strict_false_negative / len(rows) if rows else 0.0
        ),
        "capacity_proxy_false_negative_rate": (
            proxy_false_negative / len(rows) if rows else 0.0
        ),
        "limitations": [
            "Native HBM false negatives require an explicit NO_TOKEN/capacity rejection.",
            "Global free-HBM crossing is a weak proxy and is reported separately.",
            "The historical trace does not retain all non-selected deferred candidates, so their outcomes are unavailable.",
            "request_started is admission/service evidence, not a per-token CUDA boundary.",
        ],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", type=Path)
    parser.add_argument("--horizon-ms", type=float, default=2000.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.horizon_ms <= 0:
        parser.error("--horizon-ms must be positive")
    result = audit(args.audit, horizon_ms=args.horizon_ms)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
