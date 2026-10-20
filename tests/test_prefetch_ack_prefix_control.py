"""Reuse locked ancestry only within one synchronized native ACK callback."""

from collections import Counter
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PrefetchLoadStep,
)
from tests.test_sglang_v0520_observer import (
    ComponentData, UnifiedTreeCore, _static_cache,
)
from tests.test_tool_predictive_transfers import tool_runtime


def ack_prefix_fixture(depth=8, *, budget_bytes=1024**3):
    runtime, hint = tool_runtime()
    key = hint.key
    reads, locks, releases = Counter(), [], []

    class UnifiedTreeNode(NS):
        @property
        def parent(self):
            reads["parent"] += 1
            return self.ancestor

    nodes = {}
    for index in range(depth + 1):
        full = range(4) if index else None
        state = (0,) if index == depth else None
        nodes[index] = UnifiedTreeNode(
            id=index, creation_time=float(index + 1),
            ancestor=nodes.get(index - 1), key=range(4) if index else (),
            write_through_pending_id=None, load_back_pending_id=None,
            component_data={
                component: ComponentData(
                    value=value, host_value=value, lock_ref=0, host_lock_ref=0,
                    session_ref=1, session_ids={key.session_id},
                )
                for component, value in ((0, full), (2, state))
            },
        )

    def lookup(index):
        reads["node_lookup"] += 1
        return nodes[index]

    def lock(index):
        locks.append(index)
        return NS(to_dec_params=lambda: NS(node_id=index))

    def release(index, receipt):
        assert receipt.node_id == index
        releases.append(index)

    cache = _static_cache()
    size = 4 * (depth + 4)
    full = cache.token_to_kv_pool_allocator._kvcache.full_kv_pool
    cache.token_to_kv_pool_allocator.size = full.size = size
    full.get_kv_size_bytes = lambda: (10 * (size + 1), 10 * (size + 1))
    cache.host_pool_group.entry_map["kv"].host_pool.size = size
    cache.session_refs = NS(
        _session_generations={key.session_id: key.session_generation},
        _closed_session_ids=set(),
    )
    cache.tree_core = UnifiedTreeCore(
        node_by_id=lookup, inc_lock_ref=lock, dec_lock_ref=release,
    )
    runtime.attach_native_cache(cache)
    runtime._residency_budget = PrefetchResidencyBudget(1, budget_bytes)
    return runtime, key, nodes, reads, locks, releases


def commit_prefix(runtime, key, nodes, *, prefix="restore", indices=None):
    indices = tuple(indices if indices is not None else range(1, max(nodes) + 1))
    leaf = nodes[max(indices)]
    children = []
    units = Counter()
    for index in indices:
        node = nodes[index]
        state = node.component_data[2].value is not None
        command = f"{prefix}-{index}"
        step = PrefetchLoadStep(
            key, leaf.id, leaf.creation_time, index, node.creation_time, state,
        )
        amounts = (("kv", 4),) + ((("mamba", 1),) if state else ())
        pool_bytes = tuple((name, amount * (20 if name == "kv" else 80)) for name, amount in amounts)
        size = sum(amount for _, amount in pool_bytes)
        runtime.register_physical_action(PhysicalActionExpectation(
            command, "PREFETCH_GPU", key.context_id, key.context_epoch,
            (PhysicalChildExpectation(
                index, (index,), pool_bytes, size, anchor_creation_time=node.creation_time,
            ),),
            (("kv", 20), ("mamba", 80)), key.session_id, key.session_generation,
        ))
        runtime._prefetch_steps[command] = (step, "tool_wait", None)
        children.append(NS(
            command_id=command, anchor_node_id=index, published_node_ids=(index,),
            num_tokens_by_pool=amounts, num_bytes=size,
        ))
        units.update(dict(amounts))
    return runtime.on_native_transfer_commit(NS(
        direction="h2d", status="completed", node_ids=indices,
        num_tokens_by_pool=tuple(units.items()), child_commits=tuple(children),
    ))


@pytest.mark.parametrize("depth", (1, 2, 8, 34, 64))
def test_merged_ack_keeps_all_receipts_with_one_descendant_lock(depth):
    runtime, key, nodes, reads, locks, releases = ack_prefix_fixture(depth)
    try:
        completed = commit_prefix(runtime, key, nodes)
        assert len(completed) == depth
        assert len(runtime._prefetch_service_leases) == depth
        assert locks == [depth]
        assert releases == []
        assert sum(
            lease.lock_params is not None for lease in runtime._prefetch_service_leases.values()
        ) == 1
        assert runtime._prefetch_service_leases[f"restore-{depth}"].protected_bytes == depth * 80 + 80
        assert not runtime.physical_ledger.pending_count
        assert not runtime.physical_disabled
        for command in tuple(runtime._prefetch_service_leases):
            runtime._release_prefetch_service_lease(command, "first_gpu_service")
        assert releases == [depth]
    finally:
        runtime.close()


def test_locked_prefix_membership_is_shared_only_inside_the_same_ack_callback():
    runtime, key, nodes, _, locks, _ = ack_prefix_fixture(8)
    observed = []
    original = runtime._register_prefetch_service_lease

    def register(action, **kwargs):
        observed.append(kwargs["_locked_prefixes"])
        return original(action, **kwargs)

    try:
        with patch.object(runtime, "_register_prefetch_service_lease", side_effect=register):
            commit_prefix(runtime, key, nodes, indices=(1, 2, 3, 4))
            first = observed[:]
            observed.clear()
            commit_prefix(runtime, key, nodes, prefix="next", indices=(5, 6, 7, 8))
            second = observed[:]
        assert all(item is first[0] for item in first)
        assert all(item is second[0] for item in second)
        assert first[0] is not second[0]
        assert set(first[0]) == {"restore-4"}
        assert set(second[0]) == {"next-8"}
        assert locks == [4, 8]
        assert runtime._prefetch_service_leases["restore-4"].lock_params is None
        assert runtime._prefetch_service_leases["next-8"].lock_params.node_id == 8
    finally:
        runtime.close()


def test_descendant_protection_does_not_cover_a_sibling_branch():
    runtime, key, nodes, _, locks, _ = ack_prefix_fixture(8)
    nodes[5].ancestor = nodes[3]
    try:
        commit_prefix(runtime, key, nodes)
        assert locks == [8, 4]
        assert runtime._prefetch_service_leases["restore-8"].lock_params.node_id == 8
        assert runtime._prefetch_service_leases["restore-4"].lock_params.node_id == 4
        assert runtime._prefetch_service_leases["restore-3"].lock_params is None
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", ("generation", "residency", "terminal", "session"))
def test_shared_prefix_does_not_bypass_each_targets_live_validation(fault):
    runtime, key, nodes, _, locks, _ = ack_prefix_fixture(8)
    original = runtime._register_prefetch_service_lease

    def register(action, **kwargs):
        if action.command_id == "restore-4":
            if fault == "generation":
                nodes[4].creation_time += 100.
            elif fault == "residency":
                nodes[4].component_data[0].value = None
            elif fault == "terminal":
                from beliefkv.control.causal_graph import InvocationState
                runtime.graph.invocations[key.invocation_id].state = InvocationState.DONE
            else:
                runtime._native_cache.session_refs._session_generations[key.session_id] += 1
        return original(action, **kwargs)

    try:
        with patch.object(runtime, "_register_prefetch_service_lease", side_effect=register):
            commit_prefix(runtime, key, nodes)
        assert "restore-4" not in runtime._prefetch_service_leases
        assert locks == [8]
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_lack_of_protection_budget_does_not_create_a_cached_lock_or_lose_ack_credit():
    runtime, key, nodes, _, locks, _ = ack_prefix_fixture(8, budget_bytes=1)
    observed = []
    original = runtime._register_prefetch_service_lease

    def register(action, **kwargs):
        observed.append(kwargs["_locked_prefixes"])
        return original(action, **kwargs)

    try:
        with patch.object(runtime, "_register_prefetch_service_lease", side_effect=register):
            completed = commit_prefix(runtime, key, nodes)
        assert len(completed) == 8
        assert len(runtime._prefetch_service_leases) == 8
        assert not locks
        assert all(item == {} for item in observed)
        assert all(lease.lock_params is None for lease in runtime._prefetch_service_leases.values())
        assert runtime.counts["native_physical_completed"] == 8
        assert not runtime.physical_disabled
    finally:
        runtime.close()
