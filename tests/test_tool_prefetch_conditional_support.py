"""Sparse conditional tails cannot authorize speculative tool restoration."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from tests.test_online_tool_history_revision import completed, ready
from tests.test_online_workload_timing import tool_event
from tests.test_tool_predictive_transfers import tool_runtime


@pytest.mark.parametrize("scope,minimum", (("workflow", 4), ("run", 8)))
@pytest.mark.parametrize("survivors", (0, 1, 2, 3, 4, 7, 8))
def test_support_uses_observed_survivors_in_the_selected_history(scope, minimum, survivors):
    model = OnlineWorkloadToolTiming()
    workflow = "workflow" if scope == "workflow" else "other"
    for index in range(16):
        completed(
            model, index, workflow=workflow,
            duration=1000. if index < 16 - survivors else 20_000.,
        )
    ready(model)
    before = model.estimate_revision("workflow", "agent")
    assert model.prefetch_supported(
        "workflow", "agent", now_ms=15_000., scope=scope,
    ) is (survivors >= minimum)
    assert model.estimate_revision("workflow", "agent") == before
    assert not model.prefetch_supported(
        "workflow", "agent", now_ms=30_000., scope=scope,
    )


def test_censored_tail_is_not_evidence_of_a_future_completion():
    model = OnlineWorkloadToolTiming()
    for index in range(12):
        completed(
            model, index, duration=20_000. if index < 8 else 1000.,
            censored=index < 8,
        )
    ready(model)
    assert not model.prefetch_supported(
        "workflow", "agent", now_ms=15_000., scope="workflow",
    )
    assert not model.prefetch_supported(
        "workflow", "agent", now_ms=15_000., scope="run",
    )


def test_parallel_terminal_and_evicted_histories_are_not_prefetch_authority():
    model = OnlineWorkloadToolTiming(max_keys=2)
    for index in range(8):
        completed(model, index, duration=1000.)
    ready(model)
    assert model.prefetch_supported("workflow", "agent", now_ms=10_100., scope="workflow")
    ready(model, call="second")
    assert not model.prefetch_supported("workflow", "agent", now_ms=10_100., scope="workflow")
    model.observe(tool_event(
        RuntimeEventKind.TOOL_END, call="second", duration_ms=float("nan"),
    ))
    assert model.prefetch_supported("workflow", "agent", now_ms=10_100., scope="workflow")
    completed(model, 100, shape="git_status")
    assert not model.prefetch_supported("workflow", "agent", now_ms=10_100., scope="workflow")
    assert not model.prefetch_supported("workflow", "agent", now_ms=10_100., scope="invalid")
    model.observe(tool_event(RuntimeEventKind.RETURN))
    assert not model.prefetch_supported("workflow", "agent", now_ms=10_100., scope="run")


@pytest.mark.parametrize("elapsed", (5000., 19_500.))
def test_sparse_tail_blocks_h2d_without_changing_the_offload_forecast(elapsed):
    runtime, original = tool_runtime()
    model = runtime._online_tool_timing = OnlineWorkloadToolTiming()
    for index, duration in enumerate((1000., 1000., 1000., 1000., 20_000.)):
        completed(model, index, workflow=original.key.root_workflow_id, duration=duration)
    ready(
        model, workflow=original.key.root_workflow_id,
        invocation=original.key.invocation_id,
    )
    now = 10_000. + elapsed
    estimate = model.estimate(
        original.key.root_workflow_id, original.key.invocation_id, now_ms=now,
    )
    hint = replace(
        original, wait_p10_ms=estimate.p10_ms, wait_p50_ms=estimate.p50_ms,
        wait_p90_ms=estimate.p90_ms, release_cdf=estimate.release_cdf,
        issued_monotonic_ms=now, expires_monotonic_ms=now + 1000.,
        residual_support_detail="online_workflow_command",
    )
    runtime.tool_wait_hints[hint.key.context_id] = hint
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now / 1000.):
            assert runtime._tool_prefetch_remaining(hint, .5, now_ms=now) is None
            assert not runtime._tool_prefetch_ready(hint)
            assert hint.remaining_quantile(.5, now_ms=now) == 20_000. - elapsed
            assert runtime._long_tool_wait(hint.key) is (elapsed == 5000.)
            assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_sufficient_same_duration_samples_keep_imminent_prefetch_available():
    runtime, original = tool_runtime()
    model = runtime._online_tool_timing = OnlineWorkloadToolTiming()
    for index in range(8):
        completed(model, index, workflow=original.key.root_workflow_id, duration=20_000.)
    ready(
        model, workflow=original.key.root_workflow_id,
        invocation=original.key.invocation_id,
    )
    estimate = model.estimate(
        original.key.root_workflow_id, original.key.invocation_id, now_ms=29_500.,
    )
    hint = replace(
        original, wait_p10_ms=estimate.p10_ms, wait_p50_ms=estimate.p50_ms,
        wait_p90_ms=estimate.p90_ms, release_cdf=estimate.release_cdf,
        issued_monotonic_ms=29_500., expires_monotonic_ms=30_500.,
        residual_support_detail="online_workflow_command",
    )
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=29.5):
            assert runtime._tool_prefetch_ready(hint)
            assert runtime._tool_prefetch_remaining(hint, .5, now_ms=29_500.) == 500.
    finally:
        runtime.close()
