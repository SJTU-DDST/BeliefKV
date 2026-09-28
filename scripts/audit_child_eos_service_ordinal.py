#!/usr/bin/env python3
"""Bound when an observed EOS cue could have occurred during GPU decode."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_eos_joint import load_rows


EOS_FLOOR = math.log(.05)


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    return round(
        ordered[low] + (ordered[math.ceil(position)] - ordered[low])
        * (position - low), 2,
    )


def _identity_matches(event: dict, row: dict) -> bool:
    return (
        event.get("invocation_id") == row["invocation_id"]
        and event.get("context_id") == row["context_id"]
        and event.get("context_epoch") == row["context_epoch"]
    )


def latest_server_ordinal(
    output_tokens: int, scored_tokens: int, scored_through_snapshot: int,
) -> int:
    """Upper bound for any threshold hit in the snapshot's scored interval."""
    if (
        type(output_tokens) is not int or type(scored_tokens) is not int
        or type(scored_through_snapshot) is not int
        or not 0 < scored_through_snapshot <= scored_tokens <= output_tokens
    ):
        raise ValueError("invalid scored/server token accounting")
    return output_tokens - scored_tokens + scored_through_snapshot


def _client_ordinals(root: Path, rows: list[dict]) -> tuple[dict, Counter]:
    by_task = defaultdict(dict)
    for row in rows:
        by_task[row["task"]][row["rid"]] = row
    candidates: dict[str, dict] = {}
    excluded = Counter()
    for task, selected in by_task.items():
        workflow = root / task
        results = {}
        invalid = set()
        with (workflow / "runtime_events.deepagents.jsonl").open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                if event.get("kind") != "llm_result":
                    continue
                attrs = event.get("attributes") or {}
                rid = attrs.get("request_id")
                if rid not in selected:
                    continue
                if rid in results or not _identity_matches(event, selected[rid]):
                    excluded["client_result_ambiguous"] += 1
                    invalid.add(rid)
                    continue
                results[rid] = attrs.get("eos_shadow_scored_tokens")

        allowed_times = {
            rid: {snap["ts_ms"] for snap in row["snapshots"]}
            for rid, row in selected.items()
        }
        observed = Counter()
        first_positive = {}
        last_ts = {}
        with (workflow / "child_stream_content.jsonl").open("rb") as stream:
            for line in stream:
                event = orjson.loads(line)
                if event.get("event") != "child_stream_content":
                    continue
                rid = event.get("request_id")
                if rid not in selected:
                    continue
                ts = event["ts_ms"]
                if ts < last_ts.get(rid, float("-inf")):
                    invalid.add(rid)
                last_ts[rid] = ts
                count = event.get("eos_shadow_scored_tokens_since_previous_snapshot")
                if type(count) is not int or count < 0:
                    invalid.add(rid)
                    continue
                observed[rid] += count
                best = event.get("eos_shadow_max_logprob_since_previous_snapshot")
                if (
                    rid not in first_positive
                    and ts in allowed_times[rid]
                    and type(best) in (float, int)
                    and math.isfinite(best)
                    and best >= EOS_FLOOR
                ):
                    first_positive[rid] = {
                        "client_cue_ms": ts,
                        # EOS could be any scored token since the last snapshot.
                        "latest_scored_ordinal": observed[rid],
                        "interval_scored_tokens": count,
                    }
        for rid, row in selected.items():
            candidate = first_positive.get(rid)
            if candidate is None:
                continue
            total = results.get(rid)
            if rid in invalid:
                excluded["invalid_client_snapshot_order_or_count"] += 1
            elif type(total) is not int or total <= 0:
                excluded["missing_client_scored_total"] += 1
            elif observed[rid] != total:
                excluded["unclosed_client_scored_total"] += 1
            elif not 0 < candidate["latest_scored_ordinal"] <= total:
                excluded["invalid_candidate_ordinal"] += 1
            else:
                candidates[rid] = {
                    **candidate,
                    "row": row,
                    "total_scored": total,
                }
    return candidates, excluded


def audit_run(run: Path, rows: list[dict]) -> dict:
    candidates, exclusions = _client_ordinals(
        run / "workloads/workflows", rows,
    )
    server = run / "server"
    results = {}
    server_invalid = set()
    with (server / "runtime_events.sglang.jsonl").open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if event.get("kind") != "llm_result":
                continue
            attrs = event.get("attributes") or {}
            rid = attrs.get("request_id")
            candidate = candidates.get(rid)
            if candidate is None:
                continue
            if rid in results or not _identity_matches(event, candidate["row"]):
                exclusions["server_result_ambiguous"] += 1
                server_invalid.add(rid)
                continue
            count = attrs.get("output_tokens")
            if type(count) is not int or count < candidate["total_scored"]:
                exclusions["invalid_server_output_count"] += 1
                continue
            results[rid] = (float(event["ts_ms"]), count)

    # N - M is an upper bound on the number of unscored tokens preceding
    # any of the M scored tokens, even if some unscored tokens lie between
    # scored ones. Adding the end-of-snapshot scored ordinal gives a
    # conservative latest possible GPU token for the first threshold hit.
    watched = {}
    for rid, candidate in candidates.items():
        if rid not in results or rid in server_invalid:
            continue
        done, count = results[rid]
        watched[rid] = {
            **candidate,
            "server_done_ms": done,
            "server_output_tokens": count,
            "latest_server_ordinal": latest_server_ordinal(
                count, candidate["total_scored"],
                candidate["latest_scored_ordinal"],
            ),
        }
    for rid in candidates.keys() - results.keys():
        exclusions["missing_server_result"] += 1
    decoded: dict[str, list[tuple[float, int]]] = defaultdict(list)
    with (server / "runtime_audit.jsonl").open("rb") as stream:
        for line in stream:
            event = orjson.loads(line)
            if (
                event.get("event") != "gpu_service_sample"
                or event.get("phase") != "decode"
            ):
                continue
            for sample in event.get("request_samples") or ():
                rid = sample.get("request_id")
                candidate = watched.get(rid)
                if candidate is None:
                    continue
                if not _identity_matches(sample, candidate["row"]):
                    exclusions["decode_sample_identity_mismatch"] += 1
                    continue
                before, delta = (
                    sample.get("output_tokens_before"),
                    sample.get("token_delta"),
                )
                if (
                    sample.get("token_delta_semantics")
                    != "observed_output_ids_delta"
                    or type(before) is not int or before < 0
                    or type(delta) is not int or delta < 0
                    or before + delta > candidate["server_output_tokens"]
                ):
                    exclusions["invalid_decode_sample"] += 1
                    continue
                when = float(event["ts_ms"])
                if when <= candidate["server_done_ms"] + 100:
                    decoded[rid].append((when, before + delta))

    evidence = []
    for rid, candidate in watched.items():
        samples = sorted(decoded.get(rid, []))
        if any(
            current[1] < previous[1]
            for previous, current in zip(samples, samples[1:])
        ):
            exclusions["nonmonotonic_decode_sample"] += 1
            continue
        crossing = next((
            when for when, count in samples
            if count >= candidate["latest_server_ordinal"]
        ), None)
        if crossing is None or crossing > candidate["server_done_ms"]:
            exclusions["missing_or_late_ordinal_crossing"] += 1
            continue
        lead = candidate["server_done_ms"] - crossing
        evidence.append({
            "project": candidate["row"]["project"],
            "label": candidate["row"]["label"],
            "join_last": bool(candidate["row"]["join_last"]),
            "lead_lower_bound_ms": max(0., lead),
            "candidate_interval_scored_tokens": candidate[
                "interval_scored_tokens"
            ],
        })

    grouped = {}
    for project in sorted({row["project"] for row in rows}):
        selected = [row for row in rows if row["project"] == project]
        project_evidence = [entry for entry in evidence if entry["project"] == project]
        returns = [entry for entry in project_evidence if entry["label"] == "return"]
        tools = [entry for entry in project_evidence if entry["label"] == "tool"]
        lead = [entry["lead_lower_bound_ms"] for entry in returns]
        grouped[project] = {
            "natural_returns_with_content": sum(
                row["label"] == "return" for row in selected
            ),
            "tools_with_content": sum(
                row["label"] == "tool" for row in selected
            ),
            "first_eos_005_with_closed_gpu_bound": len(project_evidence),
            "matched_natural_returns": len(returns),
            "matched_tool_rounds": len(tools),
            "join_last_returns_with_eos": sum(
                entry["join_last"] for entry in returns
            ),
            "join_last_min_gpu_lead_at_least_100ms": sum(
                entry["join_last"] and entry["lead_lower_bound_ms"] >= 100
                for entry in returns
            ),
            "join_last_min_gpu_lead_at_least_250ms": sum(
                entry["join_last"] and entry["lead_lower_bound_ms"] >= 250
                for entry in returns
            ),
            "join_last_min_gpu_lead_at_least_500ms": sum(
                entry["join_last"] and entry["lead_lower_bound_ms"] >= 500
                for entry in returns
            ),
            "min_gpu_lead_at_least_100ms": sum(t >= 100 for t in lead),
            "min_gpu_lead_at_least_250ms": sum(t >= 250 for t in lead),
            "min_gpu_lead_at_least_500ms": sum(t >= 500 for t in lead),
            "min_gpu_lead_p50_ms": _quantile(lead, .5),
            "min_gpu_lead_p90_ms": _quantile(lead, .9),
            "candidate_interval_scored_tokens_p50": _quantile([
                entry["candidate_interval_scored_tokens"]
                for entry in project_evidence
            ], .5),
        }
    return {
        "run": str(run),
        "projects": grouped,
        "first_threshold": .05,
        "excluded": dict(exclusions),
        "limitations": (
            "The bound uses the server final output count and the client "
            "final scored count retrospectively; neither is an online "
            "feature. An EOS hit anywhere inside a content snapshot may "
            "be earlier than the latest scored ordinal. Server completion "
            "is a decode endpoint, not child RETURN or H2D readiness. "
            "The top-20 client changes service and agent trajectories."
        ),
    }


def evaluate(runs: list[Path]) -> dict:
    if len(set(runs)) != len(runs):
        raise ValueError("runs must be distinct")
    roots = [run / "workloads/workflows" for run in runs]
    rows, counts, manifests, sampled = load_rows(roots)
    if not sampled:
        raise ValueError("sampled EOS observations are required")
    tasks = {
        run: {
            path.parent.name for path in root.glob(
                "*/child_stream_content.jsonl"
            )
        }
        for run, root in zip(runs, roots)
    }
    return {
        "scope": (
            "Posthoc lower bound on GPU time left at the FIRST sampled "
            "0.05 EOS hit per request, not an online predictive policy."
        ),
        "collection_counts": dict(counts),
        "manifests": manifests,
        "runs": [
            audit_run(run, [row for row in rows if row["task"] in tasks[run]])
            for run in runs
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
