"""Measure bounded read-only joint-prefill inspection on real native CPU trees."""

from array import array
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.short_continuation_probe import inspect_short_continuations
from scripts.audit_native_system_costs import distribution
from scripts.train_workflow_work import write
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_thunder_admission import full_runtime
from tests.test_sglang_v0520_runtime import event


def measure(depth, tokens_per_node, samples):
    total = depth * tokens_per_node
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory() as directory:
        cache, allocator, writes, loads, commits = full_native.__wrapped__(
            patch, NS(param=3 * total + 65536),
        )
        from sglang.srt.managers.schedule_batch import Req
        from sglang.srt.managers.schedule_policy import PrefillAdder
        from sglang.srt.mem_cache.memory_pool import ReqToTokenPool
        from sglang.srt.sampling.sampling_params import SamplingParams
        from test_beliefkv_terminal_capacity import insert_session

        cache.req_to_token_pool = ReqToTokenPool(
            size=8, max_context_len=max(262144, total + 8192),
            device="cpu", enable_memory_saver=False,
        )
        for level in range(1, depth + 1):
            _leaf, generation = insert_session(
                cache, list(range(10001, 10001 + level * tokens_per_node)), "short",
            )
        insert_session(cache, list(range(1, 17)), "owner")
        owner_generation = cache.ensure_session_generation("owner")
        runtime = full_runtime(patch, Path(directory), capacity=2_000_000, tool_prefetch=False)
        runtime._native_cache = cache
        try:
            runtime.on_events((event(0, Kind.WORKFLOW_START),))
            members = []
            for index, (name, tokens, gen) in enumerate((
                ("owner", array("q", range(1, 8193)), owner_generation),
                ("short", array("q", range(10001, 10001 + total + 64)), generation),
            ), 1):
                runtime.on_events((event(
                    index, Kind.INVOCATION_CREATE, invocation_id=name, context_id="ctx-" + name,
                ),))
                req = Req(
                    rid=name, origin_input_text="", origin_input_ids=tokens,
                    sampling_params=SamplingParams(max_new_tokens=32), session_id=name,
                    beliefkv_metadata={
                        "root_workflow_id": "wf", "invocation_id": name,
                        "context_id": "ctx-" + name, "context_epoch": 0,
                    },
                )
                req.session_generation = gen
                assert runtime.register_visible_request(req)
                assert runtime._agent_admission.allows(runtime.visible[name])
                members.append(req)
            owner, short = members
            owner.init_next_round_input(cache)
            running = NS(reqs=[])
            adder = PrefillAdder(
                page_size=1, tree_cache=cache, token_to_kv_pool_allocator=allocator,
                running_batch=running, new_token_ratio=1., rem_input_tokens=2048,
                rem_chunk_tokens=2048, max_running_requests=24,
            )
            before = (
                allocator.available_size(), cache.req_to_token_pool.available_size(),
                len(writes), len(loads), len(commits),
                deepcopy(runtime._agent_admission.policy.programs),
                cache.session_refs.snapshot_latest_session_leaf_anchors("short", generation),
                owner.extend_range, short.extend_range, adder.rem_input_tokens,
                adder.rem_chunk_tokens, tuple(adder.can_run_list),
            )

            def inspect():
                return inspect_short_continuations(
                    runtime, cache, [short], running_batch=running, adder=adder,
                    chunked_req=owner, soft_budget_ms=1.,
                )

            for _ in range(20):
                assert len(inspect()["candidates"]) == 1
            cpu, wall, exceeded = [], [], 0
            for _ in range(samples):
                begin, thread = time.perf_counter_ns(), time.thread_time_ns()
                observed = inspect()
                cpu.append(time.thread_time_ns() - thread)
                wall.append(time.perf_counter_ns() - begin)
                exceeded += observed["soft_budget_exceeded"]
                assert len(observed["candidates"]) == 1
            after = (
                allocator.available_size(), cache.req_to_token_pool.available_size(),
                len(writes), len(loads), len(commits),
                deepcopy(runtime._agent_admission.policy.programs),
                cache.session_refs.snapshot_latest_session_leaf_anchors("short", generation),
                owner.extend_range, short.extend_range, adder.rem_input_tokens,
                adder.rem_chunk_tokens, tuple(adder.can_run_list),
            )
            assert before == after
            return {
                "depth": depth, "tokens_per_node": tokens_per_node, "prefix_tokens": total,
                "samples": samples, "soft_budget_exceeded_samples": exceeded,
                "thread_cpu_us": distribution(value / 1000. for value in cpu),
                "wall_us": distribution(value / 1000. for value in wall),
                "raw_cpu_ns": cpu, "raw_wall_ns": wall,
                "observed_shape": observed["candidates"][0],
                "native_resources_and_live_state_unchanged": True,
            }
        finally:
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    args = parser.parse_args()
    if (
        args.output.exists() or not 10 <= args.samples <= 300
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve reports and use bounded nice19 CPU-only measurement")
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    cases = [measure(depth, unit, args.samples) for depth, unit in (
        (1, 8192), (80, 1024), (80, 3072),
    )]
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    write(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "cpu_affinity": [cpu], "nice": 19, "cases": cases,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            Path(__file__), ROOT / "beliefkv/runtime/short_continuation_probe.py",
            ROOT / "tests/test_full_only_native_lifecycle.py",
            ROOT / "tests/test_reference_thunder_admission.py",
            engine / "python/sglang/srt/managers/schedule_policy.py",
            engine / "python/sglang/srt/managers/schedule_batch.py",
            engine / "python/sglang/srt/mem_cache/unified_radix_cache.py",
        )},
        "scope": [
            "Real native CPU radix tree, session identity, allocator and PrefillAdder budget methods.",
            "Copy controller uses the existing qualified CPU stand-ins; no transfer or GPU execution occurs.",
            "Synthetic component geometry, not a replacement workload or performance comparison.",
            "Each sample re-inspects physical residency without using the runtime's cached hints.",
            "Measured CPU/wall cost is not full scheduler overhead or JCT gain.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    })
    print(json.dumps([{
        "prefix_tokens": row["prefix_tokens"], "depth": row["depth"],
        "cpu_p50_us": row["thread_cpu_us"]["p50"], "cpu_p99_us": row["thread_cpu_us"]["p99"],
        "budget_exceeded": row["soft_budget_exceeded_samples"],
    } for row in cases]))


if __name__ == "__main__":
    main()
