import json

from scripts.evaluate_child_return_content_service import (
    _first_snapshots, _latest_notice, _notices, evaluate_rows,
)


def _row(project, label, lead, chars):
    return {
        "project": project, "task": f"{project}__task",
        "label": label, "return_ts": 3000 + lead if label == "return" else None,
        "snapshots": [
            {"ts_ms": 3000, "content_tail": "In summary.",
             "content_chars": chars, "decode_features": (1., 2., 3., 0.)},
            {"ts_ms": 3000 + lead - 1, "content_tail": "Final answer.",
             "content_chars": chars + 100, "decode_features": (3., 4., 5., 1.)},
        ],
    }


def test_project_holdout_first_observed_service_snapshot_only():
    rows = [
        _row("alpha", "return", 2100, 128),
        _row("beta", "return", 1700, 180),
        _row("gamma", "return", 900, 220),
        _row("delta", "return", 1500, 300),
        _row("delta", "tool", 0, 256),
    ]
    assert _first_snapshots(rows)[0]["snapshots"] == rows[0]["snapshots"][:1]
    assert _first_snapshots(rows, 220)[0]["snapshots"][0]["content_chars"] >= 220
    report = evaluate_rows(rows)
    assert report["eligible_requests"] == 5
    assert report["natural_returns"] == 4
    assert report["tool_rounds"] == 1
    assert report["project_holdouts"]["delta"]["held_tool_requests"] == 1
    assert report["project_holdouts"]["delta"]["eta"]["semantic"]["count"] == 1
    assert report["project_holdouts"]["alpha"]["train_return_requests"] == 3
    assert report["project_holdouts"]["delta"]["terminal_screen"]["semantic"] is None


def test_later_snapshots_cannot_change_first_snapshot_eta():
    rows = [
        _row(project, "return", lead, 128 + i * 32)
        for i, (project, lead) in enumerate(
            (("alpha", 900), ("beta", 1100), ("gamma", 1500), ("delta", 2100))
        )
    ]
    original = evaluate_rows(rows)
    for row in rows:
        row["snapshots"][1]["decode_features"] = (1e9,) * 4
        row["snapshots"][1]["content_tail"] = "Future final answer."
    assert evaluate_rows(rows) == original


def test_fixed_threshold_uses_first_causal_crossing():
    rows = [_row("alpha", "return", 2100, 64)]
    assert evaluate_rows(rows, min_chars=512)["eligible_requests"] == 0
    selected = _first_snapshots(rows, 128)
    assert selected[0]["snapshots"][0] is rows[0]["snapshots"][1]


def test_terminal_screen_has_project_heldout_tool_denominator():
    rows = [
        _row(project, label, 1000, 128 + i * 32)
        for i, (project, label) in enumerate(
            (("alpha", "return"), ("alpha", "tool"),
             ("beta", "return"), ("beta", "tool"),
             ("gamma", "return"), ("gamma", "tool"),
             ("delta", "return"), ("delta", "tool"))
        )
    ]
    result = evaluate_rows(rows)["project_holdouts"]["delta"]
    screen = result["terminal_screen"]["semantic"]
    assert screen["train_tool_requests"] == 3
    assert result["held_tool_requests"] == 1
    assert screen["held_return_hits"] <= 1
    assert screen["held_tool_false_positives"] <= 1
    assert (
        screen["held_return_hits"] + screen["held_tool_false_positives"]
        == screen["held_flagged"]
    )


def test_notice_only_affects_snapshots_after_delivery(tmp_path):
    rows = [
        _row(project, "return", 1000 + i * 200, 128 + i * 32)
        for i, project in enumerate(("alpha", "beta", "gamma", "delta"))
    ]
    for row in rows:
        row["invocation_id"] = "child"
    baseline = evaluate_rows(rows)
    future = {("alpha__task", "child"): [(3100., 4000)]}
    assert evaluate_rows(rows, notices=future) == baseline
    now = {("alpha__task", "child"): [(2900., 4000), (3100., 1)]}
    assert _latest_notice(now, "alpha__task", "child", 3000.) == (2900., 4000)
    assert _latest_notice(now, "alpha__task", "other-child", 3000.) is None
    assert evaluate_rows(rows, notices=now)["notice_seen_on_returns"] == 1

    workflow = tmp_path / "alpha__task"
    workflow.mkdir()
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        json.dumps({
            "kind": "structured_action", "ts_ms": 2900.,
            "invocation_id": "child", "attributes": {
                "child_completion_signal_kind": "stage",
                "beliefkv_child_completion_intent": True,
                "estimated_final_report_tokens": 4000,
            },
        }) + "\n"
    )
    assert _notices(tmp_path) == {("alpha__task", "child"): [(2900., 4000)]}
