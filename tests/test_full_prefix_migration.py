from array import array
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import (
    EventConfidence, ExecutionMode, RelationType, RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import request
from tests.test_paper_prefix_transfers import insert


@pytest.fixture
def live(monkeypatch, tmp_path, full_native):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained", "TOOL_RESTORE_MODE": "execution",
        "SESSIONLESS_PREFIX_MIGRATION": "1", "PREFILL_SCHEDULING_POLICY": "join_priority",
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "p.sock"))
    cache, allocator, writes, loads, commits = full_native
    cache.load_back_threshold = 1
    runtime.attach_native_cache(cache)
    # The component pool has only 64 slots; scale the paper's serving buffer.
    runtime._agent_admission.policy.buffer_tokens = 1
    clock = NS(now=10., sequence=0)
    actor = runtime._full_prefix_migration
    actor.clock = lambda: clock.now
    monkeypatch.setattr("beliefkv.runtime.agent_working_set.time.monotonic", lambda: clock.now)

    def acknowledged(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = acknowledged
    state = NS(
        runtime=runtime, actor=actor, cache=cache, allocator=allocator,
        writes=writes, loads=loads, commits=commits, clock=clock,
    )
    emit(state, Kind.WORKFLOW_START)
    yield state
    runtime.close()


def emit(live, kind, **kwargs):
    live.clock.sequence += 1
    event = RuntimeEvent(
        "full-prefix:" + str(live.clock.sequence), live.clock.now * 1000.,
        kind, "workflow", **kwargs,
    )
    live.runtime.on_events((event,))
    return event


def create(live, identity, *, parent=None):
    emit(
        live, Kind.INVOCATION_CREATE, invocation_id=identity, context_id=identity,
        parent_invocation_id=parent,
        relation_type=RelationType.SPAWN if parent else RelationType.ROOT,
        execution_mode=ExecutionMode.BACKGROUND if parent else ExecutionMode.FOREGROUND,
    )


def serve(live, identity, tokens, *, normal_eos=False):
    if identity not in live.runtime.graph.invocations:
        create(live, identity)
    rid = identity + ":" + str(live.clock.sequence)
    emit(
        live, Kind.LLM_SUBMIT, invocation_id=identity, context_id=identity,
        context_epoch=0, attributes={"request_id": rid},
    )
    req = request(rid, tokens, context=identity)
    req.output_ids = array("q", [99])
    req.finished_reason = NS(to_json=lambda: {"type": "stop", "matched": 99} if normal_eos else {
        "type": "length", "length": 1,
    })
    req.tokenizer = NS(decode=lambda *_, **__: "completed answer")
    live.runtime._native_plain_output_template = True
    live.runtime.register_visible_request(req)
    live.runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    insert(live.cache, tokens)
    assert live.runtime.on_batch_completed(NS(reqs=[req])) == (rid,)
    emit(
        live, Kind.LLM_RESULT, invocation_id=identity, context_id=identity,
        attributes={"request_id": rid},
    )
    return req


def spawn(live, *, child="child"):
    create(live, child, parent="parent")
    emit(
        live, Kind.SPAWN, invocation_id="parent", context_id="parent",
        target_invocation_id=child, execution_mode=ExecutionMode.BACKGROUND,
    )


def wait_join(live, *, inferred=False):
    if "child" not in live.runtime.graph.invocations:
        create(live, "child", parent="parent")
    confidence = EventConfidence.INFERRED if inferred else EventConfidence.OBSERVED_EXACT
    emit(
        live, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",),
        confidence=confidence,
    )
    emit(
        live, Kind.JOIN_WAIT, invocation_id="parent", context_id="parent",
        join_id="join", confidence=confidence,
    )


def offload_waiting_parent(live):
    serve(live, "parent", list(range(1, 9)))
    wait_join(live)
    live.actor.dispatch()
    assert live.writes
    live.cache.writing_check(finish_count=1)
    live.actor.dispatch()
    context = live.actor.contexts["parent"]
    assert live.actor.transfers.status(context.snapshot, owner=context.owner) == "cpu_backup"


def test_background_spawn_backs_plain_parent_without_session_or_device_release(live):
    serve(live, "parent", list(range(1, 9)))
    spawn(live)
    before = live.allocator.available_size()
    live.runtime.dispatch_after_forward()
    assert [len(op.device_indices) for op in live.writes] == [8]
    assert live.allocator.available_size() == before
    assert live.runtime.graph.invocations["parent"].state.value == "ready"
    assert not live.runtime.context_sessions
    assert not live.cache.session_refs._session_generations
    assert not live.runtime.physical_ledger.pending_count
    assert live.actor.transfers.receipts.pending_count == 1
    live.cache.writing_check(finish_count=1)
    assert live.runtime.counts["full_prefix_native_commits_completed"] == 1
    live.actor.dispatch()
    assert live.allocator.available_size() == before
    assert live.runtime.physical_disabled is False


def test_spawn_backup_transfers_only_the_new_suffix_after_parent_continues(live):
    serve(live, "parent", list(range(1, 9)))
    spawn(live)
    live.actor.dispatch()
    live.cache.writing_check(finish_count=1)
    serve(live, "parent", list(range(1, 13)))
    spawn(live, child="other-child")
    live.actor.dispatch()
    assert [len(op.device_indices) for op in live.writes] == [8, 4]
    live.cache.writing_check(finish_count=1)
    assert live.actor.counts["d2h_enqueued_tokens"] == 12


def test_extent_larger_than_backup_budget_is_transferred_in_incremental_batches(live):
    serve(live, "parent", list(range(1, 9)))
    spawn(live)
    unit = live.cache.host_pool_group.anchor_entry.host_pool.size_per_token
    live.actor.spawn_batch_bytes = 2 * unit
    before = live.allocator.available_size()
    for _ in range(4):
        live.actor.dispatch()
        live.cache.writing_check(finish_count=1)
    assert [len(op.device_indices) for op in live.writes] == [2, 2, 2, 2]
    assert live.allocator.available_size() == before
    assert live.actor.counts["split_prefix_memberships"] >= 3
    assert live.actor.transfers.receipts.pending_count == 0


def test_tool_wait_cannot_demote_until_elapsed_threshold_and_actual_backup_ack(live):
    serve(live, "parent", list(range(1, 9)))
    emit(
        live, Kind.TOOL_START, invocation_id="parent", context_id="parent",
        attributes={"tool_run_id": "tool", "tool_family": "unknown"},
    )
    before = live.allocator.available_size()
    live.clock.now += .99
    live.actor.dispatch()
    assert not live.writes
    live.clock.now += .21
    live.actor.dispatch()
    assert len(live.writes) == 1
    assert live.allocator.available_size() == before
    live.actor.next_tick = 0.
    live.actor.dispatch()
    assert live.allocator.available_size() == before
    live.cache.writing_check(finish_count=1)
    live.actor.dispatch()
    assert live.allocator.available_size() == before + 8


def test_ready_child_sharing_parent_prefix_blocks_proactive_demotion(live):
    serve(live, "parent", list(range(1, 9)))
    create(live, "child", parent="parent")
    child = request("child-pending", list(range(1, 10)), context="child")
    live.runtime.register_visible_request(child)
    emit(live, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",))
    emit(
        live, Kind.JOIN_WAIT, invocation_id="parent", context_id="parent", join_id="join",
    )
    before = live.allocator.available_size()
    live.actor.dispatch()
    live.cache.writing_check(finish_count=1)
    live.actor.dispatch()
    assert live.allocator.available_size() == before
    assert live.actor.counts["demoted_tokens"] == 0
    live.runtime.on_abort_request(NS(rid="child-pending", abort_all=False))
    live.actor.next_tick = 0.
    live.actor.dispatch()
    assert live.allocator.available_size() == before + 8


def test_confirmed_real_join_restores_parent_and_native_admission_consumes_same_agent_budget(live):
    offload_waiting_parent(live)
    serve(live, "child", [30, 31, 32, 33])
    emit(live, Kind.RETURN, invocation_id="child", context_id="child")
    assert live.actor.restores["parent"][2] == "confirmed_join"
    before = live.allocator.available_size()
    live.runtime.dispatch_after_forward()
    assert [len(op.host_indices) for op in live.loads] == [8]
    assert live.allocator.available_size() == before - 8
    live.cache.loading_check(finish_count=1)
    assert live.runtime.physical_disabled is False
    successor = request("parent-next", list(range(1, 11)), context="parent")
    live.runtime.register_visible_request(successor)
    assert live.runtime._agent_admission.allows(live.runtime.visible[successor.rid])
    live.runtime.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
    assert live.runtime._agent_admission.policy.restore_owner is None
    assert not live.actor.restores


@pytest.mark.parametrize("normal_eos", (True, False))
def test_only_actual_native_eos_can_restore_before_child_return(live, normal_eos):
    offload_waiting_parent(live)
    serve(live, "child", [30, 31, 32, 33], normal_eos=normal_eos)
    assert not live.runtime.graph.joins["join"].satisfied
    assert bool(live.actor.restores) is normal_eos
    if normal_eos:
        assert live.actor.restores["parent"][2] == "observed_eos"
        live.actor.dispatch()
        assert len(live.loads) == 1
        live.cache.loading_check(finish_count=1)
        live.actor.dispatch()
        context = live.actor.contexts["parent"]
        assert live.actor.transfers.status(context.snapshot, owner=context.owner) == "gpu"
    else:
        live.actor.dispatch()
        assert not live.loads


def test_inferred_join_does_not_authorize_backup_offload_or_restore(live):
    serve(live, "parent", list(range(1, 9)))
    wait_join(live, inferred=True)
    before = live.allocator.available_size()
    live.actor.dispatch()
    emit(
        live, Kind.JOIN_SATISFIED, join_id="join", confidence=EventConfidence.INFERRED,
    )
    live.actor.dispatch()
    assert not live.writes and not live.loads and not live.actor.restores
    assert live.allocator.available_size() == before
    assert live.runtime.graph.invocations["parent"].state.value == "ready"


def test_eos_of_one_parallel_child_waits_for_the_actual_remaining_dependency(live):
    serve(live, "parent", list(range(1, 9)))
    for child in ("first", "second"):
        create(live, child, parent="parent")
    emit(
        live, Kind.JOIN_CREATE, join_id="parallel", member_invocation_ids=("first", "second"),
    )
    emit(
        live, Kind.JOIN_WAIT, invocation_id="parent", context_id="parent", join_id="parallel",
    )
    live.actor.dispatch()
    live.cache.writing_check(finish_count=1)
    live.actor.dispatch()
    serve(live, "first", [30, 31, 32, 33], normal_eos=True)
    assert not live.actor.restores
    emit(live, Kind.RETURN, invocation_id="first", context_id="first")
    serve(live, "second", [40, 41, 42, 43], normal_eos=True)
    assert live.actor.restores["parent"][2] == "observed_eos"
    assert not live.runtime.graph.joins["parallel"].satisfied


def test_ordinary_queued_request_gets_no_restore_ticket(live):
    serve(live, "parent", list(range(1, 9)))
    successor = request("parent-next", list(range(1, 10)), context="parent")
    live.runtime.register_visible_request(successor)
    live.actor.dispatch()
    assert not live.actor.restores
    assert not live.loads
    assert live.runtime._agent_admission.policy.restore_owner is None


def test_observed_idle_owner_lends_only_after_grace_when_real_waiter_cannot_progress(live):
    serve(live, "parent", list(range(1, 57)))
    create(live, "child", parent="parent")
    child = request("waiting-child", list(range(100, 116)), context="child")
    child.finished = lambda: False
    live.runtime.register_visible_request(child)
    policy = live.runtime._agent_admission.policy
    assert not policy.allowed("child")
    live.clock.now += .9
    live.actor.dispatch()
    assert policy.agents["parent"].active and not policy.allowed("child")
    live.clock.now += .3
    live.actor.dispatch()
    assert policy.allowed("child")
    assert policy.agents["parent"].blocked == "observed_idle"
    assert live.runtime.graph.invocations["parent"].state.value == "ready"
    assert not live.runtime.graph.joins
    assert live.actor.counts["eligibility_lent_observed_idle"] == 1
    assert not live.actor.restores


def test_idle_owner_keeps_eligibility_while_other_eligible_compute_can_progress(live):
    serve(live, "parent", list(range(1, 25)))
    create(live, "active")
    active = request("active", list(range(100, 124)), context="active")
    active.finished = lambda: False
    live.runtime.register_visible_request(active)
    create(live, "waiting")
    waiting = request("waiting", list(range(200, 224)), context="waiting")
    waiting.finished = lambda: False
    live.runtime.register_visible_request(waiting)
    policy = live.runtime._agent_admission.policy
    assert policy.allowed("active") and not policy.allowed("waiting")
    live.clock.now += 2.
    live.actor.dispatch()
    assert policy.agents["parent"].active
    assert live.actor.counts["eligibility_lent_observed_idle"] == 0
    assert not live.writes


def test_native_reserve_includes_selected_unallocated_chunk_without_future_output_length(live):
    offload_waiting_parent(live)
    serve(live, "child", [30, 31, 32, 33])
    emit(live, Kind.RETURN, invocation_id="child", context_id="child")
    create(live, "large")
    free = live.allocator.available_size()
    req = request("large-pending", list(range(100, 100 + free - 4)), context="large")
    req.output_ids = array("q")
    req.finished = lambda: False
    live.runtime.register_visible_request(req)
    live.runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    req.sampling_params = NS(max_new_tokens=10**9)
    assert live.actor._native_reserve() == len(req.origin_input_ids) + 1
    live.actor.dispatch()
    assert not live.loads
    assert live.actor.counts["restore_native_capacity_deferred"] == 1
    assert live.runtime._agent_admission.policy.restore_owner is None


def test_epoch_advance_and_workflow_end_release_metadata_without_closing_unknown_context(live):
    serve(live, "parent", list(range(1, 9)))
    spawn(live)
    emit(
        live, Kind.CONTEXT_ADVANCE, invocation_id="parent", context_id="parent", context_epoch=1,
    )
    assert not live.actor.contexts and not live.actor.delegators
    before = live.allocator.available_size()
    emit(live, Kind.WORKFLOW_END)
    assert live.allocator.available_size() == before
    assert not live.actor._paths and not live.actor._members


def test_native_requeue_preserves_one_prefix_owner_and_updates_attempt(live):
    create(live, "parent")
    req = request("parent-retracted", list(range(1, 9)), context="parent")
    req.finished = lambda: False
    live.runtime.register_visible_request(req)
    live.runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    old = live.runtime.visible[req.rid]
    req.cache_request_handle.attempt_id = 1
    live.runtime.on_requests_requeued([req], is_retracted=True)
    assert live.actor.requests[req.rid] == replace(old, attempt_id=1)
    assert live.actor.contexts["parent"].key == live.runtime.visible[req.rid]
    assert len(live.actor.contexts) == len(live.actor.requests) == 1
    assert req.rid not in live.actor.admitted_requests
    assert not live.actor.restores


def test_native_source_namespace_change_cannot_back_the_previous_context_prefix(live):
    serve(live, "parent", list(range(1, 9)))
    changed = request("new-namespace", list(range(1, 10)), context="parent")
    changed.extra_key = changed.cache_salt = "different"
    live.runtime.register_visible_request(changed)
    spawn(live)
    live.actor.dispatch()
    assert live.actor.contexts["parent"].snapshot is None
    assert not live.writes and not live.loads


def test_full_prefix_ack_failure_revokes_physical_actions(live):
    from beliefkv.runtime.sglang_v0520_physical import PhysicalReceiptError

    serve(live, "parent", list(range(1, 9)))
    spawn(live)
    live.actor.dispatch()
    callback = live.actor.transfers.transfer_completed
    live.actor.transfers.transfer_completed = lambda _: (_ for _ in ()).throw(
        PhysicalReceiptError("bad native byte receipt")
    )
    live.runtime.on_native_transfer_commit(NS(direction="d2h", status="completed"))
    assert live.runtime.physical_disabled is True
    before = len(live.writes)
    live.actor.dispatch()
    assert len(live.writes) == before
    live.actor.transfers.transfer_completed = callback


def test_returned_unknown_child_retains_cache_without_becoming_permanently_protected(live):
    serve(live, "parent", list(range(1, 9)))
    create(live, "child", parent="parent")
    serve(live, "child", list(range(1, 9)))
    emit(
        live, Kind.RETURN, invocation_id="child", context_id="child",
        attributes={"context_lifetime": "unknown", "context_retired": False},
    )
    before = live.allocator.available_size()
    wait_join(live)
    # The child is already done, so this JOIN cannot authorize parent offload.
    live.actor.dispatch()
    assert live.allocator.available_size() == before
    assert not live.writes


def test_maintenance_has_fixed_owner_scan_and_no_decode_step_rescan(live):
    serve(live, "parent", list(range(1, 9)))
    for _ in range(100):
        live.actor.dispatch()
    assert live.actor.counts["maintenance_rounds"] == 1
    assert live.actor.counts["cold_owner_checks"] == 1
    live.clock.now += .2
    live.actor.dispatch()
    assert live.actor.counts["maintenance_rounds"] == 2


@pytest.mark.parametrize("mode", ("join_priority_only", "scheduler_only", "kv_only", "native_matched"))
def test_control_arms_cannot_inherit_sessionless_full_policy(mode):
    settings = arm_environment({"SESSIONLESS_PREFIX_MIGRATION": "1"}, mode)
    assert settings["BELIEFKV_SESSIONLESS_PREFIX_MIGRATION"] == "0"
