#!/usr/bin/env python3
"""Bounded native GPU/Host prefix byte checks, not model or policy performance."""

import argparse
from array import array
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.tokencake_capacity import TokenCakeNativeCapacity
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


QUALIFIED_SOURCES = (
    "scripts/qualify_paper_prefix_transfers.py",
    "beliefkv/runtime/paper_prefix_transfers.py",
    "beliefkv/runtime/kvflow_cache.py",
    "beliefkv/runtime/kvflow_baseline.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/sglang_v0520_physical.py",
    "beliefkv/policy/tokencake_capacity.py",
    "beliefkv/runtime/tokencake_capacity.py",
)
REQUIRED_EVIDENCE = (
    "all_layers_key_and_value_bytes_preserved",
    "live_shared_peer_preserved",
    "incremental_backup_transferred_only_growth",
    "split_snapshots_and_host_leases_preserved",
    "no_release_before_d2h_ack",
    "native_h2d_ownership_survived_host_lease_expiry",
    "unique_allocator_capacity_preserved",
    "released_device_pages_poisoned_before_restore",
    "prefetch_reclaim_avoids_global_write_back_wait",
    "progressive_prefix_grants_bound_actual_gpu_allocation",
    "progressive_restore_preserves_all_layer_bytes",
    "native_spatial_capacity_counts_shared_prefix_and_generation",
    "native_admission_reservations_settle_after_batch_allocation",
    "ready_host_prefix_is_protected_during_incremental_backup",
    "matching_busy_gpu_prefix_is_adopted_without_double_capacity",
    "paper_transfer_receipts_are_reconciled_by_runtime",
    "demand_allocation_retries_after_speculative_backup_ack",
    "busy_native_producer_defers_speculative_restore_without_allocation",
    "multiple_restore_owners_share_one_native_producer",
    "speculation_preserves_unallocated_native_chunk_capacity",
    "full_prompt_native_radix_requests_use_observed_prefix_history",
    "native_radix_session_provenance_survives_poisoned_page_restore",
    "session_prefix_split_preserves_live_peer_and_unique_capacity",
)


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
            raise TimeoutError("bounded native prefix transfer did not settle")
        if not predicate():
            time.sleep(.001)


def validate_report(report, *, engine_tree, patch_sha256, root=ROOT):
    session = report.get("native_radix_session") or {}
    if (
        report.get("status") != "native_prefix_transfers_qualified"
        or report.get("native_engine", {}).get("actual_tree") != engine_tree
        or report.get("native_patch_sha256") != patch_sha256
        or report.get("whole_llm_scheduler_or_baseline_qualified") is not False
        or report.get("performance_accepted") is not False
        or any(report.get(name) is not True for name in REQUIRED_EVIDENCE)
        or report.get("source_sha256") != {
            name: sha256_file(root / name) for name in QUALIFIED_SOURCES
        }
        or session.get("history_prefix_tokens") != 8
        or session.get("shared_prefix_tokens") != 4
        or session.get("final_device_free_tokens") != 128
        or session.get("native_ack_tokens") != {"d2h": 12, "h2d": 12}
        or any(
            type(session.get(name)) is not int or session[name] <= 0
            for name in ("session_generation", "peer_generation")
        )
        or session.get("session_generation") == session.get("peer_generation")
        or any(session.get(name) is not True for name in (
            "source_and_successor_requests_registered",
            "poisoned_pages_restored_with_exact_all_layer_bytes",
            "native_session_provenance_preserved", "shared_peer_preserved",
        ))
    ):
        raise ValueError("native prefix GPU evidence or qualified source differs")


def qualify_radix_sessions(runtime, cache, allocator, commits, *, fill, saved, equal, synchronize):
    """Exercise the actual full-prompt ingress on session-labelled native nodes."""
    import torch
    from sglang.srt.managers.schedule_batch import FINISH_LENGTH, Req
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams, MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from sglang.srt.sampling.sampling_params import SamplingParams
    from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates

    adapter, actor = runtime._paper_baseline, runtime._paper_baseline.transfers
    tokens, session = list(range(71, 79)), "paper-native-session"
    context_id, peer_session = "native-session-context", "paper-native-peer"
    owner, peer_owner = ("workflow", context_id, 0), ("workflow", "native-session-peer", 0)
    before = allocator.available_size()
    prior_commits = len(commits)
    if before != 128:
        raise ValueError("native session scene requires the fully returned Device pool")

    def request(identity, source, session_id, context):
        req = Req(
            rid=identity, origin_input_text="", origin_input_ids=array("q", source),
            sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=1),
            session_id=session_id, extra_key=None, cache_salt=None,
            beliefkv_metadata={
                "root_workflow_id": "workflow", "invocation_id": context,
                "context_id": context, "context_epoch": 0,
                "full_prompt_replay_guaranteed": True,
            },
        )
        req.session_generation = cache.ensure_session_generation(session_id)
        return req

    def candidates(req):
        plan = runtime.plan_native_prefill([req], running_batch=None, adder=None)
        return select_native_prefill_candidates(
            [req], plan=plan, current_semantic_revision=runtime.semantic_revision,
        ).candidates

    indices = allocator.alloc(len(tokens))
    fill(indices, tokens)
    expected = saved(indices)
    leaf_id = cache.insert(InsertParams(key=RadixKey(array("q", tokens)), value=indices)).last_device_node
    leaf = cache.tree_core.node_by_id(leaf_id)
    source = request("native-session-history", tokens, session, context_id)
    source.last_node = leaf.id
    cache.session_refs.register_session_ref(source)
    if not runtime.register_visible_request(source):
        raise ValueError("actual radix session request was rejected before prefix learning")
    source.finished_reason = FINISH_LENGTH(0)
    runtime.on_batch_completed(SimpleNamespace(reqs=[source]))
    waiting = request("native-session-waiting", tokens + [79, 80], session, context_id)
    if not runtime.register_visible_request(waiting):
        raise ValueError("actual radix session successor was rejected")
    context = adapter.contexts[context_id]
    if context.snapshot is None or context.snapshot.prefix_tokens != len(tokens) or (
        adapter.history.boundaries[context_id] != len(tokens)
        or context.snapshot.namespace != (None, None)
    ):
        raise ValueError("radix identity replaced or reconstructed the observed token prefix")
    generation = source.session_generation
    anchors = cache.session_refs.snapshot_session_leaf_anchors(session, generation)
    synchronize()
    if not actor.backup(context.snapshot, owner=owner):
        raise ValueError("session-labelled native prefix was not backed")
    if actor.demote(context.snapshot, owner=owner, max_tokens=8):
        raise ValueError("session-labelled prefix was released before its native D2H ACK")
    poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
    if actor.demote(context.snapshot, owner=owner, max_tokens=8) != 8:
        raise ValueError("native session prefix did not return eight unique Device slots")
    poison = allocator.alloc(before)
    fill(poison, range(5001, 5001 + before))
    synchronize()
    allocator.free(poison)
    if not actor.restore(context.snapshot, owner=owner, available_tokens=before):
        raise ValueError("native session prefix did not restore from its Host source")
    adapter._next_tick = float("inf")
    if candidates(waiting):
        raise ValueError("native session request was selected before its H2D ACK")
    poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
    restored = cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", tokens))))
    if not equal(restored.device_indices, expected) or candidates(waiting) != (waiting,):
        raise ValueError("native session restored bytes or subsequent admission differ")
    if cache.session_refs.snapshot_session_leaf_anchors(session, generation) != anchors:
        raise ValueError("prefix transfers changed native session provenance")

    peer_tokens = tokens[:4] + list(range(91, 95))
    peer_match = cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", peer_tokens))))
    peer_indices = allocator.alloc(4)
    fill(peer_indices, peer_tokens[4:])
    peer_leaf_id = cache.insert(InsertParams(
        key=RadixKey(array("q", peer_tokens)),
        value=torch.cat((peer_match.device_indices, peer_indices)), prev_prefix_len=4,
    )).last_device_node
    peer = request("native-session-peer", peer_tokens, peer_session, "native-session-peer")
    peer.last_node = peer_leaf_id
    cache.session_refs.register_session_ref(peer)
    peer_snapshot = actor.capture(peer_leaf_id, owner=peer_owner)
    peer_expected = saved(torch.cat((peer_match.device_indices, peer_indices)))
    synchronize()
    if allocator.available_size() != before - 12 or not actor.backup(peer_snapshot, owner=peer_owner):
        raise ValueError("shared session prefix was charged twice or could not be backed")
    poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
    if actor.demote(context.snapshot, owner=owner, max_tokens=8) != 4:
        raise ValueError("session demotion displaced the live peer's shared prefix")
    peer_match = cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", peer_tokens))))
    if not equal(peer_match.device_indices, peer_expected):
        raise ValueError("session-prefix split or demotion corrupted the peer")
    if not actor.restore(context.snapshot, owner=owner, available_tokens=4):
        raise ValueError("split native session tail did not restore")
    poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
    restored = cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", tokens))))
    if not equal(restored.device_indices, expected) or candidates(waiting) != (waiting,):
        raise ValueError("split native session did not resume with its original bytes")
    if actor.demote(context.snapshot, owner=owner, max_tokens=8) != 4 or (
        actor.demote(peer_snapshot, owner=peer_owner, max_tokens=8) != 8
    ) or allocator.available_size() != before:
        raise ValueError("split native session lifecycle leaked Device capacity")
    if (
        cache.session_refs.snapshot_session_leaf_anchors(session, generation) != anchors
        or not cache.session_refs.snapshot_session_leaf_anchors(peer_session, peer.session_generation)
    ):
        raise ValueError("split native session lifecycle changed native ownership")
    ack_tokens = {
        direction: sum(
            dict(commit.num_tokens_by_pool).get("kv", 0)
            for commit in commits[prior_commits:] if commit.direction == direction
        ) for direction in ("d2h", "h2d")
    }
    if ack_tokens != {"d2h": 12, "h2d": 12}:
        raise ValueError("session-prefix ACK tokens disagree with actual unique extents")
    return {
        "session_generation": generation, "peer_generation": peer.session_generation,
        "history_prefix_tokens": len(tokens), "shared_prefix_tokens": 4,
        "final_device_free_tokens": allocator.available_size(),
        "native_ack_tokens": ack_tokens,
        "source_and_successor_requests_registered": True,
        "poisoned_pages_restored_with_exact_all_layer_bytes": True,
        "native_session_provenance_preserved": True,
        "shared_peer_preserved": True,
    }


def qualify(engine, canonical_patch):
    if gpu_processes():
        raise ValueError("GPU is occupied before the bounded prefix transfer check")
    state = git_source_state(engine, canonical_patch=canonical_patch)["canonical_patch"]
    if not state["matches_worktree"]:
        raise ValueError("native engine differs from its canonical patch")
    sys.path.insert(0, str(engine / "python"))
    import torch
    from sglang.srt.mem_cache.allocator import TokenToKVPoolAllocator
    from sglang.srt.mem_cache.allocation import alloc_token_slots
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams, MatchPrefixParams
    from sglang.srt.mem_cache.cache_init_params import CacheInitParams
    from sglang.srt.mem_cache.memory_pool import MHATokenToKVPool, ReqToTokenPool
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
    from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.managers.schedule_batch import FINISH_LENGTH
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.sampling.sampling_params import SamplingParams
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

    if torch.cuda.device_count() != 1:
        raise ValueError("prefix qualification requires one explicit visible GPU")
    settings = ServerArgs(
        model_path="dummy", page_size=1, hicache_ratio=2.,
        hicache_io_backend="kernel", hicache_mem_layout="page_first",
        hicache_write_policy="write_back",
    )
    set_global_server_args_for_scheduler(settings)
    pool = MHATokenToKVPool(
        size=128, page_size=1, head_num=2, head_dim=64, dtype=torch.bfloat16,
        layer_num=2, device="cuda", enable_memory_saver=False,
    )
    rows = ReqToTokenPool(size=4, max_context_len=64, device="cuda", enable_memory_saver=False)
    allocator = TokenToKVPoolAllocator(
        size=128, dtype=torch.bfloat16, device="cuda", kvcache=pool, need_sort=False,
    )
    params = CacheInitParams(
        disable=False, req_to_token_pool=rows, token_to_kv_pool_allocator=allocator,
        page_size=1, tree_components=(ComponentType.FULL,), enable_session_radix_cache=True,
    )
    cache = UnifiedRadixCache(params)
    cache.init_hicache(settings, params)
    cache.load_back_threshold = 1
    commits, clock = [], [10.]
    declared = paper_environment({}, "kvflow_observed_graph")
    previous = {name: os.environ.get(name) for name in declared}
    try:
        os.environ.update(declared)
        runtime = NativeAdmissionRuntime()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    runtime.attach_native_cache(cache)
    adapter = runtime._paper_baseline
    authority = adapter._authority()
    authority.__enter__()
    actor, priorities = adapter.transfers, adapter.priorities
    actor.clock = lambda: clock[0]

    def acknowledged(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = acknowledged
    capacity = None
    busy_prefix_lock = None
    owner, peer_owner = ("workflow", "agent", 0), ("workflow", "peer", 0)

    def key(tokens):
        return RadixKey(array("q", tokens), "workflow", cache_salt="workflow")

    def fill(indices, tokens):
        token_values = torch.tensor(tokens, dtype=torch.float32, device="cuda")[:, None, None]
        feature = torch.arange(128, device="cuda", dtype=torch.float32).reshape(1, 2, 64) / 512.
        for layer, (keys, values) in enumerate(zip(pool.k_buffer, pool.v_buffer, strict=True)):
            keys[indices] = (token_values + feature + layer / 8.).to(torch.bfloat16)
            values[indices] = (token_values + feature + layer / 8. + 4.).to(torch.bfloat16)

    def saved(indices):
        return [
            (keys[indices].clone(), values[indices].clone())
            for keys, values in zip(pool.k_buffer, pool.v_buffer, strict=True)
        ]

    def equal(indices, expected):
        return all(
            torch.equal(keys[indices], old_keys) and torch.equal(values[indices], old_values)
            for (keys, values), (old_keys, old_values) in zip(
                zip(pool.k_buffer, pool.v_buffer, strict=True), expected, strict=True,
            )
        )

    def insert(tokens, indices, protected=0):
        result = cache.insert(InsertParams(
            key=key(tokens), value=indices, prev_prefix_len=protected,
        ))
        return cache.tree_core.node_by_id(result.last_device_node)

    try:
        tokens = list(range(1, 17))
        indices = allocator.alloc(16)
        fill(indices, tokens)
        leaf = insert(tokens, indices)
        snapshot = actor.capture(leaf.id, owner=owner)
        expected = saved(indices)
        torch.cuda.current_stream().synchronize()
        if not actor.backup(snapshot, owner=owner):
            raise ValueError("initial native D2H was not submitted")
        before = allocator.available_size()
        if actor.demote(snapshot, owner=owner, max_tokens=16) != 0:
            raise ValueError("prefix was demoted before native D2H acknowledgement")
        if allocator.available_size() != before:
            raise ValueError("allocator capacity changed before native D2H ACK")
        # Split the live D2H node by inserting an independently active peer.
        peer_tokens = tokens[:8] + list(range(101, 109))
        peer_private = allocator.alloc(8)
        fill(peer_private, peer_tokens[8:])
        peer_indices = torch.cat((indices[:8], peer_private))
        peer = insert(peer_tokens, peer_indices, protected=8)
        peer_lock = cache.inc_lock_ref(peer.id).to_dec_params()
        peer_expected = saved(peer_indices)
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        if len(actor.resolve(snapshot, owner=owner)) != 2:
            raise ValueError("in-flight split did not preserve the prefix snapshot")
        priorities.set_anchor("agent", snapshot, owner=owner)
        priorities.set_anchor(
            "peer", actor.capture(peer.id, owner=peer_owner), owner=peer_owner,
        )
        priorities.set_steps({"agent": 1, "peer": 0})
        growth = allocator.alloc(4)
        fill(growth, [17, 18, 19, 20])
        grown_indices = torch.cat((indices, growth))
        grown_tokens = tokens + [17, 18, 19, 20]
        grown = insert(grown_tokens, grown_indices, protected=16)
        expected = [
            (torch.cat((old[0], new[0])), torch.cat((old[1], new[1])))
            for old, new in zip(expected, saved(growth), strict=True)
        ]
        torch.cuda.current_stream().synchronize()
        snapshot = actor.capture(grown.id, owner=owner)
        ready_guard = actor.retain_host_ready(snapshot, owner=owner, deadline_s=20.)
        if (
            ready_guard is None or actor._host_leases[ready_guard].complete
            or cache.evict_host(128) != 0
        ):
            raise ValueError("existing Host prefix was not protected before incremental D2H")
        issued = actor.backup(snapshot, owner=owner, allow_host_reclaim=True)
        if sum(row[2] for row in issued) != 4:
            raise ValueError("incremental backup recopied an existing Host prefix")
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        lease = actor.retain_host(snapshot, owner=owner, deadline_s=20.)
        if lease is None:
            raise ValueError("complete prefix Host lease was not acquired")
        actor.release_host(ready_guard)
        cache.match_prefix(MatchPrefixParams(key=key(grown_tokens[:18])))
        path = actor.resolve(snapshot, owner=owner)
        if len(path) != 4 or any(node.component_data[0].host_lock_ref < 1 for node in path):
            raise ValueError("split lost part of the leased Host prefix")
        peer_snapshot = actor.capture(peer.id, owner=peer_owner)
        if not actor.backup(peer_snapshot, owner=peer_owner):
            raise ValueError("unrelated peer backup was not submitted")
        before = allocator.available_size()
        freed, _ = priorities.reclaim_for_prefetch(12, max_nodes=8)
        if freed != 12 or peer.id not in cache.ongoing_write_through:
            raise ValueError("native demotion did not preserve the active shared peer")
        if allocator.available_size() != before + 12:
            raise ValueError("native demotion disagrees with unique allocator capacity")
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        poisoned = torch.cat((indices[8:], growth))
        for keys, values in zip(pool.k_buffer, pool.v_buffer, strict=True):
            keys[poisoned] = -777.
            values[poisoned] = -777.
        torch.cuda.current_stream().synchronize()
        missing_ids = [
            node.id for node in path if node.component_data[0].value is None
        ]
        if not actor.restore(snapshot, owner=owner, available_tokens=12):
            raise ValueError("ancestor-first native prefix H2D was not submitted")
        clock[0] = 21.
        if actor.expire_host_leases() != 1 or any(
            cache.tree_core.node_by_id(identity).component_data[0].host_lock_ref < 1
            for identity in missing_ids
        ):
            raise ValueError("lease expiry released native in-flight H2D ownership")
        cache.evict_host(128)
        poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
        match = cache.match_prefix(MatchPrefixParams(key=key(grown_tokens)))
        if (
            len(match.device_indices) != 20 or not equal(match.device_indices, expected)
            or not equal(peer_indices, peer_expected)
            or allocator.available_size() != 100
            or actor.resolve(snapshot, owner=("workflow", "agent", 1)) is not None
            or os.getpid() not in gpu_processes()
        ):
            raise ValueError("restored GPU bytes, active peer or prefix ownership differs")
        totals = {
            direction: sum(
                dict(commit.num_tokens_by_pool).get("kv", 0)
                for commit in commits if commit.direction == direction
            ) for direction in ("d2h", "h2d")
        }
        if totals != {"d2h": 28, "h2d": 12}:
            raise ValueError("native ACK token totals disagree with unique transfers")
        initial_totals = totals
        extra = actor.backup(snapshot, owner=owner)
        extra_d2h = sum(item[2] for item in extra)
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        lease = actor.retain_host(snapshot, owner=owner, deadline_s=40.)
        if lease is None:
            raise ValueError("progressive restore could not retain its full Host source")
        poisoned = match.device_indices[8:].clone()
        if actor.demote(snapshot, owner=owner, max_tokens=12) != 12:
            raise ValueError("progressive fixture failed to release its private GPU tail")
        for keys, values in zip(pool.k_buffer, pool.v_buffer, strict=True):
            keys[poisoned] = -888.
            values[poisoned] = -888.
        torch.cuda.current_stream().synchronize()
        remaining, progressive_grants = 12, []
        while remaining:
            grant = (remaining + 1) // 2
            before = allocator.available_size()
            issued = actor.restore_prefix(
                snapshot, owner=owner, observed_key=key(grown_tokens), available_tokens=grant,
            )
            actual = sum(item[2] for item in issued)
            if actual != grant or before - allocator.available_size() != grant:
                raise ValueError("progressive H2D did not bind its grant to actual GPU allocation")
            progressive_grants.append(actual)
            remaining -= actual
            poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
        match = cache.match_prefix(MatchPrefixParams(key=key(grown_tokens)))
        if not equal(match.device_indices, expected) or not equal(peer_indices, peer_expected):
            raise ValueError("progressive H2D changed a restored layer or the active shared peer")
        actor.release_host(lease)
        cache.dec_lock_ref(peer.id, peer_lock)
        capacity = TokenCakeNativeCapacity(actor, generation_budget=8, context_limit=64)
        busy_prefix_lock = cache.inc_lock_ref(snapshot.leaf_id).to_dec_params()
        live, staged = [], []
        before_private = allocator.available_size()
        for ordinal, final_token in enumerate((peer_tokens[-1], 999)):
            request = Req(
                rid="capacity-" + str(ordinal), origin_input_text="",
                origin_input_ids=array("q", peer_tokens[:-1] + [final_token]),
                sampling_params=SamplingParams(
                    temperature=1., top_k=1, max_new_tokens=1 if ordinal == 0 else 256,
                ),
                extra_key="workflow", cache_salt="workflow",
                beliefkv_metadata={
                    "root_workflow_id": "workflow", "invocation_id": "capacity-" + str(ordinal),
                    "context_id": "capacity-" + str(ordinal), "context_epoch": 0,
                },
            )
            probe = capacity.probe(request)
            if probe.target_tokens != 24 or not capacity.allows(
                probe, agent_type="fixture", score=1000,
            ):
                raise ValueError("trace-independent generation admission differs")
            if ordinal == 0 and probe.external_prefix_tokens != 8:
                raise ValueError("native busy-prefix reuse did not prove its exact shared extent")
            match = cache.match_prefix(MatchPrefixParams(key=RadixKey(
                request.origin_input_ids, request.extra_key, limit=15,
                cache_salt=request.cache_salt,
            )))
            request.last_node = match.last_device_node
            request.lock_receipt = cache.inc_lock_ref(request.last_node).to_dec_params()
            capacity.stage(
                request, agent_type="fixture", score=1000, probe=probe, planned_private_tokens=1,
            )
            if ordinal == 0:
                if capacity.counts["external_prefix_tokens_adopted"] != 8:
                    raise ValueError("native busy GPU prefix was not adopted by its actual consumer")
                cache.dec_lock_ref(snapshot.leaf_id, busy_prefix_lock)
                busy_prefix_lock = None
                capacity.refresh()
            staged.append((request, match.device_indices, final_token))
        if (
            sum(claim.pending_private for claim in capacity.requests.values()) != 2
            or allocator.available_size() != before_private
            or capacity.window.uncommitted != 95
        ):
            raise ValueError("native pending admission did not reserve real future capacity")
        for request, prefix_indices, final_token in staged:
            if rows.alloc([request]) is None:
                raise ValueError("native capacity fixture request row allocation failed")
            private = allocator.alloc(1)
            fill(private, [final_token])
            request.kv.kv_allocated_len = request.kv.kv_committed_len = 16
            rows.write(
                (request.kv.req_pool_idx, slice(0, 16)),
                torch.cat((prefix_indices, private)),
            )
            live.append((request, private))
        capacity.mark_allocated([request for request, _ in live])
        for request, _ in live:
            capacity.commit(request, agent_type="fixture", score=1000)
        if (
            sum(capacity.window.used.values()) != 17
            or sum(capacity.window.growth.values()) != 16
            or capacity.window.uncommitted != 95
        ):
            raise ValueError("native shared prefix or decode growth was counted incorrectly")
        for request, private in live:
            cache.dec_lock_ref(request.last_node, request.lock_receipt)
            allocator.free(private)
            rows.free(request)
            capacity.release(request.rid)
        if capacity.window.used or capacity.window.growth or capacity.window.uncommitted != 128:
            raise ValueError("native capacity claims or growth leaked after actual release")
        existing_locks = [
            (identity, cache.inc_lock_ref(identity).to_dec_params())
            for identity in (snapshot.leaf_id, peer.id)
        ]
        retry_indices = allocator.alloc(2)
        fill(retry_indices, [901, 902])
        retry_parent = insert([901, 902], retry_indices)
        retry_snapshot = actor.capture(retry_parent.id, owner=owner)
        if not actor.backup(retry_snapshot, owner=owner):
            raise ValueError("allocation-retry fixture parent was not backed up")
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        retry_private = allocator.alloc(2)
        fill(retry_private, [903, 904])
        retry_leaf = insert(
            [901, 902, 903, 904], torch.cat((retry_indices, retry_private)), protected=2,
        )
        retry_snapshot = actor.capture(retry_leaf.id, owner=owner)
        if priorities.reclaim_for_prefetch(2, max_nodes=1) != (0, 1):
            raise ValueError("allocation-retry fixture did not enqueue an asynchronous cold backup")
        pressure = allocator.alloc(allocator.available_size() - 2)
        allocated = alloc_token_slots(cache, 4)
        if (
            len(allocated) != 4 or retry_leaf.component_data[0].value is not None
            or actor.counts["allocation_after_backup_retry"] != 1
        ):
            raise ValueError("demand allocation did not retry its newly unlocked native leaves")
        allocator.free(allocated)
        allocator.free(pressure)
        if actor.demote(retry_snapshot, owner=owner, max_tokens=4) != 2:
            raise ValueError("allocation-retry fixture leaked its remaining native parent")
        for identity, receipt in existing_locks:
            cache.dec_lock_ref(identity, receipt)
        if allocator.available_size() != 100:
            raise ValueError("native allocation retry changed unrelated cached capacity")
        targets = []
        for ordinal in range(5):
            target_owner = ("workflow", f"overlap-{ordinal}", 0)
            target_tokens = [2001 + 2 * ordinal, 2002 + 2 * ordinal]
            target_indices = allocator.alloc(2)
            fill(target_indices, target_tokens)
            target_expected = saved(target_indices)
            target_leaf = insert(target_tokens, target_indices)
            target_snapshot = actor.capture(target_leaf.id, owner=target_owner)
            torch.cuda.current_stream().synchronize()
            if not actor.backup(target_snapshot, owner=target_owner):
                raise ValueError("producer-slot fixture failed to enqueue its Host source")
            poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
            if actor.demote(target_snapshot, owner=target_owner, max_tokens=2) != 2:
                raise ValueError("producer-slot fixture failed to release its GPU source")
            targets.append((target_owner, target_snapshot, target_tokens, target_expected))
        controller = cache.cache_controller
        # Hold a real native H2D producer busy while testing nonblocking backpressure.
        with torch.cuda.stream(controller.l2_transfer_engine.host_to_device_stream):
            torch.cuda._sleep(500_000_000)
        first_owner, first_snapshot, *_ = targets[0]
        if not actor.restore(first_snapshot, owner=first_owner, available_tokens=2):
            raise ValueError("producer-slot fixture failed to start a native H2D batch")
        counter = controller.layer_done_counter
        if counter.events[counter.producer_index].finish_event.query():
            raise ValueError("producer-slot fixture did not observe an in-flight CUDA event")
        before_free, before_receipts = allocator.available_size(), actor.receipts.pending_count
        second_owner, second_snapshot, *_ = targets[1]
        if (
            actor.restore(second_snapshot, owner=second_owner, available_tokens=2)
            or allocator.available_size() != before_free
            or actor.receipts.pending_count != before_receipts
            or controller.load_queue
        ):
            raise ValueError("busy native producer admitted or allocated another speculative restore")
        poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
        previous_producer = counter.producer_index
        with actor.loading_batch():
            for target_owner, target_snapshot, *_ in targets[1:]:
                if not actor.restore(target_snapshot, owner=target_owner, available_tokens=2):
                    raise ValueError("native producer did not resume after its prior ACK")
                if counter.producer_index != previous_producer:
                    raise ValueError("one maintenance batch advanced the producer per target")
            if len(controller.load_queue) != 4:
                raise ValueError("multi-owner restore did not retain its four native copy operations")
        if (
            counter.producer_index != (previous_producer + 1) % counter.num_counters
            or len(controller.ack_load_queue) != 1
            or len(controller.ack_load_queue[0].child_receipts) != 4
        ):
            raise ValueError("multi-owner restore did not share one native producer and ACK")
        poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
        for target_owner, target_snapshot, target_tokens, target_expected in targets:
            target_match = cache.match_prefix(MatchPrefixParams(key=key(target_tokens)))
            if not equal(target_match.device_indices, target_expected):
                raise ValueError("multi-owner restore changed a native layer's KV bytes")
            if actor.demote(target_snapshot, owner=target_owner, max_tokens=2) != 2:
                raise ValueError("multi-owner restore leaked a native GPU capacity claim")
        if allocator.available_size() != 100:
            raise ValueError("producer-slot fixture changed unrelated cached capacity")
        cleanup_backup = actor.backup(peer_snapshot, owner=peer_owner)
        cleanup_d2h = sum(item[2] for item in cleanup_backup)
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        if actor.demote(peer_snapshot, owner=peer_owner, max_tokens=28) != 8 or (
            actor.demote(snapshot, owner=owner, max_tokens=28) != 20
        ):
            raise ValueError("native growth fixture could not release earlier settled cache")
        if allocator.available_size() != 128:
            raise ValueError("native growth fixture inherited unexpected physical allocations")
        growth_tokens = list(range(3001, 3009))
        growth_indices = allocator.alloc(8)
        fill(growth_indices, growth_tokens)
        growth_expected = saved(growth_indices)
        growth_leaf = insert(growth_tokens, growth_indices)
        growth_owner = ("workflow", "capacity-target", 0)
        growth_snapshot = actor.capture(growth_leaf.id, owner=growth_owner)
        torch.cuda.current_stream().synchronize()
        if not actor.backup(growth_snapshot, owner=growth_owner):
            raise ValueError("native growth target did not acquire its real Host source")
        poll_until(lambda: not cache.ongoing_write_through, cache.writing_check)
        if actor.demote(growth_snapshot, owner=growth_owner, max_tokens=8) != 8:
            raise ValueError("native growth fixture could not demote its target")

        def growth_request(identity, context, tokens):
            req = Req(
                rid=identity, origin_input_text="", origin_input_ids=array("q", tokens),
                sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=1),
                extra_key="workflow", cache_salt="workflow",
                beliefkv_metadata={
                    "root_workflow_id": "workflow", "invocation_id": context,
                    "context_id": context, "context_epoch": 0,
                },
            )
            if not runtime.register_visible_request(req):
                raise ValueError("native growth fixture request was not registered")
            return req

        source = growth_request("capacity-history", "capacity-target", growth_tokens)
        source.finished_reason = FINISH_LENGTH(0)
        runtime.on_batch_completed(SimpleNamespace(reqs=[source]))
        waiting = growth_request("capacity-waiting", "capacity-target", growth_tokens + [3010])
        pressure = allocator.alloc(119)
        busy = growth_request("capacity-busy", "capacity-busy", range(4001, 4060))
        busy.prefix_indices = pressure[:55]
        busy.kv.kv_allocated_len = busy.kv.kv_committed_len = 55
        busy._refresh_fill_ids()
        adapter.candidate_result(busy, admitted=True, result="CONTINUE")
        adapter.steps, adapter._prefetch_order = {"capacity-target": 1}, ("capacity-target",)
        adapter._graph_dirty = False
        adder = PrefillAdder(1, cache, allocator, None, 0., 64, 4)
        adder.add_chunked_req(busy)
        runtime.plan_native_prefill([waiting], running_batch=None, adder=adder)
        if (
            busy.extend_range.length != 4
            or adapter.counts["maintenance_deferred_after_native_selection"] != 1
            or cache.ongoing_load_back
        ):
            raise ValueError("paper planning mutated capacity after the native chunk was selected")
        now = time.monotonic() + 1.
        adapter.tick(now=now)
        if (
            adapter.counts["native_growth_reserve_tokens"] != 5
            or allocator.available_size() != 9 or cache.ongoing_load_back
        ):
            raise ValueError("speculative restore took the admitted native chunk's unallocated input")
        chunk = alloc_token_slots(cache, 4)
        if len(chunk) != 4:
            raise ValueError("protected native chunk did not materialize its real device slots")
        allocator.free(chunk)
        allocator.free(pressure)
        busy.finished_reason = FINISH_LENGTH(0)
        runtime.on_batch_completed(SimpleNamespace(reqs=[busy]))
        adapter.tick(now=now + 1.)
        poll_until(lambda: not cache.ongoing_load_back, cache.loading_check)
        restored_growth = cache.match_prefix(MatchPrefixParams(key=key(growth_tokens)))
        if len(restored_growth.device_indices) != 8 or not equal(
            restored_growth.device_indices, growth_expected,
        ):
            raise ValueError("paper prefetch did not resume with correct bytes after native growth")
        if actor.demote(growth_snapshot, owner=growth_owner, max_tokens=8) != 8:
            raise ValueError("native growth qualification leaked speculative device capacity")
        if allocator.available_size() != 128:
            raise ValueError("native growth qualification left private device allocations")
        session_evidence = qualify_radix_sessions(
            runtime, cache, allocator, commits, fill=fill, saved=saved, equal=equal,
            synchronize=torch.cuda.current_stream().synchronize,
        )
        totals = {
            direction: sum(
                dict(commit.num_tokens_by_pool).get("kv", 0)
                for commit in commits if commit.direction == direction
            ) for direction in ("d2h", "h2d")
        }
        if totals != {
            "d2h": 50 + extra_d2h + cleanup_d2h + session_evidence["native_ack_tokens"]["d2h"],
            "h2d": 42 + session_evidence["native_ack_tokens"]["h2d"],
        }:
            raise ValueError("progressive native ACK totals disagree with physical grants")
        if (
            runtime.physical_disabled or actor.receipts.pending_count
            or runtime.counts["paper_native_commits_completed"] == 0
        ):
            raise ValueError("paper-owned native ACKs did not reconcile in their runtime")
        return {
            "status": "native_prefix_transfers_qualified",
            "scope": "Small real two-layer native FULL GPU/Host pools; no LLM or scheduler performance claim.",
            "native_engine": state, "native_patch_sha256": sha256_file(canonical_patch),
            "gpu": torch.cuda.get_device_name(), "owned_gpu_pid": os.getpid(),
            "layers": 2, "initial_tokens": 16, "growth_tokens": 4,
            "unrelated_peer_backup_tokens": 8,
            "shared_active_tokens": 8, "demoted_and_restored_tokens": 12,
            "native_ack_tokens": totals, **dict.fromkeys(REQUIRED_EVIDENCE, True),
            "initial_lifecycle_native_ack_tokens": initial_totals,
            "progressive_restore_grants": progressive_grants,
            "spatial_capacity_counts": dict(capacity.counts),
            "transfer_counts": dict(actor.counts), "cache_counts": dict(priorities.counts),
            "runtime_counts": dict(runtime.counts),
            "native_growth_guard_counts": dict(adapter.counts),
            "native_growth_cleanup_backup_tokens": cleanup_d2h,
            "native_radix_session": session_evidence,
            "whole_llm_scheduler_or_baseline_qualified": False, "performance_accepted": False,
        }
    finally:
        if busy_prefix_lock is not None:
            cache.dec_lock_ref(snapshot.leaf_id, busy_prefix_lock)
        if capacity is not None:
            capacity.close()
        runtime.close()
        authority.__exit__(None, None, None)
        cache.release_host_resources()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-checkout", required=True, type=Path)
    parser.add_argument("--canonical-patch", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        result = qualify(args.engine_checkout.resolve(), args.canonical_patch.resolve())
    except BaseException as error:
        result = {
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
            "performance_accepted": False,
        }
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        result["source_sha256"] = {
            name: sha256_file(ROOT / name) for name in QUALIFIED_SOURCES
        }
        with (args.output_dir / "summary.json").open("x") as output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write("\n")
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
