from dataclasses import replace
import time
from types import SimpleNamespace
from unittest.mock import call, patch

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease
from tests.test_sglang_v0520_runtime import final_stage_runtime


def test_pressure_maintenance_checks_one_live_owner_for_many_nodes_each_call():
    runtime = final_stage_runtime()
    runtime.enable_prepare_host = True
    key = runtime.context_sessions["ctx-parent"]
    try:
        for node_id in range(64):
            runtime._remember_parent_pressure_node(node_id, key, float(node_id))
        with patch.object(
            runtime, "_live_parent_pressure_key",
            wraps=runtime._live_parent_pressure_key,
        ) as validate:
            runtime._prune_parent_pressure_candidates()
            runtime._prune_parent_pressure_candidates()
        assert validate.call_args_list == [call(key), call(key)]
        assert len(runtime._parent_pressure_candidates) == 64
        runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
        runtime._prune_parent_pressure_candidates()
        assert runtime._parent_pressure_candidates == {}
        assert runtime._parent_pressure_groups == {}
    finally:
        runtime.close()


def test_pressure_owner_rebinding_preserves_new_epoch_and_old_insertion_order():
    runtime = final_stage_runtime()
    key = runtime.context_sessions["ctx-parent"]
    newer = replace(key, request_id="new-request", context_epoch=key.context_epoch + 1)
    try:
        runtime._remember_parent_pressure_node(7, key, 1.)
        runtime._remember_parent_pressure_node(8, key, 2.)
        runtime._remember_parent_pressure_node(7, newer, 3.)
        assert tuple(runtime._parent_pressure_candidates) == (7, 8)
        assert runtime._parent_pressure_positions == {7: 0, 8: 1}
        assert runtime._parent_pressure_groups == {key: {8}, newer: {7}}
        with patch.object(runtime, "_live_parent_pressure_key", side_effect=lambda k: k == newer):
            runtime._prune_parent_pressure_candidates()
        assert runtime._parent_pressure_candidates == {7: (newer, 3.)}
        assert runtime._parent_pressure_groups == {newer: {7}}
        runtime._remember_parent_pressure_node(8, newer, 4.)
        assert runtime._parent_pressure_positions[8] > runtime._parent_pressure_positions[7]
    finally:
        runtime.close()


def test_sparse_publication_preserves_insertion_order_and_checks_live_pool_state():
    class Node(SimpleNamespace):
        __hash__ = object.__hash__

    runtime = final_stage_runtime()
    key = runtime.context_sessions["ctx-parent"]
    tail = (101, 60, 90, 40, 120, 70, 100, 50)
    order = (*range(1, 17), *tail, *(
        node_id for node_id in range(17, 129) if node_id not in tail
    ))
    nodes = {
        node_id: Node(
            id=node_id, creation_time=float(node_id), backuped=True,
            write_through_pending_id=None, load_back_pending_id=None,
            component_data={
                0: SimpleNamespace(value=(0,), host_value=(0,), lock_ref=0, session_ref=1),
                2: SimpleNamespace(
                    value=(0,) if node_id in tail[4:] else None,
                    host_value=(0,) if node_id in tail[4:] else None,
                    lock_ref=0, session_ref=1,
                ),
            },
        )
        for node_id in order
    }
    reads = []

    def node_by_id(node_id):
        reads.append(node_id)
        return nodes[node_id]

    cache = SimpleNamespace(
        tree_core=SimpleNamespace(
            node_by_id=node_by_id,
            evictable_device_leaves={nodes[node_id] for node_id in tail[:4]},
            lru_lists={2: SimpleNamespace(cache={
                node_id: nodes[node_id] for node_id in tail[4:]
            })},
        ),
        ongoing_write_through={},
    )
    runtime._native_cache = cache
    try:
        for node_id in order:
            runtime._remember_parent_pressure_node(node_id, key, float(node_id))
        nodes[60].creation_time += 1.
        nodes[90].load_back_pending_id = "pending"
        nodes[120].component_data[2].host_value = None
        nodes[70].component_data[2].session_ref = 2
        runtime._publish_parent_pressure_candidates(exclude_node_ids=(50,))
        assert cache.beliefkv_join_pressure_candidates == tuple(
            (node_id, float(node_id)) for node_id in range(1, 9)
        )
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: ((101, 101.), (40, 40.)), 2: ((100, 100.),),
        }
        assert reads == [*range(1, 17), *tail[:-1]]
        assert tuple(runtime._parent_pressure_candidates) == order

        nodes[60].creation_time -= 1.
        nodes[90].load_back_pending_id = None
        nodes[120].component_data[2].host_value = (0,)
        nodes[70].component_data[2].session_ref = 1
        reads.clear()
        runtime._publish_parent_pressure_candidates(exclude_node_ids=(40,))
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: ((101, 101.), (60, 60.), (90, 90.)),
            2: ((120, 120.), (70, 70.), (100, 100.), (50, 50.)),
        }
        assert reads == [*range(1, 17), *(node_id for node_id in tail if node_id != 40)]
    finally:
        runtime.close()


def test_pressure_protection_uses_exact_node_generation_and_counts_once():
    runtime = final_stage_runtime()
    key = runtime.context_sessions["ctx-parent"]
    now = time.monotonic()
    try:
        for node_id, created in ((7, 1.), (8, 2.), (9, 3.)):
            runtime._remember_parent_pressure_node(node_id, key, created)
        runtime._prefetch_service_leases = {
            str(index): _PrefetchServiceLease(
                key, str(index), node_id, created, "join_ticket", None,
                now, now + 10., (("kv", 4096),),
            )
            for index, (node_id, created) in enumerate(((7, 1.), (7, 1.), (8, 1.)))
        }
        with patch.object(runtime, "_refresh_prefetch_service_leases"), patch.object(
            runtime, "_live_parent_pressure_key", return_value=True,
        ):
            runtime._prune_parent_pressure_candidates()
            runtime._prune_parent_pressure_candidates()
        assert runtime._parent_pressure_candidates == {8: (key, 2.), 9: (key, 3.)}
        assert runtime._parent_pressure_groups == {key: {8, 9}}
        assert runtime.counts["prefetch_residency_pressure_protected"] == 1
    finally:
        runtime.close()


def test_pressure_groups_rebuild_for_replaced_or_extended_candidate_tables():
    runtime = final_stage_runtime()
    key = runtime.context_sessions["ctx-parent"]
    newer = replace(key, request_id="new-request", context_epoch=key.context_epoch + 1)
    try:
        runtime._remember_parent_pressure_node(7, key, 1.)
        runtime._parent_pressure_candidates = {8: (newer, 2.)}
        with patch.object(runtime, "_live_parent_pressure_key", return_value=True):
            runtime._prune_parent_pressure_candidates()
            runtime._parent_pressure_candidates[9] = (key, 3.)
            runtime._prune_parent_pressure_candidates()
        assert runtime._parent_pressure_groups == {newer: {8}, key: {9}}
    finally:
        runtime.close()
