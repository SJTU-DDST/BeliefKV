"""Read-only physical and native-budget evidence for one extra short prefill."""

from collections import Counter
from copy import copy
from dataclasses import asdict
from itertools import islice
import time

from beliefkv.runtime.sglang_v0520_admission import _request_key


class _LengthOnly:
    def __init__(self, size):
        self.size = size

    def __len__(self):
        return self.size


class _FreeTokens:
    def __init__(self, tokens):
        self.tokens = tokens

    def available_size(self):
        return self.tokens


class _NoEviction:
    def evictable_size(self):
        return 0

    def full_evictable_size(self):
        return 0


def _virtual_shape(req, prefix):
    result = copy(req)
    result.full_untruncated_fill_ids = _LengthOnly(
        len(req.origin_input_ids) + len(req.output_ids)
    )
    result.prefix_indices = _LengthOnly(prefix)
    return result


def _joint_budget(adder, chunked_req, req, checkpoint, free_tokens):
    """Run only native budget code on private copies with zero eviction credit."""
    from sglang.srt.managers.schedule_policy import AddReqResult, CLIP_MAX_NEW_TOKENS

    trial = copy(adder)
    trial.token_to_kv_pool_allocator = _FreeTokens(free_tokens)
    trial.tree_cache = _NoEviction()
    trial.can_run_list, trial.preempt_list = [], []
    trial.prefill_delayer_single_pass = None
    short = _virtual_shape(req, checkpoint)
    inputs = len(req.origin_input_ids) - checkpoint
    max_new = min(
        max(req.sampling_params.max_new_tokens - len(req.output_ids), 0),
        CLIP_MAX_NEW_TOKENS,
    )
    admission = trial._select_prefill_admission(
        short, total_tokens=inputs + max_new + trial.page_size,
        host_hit_length=0, swa_host_hit_length=0, truncation_align_size=None,
    )
    if isinstance(admission, AddReqResult) or admission.is_chunked or admission.extend_len != inputs:
        return None
    short.set_extend_range(checkpoint, checkpoint + admission.extend_len)
    trial.can_run_list.append(short)
    trial._update_prefill_budget(
        admission.prefix_len, admission.extend_len, admission.max_new_tokens,
        req.retracted_stain, compute_charge=(
            admission.extend_len if trial.exact_chunk_fill else None
        ),
    )
    owner = _virtual_shape(chunked_req, len(chunked_req.prefix_indices))
    retained = trial.add_chunked_req(owner)
    if (
        len(trial.can_run_list) != 2 or trial.can_run_list[-1] is not owner
        or owner.extend_range.length < adder.rem_chunk_tokens - adder.rem_chunk_tokens // 8
        or trial.rem_total_tokens <= 0 or trial.cur_rem_tokens <= 0
        or trial.rem_chunk_tokens < 0 or trial.rem_input_tokens < 0
    ):
        return None
    return {
        "short_input_tokens": admission.extend_len,
        "owner_input_tokens": owner.extend_range.length,
        "owner_remains_chunked": retained is owner,
        "remaining_native_total_tokens": trial.rem_total_tokens,
        "remaining_native_current_tokens": trial.cur_rem_tokens,
        "short_output_reserve_tokens": admission.max_new_tokens,
        "combined_input_tokens": admission.extend_len + owner.extend_range.length,
        "native_eviction_credit_tokens": 0,
    }


def inspect_short_continuations(
    runtime, cache, native_order, *, running_batch, adder, chunked_req,
    maximum_checks=8, soft_budget_ms=.5,
):
    """Observe mutually exclusive extra-member shapes; allocate or grant nothing."""
    if type(maximum_checks) is not int or not 1 <= maximum_checks <= 8:
        raise ValueError("short-continuation inspection is bounded to eight queue entries")
    if type(soft_budget_ms) not in (int, float) or not 0 < soft_budget_ms <= 1:
        raise ValueError("require a positive CPU soft budget of at most one millisecond")
    started = time.thread_time()
    reasons, candidates = Counter(), []
    report = {
        "event": "native_short_continuation_probe",
        "observed_monotonic_ms": time.monotonic() * 1000.,
        "candidate_scope": "bounded_native_queue_before_existing_chunk_admission",
        "checked": 0, "candidates": candidates, "reasons": reasons,
        "physical_capacity_credit": False, "priority_or_admission_authority": False,
        "candidate_shapes_individually_checked_not_jointly_granted": True,
    }

    def finish(reason=None):
        if reason:
            reasons[reason] += 1
        report["thread_cpu_ms"] = (time.thread_time() - started) * 1000.
        report["soft_budget_exceeded"] = report["thread_cpu_ms"] > soft_budget_ms
        report["reasons"] = dict(reasons)
        return report

    agents = getattr(runtime, "_agent_admission", None)
    if (
        getattr(runtime, "internal_comparison_mode", None) != "full"
        or getattr(runtime, "_paper_baseline", None) is not None
        or getattr(runtime, "physical_disabled", True)
        or getattr(runtime, "_native_cache", None) is not cache
        or getattr(agents, "strategy", None) != "thunderagent_reference"
        or chunked_req is None or adder is None
        or tuple(getattr(cache, "tree_components", ())) != (0,)
        or getattr(adder, "tree_cache", None) is not cache
        or getattr(adder, "token_to_kv_pool_allocator", None)
        is not getattr(cache, "token_to_kv_pool_allocator", None)
        or getattr(adder, "is_hybrid_swa", True) or getattr(adder, "is_all_swa", True)
        or getattr(adder, "is_hybrid_ssm_cache", True)
        or getattr(adder, "dllm_config", None) is not None
        or getattr(adder, "can_run_list", None)
        or type(getattr(adder, "rem_chunk_tokens", None)) is not int
        or adder.rem_chunk_tokens <= 0
        or type(getattr(adder, "max_running_requests", None)) is not int
    ):
        return finish("unsupported_scope")
    if any(req is chunked_req for req in getattr(running_batch, "reqs", ())):
        return finish("chunk_owner_still_in_running_batch")
    if (
        chunked_req.finished() or getattr(chunked_req, "to_finish", None) is not None
        or len(chunked_req.full_untruncated_fill_ids) - len(chunked_req.prefix_indices)
        <= adder.rem_chunk_tokens
    ):
        return finish("no_long_chunk")
    selected_count = len(getattr(running_batch, "reqs", ())) + 2
    slots = cache.req_to_token_pool.available_size()
    if (
        type(slots) is not int or slots < 2 or selected_count > adder.max_running_requests
        or adder.prefill_max_requests is not None and adder.prefill_max_requests < 2
        or adder.max_prefill_bs and adder.max_prefill_bs < 2
    ):
        return finish("request_slot_limit")
    free = cache.token_to_kv_pool_allocator.available_size()
    if type(free) is not int or free <= 0:
        return finish("physical_free_unavailable")
    report.update(
        physical_free_tokens=free, free_request_rows=slots,
        original_chunk_tokens=adder.rem_chunk_tokens,
        owner_request_id=chunked_req.rid,
        existing_running_requests=len(getattr(running_batch, "reqs", ())),
    )
    cap = min(256, adder.rem_chunk_tokens // 8)
    for req in islice(native_order, maximum_checks):
        if (time.thread_time() - started) * 1000. >= soft_budget_ms:
            reasons["cpu_soft_budget_reached"] += 1
            break
        report["checked"] += 1
        key = _request_key(req, known_keys=runtime.visible)
        if (
            req is chunked_req or key is None or runtime.visible.get(key.request_id) != key
            or runtime._executable_waiting_key(req, key=key) != key
            or not agents.allows(key) or req.finished()
            or getattr(req, "to_finish", None) is not None or len(req.output_ids)
            or getattr(getattr(req, "kv", None), "req_pool_idx", None) is not None
            or getattr(req, "multimodal_inputs", None) is not None
            or getattr(req, "lora_id", None) is not None
            or req.sampling_params.ignore_eos
        ):
            reasons["not_live_unstarted_eligible"] += 1
            continue
        try:
            observation = cache.inspect_beliefkv_reentry(req)
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
            observation = None
            reasons["checkpoint_inspection_failed"] += 1
        if observation is None:
            reasons["checkpoint_unknown"] += 1
            continue
        checkpoint = observation.get("checkpoint_tokens")
        if (
            type(checkpoint) is not int or checkpoint <= 0
            or checkpoint != observation.get("device_checkpoint_tokens")
            or observation.get("missing_full_tokens") != 0
            or observation.get("missing_mamba_slots") != 0
        ):
            reasons["prefix_not_fully_ready_on_device"] += 1
            continue
        if not 0 < len(req.origin_input_ids) - checkpoint <= cap:
            reasons["input_increment_outside_short_budget"] += 1
            continue
        try:
            shape = _joint_budget(adder, chunked_req, req, checkpoint, free)
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
            reasons["native_joint_budget_unknown"] += 1
            continue
        if shape is None:
            reasons["native_joint_budget_refused"] += 1
            continue
        context_limit = getattr(cache.req_to_token_pool, "max_context_len", None)
        if type(context_limit) is not int or max(
            len(req.origin_input_ids),
            len(chunked_req.prefix_indices) + shape["owner_input_tokens"],
        ) > context_limit:
            reasons["request_context_limit"] += 1
            continue
        candidates.append({
            "key": asdict(key), "checkpoint_tokens": checkpoint,
            "observed_ready_join_parent": runtime._ready_join_parent(req, key),
            **shape,
        })
    return finish()
