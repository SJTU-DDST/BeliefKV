from __future__ import annotations

import pytest

from scripts.pilot_workflow_tool_dispersion import screen


def _call(
    index: int, start: float, duration: float, *, workflow: str = "one",
    status: str = "success", previous: tuple | None = None,
) -> dict:
    return {
        "project": "train" if workflow == "one" else "heldout",
        "workflow": workflow,
        "class": "python_inline",
        "start_ts_ms": start,
        "terminal_ts_ms": start + duration,
        "duration_ms": duration,
        "status": status,
        "is_child": True,
        "previous": previous,
        "tool_call_id": str(index),
    }


def test_screen_only_uses_prior_successfully_finished_calls():
    calls = [_call(i, i * 4000, 3000 + i * 10) for i in range(4)]
    calls.append(_call(4, 16000, 3040))
    calls.append(_call(5, 21000, 3000, workflow="two"))
    result = screen(calls)
    assert result["completed_cold_child_long"] == 6
    assert result["supported_cold_child_long"] == 1
    assert result["selection"]["selected"] == 1
    assert result["selection"]["point_within_500ms"] == 1
    assert result["selection"]["lead_500_to_2000ms"] == 1
    assert result["selection"]["projects"] == ["train"]


def test_screen_excludes_inflight_failed_exact_repeats_but_counts_short_miss():
    calls = [_call(i, i * 4000, 3000) for i in range(3)]
    calls += [
        _call(3, 12000, 3100, status="failed"),
        _call(4, 15000, 3000),  # prior failed call cannot establish support
        _call(5, 22000, 3000, previous=(3000, 21000, "success")),
        _call(6, 27000, 1000),
    ]
    result = screen(calls)
    assert result["selection"]["selected"] == 1
    assert result["selection"]["false_long"] == 1
    assert result["selection"]["return_before_trigger"] == 1
    assert result["selection"]["point_within_500ms"] == 0
    assert result["completed_cold_child_long"] == 5
    with pytest.raises(ValueError):
        screen(calls, minimum_support=1)


def test_screen_does_not_use_same_timestamp_or_other_workflow_as_history():
    calls = [_call(i, i * 4000, 3000) for i in range(3)]
    calls.append(_call(3, 9000, 3000))  # Completes exactly at target start.
    calls.append(_call(4, 12000, 3010))
    calls.append(_call(5, 12500, 3000, workflow="two"))
    result = screen(calls)
    assert result["selection"]["selected"] == 0
    assert result["supported_cold_child_long"] == 0
