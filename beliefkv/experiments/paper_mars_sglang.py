"""The SGLang request, cache, and batch bindings for the pinned MARS scheduler."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from beliefkv.experiments.paper_mars_policy import Request, author_scheduler


SOURCE_ENV = "BELIEFKV_MARS_AUTHOR_SOURCE"
BLOCK_SIZE = 16


def _pages(tokens):
    return (tokens + BLOCK_SIZE - 1) // BLOCK_SIZE


def _restore_repetition_history(batch, *, exclude_last=False):
    import torch
    from sglang.srt.sampling.penaltylib import BatchedRepetitionPenalizer

    orchestrator = batch.sampling_info.penalizer_orchestrator
    penalty = orchestrator.penalizers[BatchedRepetitionPenalizer]
    if not penalty.is_prepared():
        return
    rows, tokens = [], []
    for index, req in enumerate(batch.reqs):
        if req.sampling_params.repetition_penalty == 1.0:
            continue
        ids = set(req.output_ids[:-1] if exclude_last else req.output_ids)
        rows.extend([index] * len(ids))
        tokens.extend(ids)
    if rows:
        indices = torch.tensor([rows, tokens], dtype=torch.int64).to(batch.device)
        row_ids, token_ids = indices.unbind()
        penalty.cumulated_repetition_penalties[row_ids, token_ids] = (
            penalty.repetition_penalties[row_ids, 0]
        )


class NativeSlots:
    def __init__(self, scheduler, evict_params, release_kv_cache):
        self.scheduler = scheduler
        self.tree = scheduler.tree_cache
        self.allocator = scheduler.token_to_kv_pool_allocator
        self.evict_params = evict_params
        self.release_kv_cache = release_kv_cache
        self.allocations = {}
        self.reserved_tokens = 0
        self.reserved_rows = 0
        self.blocks = SimpleNamespace(get_block_ids=lambda: ())

    def begin(self):
        self.allocations.clear()
        self.reserved_tokens = self.reserved_rows = 0

    def create_empty_block_list(self):
        return self.blocks

    def get_computed_blocks(self, request):
        req = request.native
        req.init_next_round_input(self.tree)
        matched = len(req.prefix_indices)
        if matched % BLOCK_SIZE or req.host_hit_length:
            raise ValueError("MARS waiting prefix must contain only complete HBM blocks")
        return self.blocks, matched

    def allocate_slots(
        self, request, num_new_tokens, num_new_computed_tokens=0, _blocks=None,
        *, num_lookahead_tokens, delay_cache_blocks=False, num_encoder_tokens=0,
    ):
        if num_lookahead_tokens or delay_cache_blocks or num_encoder_tokens:
            raise ValueError("MARS replay allocation requires local text KV")
        req = request.native
        new = not req.kv.holds_kv
        if new and self.scheduler.req_to_token_pool.available_size() <= self.reserved_rows:
            raise RuntimeError("MARS request row pool is smaller than its author window")
        start = request.num_computed_tokens + num_new_computed_tokens
        end = start + num_new_tokens
        tokens = (_pages(end) - _pages(start)) * BLOCK_SIZE
        if new:
            # vLLM counts an unlocked cached prefix as free until its allocation.
            self.tree.inc_lock_ref(req.last_node)
        shortfall = self.reserved_tokens + tokens - self.allocator.available_size()
        if shortfall > self.tree.evictable_size():
            if new:
                self.tree.dec_lock_ref(req.last_node)
            return None
        if shortfall > 0:
            self.tree.evict(self.evict_params(num_tokens=shortfall, page_exact=True))
            if self.allocator.available_size() < self.reserved_tokens + tokens:
                raise RuntimeError("native allocator did not release the planned MARS blocks")
        if request.request_id in self.allocations:
            raise RuntimeError("duplicate MARS allocation in one step")
        self.allocations[request.request_id] = (start, end, new, tokens)
        self.reserved_tokens += tokens
        self.reserved_rows += int(new)
        return self.blocks

    def get_blocks(self, request_id):
        assert request_id in self.allocations
        return self.blocks

    def free(self, request):
        """Undo the staged allocation and release exactly the victim chosen by MARS."""
        req = request.native
        allocation = self.allocations.pop(request.request_id, None)
        if allocation is not None:
            _, _, new, tokens = allocation
            self.reserved_tokens -= tokens
            self.reserved_rows -= int(new)
            if new:
                self.tree.dec_lock_ref(req.last_node)
        if req.kv.holds_kv:
            # Retain materialized complete blocks as evictable prefix-cache entries.
            self.release_kv_cache(req, self.tree)
        req.reset_for_retract()

    def get_num_common_prefix_blocks(self, _request, _request_count):
        # SGLang builds its own attention metadata.
        return [0]

    def take_events(self):
        return None


class MarsNativeSchedule:
    def __init__(self, scheduler, author_checkout):
        from sglang.srt.mem_cache.allocator.paged import PagedTokenToKVPoolAllocator
        from sglang.srt.mem_cache.base_prefix_cache import EvictParams
        from sglang.srt.mem_cache.common import release_kv_cache
        from sglang.srt.mem_cache.radix_cache import RadixCache

        if (
            scheduler.enable_overlap
            or (scheduler.ps.tp_size, scheduler.ps.pp_size, scheduler.ps.dp_size) != (1, 1, 1)
            or not scheduler.spec_algorithm.is_none()
            or scheduler.disaggregation_mode.value != "null"
            or getattr(scheduler, "beliefkv_runtime", None) is not None
            or type(scheduler.tree_cache) is not RadixCache
            or type(scheduler.token_to_kv_pool_allocator) is not PagedTokenToKVPoolAllocator
            or scheduler.tree_cache.disable
            or scheduler.page_size != BLOCK_SIZE
            or scheduler.max_prefill_tokens != scheduler.chunked_prefill_size
        ):
            raise ValueError("MARS replay requires synchronous single-rank text execution with paged HBM radix")
        self.scheduler = scheduler
        self.last_decode = None
        self.slots = NativeSlots(scheduler, EvictParams, release_kv_cache)
        self.policy = author_scheduler(Path(author_checkout))(
            self.slots, block_size=BLOCK_SIZE, max_tokens=scheduler.max_prefill_tokens,
            max_reqs=scheduler.max_running_requests, max_model_len=scheduler.max_req_len,
        )

    def stats(self):
        policy = self.policy
        allocator = self.slots.allocator
        total = allocator.size
        free = allocator.available_size() + self.slots.tree.evictable_size()
        if not 0 <= free <= total or (total % BLOCK_SIZE or free % BLOCK_SIZE):
            raise RuntimeError("MARS native stats do not describe whole KV blocks")
        waiters = {req.rid for req in self.scheduler.waiting_queue}
        return {
            "block_size": BLOCK_SIZE, "num_gpu_blocks": total // BLOCK_SIZE + 1,
            "num_free_blocks": free // BLOCK_SIZE, "usage": 1. - free / total,
            "total_tokens": total, "free_tokens": free,
            "scheduling_policy": policy.policy.value, "max_num_seqs": policy.max_num_running_reqs,
            "running_requests": sum(not req.native.finished() for req in policy.running),
            "waiting_requests": len(waiters),
        }

    def _sync_requests(self, abort_message, last_batch):
        scheduler = self.scheduler
        policy = self.policy
        if last_batch is not None:
            for native in last_batch.reqs:
                if not native.finished() and native.kv.holds_kv:
                    committed = native.kv.kv_committed_len
                    if committed // BLOCK_SIZE * BLOCK_SIZE <= native.kv.cache_protected_len:
                        continue
                    # Only completed pages are visible to the next scheduler step.
                    native._refresh_fill_ids()
                    native.set_extend_range(0, committed)
                    self.slots.tree.cache_unfinished_req(native, chunked=True)
        waiters = {req.rid for req in scheduler.waiting_queue}
        for req in tuple(policy.requests.values()):
            native = req.native
            if native.to_finish is not None and req in policy.running and not native.finished():
                native.update_finish_state(0)
                self.slots.release_kv_cache(native, self.slots.tree, is_insert=False)
                scheduler.ipc_channels.send_to_tokenizer.send_output(abort_message(native), native)
            if native.finished() or (req not in policy.running and req.request_id not in waiters):
                policy.retire(req)
        for native in scheduler.waiting_queue:
            if native.rid not in policy.requests:
                sampling = native.sampling_params
                if (
                    native.multimodal_inputs or native.grammar is not None
                    or native.return_logprob or native.lora_id is not None
                    or native.session is not None or native.input_embeds is not None
                    or native.positional_embed_overrides is not None
                    or native.beam_group is not None
                    or sampling.frequency_penalty or sampling.presence_penalty
                    or sampling.min_new_tokens
                ):
                    raise ValueError("request outside the MARS text replay contract")
                policy.add_request(Request(native, native.time_stats.scheduler_recv_time))

    def _batch(self, requests):
        from sglang.srt.managers.schedule_batch import ScheduleBatch

        scheduler = self.scheduler
        batch = ScheduleBatch.init_new(
            reqs=requests, req_to_token_pool=scheduler.req_to_token_pool,
            token_to_kv_pool_allocator=self.slots.allocator, tree_cache=self.slots.tree,
            model_config=scheduler.model_config, enable_overlap=False,
            spec_algorithm=scheduler.spec_algorithm,
        )
        # The author already chose and reserved these exact pages.
        batch._paper_mars_planned = True
        return batch

    def _decode(self, requests, prefixes):
        import torch
        from sglang.srt.sampling.sampling_batch_info import SamplingBatchInfo

        batch = self._batch(requests)
        rows = [req.kv.req_pool_idx for req in requests]
        batch.req_pool_indices_cpu = torch.tensor(rows, dtype=torch.int64)
        batch.req_pool_indices = batch.req_pool_indices_cpu.to(batch.device)
        batch.seq_lens_cpu = torch.tensor(prefixes, dtype=torch.int64)
        batch.seq_lens = batch.seq_lens_cpu.to(batch.device)
        batch.orig_seq_lens = batch.seq_lens.to(torch.int32)
        batch.multimodal_inputs = [None] * len(requests)
        if self.last_decode is not None and requests == self.last_decode.reqs:
            batch.sampling_info = self.last_decode.sampling_info
            batch.sampling_info.penalizer_orchestrator.batch = batch
        else:
            batch.sampling_info = SamplingBatchInfo.from_schedule_batch(
                batch, self.scheduler.model_config.vocab_size,
            )
            _restore_repetition_history(batch, exclude_last=True)
        batch.prepare_for_decode()
        self.last_decode = batch
        return batch

    def next_batch(self, *, abort_message, set_schedule_time, last_batch=None):
        from sglang.srt.managers.schedule_batch import ScheduleBatch

        self._sync_requests(abort_message, last_batch)
        self.slots.begin()
        choice = self.policy.schedule()
        prefills, decodes, decode_prefixes = [], [], []
        for rid in choice.num_scheduled_tokens:
            request = self.policy.requests[rid]
            req = request.native
            start, end, new, _ = self.slots.allocations[rid]
            req._paper_mars_middle_chunk = end < request.num_tokens
            if not new and end == start + 1 and start >= request.num_prompt_tokens and end == request.num_tokens:
                decodes.append(req)
                decode_prefixes.append(start)
                continue
            req._refresh_fill_ids()
            if not new:
                req.prefix_indices = self.scheduler.req_to_token_pool.req_to_token[
                    req.kv.req_pool_idx, :start
                ].to(dtype=req.prefix_indices.dtype, copy=True)
            req.set_extend_range(start, end)
            req.inflight_middle_chunks += int(req._paper_mars_middle_chunk)
            prefills.append(req)

        batch = None
        if prefills:
            batch = self._batch(prefills)
            batch.contains_last_prefill_chunk = bool(decodes) or any(
                not req._paper_mars_middle_chunk for req in prefills
            )
            batch.prepare_for_extend()
            if any(req.output_ids for req in prefills):
                _restore_repetition_history(batch)
        if decodes:
            decode = self._decode(decodes, decode_prefixes)
            if batch is None:
                batch = decode
            else:
                batch.decoding_reqs = decodes
                batch.mix_with_running(decode)
        else:
            self.last_decode = None
        if batch is not None:
            set_schedule_time(batch)
        running = [req.native for req in self.policy.running]
        self.scheduler.waiting_queue[:] = [
            req.native for queue in (self.policy.waiting, self.policy._mars_overflow) for req in queue
        ]
        return batch, ScheduleBatch(reqs=running)


def resolve_mars_schedule(scheduler):
    path = os.environ.get(SOURCE_ENV)
    return MarsNativeSchedule(scheduler, path) if path else None
