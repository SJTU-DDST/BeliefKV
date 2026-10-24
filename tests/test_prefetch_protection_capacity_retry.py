"""Exercise native prefill admission through its full-batch decision on CPU."""

import ast
from enum import Enum
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.test_tool_predictive_transfers import locked_runtime, req


class Result(Enum):
    CONTINUE = 0
    NO_TOKEN = 1


def prefill_selection(adder_factory):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the qualified hybrid engine")
    path = Path(checkout) / "python/sglang/srt/managers/scheduler.py"
    tree = ast.parse(path.read_text())
    scheduler = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Scheduler"
    )
    method = next(
        node for node in scheduler.body
        if isinstance(node, ast.FunctionDef) and node.name == "_get_new_batch_prefill_raw"
    )
    # Run the actual admission loop and empty-result return. Stop before
    # constructing a GPU batch; the returned list is the native adder's list.
    boundary = next(
        index for index, node in enumerate(method.body)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "can_run_set"
            for target in node.targets
        )
    )
    method.body = method.body[:boundary] + ast.parse(
        "return tuple(can_run_list), running_batch"
    ).body
    method.decorator_list = []
    namespace = {
        "PrefillAdder": adder_factory,
        "AddReqResult": Result,
        "TEST_RETRACT": False,
        "DisaggregationMode": NS(PREFILL="prefill"),
        "get_schedule": lambda: NS(prefill_max_requests=24),
    }
    module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ),
        method,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[method.name]


def selection_fixture(runtime, *, fits, can_allocate=True):
    request = req("capacity-waiter")
    request.beam_group = None
    request.kv = NS(mamba_cow_src_index=None, mamba_needs_clear=False, holds_mamba=False)
    request.init_next_round_input = Mock()
    selections = []

    class Adder:
        def __init__(self, *args, **kwargs):
            self.can_run_list = []
            selections.append(self)

        def add_one_req(self, candidate, **kwargs):
            assert candidate is request
            if fits():
                self.can_run_list.append(candidate)
                return Result.CONTINUE
            return Result.NO_TOKEN

        def budget_state(self):
            return Result.CONTINUE

    scheduler = NS(
        grammar_manager=NS(has_waiting_grammars=lambda: False),
        enable_priority_preemption=False, is_hybrid_swa=False,
        waiting_queue=[request], chunked_req=None,
        min_free_slots_delayer=None,
        get_num_allocatable_reqs=lambda *args, **kwargs: int(can_allocate),
        policy=NS(calc_priority=lambda *args, **kwargs: None),
        processed_tokens_counter=0, chunked_prefill_size=4096,
        tp_worker=NS(model_runner=NS(attn_backend=NS())),
        page_size=1, tree_cache=NS(buffer_pipeline=None),
        token_to_kv_pool_allocator=object(),
        new_token_ratio_tracker=NS(current=.5), max_prefill_tokens=4096,
        is_mixed_chunk=False, priority_scheduling_preemption_threshold=0,
        max_prefill_bs=24, max_running_requests=24, dllm_config=None,
        enable_lora=False, req_to_token_pool=NS(),
        _beliefkv_prefill_candidates=lambda *args: (request,),
        disaggregation_mode="null", beliefkv_runtime=runtime,
        enable_hicache_storage=False, truncation_align_size=1,
        enable_hierarchical_cache=True, enable_unified_cache_external_linker=False,
    )
    running = NS(reqs=[object()], batch_is_full=False, is_empty=lambda: False)
    return prefill_selection(Adder), scheduler, running, request, selections


def test_successful_optional_unlock_rearms_the_next_native_prefill_pass():
    runtime, _, _, action, receipt, _ = locked_runtime()
    tree = runtime._native_cache.tree_core
    method, scheduler, running, request, selections = selection_fixture(
        runtime, fits=lambda: (
            runtime._prefetch_service_leases[action.command_id].lock_params is None
        ),
    )
    try:
        assert method(scheduler, None, running)[0] is None
        tree.dec_lock_ref.assert_called_once_with(1, receipt)
        assert runtime._prefetch_service_leases[action.command_id].lock_params is None
        assert not running.batch_is_full
        assert method(scheduler, None, running)[0] == (request,)
        assert len(selections) == 2
        tree.dec_lock_ref.assert_called_once()
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", ("no_optional_lock", "unlock_failed", "request_rows"))
def test_unavailable_capacity_keeps_the_native_full_latch(blocked):
    runtime, _, _, action, _, _ = locked_runtime()
    tree = runtime._native_cache.tree_core
    if blocked == "no_optional_lock":
        runtime._release_prefetch_service_lease(action.command_id, "fixture")
    elif blocked == "unlock_failed":
        tree.dec_lock_ref.side_effect = RuntimeError("uncertain unlock")
    method, scheduler, running, _, selections = selection_fixture(
        runtime, fits=lambda: False, can_allocate=blocked != "request_rows",
    )
    try:
        assert method(scheduler, None, running)[0] is None
        assert running.batch_is_full
        count = len(selections)
        assert method(scheduler, None, running)[0] is None
        assert len(selections) == count
        if blocked == "unlock_failed":
            assert runtime.physical_disabled
            tree.dec_lock_ref.assert_called_once()
        elif blocked == "request_rows":
            tree.dec_lock_ref.assert_not_called()
    finally:
        runtime.close()


def test_a_retry_without_further_unlock_does_not_create_a_polling_loop():
    runtime, _, _, _, _, _ = locked_runtime()
    method, scheduler, running, _, selections = selection_fixture(
        runtime, fits=lambda: False,
    )
    try:
        assert method(scheduler, None, running)[0] is None
        assert not running.batch_is_full
        assert method(scheduler, None, running)[0] is None
        assert running.batch_is_full
        assert method(scheduler, None, running)[0] is None
        assert len(selections) == 2
    finally:
        runtime.close()
