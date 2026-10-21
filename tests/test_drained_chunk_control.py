from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import synthetic_runtime


def _scene():
    runtime, requests = synthetic_runtime(
        NativeAdmissionRuntime, CausalFrontierScheduler, 4, 1,
    )
    runtime._tool_timing_only = True
    runtime._native_chunk_input_reserve = 4096
    for req in requests:
        req.origin_input_ids = range(129)
    adder = NS(
        max_running_requests=8, max_prefill_bs=8, prefill_max_requests=None,
        page_size=64, rem_input_tokens=12288, rem_chunk_tokens=0,
        dllm_config=None, can_run_list=[NS(rid="continued-chunk")],
    )
    return runtime, requests, adder


def test_drained_chunk_keeps_current_identity_checks_and_next_batch_reserve():
    runtime, requests, adder = _scene()
    stale = runtime.visible[requests[-1].rid]
    runtime.graph.contexts[stale.context_id].epoch += 1
    rank = runtime._prefill_causal_ranks = Mock(wraps=runtime._prefill_causal_ranks)
    try:
        plan = runtime.plan_native_prefill(requests, running_batch=None, adder=adder)
        rank.assert_not_called()
        selection = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert [req.rid for req in selection.candidates] == [
            req.rid for req in requests[:-1]
        ]
        assert any(rid == requests[-1].rid for rid, _ in selection.rejected)
        assert runtime._native_chunk_input_reserve == 4096
        assert runtime._native_input_reserve == 4096
        assert not runtime._residency_ordering_hints
        assert runtime.physical_ledger.pending_count == 0
        assert runtime.counts["drained_chunk_prefill_plans"] == 1
        adder.rem_chunk_tokens = 64
        runtime.plan_native_prefill(requests, running_batch=None, adder=adder)
        rank.assert_called_once()
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "empty_batch", "remaining_tokens", "unknown_chunk", "dllm",
    "empty_input", "unknown_input", "untagged_empty_input", "demand_predictions",
))
def test_uncertain_or_executable_pass_still_uses_normal_planning(change):
    runtime, requests, adder = _scene()
    if change == "empty_batch":
        adder.can_run_list = []
    elif change == "remaining_tokens":
        adder.rem_chunk_tokens = 64
    elif change == "unknown_chunk":
        adder.rem_chunk_tokens = None
    elif change == "dllm":
        adder.dllm_config = object()
    elif change in ("empty_input", "unknown_input"):
        requests[0].origin_input_ids = () if change == "empty_input" else None
    elif change == "untagged_empty_input":
        requests.append(NS(rid="plain", beliefkv_metadata=None, origin_input_ids=()))
    else:
        runtime._tool_timing_only = False
    runtime._prefill_causal_ranks = Mock(side_effect=RuntimeError("normal planning"))
    try:
        with pytest.raises(RuntimeError, match="normal planning"):
            runtime.plan_native_prefill(requests, running_batch=None, adder=adder)
        assert not runtime.counts["drained_chunk_prefill_plans"]
    finally:
        runtime.close()


@pytest.mark.parametrize("prefix", (0, 128))
def test_real_native_adder_rejects_cold_and_hot_input_without_chunk_budget(prefix):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.managers.schedule_policy import AddReqResult, PrefillAdder

    assert Req._compute_max_prefix_len(NS(return_logprob=False), 129) == 128
    adder = PrefillAdder.__new__(PrefillAdder)
    adder.page_size = 64
    adder.rem_chunk_tokens = 0
    adder.rem_input_tokens = 12288
    adder.rem_total_token_offset = 0
    adder.is_all_swa = False
    adder.is_hybrid_ssm_cache = True
    adder.token_to_kv_pool_allocator = NS(available_size=lambda: 1_000_000)
    adder.tree_cache = NS(full_evictable_size=lambda: 0)
    adder.can_run_list = [NS(rid="continued-chunk")]
    adder.dllm_config = None
    adder.is_hybrid_swa = False
    adder.exact_chunk_fill = False
    req = NS(
        full_untruncated_fill_ids=range(129), prefix_indices=range(prefix),
        sampling_params=NS(max_new_tokens=16),
    )
    assert adder._select_prefill_admission(
        req, total_tokens=1024, host_hit_length=0, swa_host_hit_length=0,
        truncation_align_size=None,
    ) is AddReqResult.OTHER
    adder.rem_chunk_tokens = 64
    admitted = adder._select_prefill_admission(
        req, total_tokens=1024, host_hit_length=0, swa_host_hit_length=0,
        truncation_align_size=None,
    )
    assert not isinstance(admitted, AddReqResult)
    assert admitted.extend_len > 0


def test_exhausted_skip_preserves_committed_chunk_and_next_batch_capacity():
    runtime, requests, adder = _scene()
    continued = adder.can_run_list[0]
    running = NS(reqs=[NS(rid="running")])
    runtime._prefill_causal_ranks = Mock(side_effect=AssertionError("unneeded ranking"))
    runtime.inspect_context_h2d_opportunity = Mock(side_effect=AssertionError("unneeded H2D"))
    try:
        assert runtime.skip_exhausted_native_prefill(
            requests, running_batch=running, adder=adder,
        )
        assert adder.can_run_list == [continued]
        assert adder.rem_chunk_tokens == 0
        assert runtime._native_running_batch is running
        assert runtime._native_input_reserve == 4096
        assert runtime._native_chunk_input_reserve == 4096
        assert set(runtime.visible) == {request.rid for request in requests}
        assert runtime.counts["drained_chunk_prefill_skipped"] == 1
        assert not runtime.physical_ledger.pending_count
        adder.rem_chunk_tokens = 64
        assert not runtime.skip_exhausted_native_prefill(
            requests, running_batch=running, adder=adder,
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "empty_batch", "remaining_tokens", "negative_budget", "boolean_budget",
    "unknown_chunk", "dllm", "empty_input", "unknown_input",
    "untagged_empty_input", "single_tagged", "demand_predictions", "native_order",
))
def test_exhausted_skip_keeps_unknown_and_potentially_executable_requests(change):
    runtime, requests, adder = _scene()
    if change == "empty_batch":
        adder.can_run_list = []
    elif change == "remaining_tokens":
        adder.rem_chunk_tokens = 64
    elif change == "negative_budget":
        adder.rem_chunk_tokens = -1
    elif change == "boolean_budget":
        adder.rem_chunk_tokens = False
    elif change == "unknown_chunk":
        adder.rem_chunk_tokens = None
    elif change == "dllm":
        adder.dllm_config = object()
    elif change in ("empty_input", "unknown_input"):
        requests[0].origin_input_ids = () if change == "empty_input" else None
    elif change == "untagged_empty_input":
        requests.append(NS(rid="plain", beliefkv_metadata=None, origin_input_ids=()))
    elif change == "single_tagged":
        requests = requests[:1]
    elif change == "demand_predictions":
        runtime._tool_timing_only = False
    else:
        runtime._native_prefill_order = True
    runtime._observe_native_admission_capacity = Mock()
    try:
        assert not runtime.skip_exhausted_native_prefill(
            requests, running_batch=None, adder=adder,
        )
        runtime._observe_native_admission_capacity.assert_not_called()
        assert not runtime.counts["drained_chunk_prefill_skipped"]
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()
