import json
from types import SimpleNamespace

from beliefkv.experiments.deepagents_swebench import (
    JsonlAudit, _child_return_intent_shadow_tool,
)
from scripts.audit_child_return_intent_shadow import summarize


def _run(tmp_path, *, later_tool=False, blocked=False):
    root = tmp_path / "workflows" / "psf__requests-1142"
    root.mkdir(parents=True)
    events = [
        {"kind": "spawn", "target_invocation_id": "deepagents-invocation:child"},
        {
            "kind": "join_create", "join_id": "join",
            "member_invocation_ids": ["deepagents-invocation:child"],
        },
        {
            "kind": "llm_result", "invocation_id": "deepagents-invocation:child",
            "ts_ms": 1900, "attributes": {
                "request_id": "rid", "finish_reason": "stop",
                "output_chars": 12,
            },
        },
        {
            "kind": "return", "invocation_id": "deepagents-invocation:child",
            "ts_ms": 2000,
        },
        {"kind": "join_satisfied", "join_id": "join", "ts_ms": 2000},
    ]
    if later_tool:
        events.insert(2, {
            "kind": "tool_start", "invocation_id": "deepagents-invocation:child",
            "ts_ms": 1400, "attributes": {"tool_name": "read_file"},
        })
    (root / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8",
    )
    (root / "sandbox_audit.jsonl").write_text(
        json.dumps({
            "event": "child_return_intent_shadow",
            "invocation_id": "deepagents-invocation:child", "ts_ms": 1000,
        }) + "\n",
        encoding="utf-8",
    )
    if blocked:
        (root / "child_reports.json").write_text(json.dumps([{
            "invocation_id": "deepagents-invocation:child",
            "semantic_completion": {"status": "blocked", "unresolved": []},
        }]), encoding="utf-8")
    return summarize(root.parent)


def test_intent_tool_only_emits_identity_and_monotonic_timestamp(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = JsonlAudit(path)
    try:
        result = _child_return_intent_shadow_tool(
            audit, "deepagents-invocation:child",
        ).invoke({})
    finally:
        audit.close()
    assert "final report" in result
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["event"] == "child_return_intent_shadow"
    assert rows[0]["invocation_id"] == "deepagents-invocation:child"
    assert rows[0]["ts_ms"] > 0


def test_intent_tool_records_source_context_epoch_and_join(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = JsonlAudit(path)
    try:
        _child_return_intent_shadow_tool(
            audit, "deepagents-invocation:child",
            adapter=SimpleNamespace(latest_context_epoch=lambda invocation: 27),
            context_id="context:child", join_id="join:parent",
        ).invoke({})
    finally:
        audit.close()
    row = json.loads(path.read_text().strip())
    assert row["context_id"] == "context:child"
    assert row["context_epoch"] == 27
    assert row["join_id"] == "join:parent"


def test_natural_join_intent_has_one_second_lead(tmp_path):
    report = _run(tmp_path)
    assert report["natural_child_returns"] == 1
    assert report["valid_natural_intents"] == 1
    assert report["join_last_intents"] == 1
    assert report["return_lead_ms"]["p50_ms"] == 1000
    assert report["intents_in_500_to_3000ms"] == 1
    assert report["window_precision_conservative"] == 1.
    assert report["window_recall_of_natural_returns"] == 1.


def test_later_work_or_blocked_final_never_counts_as_valid_intent(tmp_path):
    invalidated = _run(tmp_path / "later", later_tool=True)
    assert invalidated["intent_invalidated_by_later_tool"] == 1
    assert invalidated["natural_returns_without_valid_intent"] == 1
    assert invalidated["join_last_intents"] == 0
    assert invalidated["window_precision_conservative"] == 0.
    blocked = _run(tmp_path / "blocked", blocked=True)
    assert blocked["natural_child_returns"] == 0
    assert blocked["intent_nonterminal_or_blocked"] == 1
    assert blocked["valid_natural_intents"] == 0
