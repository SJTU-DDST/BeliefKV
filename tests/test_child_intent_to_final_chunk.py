import json

from scripts.audit_child_intent_to_final_chunk import audit


def _run(
    tmp_path, *, false_first=False, wrong_epoch=False, skipped_epoch=False,
    old_context=False, wrong_rid=False, extra_submit=False, later_tool=False,
    wrong_notice_identity=False,
):
    workflow = tmp_path / "workflows" / "sphinx-doc__sphinx-7748"
    workflow.mkdir(parents=True)
    child = "deepagents-invocation:child"
    base = {"invocation_id": child, "context_id": "context:child",
            "context_epoch": 2}
    successor = {**base, "context_epoch": 4 if skipped_epoch else 3}
    signal = {
        **successor,
        "context_epoch": 2 if wrong_epoch else successor["context_epoch"],
        "context_id": "context:old" if old_context else successor["context_id"],
    }
    events = [
        {"kind": "invocation_create", "ts_ms": 0, **base},
        {"kind": "spawn", "target_invocation_id": child, "ts_ms": 1},
        {"kind": "join_create", "join_id": "join",
         "member_invocation_ids": [child], "ts_ms": 2},
        {"kind": "llm_result", "ts_ms": 980,
         "attributes": {"request_id": "notify", "finish_reason": "tool_calls",
                        "output_chars": 5, "tool_call_count": 1}, **base},
        {"kind": "tool_start", "ts_ms": 998,
         "attributes": {"tool_name": "announce_completion_intent"}, **base},
        {"kind": "tool_end", "ts_ms": 1002,
         "attributes": {"tool_name": "announce_completion_intent"}, **base},
        {"kind": "llm_submit", "ts_ms": 1100,
         "attributes": {"request_id": "not-final" if false_first else "final"},
         **successor},
    ]
    if extra_submit:
        events.append({"kind": "llm_submit", "ts_ms": 1200,
                       "attributes": {"request_id": "extra"}, **successor})
    if false_first:
        events.extend([
            {"kind": "structured_action", "ts_ms": 1250,
             "attributes": {"request_id": "not-final",
                            "beliefkv_child_final_chunk_shadow": True}, **signal},
            {"kind": "llm_result", "ts_ms": 1260,
             "attributes": {"request_id": "not-final",
                            "stream_final_chunk_ts_ms": 1250,
                            "finish_reason": "stop", "output_chars": 10}, **successor},
            {"kind": "llm_submit", "ts_ms": 1320,
             "attributes": {"request_id": "final"},
             **{**successor, "context_epoch": successor["context_epoch"] + 1}},
        ])
    if later_tool:
        events.append({"kind": "tool_start", "ts_ms": 1300,
                       "attributes": {"tool_name": "read_file"}, **base})
    events.extend([
        {"kind": "structured_action", "ts_ms": 1400,
         "attributes": {"request_id": "unexpected" if wrong_rid else "final",
                        "beliefkv_child_final_chunk_shadow": True},
         **({**successor, "context_epoch": successor["context_epoch"] + 1}
            if false_first else signal)},
        {"kind": "llm_result", "ts_ms": 1420,
         "attributes": {"request_id": "unexpected" if wrong_rid else "final",
                        "stream_final_chunk_ts_ms": 1400,
                        "finish_reason": "stop", "output_chars": 15},
         **({**successor, "context_epoch": successor["context_epoch"] + 1}
            if false_first else successor)},
        {"kind": "return", "ts_ms": 1550,
         **({**successor, "context_epoch": successor["context_epoch"] + 1}
            if false_first else successor)},
        {"kind": "join_satisfied", "join_id": "join", "ts_ms": 1550},
    ])
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events),
    )
    notice_identity = {**base, "context_epoch": 1} if wrong_notice_identity else base
    (workflow / "sandbox_audit.jsonl").write_text(json.dumps({
        "event": "child_return_intent_shadow",
        "invocation_id": child, "ts_ms": 1000, "join_id": "join",
        "context_id": notice_identity["context_id"],
        "context_epoch": notice_identity["context_epoch"],
    }) + "\n")
    return audit(workflow.parent)


def test_first_notice_and_first_chunk_confirm_natural_join(tmp_path):
    result = _run(tmp_path)
    assert result["counts"]["valid_natural_notices"] == 1
    assert result["counts"]["first_chunk_confirmed_return"] == 1
    assert result["notice_to_natural_return"]["p50_ms"] == 550
    assert result["first_chunk_to_natural_return"]["p50_ms"] == 150
    assert result["join_last_first_chunk_to_return"]["count"] == 1
    assert result["counts"]["first_chunk_zero_eta_within_500ms"] == 1


def test_early_nonterminal_chunk_is_not_replaced_with_later_success(tmp_path):
    result = _run(tmp_path, false_first=True)
    assert result["counts"]["valid_natural_notices"] == 1
    assert result["counts"]["first_chunk_not_natural_terminal"] == 1
    assert result["first_chunk_to_natural_return"] is None


def test_stale_identity_and_later_tool_revoke_confirmation(tmp_path):
    stale = _run(tmp_path / "stale", wrong_epoch=True)
    assert stale["counts"]["first_chunk_successor_identity_mismatch"] == 1
    assert stale["first_chunk_to_natural_return"] is None
    revoked = _run(tmp_path / "revoked", later_tool=True)
    assert revoked["counts"]["notice_revoked_by_later_tool"] == 1
    assert revoked["counts"].get("first_chunk_confirmed_return", 0) == 0


def test_first_chunk_must_follow_unique_submit_with_matching_identity(tmp_path):
    for name, options in (
        ("skipped", {"skipped_epoch": True}),
        ("context", {"old_context": True}),
        ("rid", {"wrong_rid": True}),
        ("extra", {"extra_submit": True}),
    ):
        result = _run(tmp_path / name, **options)
        assert result["counts"]["first_chunk_successor_identity_mismatch"] == 1
        assert result["first_chunk_to_natural_return"] is None


def test_notice_must_bind_source_context_and_epoch(tmp_path):
    result = _run(tmp_path, wrong_notice_identity=True)
    assert result["counts"]["notice_source_identity_mismatch"] == 1
    assert result["counts"].get("valid_natural_notices", 0) == 0
