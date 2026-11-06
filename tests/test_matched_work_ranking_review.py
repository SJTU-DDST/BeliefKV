import json
from copy import deepcopy
from pathlib import Path

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import review_matched_work_ranking as review
from scripts.review_matched_work_ranking import evaluate_pair_cohorts, verify_ranking_only_pair
from scripts.review_sustained_oracle import compare_cohorts
from tests.test_predictive_thunder_cohort import plans
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


@pytest.mark.parametrize("field,value", (
    ("path", "/another/cohort.json"),
    ("sha256", "another-cohort"),
    ("delivered_to_scheduler", True),
))
def test_ranking_pair_rejects_a_different_private_measurement_definition(tmp_path, field, value):
    control, oracle = pair(tmp_path)
    control["private_analysis_cohort"] = {
        "path": "/fixed/cohort.json", "sha256": "fixed-cohort",
        "delivered_to_scheduler": False,
    }
    oracle["private_analysis_cohort"] = deepcopy(control["private_analysis_cohort"])
    oracle["private_analysis_cohort"][field] = value
    with pytest.raises(ValueError, match="tasks, tools, arrivals or serving conditions"):
        verify_ranking_only_pair(control, oracle)


@pytest.mark.parametrize("field", (
    "source_arm", "source_plan", "token_source_sha256", "source_capacity",
    "source_server_parameters", "engine_tree", "workflow_ids", "sampling_mode",
    "max_running_requests", "request_timeout_s", "stream_response_metrics",
    "workflow_arrivals", "workflow_arrivals_path", "tool_timing_overlay",
    "native_execution_configuration", "expected_replay_capacity", "host_capacity_experiment",
))
def test_result_entry_rejects_mismatched_executions_before_evaluating_gain(tmp_path, monkeypatch, field):
    control, oracle = plans()
    oracle["order"] = ["full"]
    oracle[field] = {"changed": True}
    files = []
    for label, plan in (("control", control), ("oracle", oracle)):
        path = tmp_path / (label + ".json")
        path.write_text(json.dumps(plan))
        files.append(path)
    argv = ["review_matched_work_ranking"]
    for name, value in (
        ("control", files[0]), ("oracle", files[1]),
        ("control-review-dir", tmp_path / "control_review"),
        ("oracle-review-dir", tmp_path / "oracle_review"),
        ("arrivals", tmp_path / "arrivals.json"), ("cohort", tmp_path / "cohort.json"),
        ("output", tmp_path / "comparison.json"),
    ):
        argv.extend(("--" + name, str(value)))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(review.causal.comparison, "verify_sources", lambda plan: None)
    with pytest.raises(ValueError, match="paired execution changed"):
        review.main()
    assert not (tmp_path / "comparison.json").exists()


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


def test_legacy_control_declaration_requires_explicit_review_without_rewriting_it(tmp_path):
    control, oracle = pair(tmp_path)
    control["comparison_contract"]["matched_pair_changes_only_remaining_work_ranking"] = False
    before = json.dumps([control, oracle], sort_keys=True)
    with pytest.raises(ValueError, match="declarations"):
        verify_ranking_only_pair(control, oracle)
    assert len(verify_ranking_only_pair(
        control, oracle, allow_legacy_control_declaration=True,
    )) == 2
    assert json.dumps([control, oracle], sort_keys=True) == before
    oracle["common_environment"]["HOST_GB"] = "400"
    with pytest.raises(ValueError, match="serving conditions"):
        verify_ranking_only_pair(control, oracle, allow_legacy_control_declaration=True)


@pytest.mark.parametrize("missing_or_unpaired", (None, False))
def test_legacy_control_opt_in_cannot_accept_an_undeclared_oracle(tmp_path, missing_or_unpaired):
    control, oracle = pair(tmp_path)
    control["comparison_contract"]["matched_pair_changes_only_remaining_work_ranking"] = False
    oracle["comparison_contract"]["matched_pair_changes_only_remaining_work_ranking"] = missing_or_unpaired
    with pytest.raises(ValueError, match="declarations"):
        verify_ranking_only_pair(control, oracle, allow_legacy_control_declaration=True)


def test_explicit_one_percent_goal_preserves_full_JCT_and_throughput_requirements():
    control = censored_report()
    values = tuple(control["measured_workflow_JCT_ms"].values())
    oracle = censored_report(measured=tuple(value * .985 for value in values))
    contract = {
        "minimum_promising_measured_mean_JCT_reduction": .05,
        "common_window_output_throughput_noninferiority_fraction": .98,
    }
    original = json.dumps(contract, sort_keys=True)
    assert not evaluate_pair_cohorts(
        control, oracle, contract,
    )["checks"]["fixed_measured_mean_JCT_gain"]
    result = evaluate_pair_cohorts(control, oracle, contract, minimum_jct_gain=.01)
    assert result["checks"]["fixed_measured_mean_JCT_gain"]
    assert result["performance_goal"]["minimum_measured_mean_JCT_gain"] == .01
    assert result["performance_goal"]["configuration_minimum_measured_mean_JCT_gain"] == .05
    assert json.dumps(contract, sort_keys=True) == original
    oracle["common_window_output_tokens_per_second"] = control["common_window_output_tokens_per_second"] * .95
    result = evaluate_pair_cohorts(control, oracle, contract, minimum_jct_gain=.01)
    assert not result["fixed_cohort_performance_target_met"]


@pytest.mark.parametrize("gain", (0, 1, float("nan"), float("inf")))
def test_invalid_goal_cannot_produce_a_passing_comparison(gain):
    contract = {
        "minimum_promising_measured_mean_JCT_reduction": .05,
        "common_window_output_throughput_noninferiority_fraction": .98,
    }
    with pytest.raises(ValueError):
        evaluate_pair_cohorts(censored_report(), censored_report(), contract, minimum_jct_gain=gain)
