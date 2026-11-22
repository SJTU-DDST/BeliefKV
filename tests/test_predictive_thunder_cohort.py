from copy import deepcopy
import json

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.compare_reported_workflow_cohorts import HOST_POOL_TELEMETRY, WRITE_BACK_CAP_SETTING
from scripts.review_predictive_thunder_cohort import (
    matching_serving_traffic, matching_traffic, named_metrics, write_back_evidence,
)
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


@pytest.mark.parametrize("label", ("thunderagent_acting_decay", "native_matched"))
@pytest.mark.parametrize(("configured", "observed", "error"), (
    ((32, 32), (32, 32), None),
    ((32, 1), (32, 1), "different effective native write-back caps"),
    ((32, 32), (32, 1), "requested native write-back cap 32, observed 1"),
    ((32, 32), (None, None), "effective native write-back cap is unknown"),
    ((None, None), (None, None), None),
))
def test_system_review_requires_batched_write_back_on_both_arms(tmp_path, label, configured, observed, error):
    comparison_plans, cohorts = plans(), []
    comparison_plans[1]["order"] = [label]
    for plan, requested, cap in zip(comparison_plans, configured, observed, strict=True):
        arm = tmp_path / plan["order"][0]
        plan["run_root"] = str(tmp_path)
        if requested is not None:
            plan["reference_environment"] = {WRITE_BACK_CAP_SETTING: str(requested)}
        path = arm / HOST_POOL_TELEMETRY
        if cap is not None:
            path.parent.mkdir(parents=True)
            record = {"event": "host_pool_usage", "write_back_batch": {"max_nodes": cap}}
            path.write_text(json.dumps(record) + "\n")
        cohorts.append({})
    if error:
        with pytest.raises(ValueError, match=error):
            write_back_evidence(comparison_plans, cohorts)
    else:
        result = write_back_evidence(comparison_plans, cohorts)
        assert result["write_back_cap_comparison"] == {
            "status": "unknown" if observed[0] is None else "matched",
            "matched_max_nodes": observed[0],
        }


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


@pytest.mark.parametrize("change", (None, "bytes", "pin", "registered_arrivals"))
def test_reuses_identical_arrival_files_at_distinct_registered_paths(tmp_path, change):
    predictive, thunder = plans()
    schedule = {"arrival_schedule": [{"workflow_id": "w", "offset_seconds": 0}]}
    for index, plan in enumerate((predictive, thunder)):
        path = tmp_path / f"arrivals-{index}.json"
        path.write_text(json.dumps(schedule))
        plan["workflow_arrivals_path"] = str(path)
        plan["workflow_arrivals"] = deepcopy(schedule)
        plan["external_inputs"] = [{"path": str(path), "sha256": sha256_file(path)}]
    if change is None:
        matching_traffic(predictive, thunder)
        return
    if change == "bytes":
        path.write_text(json.dumps(schedule) + "\n")
    elif change == "pin":
        thunder["external_inputs"] = []
    else:
        for plan in (predictive, thunder):
            plan["workflow_arrivals"]["arrival_schedule"][0]["offset_seconds"] = 1
    with pytest.raises(ValueError, match="workflow_arrivals_path"):
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
