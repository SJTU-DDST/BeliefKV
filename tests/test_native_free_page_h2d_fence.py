"""Actual native allocator/controller code with CPU tensors and stream-order stand-ins."""

import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import torch


class Stream:
    def __init__(self, clock=0.):
        self.clock = clock
        self.events = []
        self.streams = []

    def wait_event(self, event):
        self.events.append(event)
        self.clock = max(self.clock, event.clock)

    def wait_stream(self, stream):
        self.streams.append(stream)
        self.clock = max(self.clock, stream.clock)


class Device:
    def __init__(self):
        self.current = Stream()

    def Event(self):
        device = self

        class Event:
            clock = 0.

            def record(self, stream=None):
                self.clock = (stream or device.current).clock

            def query(self):
                return True

        return Event()


@pytest.fixture
def native(monkeypatch):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the free-page fence candidate engine")
    monkeypatch.syspath_prepend(str(Path(checkout) / "python"))
    from sglang.srt.managers import cache_controller
    from sglang.srt.mem_cache.allocator.token import TokenToKVPoolAllocator
    from sglang.srt.mem_cache.hybrid_cache.hybrid_cache_controller import HybridCacheController

    assert hasattr(cache_controller.HiCacheController, "beliefkv_record_free_page_boundary")
    return NS(module=cache_controller, allocator=TokenToKVPoolAllocator, hybrid=HybridCacheController)


def fixture(native, monkeypatch, *, enabled=True, hybrid=False, need_sort=False, supported=True):
    monkeypatch.setenv("BELIEFKV_FREE_PAGE_H2D_FENCE", "1" if enabled else "0")
    device, forward, copy = Device(), Stream(10.), Stream()
    pool = NS(device="cpu", layer_num=2, register_layer_transfer_counter=Mock())
    allocator = native.allocator(16, torch.float32, "cpu", pool, need_sort=need_sort)
    last_use = {}
    memory, host = torch.full((2, 17), -1.), torch.arange(34.).reshape(2, 17)
    operations = []
    entry = NS(name=native.module.PoolName.KV, host_pool=NS(size_per_token=8))
    host_pool = NS(
        size_per_token=8, anchor_entry=entry,
        entry_map={native.module.PoolName.KV: entry},
    )

    def submit(transfers, *, start_event, on_layer_done=None, layer_num=2, **kwargs):
        copy.wait_event(start_event)
        operation = operations[-1]
        assert copy.clock >= max((last_use.get(index, 0.) for index in operation.device_indices.tolist()), default=0.)
        begin = device.Event()
        begin.record(copy)
        memory[:, operation.device_indices] = host[:, operation.host_indices]
        previous = device.current
        device.current = copy
        for layer in range(layer_num):
            copy.clock += 10.
            if on_layer_done is not None:
                on_layer_done(layer)
        finish = device.Event()
        finish.record(copy)
        device.current = previous
        return NS(start_event=begin, finish_event=finish, timing_enabled=False)

    engine = NS(host_to_device_stream=copy, submit_host_to_device=submit)
    monkeypatch.setattr(native.module, "device_module", device)
    monkeypatch.setattr(native.module, "L2TransferEngine", lambda _: engine)
    used_allocator = allocator if supported else NS(
        get_kvcache=lambda: pool, alloc=allocator.alloc, free=allocator.free,
    )
    cls = native.hybrid if hybrid else native.module.HiCacheController
    controller = cls(
        used_allocator, host_pool, 1, None, None, io_backend="direct",
    )
    controller.load_fence_stream = forward

    def move(operation):
        operations.append(operation)
        return operation.host_indices, operation.device_indices, operation.pool_transfers

    controller._move_op_indices = move
    controller._l2_load_transfers = lambda *_: ()
    return NS(
        controller=controller, allocator=allocator, forward=forward, copy=copy,
        memory=memory, host=host, last_use=last_use, device=device, operations=operations,
    )


def load(live, *, command="prefetch", count=2, callback=None):
    options = {"beliefkv_command_id": command, "node_id": len(live.controller.load_queue) + 1}
    if callback is not None:
        options["beliefkv_before_enqueue"] = callback
    return live.controller.load(torch.arange(1, count + 1), **options)


@pytest.mark.parametrize("hybrid", (False, True))
@pytest.mark.parametrize("need_sort", (False, True))
def test_preexisting_free_pages_overlap_the_current_forward_and_keep_native_receipts(
    native, monkeypatch, hybrid, need_sort,
):
    live = fixture(native, monkeypatch, hybrid=hybrid, need_sort=need_sort)
    assert live.controller.beliefkv_record_free_page_boundary()
    fence = live.controller._beliefkv_free_page_boundary[2]
    live.forward.clock = 110.
    indices = load(live)
    assert live.controller.start_loading() == 0
    assert not live.copy.streams and fence in live.copy.events
    assert live.copy.clock == 30. < live.forward.clock
    assert torch.equal(live.memory[:, indices], live.host[:, 1:3])
    ack = live.controller.ack_load_queue[0]
    assert ack.num_bytes == 16 and ack.num_tokens == 2
    assert ack.child_receipts[0].command_id == "prefetch"
    assert live.controller.beliefkv_h2d_fence_counts["free_event_batches"] == 1


@pytest.mark.parametrize("enabled,supported", ((False, True), (True, False)))
def test_disabled_or_unsupported_allocators_preserve_the_original_forward_fence(
    native, monkeypatch, enabled, supported,
):
    live = fixture(native, monkeypatch, enabled=enabled, supported=supported)
    assert not live.controller.beliefkv_record_free_page_boundary()
    live.forward.clock = 110.
    load(live)
    live.controller.start_loading()
    assert live.copy.streams == [live.forward]
    assert live.copy.clock == 130.
    assert not live.controller.beliefkv_h2d_fence_counts


@pytest.mark.parametrize("need_sort", (False, True))
@pytest.mark.parametrize("grouped", (False, True))
def test_pages_released_after_the_boundary_wait_for_the_actual_old_writer(
    native, monkeypatch, need_sort, grouped,
):
    live = fixture(native, monkeypatch, need_sort=need_sort)
    busy = live.allocator.alloc(16)
    live.controller.beliefkv_record_free_page_boundary()
    live.forward.clock = 110.
    live.last_use.update({index: 110. for index in busy[:2].tolist()})
    if grouped:
        live.allocator.free_group_begin()
    live.allocator.free(busy[:2])
    if grouped:
        live.allocator.free_group_end()
    restored = load(live)
    live.controller.start_loading()
    assert torch.equal(restored, busy[:2])
    assert live.copy.streams == [live.forward]
    assert live.copy.clock == 130.
    assert live.controller.beliefkv_h2d_fence_counts["forward_stream_batches"] == 1


def test_released_pages_can_use_a_new_boundary_after_their_previous_writer(native, monkeypatch):
    live = fixture(native, monkeypatch)
    busy = live.allocator.alloc(16)
    live.last_use.update({index: 10. for index in busy[:2].tolist()})
    live.allocator.free(busy[:2])
    live.controller.beliefkv_record_free_page_boundary()
    live.forward.clock = 110.
    load(live)
    live.controller.start_loading()
    assert not live.copy.streams and live.copy.clock == 30.


def test_delayed_group_publication_invalidates_a_boundary_recorded_with_pending_frees(native, monkeypatch):
    live = fixture(native, monkeypatch)
    busy = live.allocator.alloc(16)
    live.allocator.free_group_begin()
    live.allocator.free(busy[:2])
    live.controller.beliefkv_record_free_page_boundary()
    live.allocator.free_group_end()
    live.forward.clock = 110.
    load(live)
    live.controller.start_loading()
    assert live.copy.streams == [live.forward]


@pytest.mark.parametrize("same_boundary,native_peer", ((True, False), (False, False), (True, True)))
def test_merged_batch_uses_the_early_fence_only_for_one_common_proven_boundary(
    native, monkeypatch, same_boundary, native_peer,
):
    live = fixture(native, monkeypatch)
    live.controller.beliefkv_record_free_page_boundary()
    first = load(live, command=None if native_peer else "one")
    if not same_boundary:
        other = live.allocator.alloc(1)
        live.allocator.free(other)
        live.controller.beliefkv_record_free_page_boundary()
    second = load(live, command="two")
    live.forward.clock = 110.
    live.controller.start_loading()
    assert bool(live.copy.streams) is (not same_boundary)
    assert len(live.controller.ack_load_queue[0].node_ids) == 2
    assert torch.equal(live.memory[:, first], live.host[:, 1:3])
    assert torch.equal(live.memory[:, second], live.host[:, 1:3])


def test_native_demand_copy_can_use_the_same_proof_as_controlled_prefetch(native, monkeypatch):
    live = fixture(native, monkeypatch)
    live.controller.beliefkv_record_free_page_boundary()
    live.forward.clock = 110.
    indices = load(live, command=None)
    live.controller.start_loading()
    assert not live.copy.streams and live.copy.clock == 30.
    assert torch.equal(live.memory[:, indices], live.host[:, 1:3])
    assert not live.controller.ack_load_queue[0].child_receipts


@pytest.mark.parametrize("accepted", (False, True))
def test_hybrid_before_enqueue_can_invalidate_the_free_set_without_losing_ownership(
    native, monkeypatch, accepted,
):
    live = fixture(native, monkeypatch, hybrid=True)
    other = live.allocator.alloc(1)
    live.controller.beliefkv_record_free_page_boundary()

    def before_enqueue(_):
        live.allocator.free(other)
        return accepted

    before = live.allocator.available_size()
    indices = load(live, callback=before_enqueue)
    live.forward.clock = 110.
    if accepted:
        assert indices is not None
        live.controller.start_loading()
        assert live.copy.streams == [live.forward]
        assert live.allocator.available_size() == before - 1
    else:
        assert indices is None and not live.controller.load_queue
        assert live.allocator.available_size() == before + 1
        assert live.controller.start_loading() == -1


def test_pool_clear_cannot_reuse_an_old_free_generation_or_event(native, monkeypatch):
    live = fixture(native, monkeypatch)
    live.controller.beliefkv_record_free_page_boundary()
    generation = live.allocator.beliefkv_free_generation
    live.allocator.clear()
    assert live.allocator.beliefkv_free_generation > generation
    live.forward.clock = 110.
    load(live)
    live.controller.start_loading()
    assert live.copy.streams == [live.forward]


def test_free_boundary_event_is_not_recreated_on_each_decode_step(native, monkeypatch):
    live = fixture(native, monkeypatch)
    assert live.controller.beliefkv_record_free_page_boundary()
    original = live.controller._beliefkv_free_page_boundary[2]
    for _ in range(200):
        live.forward.clock += 1.
        assert not live.controller.beliefkv_record_free_page_boundary()
    assert live.controller._beliefkv_free_page_boundary[2] is original
    assert live.controller.beliefkv_h2d_fence_counts["free_boundary_events"] == 1
    busy = live.allocator.alloc(1)
    live.allocator.free(busy)
    assert live.controller.beliefkv_record_free_page_boundary()
    assert live.controller.beliefkv_h2d_fence_counts["free_boundary_events"] == 2


def test_capacity_failure_does_not_enqueue_or_publish_a_load(native, monkeypatch):
    live = fixture(native, monkeypatch)
    live.controller.beliefkv_record_free_page_boundary()
    assert load(live, count=17) is None
    assert live.controller.start_loading() == -1
    assert not live.controller.ack_load_queue and not live.copy.events


def test_extra_pool_added_after_allocation_still_uses_the_original_fence(native, monkeypatch):
    live = fixture(native, monkeypatch)
    live.controller.beliefkv_record_free_page_boundary()
    load(live)
    live.controller.load_queue[0].pool_transfers = [object()]
    live.forward.clock = 110.
    live.controller.start_loading()
    assert live.copy.streams == [live.forward]


@pytest.mark.parametrize("enabled,overlap,speculative,generation", (
    (True, True, False, True), (False, True, False, True),
    (True, False, False, True), (True, True, True, True), (True, True, False, False),
))
def test_actual_scheduler_binding_and_run_prelude_record_before_the_next_forward(
    native, monkeypatch, enabled, overlap, speculative, generation,
):
    live = fixture(native, monkeypatch, enabled=enabled)
    source = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]) / "python/sglang/srt/managers/scheduler.py"
    cls = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.ClassDef) and node.name == "Scheduler")
    constructor = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    branch = next(
        node for node in constructor.body if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute) and node.test.attr == "enable_hierarchical_cache"
        and "_beliefkv_load_free_boundary" in ast.unparse(node)
    )
    run = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "run_batch")
    namespace = {}
    exec("def bind(self):\n" + "\n".join("    " + line for line in ast.unparse(branch).splitlines()), namespace)
    exec("def launch(self):\n" + "\n".join("    " + line for line in ast.unparse(run.body[1]).splitlines()), namespace)
    scheduler = NS(
        enable_hierarchical_cache=True, tree_cache=NS(cache_controller=live.controller),
        tp_worker=NS(model_runner=NS(forward_stream=live.forward)), enable_overlap=overlap,
        spec_algorithm=NS(is_none=lambda: not speculative), is_generation=generation,
    )
    namespace["bind"](scheduler)
    namespace["launch"](scheduler)
    live.forward.clock = 110.
    expected = enabled and overlap and not speculative and generation
    assert bool(live.controller._beliefkv_free_page_boundary) is expected
    if expected:
        assert live.controller._beliefkv_free_page_boundary[2].clock == 10.
