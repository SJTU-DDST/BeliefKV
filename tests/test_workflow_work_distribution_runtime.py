import hashlib
import json
from pathlib import Path
import time

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_work_worker import DistributionWorkPrediction
from scripts.prepare_workflow_size_candidate import prepare
from tests import test_workflow_combination_runtime as combination_tests
from tests.test_workflow_work_distribution import distribution_raw


def distribution_configuration(tmp_path):
    values = combination_tests.configured(tmp_path)
    path = Path(values["WORKFLOW_WORK_MODEL"])
    profile = json.loads(path.read_bytes())["admission_cost_profile"]
    raw = {**distribution_raw(), "admission_cost_profile": profile}
    path.write_text(json.dumps(raw))
    values["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


@pytest.mark.parametrize("expired,expected", ((False, {"a"}), (True, {"b", "c"})))
def test_real_r5_distribution_or_expiry_reaches_shared_admission_and_native_candidates(
    monkeypatch, tmp_path, expired, expected,
):
    values = distribution_configuration(tmp_path)
    monkeypatch.setattr(combination_tests, "configured", lambda _path: values)
    runtime, admission, _env = combination_tests.runtime_with_queue(monkeypatch, tmp_path)
    records = []
    from types import SimpleNamespace

    runtime._opportunity_writer = SimpleNamespace(record=records.append)
    try:
        now = time.monotonic()
        for name in ("a", "b", "c"):
            admission._work._predictions[name] = DistributionWorkPrediction(
                (1., 0., 0.) if name == "a" else (0., 0., 1.),
                0 if name == "a" else 2, 1, now, "model", "calibrated_distribution",
                now - 1. if expired else now + 3., 1,
            )
        admission.tick()
        actual = {item.identity for item in admission.policy.programs.values() if item.active}
        assert actual == expected
        assert admission._priority(admission._work.prediction("a")) == 1
        assert admission._pressure_preference(admission.policy.programs["a"], now) is None
        assert admission.status()["workflow_distribution_argmax_used_for_admission"] is False
        choice = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert set(choice["decision"]["selected"]) == expected
        if expired:
            assert choice["decision"]["reason"] == "predictions_unavailable_reference_fallback"
        else:
            assert choice["decision"]["reference"] == ("b", "c")
            assert choice["predictions"]["a"]["probabilities"] == (1., 0., 0.)
            assert choice["predictions"]["b"]["probabilities"] == (0., 0., 1.)
            assert {
                value["identity"]: value["weight"] for value in choice["candidates"]
            } == pytest.approx({"a": 1., "b": .25, "c": .25})
        candidates = [admission._live_requests[name + "-request"] for name in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(candidates, running_batch=SimpleNamespace(reqs=[]), adder=None)
        selected = combination_tests.select_native_prefill_candidates(
            candidates, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert {value.rid[0] for value in selected.candidates} == expected
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_r5_cannot_silently_use_the_legacy_bucket_admission(monkeypatch, tmp_path):
    values = distribution_configuration(tmp_path)
    values["WORKFLOW_COMBINATION_ADMISSION"] = "0"
    for name, value in arm_environment(values, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    with pytest.raises(ValueError, match="require shared combination"):
        NativeAdmissionRuntime(event_socket_path=str(tmp_path / "invalid-r5.sock"))


def test_candidate_explicitly_replaces_oracle_source_and_preserves_arrivals_and_disabled_features(tmp_path):
    values = distribution_configuration(tmp_path)
    model_path = Path(values["WORKFLOW_WORK_MODEL"])
    raw = json.loads(model_path.read_bytes())
    split = {
        "mode": "workflow", "complete_workflows_disjoint": True,
        "complete_captures_disjoint": True, "unseen_project_generalization_claimed": False,
        "training_execution_sha256": ["1" * 64], "calibration_execution_sha256": ["2" * 64],
        "evaluation_execution_sha256": ["3" * 64],
    }
    raw["metadata"].update(prefill_token_weight=.01, split_contract=split)
    model_path.write_text(json.dumps(raw))
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(), "split_contract": split,
    }))
    base = tmp_path / "base.json"
    previous = {
        "common_environment": {
            **values, "WORKFLOW_WORK_SOURCE": "oracle", "BELIEFKV_WORKFLOW_WORK_SOURCE": "oracle",
            "WORKFLOW_COMBINATION_ADMISSION": "0",
        },
        "order": ["full"],
        "workflow_arrivals": {"path": "/unchanged/arrivals"},
        "private_analysis_cohort": {"path": "/unchanged/private_cohort"},
        "comparison_contract": {"future_information_used": True, "workflow_work_ranking": False},
    }
    base.write_text(json.dumps(previous))
    candidate = prepare(base, model_path, evaluation, free_dispatch_interval_ms=500)
    assert json.loads(base.read_bytes()) == previous
    assert candidate["workflow_arrivals"] == previous["workflow_arrivals"]
    assert candidate["private_analysis_cohort"] == previous["private_analysis_cohort"]
    effective = arm_environment(candidate["common_environment"], "full")
    assert effective["BELIEFKV_WORKFLOW_WORK_SOURCE"] == "learned"
    assert effective["BELIEFKV_WORKFLOW_COMBINATION_ADMISSION"] == "1"
    assert effective["BELIEFKV_LAYER_HANDOFF"] == effective["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert not candidate["comparison_contract"]["future_information_used"]
    assert not candidate["comparison_contract"]["workflow_work_argmax_used_for_admission"]
    assert candidate["comparison_contract"]["workflow_distribution_changes_pressure_victims"] is False
    assert not candidate["gpu_execution"]["registered"]
