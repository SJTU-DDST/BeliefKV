from __future__ import annotations

import json
from dataclasses import replace
import pytest

from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.sglang_v0520_sessions import (
    NativeRadixSessionLeases,
    close_native_radix_session,
)


def _metadata(**kwargs) -> BeliefKVRequestMetadata:
    values = dict(
        root_workflow_id="workflow",
        invocation_id="root",
        context_id="root-context",
        context_epoch=0,
        full_prompt_replay_guaranteed=True,
    )
    values.update(kwargs)
    return BeliefKVRequestMetadata(**values)


def test_session_survives_tool_wait_and_closes_once_at_context_terminal() -> None:
    closed: list[str] = []
    leases = NativeRadixSessionLeases(closed.append)
    root = _metadata()
    first = leases.for_request(root)
    assert first is not None and first.startswith("beliefkv-")
    assert leases.for_request(root) == first
    assert leases.for_request(replace(root, context_epoch=0)) == first
    assert closed == []
    child = leases.for_request(
        _metadata(invocation_id="child", context_id="child-context")
    )
    assert child is not None and child != first
    leases.retire("workflow", "child-context")
    leases.retire("workflow", "child-context")
    assert closed == [child]
    assert leases.for_request(root) == first
    leases.retire("workflow", "root-context")
    assert closed == [child, first]
    with pytest.raises(RuntimeError, match="terminal"):
        leases.for_request(root)


def test_compaction_closes_prior_epoch_before_reusing_context() -> None:
    closed: list[str] = []
    leases = NativeRadixSessionLeases(closed.append)
    root = _metadata()
    first = leases.for_request(root)
    second = leases.for_request(replace(root, context_epoch=1))
    assert closed == [first]
    assert second != first
    with pytest.raises(RuntimeError, match="regressed"):
        leases.for_request(root)
    leases.retire("workflow", "root-context")
    assert closed == [first, second]


def test_close_failure_never_forgets_reference_and_can_retry() -> None:
    closed: list[str] = []

    def close(session_id: str) -> None:
        if not closed:
            closed.append("failed")
            raise OSError("server unavailable")
        closed.append(session_id)

    leases = NativeRadixSessionLeases(close)
    root = _metadata()
    session_id = leases.for_request(root)
    with pytest.raises(OSError, match="unavailable"):
        leases.retire("workflow", "root-context")
    with pytest.raises(RuntimeError, match="terminal"):
        leases.for_request(root)
    leases.retire("workflow", "root-context")
    assert closed == ["failed", session_id]


def test_workflow_end_retires_orphaned_child_but_not_other_workflows() -> None:
    closed = []
    leases = NativeRadixSessionLeases(closed.append)
    root = leases.for_request(_metadata())
    child = leases.for_request(
        _metadata(invocation_id="child", context_id="child-context")
    )
    other = leases.for_request(_metadata(root_workflow_id="other"))
    leases.retire_workflow("workflow")
    leases.retire_workflow("workflow")
    assert set(closed) == {root, child}
    assert leases.for_request(_metadata(root_workflow_id="other")) == other
    assert other not in closed
    with pytest.raises(RuntimeError, match="terminal"):
        leases.for_request(_metadata(context_id="late-child"))


def test_unreplayable_prompt_does_not_create_native_session() -> None:
    leases = NativeRadixSessionLeases(lambda _session_id: None)
    assert leases.for_request(_metadata(full_prompt_replay_guaranteed=False)) is None


def test_http_close_uses_native_endpoint_and_checks_status(monkeypatch) -> None:
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def open_url(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return Response()

    monkeypatch.setattr(
        "beliefkv.runtime.sglang_v0520_sessions.urllib.request.urlopen", open_url
    )
    close_native_radix_session("http://localhost:30000/v1", "beliefkv-123")
    assert calls == [
        ("http://localhost:30000/close_session", {"session_id": "beliefkv-123"}, 2.0)
    ]
    with pytest.raises(ValueError, match="invalid native session close"):
        close_native_radix_session("http://localhost:30000", "foreign-session")
