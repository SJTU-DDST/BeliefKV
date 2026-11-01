"""Native replay uses the same fixed-cohort fidelity and shutdown authority."""

from copy import deepcopy
import json

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from scripts import run_position_replay_calibration as position
from scripts import stop_after_measured_cohort as stop
from scripts.review_predictive_native_cohort import matching_traffic, named_metrics
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from tests.test_causal_comparison import reference
from tests.test_measured_cohort_stop import partial_fixture
from tests.test_sustained_oracle_review import censored_report
from beliefkv.experiments.workflow_cohort import summarize_cohort
from beliefkv.experiments.source_provenance import sha256_file


@pytest.mark.parametrize("declared", (None, False, 1, "true", True))
def test_native_position_baseline_requires_explicit_boolean_declaration(declared):
    spec = {"order": ["native_matched"], "comparison_contract": {}}
    if declared is not None:
        spec["comparison_contract"]["native_replay_baseline"] = declared
    assert position.valid_comparison_order(spec) is (declared is True)


@pytest.mark.parametrize("order", (
    [], ["full"], ["native_matched", "full"], ["native_matched", "native_matched"],
    ["unknown"], "native_matched", None, [True],
))
def test_native_declaration_cannot_silently_start_another_or_multiple_arms(order):
    assert not position.valid_comparison_order({
        "order": order, "comparison_contract": {"native_replay_baseline": True},
    })


@pytest.mark.parametrize("order", (
    ["full"], ["thunderagent_acting_decay"], ["full", "join_priority_only"],
))
def test_legacy_nonnative_orders_need_no_new_declaration(order):
    assert position.valid_comparison_order({"order": order})


def test_native_measured_stop_selects_its_actual_output_directory(tmp_path):
    assert stop.replay_arm({
        "order": ["native_matched"], "run_root": str(tmp_path),
        "comparison_contract": {"native_replay_baseline": True},
    }) == (
        tmp_path / "native_matched"
    )
    with pytest.raises(ValueError, match="one declared"):
        stop.replay_arm({"order": ["native_matched", "full"], "run_root": str(tmp_path)})


@pytest.mark.parametrize("declared", (None, False, 1, "true"))
def test_native_measured_stop_rejects_missing_or_nonboolean_declaration(tmp_path, declared):
    with pytest.raises(ValueError, match="one declared"):
        stop.replay_arm({
            "order": ["native_matched"], "run_root": str(tmp_path),
            "comparison_contract": {"native_replay_baseline": declared},
        })


@pytest.mark.parametrize("change", (None, "unpinned", "hash", "order", "undeclared", "nonboolean"))
def test_native_stop_reads_the_immutable_configuration_from_actual_registration_shape(tmp_path, change):
    path = tmp_path / "configuration.json"
    spec = {
        "order": ["native_matched"],
        "comparison_contract": {"native_replay_baseline": True},
    }
    if change == "order":
        spec["order"] = ["full"]
    elif change == "undeclared":
        spec["comparison_contract"] = {}
    elif change == "nonboolean":
        spec["comparison_contract"]["native_replay_baseline"] = 1
    path.write_text(json.dumps(spec))
    plan = {
        "order": ["native_matched"], "run_root": str(tmp_path),
        "experiment_configuration": str(path),
        "external_inputs": [{"path": str(path), "sha256": sha256_file(path)}],
    }
    if change == "unpinned":
        plan["external_inputs"] = []
    elif change == "hash":
        plan["external_inputs"][0]["sha256"] = "changed"
    if change is None:
        assert stop.replay_arm(plan) == tmp_path / "native_matched"
    else:
        with pytest.raises(ValueError, match="one declared"):
            stop.replay_arm(plan)


def test_native_measured_JCT_keeps_all_fixed_tails_and_censors_only_background():
    arrivals, cohort, events, samples = partial_fixture()
    result = summarize_cohort(cohort, arrivals, events, samples)
    assert result["all_measured_completed"]
    assert result["measured_complete_workflow_JCT_ms"]["count"] == 2
    assert not result["normal_tails_removed"]
    assert result["nonmeasured_workflows_right_censored"] == ["w3", "w4", "w5"]
    unfinished = [row for row in events if not (
        row["kind"] == "workflow_end" and row["workflow_id"] == "w1"
    )]
    with pytest.raises(ValueError, match="all fixed measured"):
        summarize_cohort(cohort, arrivals, unfinished, samples)


def test_native_environment_clears_full_scheduling_prefetch_and_future_information(reference):
    common = deepcopy(reference["arms"]["full"])
    common.update({
        "WORKFLOW_SIZE_RESUME": "1", "BELIEFKV_WORKFLOW_SIZE_RESUME": "1",
        "WORKFLOW_COMBINATION_ADMISSION": "1", "BELIEFKV_WORKFLOW_COMBINATION_ADMISSION": "1",
        "WORKFLOW_WORK_SOURCE": "oracle", "BELIEFKV_WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": "/private/future.json", "BELIEFKV_WORKFLOW_WORK_MODEL": "/private/future.json",
        "WORKFLOW_WORK_MODEL_SHA256": "future", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "future",
        "AGENT_RESIDENCY_PAUSE": "1", "BELIEFKV_AGENT_RESIDENCY_PAUSE": "1",
        "PREDICTED_TOOL_OFFLOAD": "1", "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD": "1",
    })
    native = arm_environment(common, "native_matched")
    assert native["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "native_matched"
    for key in (
        "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD", "BELIEFKV_ENABLE_TOOL_PREFETCH",
        "BELIEFKV_ENABLE_PREPARE_HOST", "BELIEFKV_WORKFLOW_SIZE_RESUME",
        "BELIEFKV_WORKFLOW_COMBINATION_ADMISSION", "BELIEFKV_AGENT_RESIDENCY_PAUSE",
    ):
        assert native[key] == "0"
    assert native["BELIEFKV_AGENT_ADMISSION"] == "off"
    assert native["BELIEFKV_WORKFLOW_WORK_SOURCE"] == "learned"
    assert not native["BELIEFKV_WORKFLOW_WORK_MODEL"]


@pytest.mark.parametrize("changed", (None, *TRAFFIC_FIELDS, "oracle_information", "wrong_arm"))
def test_native_comparison_rejects_work_arrival_resource_and_future_information_changes(changed):
    predictive = {"order": ["full"], **{name: {"fixed": name} for name in TRAFFIC_FIELDS}}
    native = {**deepcopy(predictive), "order": ["native_matched"]}
    if changed == "oracle_information":
        native[changed] = {"future_model_token_work_used": True}
    elif changed == "wrong_arm":
        native["order"] = ["thunderagent_acting_decay"]
    elif changed is not None:
        native[changed] = {"changed": changed}
    if changed is None:
        matching_traffic(predictive, native)
    else:
        with pytest.raises(ValueError):
            matching_traffic(predictive, native)


def test_native_comparison_preserves_gain_polarity_complete_JCT_and_censored_background():
    result = named_metrics(censored_report(), censored_report(measured=(80., 200.)))
    group = result["groups"]["measured"]
    assert group["predictive_JCT_ms"]["mean"] < group["native_JCT_ms"]["mean"]
    assert group["mean_JCT_reduction_percent"] > 0
    assert group["paired_completed_workflows"] == group["fixed_population"]
    assert "oracle_JCT_ms" not in group and "control_JCT_ms" not in group
    assert "native_tokens_per_second" in result["common_window_output_throughput"]


def test_native_receipt_validation_requires_exact_measured_tokens_and_original_stopping(tmp_path):
    source, arm = tmp_path / "source", tmp_path / "native_matched"
    (source / "server").mkdir(parents=True)
    (arm / "replay").mkdir(parents=True)
    identity = ["measured", "agent", "context", 0]
    (source / "expected.json").write_text(json.dumps({
        "r": {"llm_submit": [identity, 2], "llm_result": [identity, 1]},
        "background": {"llm_submit": [["background", "a", "c", 0], 2]},
    }))
    tokens = [
        {"request_id": "r", "event": "request_prompt", "token_sha256": "prompt"},
        {"request_id": "r", "event": "request_output", "token_sha256": "output",
         "token_count": 1, "native_finish_reason": {"type": "stop"}},
    ]
    (source / "server/request_tokens.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tokens),
    )
    row = {
        "request_id": "r", "prompt_tokens": 2, "output_tokens": 1,
        "prompt_sha256": "prompt", "output_sha256": "output",
        "native_finish_reason": {"type": "stop"}, "exact_tokens_verified": True,
        "native_receipt": {
            "sampling_path": "position", "identity": identity, "output_token_ids": [1],
        },
    }
    receipts = arm / "replay/native_requests.jsonl"
    receipts.write_text(json.dumps(row) + "\n")
    plan = {"order": ["native_matched"], "run_root": str(tmp_path), "source_arm": str(source),
            "sampling_mode": "position", "comparison_contract": {"native_replay_baseline": True}}
    cohort = {"measured_workflow_ids": ["measured"]}
    proof = stop.receipt_proof(plan, cohort, stop.replay_arm(plan))
    assert proof["complete_measured_native_work_verified"]
    assert proof["measured_requests"] == 1 and proof["measured_output_tokens"] == 1
    row["native_finish_reason"] = {"type": "length"}
    receipts.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="hashes or stopping"):
        stop.receipt_proof(plan, cohort, arm)
