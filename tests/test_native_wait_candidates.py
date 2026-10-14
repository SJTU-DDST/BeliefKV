"""Waiting membership follows graph events and native session rebinding."""

from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import CausalGraphError, InvocationState
from beliefkv.core.events import ExecutionMode, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, req


def waiting_runtime(*, bind=True):
    runtime = NativeAdmissionRuntime()
    requests = {}
    for name in ("z", "tool-z", "a", "tool-a", "child"):
        tagged = req(name)
        tagged.session_id, tagged.session_generation = f"session-{name}", 1
        requests[name] = tagged
        if bind:
            runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        *(event(index, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id=name, context_id=f"ctx-{name}")
          for index, name in enumerate(requests, start=1)),
        event(6, RuntimeEventKind.JOIN_CREATE,
              join_id="join", member_invocation_ids=("child",)),
        event(7, RuntimeEventKind.JOIN_WAIT, invocation_id="z", join_id="join"),
        event(8, RuntimeEventKind.JOIN_WAIT, invocation_id="a", join_id="join"),
        event(9, RuntimeEventKind.TOOL_START, invocation_id="tool-z"),
        event(10, RuntimeEventKind.TOOL_START, invocation_id="tool-a"),
    ))
    return runtime, requests


def test_wait_candidates_keep_prepare_order_and_tool_refresh_rotation():
    runtime, _ = waiting_runtime()
    joins = runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
    tools = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    assert [key.invocation_id for key in joins] == ["a", "z"]
    assert [key.invocation_id for key in tools] == ["tool-z", "tool-a"]
    assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) is joins
    assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) is tools
    runtime.on_events((
        event(11, RuntimeEventKind.TOOL_END, invocation_id="tool-z"),
        event(12, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
    ))
    assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) == ()
    assert [
        key.invocation_id for key in runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    ] == ["tool-a"]


def test_native_binding_invalidates_an_empty_wait_snapshot_without_graph_change():
    runtime, requests = waiting_runtime(bind=False)
    assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) == ()
    version = runtime.graph.graph_version
    runtime.register_visible_request(requests["tool-a"])
    assert runtime.graph.graph_version == version
    assert [
        key.invocation_id for key in runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    ] == ["tool-a"]


def test_requeued_session_generation_replaces_cached_wait_identity():
    runtime, requests = waiting_runtime()
    before = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    version = runtime.graph.graph_version
    requests["tool-a"].session_generation = 2
    runtime.on_requests_requeued((requests["tool-a"],), is_retracted=True)
    after = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    assert runtime.graph.graph_version == version
    assert [key.invocation_id for key in after] == ["tool-z", "tool-a"]
    assert before[-1].session_generation == 1
    assert after[-1].session_generation == 2


def test_abort_of_finished_waiting_request_removes_cached_session_without_graph_change():
    runtime, requests = waiting_runtime()
    runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    requests["tool-a"].finished = lambda: True
    runtime.on_batch_completed(NS(reqs=(requests["tool-a"],)))
    assert "tool-a" not in runtime.visible
    assert "ctx-tool-a" in runtime.context_sessions
    version = runtime.graph.graph_version
    runtime.on_abort_request(NS(rid="tool-a", abort_all=False))
    assert runtime.graph.graph_version == version
    assert [
        key.invocation_id for key in runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    ] == ["tool-z"]


def test_graph_failure_discards_cached_wait_candidates():
    runtime, _ = waiting_runtime()
    runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
    runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
    with pytest.raises(CausalGraphError):
        runtime.on_events((event(11, RuntimeEventKind.WORKFLOW_START),))
    assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) == ()
    assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) == ()


def test_join_parent_selection_reuses_sorted_live_waiter(monkeypatch):
    runtime, _ = waiting_runtime()
    checked = []
    original = runtime._terminal

    def terminal(key):
        checked.append(key)
        return original(key)

    monkeypatch.setattr(runtime, "_terminal", terminal)
    first = runtime._join_parent_key("join")
    assert first.invocation_id == "a"
    for _ in range(8):
        assert runtime._join_parent_key("join") is first
    assert checked == [first]


def test_join_parent_negative_cache_is_invalidated_by_native_binding():
    runtime, requests = waiting_runtime(bind=False)
    assert runtime._join_parent_key("join") is None
    version = runtime.graph.graph_version
    runtime.register_visible_request(requests["z"])
    assert runtime.graph.graph_version == version
    assert runtime._join_parent_key("join").invocation_id == "z"
    runtime.register_visible_request(requests["a"])
    assert runtime._join_parent_key("join").invocation_id == "a"


def test_join_parent_cache_follows_requeued_session_generation():
    runtime, requests = waiting_runtime()
    first = runtime._join_parent_key("join")
    version = runtime.graph.graph_version
    requests["a"].session_generation = 2
    runtime.on_requests_requeued((requests["a"],), is_retracted=True)
    second = runtime._join_parent_key("join")
    assert runtime.graph.graph_version == version
    assert first.session_generation == 1
    assert second.session_generation == 2


def test_join_parent_cache_follows_terminal_waiter_and_workflow_events():
    runtime, _ = waiting_runtime()
    assert runtime._join_parent_key("join").invocation_id == "a"
    runtime.on_events((event(11, RuntimeEventKind.RETURN, invocation_id="a"),))
    assert runtime._join_parent_key("join").invocation_id == "z"
    runtime.on_events((event(12, RuntimeEventKind.WORKFLOW_END),))
    assert runtime._join_parent_key("join") is None


def test_join_parent_cache_follows_abort_without_graph_change():
    runtime, _ = waiting_runtime()
    assert runtime._join_parent_key("join").invocation_id == "a"
    version = runtime.graph.graph_version
    runtime.on_abort_request(NS(rid="a", abort_all=False))
    assert runtime.graph.graph_version == version
    assert runtime._join_parent_key("join").invocation_id == "z"


def test_graph_failure_discards_cached_join_parent():
    runtime, _ = waiting_runtime()
    assert runtime._join_parent_key("join") is not None
    with pytest.raises(CausalGraphError):
        runtime.on_events((event(11, RuntimeEventKind.WORKFLOW_START),))
    assert runtime._join_parent_cache is None
    assert runtime._join_parent_key("join") is None


def test_join_parent_cache_follows_context_epoch_advance():
    runtime, _ = waiting_runtime()
    assert runtime._join_parent_key("join").invocation_id == "a"
    runtime.on_events((event(
        11, RuntimeEventKind.CONTEXT_ADVANCE, invocation_id="a",
        context_id="ctx-a", context_epoch=1,
    ),))
    assert runtime._join_parent_key("join").invocation_id == "z"


def test_unknown_join_projection_follows_join_create_and_wait():
    runtime, _ = waiting_runtime()
    assert runtime._join_parent_key("later") is None
    runtime.on_events((
        event(11, RuntimeEventKind.JOIN_CREATE,
              join_id="later", member_invocation_ids=("child",)),
        event(12, RuntimeEventKind.JOIN_WAIT, invocation_id="z", join_id="later"),
    ))
    assert runtime._join_parent_key("later").invocation_id == "z"


class CountedSessions(dict):
    def __init__(self, values):
        super().__init__(values)
        self.full_scans = 0

    def values(self):
        self.full_scans += 1
        return super().values()


def uncached_waiters(runtime, state):
    keys = tuple(
        key for key in dict.values(runtime.context_sessions)
        if runtime.graph.invocations[key.invocation_id].state is state
    )
    return tuple(sorted(keys, key=lambda key: key.context_id)) if (
        state is InvocationState.WAIT_JOIN
    ) else keys


def test_observation_does_not_rebuild_unchanged_waiting_groups():
    runtime, _ = waiting_runtime()
    runtime.context_sessions = CountedSessions(runtime.context_sessions)
    try:
        joins = runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
        tools = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        scans = runtime.context_sessions.full_scans
        runtime.on_events((event(
            11, RuntimeEventKind.TOOL_WAIT_OBSERVATION, invocation_id="tool-a",
        ),))
        assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) is joins
        assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) is tools
        assert runtime.context_sessions.full_scans == scans
    finally:
        runtime.close()


def test_unrelated_model_transitions_preserve_wait_groups_and_order():
    runtime, _ = waiting_runtime()
    runtime.context_sessions = CountedSessions(runtime.context_sessions)
    try:
        joins = runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
        tools = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        scans = runtime.context_sessions.full_scans
        for sequence, kind in (
            (11, RuntimeEventKind.LLM_SUBMIT),
            (12, RuntimeEventKind.LLM_RESULT),
        ):
            runtime.on_events((event(
                sequence, kind, invocation_id="child", context_id="ctx-child",
                context_epoch=0,
            ),))
            assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) is joins
            assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) is tools
            assert runtime.context_sessions.full_scans == scans
    finally:
        runtime.close()


def test_unrelated_native_binding_preserves_existing_wait_snapshots():
    runtime, requests = waiting_runtime()
    try:
        joins = runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
        tools = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        replacement = req("child")
        replacement.rid = "next-child"
        replacement.session_id = requests["child"].session_id
        replacement.session_generation = 1
        assert runtime.register_visible_request(replacement)
        assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) is joins
        assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) is tools
    finally:
        runtime.close()


def test_rebinding_invalidates_only_the_waiting_group_that_uses_it():
    runtime, requests = waiting_runtime()
    try:
        joins = runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
        tools = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        requests["tool-a"].session_generation = 2
        runtime.on_requests_requeued((requests["tool-a"],), is_retracted=True)
        assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) is joins
        replaced = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        assert replaced is not tools
        assert replaced == uncached_waiters(runtime, InvocationState.WAIT_TOOL)
        assert replaced[-1].session_generation == 2
    finally:
        runtime.close()


def test_changed_wait_membership_invalidates_only_its_group():
    runtime, _ = waiting_runtime()
    try:
        joins = runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
        runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        runtime.on_events((event(
            11, RuntimeEventKind.TOOL_END, invocation_id="tool-a",
        ),))
        assert runtime._waiting_context_keys(InvocationState.WAIT_JOIN) is joins
        assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) == uncached_waiters(
            runtime, InvocationState.WAIT_TOOL,
        )
    finally:
        runtime.close()


def test_same_state_parallel_tool_change_keeps_membership_snapshot():
    runtime, _ = waiting_runtime()
    try:
        tools = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        runtime.on_events((event(
            11, RuntimeEventKind.TOOL_START, invocation_id="tool-a",
            attributes={"tool_run_id": "parallel"},
        ),))
        assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) is tools
        runtime.on_events((event(
            12, RuntimeEventKind.TOOL_END, invocation_id="tool-a",
            attributes={"tool_run_id": "parallel"},
        ),))
        assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) is tools
        assert tools == uncached_waiters(runtime, InvocationState.WAIT_TOOL)
    finally:
        runtime.close()


def test_out_of_band_graph_change_rebuilds_before_trusting_new_deltas():
    runtime, _ = waiting_runtime()
    try:
        runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        runtime.graph.apply(event(
            11, RuntimeEventKind.TOOL_END, invocation_id="tool-a",
        ))
        runtime.on_events((event(
            12, RuntimeEventKind.TOOL_WAIT_OBSERVATION, invocation_id="tool-z",
        ),))
        for state in (InvocationState.WAIT_JOIN, InvocationState.WAIT_TOOL):
            assert runtime._waiting_context_keys(state) == uncached_waiters(runtime, state)
    finally:
        runtime.close()


def test_cached_aliases_follow_invocation_changes_even_with_different_context_tags():
    runtime, _ = waiting_runtime()
    try:
        alias = req("tool-z")
        alias.rid = "alias-tool-z"
        alias.beliefkv_metadata["context_id"] = "alias-context"
        alias.session_id, alias.session_generation = "alias-session", 1
        assert runtime.register_visible_request(alias)
        before = runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        assert len(before) == 3
        runtime.on_events((event(
            11, RuntimeEventKind.TOOL_END, invocation_id="tool-z",
        ),))
        assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) == uncached_waiters(
            runtime, InvocationState.WAIT_TOOL,
        )
    finally:
        runtime.close()


def test_mixed_event_batches_and_bulk_end_match_original_membership():
    runtime, _ = waiting_runtime()
    try:
        batches = (
            (event(11, RuntimeEventKind.TOOL_END, invocation_id="tool-a"),
             event(12, RuntimeEventKind.TOOL_START, invocation_id="child")),
            (event(13, RuntimeEventKind.JOIN_SATISFIED, join_id="join"),
             event(14, RuntimeEventKind.TOOL_END, invocation_id="tool-z")),
            (event(15, RuntimeEventKind.TOOL_END, invocation_id="child"),
             event(16, RuntimeEventKind.WORKFLOW_END)),
        )
        for batch in batches:
            runtime.on_events(batch)
            for state in (InvocationState.WAIT_JOIN, InvocationState.WAIT_TOOL, InvocationState.READY):
                assert runtime._waiting_context_keys(state) == uncached_waiters(runtime, state)
    finally:
        runtime.close()


@pytest.mark.parametrize("completion", (
    RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL, RuntimeEventKind.LLM_RESULT,
))
def test_returning_callee_restores_tool_wait_without_an_awakening_delta(completion):
    runtime, _ = waiting_runtime()
    try:
        runtime.on_events((event(
            11, RuntimeEventKind.CALL, invocation_id="tool-a",
            target_invocation_id="child", execution_mode=ExecutionMode.FOREGROUND,
        ),))
        assert runtime.graph.invocations["tool-a"].state is InvocationState.WAIT_CHILD
        runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        runtime.on_events((event(
            12, completion, invocation_id="child",
            attributes={"terminal": True} if completion is RuntimeEventKind.LLM_RESULT else {},
        ),))
        assert runtime.graph.invocations["tool-a"].state is InvocationState.WAIT_TOOL
        assert runtime._waiting_context_keys(InvocationState.WAIT_TOOL) == uncached_waiters(
            runtime, InvocationState.WAIT_TOOL,
        )
    finally:
        runtime.close()


def test_wait_cache_reuses_the_tool_census_affected_invocations():
    runtime, _ = waiting_runtime()

    class CountedIds:
        def __init__(self, values):
            self.values = tuple(values)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return iter(self.values)

    try:
        invocation = runtime.graph.invocations["tool-a"]
        runtime._local_frontier_features(invocation, 0, now_ms=10.)
        runtime._waiting_context_keys(InvocationState.WAIT_JOIN)
        runtime._waiting_context_keys(InvocationState.WAIT_TOOL)
        contexts = CountedIds(runtime.graph.contexts["ctx-tool-a"].invocation_ids)
        runtime.graph.contexts["ctx-tool-a"].invocation_ids = contexts
        runtime.on_events((event(
            11, RuntimeEventKind.TOOL_END, invocation_id="tool-a",
        ),))
        assert contexts.iterations == 1
        for state in (InvocationState.WAIT_JOIN, InvocationState.WAIT_TOOL):
            assert runtime._waiting_context_keys(state) == uncached_waiters(runtime, state)
    finally:
        runtime.close()
