from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import full_runtime


def setup(monkeypatch, tmp_path, *, enabled=True, capacity=800):
    live = full_runtime(
        monkeypatch, tmp_path, capacity=capacity, free_continuation_admission=enabled,
    )
    live.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context"),
        observed(2, Kind.INVOCATION_CREATE, invocation_id="peer", context_id="peer-context"),
    ))
    first = request("a-first", "a", tokens=200, workflow="wf")
    live.register_visible_request(first)
    live.on_batch_completed(NS(reqs=[first]))
    second = request("a-second", "a", tokens=200, workflow="wf")
    live.register_visible_request(second)
    live.on_batch_completed(NS(reqs=[second]))
    adapter = live._agent_admission
    adapter.policy._pause(adapter.policy.programs["a"], time.monotonic())
    peer = request("peer", "peer", tokens=400, workflow="wf")
    peer.output_ids.clear()
    live.register_visible_request(peer)
    adapter.next_tick = 0.
    adapter.tick(now=time.monotonic())
    adapter.next_tick = float("inf")
    assert adapter.policy.programs["peer"].active
    assert not adapter.policy.programs["a"].active
    following = request("a-next", "a", tokens=200, workflow="wf")
    live.register_visible_request(following)
    return live, adapter, peer, following


@pytest.mark.parametrize("enabled", (False, True))
def test_actual_registration_uses_free_complete_budget_without_waiting_for_tick(
    monkeypatch, tmp_path, enabled,
):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, enabled=enabled)
    try:
        assert adapter.policy.programs["a"].active is enabled
        assert adapter.policy.remaining_capacity() == (0 if enabled else 300)
        assert adapter.next_tick == float("inf")
        if enabled:
            assert type(adapter) is ReferenceFreeContinuationAdmission
            validate_reference_admission(adapter.status(), free_continuation_admission=True)
            with pytest.raises(ValueError):
                validate_reference_admission(adapter.status())
    finally:
        live.close()


def test_planned_release_is_not_capacity_but_actual_completion_can_admit_continuation(
    monkeypatch, tmp_path,
):
    live, adapter, peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        assert not adapter.policy.programs["a"].active
        adapter.policy.programs["peer"].marked_for_pause = True
        assert not adapter._admit_free_continuation()
        assert adapter.policy.remaining_capacity() == 200
        live.on_batch_completed(NS(reqs=[peer]))
        assert adapter.policy.programs["a"].active
        assert not adapter.policy.programs["peer"].active
        assert adapter.policy.remaining_capacity() == 400
        assert adapter.counts["free_continuation_admitted"] == 1
    finally:
        live.close()


def test_empty_decode_callbacks_do_not_scan_or_grant(monkeypatch, tmp_path):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        adapter.forget("peer")
        count = adapter.counts["free_continuation_event_checks"]
        adapter.completed(NS(reqs=[]), ())
        adapter.events(())
        assert adapter.counts["free_continuation_event_checks"] == count
        assert not adapter.policy.programs["a"].active
        adapter.events((observed(
            10, Kind.LLM_SUBMIT, invocation_id="a", context_id="a-context",
            attributes={"request_id": "a-next"},
        ),))
        assert adapter.policy.programs["a"].active
    finally:
        live.close()


def test_grant_refreshes_live_growth_and_never_pauses_a_peer(monkeypatch, tmp_path):
    live, adapter, peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        adapter.policy.capacity_tokens = 800
        before = deepcopy(adapter.policy.programs["peer"])
        peer.output_ids.extend([1] * 10)
        assert not adapter._admit_free_continuation()
        assert adapter.policy.programs["peer"].active == before.active
        assert adapter.policy.programs["peer"].tokens == 410
        assert adapter.policy.remaining_capacity() == 290
        assert not adapter.policy.programs["a"].active
    finally:
        live.close()


@pytest.mark.parametrize("change", (
    "marked", "new_program", "stale_latest", "stale_visible", "stale_epoch",
    "blocked_tool", "terminal",
))
def test_free_capacity_cannot_override_identity_lifecycle_or_native_request_evidence(
    monkeypatch, tmp_path, change,
):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        adapter.forget("peer")
        key = adapter.latest["a"]
        if change == "marked":
            adapter.policy.programs["a"].marked_for_pause = True
        elif change == "new_program":
            adapter.policy.programs["a"].steps = 1
        elif change == "stale_latest":
            adapter.latest["a"] = replace(key, context_epoch=key.context_epoch + 1)
        elif change == "stale_visible":
            live.visible.pop("a-next")
        elif change == "stale_epoch":
            live.graph.contexts["a-context"].epoch += 1
        elif change == "blocked_tool":
            live.graph.invocations["a"].active_tool_calls["tool"] = "execute"
        else:
            live.graph.invocations["a"].state = InvocationState.DONE
        assert not adapter._admit_free_continuation()
        assert not adapter.policy.programs["a"].active
    finally:
        live.close()


def test_successful_grant_is_journaled_once_and_does_not_issue_a_physical_transfer(
    monkeypatch, tmp_path,
):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        adapter.forget("peer")
        records = []
        with monkeypatch.context() as patch:
            patch.setattr(live, "_opportunity_writer", NS(record=records.append))
            assert adapter._admit_free_continuation()
            assert not adapter._admit_free_continuation()
        assert len(records) == 1
        assert records[0]["event"] == "agent_continuation_free_capacity_admitted"
        assert records[0]["required_tokens"] == 300
        assert records[0]["remaining_after_tokens"] == 400
        assert records[0]["programs"][0]["key"]["request_id"] == "a-next"
        assert records[0]["native_capacity_authority_preserved"]
        assert adapter.status()["free_continuation_physical_prefetch"] is False
    finally:
        live.close()


@pytest.mark.parametrize("mode", ("native_matched", "scheduler_only", "kv_only"))
def test_controls_strip_the_new_full_only_candidate(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1",
    }, mode)
    assert settings["BELIEFKV_AGENT_FREE_CONTINUATION_ADMISSION"] == "0"


def test_known_ready_join_parent_precedes_an_older_ordinary_continuation(
    monkeypatch, tmp_path,
):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        adapter.forget("peer")
        live.on_events((observed(
            11, Kind.INVOCATION_CREATE, invocation_id="b", context_id="b-context",
        ),))
        with monkeypatch.context() as patch:
            patch.setattr(adapter, "_admit_free_continuation", lambda **kwargs: False)
            live.register_visible_request(request("b-next", "b", tokens=200, workflow="wf"))
        adapter.policy.programs["b"].steps = 2
        live._join_priority_pending["b"] = "join"
        monkeypatch.setattr(live, "_ready_join_parent", lambda req, key: key.invocation_id == "b")
        assert adapter._admit_free_continuation()
        assert adapter.policy.programs["b"].active
        assert not adapter.policy.programs["a"].active
        assert adapter.counts["free_join_continuation_admitted"] == 1
        assert adapter.policy.remaining_capacity() == 400
    finally:
        live.close()


def test_too_many_unexamined_join_parents_defer_ordinary_extra_admission(
    monkeypatch, tmp_path,
):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        adapter.forget("peer")
        live._join_priority_pending = {str(index): "join" for index in range(25)}
        assert not adapter._admit_free_continuation()
        assert not adapter.policy.programs["a"].active
        assert adapter.counts["free_continuation_join_window_exceeded"] == 1
    finally:
        live.close()


def test_newly_submitted_candidate_is_checked_within_the_bounded_ordinary_window(
    monkeypatch, tmp_path,
):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        checked = []
        monkeypatch.setattr(adapter, "_candidate", lambda rid, key: checked.append(rid))
        for index in range(30):
            adapter.requests[f"waiting-{index}"] = NS(invocation_id=str(index)), 0.
        preferred = NS(request_id="submitted-now")
        assert not adapter._admit_free_continuation(preferred=preferred)
        assert checked[0] == "submitted-now"
        assert len(checked) == 8
    finally:
        adapter.requests.clear()
        live.close()


def test_free_ordinary_budget_does_not_grant_speculative_tool_warming(
    monkeypatch, tmp_path,
):
    live, adapter, _peer, _following = setup(monkeypatch, tmp_path, capacity=700)
    try:
        adapter.forget("peer")
        assert not adapter.claim_restore(adapter.latest["a"], source="tool_wait")
        assert not adapter.policy.programs["a"].active
    finally:
        live.close()
