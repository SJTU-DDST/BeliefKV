#!/usr/bin/env python3
"""Read-only, project-disjoint child RETURN screen using recent server EOS windows."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_child_server_eos_cue import (
    _first_tool_chunks,
    _native_results,
    expected_eos_ids,
)
from scripts.compare_child_return_joint_cues import summarize
from scripts.pilot_child_stream_content import collect, evaluate
from scripts.pilot_join_service_progress import clock_bracket


EOS_THRESHOLD = 0.05
CONTENT_FLOOR_CHARS = 256
CONTENT_FRESHNESS_MS = 1000.0


def read_windows(run: Path, rows: list[dict], tokenizer_json: Path) -> tuple[dict, dict]:
    workflows = run / "workloads/workflows"
    events = run / "server/runtime_events.sglang.jsonl"
    native = _native_results(events)
    lower, _upper, bridge = clock_bracket(workflows, events)
    eos_ids = expected_eos_ids(tokenizer_json)
    by_rid = {row["rid"]: row for row in rows}
    if len(by_rid) != len(rows):
        raise ValueError("duplicate child request ID")
    workflow_info = {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in workflows.glob("*/result.json")
    }
    windows = defaultdict(list)
    invalid = set()
    reasons = Counter()
    prior_ordinal: dict[str, int] = {}
    with (run / "server/runtime_audit.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            rid = event.get("request_id")
            if rid not in by_rid:
                continue
            if event.get("event") == "targeted_pair_ordinal_invalid":
                invalid.add(rid)
                reasons["producer_ordinal_invalid"] += 1
                continue
            if event.get("event") != "targeted_pair_recent_window":
                continue
            row = by_rid[rid]
            info = workflow_info.get(row["task"], {})
            if info.get("measurement_valid") is not True:
                reasons["invalid_workflow"] += 1
                continue
            result = native.get(rid)
            if (
                result is None
                or result.get("workflow_id") != info.get("workflow_id")
                or event.get("workflow_id") != info.get("workflow_id")
                or any(
                    result.get(key) != row[key] or event.get(key) != row[key]
                    for key in ("invocation_id", "context_id", "context_epoch")
                )
            ):
                invalid.add(rid)
                reasons["identity_mismatch"] += 1
                continue
            first = event.get("first_output_token_ordinal")
            last = event.get("last_output_token_ordinal")
            peak = event.get("max_output_token_ordinal")
            count = event.get("scored_tokens")
            output_tokens = (result.get("attributes") or {}).get("output_tokens")
            if (
                not isinstance(event.get("probe_token_ids"), list)
                or len(event["probe_token_ids"]) != 2
                or any(type(value) is not int for value in event["probe_token_ids"])
                or frozenset(event["probe_token_ids"]) != eos_ids
                or any(type(value) is not int for value in (
                    first, last, peak, count, output_tokens,
                ))
                or not 1 <= first <= peak <= last <= output_tokens
                or not 1 <= count <= 32
                or last - first + 1 < count
                or last <= prior_ordinal.get(rid, 0)
                or any(
                    type(event.get(key)) not in (int, float)
                    or not math.isfinite(event[key])
                    for key in ("ts_ms", "max_logprob", "last_logprob")
                )
            ):
                invalid.add(rid)
                reasons["invalid_window_evidence"] += 1
                continue
            prior_ordinal[rid] = last
            if event["ts_ms"] > result["ts_ms"]:
                reasons["post_result_window"] += 1
                continue
            if event.get("finished_at_boundary") is not False:
                reasons["terminal_window"] += 1
                continue
            windows[rid].append({
                "ts_ms": float(event["ts_ms"]) - lower,
                "max_logprob": float(event["max_logprob"]),
                "last_logprob": float(event["last_logprob"]),
                "last_ordinal": last,
            })
    for rid in invalid:
        windows.pop(rid, None)
    if not any(windows.values()):
        raise ValueError("no valid pre-terminal server EOS windows")
    return dict(windows), {
        "clock_bridge": bridge,
        "exclusions": dict(reasons),
        "invalid_requests": len(invalid),
        "requests_with_windows": len(windows),
    }


def first_cues(
    rows: dict[str, dict], windows: dict[str, list[dict]],
    tool_chunks: dict[str, float], model_times: dict[str, list[float]] | None,
) -> dict[str, float]:
    first: dict[str, float] = {}
    for rid, row in rows.items():
        cutoff = min(float(row["result_ts"]), tool_chunks.get(rid, math.inf))
        eligible_snapshots = [
            snap for snap in row["snapshots"]
            if snap["content_chars"] >= CONTENT_FLOOR_CHARS
            and snap["content_tail"].strip()
        ]
        if not eligible_snapshots:
            continue
        times = model_times.get(rid, []) if model_times is not None else None
        for window in sorted(windows.get(rid, []), key=lambda item: item["ts_ms"]):
            ts = window["ts_ms"]
            if ts >= cutoff:
                break
            if window["max_logprob"] < math.log(EOS_THRESHOLD):
                continue
            if not any(snap["ts_ms"] <= ts for snap in eligible_snapshots):
                continue
            if times is not None and not any(
                ts - CONTENT_FRESHNESS_MS <= instant <= ts
                for instant in times
            ):
                continue
            first[rid] = ts
            break
    return first


def audit(run: Path, split: Path, tokenizer_json: Path) -> dict:
    selection = json.loads(split.read_text(encoding="utf-8"))
    fit = tuple(selection["fit_projects"])
    calibration = tuple(selection["calibration_projects"])
    heldouts = tuple(selection["heldout_projects"])
    if not fit or not calibration or not heldouts or (
        len(set(fit + calibration + heldouts)) != len(fit + calibration + heldouts)
    ):
        raise ValueError("fit, calibration, and held-out projects must be disjoint")
    selected = selection["instance_ids"]
    actual = json.loads(
        (run / "workloads/manifest.json").read_text(encoding="utf-8")
    )["config"]["instance_ids"]
    if (
        len(set(selected)) != len(selected)
        or actual != selected
        or {task.split("__", 1)[0] for task in selected}
        != set(fit + calibration + heldouts)
    ):
        raise ValueError("collected tasks do not match the frozen project split")
    workflows = run / "workloads/workflows"
    rows, counts = collect(workflows, min_snapshot_chars=1)
    windows, evidence = read_windows(run, rows, tokenizer_json)
    tool_chunks = _first_tool_chunks(workflows)
    valid_tasks = {
        path.parent.name
        for path in workflows.glob("*/result.json")
        if json.loads(path.read_text(encoding="utf-8")).get("measurement_valid") is True
    }
    grouped: dict[str, dict] = {}
    for project in fit + calibration + heldouts:
        project_rows = {
            row["rid"]: row for row in rows
            if row["project"] == project and row["task"] in valid_tasks
        }
        if not project_rows:
            grouped[project] = {"status": "no_qualified_requests"}
            continue
        eos_first = first_cues(project_rows, windows, tool_chunks, None)
        if project not in heldouts:
            grouped[project] = {
                "status": "fit_or_calibration_description_only",
                "qualified_requests": len(project_rows),
                "eos_recent_with_content": summarize(project_rows, eos_first),
            }
            continue
        content = evaluate(
            workflows, project,
            train_projects=fit,
            calibration_projects=calibration,
            include_qualified_snapshot_times=True,
        )
        results = {}
        for name in ("size_only", "progress_only", "progress_conditioned_content"):
            model = content.get("results", {}).get(f"near_return_2000ms_{name}")
            if not model or model.get("status"):
                results[name] = {"status": "insufficient_content_model"}
                continue
            qualified = model["qualified_snapshot_times_by_request"]
            results[name] = {
                "selection": "frozen_project_calibration",
                "content_only": summarize(project_rows, {
                    rid: min(times) for rid, times in qualified.items()
                    if rid in project_rows
                }),
                "recent_eos_and_content": summarize(
                    project_rows,
                    first_cues(project_rows, windows, tool_chunks, qualified),
                ),
            }
        grouped[project] = {
            "status": content["status"],
            "qualified_requests": len(project_rows),
            "eos_recent_with_content": summarize(project_rows, eos_first),
            "models": results,
        }
    return {
        "scope": (
            "Development-only, read-only first-eligible-window screen. The 256 "
            "delivered-character floor was selected on Django/Pytest and has "
            "not been independently validated; the request's most recent "
            "qualified content snapshot must precede the EOS window by <=1s. "
            "The client notification is assumed instantaneous; this is an "
            "optimistic opportunity bound, not physical H2D evidence. Pydata "
            "and Pylint have already been used for development and are not "
            "new sealed test projects."
        ),
        "configuration": {
            "eos_threshold": EOS_THRESHOLD,
            "content_floor_chars": CONTENT_FLOOR_CHARS,
            "content_freshness_ms": CONTENT_FRESHNESS_MS,
            "fit_projects": fit,
            "calibration_projects": calibration,
            "development_projects": heldouts,
        },
        "collection": dict(counts),
        "evidence": evidence,
        "projects": grouped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--tokenizer-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.run, args.split, args.tokenizer_json)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
