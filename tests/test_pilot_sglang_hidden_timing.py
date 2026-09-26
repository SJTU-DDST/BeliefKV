from scripts.pilot_sglang_hidden_timing import (
    _finish_summary,
    _hidden_shape,
    _observe_event,
)


def test_probe_reports_first_early_hidden_chunk_without_storing_values():
    state = {
        "response_id": None,
        "text_chunks": 0,
        "first_text_ms": None,
        "hidden_chunks": 0,
        "first_hidden_ms": None,
        "hidden_event_times_ms": [],
        "hidden_shapes": [],
        "first_finish_ms": None,
        "finish_reason": None,
    }
    _observe_event(state, {
        "id": "request",
        "choices": [{
            "delta": {
                "content": "not persisted",
                "hidden_states": [[.1, .2]],
            },
            "finish_reason": None,
        }],
    }, 100)
    _observe_event(state, {
        "id": "request",
        "choices": [{
            "delta": {"hidden_states": [[.3, .4]]},
            "finish_reason": "stop",
        }],
    }, 250)
    assert state["first_hidden_ms"] == 100
    assert state["first_finish_ms"] == 250
    assert state["hidden_chunks"] == 2
    assert state["hidden_event_times_ms"] == [100, 250]
    assert state["hidden_shapes"] == [[1, 2], [1, 2]]
    assert _hidden_shape([]) == [0]
    assert _finish_summary(state)["last_hidden_lead_ms"] == 150
    assert state["early_hidden_chunks"] == 1
    assert state["first_hidden_lead_ms"] == 150
