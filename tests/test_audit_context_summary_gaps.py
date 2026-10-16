import json

import pytest

from scripts.audit_context_summary_gaps import (
    analyze_workflow, audit, interval_union_ms, summarize,
)


def event(kind, when, *, invocation="child", **changes):
    return {
        "kind": kind, "ts_ms": when, "sequence": int(when * 10),
        "workflow_id": "wf", "invocation_id": invocation, **changes,
    }


def submit(when, request, *, invocation="child", internal=False):
    return event(
        "llm_submit", when, invocation=invocation,
        attributes={"request_id": request, "runtime_internal": internal},
    )


def tool(kind, when, name):
    return event(
        kind, when, attributes={"tool_run_id": name, "tool_name": "execute"},
    )


def summary(when, end, name="s1", *, parent="child", output=100, finish="stop"):
    rows = [
        event(
            "invocation_create", when - 1, invocation=name,
            agent_definition_id="context-summarizer", parent_invocation_id=parent,
        ),
        submit(when, name, invocation=name, internal=True),
    ]
    if end is not None:
        rows.append(event(
            "llm_result", end, invocation=name, attributes={
                "request_id": name, "runtime_internal": True,
                "output_chars": output, "finish_reason": finish,
            },
        ))
    return rows


def analyze(rows):
    return analyze_workflow(rows, workflow_path="instance")


def test_parallel_tool_round_uses_last_completion_and_summary_union_not_sum():
    result = analyze([
        submit(0, "initial"), tool("tool_start", 1, "a"),
        tool("tool_start", 2, "b"), tool("tool_end", 5, "a"),
        tool("tool_end", 10, "b"), submit(30, "next"),
        *summary(12, 24), *summary(20, 28, "s2"),
    ])
    row, = result["gap_rows"]
    assert row["status"] == "measured"
    assert row["completion_ts_ms"] == 10
    assert row["gap_ms"] == 20
    assert row["summary_span_overlap_ms"] == 16
    assert row["outside_summary_request_spans_ms"] == 4
    assert row["next_request_id"] == "next"


def test_other_parent_summary_and_unrelated_late_summary_do_not_explain_gap():
    result = analyze([
        submit(0, "initial"), tool("tool_start", 1, "a"), tool("tool_end", 10, "a"),
        submit(20, "next"), *summary(11, 19, parent="sibling"),
        *summary(30, 40, "later"),
    ])
    row, = result["gap_rows"]
    assert row["summary_request_count"] == 0
    assert row["outside_summary_request_spans_ms"] == 10


def test_empty_length_retry_and_compaction_are_preserved():
    result = analyze([
        submit(0, "initial"), tool("tool_start", 1, "a"), tool("tool_end", 10, "a"),
        *summary(11, 50, output=0, finish="length"), *summary(51, 80, "s2"),
        event("context_compact", 82, attributes={"source_message_count": 150}),
        submit(85, "next"),
    ])
    row, = result["gap_rows"]
    assert row["empty_length_summary_requests"] == 1
    assert row["summary_request_count"] == 2
    assert row["summary_span_overlap_ms"] == 68
    assert row["context_compactions_in_gap"] == [
        {"ts_ms": 82, "attributes": {"source_message_count": 150}},
    ]
    assert row["completion_to_first_summary_submit_ms"] == 1
    assert row["between_summary_request_spans_ms"] == 1
    assert row["last_summary_result_to_compaction_ms"] == 2
    assert row["compaction_to_next_submit_ms"] == 3
    stats = summarize(result["summary_rows"], result["gap_rows"])
    assert stats["empty_length_summary_requests"] == 1
    assert stats["summary_requests"] == 2


def test_missing_result_keeps_overlap_unknown_and_known_lower_bound():
    result = analyze([
        submit(0, "initial"), tool("tool_start", 1, "a"), tool("tool_end", 10, "a"),
        *summary(11, 15), *summary(16, None, "open"), submit(20, "next"),
    ])
    row, = result["gap_rows"]
    assert row["summary_span_overlap_ms"] is None
    assert row["outside_summary_request_spans_ms"] is None
    assert row["summary_span_overlap_lower_bound_ms"] == 4
    assert row["summary_requests_with_unknown_span"] == 1


@pytest.mark.parametrize("missing", ("tool_end", "llm_submit"))
def test_unclosed_tool_or_next_submit_is_retained_as_censored(missing):
    rows = [submit(0, "initial"), tool("tool_start", 1, "a")]
    if missing != "tool_end":
        rows.append(tool("tool_end", 10, "a"))
    if missing != "llm_submit":
        rows.append(submit(20, "next"))
    row, = analyze(rows)["gap_rows"]
    assert row["status"] == (
        "unproven_completion" if missing == "tool_end" else "no_next_ordinary_submit"
    )
    assert "gap_ms" not in row


def test_join_matches_declared_parent_and_allows_same_timestamp_submission():
    rows = [
        event("join_create", 0, invocation=None, join_id="j"),
        event("join_wait", 0, join_id="j"),
        event("join_satisfied", 10, invocation=None, join_id="j"),
        submit(10, "next"),
        submit(11, "sibling-request", invocation="sibling"),
    ]
    row, = analyze(rows)["gap_rows"]
    assert row["status"] == "measured"
    assert row["invocation_id"] == "child"
    assert row["next_request_id"] == "next"
    assert row["gap_ms"] == 0


def test_parallel_tool_and_join_do_not_turn_child_waiting_into_client_idle():
    result = analyze([
        submit(0, "initial"), tool("tool_start", 1, "a"), tool("tool_end", 10, "a"),
        event("join_create", 2, invocation=None, join_id="j"),
        event("join_wait", 2, join_id="j"),
        event("join_satisfied", 90, invocation=None, join_id="j"),
        submit(100, "next"),
    ])
    tool_row, join_row = result["gap_rows"]
    assert tool_row["blocking_join_overlap_ms"] == 80
    assert tool_row["outside_summary_and_join_spans_ms"] == 10
    assert join_row["gap_ms"] == 10


def test_unknown_join_parent_is_not_inferred_from_another_invocation():
    row, = analyze([
        event("join_satisfied", 10, invocation=None, join_id="missing"),
        submit(11, "next"),
    ])["gap_rows"]
    assert row["status"] == "unproven_join_parent"


def test_duplicate_request_identity_fails_instead_of_overwriting():
    with pytest.raises(ValueError, match="duplicate llm_submit"):
        analyze([submit(0, "same"), submit(10, "same")])


def test_summary_created_without_request_is_retained_and_not_called_zero_work():
    result = analyze([
        submit(0, "initial"), tool("tool_start", 1, "a"), tool("tool_end", 10, "a"),
        event(
            "invocation_create", 11, invocation="s1",
            agent_definition_id="context-summarizer", parent_invocation_id="child",
        ), submit(20, "next"),
    ])
    summary_row, = result["summary_rows"]
    gap, = result["gap_rows"]
    assert summary_row["status"] == "missing_submit"
    assert gap["summary_span_overlap_ms"] is None


def test_reversed_summary_result_is_not_counted_as_negative_or_complete_work():
    result = analyze([
        submit(0, "initial"), tool("tool_start", 1, "a"), tool("tool_end", 10, "a"),
        *summary(15, 14), submit(20, "next"),
    ])
    assert result["summary_rows"][0]["status"] == "invalid_result_identity_or_order"
    assert result["gap_rows"][0]["summary_span_overlap_ms"] is None


@pytest.mark.parametrize("intervals,expected", (
    ([(0, 15), (14, 50)], 20), ([(0, 5), (40, 50)], 0),
    ([(12, 15), (15, 17), (13, 14)], 5), ([], 0),
))
def test_interval_intersections_are_bounded_and_do_not_double_count(intervals, expected):
    assert interval_union_ms(intervals, 10, 30) == expected


def test_file_binding_covers_full_raw_bytes_and_rejects_invalid_clock(tmp_path):
    path = tmp_path / "workflows/instance/runtime_events.deepagents.jsonl"
    path.parent.mkdir(parents=True)
    payload = json.dumps(submit(0, "first")) + "\n"
    path.write_text(payload)
    result = audit(tmp_path)
    assert result["workflow_sources"] == 1
    assert result["inputs"][0]["bytes"] == len(payload.encode())
    assert result["inputs"][0]["events"] == 1
    path.write_text(json.dumps({**submit(0, "bad"), "ts_ms": float("inf")}))
    with pytest.raises(ValueError, match="invalid timestamp"):
        audit(tmp_path)
