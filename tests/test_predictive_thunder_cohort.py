from copy import deepcopy

import pytest

from scripts.review_predictive_thunder_cohort import matching_traffic, named_metrics
from tests.test_sustained_oracle_review import censored_report


def plans():
    fields = (
        "source_arm", "source_plan", "token_source_sha256", "source_capacity",
        "source_server_parameters", "engine_tree", "workflow_ids", "sampling_mode",
        "max_running_requests", "request_timeout_s", "stream_response_metrics",
        "workflow_arrivals", "workflow_arrivals_path", "tool_timing_overlay",
    )
    predictive = {**{name: name for name in fields}, "order": ["full"]}
    return predictive, {**deepcopy(predictive), "order": ["thunderagent_acting_decay"]}


def test_matched_baseline_preserves_all_traffic_and_complete_jct():
    matching_traffic(*plans())
    result = named_metrics(censored_report(), censored_report(measured=(80., 200.)))
    group = result["groups"]["measured"]
    assert group["predictive_JCT_ms"]["mean"] < group["thunderagent_JCT_ms"]["mean"]
    assert group["mean_JCT_reduction_percent"] > 0
    assert group["paired_completed_workflows"] == group["fixed_population"]
    assert "oracle_JCT_ms" not in group and "control_JCT_ms" not in group


@pytest.mark.parametrize("field", (
    "source_arm", "source_plan", "token_source_sha256", "source_capacity",
    "source_server_parameters", "engine_tree", "workflow_ids", "sampling_mode",
    "max_running_requests", "request_timeout_s", "stream_response_metrics",
    "workflow_arrivals", "workflow_arrivals_path", "tool_timing_overlay", "order",
    "oracle_information",
))
def test_baseline_rejects_changed_work_resources_arrivals_or_oracle_metadata(field):
    predictive, thunder = plans()
    thunder[field] = "changed"
    with pytest.raises(ValueError):
        matching_traffic(predictive, thunder)
