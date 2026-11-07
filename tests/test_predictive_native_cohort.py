from copy import deepcopy

import pytest

from scripts.review_predictive_native_cohort import matching_traffic, named_metrics
from tests.test_predictive_thunder_cohort import plans as thunder_plans
from tests.test_sustained_oracle_review import censored_report


def plans():
    predictive, native = thunder_plans()
    native["order"] = ["native_matched"]
    return predictive, native


def test_native_comparison_keeps_complete_fixed_cohort_and_metric_names():
    matching_traffic(*plans())
    result = named_metrics(censored_report(), censored_report(measured=(80., 200.)))
    measured = result["groups"]["measured"]
    assert measured["mean_JCT_reduction_percent"] > 0
    assert measured["paired_completed_workflows"] == measured["fixed_population"]
    assert measured["predictive_JCT_ms"]["mean"] < measured["native_JCT_ms"]["mean"]
    assert "oracle_JCT_ms" not in measured
    assert "control_JCT_ms" not in measured
    assert "native_tokens_per_second" in result["common_window_output_throughput"]


@pytest.mark.parametrize("field", (
    "native_execution_configuration", "expected_replay_capacity", "host_capacity_experiment",
))
def test_native_comparison_rejects_changed_execution_and_capacity_contracts(field):
    predictive, native = plans()
    native[field] = {"changed": True}
    with pytest.raises(ValueError):
        matching_traffic(predictive, native)


def test_native_comparison_requires_same_host_allocation_policy_but_not_page_counts():
    predictive, native = plans()
    common = {
        "MODEL_CACHE_LAYOUT": "full_only", "HICACHE_SIZE_GB": "400",
        "HOST_NUMA_MEMORY_POLICY": "interleave", "HOST_NUMA_MEMORY_NODES": "0,1",
        "HOST_NUMA_PLACEMENT_CHECK": "diagnostic",
    }
    predictive["reference_environment"] = deepcopy(common)
    native["reference_environment"] = deepcopy(common)
    predictive["observed_numa_pages"] = {"0": 40, "1": 60}
    native["observed_numa_pages"] = {"0": 55, "1": 45}
    matching_traffic(predictive, native)
    native["reference_environment"]["HICACHE_SIZE_GB"] = "200"
    with pytest.raises(ValueError, match="NUMA allocation"):
        matching_traffic(predictive, native)


@pytest.mark.parametrize("field", ("workflow_arrivals", "token_source_sha256", "oracle_information", "order"))
def test_native_comparison_still_rejects_changed_work_or_future_information(field):
    predictive, native = plans()
    native[field] = "changed"
    with pytest.raises(ValueError):
        matching_traffic(predictive, native)
