"""A foreground caller is cold only for its actual live blocking dependency."""

from dataclasses import replace

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import ExecutionMode, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, req
from tests.test_waiting_full_tail_backup import native_cache, waiting_tail


def fixture():
    runtime = NativeAdmissionRuntime()
    runtime.enable_prepare_host = True
    request = req("parent")
    request.session_id, request.session_generation = "caller-session", 1
    runtime.register_visible_request(request)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE, invocation_id="parent",
              context_id="ctx-parent", agent_definition_id="root", agent_instance_id="parent"),
        event(2, RuntimeEventKind.INVOCATION_CREATE, invocation_id="child",
              context_id="ctx-child", agent_definition_id="summary", agent_instance_id="child"),
        event(3, RuntimeEventKind.CALL, invocation_id="parent", target_invocation_id="child",
              execution_mode=ExecutionMode.FOREGROUND, attributes={"runtime_internal": True}),
    ))
    return runtime, runtime.context_sessions["ctx-parent"]


def test_observed_internal_CALL_authorizes_existing_cold_checks_without_starting_backup():
    runtime, key = fixture()
    try:
        assert runtime.graph.invocations["parent"].state is InvocationState.WAIT_CHILD
        assert runtime._live_parent_pressure_key(key)
        assert runtime._live_cold_full_owners(((key.session_id, key.session_generation),))
        assert not runtime._spawn_full_prepares
        assert not runtime.physical_ledger.pending_count
        assert not runtime._join_ticket
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", (RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL))
def test_actual_child_completion_revokes_caller_donor_authority_immediately(kind):
    runtime, key = fixture()
    try:
        assert runtime._live_parent_pressure_key(key)
        runtime.on_events((event(4, kind, invocation_id="child"),))
        assert not runtime.graph.invocations["parent"].blocking_child_ids
        assert not runtime._live_parent_pressure_key(key)
        assert not runtime._live_cold_full_owners(((key.session_id, key.session_generation),))
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "empty_blockers", "missing_child", "wrong_parent", "foreign_workflow",
    "background_child", "terminal_child", "unlinked_child",
    "parent_ready", "parent_running", "parent_done", "workflow_ended",
    "context_advanced", "session_rebound", "prepare_disabled", "physical_disabled",
))
def test_stale_nonblocking_or_unrelated_CALL_never_authorizes_reclaim(change):
    runtime, key = fixture()
    parent, child = (runtime.graph.invocations[name] for name in ("parent", "child"))
    try:
        if change == "empty_blockers":
            parent.blocking_child_ids.clear()
        elif change == "missing_child":
            del runtime.graph.invocations["child"]
        elif change == "wrong_parent":
            child.parent_invocation_id = "unrelated"
        elif change == "foreign_workflow":
            child.workflow_id = "unrelated"
        elif change == "background_child":
            child.execution_mode = ExecutionMode.BACKGROUND
        elif change == "terminal_child":
            child.state = InvocationState.DONE
        elif change == "unlinked_child":
            parent.child_invocation_ids.clear()
        elif change.startswith("parent_"):
            parent.state = {
                "parent_ready": InvocationState.READY,
                "parent_running": InvocationState.RUNNING_LLM,
                "parent_done": InvocationState.DONE,
            }[change]
        elif change == "workflow_ended":
            runtime.graph.workflows[key.root_workflow_id].end_ts_ms = 10.
        elif change == "context_advanced":
            runtime.graph.contexts[key.context_id].epoch += 1
        elif change == "session_rebound":
            runtime.context_sessions[key.context_id] = replace(key, session_generation=2)
        elif change == "prepare_disabled":
            runtime.enable_prepare_host = False
        else:
            runtime.physical_disabled = True
        assert not runtime._live_parent_pressure_key(key)
        assert not runtime._live_cold_full_owners(((key.session_id, key.session_generation),))
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_one_ready_owner_still_protects_shared_FULL_pages():
    runtime, key = fixture()
    other = replace(key, request_id="other-request", invocation_id="other",
                    context_id="ctx-other", session_id="other-session", session_generation=2)
    runtime.on_events((event(
        4, RuntimeEventKind.INVOCATION_CREATE, invocation_id="other",
        context_id="ctx-other", agent_definition_id="other", agent_instance_id="other",
    ),))
    runtime.context_sessions[other.context_id] = other
    runtime._context_session_changed(other.context_id)
    owners = ((key.session_id, key.session_generation), (other.session_id, 2))
    try:
        assert runtime._live_parent_pressure_key(key)
        assert not runtime._live_cold_full_owners(owners)
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_early_next_request_binding_cannot_inherit_the_previous_CALL_wait():
    runtime, key = fixture()
    next_key = replace(key, request_id="next-request", context_epoch=key.context_epoch + 1)
    runtime.context_sessions[key.context_id] = next_key
    runtime._context_session_changed(key.context_id)
    try:
        assert not runtime._live_parent_pressure_key(next_key)
        assert not runtime._live_cold_full_owners(((key.session_id, key.session_generation),))
    finally:
        runtime.close()


@pytest.mark.parametrize("guard", ("backed", "unbacked", "locked", "inflight", "returned"))
def test_real_native_reclaim_keeps_CALL_checkpoint_and_all_physical_guards(waiting_tail, guard):
    runtime, hint, cache, allocator, _, checkpoint, leaf = waiting_tail
    runtime.enable_tool_prefetch = False
    runtime.on_events((
        event(3, RuntimeEventKind.TOOL_END, invocation_id="tool",
              attributes={"tool_run_id": "long"}),
        event(4, RuntimeEventKind.INVOCATION_CREATE, invocation_id="child",
              context_id="ctx-child", agent_definition_id="summary", agent_instance_id="child"),
        event(5, RuntimeEventKind.CALL, invocation_id="tool", target_invocation_id="child",
              execution_mode=ExecutionMode.FOREGROUND, attributes={"runtime_internal": True}),
    ))
    if guard != "unbacked":
        leaf.component_data[0].host_value = leaf.component_data[0].value.clone()
        cache.tree_core._update_duplicate_tracking(leaf)
    if guard == "locked":
        leaf.component_data[0].lock_ref = 1
    elif guard == "inflight":
        leaf.load_back_pending_id = 123
    elif guard == "returned":
        runtime.on_events((event(6, RuntimeEventKind.RETURN, invocation_id="child"),))
    before = allocator.available_size()
    anchors = cache.session_refs.snapshot_latest_session_leaf_anchors(
        "s", hint.key.session_generation,
    )
    result = cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=before + 34, mamba_slots=0, max_nodes=4,
    )
    assert result.get(0, 0) == (34 if guard == "backed" else 0)
    assert allocator.available_size() == before + result.get(0, 0)
    assert cache.session_refs.snapshot_latest_session_leaf_anchors(
        "s", hint.key.session_generation,
    ) == anchors
    assert checkpoint.component_data[0].host_value is not None
    assert (leaf.component_data[0].value is None) is (guard == "backed")
    cache._execute_and_commit_kv_backup.assert_not_called()
