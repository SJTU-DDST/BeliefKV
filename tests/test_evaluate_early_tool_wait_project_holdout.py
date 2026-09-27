import json

import pytest

from scripts.evaluate_early_tool_wait_project_holdout import compare, evaluate


def _event(kind, ts_ms, call_id, **attrs):
    return {
        "kind": kind,
        "ts_ms": ts_ms,
        "invocation_id": "child",
        "attributes": {"tool_call_id": call_id, **attrs},
    }


def _write_trace(workflows, name, rows):
    folder = workflows / name
    folder.mkdir(parents=True)
    (folder / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )


def _train():
    return [
        {"status": "success", "shape": "script", "duration_ms": 1600.0}
        for _ in range(5)
    ]


def _early_attrs(input_hash):
    return {
        "is_child": True,
        "tool_name": "execute",
        "observed_command_shape": "script",
        "input_sha256": input_hash * 64,
        "project_shape_survivor_100ms_total_median_ms": 2200,
        "project_shape_survivor_100ms_support": 4,
        "project_shape_survivor_100ms_deviation_p90_ms": 200,
    }


def test_pairs_first_success_input_with_frozen_shape_and_online_clock(tmp_path):
    workflows = tmp_path / "workflows"
    for index in range(5):
        _write_trace(workflows, f"sphinx__{index}", [
            _event("tool_start", 0, "first", **_early_attrs("a")),
            _event("structured_action", 101, "first",
                   beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
            _event("tool_end", 2200, "first", status="success"),
            _event("tool_start", 3000, "retry", **_early_attrs("a")),
            _event("structured_action", 3101, "retry",
                   beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
            _event("tool_end", 5200, "retry", status="success"),
            _event("workflow_end", 5500, None),
        ])
    report = evaluate(_train(), workflows)
    assert report["heldout_success_distinct_observed_inputs"] == 5
    assert report["heldout_success_window_500ms_start_count"] == 5
    assert report["heldout_success_window_500ms_start_reasons"] == {
        "observed": 5,
    }
    assert report["online_error_p50_ms"] == 0
    assert report["frozen_error_p50_ms"] == 600
    assert report["paired_gain"]["paired_positive_95pct"] is True
    assert report["online_selected_remaining_500ms"] == {
        "distinct_success_inputs": 5, "true_remaining_500ms": 5,
        "returned_before_500ms": 0,
    }


def test_short_window_and_sparse_workflows_cannot_claim_significance(tmp_path):
    workflows = tmp_path / "workflows"
    _write_trace(workflows, "sphinx__short", [
        _event("tool_start", 0, "one", **_early_attrs("a")),
        _event("structured_action", 101, "one",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
        _event("tool_end", 490, "one", status="success"),
        _event("workflow_end", 500, None),
    ])
    report = evaluate(_train(), workflows)
    assert report["heldout_success_window_500ms_start_count"] == 0
    assert report["heldout_success_window_500ms_start_reasons"] == {}
    assert report["online_selected_remaining_500ms"]["returned_before_500ms"] == 1
    assert report["paired_gain"]["paired_positive_95pct"] is False


def test_rejects_observation_after_tool_end(tmp_path):
    workflows = tmp_path / "workflows"
    _write_trace(workflows, "sphinx__invalid", [
        _event("tool_start", 0, "one", **_early_attrs("a")),
        _event("tool_end", 100, "one", status="success"),
        _event("structured_action", 101, "one",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
    ])
    with pytest.raises(ValueError, match="did not precede return"):
        evaluate(_train(), workflows)


def test_explains_why_true_window_lacked_live_qualification(tmp_path):
    workflows = tmp_path / "workflows"
    _write_trace(workflows, "sphinx__missing", [
        _event("tool_start", 0, "one", **{
            **_early_attrs("a"),
            "project_shape_survivor_100ms_support": None,
        }),
        _event("tool_end", 2200, "one", status="success"),
        _event("tool_start", 3000, "two", **{
            **_early_attrs("b"),
            "project_shape_survivor_100ms_total_median_ms": 900,
        }),
        _event("tool_end", 5200, "two", status="success"),
        _event("tool_start", 6000, "three", **{
            **_early_attrs("c"),
            "project_shape_survivor_100ms_deviation_p90_ms": 1100,
        }),
        _event("tool_end", 8200, "three", status="success"),
    ])
    report = evaluate(_train(), workflows)
    assert report["heldout_success_window_500ms_start_reasons"] == {
        "missing_supported_history": 1,
        "historical_median_at_most_1100ms": 1,
        "historical_spread_above_1000ms": 1,
    }


def test_comparison_rejects_same_project_before_reading_trace(tmp_path):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    for folder in (train, heldout):
        folder.mkdir()
        (folder / "manifest.json").write_text(
            json.dumps({"instance_ids": ["sphinx__one"]}), encoding="utf-8",
        )
        (folder / "summary.json").write_text(json.dumps({
            "workflow_count": 1,
            "workflows": [{"instance_id": "sphinx__one", "outcome": "completed"}],
        }), encoding="utf-8")
        (folder / "workflows").mkdir()
        (folder / "workflows/sphinx__one").mkdir()
        (folder / "workflows/sphinx__one/result.json").write_text("{}")
    with pytest.raises(ValueError, match="disjoint"):
        compare(train / "workflows", heldout / "workflows")


def test_comparison_validates_complete_disjoint_batch(tmp_path, monkeypatch):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    for folder, task in ((train, "pydata__one"), (heldout, "sphinx__one")):
        folder.mkdir()
        (folder / "manifest.json").write_text(
            json.dumps({"instance_ids": [task]}), encoding="utf-8",
        )
        (folder / "summary.json").write_text(json.dumps({
            "workflow_count": 1,
            "workflows": [{"instance_id": task, "outcome": "completed"}],
        }), encoding="utf-8")
        (folder / "workflows").mkdir()
        (folder / "workflows" / task).mkdir()
        (folder / "workflows" / task / "result.json").write_text("{}")
    _write_trace(heldout / "workflows", "sphinx__extra", [
        _event("workflow_end", 500, None),
    ])
    monkeypatch.setattr(
        "scripts.evaluate_early_tool_wait_project_holdout.cold_calls",
        lambda _: (_train(), {}),
    )
    with pytest.raises(ValueError, match="frozen workflow traces"):
        compare(train / "workflows", heldout / "workflows")

    (heldout / "workflows/sphinx__extra/runtime_events.deepagents.jsonl").unlink()
    (heldout / "workflows/sphinx__extra").rmdir()
    rows = [
        _event("tool_start", 0, "one", **_early_attrs("a")),
        _event("structured_action", 101, "one",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
        _event("tool_end", 2200, "one", status="success"),
        _event("workflow_end", 2300, None),
    ]
    (heldout / "workflows/sphinx__one/runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )
    report = compare(train / "workflows", heldout / "workflows")
    assert report["train_projects"] == ["pydata"]
    assert report["heldout_projects"] == ["sphinx"]
    assert report["heldout_early_audit"]["counts"]["observations"] == 1
