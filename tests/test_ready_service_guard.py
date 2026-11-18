from dataclasses import replace
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime import ready_service_guard
from tests.test_global_workflow_oracle import queued
from tests.test_tool_predictive_transfers import event
from tests.test_workflow_size_admission import settings


def guarded(monkeypatch, tmp_path):
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, service_timeout_s=1800,
    )
    admission.forget("active")
    admission.next_tick = 0.
    return runtime, admission


def test_threshold_preserves_short_priority_then_allows_one_logical_exception(monkeypatch, tmp_path):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        now = time.monotonic()
        a.tick(now=now)
        assert {p.identity for p in a.policy.programs.values() if p.active} == {"short"}
        a.next_tick = 0.
        a.tick(now=now + 1799.999)
        assert a._ready_service_guard.target is None
        assert not a.counts["public_timeout_force_resumed"]
        a.next_tick = 0.
        a.tick(now=now + 1800.)
        assert a._ready_service_guard.target == a.latest["long"]
        assert a.policy.programs["long"].active
        assert a.counts["ready_service_guard_selected"] == 1
        assert a.counts["public_timeout_force_resumed"] == 1
        assert a.status()["ready_service_guard"]["physical_capacity_credit"] is False
        assert a.status()["workflow_public_timeout_force_resume"] is True
        assert a.status()["ready_service_guard"]["max_concurrent_exceptions"] == 1
        # The exception is explicitly allowed to overcommit logical eligibility.
        # Native page allocation, locks, and DMA remain authoritative.
        assert a.policy.remaining_capacity() < 0
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", (Kind.TOOL_START, Kind.JOIN_WAIT))
def test_external_blocking_drops_ready_clock_even_between_control_ticks(monkeypatch, tmp_path, kind):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        guard = a._ready_service_guard
        now = time.monotonic()
        guard.begin(now)
        guard.events((event(1., kind, invocation_id="long"),))
        guard.begin(now + 1800.)
        assert guard.target != a.latest["long"]
        assert guard.since["long"][1] == now + 1800.
    finally:
        runtime.close()


def test_still_blocked_and_invalid_requests_never_receive_timeout_rights(monkeypatch, tmp_path):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        guard = a._ready_service_guard
        now = time.monotonic()
        guard.begin(now)
        runtime.graph.invocations["long"].active_tool_calls["tool"] = "execute"
        guard.begin(now + 1800.)
        assert "long" not in guard.since
        assert not guard.allows_timeout(a.latest["long"])
        runtime.graph.invocations["long"].active_tool_calls.clear()
        stale = a.latest["long"]
        a.latest["long"] = replace(stale, context_epoch=stale.context_epoch + 1)
        guard.begin(now + 3600.)
        assert "long" not in guard.since
        assert not guard.allows_timeout(stale)
    finally:
        runtime.close()


def test_only_actual_service_resets_the_workflow_clock_and_releases_exception(monkeypatch, tmp_path):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        guard = a._ready_service_guard
        now = time.monotonic()
        guard.begin(now)
        guard.begin(now + 1800.)
        assert guard.target == a.latest["long"]
        a.policy.programs["long"].active = True
        # Logical admission leaves the clock and sticky exception intact.
        guard.begin(now + 1801.)
        assert guard.target == a.latest["long"]
        monkeypatch.setattr(ready_service_guard, "time", NS(monotonic=lambda: now + 1801.))
        a.native_service(NS(reqs=[a._live_requests["long-request"]]), ())
        assert guard.target is None
        assert guard.since["long"][1] == now + 1801.
        assert guard.since["short"][1] == now
        assert a.counts["ready_service_guard_served"] == 1
        a.forget("long")
        assert "long" not in guard.since
    finally:
        runtime.close()


@pytest.mark.parametrize("finished", (False, True))
def test_real_native_completion_order_clears_service_target(monkeypatch, tmp_path, finished):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        guard = a._ready_service_guard
        now = time.monotonic()
        guard.begin(now)
        key = a.latest["long"]
        request = a._live_requests[key.request_id]
        request.output_ids = list(range(1000 if finished else 1))
        request.finished = lambda: finished
        guard.target = key
        monkeypatch.setattr(ready_service_guard, "time", NS(monotonic=lambda: now + 1801.))

        completed = runtime.on_batch_completed(NS(reqs=[request]))

        assert completed == ((key.request_id,) if finished else ())
        assert guard.target is None
        assert guard.since["long"][1] == now + 1801.
        assert a.counts["ready_service_guard_served"] == 1
        if finished:
            assert key.request_id not in runtime.visible
            assert key.request_id not in a.requests
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "completion_not_named", "request_object", "context_epoch", "attempt",
    "session", "visibility", "latest_key",
))
def test_terminal_service_requires_exact_native_completion(monkeypatch, tmp_path, change):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        guard = a._ready_service_guard
        now = time.monotonic()
        guard.begin(now)
        key = a.latest["long"]
        request = a._live_requests[key.request_id]
        runtime.visible.pop(key.request_id)
        completed = (key.request_id,)
        if change == "completion_not_named":
            completed = ()
        elif change == "request_object":
            request = NS(**vars(request))
        elif change == "context_epoch":
            request.beliefkv_metadata["context_epoch"] += 1
        elif change == "attempt":
            request.cache_request_handle.attempt_id += 1
        elif change == "session":
            request.session_id, request.session_generation = "replacement", 1
        elif change == "visibility":
            runtime.visible[key.request_id] = replace(key, context_epoch=key.context_epoch + 1)
        elif change == "latest_key":
            a.latest[key.invocation_id] = replace(key, attempt_id=key.attempt_id + 1)
        guard.target = key
        monkeypatch.setattr(ready_service_guard, "time", NS(monotonic=lambda: now + 1801.))

        guard.serviced(NS(reqs=[request]), completed)

        assert guard.target == key
        assert guard.since["long"][1] == now
        assert not a.counts["ready_service_guard_served"]
    finally:
        runtime.close()


def test_same_workflow_sibling_service_resets_clock_but_stale_service_does_not(monkeypatch, tmp_path):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        guard = a._ready_service_guard
        now = time.monotonic()
        guard.begin(now)
        original = a.latest["long"]
        sibling = replace(a.latest["short"], root_workflow_id=original.root_workflow_id)
        a.requests["short-request"] = sibling, now
        monkeypatch.setattr(ready_service_guard, "time", NS(monotonic=lambda: now + 100.))
        guard.serviced(NS(reqs=[a._live_requests["short-request"]]))
        assert guard.since["long"][1] == now
        runtime.visible["short-request"] = sibling
        guard.serviced(NS(reqs=[a._live_requests["short-request"]]))
        assert guard.since["long"][1] == now + 100.
    finally:
        runtime.close()


@pytest.mark.parametrize("protected_head", (False, True))
def test_native_priority_preserves_critical_head_and_exact_identity(monkeypatch, tmp_path, protected_head):
    runtime, a = guarded(monkeypatch, tmp_path)
    try:
        guard = a._ready_service_guard
        guard.target = a.latest["long"]
        a.policy.programs["long"].active = True
        ordered = [(0, NS(rid="head")), (1, NS(rid="ordinary")), (2, a._live_requests["long-request"])]
        keys = [None, a.latest["short"], a.latest["long"]]
        a.order_ready(ordered, keys, now=time.monotonic(), protected_head=protected_head)
        assert ordered[int(protected_head)][1].rid == "long-request"
        if protected_head:
            assert ordered[0][1].rid == "head"
        a.requests["long-request"] = replace(a.latest["long"], attempt_id=1), 0.
        unchanged = list(reversed(ordered))
        a.order_ready(unchanged, keys, now=time.monotonic())
        assert unchanged == list(reversed(ordered))
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "native_matched", "scheduler_only"))
def test_guard_setting_is_explicit_and_isolated_from_other_modes(tmp_path, mode):
    common = dict(settings(tmp_path), BELIEFKV_AGENT_READY_SERVICE_TIMEOUT_S="1800")
    environment = arm_environment(common, mode)
    expected = "1800" if mode == "full" else "0"
    assert environment["BELIEFKV_AGENT_READY_SERVICE_TIMEOUT_S"] == expected
    with pytest.raises(ValueError, match="ready service timeout"):
        arm_environment(dict(common, BELIEFKV_AGENT_READY_SERVICE_TIMEOUT_S="30"), mode)
