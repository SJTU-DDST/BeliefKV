from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import synthetic_runtime


@pytest.mark.parametrize("profiling", (False, True))
def test_completion_ids_include_finished_untracked_requests_and_are_fresh_each_batch(profiling):
    runtime = NativeAdmissionRuntime()
    runtime._hotpath_timing = HotpathTiming(enabled=profiling)
    runtime._tool_timing_only = True
    done = NS(rid="untracked-done", finished=Mock(return_value=True))
    active = NS(rid="untracked-active", finished=Mock(return_value=False))
    batch = NS(reqs=(done, active))
    try:
        assert runtime.on_batch_completed(batch) == ("untracked-done",)
        done.finished.assert_called_once_with()
        active.finished.assert_called_once_with()
        active.finished.return_value = True
        assert runtime.on_batch_completed(NS(reqs=(active,))) == ("untracked-active",)
        assert runtime.on_batch_completed(NS(reqs=())) == ()
        if profiling:
            phase = runtime._hotpath_timing.snapshot()["phases"]["batch_completed"]
            assert phase["count"] == 3
    finally:
        runtime.close()


@pytest.mark.parametrize("timing_only", (False, True))
def test_completion_ids_keep_visible_retirement_and_context_accounting(timing_only):
    runtime, requests = synthetic_runtime(
        NativeAdmissionRuntime, CausalFrontierScheduler, 2, 1,
    )
    runtime._tool_timing_only = timing_only
    active, done = requests
    done.finished = Mock(return_value=True)
    done.origin_input_ids, done.output_ids = (1, 2, 3), (4, 5)
    key = runtime.visible[done.rid]
    revision = runtime.semantic_revision
    try:
        assert runtime.on_batch_completed(NS(reqs=requests)) == (done.rid,)
        assert done.rid not in runtime.visible
        assert active.rid in runtime.visible
        assert runtime.semantic_revision == revision + 1
        assert runtime._context_tokens[key.context_id] == (key.context_epoch, 3, 2, True)
        assert runtime.context_sessions[key.context_id] == key
        done.finished.assert_called_once_with()
    finally:
        runtime.close()


@pytest.mark.parametrize("timing_only", (False, True))
def test_empty_completion_ids_still_record_first_service_before_ack(timing_only):
    runtime, requests = synthetic_runtime(
        NativeAdmissionRuntime, CausalFrontierScheduler, 1, 1,
    )
    runtime._tool_timing_only = timing_only
    request = requests[0]
    key = runtime.visible[request.rid]
    step = PrefetchLoadStep(key, 11, 1., 11, 1., False)
    runtime._prefetch_steps["pending-load"] = (step, "tool_wait", 0.)
    try:
        assert runtime.on_batch_completed(NS(reqs=requests)) == ()
        assert runtime._prefetch_first_services["pending-load"][1] == request.rid
        assert request.rid in runtime.visible
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()
