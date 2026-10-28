from array import array
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_sibling_coadmission import group
from tests.test_reference_thunder_admission import full_runtime


def test_unrelated_retirement_retries_previously_capacity_blocked_group(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, capacity=1000, coadmit_join_children=True)
    try:
        group(live, waiting=False)
        first, peer, second = (
            request("first", "child-0", tokens=300, workflow="wf"),
            request("peer", "peer", tokens=300, workflow="other"),
            request("second", "child-1", tokens=300, workflow="wf"),
        )
        for req in (first, peer, second):
            live.register_visible_request(req)
        a = live._agent_admission
        live.on_events((observed(10, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        assert not a.allows(live.visible["second"])
        assert "join" in a._siblings.waiting
        # No event on the waiting group's members is needed.
        a.forget("peer")
        a.next_tick = float("inf")
        live.plan_native_prefill([first, second], running_batch=NS(reqs=[]), adder=None)
        assert a.allows(live.visible["second"])
        assert a.counts["sibling_release_retry_groups"] == 1
        for _ in range(20):
            live.plan_native_prefill([first, second], running_batch=NS(reqs=[]), adder=None)
        assert a.counts["sibling_release_retry_groups"] == 1
    finally:
        live.close()


def test_actual_native_completion_marks_release_and_retries_only_once(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, capacity=1000, coadmit_join_children=True)
    try:
        members = group(live)
        first = request("first", members[0], tokens=300, workflow="wf")
        peer = request("peer", "peer", tokens=300)
        second = request("second", members[1], tokens=300, workflow="wf")
        for req in (first, peer, second):
            live.register_visible_request(req)
        a = live._agent_admission
        assert not a.allows(live.visible["second"])
        # A reference pause at the completion boundary releases logical budget.
        a.policy.programs["peer"].marked_for_pause = True
        live.on_batch_completed(NS(reqs=[peer]))
        assert a._siblings.dirty
        a.next_tick = float("inf")
        live.plan_native_prefill([first, second], running_batch=NS(reqs=[]), adder=None)
        assert a.allows(live.visible["second"])
    finally:
        live.close()


@pytest.fixture
def sibling_native(full_native, monkeypatch, tmp_path):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, *_ = full_native
    for name, value in arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_COADMIT_JOIN_CHILDREN": "1",
        "EXECUTION_FRONTIER_RESTORE": "0",
    }, "full").items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "sibling-service.sock"))
    live.attach_native_cache(cache)
    group(live)
    native = []
    for index, identity in enumerate(("child-0", "peer", "child-1")):
        tokens = list(range(100 * (index + 1), 100 * (index + 1) + 8))
        session = f"sibling-{index}"
        node, generation = insert_session(cache, tokens, session)
        req = request(identity, identity, tokens=9, workflow="other" if identity == "peer" else "wf")
        req.origin_input_ids = array("q", (*tokens, 999))
        req.session_id, req.session_generation = session, generation
        req.extra_key = req.cache_salt = None
        req.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
        live.register_visible_request(req)
        native.append(req)
    live._agent_admission.next_tick = float("inf")
    live._native_prefill_order = True
    adder = NS(
        can_run_list=[], rem_input_tokens=16, rem_chunk_tokens=16,
        max_running_requests=24, max_prefill_bs=24, dllm_config=None, page_size=1,
    )
    yield live, cache, allocator, native, adder
    live.close()


@pytest.mark.parametrize("full_native", (512,), indirect=True)
@pytest.mark.parametrize("limit", ("fits", "rows", "input", "physical", "already_selected"))
def test_sibling_actual_selection_preserves_head_and_native_resource_limits(sibling_native, limit):
    live, cache, allocator, native, adder = sibling_native
    first, peer, second = native
    if limit == "rows":
        adder.max_running_requests = 1
    elif limit == "input":
        adder.rem_input_tokens = adder.rem_chunk_tokens = 1
    elif limit == "physical":
        assert allocator.alloc(allocator.available_size() - 1) is not None
    elif limit == "already_selected":
        adder.can_run_list = [peer]
        adder.max_running_requests = 2
    plan = live.plan_native_prefill(native, running_batch=NS(reqs=[]), adder=adder)
    actual = select_native_prefill_candidates(
        native, plan=plan, current_semantic_revision=live.semantic_revision,
    ).candidates
    assert actual == ((first, second, peer) if limit == "fits" else tuple(native))
    assert actual[0] is first
    assert cache.cache_controller.load_queue == []
    assert cache.cache_controller.write_queue == []
    assert live._agent_admission.policy.remaining_capacity() >= 0


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_co_selection_is_not_counted_as_service_until_native_forward_returns(sibling_native, monkeypatch):
    live, cache, allocator, native, adder = sibling_native
    first, peer, second = native
    a = live._agent_admission
    live.plan_native_prefill(native, running_batch=NS(reqs=[]), adder=adder)
    rows = []
    monkeypatch.setattr(live, "_opportunity_writer", NS(record=rows.append, close=lambda: None))
    for req in (first, second):
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        req.finished = lambda: False
    batch = NS(reqs=[first, second])
    live.on_batch_selected(batch)
    assert not a.counts["sibling_joint_native_service_batches"]
    live.on_batch_completed(batch)
    assert a.counts["sibling_joint_native_service_batches"] == 1
    evidence = [row for row in rows if row["event"] == "agent_join_siblings_native_service"]
    assert len(evidence) == 1
    assert set(evidence[0]["request_ids"]) == {"child-0", "child-1"}
    assert all(wait >= 0 for wait in evidence[0]["ready_wait_ms"])
    live.on_batch_selected(batch)
    live.on_batch_completed(batch)
    assert a.counts["sibling_joint_native_service_batches"] == 1


def test_bounded_release_retry_does_not_loop_over_failed_groups_each_prefill(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, coadmit_join_children=True)
    try:
        a = live._agent_admission
        for index in range(19):
            a._siblings.waiting[str(index)] = None
        checks = []
        monkeypatch.setattr(a, "_coadmit_join", checks.append)
        a._siblings.capacity_changed()
        for _ in range(10):
            a.prepare_prefill(running_batch=NS(reqs=[]), adder=None)
        assert checks == [str(index) for index in range(19)]
        assert not a._siblings.dirty
    finally:
        live.close()


def test_oversized_waiter_does_not_prevent_a_smaller_ready_sibling_fitting(monkeypatch, tmp_path):
    live = full_runtime(monkeypatch, tmp_path, capacity=1000, coadmit_join_children=True)
    try:
        group(live, size=3, waiting=False)
        for rid, identity, tokens in (
            ("first", "child-0", 300), ("peer", "peer", 1200),
            ("large", "child-1", 900), ("small", "child-2", 100),
        ):
            live.register_visible_request(request(rid, identity, tokens=tokens, workflow="wf"))
        a = live._agent_admission
        live.on_events((observed(20, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        assert a.allows(live.visible["small"])
        assert not a.allows(live.visible["large"])
        assert a.policy.remaining_capacity() >= 0
    finally:
        live.close()


@pytest.mark.parametrize("full_native", (512,), indirect=True)
@pytest.mark.parametrize("reason", ("aged_peer", "ready_parent", "cold_sibling", "tool_waiting_sibling"))
def test_joint_preference_does_not_cross_existing_fairness_dependency_or_locality(
    sibling_native, monkeypatch, reason,
):
    live, cache, allocator, native, adder = sibling_native
    first, peer, second = native
    if reason == "aged_peer":
        live._visible_since[peer.rid] = time.monotonic() - 11.
    elif reason == "ready_parent":
        original = live._ready_join_parent
        monkeypatch.setattr(live, "_ready_join_parent", lambda req, key: (
            req is peer or original(req, key)
        ))
    elif reason == "cold_sibling":
        node = next(iter(cache.components[0]._session_leaves[second.session_id]))
        node.component_data[0].host_value = node.component_data[0].value.clone()
        cache.tree_core._update_duplicate_tracking(node)
        cache._demote(node.id, {})
    else:
        live.on_events((observed(
            20, Kind.TOOL_START, invocation_id="child-1", context_id="child-1-context",
            attributes={"tool_call_id": "waiting"},
        ),))
    plan = live.plan_native_prefill(native, running_batch=NS(reqs=[]), adder=adder)
    actual = select_native_prefill_candidates(
        native, plan=plan, current_semantic_revision=live.semantic_revision,
    ).candidates
    assert actual == tuple(native)
    assert not live._agent_admission.counts["sibling_joint_batch_plans"]


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_native_requeue_discards_only_that_members_old_service_identity(sibling_native):
    live, cache, allocator, native, adder = sibling_native
    first, peer, second = native
    a = live._agent_admission
    for req in (first, second):
        a.admitted(req)
    old = live.visible[first.rid]
    peer_key = live.visible[second.rid]
    assert old in a._siblings.admitted_keys and peer_key in a._siblings.admitted_keys
    first.cache_request_handle.attempt_id += 1
    live.on_requests_requeued((first,), is_retracted=True)
    assert old not in a._siblings.admitted_keys
    assert peer_key in a._siblings.admitted_keys
    assert a.latest["child-0"].attempt_id == 1
    assert a.allows(live.visible[first.rid])
