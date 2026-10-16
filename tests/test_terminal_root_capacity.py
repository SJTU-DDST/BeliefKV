"""A root can release private KV only after its workflow has ended."""

from dataclasses import replace

import pytest

from beliefkv.core.events import RuntimeEventKind
from tests.test_sglang_v0520_runtime import event, req
from tests.test_terminal_child_capacity import family, reclaimed, returned
from tests.test_waiting_full_tail_backup import native_cache


@pytest.mark.parametrize("batched", (False, True))
@pytest.mark.parametrize("persistent", (False, True))
def test_root_reclamation_requires_workflow_end_and_survives_session_unbinding(
    family, batched, persistent,
):
    runtime, _, cache, records = family
    runtime.graph.invocations["ordinary"].persistent = persistent
    runtime.graph.contexts["ctx-ordinary"].persistent = persistent
    completion = returned(7, "ordinary")
    ended = event(8, RuntimeEventKind.WORKFLOW_END)
    if batched:
        runtime.on_events((completion, ended))
    else:
        runtime.on_events((completion,))
        assert "ctx-ordinary" not in runtime.context_sessions
        cache.retire_beliefkv_child_session.assert_not_called()
        assert len(runtime._terminal_root_contexts) == 1
        runtime.on_events((ended,))
    cache.retire_beliefkv_child_session.assert_called_once_with(
        session_id="s-ordinary", session_generation=1,
    )
    assert runtime._terminal_root_contexts == {}
    assert runtime._parent_capacity_handoffs == {}
    assert runtime.counts["terminal_root_freed_full_tokens"] == 8
    assert runtime.counts["terminal_child_freed_full_tokens"] == 0
    root_rows = [row for row in records if row.get("event") == "terminal_workflow_root_cache_reclaimed"]
    assert len(root_rows) == 1
    assert root_rows[0]["invocation_scope"] == "workflow_root"
    assert root_rows[0]["join_id"] is None
    runtime.on_events((event(9, RuntimeEventKind.WORKFLOW_END),))
    cache.retire_beliefkv_child_session.assert_called_once()


def test_epoch_retirement_is_not_a_root_completion(family):
    runtime, _, cache, _ = family
    runtime.on_events((event(
        7, RuntimeEventKind.CONTEXT_ADVANCE, invocation_id="ordinary",
        context_id="ctx-ordinary", context_epoch=1,
    ),))
    assert runtime._terminal_root_contexts == {}
    runtime.on_events((event(8, RuntimeEventKind.WORKFLOW_END),))
    cache.retire_beliefkv_child_session.assert_not_called()


def test_rebound_root_context_invalidates_deferred_retirement(family):
    runtime, _, cache, _ = family
    runtime.on_events((returned(7, "ordinary"),))
    runtime.graph.contexts["ctx-ordinary"].epoch = 1
    runtime.on_events((event(8, RuntimeEventKind.WORKFLOW_END),))
    cache.retire_beliefkv_child_session.assert_not_called()
    assert runtime._terminal_root_contexts == {}
    assert runtime.counts["terminal_root_retirement_invalidated"] == 1


def test_another_live_workflow_cannot_lose_its_native_session(family):
    runtime, _, cache, _ = family
    workflow = "other-workflow"
    runtime.on_events((
        replace(event(7, RuntimeEventKind.WORKFLOW_START), workflow_id=workflow),
        replace(event(
            8, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="outside", context_id="ctx-outside",
        ), workflow_id=workflow),
    ))
    outside = req("outside")
    outside.beliefkv_metadata["root_workflow_id"] = workflow
    outside.session_id, outside.session_generation = "s-ordinary", 1
    assert runtime.register_visible_request(outside)
    runtime.on_events((returned(9, "ordinary"), event(10, RuntimeEventKind.WORKFLOW_END)))
    cache.retire_beliefkv_child_session.assert_not_called()
    assert runtime.counts["terminal_root_retirement_invalidated"] == 1


def test_root_native_dependency_retries_after_workflow_end(family):
    runtime, _, cache, records = family
    cache.retire_beliefkv_child_session.side_effect = (
        reclaimed(retry=((11, 1),)),
        {**reclaimed(device={0: 8}), "freed_host_units": {0: 4}},
    )
    runtime.on_events((returned(7, "ordinary"), event(8, RuntimeEventKind.WORKFLOW_END)))
    assert len(runtime._terminal_child_reclaims) == 1
    runtime.on_events((event(9, RuntimeEventKind.WORKFLOW_END),))
    assert len(runtime._terminal_child_reclaims) == 1
    runtime._retry_terminal_child_reclaims(now_ms=100.)
    assert runtime._terminal_child_reclaims == {}
    assert cache.retire_beliefkv_child_session.call_args.kwargs == {
        "session_id": "s-ordinary", "session_generation": 1,
        "retry_anchors": ((11, 1),), "max_nodes": 16,
    }
    assert runtime.counts["terminal_root_freed_full_tokens"] == 8
    assert records[-1]["freed_host_units"] == {0: 4}


@pytest.mark.parametrize("backed", (False, True))
@pytest.mark.parametrize("close_first", (False, True))
def test_root_cleanup_uses_the_real_native_allocator_and_keeps_shared_prefix(
    native_cache, monkeypatch, backed, close_first,
):
    from test_beliefkv_terminal_capacity import insert_session, match_length
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    cache, allocator, request_pool = native_cache
    insert_session(cache, [1, 2, 9, 9], "other")
    leaf, generation = insert_session(cache, [1, 2, 3, 4], "root")
    host_frees = {0: [], 2: []}
    for component, values in host_frees.items():
        monkeypatch.setattr(cache.components[component], "free_host_values", values.extend)
    if backed:
        for component in (0, 2):
            data = leaf.component_data[component]
            data.host_value = data.value.clone()
        cache.tree_core._update_duplicate_tracking(leaf)
    runtime = NativeAdmissionRuntime()
    runtime.attach_native_cache(cache)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(1, RuntimeEventKind.INVOCATION_CREATE, invocation_id="root", context_id="ctx-root"),
    ))
    request = req("root")
    request.session_id, request.session_generation = "root", generation
    assert runtime.register_visible_request(request)
    free = allocator.available_size()
    mamba_free = request_pool.mamba_allocator.available_size()
    try:
        runtime.on_events((returned(2, "root"),))
        assert allocator.available_size() == free
        if close_first:
            cache.release_radix_session("root")
        runtime.on_events((event(3, RuntimeEventKind.WORKFLOW_END),))
        assert allocator.available_size() == free + 2
        assert request_pool.mamba_allocator.available_size() == mamba_free + 1
        assert match_length(cache, [1, 2, 9, 9]) == 4
        assert match_length(cache, [1, 2, 3, 4]) < 4
        assert sum(len(values) for values in host_frees[0]) == (2 if backed else 0)
        assert sum(len(values) for values in host_frees[2]) == int(backed)
        with pytest.raises(KeyError):
            cache.tree_core.node_by_id(leaf.id)
        cache._execute_and_commit_kv_backup.assert_not_called()
    finally:
        runtime.close()
