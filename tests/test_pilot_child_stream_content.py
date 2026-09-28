from __future__ import annotations

from collections import Counter

from scripts import pilot_child_stream_content as pilot


def test_project_holdout_counts_tool_rounds_and_first_trigger(monkeypatch, tmp_path):
    rows = []
    for project in ("astropy", "django", "sphinx-doc"):
        for index in range(4):
            for label in ("return", "tool"):
                text = (
                    f"Final result: solution ready for {index}."
                    if label == "return" else
                    f"Still investigating: run tests for {index}."
                )
                rows.append({
                    "task": f"{project}__{index}",
                    "project": project,
                    "rid": f"{project}-{index}-{label}",
                    "label": label,
                    "join_last": label == "return",
                    "return_ts": 1000.0 if label == "return" else None,
                    "snapshots": [
                        {"ts_ms": 100.0, "content_chars": 128,
                         "content_tail": text},
                        {"ts_ms": 300.0, "content_chars": 256,
                         "content_tail": text + " extra"},
                    ],
                })
    monkeypatch.setattr(pilot, "collect", lambda _: (rows, Counter()))
    result = pilot.evaluate(tmp_path, "sphinx-doc")
    assert result["heldout_rounds"] == 8
    assert result["train_rounds"] == 16
    assert result["train_projects"] == ["astropy", "django"]
    for model in result["results"].values():
        assert model["heldout_return_rounds"] == 4
        assert model["heldout_tool_rounds"] == 4
        assert model["heldout_join_last_rounds"] == 4
        assert model["heldout_true_first_triggers"] <= 4
        assert model["heldout_false_first_triggers"] <= 4
