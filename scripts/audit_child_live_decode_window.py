#!/usr/bin/env python3
"""Upper-bound child RETURN windows with a still-unfinished decode request."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_child_http_stream_transport import _quantile
from scripts.child_stream_service_index import load_index, snapshot_status
from scripts.pilot_child_stream_content import collect


def audit(run: Path) -> dict:
    workflows = run / "workloads/workflows"
    rows, collection = collect(workflows, min_snapshot_chars=1)
    validity = {
        path.parent.name: orjson.loads(path.read_bytes())["measurement_valid"]
        for path in workflows.glob("*/result.json")
    }
    service, bracket, exclusions = load_index(run, rows)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rid = row["rid"]
        state = service.get(rid)
        if state is None:
            exclusions["missing_server_result"] = (
                exclusions.get("missing_server_result", 0) + 1
            )
            continue
        window = [
            snap for snap in row["snapshots"]
            if 500 <= row["return_ts"] - snap["ts_ms"] <= 2000
        ] if row["label"] == "return" else []
        unfinished = [
            snap for snap in window
            if snapshot_status(state, snap["ts_ms"]).startswith("unfinished_")
        ]
        live = [
            snap for snap in unfinished
            if snapshot_status(state, snap["ts_ms"])
            == "unfinished_with_recent_decode"
        ]
        grouped[row["project"]].append({
            "label": row["label"],
            "join_last": row["join_last"],
            "measurement_valid": validity[row["task"]],
            "has_window": bool(window),
            "has_unfinished": bool(unfinished),
            "has_recent_decode": bool(live),
            "server_end_to_return_ms": (
                row["return_ts"] - (
                    state["server_end_ms"] - state["offset_lower_ms"]
                )
                if row["label"] == "return" else None
            ),
        })
    projects = {}
    for project, entries in sorted(grouped.items()):
        natural = [row for row in entries if row["label"] == "return"]
        last = [row for row in natural if row["join_last"]]
        valid = [row for row in natural if row["measurement_valid"]]
        valid_last = [row for row in valid if row["join_last"]]
        projects[project] = {
            "natural_returns": len(natural),
            "join_last_returns": len(last),
            "content_window_oracle": sum(row["has_window"] for row in natural),
            "window_before_server_end": sum(
                row["has_unfinished"] for row in natural
            ),
            "window_before_server_end_with_prior_decode": sum(
                row["has_recent_decode"] for row in natural
            ),
            "join_last_content_window_oracle": sum(
                row["has_window"] for row in last
            ),
            "join_last_window_before_server_end": sum(
                row["has_unfinished"] for row in last
            ),
            "join_last_window_with_prior_decode": sum(
                row["has_recent_decode"] for row in last
            ),
            "valid_workflow_natural_returns": len(valid),
            "valid_workflow_window_with_prior_decode": sum(
                row["has_recent_decode"] for row in valid
            ),
            "valid_workflow_join_last_returns": len(valid_last),
            "valid_workflow_join_last_window_with_prior_decode": sum(
                row["has_recent_decode"] for row in valid_last
            ),
            "tool_rounds_with_content": sum(
                row["label"] == "tool" for row in entries
            ),
            "server_end_to_return_p50_ms": _quantile([
                row["server_end_to_return_ms"] for row in natural
                if row["server_end_to_return_ms"] is not None
            ], .5),
        }
    return {
        "scope": (
            "Development-only offline oracle, not an online feature or "
            "calibrated model. Unfinished requests may still be queued. "
            "Prior decode means at least one identity-matched positive token "
            "delta in the preceding 2 seconds; missing samples are not proof "
            "that no decode occurred. Return windows require delivered content."
        ),
        "clock_bracket": bracket,
        "collection": dict(collection),
        "excluded": exclusions,
        "projects": projects,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.write_text(
        json.dumps(audit(args.run), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
