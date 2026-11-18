from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_size_admission import WorkflowSizeAdmission
from beliefkv.runtime.workflow_tool_pressure import ToolRetentionSettings
from beliefkv.experiments.internal_comparison import arm_environment
from scripts.audit_scheduler_parameters import counterfactual, inspect
from scripts.finish_model_work_oracle import comparison_scope, verify_same_objective
from scripts.prepare_bounded_ready_wait import prepare as bounded
from scripts.prepare_scheduler_parameter_review import prepare, prepare_artifacts, profile_identity
from tests import test_workflow_combination_runtime as runtime_fixture
from tests.test_bounded_ready_wait_preparation import prepared_pair
from tests.test_model_work_oracle_finish import pair
from tests.test_ready_wait_aging_audit import recorded_aging


def inputs(tmp_path):
    paths = prepared_pair(tmp_path)
    for name, path in paths.items():
        config = json.loads(path.read_bytes())
        for prefix in ("", "BELIEFKV_"):
            config["common_environment"][prefix + "AGENT_PRESSURE_PAUSE_POLICY"] = "workflow_tool_aware"
        artifact_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
        raw = json.loads(artifact_path.read_bytes())
        profile = raw["admission_cost_profile"]
        profile["identity"] = profile_identity(profile)
        if name == "oracle":
            learned = json.loads(paths["learned"].read_bytes())
            raw["combination_reference_source_model_sha256"] = learned["common_environment"][
                "WORKFLOW_WORK_MODEL_SHA256"
            ]
        artifact_path.write_text(json.dumps(raw))
        for prefix in ("", "BELIEFKV_"):
            config["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(artifact_path)
        path.write_text(json.dumps(config))
    output = tmp_path / "bounded"
    bounded(paths["learned"], paths["oracle"], output, ReadyWaitSettings(120000))
    return {name: output / (name + ".json") for name in paths}


def test_parameter_preparation_preserves_traffic_forecasts_and_measured_costs(tmp_path):
    paths = inputs(tmp_path)
    original = {name: path.read_bytes() for name, path in paths.items()}
    output = tmp_path / "review"
    report = prepare(paths["learned"], paths["oracle"], output)
    assert report["GPU_action"] is False
    assert report["live_or_pending_registrations_changed"] is False
    assert report["performance_accepted"] is False
    for name, path in paths.items():
        assert path.read_bytes() == original[name]
        before = json.loads(original[name])
        after = json.loads((output / (name + ".json")).read_bytes())
        for field in (
            "source_registration", "workflow_arrivals", "private_analysis_cohort",
            "tool_timing_overlay", "workload_acceptance", "stream_response_metrics",
        ):
            assert after.get(field) == before.get(field)
        old_work = json.loads(Path(before["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
        new_work = json.loads((output / (name + "_work.json")).read_bytes())
        assert ReadyWaitSettings.from_artifact(new_work) == ReadyWaitSettings.from_artifact(old_work)
        assert forecast_fingerprint(new_work) == forecast_fingerprint(old_work)
        old_costs = old_work["admission_cost_profile"]["costs"]
        assert {key: value for key, value in new_work["admission_cost_profile"]["costs"].items()
                if key != "join_progress"} == old_costs
        assert new_work["admission_cost_profile"]["identity"] == profile_identity(
            new_work["admission_cost_profile"]
        )
        assert new_work["tool_retention_policy"] == asdict(ToolRetentionSettings())
        left, right = deepcopy(before["common_environment"]), deepcopy(after["common_environment"])
        for prefix in ("", "BELIEFKV_"):
            for field in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256"):
                left.pop(prefix + field)
                right.pop(prefix + field)
        assert left == right
    with pytest.raises(FileExistsError):
        prepare(paths["learned"], paths["oracle"], output)


@pytest.mark.parametrize("change", ("digest", "profile", "selector", "pressure"))
def test_preparation_refuses_unbound_or_incompatible_inputs(tmp_path, change):
    paths = inputs(tmp_path)
    config = json.loads(paths["learned"].read_bytes())
    artifact_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(artifact_path.read_bytes())
    if change == "pressure":
        config["common_environment"]["AGENT_PRESSURE_PAUSE_POLICY"] = "reference"
    elif change == "selector":
        raw["shortest_work_first_admission"] = False
    else:
        raw["admission_cost_profile"]["costs"]["epsilon_ms"] = 123.
    artifact_path.write_text(json.dumps(raw))
    if change != "digest":
        for prefix in ("", "BELIEFKV_"):
            config["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(artifact_path)
    paths["learned"].write_text(json.dumps(config))
    with pytest.raises(ValueError):
        prepare(paths["learned"], paths["oracle"], tmp_path / "review")


def test_artifact_preparation_rejects_a_stale_profile_identity_without_mutation(tmp_path):
    paths = inputs(tmp_path)
    artifacts = [
        json.loads(Path(json.loads(paths[name].read_bytes())["common_environment"][
            "WORKFLOW_WORK_MODEL"
        ]).read_bytes()) for name in ("learned", "oracle")
    ]
    for raw in artifacts:
        raw["admission_cost_profile"]["costs"]["epsilon_ms"] = 123.
    before = deepcopy(artifacts)
    with pytest.raises(ValueError, match="cost profile identity"):
        prepare_artifacts(*artifacts)
    assert artifacts == before


@pytest.mark.parametrize("field,value", (
    ("short_grace_ms", 1000), ("half_life_ms", 2000), ("relative_tie", .02),
))
def test_oracle_and_learned_must_use_the_same_tool_retention_policy(field, value):
    learned, oracle, model, truth = pair()
    for raw in (model, truth):
        raw["tool_retention_policy"] = asdict(ToolRetentionSettings())
    verify_same_objective(learned, oracle, model, truth)
    assert comparison_scope(truth)["tool_retention_policy"] == truth["tool_retention_policy"]
    truth["tool_retention_policy"][field] = value
    with pytest.raises(ValueError, match="differs"):
        verify_same_objective(learned, oracle, model, truth)
    truth.pop("tool_retention_policy")
    with pytest.raises(ValueError, match="differs"):
        verify_same_objective(learned, oracle, model, truth)


def test_real_runtime_attaches_explicit_tool_and_join_parameters(monkeypatch, tmp_path):
    env = runtime_fixture.configured(tmp_path)
    path = Path(env["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["tool_retention_policy"] = asdict(ToolRetentionSettings())
    raw["admission_cost_profile"]["costs"]["join_progress"] = "linear"
    path.write_text(json.dumps(raw))
    env.update(
        WORKFLOW_WORK_MODEL_SHA256=sha256_file(path),
        AGENT_PRESSURE_PAUSE_POLICY="workflow_tool_aware",
    )
    monkeypatch.setattr(runtime_fixture, "configured", lambda _: env)
    runtime, admission, _env = runtime_fixture.runtime_with_queue(monkeypatch, tmp_path)
    try:
        assert admission.policy.tool_retention_relative_tie == .01
        assert admission._tool_pressure.settings == ToolRetentionSettings()
        assert admission._combination.costs.join_progress == "linear"
        status = admission.status()
        assert status["workflow_join_progress"] == "linear_unfinished_share_v1"
        assert status["workflow_tool_retention_policy"] == asdict(ToolRetentionSettings())
        admission.tick()
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"b", "c"}
    finally:
        runtime.close()


def test_invalid_tool_settings_close_the_prediction_worker(monkeypatch, tmp_path):
    env = runtime_fixture.configured(tmp_path)
    path = Path(env["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["tool_retention_policy"] = {"kind": "relative_tool_retention_v2"}
    path.write_text(json.dumps(raw))
    env.update(
        WORKFLOW_WORK_MODEL_SHA256=sha256_file(path),
        AGENT_PRESSURE_PAUSE_POLICY="workflow_tool_aware",
    )
    for name, value in arm_environment(env, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    workers = []
    original = WorkflowSizeAdmission._create_work

    def create_work(admission, runtime):
        work = original(admission, runtime)
        workers.append(work)
        return work

    monkeypatch.setattr(WorkflowSizeAdmission, "_create_work", create_work)
    with pytest.raises(ValueError, match="tool retention"):
        NativeAdmissionRuntime(event_socket_path=str(tmp_path / "invalid.sock"))
    assert len(workers) == 1 and workers[0]._stopped is True


@pytest.mark.parametrize("required", ((), ("long-a",)))
@pytest.mark.parametrize("deadline", (None, 1, 2, 4))
def test_parameter_audit_preserves_required_join_search_cutoff_and_original_evidence(
    required, deadline,
):
    row, costs = recorded_aging(required=required, deadline=deadline)
    before = deepcopy(row)
    result = counterfactual(row, replace(costs, join_progress="linear"), ReadyWaitSettings(120000))
    assert set(required) <= set(result["selected"])
    assert result["search_complete"] == row["decision"]["search_complete"]
    if not result["search_complete"]:
        assert result["selected"] == row["decision"]["reference"]
    assert row == before


def test_parameter_sensitivity_has_no_future_service_or_jct_estimate():
    row, costs = recorded_aging()
    before = deepcopy(row)
    report = inspect([row], {"identity": "frozen", "costs": asdict(costs)})
    assert report["counts"]["exact_actual_searches_replayed"] == 1
    assert report["future_candidate_windows_native_service_and_JCT_not_simulated"] is True
    assert report["GPU_action"] is False
    assert report["performance_accepted"] is False
    assert report["variants"]["linear_different_unused_cost_horizon"]["counts"][
        "changed_from_bounded_linear"
    ] == 0
    assert row == before
