"""Use one explicit client event source for native captures and causal replays."""

import json

import pytest

from scripts import audit_join_reentry_service as joins
from scripts import audit_tool_reentry_service as tools
from tests import test_audit_join_reentry_service as join_fixture
from tests import test_audit_tool_reentry_service as tool_fixture


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.mark.parametrize("kind", ["join", "tool"])
@pytest.mark.parametrize("source", ["native", "replay"])
def test_reentry_audits_read_the_complete_selected_client_source(tmp_path, kind, source):
    fixture = join_fixture if kind == "join" else tool_fixture
    auditor = joins if kind == "join" else tools
    relative = (
        "client_1/workflows/wf/runtime_events.deepagents.jsonl"
        if source == "native" else "replay/runtime_events.replay.jsonl"
    )
    client = tmp_path / relative
    write_jsonl(client, fixture.client_events())
    write_jsonl(tmp_path / "server/runtime_events.sglang.jsonl", fixture.native_events())
    write_jsonl(tmp_path / "server/runtime_audit.jsonl", fixture.services())
    report = auditor.audit_arm(tmp_path)
    assert report["summary"]["first_service_observed_requests"] == 1
    assert report["summary"]["native_queue_to_service_ms"]["p50"] == 40.
    assert report["first_workflow_start_monotonic_ms"] == 100.
    assert str(client) in report["input_jsonl_prefixes"]
    assert len(report["input_jsonl_prefixes"]) == 3


@pytest.mark.parametrize("auditor", [joins, tools], ids=["join", "tool"])
def test_reentry_audits_reject_mixed_native_and_replay_sources(tmp_path, auditor):
    write_jsonl(
        tmp_path / "client_1/workflows/wf/runtime_events.deepagents.jsonl", [],
    )
    write_jsonl(tmp_path / "replay/runtime_events.replay.jsonl", [])
    with pytest.raises(ValueError, match="ambiguous"):
        auditor.audit_arm(tmp_path)
