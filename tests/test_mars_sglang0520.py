from array import array
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from beliefkv.experiments.paper_mars_policy import Request, author_scheduler
from beliefkv.experiments.paper_mars_sglang import MarsNativeSchedule, NativeSlots


CHECKOUT = Path(__file__).resolve().parents[2] / "references/MARS-official-20261123"


class BlockBudget:
    def __init__(self):
        self.block = SimpleNamespace(get_block_ids=lambda: ())
        self.attempts, self.victims = [], []
        self.allow = lambda _request, _count: True

    def allocate_slots(self, req, count, *_args, **_kwargs):
        self.attempts.append((req.request_id, count))
        return self.block if self.allow(req, count) else None

    def free(self, req):
        self.victims.append(req.request_id)

    def create_empty_block_list(self):
        return self.block

    def get_computed_blocks(self, _req):
        return self.block, 0

    def get_blocks(self, _rid):
        return self.block

    def get_num_common_prefix_blocks(self, *_args):
        return [0]

    def take_events(self):
        return None


def request(rid, length, order):
    return Request(SimpleNamespace(rid=rid, origin_input_ids=range(length), output_ids=[]), order)


def scheduler():
    if not CHECKOUT.is_dir():
        pytest.skip("pinned MARS author checkout unavailable")
    cache = BlockBudget()
    return author_scheduler(CHECKOUT)(
        cache, block_size=16, max_tokens=2048, max_reqs=24, max_model_len=262144,
    ), cache


def test_author_waiting_levels_overflow_and_feedback():
    policy, _ = scheduler()
    long = request("long", 33000, 0)
    policy.add_request(long)
    for index in range(29):
        policy.add_request(request(f"short-{index}", 32, index + 1))
    overflow = request("overflow", 16, 30)
    policy.add_request(overflow)
    assert list(policy._mars_overflow) == [overflow]
    assert len(policy.waiting) == 30

    first = policy.schedule()
    assert list(first.num_scheduled_tokens) == [f"short-{i}" for i in range(24)]
    assert all(count == 32 for count in first.num_scheduled_tokens.values())

    for req in tuple(policy.running):
        policy.retire(req)
    second = policy.schedule()
    assert list(second.num_scheduled_tokens) == [
        *(f"short-{i}" for i in range(24, 29)), "overflow", "long",
    ]
    assert second.num_scheduled_tokens["long"] == 1872
    final = policy.schedule()
    assert final.num_scheduled_tokens == {"long": 512}
    assert policy._mars_level_by_req_id["long"] == 1


def test_author_shrinks_then_reclaims_lowest_priority_victim():
    policy, cache = scheduler()
    short = request("short", 32, 0)
    long = request("long", 40000, 1)
    policy.add_request(short)
    policy.add_request(long)
    policy.schedule()
    short.native.output_ids.append(1)
    # The next decode can use a slot once MARS has displaced the long prefill.
    cache.allow = lambda req, count: (
        req is short and bool(cache.victims) or req is long and count <= 16
    )
    cache.attempts.clear()

    choice = policy.schedule()
    assert cache.victims == ["long"]
    assert choice.num_scheduled_tokens == {"short": 1}
    assert policy.waiting.peek_request() is long
    assert policy._mars_level_by_req_id["long"] == long.num_computed_tokens == 0

    policy.retire(short)
    cache.attempts.clear()
    choice = policy.schedule()
    assert choice.num_scheduled_tokens == {"long": 16}
    assert cache.attempts == [("long", size) for size in (2048, 1024, 512, 256, 128, 64, 32, 16)]


def test_native_slots_protect_prefix_and_charge_actual_page_growth():
    class Tree:
        free, evictable = 16, 48

        def inc_lock_ref(self, node):
            self.evictable -= node

        def dec_lock_ref(self, node):
            self.evictable += node

        def evictable_size(self):
            return self.evictable

        def evict(self, args):
            self.free += args.num_tokens
            self.evictable -= args.num_tokens

    tree = Tree()
    slots = NativeSlots(SimpleNamespace(
        tree_cache=tree, token_to_kv_pool_allocator=SimpleNamespace(available_size=lambda: tree.free),
        req_to_token_pool=SimpleNamespace(available_size=lambda: 2),
    ), SimpleNamespace, None)
    req = request("waiting", 60, 0)
    req.native.kv = SimpleNamespace(holds_kv=False)
    req.native.last_node = 32
    assert slots.allocate_slots(req, 33, 32, num_lookahead_tokens=0) is None
    assert tree.evictable == 48
    assert slots.allocate_slots(req, 17, 32, num_lookahead_tokens=0) is slots.blocks
    assert (slots.reserved_tokens, slots.reserved_rows, tree.free, tree.evictable) == (32, 1, 32, 0)

    resident = request("resident", 70, 1)
    resident.native.kv = SimpleNamespace(holds_kv=True)
    resident.num_computed_tokens = 17
    assert slots.allocate_slots(resident, 15, num_lookahead_tokens=0) is slots.blocks
    assert slots.reserved_tokens == 32


@pytest.fixture
def native_scheduler(monkeypatch):
    checkout = os.environ.get("MARS_SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set MARS_SGLANG_SOURCE_CHECKOUT to the ported native source")
    import torch
    from sglang.srt.managers.overlap_utils import FutureMap
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.mem_cache.allocator.paged import PagedTokenToKVPoolAllocator
    from sglang.srt.mem_cache.cache_init_params import CacheInitParams
    from sglang.srt.mem_cache.memory_pool import ReqToTokenPool
    from sglang.srt.mem_cache.registry import TreeCacheBuildContext, create_tree_cache
    from sglang.srt.sampling.sampling_params import SamplingParams
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler
    from sglang.srt.speculative.spec_info import SpeculativeAlgorithm

    assert Path(__import__("sglang").__file__).resolve().is_relative_to(Path(checkout).resolve())
    server_args = ServerArgs(model_path="dummy", attention_backend="torch_native", page_size=16)
    set_global_server_args_for_scheduler(server_args)
    monkeypatch.setenv("BELIEFKV_MARS_AUTHOR_SOURCE", str(CHECKOUT))

    # CPU stand-ins for the two paged allocator kernels. Row allocation,
    # physical page free, cache locks, and forward inputs use the native code.
    def extend(allocator, _prefix, prefix_cpu, _seq, seq_cpu, last, _count, **_kwargs):
        out = []
        for start, end, loc in zip(prefix_cpu.tolist(), seq_cpu.tolist(), last.tolist(), strict=True):
            use_tail = min((-start) % 16, end - start)
            out.extend(range(loc + 1, loc + 1 + use_tail))
            extra = end - start - use_tail
            new = allocator.alloc(((extra + 15) // 16) * 16)
            assert new is not None, "batch exceeds the staged page reservations"
            out.extend(new[:extra].tolist())
        return torch.tensor(out, dtype=torch.int64)

    def decode(allocator, seq, seq_cpu, last):
        return extend(allocator, None, seq_cpu - 1, seq, seq_cpu, last, len(last))

    monkeypatch.setattr(PagedTokenToKVPoolAllocator, "alloc_extend", extend)
    monkeypatch.setattr(PagedTokenToKVPoolAllocator, "alloc_decode", decode)

    def build(capacity):
        rows = ReqToTokenPool(24, 41000, device="cpu", enable_memory_saver=False)
        alloc = PagedTokenToKVPoolAllocator(capacity, 16, torch.float32, "cpu", None, False)
        model = SimpleNamespace(is_encoder_decoder=False, vocab_size=128)
        tree = create_tree_cache(TreeCacheBuildContext(
            server_args=server_args, params=CacheInitParams(False, rows, alloc, page_size=16),
            is_hybrid_swa=False, is_hybrid_ssm=False, enable_hierarchical_cache=False,
            disable_radix_cache=False, effective_chunked_prefill_size=2048,
            tp_worker=None, model_config=model, tp_size=1, tp_rank=0, tp_group=None,
        ))
        spec = SpeculativeAlgorithm.NONE
        sink = SimpleNamespace(send_output=lambda message, req: aborted.append((message, req.rid)))
        aborted = []
        scheduler = SimpleNamespace(
            enable_overlap=False, ps=SimpleNamespace(tp_size=1, pp_size=1, dp_size=1),
            spec_algorithm=spec, disaggregation_mode=SimpleNamespace(value="null"),
            tree_cache=tree, token_to_kv_pool_allocator=alloc, req_to_token_pool=rows,
            page_size=16, max_prefill_tokens=2048, chunked_prefill_size=2048,
            max_running_requests=24, max_req_len=40960, waiting_queue=[],
            model_config=model,
            future_map=FutureMap(torch.device("cpu"), spec, rows),
            ipc_channels=SimpleNamespace(send_to_tokenizer=sink),
        )
        return MarsNativeSchedule(scheduler, CHECKOUT), aborted

    def make_request(rid, ids):
        sampling = SamplingParams(max_new_tokens=16, temperature=0, repetition_penalty=1.05)
        req = Req(rid, "", array("q", ids), sampling, vocab_size=128)
        req.time_stats.set_scheduler_recv_time()
        return req

    return build, make_request


def _next(native, last=None):
    return native.next_batch(
        abort_message=lambda req: req.rid, set_schedule_time=lambda _batch: None, last_batch=last,
    )[0]


def _finish_cpu_step(native, batch, token):
    import torch
    from sglang.srt.managers.overlap_utils import RelayPayload, resolve_forward_inputs

    resolve_forward_inputs(batch, native.scheduler.future_map)
    produced = torch.tensor([token] * len(batch.reqs), dtype=torch.int64)
    native.scheduler.future_map.stash(batch.req_pool_indices, RelayPayload(bonus_tokens=produced))
    for req in batch.reqs:
        if req._paper_mars_middle_chunk:
            assert req.inflight_middle_chunks == 1
            req.inflight_middle_chunks -= 1
        else:
            req.output_ids.append(token)
            if not batch.forward_mode.is_decode() and req not in (batch.decoding_reqs or ()):
                native.slots.tree.cache_unfinished_req(req)


def test_native_mixed_batch_resolves_two_partial_prefills_and_decode(native_scheduler):
    from sglang.srt.managers.schedule_batch import FINISH_ABORT
    from sglang.srt.sampling.penaltylib import BatchedRepetitionPenalizer

    build, make_request = native_scheduler
    native, aborted = build(8192)
    short = make_request("decode", [1] * 31)
    first = make_request("first", [2] * 40000)
    second = make_request("second", [3] * 40000)
    native.scheduler.waiting_queue.extend((short, first))

    initial = _next(native)
    assert initial.extend_lens == [31, 2017]
    _finish_cpu_step(native, initial, 81)
    native.scheduler.waiting_queue.append(second)

    mixed = _next(native, initial)
    assert mixed.forward_mode.is_mixed()
    assert mixed.reqs == [first, second, short]
    assert mixed.extend_lens == [256, 1791, 1]
    assert mixed.prefix_lens == [2017, 0, 31]
    _finish_cpu_step(native, mixed, 82)
    assert mixed.input_ids.tolist() == [2] * 256 + [3] * 1791 + [81]

    first.to_finish = second.to_finish = FINISH_ABORT()
    continuation = make_request("reuse", [1] * 31 + [81, 5])
    native.scheduler.waiting_queue.append(continuation)
    reused = _next(native, mixed)
    assert reused.reqs == [continuation, short]
    assert reused.prefix_lens == [32, 32]
    assert set(aborted) == {("first", "first"), ("second", "second")}
    penalty = reused.sampling_info.penalizer_orchestrator.penalizers[BatchedRepetitionPenalizer]
    assert penalty.get_scaling_penalties()[1, [81, 82]].tolist() == pytest.approx([1.05, 1.05])
    assert penalty.get_scaling_penalties()[0, [81, 82]].tolist() == [1., 1.]
    _finish_cpu_step(native, reused, 83)
    assert reused.input_ids.tolist() == [5, 82]
    continuation.to_finish = FINISH_ABORT()
    decode = _next(native, reused)
    assert decode.reqs == [short] and decode.forward_mode.is_decode()
    penalty = decode.sampling_info.penalizer_orchestrator.penalizers[BatchedRepetitionPenalizer]
    assert penalty.get_scaling_penalties()[0, [81, 82, 83]].tolist() == pytest.approx([1.05] * 3)
    _finish_cpu_step(native, decode, 84)
    short.to_finish = FINISH_ABORT()
    assert _next(native, decode) is None
    assert native.scheduler.req_to_token_pool.available_size() == 24
    assert native.stats()["free_tokens"] == 8192


def test_native_author_victim_reclaims_only_the_needed_page(native_scheduler):
    from sglang.srt.managers.schedule_batch import FINISH_ABORT

    build, make_request = native_scheduler
    native, _ = build(2048)
    short = make_request("decode", [1] * 32)
    long = make_request("victim", [2] * 40000)
    native.scheduler.waiting_queue.extend((short, long))
    initial = _next(native)
    assert initial.extend_lens == [32, 2016]
    _finish_cpu_step(native, initial, 81)

    step = _next(native, initial)
    assert step.reqs == [short] and step.forward_mode.is_decode()
    assert native.scheduler.waiting_queue == [long]
    assert long.retraction_count == 1 and not long.kv.holds_kv
    assert native.slots.tree.evictable_size() == 2000
    _finish_cpu_step(native, step, 82)

    short.to_finish = FINISH_ABORT()
    resumed = _next(native, step)
    assert resumed.reqs == [long]
    assert resumed.prefix_lens == [2000]
    assert resumed.extend_lens == [32]
    _finish_cpu_step(native, resumed, 83)
    assert resumed.input_ids.tolist() == [2] * 32
