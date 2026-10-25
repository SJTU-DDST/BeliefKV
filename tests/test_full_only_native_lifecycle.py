"""Real FULL-only CPU tree/allocator lifecycle, with native copy queue stand-ins."""

from array import array
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_physical import inspect_session_h2d_opportunity
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_observer import HostPoolGroup, MHATokenToKVPoolHost


@pytest.fixture
def full_native(monkeypatch, request):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the qualified native engine")
    checkout = Path(checkout)
    monkeypatch.syspath_prepend(str(checkout / "python"))
    monkeypatch.syspath_prepend(str(checkout / "test/srt"))
    import test_beliefkv_terminal_capacity as fixtures
    from test_beliefkv_cold_full_prepare import install_production_cpu_controller

    cache = fixtures.cache.__wrapped__(request)
    allocator = cache.token_to_kv_pool_allocator
    host = MHATokenToKVPoolHost(
        size=2_000_000, size_per_token=64, device_pool=allocator._kvcache,
        available_size=lambda: 2_000_000,
    )
    entry = NS(name="kv", host_pool=host, device_pool=allocator._kvcache)
    group = HostPoolGroup(
        entry_map={"kv": entry}, anchor_entry=entry, size_per_token=64,
        available_size=host.available_size,
    )
    cache.host_pool_group = group
    cache.is_write_back = True
    cache.host_memory_mode = "cache"
    cache.ongoing_write_through = {}
    cache.load_back_threshold = 10
    cache.beliefkv_join_pressure_validator = lambda *_: False
    cache.cache_controller = NS(
        mem_pool_host=group, write_policy="write_back",
        write_queue=[], load_queue=[], ack_write_queue=[], ack_load_queue=[],
        load_fence_stream=object(), resume_beliefkv_loading=Mock(),
    )
    writes, commits = install_production_cpu_controller(cache)
    loads = []

    def load(host_indices, **kwargs):
        assert kwargs["extra_pools"] is None
        assert kwargs["beliefkv_no_device_reclaim"] is (
            kwargs["beliefkv_command_id"] is not None
        )
        operation = NS(
            node_id=kwargs["node_id"], host_indices=host_indices.clone(),
            beliefkv_command_id=kwargs["beliefkv_command_id"],
        )
        before_enqueue = kwargs["beliefkv_before_enqueue"]
        if before_enqueue is not None and not before_enqueue(operation):
            return None
        indices = allocator.alloc(len(host_indices))
        assert indices is not None
        loads.append(operation)
        cache.cache_controller.load_queue.append(operation)
        return indices

    def start_loading(**kwargs):
        assert kwargs.get("beliefkv_prefetch_tier", "complete") in (
            "complete", "eager", "pipeline",
        )
        queue = cache.cache_controller.load_queue
        cache.cache_controller.ack_load_queue.append(NS(
            node_ids=[op.node_id for op in queue],
            finish_event=NS(synchronize=Mock()), timing_enabled=False,
            num_tokens_by_pool={"kv": sum(len(op.host_indices) for op in queue)},
            num_bytes=sum(len(op.host_indices) for op in queue) * 64,
            submit_ts_ms=None, submit_mono=None, unacked_bytes_at_submit=0,
        ))
        queue.clear()
        return 0

    cache.cache_controller.load = Mock(side_effect=load)
    cache.cache_controller.start_loading = Mock(side_effect=start_loading)
    return cache, allocator, writes, loads, commits


def anchors(cache, generation, *, input_tokens):
    key = PrefillCandidateKey("r", "w", "i", "c", 0, 1, "parent", generation)
    return NativeAdmissionRuntime._snapshot_key_session_anchors(
        cache, key, reusable_input_tokens=input_tokens,
    )


@pytest.mark.parametrize("tier", ("complete", "eager", "pipeline"))
def test_full_only_spawn_growth_ack_reclaim_and_ordered_restore(full_native, tier):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, commits = full_native
    first, generation = insert_session(cache, [1, 2, 3, 4], "parent")
    free = allocator.available_size()
    assert cache.prepare_beliefkv_spawn_full(
        session_id="parent", session_generation=generation,
    )["issued"] == ((first.id, first.creation_time, 4),)
    assert allocator.available_size() == free
    assert not commits
    assert first.write_through_pending_id == first.id
    cache.writing_check(finish_count=1)
    assert first.write_through_pending_id is None
    old_host = first.component_data[0].host_value

    tail, _ = insert_session(cache, list(range(1, 9)), "parent")
    free = allocator.available_size()
    cache.beliefkv_cold_full_owner_validator = (
        lambda owners: owners == (("parent", generation),)
    )
    assert cache.prepare_beliefkv_spawn_full(
        session_id="parent", session_generation=generation,
    )["issued"] == ((tail.id, tail.creation_time, 4),)
    assert [len(op.device_indices) for op in writes] == [4, 4]
    assert first.component_data[0].host_value is old_host
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=free + 8, mamba_slots=0,
    ) == {}
    cache.writing_check(finish_count=1)
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=free + 8, mamba_slots=0,
    ) == {0: 8}
    assert allocator.available_size() == free + 8
    assert first.component_data[0].value is tail.component_data[0].value is None

    snapshot = anchors(cache, generation, input_tokens=8)
    assert snapshot is not None and len(snapshot.component_leaves) == 1
    plan = inspect_session_h2d_opportunity(
        cache, snapshot, max_steps=8, include_checkpoint_plan=True,
    )
    assert plan.headroom.observable
    assert plan.required_full_tokens == 8 and plan.required_mamba_slots == 0
    assert [step.node_id for step in plan.steps] == [first.id, tail.id]
    assert all(not step.include_mamba for step in plan.steps)
    issued = cache.prefetch_gpu_session_nodes(
        session_id="parent", session_generation=generation,
        leaf_node_id=plan.steps[0].leaf_node_id,
        leaf_creation_time=plan.steps[0].leaf_creation_time,
        nodes=tuple(
            (step.node_id, step.creation_time, step.include_mamba, f"h2d-{i}")
            for i, step in enumerate(plan.steps)
        ),
        beliefkv_before_enqueue=lambda operation: True, restore_tier=tier,
    )
    assert len(issued) == 2 and all(item.issued and item.load_started for item in issued)
    assert [op.node_id for op in loads] == [first.id, tail.id]
    assert allocator.available_size() == free
    assert first.load_back_pending_id is not None
    assert tail.load_back_pending_id is not None
    assert inspect_session_h2d_opportunity(cache, snapshot).step is None
    cache.loading_check(finish_count=1)
    assert not cache.ongoing_load_back
    assert first.load_back_pending_id is tail.load_back_pending_id is None
    assert all(node.component_data[0].host_lock_ref == 0 for node in (first, tail))
    observed = cache.inspect_beliefkv_reentry(NS(
        session_id="parent", session_generation=generation,
        origin_input_ids=array("q", range(1, 10)), extra_key=None, cache_salt=None,
    ))
    assert observed["device_checkpoint_tokens"] == 8
    assert observed["missing_full_tokens"] == observed["missing_mamba_slots"] == 0
    assert [(commit.direction, dict(commit.num_tokens_by_pool)) for commit in commits] == [
        ("d2h", {"kv": 4}), ("d2h", {"kv": 4}), ("h2d", {"kv": 8}),
    ]
    assert inspect_session_h2d_opportunity(
        cache, snapshot, skip_empty_restore=True,
    ).no_step_reason == "no_host_backed_missing_copy"


def test_full_only_restore_input_limit_and_generation_are_rechecked(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, loads, _ = full_native
    leaf, generation = insert_session(cache, list(range(1, 9)), "parent")
    assert cache.prepare_beliefkv_spawn_full(
        session_id="parent", session_generation=generation,
    )["issued"]
    cache.writing_check(finish_count=1)
    free = allocator.available_size()
    cache.beliefkv_cold_full_owner_validator = lambda owners: True
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=free + 8, mamba_slots=0,
    ) == {0: 8}
    snapshot = anchors(cache, generation, input_tokens=4)
    plan = inspect_session_h2d_opportunity(
        cache, snapshot, max_steps=8, include_checkpoint_plan=True,
    )
    assert plan.required_full_tokens == 0
    assert plan.step is None
    stale = replace(snapshot, key=replace(snapshot.key, session_generation=generation + 1))
    assert inspect_session_h2d_opportunity(cache, stale).step is None
    assert not cache.prefetch_gpu_session_node(
        session_id="parent", session_generation=generation + 1,
        leaf_node_id=leaf.id, leaf_creation_time=float(leaf.creation_time),
        node_id=leaf.id, node_creation_time=float(leaf.creation_time),
        beliefkv_command_id="stale", beliefkv_before_enqueue=lambda operation: True,
        beliefkv_include_mamba=False,
    ).issued
    assert loads == []
    assert allocator.available_size() == free + 8


def test_full_only_enqueue_refusal_and_pending_ack_cannot_leak_or_duplicate(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, loads, commits = full_native
    leaf, generation = insert_session(cache, [1, 2, 3, 4], "parent")
    assert cache.prepare_beliefkv_spawn_full(
        session_id="parent", session_generation=generation,
    )["issued"]
    cache.writing_check(finish_count=1)
    cache.beliefkv_cold_full_owner_validator = lambda owners: True
    free = allocator.available_size()
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=free + 4, mamba_slots=0,
    ) == {0: 4}
    free = allocator.available_size()
    arguments = dict(
        session_id="parent", session_generation=generation,
        leaf_node_id=leaf.id, leaf_creation_time=float(leaf.creation_time),
        node_id=leaf.id, node_creation_time=float(leaf.creation_time),
        beliefkv_command_id="restore", beliefkv_include_mamba=False,
    )
    assert not cache.prefetch_gpu_session_node(
        **arguments, beliefkv_before_enqueue=lambda operation: False,
    ).issued
    full = leaf.component_data[0]
    assert allocator.available_size() == free
    assert full.value is None and full.host_value is not None
    assert full.lock_ref == full.host_lock_ref == 0
    assert not cache.ongoing_load_back
    assert loads == []
    assert cache.prefetch_gpu_session_node(
        **arguments, beliefkv_before_enqueue=lambda operation: True,
    ).issued
    assert allocator.available_size() == free - 4
    assert len(loads) == 1 and len(commits) == 1
    assert not cache.prefetch_gpu_session_node(
        **arguments, beliefkv_before_enqueue=lambda operation: True,
    ).issued
    assert len(loads) == 1
    cache.loading_check(finish_count=1)
    assert full.lock_ref == full.host_lock_ref == 0
    assert not cache.ongoing_load_back
    assert len(commits) == 2 and commits[-1].direction == "h2d"
