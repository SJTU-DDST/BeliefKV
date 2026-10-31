import json
from pathlib import Path

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.review_matched_work_ranking import verify_ranking_only_pair
from scripts.review_sustained_oracle import compare_cohorts
from tests.test_sustained_oracle_review import censored_report


def pair(tmp_path):
    configurations = []
    for label, ranking in (("control", False), ("oracle", True)):
        path = tmp_path / (label + ".json")
        path.write_text(json.dumps({
            "remaining_work_ranking_enabled": ranking,
            "free_dispatch_coalescing_window_ms": 2000,
            "workflows": {"a": {"model": [10, 20], "tools": [1, 5]}},
        }))
        common = {
            "WORKFLOW_SIZE_RESUME": "1", "WORKFLOW_WORK_SOURCE": "oracle",
            "WORKFLOW_WORK_MODEL": str(path), "WORKFLOW_WORK_MODEL_SHA256": sha256_file(path),
            "WORKFLOW_COMBINATION_ADMISSION": "0",
            "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
            "AGENT_CONTROL_INTERVAL_MS": "2000", "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
            "TOOL_PREFETCH": "0", "PREDICTED_TOOL_OFFLOAD": "1", "HOST_GB": "200",
        }
        common.update({"BELIEFKV_" + name: value for name, value in list(common.items())})
        configurations.append({
            "order": ["full"], "source_registration": "source",
            "workflow_arrivals": {"sha256": "arrivals"}, "tool_timing_overlay": {"sha256": "tools"},
            "stream_response_metrics": True, "common_environment": common,
            "comparison_contract": {
                "matched_pair_changes_only_remaining_work_ranking": True,
                "workflow_work_ranking": ranking,
                "coalescing_and_oracle_ranking_are_joint_change": False,
                "minimum_promising_measured_mean_JCT_reduction": .05,
                "common_window_output_throughput_noninferiority_fraction": .98,
            },
        })
    return configurations


def change_artifact(configuration, change, *, rehash=True):
    common = configuration["common_environment"]
    path = common["WORKFLOW_WORK_MODEL"]
    with open(path) as stream:
        artifact = json.load(stream)
    change(artifact)
    Path(path).write_text(json.dumps(artifact))
    if rehash:
        common["WORKFLOW_WORK_MODEL_SHA256"] = common["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"] = (
            sha256_file(Path(path))
        )


def test_accepts_only_ranking_toggle_with_identical_progress_work(tmp_path):
    control, oracle = pair(tmp_path)
    assert len(verify_ranking_only_pair(control, oracle)) == 2


@pytest.mark.parametrize("change", (
    "arrivals", "tools", "source", "stream", "host", "offload", "window", "work",
    "ranking", "stale_hash", "alias", "threshold", "undeclared",
))
def test_rejects_changes_hidden_behind_matched_pair_label(tmp_path, change):
    control, oracle = pair(tmp_path)
    if change in ("arrivals", "tools", "source", "stream"):
        field = {
            "arrivals": "workflow_arrivals", "tools": "tool_timing_overlay",
            "source": "source_registration", "stream": "stream_response_metrics",
        }[change]
        oracle[field] = "changed"
    elif change in ("host", "offload"):
        oracle["common_environment"][{"host": "HOST_GB", "offload": "PREDICTED_TOOL_OFFLOAD"}[change]] = "0"
    elif change == "alias":
        control["common_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"] = "another"
    elif change == "threshold":
        oracle["comparison_contract"]["minimum_promising_measured_mean_JCT_reduction"] = .01
    elif change == "undeclared":
        oracle["comparison_contract"]["matched_pair_changes_only_remaining_work_ranking"] = False
    else:
        change_artifact(oracle, {
            "window": lambda row: row.update(free_dispatch_coalescing_window_ms=1000),
            "work": lambda row: row["workflows"]["a"].update(tools=[1, 6]),
            "ranking": lambda row: row.update(remaining_work_ranking_enabled=False),
            "stale_hash": lambda row: row.update(another_field=True),
        }[change], rehash=change != "stale_hash")
    with pytest.raises(ValueError):
        verify_ranking_only_pair(control, oracle)


def test_numeric_gain_does_not_erase_background_coverage_failure():
    control, oracle = censored_report(), censored_report(measured=(80., 200.))
    control["background_submission_coverage"]["covers_all_measured_completions"] = False
    result = compare_cohorts(control, oracle)
    assert result["checks"]["fixed_measured_mean_JCT_gain"]
    assert not result["checks"]["background_covers_both_measured_tails"]
    assert not result["fixed_cohort_performance_target_met"]
