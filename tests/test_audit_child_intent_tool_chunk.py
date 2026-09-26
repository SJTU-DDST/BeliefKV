import json

from scripts.audit_child_intent_tool_chunk import audit


def _workflow(tmp_path, *, other_rid=False, two_tools=False, wrong_epoch=False):
    root = tmp_path / "workflows" / "psf__requests-1724"
    root.mkdir(parents=True)
    base = {
        "invocation_id": "deepagents-invocation:child",
        "context_id": "context:child", "context_epoch": 2,
    }
    events = [
        {"kind": "llm_submit", "ts_ms": 100,
         "attributes": {"request_id": "rid"}, **base},
        {"kind": "structured_action", "ts_ms": 250,
         "attributes": {
             "request_id": "other" if other_rid else "rid",
             "beliefkv_child_first_tool_chunk_shadow": True,
         }, **{**base, "context_epoch": 3 if wrong_epoch else 2}},
        {"kind": "llm_result", "ts_ms": 400,
         "attributes": {
             "request_id": "rid", "finish_reason": "tool_calls",
             "tool_call_count": 2 if two_tools else 1,
         }, **base},
        {"kind": "tool_start", "ts_ms": 500,
         "attributes": {"tool_name": "announce_completion_intent"}, **base},
    ]
    (root / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
    )
    return audit(root.parent)


def test_single_tool_round_bounds_advance_without_predicting_online(tmp_path):
    report = _workflow(tmp_path)
    assert report["counts"]["matched_single_tool_rounds"] == 1
    assert report["first_chunk_to_intent_tool_start_ms"]["p50"] == 250
    assert report["first_chunk_to_intent_tool_start_ms"]["at_least_500ms"] == 0


def test_ambiguous_or_stale_chunks_are_not_counted(tmp_path):
    for suffix, settings, reason in (
        ("other", {"other_rid": True}, "missing_or_ambiguous_chunk"),
        ("multiple", {"two_tools": True}, "ambiguous_result"),
        ("epoch", {"wrong_epoch": True}, "invalid_identity_or_order"),
    ):
        report = _workflow(tmp_path / suffix, **settings)
        assert report["counts"][reason] == 1
        assert report["first_chunk_to_intent_tool_start_ms"] is None
