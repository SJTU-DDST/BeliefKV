from copy import deepcopy

import pytest

from scripts.review_predictive_thunder_cohort import matching_serving_traffic, matching_traffic, named_metrics
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


@pytest.mark.parametrize("field", (
    "native_execution_configuration", "expected_replay_capacity", "host_capacity_experiment",
))
def test_shared_serving_match_keeps_declared_capacity_and_native_execution(field):
    first, second = plans()
    second["order"] = ["full"]
    second[field] = {"changed": True}
    with pytest.raises(ValueError):
        matching_serving_traffic(first, second)


def test_shared_serving_match_does_not_require_equal_observed_numa_page_counts():
    first, second = plans()
    second["order"] = ["full"]
    common = {
        "MODEL_CACHE_LAYOUT": "full_only", "HICACHE_SIZE_GB": "400",
        "HOST_NUMA_MEMORY_POLICY": "interleave", "HOST_NUMA_MEMORY_NODES": "0,1",
        "HOST_NUMA_PLACEMENT_CHECK": "diagnostic",
    }
    first["reference_environment"] = second["reference_environment"] = common
    first["observed_numa_pages"] = {"0": 40, "1": 60}
    second["observed_numa_pages"] = {"0": 55, "1": 45}
    matching_serving_traffic(first, second)
    second["reference_environment"] = {**common, "HICACHE_SIZE_GB": "200"}
    with pytest.raises(ValueError, match="NUMA allocation"):
        matching_serving_traffic(first, second)
