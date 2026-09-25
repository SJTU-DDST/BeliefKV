from __future__ import annotations

import json

from scripts.pilot_native_join_progress import _collect, _feature


def test_join_progress_does_not_use_future_or_partial_child_snapshots(tmp_path):
    (tmp_path / "reentries.jsonl").write_text(
        json.dumps({
            "reentry_kind": "join",
            "terminal_status": "satisfied",
            "training_eligible": True,
            "workflow_id": "wf",
            "reentry_id": "join:one",
            "project": "example/one",
            "invocation_id": "parent",
            "wait_start_ts_ms": 100,
            "reentry_ts_ms": 800,
            "member_outcomes": [
                {"invocation_id": "child-a", "start_ts_ms": 80,
                 "return_ts_ms": 400},
                {"invocation_id": "child-b", "start_ts_ms": 90,
                 "return_ts_ms": 800},
            ],
        }) + "\n",
        encoding="utf-8",
    )

    def row(timestamp, *, full=True, state="wait_join"):
        invocations = [{"invocation_id": "parent", "state": state}]
        invocations.append({"invocation_id": "child-a", "llm_round": 1})
        if full:
            invocations.append({"invocation_id": "child-b", "llm_round": 2})
        return {
            "workflow_id": "wf", "timestamp_ms": timestamp,
            "invocations": invocations,
        }

    rows = [
        row(90), row(120, full=False), row(150, state="ready"),
        row(200), row(300), row(500), row(900),
    ]
    (tmp_path / "frontier_decision_points.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in rows),
        encoding="utf-8",
    )
    groups = _collect(tmp_path, sample_spacing_ms=75)
    assert len(groups) == 1
    assert groups[0]["project"] == "example/one"
    assert [(ts, sorted(pending))
            for ts, pending in groups[0]["snapshots"]] == [
                (200, ["child-a", "child-b"]),
                (300, ["child-a", "child-b"]),
                (500, ["child-b"]),
    ]
    assert groups[0]["snapshots"][0][1]["child-b"][
        "invocation_elapsed_ms"
    ] == 110
    assert groups[0]["snapshots"][0][1]["child-b"]["is_child"] is True
    assert _feature(
        groups[0]["snapshots"][0][1]["child-b"],
        {"state": {}, "agent_definition_id": {},
         "active_tool_family": {}, "backend_pressure": {}},
    )[5] > 0
