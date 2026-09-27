import json

import pytest

from scripts.evaluate_tool_global_execute_pressure import (
    _boundaries, _candidate, _quality, attach_pressure, choose,
)


def _row(project, index, *, duration=800., active=9, chars=38, score=.2):
    return {
        "project": project,
        "workflow": f"{project}__{index}",
        "shape": "other",
        "input_chars": chars,
        "other_execute_inflight": active,
        "score": score,
        "duration_ms": duration,
        "start_ts_ms": 200.,
    }


def test_pressure_at_tool_start_ignores_same_time_and_future_events():
    rows = attach_pressure(
        [_row("test", 0)], starts=[0., 100., 200., 250.],
        ends=[50., 200.],
    )
    assert rows[0]["other_execute_inflight"] == 1
    with pytest.raises(ValueError, match="ordered"):
        attach_pressure([_row("test", 0)], [200., 100.], [])
    with pytest.raises(ValueError, match="negative occupancy"):
        attach_pressure([_row("test", 0)], [], [100.])


def test_open_execute_is_counted_and_orphan_end_is_rejected(tmp_path):
    workflow = tmp_path / "one"
    workflow.mkdir()
    trace = workflow / "runtime_events.deepagents.jsonl"

    def record(kind, ts, call):
        return {
            "kind": kind, "ts_ms": ts,
            "attributes": {"tool_name": "execute", "tool_call_id": call},
        }

    trace.write_text("\n".join(json.dumps(row) for row in [
        record("tool_start", 100, "a"),
        record("tool_end", 150, "a"),
        record("tool_start", 200, "b"),
    ]) + "\n", encoding="utf-8")
    starts, ends = _boundaries(tmp_path)
    assert starts == [100., 200.]
    assert ends == [150.]
    assert attach_pressure([{
        **_row("test", 0), "start_ts_ms": 220.,
    }], starts, ends)[0]["other_execute_inflight"] == 1
    with trace.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record("tool_end", 250, "unknown")) + "\n")
    with pytest.raises(ValueError, match="orphan"):
        _boundaries(tmp_path)


def test_policy_only_adds_frozen_classifier_misses_and_keeps_project_guard():
    rows = [
        _row(project, index)
        for project in ("django", "pydata", "pytest-dev")
        for index in range(3)
    ]
    policy, evidence = choose(rows)
    assert policy is not None
    assert evidence[f"{policy[0]}:{policy[1]}"]["qualified"] is True
    assert _quality([row for row in rows if _candidate(row, policy)])[
        "true_windows"
    ] == 9
    assert not _candidate(_row("other", 0, score=.8), policy)
    assert not _candidate(_row("other", 0, chars=200), policy)
    assert choose([
        _row("django", index) for index in range(10)
    ])[0] is None
    assert choose(
        rows + [_row("django", 4, duration=100.)] * 3,
    )[0] is None
