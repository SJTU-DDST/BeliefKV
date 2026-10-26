import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.workflow_metrics import (
    StreamingWorkflowMetrics, evaluate_workflow_service_slo,
)


def event(metrics, kind, now, workflow="w"):
    metrics.event(RuntimeEvent(f"{workflow}:{kind}:{now}", now, kind, workflow))


def test_streaming_interactivity_and_workflow_ttft_include_initial_admission_wait():
    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    metrics.submit("r", "w", 100.)
    metrics.progress("r", 1, 1100.)
    metrics.progress("r", 2, 1200.)
    metrics.progress("r", 3, 1300.)
    metrics.complete("r", 3, 1400.)
    event(metrics, Kind.WORKFLOW_END, 2000.)
    request, result = metrics.request_summary("r"), metrics.summary()
    assert request["ttft_ms"] == 1000.
    assert request["interactivity_tps"] == 10.
    assert result["workflow_ttft_ms"]["mean"] == 1100.
    assert result["workflow_jct_ms"]["mean"] == 2000.
    assert result["workflow_max_ready_output_gap_ms"]["mean"] == 1000.
    assert result["workflow_max_post_first_ready_output_gap_ms"]["mean"] == 100.
    assert result["workflow_event_makespan_ms"] == 2000.
    assert result["output_tokens"] == 3
    assert result["all_workflows_completed"]


def test_external_wait_is_excluded_but_reentry_admission_and_kv_waits_are_included():
    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    metrics.submit("a", "w", 0.)
    metrics.progress("a", 1, 10.)
    metrics.complete("a", 1, 11.)
    metrics.submit("b", "w", 1_000_000.)
    metrics.progress("b", 1, 1_000_100.)
    metrics.complete("b", 1, 1_000_101.)
    event(metrics, Kind.WORKFLOW_END, 1_000_102.)
    row = metrics.summary()["workflows"]["w"]
    assert row["ready_output_service_gaps_ms"]["max"] == 100.
    assert row["post_first_ready_output_service_gaps_ms"]["max"] == 100.
    assert row["jct_ms"] == 1_000_102.
    assert row["mean_request_interactivity_tps"] is None


def test_parallel_agent_streams_cannot_reset_workflow_service_age_or_multiply_weight():
    metrics = StreamingWorkflowMetrics()
    for workflow in ("w", "other"):
        event(metrics, Kind.WORKFLOW_START, 0., workflow)
    metrics.submit("a", "w", 0.)
    metrics.submit("b", "w", 900.)
    metrics.progress("b", 1, 1000.)
    metrics.progress("a", 1, 1100.)
    metrics.progress("b", 2, 1200.)
    metrics.progress("a", 2, 1300.)
    for request in ("a", "b"):
        metrics.complete(request, 2, 1400.)
    metrics.submit("c", "other", 0.)
    metrics.progress("c", 1, 1000.)
    metrics.progress("c", 2, 1100.)
    metrics.complete("c", 2, 1200.)
    for workflow in ("w", "other"):
        event(metrics, Kind.WORKFLOW_END, 1500., workflow)
    result = metrics.summary()
    assert result["request_interactivity_tps"]["mean"] == pytest.approx(20. / 3.)
    assert result["equal_workflow_mean_interactivity_tps"]["mean"] == 7.5
    assert result["workflows"]["w"]["ready_output_service_gaps_ms"]["max"] == 1000.
    assert result["workflows"]["w"]["post_first_ready_output_service_gaps_ms"]["max"] == 100.
    assert result["workflows"]["w"]["max_request_ttft_ms"] == 1100.
    assert result["workflows"]["w"]["max_request_output_gap_ms"] == 200.


def test_chunk_coalescing_and_undefined_decode_rates_are_disclosed_without_infinities():
    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    metrics.submit("r", "w", 0.)
    metrics.progress("r", 0, 1.)
    metrics.progress("r", 5, 10.)
    metrics.progress("r", 5, 11.)
    metrics.complete("r", 5, 12.)
    event(metrics, Kind.WORKFLOW_END, 13.)
    result = metrics.summary()
    assert result["coverage"]["coalesced_output_chunks"] == 1
    assert result["coverage"]["undefined_interactivity_requests"] == 1
    assert result["request_interactivity_tps"]["mean"] is None
    assert result["workflows"]["w"]["ready_output_service_gaps_ms"]["count"] == 1


def test_e2e_interactivity_includes_waiting_and_uses_slow_latency_ratio_percentiles():
    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    for request, first, last, complete in (("a", 1., 2., 10.), ("b", 90., 99., 100.)):
        metrics.submit(request, "w", 0.)
        metrics.progress(request, 1, first)
        metrics.progress(request, 2, last)
        metrics.complete(request, 2, complete)
    result = metrics.summary()
    assert metrics.request_summary("a")["e2e_interactivity_tps"] == 200.
    assert metrics.request_summary("b")["e2e_interactivity_tps"] == 20.
    assert result["e2e_normalized_interactivity_p90_tps"] == pytest.approx(1. / .0455)
    assert result["e2e_normalized_interactivity_p75_tps"] == pytest.approx(1. / .03875)


@pytest.mark.parametrize("bad", (True, -1, 1.5))
def test_corrupt_stream_progress_is_not_accepted(bad):
    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    metrics.submit("r", "w", 0.)
    with pytest.raises(ValueError):
        metrics.progress("r", bad, 1.)


def test_first_output_is_separate_from_subsequent_stalls_and_slo_keeps_normal_tails():
    metrics = StreamingWorkflowMetrics()
    for workflow, first, last, end in (
        ("short", 100., 200., 250.), ("tail", 5000., 9000., 10000.),
    ):
        event(metrics, Kind.WORKFLOW_START, 0., workflow)
        metrics.submit(workflow, workflow, 0.)
        metrics.progress(workflow, 1, first)
        metrics.progress(workflow, 2, last)
        metrics.complete(workflow, 2, end)
        event(metrics, Kind.WORKFLOW_END, end, workflow)
    summary = metrics.summary()
    result = evaluate_workflow_service_slo(
        summary, first_output_budget_ms=1000., ready_gap_budget_ms=1000.,
    )
    assert result["workflow_count"] == 2
    assert result["equal_workflow_attainment"] == .5
    assert result["qualifying_workflows_per_second"] == .1
    assert result["qualifying_output_tokens_per_second"] == .2
    assert not result["workflows"]["tail"]["attained"]
    assert summary["workflow_jct_ms"]["mean"] == 5125.
    assert not result["normal_tails_removed"] and not result["policy_modified"]


def test_first_service_pass_does_not_hide_an_excessive_post_start_service_gap():
    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    metrics.submit("r", "w", 0.)
    metrics.progress("r", 1, 1.)
    metrics.progress("r", 2, 1001.)
    metrics.complete("r", 2, 1002.)
    event(metrics, Kind.WORKFLOW_END, 1003.)
    result = evaluate_workflow_service_slo(
        metrics.summary(), first_output_budget_ms=10., ready_gap_budget_ms=100.,
    )
    assert result["workflows"]["w"]["first_output_budget_met"]
    assert not result["workflows"]["w"]["post_first_ready_gap_budget_met"]
    assert result["equal_workflow_attainment"] == 0.


def test_zero_output_and_unfinished_workflows_remain_in_the_slo_denominator():
    metrics = StreamingWorkflowMetrics()
    for workflow in ("complete", "no-output", "unfinished"):
        event(metrics, Kind.WORKFLOW_START, 0., workflow)
        metrics.submit(workflow, workflow, 0.)
    metrics.progress("complete", 1, 10.)
    metrics.complete("complete", 1, 11.)
    metrics.complete("no-output", 0, 12.)
    event(metrics, Kind.WORKFLOW_END, 13., "complete")
    event(metrics, Kind.WORKFLOW_END, 14., "no-output")
    summary = metrics.summary()
    result = evaluate_workflow_service_slo(
        summary, first_output_budget_ms=100., ready_gap_budget_ms=100.,
    )
    assert result["equal_workflow_attainment"] == pytest.approx(1. / 3.)
    assert not result["all_workflows_completed"]
    assert result["qualifying_workflows_per_second"] is None
    assert summary["workflow_event_makespan_ms"] is None
    assert summary["coverage"]["completed_workflows"] == 2
    assert summary["coverage"]["workflows_with_output_timing"] == 1
    assert summary["workflows"]["complete"]["post_first_ready_output_service_gaps_ms"]["count"] == 0
    assert not result["workflows"]["no-output"]["complete_output_timing"]


@pytest.mark.parametrize("budget", (True, 0., -1., float("inf"), float("nan")))
def test_invalid_service_budgets_are_rejected(budget):
    with pytest.raises(ValueError, match="budgets"):
        evaluate_workflow_service_slo(
            StreamingWorkflowMetrics().summary(),
            first_output_budget_ms=budget, ready_gap_budget_ms=100.,
        )
