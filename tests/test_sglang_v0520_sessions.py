from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
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
    assert leases.for_request(replace(root, context_epoch=1)) == first
    assert leases.for_request(replace(root, context_epoch=2)) == first
    with pytest.raises(RuntimeError, match="regressed"):
        leases.for_request(root)
    assert closed == []
    child = leases.for_request(
        _metadata(invocation_id="child", context_id="child-context")
    )
    assert child is not None and child != first
    leases.retire("workflow", "child-context")
    leases.retire("workflow", "child-context")
    assert closed == [child]
    assert leases.for_request(replace(root, context_epoch=2)) == first
    leases.retire("workflow", "root-context")
    assert closed == [child, first]
    with pytest.raises(RuntimeError, match="terminal"):
        leases.for_request(root)


def test_compaction_closes_prior_epoch_before_reusing_context() -> None:
    closed: list[str] = []
    leases = NativeRadixSessionLeases(closed.append)
    root = _metadata()
    first = leases.for_request(root)
    assert leases.for_request(replace(root, context_epoch=1)) == first
    leases.compact("workflow", "root-context", 2)
    assert closed == [first]
    second = leases.for_request(replace(root, context_epoch=2))
    assert second != first
    with pytest.raises(RuntimeError, match="regressed"):
        leases.for_request(root)
    leases.retire("workflow", "root-context")
    assert closed == [first, second]


def test_compaction_close_failure_preserves_old_reference_for_retry() -> None:
    closed = []

    def close(session_id: str) -> None:
        closed.append(session_id)
        if len(closed) == 1:
            raise OSError("close failed")

    leases = NativeRadixSessionLeases(close)
    root = _metadata()
    first = leases.for_request(root)
    with pytest.raises(OSError, match="close failed"):
        leases.compact("workflow", "root-context", 1)
    leases.compact("workflow", "root-context", 1)
    assert closed == [first, first]
    assert leases.for_request(replace(root, context_epoch=1)) != first


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


def test_terminal_session_close_reports_latency_not_physical_reclamation():
    records = []
    closed = []
    leases = NativeRadixSessionLeases(closed.append, lifecycle_observer=records.append)
    session = leases.for_request(_metadata())
    leases.retire("workflow", "root-context")
    assert closed == [session]
    assert [row["event"] for row in records] == [
        "native_session_retire_start", "native_session_retire_complete",
    ]
    assert records[-1]["close_elapsed_ms"] >= 0
    assert "not physical cache reclamation" in records[-1]["semantics"]
    assert "HTTP dispatch accepted" in records[-1]["semantics"]


def test_deferred_close_leaves_parent_requests_unblocked_and_deduplicates():
    started, release = threading.Event(), threading.Event()
    closed, records = [], []

    def close(session_id):
        closed.append(session_id)
        started.set()
        assert release.wait(2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        leases = NativeRadixSessionLeases(
            close, close_executor=executor, lifecycle_observer=records.append,
        )
        root = _metadata()
        parent = leases.for_request(root)
        child_metadata = _metadata(context_id="child-context")
        child = leases.for_request(child_metadata)
        try:
            leases.retire("workflow", "child-context")
            assert started.wait(1)
            leases.retire("workflow", "child-context")
            with ThreadPoolExecutor(max_workers=1) as request_executor:
                request = request_executor.submit(leases.for_request, root)
                assert request.result(timeout=1) == parent
            with pytest.raises(RuntimeError, match="terminal"):
                leases.for_request(child_metadata)
            assert closed == [child]
            assert not any(row["event"].endswith("complete") for row in records)
        finally:
            release.set()
        leases.drain()
    assert [row["event"] for row in records] == [
        "native_session_retire_queued", "native_session_retire_start",
        "native_session_retire_complete",
    ]
    completed = records[-1]
    assert completed["enqueue_to_close_start_ms"] >= 0
    assert completed["enqueue_to_http_complete_ms"] >= completed["close_elapsed_ms"]


def test_deferred_workflow_close_drains_remaining_contexts_and_preserves_other_workflows():
    closed = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        leases = NativeRadixSessionLeases(closed.append, close_executor=executor)
        root = leases.for_request(_metadata())
        child = leases.for_request(_metadata(context_id="child-context"))
        other_metadata = _metadata(root_workflow_id="other")
        other = leases.for_request(other_metadata)
        leases.retire("workflow", "child-context")
        leases.retire_workflow("workflow")
        leases.retire_workflow("workflow")
        leases.drain()
        leases.drain()
        assert sorted(closed) == sorted([root, child])
        assert leases.for_request(other_metadata) == other
        with pytest.raises(RuntimeError, match="terminal"):
            leases.for_request(_metadata(context_id="late-child"))


def test_deferred_close_failure_is_audited_and_retries_the_same_identity_at_drain():
    calls, records = [], []
    failed = threading.Event()

    def close(session_id):
        calls.append(session_id)
        if len(calls) == 1:
            failed.set()
            raise OSError("temporary failure")

    with ThreadPoolExecutor(max_workers=1) as executor:
        leases = NativeRadixSessionLeases(
            close, close_executor=executor, lifecycle_observer=records.append,
        )
        session_id = leases.for_request(_metadata())
        leases.retire("workflow", "root-context")
        assert failed.wait(1)
        leases.retire_workflow("workflow")
        leases.drain()
        assert calls == [session_id, session_id]
        assert [row["event"] for row in records].count("native_session_retire_failed") == 1
        assert records[-1]["event"] == "native_session_retire_complete"
        with pytest.raises(RuntimeError, match="terminal"):
            leases.for_request(_metadata())


def test_failed_deferred_drain_waits_for_other_dispatches_before_reporting_failure():
    other_started, release = threading.Event(), threading.Event()
    calls = []

    def close(session_id):
        calls.append(session_id)
        if session_id == failed_session:
            raise OSError("persistent failure")
        other_started.set()
        assert release.wait(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        leases = NativeRadixSessionLeases(close, close_executor=executor)
        failed_session = leases.for_request(_metadata())
        child = leases.for_request(_metadata(context_id="child-context"))
        leases.retire_workflow("workflow")
        assert other_started.wait(1)
        with ThreadPoolExecutor(max_workers=1) as drain_executor:
            future = drain_executor.submit(leases.drain)
            try:
                assert not future.done()
            finally:
                release.set()
            with pytest.raises(OSError, match="persistent failure"):
                future.result(timeout=1)
        assert calls.count(failed_session) == 2
        assert calls.count(child) == 1


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
        ("http://localhost:30000/close_session", {"session_id": "beliefkv-123"}, 30.0)
    ]
    with pytest.raises(ValueError, match="invalid native session close"):
        close_native_radix_session("http://localhost:30000", "foreign-session")
