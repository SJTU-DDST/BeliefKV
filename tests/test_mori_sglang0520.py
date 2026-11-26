"""CPU differential tests against the pinned MORI release.

Run with the pinned SGLang dependencies (or set MORI_TEST_PYTHON). Each
checkout gets its own process and imports its real radix, HTTP and IPC code.
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest


EXPERIMENT = Path(__file__).resolve().parents[2]
AUTHOR = "b4f6a1d50fe50a09b522f85333fa432871533690"
BASE = "c5ad39a1e1285532335139fa3bc33d49c7eabcf6"
TREE = "434c35dd66211d0a230717c88bf1452056769a40"


@pytest.fixture(scope="module")
def probes():
    author = Path(os.environ.get(
        "MORI_AUTHOR_CHECKOUT", EXPERIMENT / "references/MORI-official-20261123"
    ))
    port = Path(os.environ.get(
        "MORI_SGLANG_CHECKOUT", EXPERIMENT / ".worktrees/sglang-mori-paper-0520-20261123"
    ))
    if not author.is_dir() or not port.is_dir():
        pytest.skip("MORI author and port checkouts are required")

    def git(repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    assert git(author, "rev-parse", "HEAD") == AUTHOR
    assert not git(author, "status", "--porcelain", "--untracked-files=no", "sglang/python")
    assert git(port, "rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [BASE, TREE]

    results = {}
    python = os.environ.get("MORI_TEST_PYTHON", sys.executable)
    for name, path in (("author", author / "sglang"), ("port", port)):
        proc = subprocess.run(
            [python, str(Path(__file__).resolve()), name],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(map(str, (path / "python", EXPERIMENT / "BeliefKV"))),
                "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "",
                "ROCR_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1",
                "OMP_NUM_THREADS": "1", "HF_HUB_OFFLINE": "1",
                "SGLANG_USE_PICKLE_IPC": "0",
                "SGLANG_ENABLE_MORI": "1" if name == "port" else "0",
            },
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        line = next(x for x in proc.stdout.splitlines() if x.startswith("MORI_PROBE="))
        results[name] = json.loads(line.split("=", 1)[1])
    return results


@pytest.mark.parametrize("scenario", [
    "ownership", "lru", "lfu", "host_epoch", "host_pressure", "h2d", "http",
])
def test_matches_author(probes, scenario):
    assert probes["port"][scenario] == probes["author"][scenario]


def test_port_keeps_h2d_sources_through_splits_locks_and_pending_acks(probes):
    result = probes["port"]["pending_split"]
    assert result == {
        "before_ack_host": 20, "after_ack_host": 0, "after_ack_freed": 20,
        "before_ack_device_evicted": 0, "after_ack_request_protected": 20,
        "after_request_unlock_evictable": 20,
        "aborted_load_source_refs": 0, "aborted_load_host": 20,
    }


def test_port_preserves_stock_path_when_disabled(probes):
    assert probes["port"]["inactive"] == {"host_after_ack": 20, "disabled_route": 404}


def test_matched_write_back_waits_after_each_node_in_the_same_order(probes):
    assert probes["port"]["write_back_modes"] == {
        "author": [
            ["write", 10], ["write", 20], ["write_ack", 10], ["write_ack", 20],
            ["device_free", 10], ["device_free", 20],
        ],
        "matched": [
            ["write", 10], ["write_ack", 10], ["write", 20], ["write_ack", 20],
            ["device_free", 10], ["device_free", 20],
        ],
    }


def test_raw_generate_delivers_program_identity_to_native_req(probes):
    assert probes["port"]["generate"] == {
        "single": [["a", "tool-session", [1, 2]]],
        "batch": [["b", "other-session", [1, 2]], ["c", "other-session", [3, 4]]],
        "untyped": [["d", None, [1, 2]]],
    }
    assert probes["port"]["cancelled_control"] == ["Released first", "Released second"]
    assert probes["port"]["terminal"] == {
        "request_id": "a", "program_id": "tool-session", "output_token_ids": [9],
        "completion_boundary": "native_terminal_output_emit",
    }


class Event:
    def __init__(self, ready=False, on_wait=None):
        self.ready = ready
        self.on_wait = on_wait

    def query(self):
        return self.ready

    def synchronize(self):
        if self.on_wait is not None:
            self.on_wait()
            self.ready = True
        assert self.ready


class Ack(NS):
    def __iter__(self):
        yield None
        yield self.finish_event
        yield self.node_ids


class Controller:
    write_policy = "write_back"

    def __init__(self, torch, host_capacity):
        self.torch = torch
        self.host_capacity = host_capacity
        self.mem_pool_device_allocator = NS(
            free=self.free_device, available_size=lambda: 0, device="cpu",
            free_segments=lambda _: None, free_segment=lambda *a, **k: None,
        )
        self.reset()

    def reset(self):
        self.live_host = {}
        self.next_host = 1
        self.load_queue = []
        self.ack_load_queue = []
        self.ack_write_queue = []
        self.pending_writes = set()
        self.reject_load = False
        self.events = []

    def write(self, device_indices, node_id):
        values = device_indices.tolist()
        if len(values) + len(self.live_host) > self.host_capacity:
            return None
        indices = list(range(self.next_host, self.next_host + len(values)))
        self.next_host += len(values)
        self.live_host.update(zip(indices, values))
        self.pending_writes.update(values)
        self.events.append(("write", values[0]))

        def finish():
            self.events.append(("write_ack", values[0]))
            self.pending_writes.difference_update(values)

        self.ack_write_queue.append(Ack(finish_event=Event(on_wait=finish), node_ids=[node_id]))
        return self.torch.tensor(indices, device="cpu")

    def free_device(self, device_indices):
        values = device_indices.tolist()
        if values:
            assert not self.pending_writes.intersection(values)
            self.events.append(("device_free", values[0]))

    def evict_device(self, device_indices):
        self.free_device(device_indices)
        return len(device_indices)

    def evict_host(self, host_indices):
        indices = host_indices.tolist()
        for ack in self.ack_load_queue:
            if not ack.finish_event.query():
                assert not ack.sources.intersection(indices)
        values = [self.live_host.pop(i) for i in indices]
        self.events.append(("host_free", values[0]))
        return len(values)

    def load(self, host_indices, node_id):
        if self.reject_load:
            return None
        self.load_queue.append((node_id, host_indices.tolist()))
        return host_indices + 10000

    def start_loading(self):
        self.ack_load_queue.append(Ack(
            finish_event=Event(),
            node_ids=[node_id for node_id, _ in self.load_queue],
            sources={i for _, indices in self.load_queue for i in indices},
        ))
        self.load_queue.clear()
        return len(self.ack_load_queue) - 1


def _cache_probes(author):
    import torch
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams, InsertParams, MatchPrefixParams
    from sglang.srt.mem_cache.cache_init_params import CacheInitParams
    from sglang.srt.mem_cache.hiradix_cache import HiRadixCache
    from sglang.srt.mem_cache.radix_cache import RadixCache, RadixKey

    if author:
        from sglang.srt.mem_cache.evict_policy import TypedHostEvictionStrategy
        from sglang.srt.mem_cache.radix_cache import ProgramType, _CPU_EVICTION_ORDER
    else:
        from sglang.srt.mem_cache.mori import MoriPolicy, ProgramType

    def make_cache(capacity=100, policy="lru", active=True, write_back_cap=0):
        cache = HiRadixCache.__new__(HiRadixCache)
        cache.cache_controller = Controller(torch, capacity)
        cache.token_to_kv_pool_host = NS(clear=lambda: None)
        cache.ongoing_load_back = {}
        cache.ongoing_write_through = {}
        cache.ongoing_load_back_nodes = {}
        cache.prefetch_loaded_tokens_by_reqid = {}
        cache.evictable_host_leaves = set()
        cache._pass_epoch = 0
        cache.enable_storage = cache.enable_storage_metrics = False
        cache.metrics_collector = None
        cache.pp_rank, cache.pp_size, cache.tp_world_size = 0, 1, 1
        cache.attn_tp_group = cache.attn_cp_group = cache.pp_group = None
        cache.work_list = []
        cache.load_back_threshold = 10
        RadixCache.__init__(cache, CacheInitParams(
            disable=False, req_to_token_pool=None, page_size=2, eviction_policy=policy,
            token_to_kv_pool_allocator=cache.cache_controller.mem_pool_device_allocator,
        ))
        if author:
            cache.host_eviction_strategy = TypedHostEvictionStrategy(
                cache.eviction_strategy, cache.get_program_type, _CPU_EVICTION_ORDER,
                ProgramType.INTERACTIVE, ProgramType.ASYNC, lambda: cache._pass_epoch,
                lambda: bool(cache._program_types),
            )
        else:
            cache.mori_write_back_cap = write_back_cap
            if active:
                cache.mori = MoriPolicy(cache.eviction_strategy)
        return cache

    def match(cache, tokens):
        return cache.match_prefix(MatchPrefixParams(key=RadixKey(list(tokens))))

    def insert(cache, first, count, program):
        tokens = list(range(first, first + count))
        cache.insert(InsertParams(
            key=RadixKey(tokens), value=torch.tensor(tokens, device="cpu"), program_id=program,
        ))
        return match(cache, tokens).last_device_node

    def stamp(cache, node, program):
        if author:
            cache.stamp_program_metadata(node, program)
        else:
            cache.mori.stamp_program_metadata(node, cache.root_node, program)

    def control(cache):
        return cache if author else cache.mori

    def evict(cache, num):
        return cache.evict(EvictParams(num_tokens=num)).num_tokens_evicted

    cache = make_cache()
    child = insert(cache, 10, 20, "first")
    stamp(cache, child, "first")
    prefix = match(cache, range(10, 20)).last_device_node
    control(cache).release_program("first")
    stamp(cache, prefix, "second")
    reowned = prefix.program_id
    stamp(cache, prefix, "third")
    control(cache).release_program("second")
    control(cache).release_program("third")
    stamp(cache, prefix, "fourth")
    assert (reowned, prefix.program_id, child.program_id) == (
        "second", "__sglang_shared__", "first",
    )
    results = {"ownership": (reowned, prefix.program_id, child.program_id)}
    for policy in ("lru", "lfu"):
        cache = make_cache(policy=policy)
        seed = [
            (10, "interactive", ProgramType.INTERACTIVE),
            (20, "async", ProgramType.ASYNC),
            (30, "idle", ProgramType.INACTIVE),
            (40, "share-a", ProgramType.INTERACTIVE),
        ]
        for i, (first, program, tier) in enumerate(seed):
            node = insert(cache, first, 10, program)
            control(cache).mark_program_type(program, tier)
            node.last_access_time = i
            if first == 40:
                stamp(cache, node, "share-b")
        # Older async leaf loses the LRU tie, but wins the LFU tie.
        async_peer = insert(cache, 50, 10, "async")
        async_peer.last_access_time = 1.5
        if policy == "lfu":
            match(cache, range(20, 30)).last_device_node.hit_count = 1
            # The prefix lookup updates last access. Keep base strategy inputs fixed.
            match(cache, range(20, 30)).last_device_node.last_access_time = 1
        assert evict(cache, 50) == 50
        events = list(cache.cache_controller.events)
        write_order = [first for kind, first in events if kind == "write"]
        assert write_order == ([30, 20, 50, 10, 40] if policy == "lru" else [30, 50, 20, 10, 40])
        assert [kind for kind, _ in events[:5]] == ["write"] * 5
        cache.evict_host(50)
        results[policy] = cache.cache_controller.events

    cache = make_cache()
    for first in (10, 20, 30):
        insert(cache, first, 10, "async")
        control(cache).mark_program_type("async", ProgramType.ASYNC)
        if first < 30:
            assert evict(cache, 10) == 10
    cache.check_hicache_events()
    request_prefix = match(cache, range(10, 20))
    assert request_prefix.host_hit_length == 10
    assert evict(cache, 10) == 10
    cache.evict_host(30)
    results["host_epoch"] = [first for kind, first in cache.cache_controller.events if kind == "host_free"]
    assert results["host_epoch"] == [20, 30, 10]

    cache = make_cache(capacity=10)
    insert(cache, 10, 20, "interactive")
    prefix = match(cache, range(10, 20)).last_device_node
    stamp(cache, prefix, "interactive")
    assert evict(cache, 20) == 10
    cache.inc_lock_ref(prefix)
    assert evict(cache, 10) == 0
    cache.dec_lock_ref(prefix)
    assert evict(cache, 10) == 10
    results["host_pressure"] = cache.cache_controller.events

    cache = make_cache()
    node = insert(cache, 10, 20, "async")
    control(cache).mark_program_type("async", ProgramType.ASYNC)
    assert evict(cache, 20) == 20
    assert len(cache.load_back(node)) == 20
    cache.ready_to_load_host_cache()
    control(cache).release_program("async")
    cache.loading_check()
    assert len(cache.cache_controller.live_host) == 20
    assert evict(cache, 20) == 0
    cache.cache_controller.ack_load_queue[0].finish_event.ready = True
    cache.loading_check()
    cache.loading_check()
    assert not cache.cache_controller.live_host
    assert node.host_value is None and node.lock_ref == 0
    results["h2d"] = cache.cache_controller.events

    if not author:
        results["write_back_modes"] = {}
        for name, cap in (("author", 0), ("matched", 1)):
            cache = make_cache(write_back_cap=cap)
            for first in (10, 20):
                leaf = insert(cache, first, 10, "async")
                leaf.last_access_time = first
            assert evict(cache, 20) == 20
            results["write_back_modes"][name] = cache.cache_controller.events

        cache = make_cache()
        node = insert(cache, 100, 20, "active")
        stamp(cache, node, "active")
        assert evict(cache, 20) == 20
        cache.cache_controller.reject_load = True
        assert cache.load_back(node) is None
        aborted_refs = node.host_ref_counter
        aborted_host = len(cache.cache_controller.live_host)
        cache.cache_controller.reject_load = False
        assert len(cache.load_back(node)) == 20
        cache.ready_to_load_host_cache()
        # Two splits while DMA reads the original contiguous Host extent.
        match(cache, range(100, 112))
        match(cache, range(100, 106))
        cache.inc_lock_ref(node)
        control(cache).release_program("active")
        cache.loading_check()
        cache.evict_host(20)
        before_host = len(cache.cache_controller.live_host)
        before_device = evict(cache, 20)
        cache.cache_controller.ack_load_queue[0].finish_event.ready = True
        cache.loading_check()
        assert node.lock_ref == 1 and not cache.mori.load_sources
        after_ack_protected = cache.protected_size_
        cache.dec_lock_ref(node)
        results["pending_split"] = {
            "before_ack_host": before_host, "after_ack_host": len(cache.cache_controller.live_host),
            "before_ack_device_evicted": before_device,
            "after_ack_freed": cache.mori.host_tokens_freed_on_h2d_ack,
            "after_ack_request_protected": after_ack_protected,
            "after_request_unlock_evictable": cache.evictable_size_,
            "aborted_load_source_refs": aborted_refs, "aborted_load_host": aborted_host,
        }
        cache = make_cache(active=False)
        node = insert(cache, 100, 20, None)
        assert evict(cache, 20) == 20
        assert len(cache.load_back(node)) == 20
        cache.ready_to_load_host_cache()
        cache.cache_controller.ack_load_queue[0].finish_event.ready = True
        cache.loading_check()
        results["inactive"] = {"host_after_ack": len(cache.cache_controller.live_host)}
    return results, make_cache()


async def _http_probes(author, cache, results):
    from httpx import ASGITransport, AsyncClient
    from sglang.srt.entrypoints import http_server
    from sglang.srt.managers.scheduler import Scheduler

    scheduler = Scheduler.__new__(Scheduler)
    scheduler.tree_cache = cache

    if author:
        async def mark(obj):
            return scheduler.handle_mark_program_type(obj)

        async def release(obj):
            return scheduler.handle_release_program(obj)

        async def batch(obj):
            return scheduler.handle_batch_program_ops(obj)

        tokenizer = NS(mark_program_type=mark, release_program=release, batch_program_ops=batch)
    else:
        from sglang.srt.disaggregation.utils import DisaggregationMode
        from sglang.srt.managers import io_struct, tokenizer_control_mixin
        from sglang.srt.managers.schedule_batch import FINISH_LENGTH
        from sglang.srt.managers.tokenizer_manager import TokenizerManager
        from sglang.srt.observability.req_time_stats import APIServerReqTimeStats
        from sglang.srt.sampling.sampling_params import SamplingParams
        from sglang.utils import TypeBasedDispatcher

        from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY
        from beliefkv.experiments.paper_baseline_sglang import (
            ARM_ENV, TERMINAL_KEY, paper_streamer_class,
        )

        class DispatchStub:
            def __getattr__(self, attr):
                return lambda _: None

        scheduler.flush_wrapper = DispatchStub()
        scheduler.weight_updater = DispatchStub()
        scheduler.init_request_dispatcher()
        scheduler.enable_session_radix_cache = False
        scheduler.tokenizer = None
        scheduler.model_config = NS(vocab_size=256, hf_eos_token_id=0)
        scheduler.metrics_reporter = NS(enable_metrics=False)
        scheduler.dllm_config = None
        scheduler.disaggregation_mode = DisaggregationMode.NULL
        scheduler.spec_algorithm = NS(is_dflash_family=lambda: False, is_uno=lambda: False)
        scheduler.max_req_input_len = 256
        scheduler.grammar_manager = NS(process_req_with_grammar=lambda _: False)
        scheduler._maybe_namespace_elastic_radix_cache = lambda req: None
        scheduler.init_req_max_new_tokens = lambda req: None

        admitted = []
        scheduler._add_request_to_queue = admitted.append

        class Tokenizer(tokenizer_control_mixin.TokenizerControlMixin):
            preferred_sampling_params = None
            sampling_params_class = SamplingParams
            model_config = scheduler.model_config
            tokenizer = None
            rid_to_state = {}

            def auto_create_handle_loop(self):
                pass

            def _dispatch_to_scheduler(self, wire):
                wire = io_struct.msgpack_decode(io_struct.msgpack_encode(wire))
                result = scheduler._request_dispatcher(wire)
                result = io_struct.msgpack_decode(io_struct.msgpack_encode(result))
                if self.hold:
                    self.held_replies.append(result)
                else:
                    asyncio.get_running_loop().call_soon(self._result_dispatcher, result)

            async def generate_request(self, obj, request):
                obj.normalize_batch_and_arguments()
                admitted.clear()
                for item in [obj] if obj.is_single else [obj[i] for i in range(obj.batch_size)]:
                    self.rid_to_state[item.rid] = NS(
                        time_stats=APIServerReqTimeStats(disagg_mode=DisaggregationMode.NULL)
                    )
                    wire = TokenizerManager._create_tokenized_object(self, item, "", item.input_ids)
                    wire.wrap_pickle_fields()
                    tokenized = io_struct.msgpack_decode(io_struct.msgpack_encode(wire))
                    tokenized.unwrap_pickle_fields()
                    scheduler._request_dispatcher(tokenized)
                yield {"requests": [
                    [req.rid, req.program_id, list(req.origin_input_ids)] for req in admitted
                ]}

        tokenizer = Tokenizer()
        tokenizer.hold = False
        tokenizer.held_replies = []
        tokenizer._result_dispatcher = TypeBasedDispatcher([])
        with patch.object(tokenizer_control_mixin, "get_parallel", return_value=NS(dp_size=1)):
            tokenizer.init_communicators()

    http_server._global_state = NS(tokenizer_manager=tokenizer)
    async with AsyncClient(transport=ASGITransport(app=http_server.app), base_url="http://test") as client:
        commands = [
            ("POST", "/mark_program_type", {"program_id": "a", "program_type": "ASYNC"}, None),
            ("PUT", "/batch_program_ops", None, {
                "marks": [{"program_id": "a", "program_type": "inactive"},
                          {"program_id": "b", "program_type": "invalid"},
                          {"program_id": "c", "program_type": "interactive"}],
                "releases": ["a"],
            }),
            ("POST", "/mark_program_type", {"program_id": "a", "program_type": "shared"}, None),
            ("POST", "/batch_program_ops", None, {"marks": None, "releases": None}),
            ("PUT", "/release_program", {"program_id": "missing"}, None),
        ]
        responses = []
        for method, route, params, body in commands:
            response = await client.request(method, route, params=params, json=body)
            table = cache._program_types if author else cache.mori.program_types
            responses.append([response.status_code, response.json(), dict(table)])
        assert responses[1][1] == {"success": False, "message": "invalid type 'invalid' for b"}
        assert responses[1][2] == {"c": 2}
        results["http"] = responses

        if not author:
            disagg = NS(disaggregation_transfer_backend="none", disaggregation_bootstrap_port=0)
            with (
                patch("sglang.srt.managers.tokenizer_manager.get_disagg", return_value=disagg),
                patch("sglang.srt.managers.scheduler.get_disagg", return_value=disagg),
                patch("sglang.srt.managers.scheduler.get_device", return_value=NS(mlx_enable_sampling=False)),
                patch("sglang.srt.managers.scheduler.get_serving", return_value=NS(allow_auto_truncate=False)),
                patch("sglang.srt.managers.schedule_batch.get_spec", return_value=NS(speculative_algorithm=None)),
            ):
                requests = {
                    "single": {"rid": "a", "program_id": "tool-session", "input_ids": [1, 2]},
                    "batch": {"rid": ["b", "c"], "program_id": "other-session", "input_ids": [[1, 2], [3, 4]]},
                    "untyped": {"rid": "d", "input_ids": [1, 2]},
                }
                results["generate"] = {}
                for key, payload in requests.items():
                    response = await client.post("/generate", json=payload)
                    assert response.status_code == 200, response.text
                    results["generate"][key] = response.json()["requests"]
                    if key == "single":
                        req = admitted[0]
                        req.beliefkv_metadata = {
                            "root_workflow_id": "root", "invocation_id": "child",
                            "context_id": "context", "context_epoch": 0,
                        }
                        req.sampling_params.custom_params = {
                            PARAMETER_KEY: {"request_id": req.rid}
                        }
                        req.output_ids.append(9)
                        req.finished_reason = FINISH_LENGTH(1)
                        req.send_token_offset = 1
                        streamer_cls = paper_streamer_class(scheduler.get_output_streamer_class())
                        streamer = streamer_cls.__new__(streamer_cls)
                        with patch.dict(os.environ, {ARM_ENV: "mori"}):
                            receipt = streamer.build_additional_customized_info([req])[TERMINAL_KEY][0][0]
                        results["terminal"] = {key: receipt[key] for key in (
                            "request_id", "program_id", "output_token_ids", "completion_boundary",
                        )}

            with patch.dict(os.environ, {"SGLANG_ENABLE_MORI": "0"}):
                response = await client.post("/release_program", params={"program_id": "c"})
                results["inactive"]["disabled_route"] = response.status_code

            tokenizer.hold = True
            release = io_struct.ReleaseProgramReqInput
            first = asyncio.create_task(tokenizer.release_program(release(program_id="first")))
            while not tokenizer.held_replies:
                await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(tokenizer.release_program(release(program_id="second")))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert len(tokenizer.held_replies) == 1
            tokenizer._result_dispatcher(tokenizer.held_replies.pop(0))
            while not tokenizer.held_replies:
                await asyncio.sleep(0)
            tokenizer._result_dispatcher(tokenizer.held_replies.pop(0))
            results["cancelled_control"] = ["Released first", (await second).message]


if __name__ == "__main__":
    is_author = sys.argv[1] == "author"
    evidence, cache = _cache_probes(is_author)
    asyncio.run(_http_probes(is_author, cache, evidence))
    print("MORI_PROBE=" + json.dumps(evidence, sort_keys=True))
