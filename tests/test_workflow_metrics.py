import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics


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
