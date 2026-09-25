from beliefkv.experiments.child_terminal_signal import summarize_child_terminal_signals


def join(child="last"):
    return {
        "workflow_id": "wf", "reentry_kind": "join",
        "terminal_status": "satisfied", "training_eligible": True,
        "reentry_ts_ms": 1000,
        "member_outcomes": [{"invocation_id": child, "return_ts_ms": 1000}],
    }


def ev(kind, ts, child="last", **attrs):
    return {
        "workflow_id": "wf", "invocation_id": child,
        "kind": kind, "ts_ms": ts, "attributes": attrs,
    }


def test_natural_completion_lead_and_nonterminal_false_positive():
    events = [
        ev("llm_result", 500, tool_call_count=0, output_chars=10,
           structured_action_names=[]),
        ev("llm_submit", 600),
        ev("llm_result", 800, tool_call_count=0, output_chars=10,
           structured_action_names=[]),
        ev("return", 1000),
    ]
    report = summarize_child_terminal_signals([join()], events)
    assert report["counts"]["natural_final_candidate"] == 2
    assert report["counts"]["natural_final_not_next_return"] == 1
    assert report["lead_distributions"]["natural_final_to_last_child_return"]["p50_ms"] == 200
    assert report["last_child_join_groups_with_signal"] == 1


def test_no_text_no_terminal_signal_and_child_completion():
    events = [
        ev("llm_result", 600, tool_call_count=0, output_chars=0,
           structured_action_names=[]),
        ev("llm_result", 700, tool_call_count=1, output_chars=0,
           structured_action_names=["ChildCompletion"]),
        ev("return", 1000),
    ]
    report = summarize_child_terminal_signals([join()], events)
    assert report["counts"]["explicit_child_completion_followed_by_return"] == 1
    assert report["counts"].get("natural_final_candidate", 0) == 0


def test_all_spawn_audit_includes_children_outside_eligible_joins():
    events = [
        {**ev("invocation_create", 100), "relation_type": "spawn"},
        {**ev("invocation_create", 100, child="cancelled"), "relation_type": "spawn"},
        ev("llm_result", 700, tool_call_count=0, output_chars=12,
           structured_action_names=[], finish_reason="stop"),
        ev("return", 1000),
        ev("llm_result", 720, child="cancelled", tool_call_count=0,
           output_chars=10, structured_action_names=[]),
        ev("invocation_cancel", 760, child="cancelled"),
        ev("llm_result", 800, child="cancelled", tool_call_count=0,
           output_chars=10, structured_action_names=[], finish_reason="length"),
    ]
    report = summarize_child_terminal_signals([join()], events)
    assert report["all_spawn_child_count"] == 2
    assert report["counts"]["all_spawn_natural_final_candidate"] == 2
    assert report["counts"]["all_spawn_natural_final_followed_by_return"] == 1
    assert report["counts"]["all_spawn_natural_final_not_next_return"] == 1
    assert report["counts"]["natural_final_candidate"] == 1
