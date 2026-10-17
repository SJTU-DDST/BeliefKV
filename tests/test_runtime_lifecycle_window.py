import pytest

from scripts.audit_runtime_lifecycle_window import lifecycle_window


def state(timestamp, **counts):
    return {"event": "admission_runtime_state", "ts_ms": timestamp, "counts": counts}


def demotion(timestamp, source="join_wait", **units):
    return {
        "event": "parent_pressure_demoted", "ts_ms": timestamp,
        "source": source, "freed_units": units,
    }


def test_half_open_action_window_and_preceding_counter_boundaries():
    rows = [
        state(0, parent_pressure_demoted=1),
        demotion(9, **{"0": 8}),
        demotion(10, **{"0": 64, "2": 1}),
        state(11, parent_pressure_demoted=3),
        demotion(19, source="tool_wait", **{"0": 32}),
        state(19.5, parent_pressure_demoted=4),
        demotion(20, **{"0": 512}),
        state(21, parent_pressure_demoted=5),
    ]
    result = lifecycle_window(rows, 10, 20)
    assert result["recorded_waiting_demotion_count"] == 2
    assert result["recorded_waiting_demotion_sources"] == {"join_wait": 1, "tool_wait": 1}
    assert result["recorded_waiting_demotion_freed_component_units"] == {"0": 96, "2": 1}
    assert result["runtime_counter_delta"]["parent_pressure_demoted"] == 3
    assert [row["ts_ms"] for row in result["runtime_counter_boundary_rows"]] == [0, 19.5]


def test_covered_zero_demotion_is_distinct_from_missing_observation():
    result = lifecycle_window([state(0), state(21)], 10, 20)
    assert result["recorded_waiting_demotion_count"] == 0
    assert result["recorded_waiting_demotion_freed_component_units"] == {}
    for rows in ([], [state(11), state(21)], [state(0), state(19)]):
        with pytest.raises(ValueError, match="does not cover"):
            lifecycle_window(rows, 10, 20)


def test_counter_contract_and_out_of_order_writes():
    result = lifecycle_window([state(21, actions=4), state(9), state(19, actions=3)], 10, 20)
    assert result["runtime_counter_delta"] == {"actions": 3}
    assert result["runtime_state_observed_through_ts_ms"] == 21
    with pytest.raises(ValueError, match="reset"):
        lifecycle_window([state(0, actions=2), state(19, actions=1), state(21)], 10, 20)


@pytest.mark.parametrize("row", [
    {"event": "tool_wait_forecast"},
    {"event": "admission_runtime_state", "ts_ms": float("nan"), "counts": {}},
    state(15, counter=True),
    state(15, counter=-1),
    demotion(15, source="unknown", **{"0": 1}),
    demotion(15, **{"0": -1}),
    demotion(15, **{"0": 1.5}),
    demotion(15, **{"9": 1}),
])
def test_invalid_events_do_not_turn_into_zero_action_evidence(row):
    with pytest.raises(ValueError):
        lifecycle_window([state(0), row, state(21)], 10, 20)


@pytest.mark.parametrize("begin,end", [(20, 10), (10, 10), (True, 20), (10, float("inf"))])
def test_invalid_window(begin, end):
    with pytest.raises(ValueError, match="invalid lifecycle window"):
        lifecycle_window([], begin, end)
