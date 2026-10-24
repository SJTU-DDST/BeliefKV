from copy import deepcopy
import json

import pytest

from scripts.run_causal_comparison import (
    METRIC_BOUNDARY, replay_event_metrics, workflow_event_metrics,
)


def events():
    return [
        {"kind": "workflow_start", "workflow_id": "early", "ts_ms": 100.},
        {"kind": "workflow_start", "workflow_id": "late", "ts_ms": 200.},
        {"kind": "workflow_end", "workflow_id": "early", "ts_ms": 1100.},
        {"kind": "workflow_end", "workflow_id": "late", "ts_ms": 10200.},
    ]


def test_jct_and_makespan_keep_arrival_spread_and_the_last_workflow():
    result = workflow_event_metrics(events(), ["early", "late"])
    assert result["metric_boundary"] == METRIC_BOUNDARY
    assert result["workflow_jct_ms"] == {"early": 1000., "late": 10000.}
    assert result["mean_jct_ms"] == 5500.
    assert result["workflow_event_makespan_ms"] == 10100.


@pytest.mark.parametrize("change", (
    "missing_end", "missing_start", "duplicate_end", "foreign_workflow",
    "nan", "bool", "negative", "zero_span", "reversed_span", "empty_population",
))
def test_missing_or_ambiguous_boundary_data_cannot_produce_comparable_metrics(change):
    rows = events()
    population = ["early", "late"]
    if change == "missing_end":
        rows.pop()
    elif change == "missing_start":
        rows.pop(0)
    elif change == "duplicate_end":
        rows.append(deepcopy(rows[-1]))
    elif change == "foreign_workflow":
        rows[-1]["workflow_id"] = "other"
    elif change == "nan":
        rows[0]["ts_ms"] = float("nan")
    elif change == "bool":
        rows[0]["ts_ms"] = True
    elif change == "negative":
        rows[0]["ts_ms"] = -1.
    elif change == "zero_span":
        rows[-1]["ts_ms"] = 200.
    elif change == "reversed_span":
        rows[-1]["ts_ms"] = 199.
    else:
        population = []
    with pytest.raises(ValueError, match="workflow metrics"):
        workflow_event_metrics(rows, population)


@pytest.mark.parametrize("change", (None, "missing", "changed", "bool"))
def test_replay_jct_is_checked_against_the_actual_emitted_events(tmp_path, change):
    target = tmp_path / "replay"
    target.mkdir()
    path = target / "runtime_events.replay.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in events()))
    result = {"workflow_elapsed_ms": {"early": 1000., "late": 10000.}}
    if change == "missing":
        del result["workflow_elapsed_ms"]["late"]
    elif change == "changed":
        result["workflow_elapsed_ms"]["late"] = 10001.
    elif change == "bool":
        result["workflow_elapsed_ms"]["early"] = True
    if change:
        with pytest.raises(ValueError, match="emitted event boundaries"):
            replay_event_metrics(result, tmp_path, ["early", "late"])
    else:
        measured = replay_event_metrics(result, tmp_path, ["early", "late"])
        assert measured["workflow_event_makespan_ms"] == 10100.
        assert len(measured["workflow_event_log_sha256"]) == 64
