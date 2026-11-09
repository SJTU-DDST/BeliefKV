from scripts.audit_idle_tool_restore_opportunities import fit_window, tool_successors


def event(kind, when, **attrs):
    return {
        "kind": kind, "ts_ms": when, "invocation_id": "a", "workflow_id": "w",
        "context_id": "c", "context_epoch": 1, "attributes": attrs,
    }


def test_tool_successors_use_the_last_return_and_do_not_merge_request_rounds():
    events = [
        event("tool_start", 0, tool_run_id="one"), event("tool_start", 10, tool_run_id="two"),
        event("tool_end", 50, tool_run_id="one"), event("tool_end", 100, tool_run_id="two"),
        event("llm_submit", 110, request_id="r"), event("llm_submit", 200, request_id="next"),
        event("tool_start", 210, tool_run_id="unfinished"),
        event("llm_submit", 220, request_id="background"),
    ]
    rows = tool_successors(events)
    assert len(rows) == 1
    assert rows[0]["tool_return_ms"] == 100
    assert rows[0]["request_id"] == "r"


def test_join_rounds_remain_separate_from_pure_tool_successors():
    events = [
        event("tool_start", 0, tool_run_id="t"), event("join_wait", 1),
        event("tool_end", 2, tool_run_id="t"), event("llm_submit", 3, request_id="r"),
    ]
    assert tool_successors(events)[0]["has_join"] is True


def test_screen_requires_actual_pause_no_ready_peer_and_fresh_sufficient_free_pages():
    target = {
        "invocation_id": "a", "host_tokens": 100,
        "tool_return_ms": 1000., "tool_intervals_ms": [(0., 1000.), (100., 1000.)],
    }
    states = [
        {"monotonic_ms": when, "ts_ms": when + 10000,
         "agent_admission": {"paused_invocation_ids": ["a"] if when != 400 else []}}
        for when in (0, 200, 400, 600, 800, 1000)
    ]
    aligned = {
        row["monotonic_ms"]: {"ready_paused": int(row["monotonic_ms"] == 200)}
        for row in states
    }
    pools = [
        {"ts_ms": 10000., "device_pools": {"full": {"observable": True, "free_units": 2300}}},
        {"ts_ms": 10600., "device_pools": {"full": {"observable": True, "free_units": 100}}},
    ]
    result = fit_window(target, states, aligned, pools)
    assert result["paused_during_tool_observed"]
    assert result["free_page_and_no_ready_paused_window_ms"] == 200.
    assert result["qualifying_observations"] == 1
