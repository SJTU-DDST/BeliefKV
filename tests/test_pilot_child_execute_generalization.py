from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.pilot_child_execute_generalization import audit


def _calls(root: Path, project: str, durations: list[tuple[str, float]], *,
           child: bool = True) -> Path:
    target = root / f"{project}__task"
    target.mkdir(parents=True)
    events = []
    for index, (command, duration) in enumerate(durations):
        attrs = {"tool_name": "execute", "tool_call_id": f"call-{index}"}
        events.append({
            "kind": "tool_start", "ts_ms": index * 10000.0,
            "invocation_id": "child", "workflow_id": str(target),
            "attributes": {**attrs, "is_child": child,
                           "observed_command_class": command},
        })
        events.append({
            "kind": "tool_end", "ts_ms": index * 10000.0 + duration,
            "invocation_id": "child", "workflow_id": str(target),
            "attributes": attrs,
        })
    (target / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return root


def test_child_pilot_reports_project_isolated_short_and_long_calls(
    tmp_path: Path,
) -> None:
    train = _calls(tmp_path / "train", "django", [
        ("other", 50), ("test_suite", 3000), ("test_suite", 3200),
    ])
    evaluation = _calls(tmp_path / "evaluation", "astropy", [
        ("other", 60), ("test_suite", 3100),
    ])
    result = audit(train, evaluation, minimum_class_samples=1)
    assert result["evaluation"]["matched_execute_calls"] == 2
    assert result["evaluation"]["long_calls_at_least_2s"] == 1
    assert result["evaluation"]["class_p50_absolute_error_ms"] < (
        result["evaluation"]["global_p50_absolute_error_ms"]
    )
    assert result["train_class_counts"] == {"other": 1, "test_suite": 2}


def test_child_pilot_rejects_overlap_and_non_child_samples(tmp_path: Path) -> None:
    train = _calls(tmp_path / "train", "django", [("other", 50)])
    overlap = _calls(tmp_path / "overlap", "django", [("other", 60)])
    with pytest.raises(ValueError, match="overlap"):
        audit(train, overlap)
    non_child = _calls(tmp_path / "non_child", "astropy", [("other", 60)],
                       child=False)
    with pytest.raises(ValueError, match="no completed child"):
        audit(train, non_child)
