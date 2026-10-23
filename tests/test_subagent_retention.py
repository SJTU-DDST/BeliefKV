from array import array
from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.subagent_retention import (
    DormantState, DormantSubagentRetention, KVExtent, ReuseEstimator,
    demote_dormant_backed, inspect_dormant_extents,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, req
from tests.test_waiting_full_tail_backup import native_cache


def extent(node=1, *, size=100, component=0, device=True, host=True, exclusive=True):
    return KVExtent(component, node, float(node), 4 if component == 0 else 1,
                    size, device, host, exclusive, False)


def remember(manager, context="a", *, returned_at=1., extents=None, features=None):
    request = req(context)
    request.session_id, request.session_generation = f"session-{context}", 1
    from beliefkv.runtime.sglang_v0520_admission import _request_key
    key = _request_key(request)
    manager.remember(key, event(
        5, Kind.RETURN, invocation_id=context, context_id=key.context_id,
        attributes={
            "reusable_subagent": True, "agent_handle": f"handle-{context}",
            "reuse_features": features or {},
        },
    ))
    state = manager.states[key.context_id]
    state.returned_at = returned_at
    state.extents = (extent(),) if extents is None else extents
    state.scored = True
    return state


def test_cold_start_uses_bounded_mru_and_replans_previously_settled_owners():
    manager = DormantSubagentRetention("selective", host_budget_bytes=100)
    old = remember(manager, "old", returned_at=1.)
    manager._plan()
    assert old.tier == "host"
    manager.work.clear()
    new = remember(manager, "new", returned_at=2., extents=(extent(2),))
    manager._plan()
    assert new.tier == "host" and old.tier == "drop"
    assert old.key.context_id in manager.work
    assert manager.counts["planned_unique_host_bytes"] == 100
    assert not manager.estimator.ready


def test_budget_counts_shared_physical_extents_once_and_keeps_required_state():
    manager = DormantSubagentRetention("lru", host_budget_bytes=300)
    common = extent(1, exclusive=False)
    a = remember(manager, "a", extents=(common, extent(2), extent(3, component=2, size=50)))
    b = remember(manager, "b", returned_at=2., extents=(common, extent(4, size=50)))
    manager._plan()
    assert a.tier == b.tier == "host"
    assert manager.counts["planned_unique_host_bytes"] == 300
    manager.host_budget_bytes = 299
    manager._plan()
    assert a.tier == "drop" and b.tier == "host"


def test_predictor_uses_real_resume_and_only_completed_negative_episodes():
    manager = DormantSubagentRetention()
    positive = remember(manager, "yes")
    manager.activate(event(
        6, Kind.INVOCATION_CREATE, invocation_id="fresh-yes",
        context_id=positive.key.context_id, attributes={
            "agent_handle": positive.handle, "resumed": True,
        },
    ))
    assert manager.estimator.counts["positive"] == 0
    manager.started(event(
        7, Kind.LLM_SUBMIT, invocation_id="fresh-yes", context_id=positive.key.context_id,
    ))
    assert manager.estimator.counts["positive"] == 1
    assert positive.key.context_id not in manager.states
    remember(manager, "censored")
    manager.end("wf", labels_complete=False)
    assert manager.estimator.counts["negative"] == 0
    other = DormantSubagentRetention()
    remember(other, "completed")
    other.end("wf", labels_complete=True)
    assert other.estimator.counts["negative"] == 1


def test_dropped_physical_state_still_provides_positive_reuse_label():
    manager = DormantSubagentRetention("drop")
    state = remember(manager)
    cache = NS(retire_beliefkv_child_session=Mock(return_value={
        "retry_anchors": (), "freed_device_units": {0: 4},
        "freed_host_units": {}, "discarded_nodes": ((1, 1.),), "reason": None,
    }))
    runtime = NS(_native_cache=cache, _opportunity_writer=None)
    manager._drop(runtime, state)
    assert state.discarded and state.key.context_id in manager.states
    assert not manager.work
    manager.activate(event(
        7, Kind.INVOCATION_CREATE, invocation_id="fresh-activation",
        context_id=state.key.context_id, attributes={
            "agent_handle": state.handle, "resumed": True,
        },
    ))
    manager.started(event(
        8, Kind.LLM_SUBMIT, invocation_id="fresh-activation", context_id=state.key.context_id,
    ))
    assert manager.estimator.counts["positive"] == 1
    assert manager.counts["resume_after_physical_drop"] == 1


def test_censored_return_does_not_train_an_apparent_negative():
    model = ReuseEstimator(minimum_labels=4)
    for value in (True, True, False, False):
        model.observe({"implementation": True}, value, predicted=.5)
    assert model.ready and model.predict({"implementation": True}) == .5
    model.observe({"censored": True}, False, predicted=.5)
    assert model.counts["labels"] == 4 and model.counts["censored"] == 1
    assert model.brier_sum == 1.


def test_resume_cancelled_before_model_start_is_censored():
    manager = DormantSubagentRetention()
    state = remember(manager)
    manager.activate(event(
        6, Kind.INVOCATION_CREATE, invocation_id="new",
        context_id=state.key.context_id,
        attributes={"agent_handle": state.handle, "resumed": True},
    ))
    manager.cancel(event(7, Kind.INVOCATION_CANCEL, invocation_id="new"))
    assert not manager.pending_resumes
    assert manager.estimator.counts["labels"] == 0
    assert manager.counts["censored_resume_before_model"] == 1


def test_warm_predictor_scores_avoided_work_against_missing_backup_and_capacity():
    manager = DormantSubagentRetention(host_budget_bytes=100)
    for _ in range(8):
        manager.estimator.observe({"unresolved": True}, True, predicted=.5)
        manager.estimator.observe({}, False, predicted=.5)
    good = remember(manager, "good", returned_at=1., features={"unresolved": True})
    bad = remember(manager, "bad", returned_at=2., extents=(extent(2),))
    manager._plan()
    assert good.probability > bad.probability
    assert good.tier == "host" and bad.tier == "drop"


def test_semantic_scope_updates_only_after_an_actual_new_delegation():
    manager = DormantSubagentRetention()
    state = remember(manager, features={"scope_ids": [1, 2, 3]})
    assert "related_delegation" not in state.features
    manager.observe_delegation(event(
        6, Kind.INVOCATION_CREATE, invocation_id="other", context_id="ctx-other",
        attributes={"reusable_subagent": True, "reuse_features": {"scope_ids": [2, 3, 4]}},
    ))
    assert state.features["related_delegation"] is True
    assert manager.counts["semantic_boundary_updates"] == 1
    assert manager.estimator.counts["labels"] == 0


def test_wrong_root_or_old_invocation_cannot_label_a_resume():
    manager = DormantSubagentRetention()
    state = remember(manager)
    for arguments in (
        {"workflow_id": "other", "invocation_id": "new"},
        {"invocation_id": state.key.invocation_id},
    ):
        manager.activate(replace(event(
            6, Kind.INVOCATION_CREATE, context_id=state.key.context_id,
            attributes={"agent_handle": state.handle, "resumed": True},
        ), **arguments))
    assert state.key.context_id in manager.states
    assert not manager.estimator.counts["labels"]


def close_event(state, **overrides):
    return replace(event(
        8, Kind.STRUCTURED_ACTION,
        invocation_id=state.key.invocation_id, context_id=state.key.context_id,
        context_epoch=state.key.context_epoch,
        attributes={"subagent_context_close": True, "agent_handle": state.handle},
    ), **overrides)


def test_explicit_close_records_one_negative_even_if_workflow_later_ends():
    manager = DormantSubagentRetention()
    state = remember(manager)
    manager.close_context(close_event(state))
    manager.close_context(close_event(state))
    manager.end(state.key.root_workflow_id, labels_complete=True)
    assert manager.estimator.counts["labels"] == manager.estimator.counts["negative"] == 1
    assert manager.counts["explicit_closes"] == 1
    assert state.closed


@pytest.mark.parametrize("mismatch", ("root", "context", "epoch", "invocation", "handle", "flag"))
def test_stale_or_foreign_close_cannot_label_or_retire_dormant_state(mismatch):
    manager = DormantSubagentRetention()
    state = remember(manager)
    changes = {
        "root": {"workflow_id": "other"},
        "context": {"context_id": "other"},
        "epoch": {"context_epoch": state.key.context_epoch + 1},
        "invocation": {"invocation_id": "old"},
        "handle": {"attributes": {"subagent_context_close": True, "agent_handle": "other"}},
        "flag": {"attributes": {"agent_handle": state.handle}},
    }
    manager.close_context(close_event(state, **changes[mismatch]))
    assert not state.closed and not manager.estimator.counts["labels"]


def test_guarded_return_close_is_censored_and_physical_drop_is_not_a_label():
    manager = DormantSubagentRetention()
    state = remember(manager, features={"censored": True})
    state.discarded = True
    manager.close_context(close_event(state))
    manager.end(state.key.root_workflow_id, labels_complete=True)
    assert manager.estimator.counts["labels"] == 0
    assert manager.estimator.counts["censored"] == 1


@pytest.fixture
def dormant_native(native_cache):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, request_pool = native_cache
    node, generation = insert_session(cache, list(range(1, 17)), "session-child")
    # Production metadata retains one latest checkpoint for each component.
    cache.session_refs.register_session_ref(NS(
        session_id="session-child", session_generation=generation, session=None,
        last_node=node.id, origin_input_ids=array("q", range(1, 17)),
        output_ids=array("q"), extra_key=None, beliefkv_metadata={},
    ))
    cache.is_write_back = True
    cache.host_memory_mode = "cache"
    cache.cache_controller.write_queue = []
    cache.cache_controller.ack_write_queue = []
    runtime = NativeAdmissionRuntime()
    manager = DormantSubagentRetention("selective")
    runtime._subagent_retention = manager
    runtime.on_events((event(0, Kind.WORKFLOW_START), event(
        1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="ctx-parent",
    ), event(
        2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="ctx-child",
        parent_invocation_id="parent", attributes={"persistent": True},
    )))
    request = req("child")
    request.session_id, request.session_generation = "session-child", generation
    runtime.register_visible_request(request)
    runtime.attach_native_cache(cache)
    runtime.on_events((event(
        3, Kind.RETURN, invocation_id="child", context_id="ctx-child",
        attributes={
            "context_retired": False, "reusable_subagent": True,
            "agent_handle": "handle-child", "reuse_features": {},
        },
    ),))
    assert "ctx-child" not in runtime.context_sessions
    assert "ctx-child" in manager.states
    try:
        yield runtime, manager, cache, allocator, request_pool, node
    finally:
        runtime.close()


def back(node, cache):
    for data in node.component_data:
        if data.value is not None:
            data.host_value = data.value.clone()
    cache.tree_core._update_duplicate_tracking(node)


def test_actual_radix_backup_ack_is_required_before_any_device_release(dormant_native):
    runtime, manager, cache, allocator, _, node = dormant_native
    state = manager.states["ctx-child"]
    before = allocator.available_size()
    initial = inspect_dormant_extents(cache, state.key)
    assert sum(item.units for item in initial if item.component == 0) == 16
    assert not demote_dormant_backed(cache, state.key, initial)
    assert allocator.available_size() == before
    back(node, cache)
    node.write_through_pending_id = 71
    snapshot = inspect_dormant_extents(cache, state.key)
    assert not demote_dormant_backed(cache, state.key, snapshot)
    node.write_through_pending_id = None
    freed = demote_dormant_backed(cache, state.key, inspect_dormant_extents(cache, state.key))
    assert freed == {0: 16, 2: 1}
    assert allocator.available_size() == before + 16
    assert node.component_data[0].value is None and node.component_data[0].host_value is not None
    assert cache.session_refs.snapshot_latest_session_leaf_anchors(
        state.key.session_id, state.key.session_generation,
    ) is not None
    cache._execute_and_commit_kv_backup.assert_not_called()


def test_incremental_full_then_state_backup_and_demotion_use_native_safe_points(dormant_native):
    runtime, manager, cache, allocator, _, node = dormant_native
    state = manager.states["ctx-child"]
    # Scale accounting only; the actual radix and allocator remain CPU-backed.
    cache.cache_controller.mem_pool_host.entry_map["kv"].host_pool.size_per_token = 16 * 1024**2
    cache.cache_controller.mem_pool_host.size_per_token = 16 * 1024**2
    cache.cache_controller.mem_pool_host.available_size = lambda: 64
    cache.cache_controller.start_writing = Mock()
    enqueued = []

    def enqueue(action, **kwargs):
        node_id = action.node_ids[0]
        target = cache.tree_core.node_by_id(node_id)
        target.write_through_pending_id = 91
        cache.ongoing_write_through[node_id] = ("cpu-copy",)
        enqueued.append((target, kwargs.get("beliefkv_include_mamba", True)))
        return 0

    cache._execute_and_commit_kv_backup = Mock(side_effect=enqueue)

    def acknowledge():
        target, include_state = enqueued[-1]
        for component, data in enumerate(target.component_data):
            if data.value is not None and (component == 0 or include_state):
                data.host_value = data.value.clone()
        target.write_through_pending_id = None
        cache.ongoing_write_through.pop(target.id)
        cache.tree_core._update_duplicate_tracking(target)

    before = allocator.available_size()
    manager.dispatch(runtime)
    assert len(enqueued) == 1 and enqueued[0][1] is False
    assert allocator.available_size() == before
    assert node.component_data[0].host_value is None
    manager.next_ms = 0.
    manager.dispatch(runtime)
    assert len(enqueued) == 1 and allocator.available_size() == before
    acknowledge()
    manager.next_ms = 0.
    manager.dispatch(runtime)
    assert allocator.available_size() == before + 16
    assert len(enqueued) == 2 and enqueued[1][1] is True
    assert node.component_data[2].value is not None
    acknowledge()
    manager.next_ms = 0.
    manager.dispatch(runtime)
    assert node.component_data[2].value is None
    assert manager.counts["backup_batches"] == 1
    assert manager.counts["backup_full_tokens"] == 16
    assert manager.counts["demoted_mamba_slots"] == 1


@pytest.mark.parametrize("blocked", ("full_lock", "host_lock", "d2h", "h2d", "generation", "shared"))
def test_actual_radix_dormant_demotion_checks_native_locks_and_ownership(dormant_native, blocked):
    runtime, manager, cache, allocator, _, node = dormant_native
    state = manager.states["ctx-child"]
    back(node, cache)
    before = allocator.available_size()
    if blocked == "full_lock":
        node.component_data[0].lock_ref += 1
    elif blocked == "host_lock":
        node.component_data[0].host_lock_ref += 1
    elif blocked == "d2h":
        node.write_through_pending_id = 91
    elif blocked == "h2d":
        node.load_back_pending_id = 92
    elif blocked == "generation":
        cache.release_radix_session(state.key.session_id)
        cache.ensure_session_generation(state.key.session_id)
    else:
        from test_beliefkv_terminal_capacity import insert_session
        insert_session(cache, list(range(1, 17)), "another-live-session")
    extents = inspect_dormant_extents(cache, state.key)
    if extents is not None:
        assert not demote_dormant_backed(cache, state.key, extents)
    assert allocator.available_size() == before


def test_critical_parent_revokes_hbm_grace_without_starting_dormant_dma(dormant_native):
    runtime, manager, cache, allocator, _, node = dormant_native
    state = manager.states["ctx-child"]
    back(node, cache)
    state.features = {"reuse_count": 1, "unresolved": True}
    state.returned_at = time.monotonic()
    runtime._live_critical_parent_demand = lambda: ("parent",)
    cache.prepare_beliefkv_spawn_full = Mock(side_effect=AssertionError("demand must win"))
    before = allocator.available_size()
    manager.dispatch(runtime)
    assert manager.counts["hbm_grace_revoked"] == 1
    assert allocator.available_size() == before + 16
    assert manager.counts["backup_yielded_to_demand"] == 1


def test_retain_all_is_native_soft_cache_without_forced_eager_backup(dormant_native):
    runtime, manager, cache, allocator, _, node = dormant_native
    manager.policy = "all"
    before = allocator.available_size()
    cache.prepare_beliefkv_spawn_full = Mock(side_effect=AssertionError("no forced D2H"))
    manager.dispatch(runtime)
    assert allocator.available_size() == before
    assert node.component_data[0].value is not None
    assert not manager.work and len(manager.states) == 1
    cache._execute_and_commit_kv_backup.assert_not_called()


def test_retain_all_does_not_pin_dormant_pages_against_native_pressure(dormant_native):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    runtime, manager, cache, allocator, _, node = dormant_native
    manager.policy = "all"
    state = manager.states["ctx-child"]
    manager.dispatch(runtime)
    cache.is_write_back = False
    cache.cache_controller.write_policy = "write_through"
    before = allocator.available_size()
    result = cache.evict(EvictParams(num_tokens=16))
    assert result.num_tokens_evicted == 16
    assert allocator.available_size() == before + 16
    with pytest.raises(KeyError):
        cache.tree_core.node_by_id(node.id)
    assert not state.closed and not state.discarded
    assert manager.estimator.counts["labels"] == 0


def test_actual_drop_frees_kv_but_does_not_close_logical_handle(dormant_native):
    runtime, manager, cache, allocator, _, node = dormant_native
    manager.policy = "drop"
    state = manager.states["ctx-child"]
    before = allocator.available_size()
    manager.dispatch(runtime)
    assert allocator.available_size() == before + 16
    assert state.discarded and not state.closed
    old_generation = state.key.session_generation
    assert cache.ensure_session_generation(state.key.session_id) != old_generation
    # A stale cleanup proof must not affect a newly opened native generation.
    stale = cache.retire_beliefkv_child_session(
        session_id=state.key.session_id, session_generation=old_generation,
    )
    assert stale["reason"] == "session_anchors_unavailable"


def test_compaction_or_new_activation_revokes_old_dormant_authority(dormant_native):
    runtime, manager, cache, allocator, _, node = dormant_native
    back(node, cache)
    runtime.graph.contexts["ctx-child"].epoch += 1
    before = allocator.available_size()
    manager.dispatch(runtime)
    assert not manager.states and allocator.available_size() == before
    assert manager.counts["stale_context"] == 1


def test_completed_workflow_discards_dormant_state_and_records_negative(dormant_native):
    runtime, manager, cache, allocator, _, node = dormant_native
    runtime.on_events((event(
        4, Kind.WORKFLOW_END, attributes={"reuse_labels_complete": True},
    ),))
    manager.dispatch(runtime)
    assert not manager.states
    assert manager.estimator.counts["negative"] == 1


def test_feature_disabled_does_not_construct_a_manager(monkeypatch):
    monkeypatch.delenv("BELIEFKV_SUBAGENT_RETENTION", raising=False)
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime._subagent_retention is None
    finally:
        runtime.close()
