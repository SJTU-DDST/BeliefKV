from __future__ import annotations

import json

import pytest

from scripts.evaluate_child_eos_shadow import audit, evaluate, load, score


def _trace(root, project, name, *, cue=True, label="true", cue_ts=1200):
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
            "kind": "structured_action", "ts_ms": cue_ts,
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
            [train_row] if root == train else heldout_rows,
            {"workflows": 3, "observed_child_returns_total": 3,
             "observed_join_last_total": 2, "natural_child_returns_total": 3,
             "natural_join_last_total": 2},
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
    coverage = audit(heldout)
    assert coverage["eos_scored_tokens_available"] == 3
    assert coverage["natural_join_last_total"] == 2
    assert coverage["observed_join_last_total"] == 2
    assert coverage["thresholds"]["0.1"]["false_first_trigger"] == 1
    assert coverage["thresholds"]["0.1"]["natural_lead_ms"] == {
        "min": 800., "median": 800., "max": 800.,
    }
    duplicate = _trace(heldout, "alpha", "1")
    heldout_rows.append(duplicate)
    with pytest.raises(ValueError, match="disjoint"):
        evaluate([train], heldout)


def test_eos_audit_does_not_count_late_delivery_as_advance(monkeypatch, tmp_path):
    root = tmp_path / "workflows"
    rows = [
        _trace(root, "alpha", "on_time"),
        _trace(root, "alpha", "late", cue_ts=2200),
        _trace(root, "alpha", "simultaneous", cue_ts=2000),
    ]
    monkeypatch.setattr(
        "scripts.evaluate_child_eos_shadow.collect",
        lambda _root, _threshold: (
            rows, {"workflows": 3, "observed_child_returns_total": 3,
                   "observed_join_last_total": 3,
                   "natural_child_returns_total": 3,
                   "natural_join_last_total": 3},
        ),
    )
    coverage = audit(root)["thresholds"]["0.1"]
    assert coverage["first_trigger"] == 3
    assert coverage["natural_first_trigger"] == 1
    assert coverage["after_return_trigger"] == 2
    assert coverage["natural_with_500ms_lead"] == 1
    assert coverage["natural_join_last_trigger"] == 1


def test_low_prob_audit_requires_trace_collection_contract(monkeypatch, tmp_path):
    workflows = tmp_path / "run" / "workflows"
    row = _trace(workflows, "alpha", "one")
    row["observed_first_content_ts_ms"] = 950.
    path = workflows / row["task_id"] / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events.append({
        "kind": "structured_action", "ts_ms": 970,
        "invocation_id": "child", "context_id": "ctx", "context_epoch": 1,
        "attributes": {
            "request_id": "req", "beliefkv_child_eos_shadow": True,
            "eos_top_probability_threshold": 0.001,
        },
    })
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    monkeypatch.setattr(
        "scripts.evaluate_child_eos_shadow.collect",
        lambda _root, _threshold: (
            [row], {"workflows": 1, "observed_child_returns_total": 1,
                    "observed_join_last_total": 1,
                    "natural_child_returns_total": 1,
                    "natural_join_last_total": 1},
        ),
    )
    assert "0.001" not in audit(workflows)["thresholds"]
    with pytest.raises(ValueError, match="was not collected"):
        audit(workflows, (0.001, 0.01))
    (workflows.parent / "manifest.json").write_text(json.dumps({
        "config": {"child_eos_low_prob_shadow": True},
    }))
    low = audit(workflows, (0.001, 0.01))["thresholds"]["0.001"]
    assert low["natural_first_trigger"] == 1
    assert low["natural_lead_ms"]["median"] == 1030


def test_early_eos_uses_only_first_content_baseline():
    train = {
        "task_id": "train__one", "label": "true", "lead_ms": 1000.,
        "return_ts_ms": 2000., "signal_ts_ms": 1000.,
        "observed_first_content_ts_ms": 900.,
        "first_eos_ts": {0.001: 980.}, "join_last": True,
        "scored_tokens": 5, "top_hits": 1,
    }
    test = {
        **train, "task_id": "heldout__one", "return_ts_ms": 2100.,
        "first_eos_ts": {0.001: 950.},
    }
    report = score([train], [test], 0.001)
    assert report["first_trigger_before_64_chars"] == 1
    timing = report["return_timing_same_triggers"]
    assert timing["train_first_64_prior_at_cue"] is None
    assert timing["train_first_content_prior_at_cue"]["count"] == 1


def test_late_low_eos_may_compare_to_past_64_char_stage():
    train = {
        "task_id": "train__one", "label": "true", "lead_ms": 1100.,
        "return_ts_ms": 2000., "signal_ts_ms": 900.,
        "first_64_ts_ms": 1050., "stage_threshold_chars": 0,
        "observed_first_content_ts_ms": 900.,
        "first_eos_ts": {0.001: 1200.}, "join_last": True,
        "scored_tokens": 5, "top_hits": 1,
    }
    test = {
        **train, "task_id": "heldout__one", "return_ts_ms": 2100.,
        "first_eos_ts": {0.001: 1250.},
    }
    report = score([train], [test], 0.001)
    assert report["first_trigger_before_64_chars"] == 0
    assert report["return_timing_same_triggers"][
        "train_first_64_prior_at_cue"
    ]["count"] == 1
