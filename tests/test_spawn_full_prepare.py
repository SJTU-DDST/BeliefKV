"""Delegation causes incremental backups before JOIN pressure, without a GPU."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind as K
from beliefkv.runtime.sglang_v0520_runtime import SPAWN_FULL_PREPARE_MAX_BYTES
from tests.test_sglang_v0520_runtime import event, final_stage_runtime, req


def fixture():
    runtime = final_stage_runtime()
    runtime.enable_prepare_host = True
    key = runtime.context_sessions["ctx-parent"]
    prepare = Mock(return_value={
        "issued": ((71, 17., 100),), "complete": False,
        "reason": "issued", "max_merged_nodes": 64,
    })
    runtime.attach_native_cache(NS(prepare_beliefkv_spawn_full=prepare))
    runtime.graph.invocations["parent"].state = runtime.graph.invocations["child"].state
    runtime.graph.invocations["child"].parent_invocation_id = "parent"
    return runtime, key, prepare


@pytest.mark.parametrize("kind", (K.SPAWN, K.CALL))
def test_spawn_queues_backup_without_join_wait_or_pressure_and_coalesces_siblings(kind):
    runtime, key, prepare = fixture()
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        runtime.on_events((
            event(7, kind, invocation_id="parent", target_invocation_id="child"),
            event(8, kind, invocation_id="parent", target_invocation_id="other"),
        ))
        assert runtime._spawn_full_prepares == {key.context_id: key}
        assert runtime.counts["spawn_full_prepare_queued"] == 1
        assert runtime.counts["spawn_full_prepare_coalesced"] == 1
        prepare.assert_not_called()
        runtime.dispatch_spawn_prepare()
        prepare.assert_called_once_with(
            session_id=key.session_id, session_generation=key.session_generation,
            max_batch_bytes=SPAWN_FULL_PREPARE_MAX_BYTES,
        )
        assert runtime._cold_full_backups == {71: (17., "spawn")}
        assert not runtime.physical_ledger.pending_count
        assert not runtime.completed_physical_actions
        [issued] = [row for row in records if row["event"] == "spawn_full_prepare"]
        assert issued["include_mamba"] is False
        assert issued["max_batch_bytes"] == 4 * 1024**3
        assert runtime.counts["spawn_full_prepare_settled_contexts"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("generation", "epoch", "terminal", "internal", "disabled"))
def test_spawn_backup_never_uses_stale_or_internal_delegation(change):
    runtime, key, prepare = fixture()
    if change == "disabled":
        runtime.enable_prepare_host = False
    try:
        runtime.on_events((event(
            7, K.SPAWN, invocation_id="parent", target_invocation_id="child",
            attributes={"runtime_internal": change == "internal"},
        ),))
        if change in ("generation", "epoch"):
            runtime.context_sessions[key.context_id] = replace(
                key, **({"session_generation": 2} if change == "generation" else {"context_epoch": 1}),
            )
        elif change == "terminal":
            runtime.on_events((event(8, K.RETURN, invocation_id="parent"),))
        runtime.dispatch_spawn_prepare()
        prepare.assert_not_called()
        assert not runtime._cold_full_backups
    finally:
        runtime.close()


def test_backup_remains_queued_until_native_reports_settled_incremental_copy():
    runtime, key, prepare = fixture()
    runtime._queue_spawn_prepare(event(
        7, K.SPAWN, invocation_id="parent", target_invocation_id="child",
    ))
    prepare.side_effect = (
        {"issued": ((71, 17., 100),), "complete": False, "reason": "issued", "max_merged_nodes": 64},
        {"issued": (), "complete": False, "reason": "d2h_busy"},
        {"issued": ((72, 18., 900),), "complete": False, "reason": "issued", "max_merged_nodes": 64},
        {"issued": (), "complete": True, "reason": "resident_full_backed"},
    )
    try:
        for index in range(4):
            with patch(
                "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                return_value=float(index + 1),
            ):
                runtime.dispatch_spawn_prepare()
            assert bool(runtime._spawn_full_prepares) is (index < 3)
        assert runtime.counts["spawn_full_prepare_issued_batches"] == 2
        assert runtime.counts["spawn_full_prepare_issued_tokens"] == 1000
        assert runtime.counts["spawn_full_prepare_settled_contexts"] == 1
    finally:
        runtime.close()


def test_spawn_backups_are_dispatched_before_pressure_prepare_and_yield_to_critical_restore():
    runtime, key, prepare = fixture()
    runtime._queue_spawn_prepare(event(
        7, K.SPAWN, invocation_id="parent", target_invocation_id="child",
    ))
    calls = []
    runtime.dispatch_join_prepare = lambda *args, **kwargs: calls.append("join-prepare")
    runtime.dispatch_tool_prepare = lambda *args, **kwargs: calls.append("tool-prepare")
    runtime.dispatch_idle_prepare = lambda: calls.append("idle")
    prepare.side_effect = lambda **kwargs: calls.append("spawn") or {
        "issued": (), "complete": True, "reason": "resident_full_backed",
    }
    try:
        runtime.dispatch_after_forward()
        assert calls == ["spawn", "join-prepare", "tool-prepare", "idle"]
        runtime._queue_spawn_prepare(event(
            8, K.SPAWN, invocation_id="parent", target_invocation_id="child",
        ))
        runtime._spawn_prepare_next_ms = 0.
        with patch.object(runtime, "_live_critical_parent_demand", return_value=(key, 100, 0, 10.)):
            runtime.dispatch_spawn_prepare()
        assert prepare.call_count == 1
        assert runtime._spawn_full_prepares
        assert runtime.counts["spawn_full_prepare_blocked_critical"] == 1
    finally:
        runtime.close()


def test_nested_delegator_is_queued_by_its_own_context():
    runtime, _, prepare = fixture()
    child = req("child")
    child.session_id, child.session_generation = "child-session", 1
    runtime.register_visible_request(child)
    try:
        runtime.on_events((
            event(7, K.INVOCATION_CREATE, invocation_id="grand", context_id="ctx-grand",
                  agent_definition_id="grand", agent_instance_id="grand"),
            event(8, K.SPAWN, invocation_id="child", target_invocation_id="grand"),
        ))
        key = runtime.context_sessions["ctx-child"]
        assert runtime._spawn_full_prepares == {"ctx-child": key}
        runtime.dispatch_spawn_prepare()
        assert prepare.call_args.kwargs["session_id"] == "child-session"
    finally:
        runtime.close()


def test_spawn_backup_survives_the_parent_next_request_and_binding_gap():
    runtime, key, prepare = fixture()
    try:
        runtime.on_events((
            event(7, K.SPAWN, invocation_id="parent", target_invocation_id="child"),
            event(8, K.LLM_SUBMIT, invocation_id="parent", context_id=key.context_id,
                  context_epoch=1, attributes={"request_id": "parent-next"}),
        ))
        assert key.context_id not in runtime.context_sessions
        runtime.dispatch_spawn_prepare()
        assert prepare.call_count == 1
        successor = req("parent")
        successor.rid = "parent-next"
        successor.session_id, successor.session_generation = key.session_id, key.session_generation
        successor.beliefkv_metadata["context_epoch"] = 1
        runtime.register_visible_request(successor)
        runtime._spawn_prepare_next_ms = 0.
        prepare.return_value = {"issued": (), "complete": True, "reason": "resident_full_backed"}
        runtime.dispatch_spawn_prepare()
        assert prepare.call_count == 2
        assert not runtime._spawn_full_prepares
    finally:
        runtime.close()


@pytest.mark.parametrize("settled", (False, True))
def test_spawn_backup_ack_rearms_only_after_native_copy_is_settled(settled):
    runtime, key, prepare = fixture()
    cache = runtime._native_cache
    node = NS(
        creation_time=17., component_data={0: NS(host_value=(1,))},
        write_through_pending_id=None if settled else 71,
    )
    cache.tree_core = NS(node_by_id=lambda _: node)
    cache.ongoing_write_through = {}
    runtime._cold_full_backups[71] = (17., "spawn")
    runtime._spawn_prepare_next_ms = 1000.
    try:
        assert not runtime.on_native_transfer_commit(NS(
            direction="d2h", status="completed", node_ids=(71,),
            num_tokens_by_pool=(("kv", 100),),
        ))
        assert runtime._spawn_prepare_next_ms == (0. if settled else 1000.)
        assert runtime.counts["spawn_cold_full_backup_acked"] == int(settled)
        prepare.assert_not_called()
    finally:
        runtime.close()


def test_changed_native_path_retries_from_a_fresh_snapshot_without_per_step_scanning():
    runtime, key, prepare = fixture()
    runtime._queue_spawn_prepare(event(
        7, K.SPAWN, invocation_id="parent", target_invocation_id="child",
    ))
    prepare.side_effect = (
        {"issued": (), "complete": False, "reason": "changed_path"},
        {"issued": (), "complete": True, "reason": "resident_full_backed"},
    )
    try:
        for now in (1., 1.1, 1.199):
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now):
                runtime.dispatch_spawn_prepare()
        assert prepare.call_count == 1
        assert runtime._spawn_full_prepares
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1.2):
            runtime.dispatch_spawn_prepare()
        assert prepare.call_count == 2
        assert not runtime._spawn_full_prepares
    finally:
        runtime.close()


def test_context_compaction_retires_backup_even_during_the_binding_gap():
    runtime, key, prepare = fixture()
    runtime._queue_spawn_prepare(event(
        7, K.SPAWN, invocation_id="parent", target_invocation_id="child",
    ))
    runtime.context_sessions.pop(key.context_id)
    try:
        runtime.on_events((event(
            8, K.CONTEXT_COMPACT, invocation_id="parent",
            context_id=key.context_id, context_epoch=1,
        ),))
        runtime.dispatch_spawn_prepare()
        assert not runtime._spawn_full_prepares
        prepare.assert_not_called()
    finally:
        runtime.close()
