import json

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
from scripts.evaluate_workflow_service_slo import evaluate
from tests.test_workflow_metrics import event


def replay(first, finish, *, token_hash="same"):
    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    metrics.submit("r", "w", 0.)
    metrics.progress("r", 1, first)
    metrics.progress("r", 2, first + 1.)
    metrics.complete("r", 2, finish)
    event(metrics, Kind.WORKFLOW_END, finish, "w")
    return {
        "causal_execution_completed": True, "exact_tokens_verified": True,
        "original_native_stopping_verified": True,
        "streaming_response_metrics_enabled": True,
        "streaming_response_metrics": metrics.summary(),
        "source_plan": {"requests": 1, "output_tokens": 2},
        "workflow_elapsed_ms": {"w": finish},
        "token_source": {"token_source_sha256": token_hash},
    }


def test_common_budget_grid_keeps_completed_tails_and_whole_run_throughput(tmp_path):
    paths = []
    for label, result in (("a", replay(10., 100.)), ("b", replay(1000., 2000.))):
        path = tmp_path / (label + ".json")
        path.write_text(json.dumps(result))
        paths.append((label, path))
    report = evaluate(paths, [100., 1500.], [10., 100.])
    assert report["all_normal_tails_retained"]
    assert not report["performance_accepted"]
    a, b = report["results"]["a"], report["results"]["b"]
    assert a["whole_run_output_tokens_per_second"] == 20.
    assert b["whole_run_output_tokens_per_second"] == 1.
    assert len(a["service_budgets"]) == len(b["service_budgets"]) == 4
    assert a["service_budgets"][0]["equal_workflow_attainment"] == 1.
    assert b["service_budgets"][0]["equal_workflow_attainment"] == 0.
    assert b["service_budgets"][2]["equal_workflow_attainment"] == 1.
    assert b["workflow_jct_ms"]["mean"] == 2000.


@pytest.mark.parametrize("change", (
    "tokens", "streaming", "incomplete", "coverage", "duplicate_label",
))
def test_work_or_protocol_mismatch_is_rejected(tmp_path, change):
    a, b = replay(10., 100.), replay(100., 200.)
    if change == "tokens":
        b["token_source"]["token_source_sha256"] = "foreign"
    elif change == "streaming":
        b["streaming_response_metrics_enabled"] = False
    elif change == "incomplete":
        b["causal_execution_completed"] = False
    elif change == "coverage":
        b["streaming_response_metrics"]["coverage"]["completed_requests"] = 0
    paths = []
    for label, result in (("a", a), ("a" if change == "duplicate_label" else "b", b)):
        path = tmp_path / (str(len(paths)) + ".json")
        path.write_text(json.dumps(result))
        paths.append((label, path))
    with pytest.raises(ValueError):
        evaluate(paths, [100.], [100.])
