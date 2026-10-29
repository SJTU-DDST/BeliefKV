#!/usr/bin/env python3
"""Check actual native FULL KV bytes through Agentix swap; no LLM performance claim."""

import argparse
from array import array
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.agentix_swap import AgentixNativeSwap


def gpu_processes():
    result = subprocess.run([
        "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
    ], check=True, capture_output=True, text=True, timeout=10.)
    return [int(value) for value in result.stdout.splitlines() if value.strip()]


def poll_until(predicate, advance):
    deadline = time.monotonic() + 30.
    while not predicate():
        advance()
        if time.monotonic() >= deadline:
            raise TimeoutError("native KV transfer did not finish within the bounded check")
        if not predicate():
            time.sleep(.001)


def restore_native_consumer(cache, actor, req):
    """Use native admission/allocation, returning the uncomputed fixture tail."""
    import torch
    from sglang.srt.managers.schedule_batch import ScheduleBatch
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.mem_cache.allocation import alloc_for_extend
    from sglang.srt.model_executor.forward_batch_info import ForwardMode

    committed = actor.paused[req.rid].proof.committed
    allocator, rows = cache.token_to_kv_pool_allocator, cache.req_to_token_pool
    req.init_next_round_input(cache, cow_mamba=False)
    adder = PrefillAdder(
        1, cache, allocator, None, 0., allocator.size, allocator.size,
        max_running_requests=rows.size,
    )
    adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
    if adder.can_run_list != [req] or len(req.prefix_indices) != committed:
        raise ValueError("native PrefillAdder did not admit the complete paused checkpoint")
    count = req.extend_range.length
    if count != 1:
        raise ValueError("native resumed fixture must extend only its last uncomputed token")
    seq_lens_cpu = torch.tensor([req.extend_range.end], dtype=torch.int64)
    batch = ScheduleBatch(
        reqs=[req], req_to_token_pool=rows, token_to_kv_pool_allocator=allocator,
        tree_cache=cache, device=rows.device, forward_mode=ForwardMode.EXTEND,
        prefix_lens=[committed], extend_lens=[count], extend_num_tokens=count,
        seq_lens_cpu=seq_lens_cpu, seq_lens=seq_lens_cpu.to(rows.device),
    )
    extension, _, _ = alloc_for_extend(batch)
    if not actor.resumed(req):
        raise ValueError("native consumer did not consume its paused Host ownership")
    # This byte fixture performs no forward for the newly allocated last token.
    allocator.free(extension)
    req.kv.kv_committed_len = req.kv.kv_allocated_len = committed
    return {
        "native_prefill_admitted": True,
        "native_extend_allocation_tokens": len(extension),
        "checkpoint_tokens": committed,
        "uncomputed_fixture_tail_returned": True,
    }


def qualify(engine, canonical_patch, *, native_radix_sessions=False):
    if gpu_processes():
        raise ValueError("GPU is occupied before the bounded native KV check")
    state = git_source_state(engine, canonical_patch=canonical_patch)["canonical_patch"]
    if not state["matches_worktree"]:
        raise ValueError("native tree differs from the qualified canonical patch")
    sys.path.insert(0, str(engine / "python"))
    import torch
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.mem_cache.allocator import TokenToKVPoolAllocator
    from sglang.srt.mem_cache.cache_init_params import CacheInitParams
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.memory_pool import MHATokenToKVPool, ReqToTokenPool
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
    from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache
    from sglang.srt.sampling.sampling_params import SamplingParams
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

    if torch.cuda.device_count() != 1:
        raise ValueError("native KV qualification requires one explicit visible GPU")
    server_args = ServerArgs(
        model_path="dummy", page_size=1, hicache_ratio=2.,
        hicache_io_backend="kernel", hicache_mem_layout="page_first",
        hicache_write_policy="write_back",
    )
    set_global_server_args_for_scheduler(server_args)
    pool = MHATokenToKVPool(
        size=128, page_size=1, head_num=2, head_dim=64, dtype=torch.bfloat16,
        layer_num=2, device="cuda", enable_memory_saver=False,
    )
    rows = ReqToTokenPool(
        size=4, max_context_len=64, device="cuda", enable_memory_saver=False,
    )
    allocator = TokenToKVPoolAllocator(
        size=128, dtype=torch.bfloat16, device="cuda", kvcache=pool, need_sort=False,
    )
    params = CacheInitParams(
        disable=False, req_to_token_pool=rows, token_to_kv_pool_allocator=allocator,
        page_size=1, tree_components=(ComponentType.FULL,),
        enable_session_radix_cache=True,
    )
    cache = UnifiedRadixCache(params)
    cache.init_hicache(server_args, params)
    cache.load_back_threshold = 1
    commits = []
    cache.on_hicache_transfer_commit = commits.append
    drained = torch.cuda.Event()
    actor = None

    def make_request(identity, outputs):
        req = Req(
            rid=identity, origin_input_text="",
            origin_input_ids=array("q", range(1, 17)),
            sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=48),
            session_id=("native-" + identity) if native_radix_sessions else None,
            beliefkv_metadata={
                "root_workflow_id": "workflow", "invocation_id": identity,
                "context_id": identity, "context_epoch": 0,
                "full_prompt_replay_guaranteed": True,
            },
        )
        if native_radix_sessions:
            req.session_generation = cache.ensure_session_generation(req.session_id)
        req.output_ids.extend(outputs)
        count = req.seqlen - 1
        if rows.alloc([req]) is None:
            raise ValueError("native request row allocation failed")
        indices = allocator.alloc(count)
        if indices is None:
            raise ValueError("native KV allocation failed")
        rows.write((req.kv.req_pool_idx, slice(0, count)), indices)
        req.kv.kv_committed_len = req.kv.kv_allocated_len = count
        req.last_node = cache.root_node_handle()
        tokens = torch.tensor(
            list(req.origin_input_ids) + list(req.output_ids[:-1]),
            device="cuda", dtype=torch.float32,
        )[:, None, None]
        feature = torch.arange(128, device="cuda", dtype=torch.float32).reshape(1, 2, 64) / 512.
        for layer, (key, value) in enumerate(zip(pool.k_buffer, pool.v_buffer, strict=True)):
            key[indices] = (tokens + feature + layer / 8.).to(torch.bfloat16)
            value[indices] = (tokens + feature + layer / 8. + 4.).to(torch.bfloat16)
        return req, indices

    def snapshot(indices):
        return [
            (key[indices].clone(), value[indices].clone())
            for key, value in zip(pool.k_buffer, pool.v_buffer, strict=True)
        ]

    def equal(indices, expected):
        return all(
            torch.equal(key[indices], saved_key) and torch.equal(value[indices], saved_value)
            for (key, value), (saved_key, saved_value) in zip(
                zip(pool.k_buffer, pool.v_buffer, strict=True), expected, strict=True,
            )
        )

    try:
        peer, peer_indices = make_request("peer", (201, 202, 203))
        peer._refresh_fill_ids()
        peer.set_extend_range(0, peer.seqlen - 1)
        cache.cache_unfinished_req(peer, chunked=True)
        victim, victim_indices = make_request("victim", (101, 102, 103))
        peer_expected, victim_expected = snapshot(peer_indices), snapshot(victim_indices)
        original_inputs, original_outputs = tuple(victim.origin_input_ids), tuple(victim.output_ids)
        sampler = victim.sampling_params
        namespace = victim.session_id, victim.session_generation
        anchors = (
            cache.session_refs.snapshot_session_leaf_anchors(*namespace)
            if native_radix_sessions else None
        )
        # The explicit drain establishes safety for this stand-alone fixture.
        # The real scheduler must instead drain its native forward/result queue.
        drained.record(torch.cuda.current_stream())
        drained.synchronize()
        actor = AgentixNativeSwap(cache, drained=drained.query)
        running_batch = type("RunningBatch", (), {"reqs": [peer, victim]})()
        prepared = actor.prepare([victim], running_batch=running_batch)
        before = allocator.available_size()
        tail = rows.req_to_token[
            victim.kv.req_pool_idx, victim.seqlen - 3:victim.seqlen - 1
        ].clone()
        if actor.commit() is not None or allocator.available_size() != before:
            raise ValueError("native device capacity was released before D2H ACK")
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        paused = actor.commit()
        if (
            paused["status"] != "paused"
            or paused["demoted_full_tokens"] != 2
            or paused["allocator_available_increase"] != 2
            or not equal(peer_indices, peer_expected)
        ):
            raise ValueError("selective native release changed live peer KV or capacity")
        pin = actor.paused[victim.rid]
        leaf = cache.tree_core.node_by_id(pin.leaf)
        cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", range(1, 9)))))
        path = actor.transfers.resolve(pin.snapshot, owner=("workflow", "victim", 0))
        if path is None or len(path) != 3 or any(
            node.component_data[0].host_lock_ref <= 0 for node in path
        ):
            raise ValueError("native split lost part of the paused request's full Host prefix")
        if leaf.component_data[0].value is not None or cache.evict_host(1000) != 0:
            raise ValueError("paused KV was not Host-only and protected")
        for key, value in zip(pool.k_buffer, pool.v_buffer, strict=True):
            key[tail] = -777.
            value[tail] = -777.
        consumer = restore_native_consumer(cache, actor, victim)
        if not cache.ongoing_load_back or leaf.component_data[0].host_lock_ref < 1:
            raise ValueError("native H2D lock did not survive the admission pin handoff")
        cache.cache_controller.start_loading()
        poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
        restored = rows.req_to_token[victim.kv.req_pool_idx, :victim.seqlen - 1]
        if (
            not equal(restored, victim_expected) or not equal(peer_indices, peer_expected)
            or tuple(victim.origin_input_ids) != original_inputs
            or tuple(victim.output_ids) != original_outputs
            or victim.sampling_params is not sampler
            or (victim.session_id, victim.session_generation) != namespace
            or native_radix_sessions and (
                cache.session_refs.snapshot_session_leaf_anchors(*namespace) != anchors
            )
            or actor.paused or actor.pending
            or os.getpid() not in gpu_processes()
        ):
            raise ValueError("native swap changed KV bytes, request work or sampler")
        transfers = [
            {"direction": commit.direction, "tokens": dict(commit.num_tokens_by_pool)}
            for commit in commits
        ]
        if transfers != [
            {"direction": "d2h", "tokens": {"kv": 18}},
            {"direction": "h2d", "tokens": {"kv": 2}},
        ]:
            raise ValueError("actual native transfer ACKs do not match unique KV extents")
        return {
            "status": "native_kv_swap_interface_qualified",
            "scope": "Actual small native FULL GPU/Host pools and kernels; no model forward or policy performance experiment.",
            "gpu": torch.cuda.get_device_name(), "owned_gpu_pid": os.getpid(),
            "native_engine": state, "checkpoint_tokens": prepared["unique_checkpoint_tokens"],
            "shared_live_peer_tokens": 16, "exclusive_tokens_released_and_restored": 2,
            "layers": 2, "key_and_value_bytes_preserved_all_layers": True,
            "live_peer_kv_preserved": True, "released_tail_poisoned_before_restore": True,
            "no_device_capacity_release_before_d2h_ack": True,
            "host_pin_protected_until_actual_resumption": True,
            "split_complete_host_prefix_protected_under_pressure": True,
            "native_h2d_lock_survived_pin_handoff": True,
            "input_output_and_sampler_preserved": True,
            "native_transfer_acknowledgements": transfers, "actor": actor.status(),
            "native_prefill_consumer": consumer,
            "request_transport": "full_prompt_native_radix" if native_radix_sessions else "plain_tokens",
            "native_session_generation_preserved": True if native_radix_sessions else None,
            "native_session_anchors_preserved": True if native_radix_sessions else None,
            "whole_llm_scheduler_or_baseline_qualified": False,
            "performance_accepted": False,
        }
    finally:
        try:
            if actor is not None:
                actor.close()
        finally:
            cache.release_host_resources()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-checkout", required=True, type=Path)
    parser.add_argument("--canonical-patch", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--native-radix-sessions", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        result = qualify(
            args.engine_checkout.resolve(), args.canonical_patch.resolve(),
            native_radix_sessions=args.native_radix_sessions,
        )
    except BaseException as error:
        result = {
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
            "performance_accepted": False,
        }
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        result["source_sha256"] = {
            name: sha256_file(ROOT / name) for name in (
                "scripts/qualify_agentix_swap.py", "beliefkv/runtime/agentix_swap.py",
                "beliefkv/runtime/paper_prefix_transfers.py",
            )
        }
        with (args.output_dir / "summary.json").open("x") as output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write("\n")
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
