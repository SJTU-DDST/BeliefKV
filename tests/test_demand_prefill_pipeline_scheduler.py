"""Execute the changed native scheduling method with real CPU admission."""

import __future__
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.test_demand_prefill_pipeline_native import staged_native
from tests.test_full_only_native_lifecycle import full_native


class BatchConstructionReached(Exception):
    pass


@pytest.mark.parametrize("staged_native", ({"members": 2, "select": False},), indirect=True)
@pytest.mark.parametrize("enabled", (False, True))
def test_native_batch_restore_defers_callbacks_without_splitting_selected_members(staged_native, enabled):
    from sglang.srt.managers.schedule_policy import AddReqResult, PrefillAdder

    f = staged_native
    source = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]) / "python/sglang/srt/managers/scheduler.py"
    cls = next(
        node for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "Scheduler"
    )
    method = next(
        node for node in cls.body if isinstance(node, ast.FunctionDef)
        and node.name == "_get_new_batch_prefill_raw"
    )
    construction = []

    def create(reqs, *_args, **_kwargs):
        construction.append(tuple(reqs))
        raise BatchConstructionReached

    null, prefill = object(), object()
    scope = {
        "PrefillAdder": PrefillAdder, "AddReqResult": AddReqResult,
        "get_schedule": lambda: NS(prefill_max_requests=None), "TEST_RETRACT": False,
        "DisaggregationMode": NS(PREFILL=prefill),
        "set_time_batch": lambda *_args: None,
        "ScheduleBatch": NS(init_new=create),
    }
    exec(compile(
        ast.Module(body=[method], type_ignores=[]), str(source), "exec",
        flags=__future__.annotations.compiler_flag,
    ), scope)
    f.runtime.defer_prefill_for_prefetch = lambda _: False
    f.runtime.on_prefill_candidate_result = Mock(return_value=False)
    running = f.running
    running.batch_is_full = False
    scheduler = NS(
        _beliefkv_demand_pipeline=f.pipeline if enabled else None,
        beliefkv_runtime=f.runtime, tree_cache=f.cache,
        token_to_kv_pool_allocator=f.allocator, req_to_token_pool=f.cache.req_to_token_pool,
        chunked_req=None, waiting_queue=f.waiting,
        grammar_manager=NS(has_waiting_grammars=lambda: False),
        enable_priority_preemption=False, is_hybrid_swa=False,
        min_free_slots_delayer=None, policy=NS(calc_priority=lambda *_args, **_kwargs: None),
        processed_tokens_counter=0,
        get_num_allocatable_reqs=lambda *_args, **_kwargs: 24,
        chunked_prefill_size=32, page_size=1,
        tp_worker=NS(model_runner=NS(attn_backend=object())),
        new_token_ratio_tracker=NS(current=1.), max_prefill_tokens=32,
        is_mixed_chunk=False, priority_scheduling_preemption_threshold=0,
        max_prefill_bs=24, max_running_requests=24, dllm_config=None,
        enable_lora=False, enable_hicache_storage=False,
        enable_hierarchical_cache=True, enable_unified_cache_external_linker=False,
        truncation_align_size=None, disaggregation_mode=null,
        _beliefkv_prefill_candidates=lambda *_args: tuple(f.members),
        model_config=object(), enable_overlap=True, spec_algorithm=object(),
    )
    invoke = lambda: scope["_get_new_batch_prefill_raw"](scheduler, None, running)
    if not enabled:
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert construction == [tuple(f.members)]
        assert all(call.kwargs["admitted"] for call in f.runtime.on_prefill_candidate_result.call_args_list)
        assert f.pipeline.pending is None and f.cache.cache_controller.load_queue
        f.cache.ready_to_load_host_cache()
        f.complete.ready = True
        f.cache.loading_check(finish_count=1)
    else:
        assert invoke() == (None, running)
        assert scheduler.waiting_queue == f.members
        assert len(f.loads) == 2 and len(f.pipeline.pending.members) == 2
        assert not construction
        callbacks = f.runtime.on_prefill_candidate_result.call_args_list
        assert len(callbacks) == 2
        assert all(
            call.kwargs == {"admitted": False, "result": "RESTORE_STAGED"}
            for call in callbacks
        )
        assert invoke() == (None, running)
        assert len(f.runtime.on_prefill_candidate_result.call_args_list) == 2
        f.complete.ready = True
        f.cache.loading_check(finish_count=1)
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert construction == [tuple(f.members)]
        assert len(f.loads) == 2 and f.pipeline.pending is None
        assert all(
            call.kwargs["admitted"]
            for call in f.runtime.on_prefill_candidate_result.call_args_list[2:]
        )
    for member in f.members:
        f.cache.dec_lock_ref(member.last_node, member.lock_receipt)
