from __future__ import annotations

import json

import pytest

from scripts.evaluate_child_eos_shadow import audit, evaluate, load


def _trace(root, project, name, *, cue=True, label="true"):
    task = root / f"{project}__{name}"
    task.mkdir(parents=True)
    events = [{
        "kind": "llm_result",
        "ts_ms": 1700,
        "invocation_id": "child",
        "attributes": {
            "request_id": "req", "eos_shadow_scored_tokens": 23,
            "eos_shadow_top_hits": 2,
        },
    }]
    if cue:
        events.append({
            "kind": "structured_action", "ts_ms": 1200,
            "invocation_id": "child", "context_id": "ctx",
            "context_epoch": 1,
            "attributes": {
                "request_id": "req", "beliefkv_child_eos_shadow": True,
                "eos_top_probability_threshold": 0.1,
            },
        })
    (task / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    return {
        "project": project, "task_id": task.name, "invocation_id": "child",
        "request_id": "req", "context_id": "ctx", "context_epoch": 1,
        "signal_ts_ms": 1000., "return_ts_ms": (
            2000. if label == "true" else None
        ), "lead_ms": 1000. if label == "true" else None,
        "label": label, "join_last": True,
    }


def test_eos_first_delivery_project_split_and_missing(monkeypatch, tmp_path):
    train, heldout = tmp_path / "train", tmp_path / "heldout"
    train_row = _trace(train, "alpha", "0")
    heldout_rows = [
        _trace(heldout, "beta", "0"),
        _trace(heldout, "beta", "1", cue=False),
        _trace(heldout, "beta", "2", label="false"),
    ]
    monkeypatch.setattr(
        "scripts.evaluate_child_eos_shadow.collect",
        lambda root, threshold: (
            [train_row] if root == train else heldout_rows, {"workflows": 3}
        ),
    )
    rows, _ = load(heldout)
    assert rows[0]["first_eos_ts"][0.1] == 1200.
    assert not rows[1]["first_eos_ts"]
    assert rows[2]["label"] == "false"
    report = evaluate([train], heldout)["thresholds"]["0.1"]["evaluation"]
    assert report["first_trigger"] == 2
    assert report["first_trigger_labels"] == {"true": 1, "false": 1}
    assert report["natural_without_trigger"] == 1
    assert report["natural_trigger_lead_at_least_500ms"] == 1
    assert audit(heldout)["eos_scored_tokens_available"] == 3
    duplicate = _trace(heldout, "alpha", "1")
    heldout_rows.append(duplicate)
    with pytest.raises(ValueError, match="disjoint"):
        evaluate([train], heldout)
