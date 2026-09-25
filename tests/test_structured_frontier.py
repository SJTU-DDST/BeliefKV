from __future__ import annotations

import json
from pathlib import Path

import pytest

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.predictor.action_frontier import ActionTimingCurve
from beliefkv.predictor.composer import RemainingTimePredictor
from beliefkv.predictor.frontier_belief import (
    BeliefScopeBuilder,
    PredictiveEvidenceReadSet,
    ScenarioProjection,
)
from beliefkv.predictor.structured_frontier import (
    EmpiricalDistribution,
    evaluate_frontier_model,
    _demand_feature_key,
    _local_features_from_row,
    FrontierBeliefModel,
    FrontierModelHyperparameters,
    FrontierScenarioComposer,
    LocalFrontierFeatures,
    LocalFrontierPrediction,
    WaitBelief,
    WaitBeliefKind,
    load_decision_rows,
    load_evaluation_rows,
    runtime_environment_digest,
    select_frontier_hyperparameters,
    summarize_training_corpus,
    validate_training_corpus_diversity,
    _recall_oriented_threshold,
)


def test_local_frontier_features_round_trip() -> None:
    features = LocalFrontierFeatures(
        invocation_id="child-7",
        state="wait_tool",
        agent_definition_id="coder",
        boundary_history=("tool", "tool"),
        tool_family="shell",
        backend_class="sandbox",
        command_class="pytest",
        observed_command_class="test_suite",
        generated_tokens=37,
        elapsed_wait_ms=125.5,
        current_sequence_tokens=8192,
        active_tool_count=3,
        backend_pressure="shell:3",
        invocation_elapsed_ms=12_345.0,
        state_elapsed_ms=125.5,
        llm_round=7,
        child_count=2,
        unfinished_child_count=1,
        is_child=True,
    )

    assert LocalFrontierFeatures.from_dict(features.to_dict()) == features


def test_observed_command_contract_keeps_child_and_root_tool_heads_separate() -> None:
    rows = []
    for index in range(12):
        row = _tool_row(f"isolated-{index}", 80.0 if index < 6 else 4000.0, "success")
        row["trigger_attributes"].update({
            "tool_name": "execute",
            "is_child": index >= 6,
            "observed_command_class": "test_suite",
        })
        row["trigger_invocation_id"] = "worker"
        row["invocations"][0]["is_child"] = index >= 6
        rows.append(row)
    model = FrontierBeliefModel(tool_feature_contract="observed_command_child_v1")
    model.fit(rows)
    root = LocalFrontierFeatures(
        invocation_id="root", state="wait_tool", agent_definition_id="worker",
        tool_family="shell", command_class="execute",
        observed_command_class="test_suite", current_sequence_tokens=4096,
        active_tool_count=1, backend_pressure="active_family:1",
    )
    child = LocalFrontierFeatures.from_dict({**root.to_dict(), "is_child": True})
    root_p50 = model.predict(root).wait_belief.residual_duration.quantile(0.5)
    child_p50 = model.predict(child).wait_belief.residual_duration.quantile(0.5)
    assert root_p50 < 500
    assert child_p50 > 2000
    loaded = FrontierBeliefModel.from_dict(model.to_dict())
    assert loaded.tool_feature_contract == "observed_command_child_v1"
    assert loaded.predict(child).wait_belief.residual_duration.quantile(0.5) == child_p50
    assert loaded.predict(root).wait_belief.residual_duration.quantile(0.5) == root_p50

    legacy_raw = FrontierBeliefModel.from_dict(
        FrontierBeliefModel().to_dict()
    ).to_dict()
    legacy_raw["schema_version"] = 6
    legacy_raw.pop("tool_feature_contract")
    legacy_raw["components"].pop("child_tool")
    legacy = FrontierBeliefModel.from_dict(legacy_raw)
    assert legacy.tool_feature_contract == "legacy"
    assert legacy.predict(root).wait_belief.support_level == "unavailable"


def test_repeat_tool_contract_requires_fitted_uncertainty_and_causal_success() -> None:
    rows = []
    for index in range(40):
        row = _tool_row(f"repeat-{index}", 3010.0 + index, "success")
        row["workflow_id"] = f"workflow-{index // 4}"
        row["trigger_invocation_id"] = "worker"
        row["trigger_attributes"].update({
            "tool_name": "execute",
            "observed_command_class": "test_suite",
            "is_child": True,
            "previous_same_input_status": "success",
            "previous_same_input_duration_ms": 3000.0,
        })
        row["invocations"][0]["is_child"] = True
        rows.append(row)
    model = FrontierBeliefModel(
        tool_feature_contract="observed_command_child_repeat_v2"
    )
    model.fit(rows)
    assert model.repeat_error_p90_ms == 45.0
    features = LocalFrontierFeatures(
        invocation_id="child", state="wait_tool", is_child=True,
        command_class="execute", observed_command_class="test_suite",
        previous_same_input_duration_ms=3000.0, elapsed_wait_ms=500.0,
    )
    wait = model.predict(features).wait_belief
    assert wait.residual_duration.quantile(.5) == 2500.0
    assert wait.residual_duration.quantile(.9) == 2545.0
    assert FrontierBeliefModel.from_dict(model.to_dict()).predict(
        features
    ).wait_belief.residual_duration.quantile(.5) == 2500.0
    v7 = FrontierBeliefModel(tool_feature_contract="observed_command_child_v1")
    v7.fit(rows)
    assert v7.predict(features).wait_belief.residual_duration.quantile(.5) != 2500.0
    with pytest.raises(ValueError, match="schema v8"):
        FrontierBeliefModel.from_dict({**model.to_dict(), "schema_version": 7})


def test_repeat_tool_calibration_is_workflow_grouped_and_falls_back_if_sparse() -> None:
    fit_rows = []
    for index in range(40):
        row = _tool_row(f"fit-{index}", 3020.0, "success")
        row["workflow_id"] = f"fit-workflow-{index // 4}"
        row["trigger_invocation_id"] = "worker"
        row["trigger_attributes"].update({
            "tool_name": "execute", "observed_command_class": "test_suite",
            "is_child": True, "previous_same_input_status": "success",
            "previous_same_input_duration_ms": 3000.0,
        })
        row["invocations"][0]["is_child"] = True
        fit_rows.append(row)
    model = FrontierBeliefModel(
        tool_feature_contract="observed_command_child_repeat_v2"
    )
    model.fit(fit_rows)
    original = model.to_dict()
    calibration_rows = []
    for index in range(8):
        row = _tool_row(
            f"cal-{index}", 4000.0 if index == 7 else 3030.0, "success"
        )
        row["split"] = "calibration"
        row["workflow_id"] = f"cal-workflow-{index}"
        row["trigger_invocation_id"] = "worker"
        row["trigger_attributes"].update({
            "tool_name": "execute", "observed_command_class": "test_suite",
            "is_child": True, "previous_same_input_status": "success",
            "previous_same_input_duration_ms": 3000.0,
        })
        row["invocations"][0]["is_child"] = True
        calibration_rows.append(row)
    summary = model.calibrate(calibration_rows)
    assert summary["observation_counts"]["repeat_workflows_calibrated"] == 8
    assert summary["repeat_same_input_margin_ms"] >= 1000.0
    sparse = FrontierBeliefModel.from_dict(original)
    sparse_summary = sparse.calibrate(calibration_rows[:7])
    assert sparse_summary["repeat_same_input_margin_ms"] is None
    assert sparse.repeat_error_p90_ms is None


def test_observed_command_contract_rejects_incomplete_tool_provenance() -> None:
    model = FrontierBeliefModel(tool_feature_contract="observed_command_child_v1")
    row = _tool_row("no-origin", 100.0, "success")
    row["trigger_attributes"]["tool_name"] = "execute"
    with pytest.raises(ValueError, match="trigger invocation identity"):
        model.fit([row])
    row["trigger_invocation_id"] = "worker"
    with pytest.raises(ValueError, match="provenance"):
        model.fit([row])
    row["trigger_attributes"]["is_child"] = False
    with pytest.raises(ValueError, match="observed command class"):
        model.fit([row])
    row["trigger_attributes"]["observed_command_class"] = "other"
    row["invocations"][0]["is_child"] = True
    with pytest.raises(ValueError, match="disagrees with invocation origin"):
        model.fit([row])


def test_observed_tool_head_ignores_sibling_waiting_on_other_tool() -> None:
    rows = []
    for index in range(6):
        row = _tool_row(f"parallel-{index}", 100.0, "success")
        row["trigger_invocation_id"] = "worker"
        row["trigger_attributes"].update({
            "tool_name": "execute", "observed_command_class": "python_inline",
            "is_child": False,
        })
        sibling = dict(row["invocations"][0])
        sibling.update({"invocation_id": "sibling", "is_child": True})
        row["invocations"].append(sibling)
        row["labels"].append({
            "invocation_id": "sibling",
            "next_boundary_status": "success",
            "next_boundary_delay_ms": 5000.0,
        })
        rows.append(row)
    model = FrontierBeliefModel(tool_feature_contract="observed_command_child_v1")
    model.fit(rows)
    assert model.child_tool.to_dict()["status"] == []
    assert sum(
        item["counts"].get("success", 0)
        for item in model.tool.to_dict()["status"]
        if item["key"] == ["*"]
    ) == pytest.approx(6.0)
    sibling_features = _local_features_from_row(
        rows[0], rows[0]["invocations"][1],
        tool_feature_contract=model.tool_feature_contract,
    )
    assert sibling_features.observed_command_class == "unknown"
    initiated_child = dict(rows[0])
    initiated_child["trigger_invocation_id"] = "sibling"
    initiated_child["trigger_attributes"] = {
        **rows[0]["trigger_attributes"], "is_child": True
    }
    assert _local_features_from_row(
        initiated_child,
        {"invocation_id": "sibling", "state": "wait_tool"},
        tool_feature_contract=model.tool_feature_contract,
    ).is_child is True


def _row(decision: str, remaining: int, target: str = "function_call") -> dict:
    return {
        "schema_version": 2,
        "decision_id": decision,
        "episode_group_id": f"episode-{decision}",
        "split": "development",
        "trigger_kind": "llm_submit",
        "invocations": [
            {
                "invocation_id": "child",
                "agent_definition_id": "worker",
                "state": "running_llm",
                "boundary_history": ["tool"],
                "context_tokens": 4096,
                "current_sequence_tokens": 4096,
                "observed_output_tokens": 0,
            }
        ],
        "labels": [
            {
                "invocation_id": "child",
                "next_boundary_kind": target,
                "remaining_output_tokens": remaining,
                "reentry_prompt_delta_tokens": 128,
                "demand_label_semantics": "token demand only",
                "censored": False,
            }
        ],
    }


def _calibration_row(decision: str, remaining: float) -> dict:
    row = _row(decision, remaining, target="final_answer")
    row["split"] = "calibration"
    return row


def _tool_row(decision: str, duration_ms: float, status: str) -> dict:
    return {
        "schema_version": 2,
        "decision_id": decision,
        "episode_group_id": f"episode-{decision}",
        "split": "development",
        "trigger_kind": "tool_start",
        "trigger_attributes": {"tool_family": "shell"},
        "invocations": [
            {
                "invocation_id": "worker",
                "agent_definition_id": "worker",
                "state": "wait_tool",
                "active_tool_family": "shell",
                "active_tool_elapsed_ms": 0,
                "context_tokens": 4096,
                "current_sequence_tokens": 4096,
                "active_tool_count": 1,
                "backend_pressure": "active_family:1",
            }
        ],
        "labels": [
            {
                "invocation_id": "worker",
                "next_boundary_kind": "tool_end",
                "next_boundary_status": status,
                "next_boundary_delay_ms": duration_ms,
                "censored": status == "censored",
            }
        ],
    }


def _action_target(
    decision: str,
    *,
    split: str,
    residual_wait_ms: float,
    d2h_tau_ms: float = 100.0,
    h2d_tau_ms: float = 300.0,
) -> dict:
    return {
        "schema_version": 4,
        "row_type": "operational_action_target",
        "contract_id": "test-contract",
        "deployment_profile_id": "test-profile",
        "decision_id": decision,
        "workflow_id": f"workflow-{decision}",
        "project": "project/test",
        "split": split,
        "invocation_id": "worker",
        "tool_wait_episode_id": f"tool-{decision}",
        "timestamp_ms": 1000.0,
        "elapsed_wait_ms": 0.0,
        "residual_wait_ms": residual_wait_ms,
        "right_censored": False,
        "active_tool_count": 1,
        "tool_family": "shell",
        "backend_class": "unknown",
        "command_class": "unknown",
        "agent_definition_id": "worker",
        "boundary_history": ["tool"],
        "current_sequence_tokens": 4096,
        "actual_kv_bytes": 4096 * 98304,
        "tau_evidence": "test",
        "physical_shape_available": False,
        "actions": {
            "prepare_host": {
                "operational_tau_ms": d2h_tau_ms,
                "outcome_known": True,
                "outcome": residual_wait_ms > d2h_tau_ms,
            },
            "prefetch_gpu": {
                "operational_tau_ms": h2d_tau_ms,
                "outcome_known": True,
                "outcome": residual_wait_ms <= h2d_tau_ms,
            },
        },
    }


def _ready_row(decision: str, output_tokens: int) -> dict:
    return {
        "schema_version": 2,
        "decision_id": decision,
        "episode_group_id": f"episode-{decision}",
        "split": "development",
        "trigger_kind": "reactivate",
        "invocations": [
            {
                "invocation_id": "worker",
                "agent_definition_id": "worker",
                "state": "ready",
                "context_tokens": 4096,
                "current_sequence_tokens": 4096,
            }
        ],
        "labels": [
            {
                "invocation_id": "worker",
                "next_boundary_kind": "final_answer",
                "next_output_tokens": output_tokens,
                "censored": False,
            }
        ],
    }


def _wait_tool_row_with_next_output(decision: str, output_tokens: int) -> dict:
    row = _tool_row(decision, duration_ms=100.0, status="success")
    row["labels"][0]["next_output_tokens"] = output_tokens
    return row


def _wait_tool_features() -> LocalFrontierFeatures:
    return LocalFrontierFeatures(
        invocation_id="worker",
        state="wait_tool",
        agent_definition_id="worker",
        boundary_history=("tool",),
        tool_family="shell",
        backend_class="unknown",
        generated_tokens=0,
        elapsed_wait_ms=0.0,
        current_sequence_tokens=4096,
        active_tool_count=1,
        backend_pressure="active_family:1",
    )


def _demand_key(features: LocalFrontierFeatures) -> tuple[str, ...]:
    return _demand_feature_key(
        features.agent_definition_id,
        features.state,
        features.tool_family,
        {
            "current_sequence_tokens": features.current_sequence_tokens,
            "generated_tokens": features.generated_tokens,
            "backend_class": features.backend_class,
        },
    )


def _fixed_prediction(invocation_id: str, decode_tokens: float) -> LocalFrontierPrediction:
    point = EmpiricalDistribution((decode_tokens,), (1.0,), 4.0)
    empty = EmpiricalDistribution.empty()
    return LocalFrontierPrediction(
        invocation_id=invocation_id,
        boundary_distribution={"final": 1.0},
        current_sequence_tokens=4096,
        remaining_decode_tokens=point,
        remaining_external_wait=empty,
        tool_terminal_distribution={"success": 1.0},
        prompt_growth_tokens=empty,
        next_output_tokens=empty,
        support_level="exact",
        calibration_coverage=0.0,
    )


def _write_dataset(
    root: Path,
    *,
    run_id: str,
    split: str,
    decision_id: str,
    formal_training_eligible: bool = True,
    formal_local_training_eligible: bool | None = None,
) -> Path:
    root.mkdir(parents=True)
    (root / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "dataset_kind": "beliefkv_p6_training_evidence",
                "formal_training_eligible": formal_training_eligible,
                "formal_local_training_eligible": (
                    formal_training_eligible
                    if formal_local_training_eligible is None
                    else formal_local_training_eligible
                ),
                "evaluation_role": (
                    "frozen_split_local_training_evidence"
                    if formal_local_training_eligible is True
                    else "frozen_split_training_evidence"
                ),
                "source": {
                    "run_id": run_id,
                    "workload_manifest_sha256": f"manifest-{run_id}",
                    "runtime_environment_contract": {
                        "uniform": True,
                        "runtime_profile": {"sha256": "runtime-profile"},
                        "model_revision_sha256": {
                            "config.json": "model-config",
                            "tokenizer.json": "tokenizer",
                        },
                        "server_identity": {
                            "weight_dtype": "bfloat16",
                            "resolved_kv_dtype": "bfloat16",
                        },
                        "hardware": {"uuid": "gpu"},
                        "sglang_commit": "sglang",
                        "sglang_patch_sha256": "patch",
                    },
                    "collection_contract": {
                        "plan_id": "p6-agent-semantics-v1",
                        "split": split,
                        "training_eligible": formal_training_eligible,
                        "runtime_source_stable": True,
                        "runtime_policy": "frozen_p5_observed",
                        "predictor_enabled": False,
                        "predictive_actions_enabled": False,
                    },
                },
                "split_contract": {
                    "source": "explicit frozen split manifest",
                    "development_only": False,
                    "manifest_digest": "frozen-split",
                },
            }
        ),
        encoding="utf-8",
    )
    row = _row(decision_id, 10.0)
    row["split"] = split
    (root / "frontier_decision_points.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize(
    "plan_id",
    (
        "qwen35-native-reactive-v0520-v1",
        "qwen35-native-reactive-v0520-v5-overlapped-128root",
    ),
)
def test_native_formal_loader_only_accepts_verified_local_train(
    tmp_path: Path, plan_id: str
) -> None:
    root = _write_dataset(
        tmp_path / "native", run_id="native-run", split="train",
        decision_id="native-decision", formal_training_eligible=False,
        formal_local_training_eligible=True,
    )
    path = root / "dataset_manifest.json"
    manifest = json.loads(path.read_text())
    contract = manifest["source"]["collection_contract"]
    contract.update({
        "plan_id": plan_id,
        "runtime_policy": "frozen_native_reactive_v0520",
        "raw_trace_eligible": True,
        "model_revision_stable": True,
        "training_eligible": False,
    })
    manifest["source"]["runtime_environment_contract"].update({
        "runtime_kind": "native_reactive_v0520",
        "runtime_profile": None,
    })
    manifest["source"]["native_request_evidence"] = {"telemetry_complete": True}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="native reactive input"):
        load_decision_rows((root,), allowed_splits=("train",))
    rows, _ = load_decision_rows(
        (root,), allowed_splits=("train",), allow_formal_local=True
    )
    assert len(rows) == 1
    with pytest.raises(ValueError, match="cannot consume calibration"):
        load_decision_rows(
            (root,), allowed_splits=("calibration",), allow_formal_local=True
        )
    manifest["source"]["native_request_evidence"]["telemetry_complete"] = False
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="native reactive input"):
        load_decision_rows(
            (root,), allowed_splits=("train",), allow_formal_local=True
        )


def test_native_calibration_loader_is_split_bound_and_requires_permission(
    tmp_path: Path,
) -> None:
    root = _write_dataset(
        tmp_path / "native-calibration", run_id="native-calibration-run",
        split="calibration", decision_id="calibration-decision",
        formal_training_eligible=False, formal_local_training_eligible=True,
    )
    path = root / "dataset_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["source"]["collection_contract"].update({
        "plan_id": "qwen35-native-reactive-v0520-v1-calibration-66root",
        "runtime_policy": "frozen_native_reactive_v0520",
        "raw_trace_eligible": True,
        "model_revision_stable": True,
        "training_eligible": False,
    })
    manifest["source"]["runtime_environment_contract"].update({
        "runtime_kind": "native_reactive_v0520",
        "runtime_profile": None,
    })
    manifest["source"]["native_request_evidence"] = {"telemetry_complete": True}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="formal evaluation input is ineligible"):
        load_evaluation_rows((root,), split="calibration")
    rows, _ = load_evaluation_rows(
        (root,), split="calibration", allow_formal_local=True,
    )
    assert len(rows) == 1
    with pytest.raises(ValueError, match="cannot provide 'train' evidence"):
        load_decision_rows((root,), allowed_splits=("train",), allow_formal_local=True)
    manifest["source"]["native_request_evidence"]["telemetry_complete"] = False
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="native reactive input"):
        load_evaluation_rows((root,), split="calibration", allow_formal_local=True)


def test_formal_loaders_fail_closed_on_ineligible_dataset(tmp_path: Path) -> None:
    train = _write_dataset(
        tmp_path / "train",
        run_id="run-train",
        split="train",
        decision_id="train-decision",
        formal_training_eligible=False,
    )
    calibration = _write_dataset(
        tmp_path / "calibration",
        run_id="run-calibration",
        split="calibration",
        decision_id="calibration-decision",
        formal_training_eligible=False,
    )

    with pytest.raises(ValueError, match="formal training input is ineligible"):
        load_decision_rows((train,), allowed_splits=("train",))
    with pytest.raises(ValueError, match="formal evaluation input is ineligible"):
        load_evaluation_rows((calibration,), split="calibration")


def test_calibration_loader_requires_explicit_formal_local_permission(
    tmp_path: Path,
) -> None:
    calibration = _write_dataset(
        tmp_path / "calibration-local",
        run_id="run-calibration-local",
        split="calibration",
        decision_id="calibration-local-decision",
        formal_training_eligible=False,
        formal_local_training_eligible=True,
    )

    with pytest.raises(ValueError, match="formal evaluation input is ineligible"):
        load_evaluation_rows((calibration,), split="calibration")

    rows, manifests = load_evaluation_rows(
        (calibration,), split="calibration", allow_formal_local=True
    )

    assert [row["decision_id"] for row in rows] == [
        "calibration-local-decision"
    ]
    assert manifests[0]["formal_training_eligible"] is False
    assert manifests[0]["formal_local_training_eligible"] is True

    with pytest.raises(ValueError, match="allowed only for calibration"):
        load_evaluation_rows(
            (calibration,), split="test_id", allow_formal_local=True
        )


def test_formal_loader_attaches_only_complete_child_return_targets(
    tmp_path: Path,
) -> None:
    root = _write_dataset(
        tmp_path / "train-child-return",
        run_id="run-child-return",
        split="train",
        decision_id="child-decision",
    )
    row = json.loads(
        (root / "frontier_decision_points.jsonl").read_text(encoding="utf-8")
    )
    row["timestamp_ms"] = 1_000.0
    (root / "frontier_decision_points.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    (root / "reentries.jsonl").write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "reentry_kind": "join",
                    "training_eligible": True,
                    "terminal_status": "satisfied",
                    "member_outcomes": [
                        {
                            "invocation_id": "child",
                            "start_ts_ms": 500.0,
                            "return_ts_ms": 6_000.0,
                        }
                    ],
                },
                {
                    "reentry_kind": "join",
                    "training_eligible": False,
                    "terminal_status": "censored",
                    "member_outcomes": [
                        {"invocation_id": "other", "return_ts_ms": 2_000.0}
                    ],
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    rows, _ = load_decision_rows((root,), allowed_splits=("train",))
    label = rows[0]["labels"][0]

    assert label["remaining_to_return_ms"] == 5_000.0
    assert label["target_training_eligible"]["child_completion"]
    assert label["target_horizon_timestamp_ms"]["child_completion"] == 6_000.0
    assert rows[0]["invocations"][0]["invocation_elapsed_ms"] == 500.0
    assert rows[0]["invocations"][0]["is_child"] is True


def test_child_completion_head_predicts_full_return_residual() -> None:
    rows = []
    for index, residual_ms in enumerate((300_000.0, 600_000.0, 900_000.0)):
        row = _row(f"child-return-{index}", 32)
        row["workflow_id"] = f"workflow-{index}"
        row["invocations"][0].update(
            {
                "llm_round": index + 1,
                "state_elapsed_ms": 100.0,
                "invocation_elapsed_ms": 1_000.0 * (index + 1),
            }
        )
        row["labels"][0]["remaining_to_return_ms"] = residual_ms
        row["labels"][0]["target_training_eligible"] = {
            "child_completion": True
        }
        rows.append(row)

    model = FrontierBeliefModel(model_version="child-completion-test")
    summary = model.fit(rows)
    prediction = model.predict(
        LocalFrontierFeatures(
            invocation_id="child",
            state="running_llm",
            agent_definition_id="worker",
            current_sequence_tokens=4096,
            llm_round=2,
            invocation_elapsed_ms=2_000.0,
            state_elapsed_ms=100.0,
            is_child=True,
        )
    )

    assert summary["observation_counts"]["child_completion"] == 3
    assert prediction.remaining_to_return_ms.support == pytest.approx(3.0)
    assert prediction.remaining_to_return_ms.quantile(0.5) > 100_000.0
    restored = LocalFrontierPrediction.from_dict(prediction.to_dict())
    assert restored.remaining_to_return_ms == prediction.remaining_to_return_ms


def test_runtime_environment_digest_ignores_source_count_and_paths() -> None:
    base = {
        "runtime_profile": {
            "path": "/first/profile.json",
            "profile_id": "h200_bf16_v4",
            "sha256": "profile-sha",
        },
        "model_revision_sha256": {
            "config.json": "config-sha",
            "tokenizer.json": "tokenizer-sha",
        },
        "hardware": {
            "index": 0,
            "name": "NVIDIA H200 NVL",
            "uuid": "gpu-uuid",
            "driver_version": "driver",
            "memory_total_mib": 143771,
        },
        "server_identity": {
            "model_path": "/models/qwen",
            "served_model_name": "qwen",
            "sglang_version": "0.5.2rc1",
            "weight_dtype": "bfloat16",
            "configured_kv_dtype": "auto",
            "resolved_kv_dtype": "bfloat16",
        },
        "sglang_commit": "sglang",
        "sglang_patch_sha256": "patch",
        "uniform": True,
        "physical_source_count": 5,
    }
    calibration = json.loads(json.dumps(base))
    calibration["runtime_profile"]["path"] = "/second/profile.json"
    calibration["physical_source_count"] = 1

    assert runtime_environment_digest(base) == runtime_environment_digest(
        calibration
    )
    calibration["server_identity"]["resolved_kv_dtype"] = "fp8_e4m3"
    assert runtime_environment_digest(base) != runtime_environment_digest(
        calibration
    )


def test_formal_loader_rejects_p5_development_evidence(tmp_path: Path) -> None:
    root = _write_dataset(
        tmp_path / "p5-w4",
        run_id="p5-run",
        split="train",
        decision_id="p5-decision",
    )
    manifest_path = root / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["collection_contract"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="P6 collection plan"):
        load_decision_rows((root,), allowed_splits=("train",))


def test_formal_diversity_gate_counts_workflows_not_decision_rows() -> None:
    rows = []
    for index in range(200):
        row = _row(f"decision-{index}", 10)
        row.update(
            {
                "run_id": "run-w4",
                "workflow_id": f"workflow-{index % 4}",
                "project": f"project-{index % 2}",
                "instance_id": f"task-{index % 4}",
                "base_commit": f"commit-{index % 4}",
                "split": "train",
            }
        )
        rows.append(row)

    summary = summarize_training_corpus(rows)
    assert summary["decision_point_count"] == 200
    assert summary["workflow_count"] == 4
    assert summary["task_count"] == 4
    with pytest.raises(ValueError, match="workflow memorization"):
        validate_training_corpus_diversity(rows)


@pytest.mark.parametrize(
    "contaminated_feature",
    ("batch_size", "elapsed_gpu_service_ms", "observed_gpu_service_ms"),
)
def test_frontier_fit_rejects_load_coupled_semantic_features(
    contaminated_feature: str,
) -> None:
    row = _row("contaminated", 10)
    row["invocations"][0][contaminated_feature] = 4

    with pytest.raises(ValueError, match="scheduler features are forbidden"):
        FrontierBeliefModel().fit([row])


def test_frontier_fit_allows_load_observations_in_diagnostics_only() -> None:
    row = _row("diagnostic", 10)
    row["invocations"][0]["diagnostics"] = {
        "last_observed_batch_size": 4,
        "observed_gpu_service_ms": 12.5,
    }

    summary = FrontierBeliefModel().fit([row])

    assert summary["observation_counts"]["remaining_decode_demand"] == 1


def test_state_semantic_decode_uses_next_output_for_waiting_states() -> None:
    rows = [_wait_tool_row_with_next_output(f"d{i}", 128) for i in range(4)]
    model = FrontierBeliefModel(
        hyperparameters=FrontierModelHyperparameters(
            empirical_minimum_support=1.0
        )
    )
    summary = model.fit(rows)
    assert (
        summary["observation_counts"]["state_conditional_decode_demand"]
        == 4
    )
    features = _wait_tool_features()
    dist, level = model.decode_demand.predict(_demand_key(features))
    assert level in {"exact", "backoff"}
    assert dist.quantile(0.5) > 0


def test_without_state_semantic_labels_waiting_decode_is_unavailable() -> None:
    # _tool_row carries no next_output_tokens: the wait_tool decode key has no
    # observations and must fail closed to unavailable (previous behavior).
    rows = [_tool_row(f"d{i}", 100.0, "success") for i in range(4)]
    model = FrontierBeliefModel(
        hyperparameters=FrontierModelHyperparameters(
            empirical_minimum_support=1.0
        )
    )
    model.fit(rows)
    features = _wait_tool_features()
    _dist, level = model.decode_demand.predict(_demand_key(features))
    assert level == "unavailable"


def test_formal_loaders_reject_duplicate_runs_and_decisions(tmp_path: Path) -> None:
    first = _write_dataset(
        tmp_path / "first",
        run_id="same-run",
        split="train",
        decision_id="decision-a",
    )
    duplicate_run = _write_dataset(
        tmp_path / "duplicate-run",
        run_id="same-run",
        split="train",
        decision_id="decision-b",
    )
    with pytest.raises(ValueError, match="duplicate source run"):
        load_decision_rows(
            (first, duplicate_run),
            allowed_splits=("train",),
        )

    duplicate_decision = _write_dataset(
        tmp_path / "duplicate-decision",
        run_id="different-run",
        split="train",
        decision_id="decision-a",
    )
    with pytest.raises(ValueError, match="duplicate decision point"):
        load_decision_rows(
            (first, duplicate_decision),
            allowed_splits=("train",),
        )


def test_local_model_roundtrip_preserves_distribution(tmp_path) -> None:
    hyperparameters = FrontierModelHyperparameters(
        boundary_max_order=2,
        boundary_minimum_support=2.0,
        empirical_minimum_support=2.0,
        tool_minimum_support=2.0,
    )
    model = FrontierBeliefModel(
        model_version="dev-v1",
        hyperparameters=hyperparameters,
    )
    summary = model.fit(
        [_row("d1", 10), _row("d2", 20), _row("d3", 15), _row("d4", 25)]
    )
    assert summary["episode_count"] == 4
    features = LocalFrontierFeatures(
        invocation_id="child",
        state="running_llm",
        agent_definition_id="worker",
        boundary_history=("tool",),
        current_sequence_tokens=4096,
    )
    before = model.predict(features)
    path = tmp_path / "model.json"
    model.save(path, metadata={"development_only": True})
    loaded = FrontierBeliefModel.load(path)
    after = loaded.predict(features)
    assert before == after
    assert loaded.hyperparameters == hyperparameters
    assert loaded.artifact_metadata == {"development_only": True}
    assert before.boundary_distribution["tool"] > 0.9
    assert before.calibration_coverage == 0.0


def test_lopo_hyperparameter_selection_is_train_only_and_project_macro() -> None:
    rows = []
    for project, base in (("org/a", 10), ("org/b", 20), ("org/c", 30)):
        for offset in range(4):
            row = _row(f"{project}-{offset}", base + offset)
            row["split"] = "train"
            row["project"] = project
            rows.append(row)

    report = select_frontier_hyperparameters(
        rows,
        candidates=(
            FrontierModelHyperparameters(),
            FrontierModelHyperparameters(
                boundary_max_order=2,
                boundary_minimum_support=2.0,
                empirical_minimum_support=2.0,
                tool_minimum_support=2.0,
            ),
        ),
    )
    assert report["projects"] == ["org/a", "org/b", "org/c"]
    assert report["candidate_count"] == 2
    assert len(report["candidates"][0]["folds"]) == 3
    assert report["selected_hyperparameters"] in [
        item["hyperparameters"] for item in report["candidates"]
    ]

    invalid = [dict(rows[0], split="calibration"), *rows[1:]]
    with pytest.raises(ValueError, match="formal train rows"):
        select_frontier_hyperparameters(invalid)


def test_calibration_does_not_refit_training_counts_and_survives_roundtrip(
    tmp_path,
) -> None:
    model = FrontierBeliefModel(model_version="train-v1")
    model.fit(
        [_row("d1", 10), _row("d2", 20), _row("d3", 15), _row("d4", 25)]
    )
    before = model.to_dict()["components"]
    summary = model.calibrate(
        [
            _calibration_row("c1", 100),
            _calibration_row("c2", 120),
            _calibration_row("c3", 80),
        ],
        target_coverage=0.9,
    )
    assert model.to_dict()["components"] == before
    assert summary["training_counts_refit"] is False
    assert summary["interval_slack"]["remaining_decode_tokens"] > 0

    features = LocalFrontierFeatures(
        invocation_id="child",
        state="running_llm",
        agent_definition_id="worker",
        boundary_history=("tool",),
        current_sequence_tokens=4096,
    )
    prediction = model.predict(features)
    assert prediction.calibration_coverage == 0.9
    assert (
        prediction.calibrated_intervals["remaining_decode_tokens"][1]
        >= 120
    )
    path = tmp_path / "calibrated.json"
    model.save(path)
    assert FrontierBeliefModel.load(path).predict(features) == prediction


def test_remaining_time_predictor_loads_schema_v4_frontier() -> None:
    model = FrontierBeliefModel(model_version="schema-v4-loader")

    predictor = RemainingTimePredictor.from_dict(model.to_dict())

    assert predictor.frontier_model is not None
    assert predictor.frontier_model.model_version == "schema-v4-loader"


def test_calibration_rejects_training_or_test_rows() -> None:
    model = FrontierBeliefModel(model_version="train-v1")
    model.fit(
        [_row("d1", 10), _row("d2", 20), _row("d3", 15), _row("d4", 25)]
    )
    with pytest.raises(ValueError, match="calibration split"):
        model.calibrate([_row("train", 30)])


def test_calibration_excludes_right_censored_tool_completion_targets() -> None:
    model = FrontierBeliefModel(model_version="tool-calibration-v1")
    model.fit(
        [
            _tool_row("train-success", 100, "success"),
            _tool_row("train-error", 200, "error"),
        ]
    )
    completed = _tool_row("calibration-complete", 150, "success")
    completed["split"] = "calibration"
    completed["labels"][0]["target_training_eligible"] = {
        "external_wait": True
    }
    completed["labels"][0]["target_right_censored"] = {
        "external_wait": False
    }
    censored = _tool_row("calibration-censored", 500, "censored")
    censored["split"] = "calibration"
    censored["labels"][0]["target_training_eligible"] = {
        "external_wait": True
    }
    censored["labels"][0]["target_right_censored"] = {
        "external_wait": True
    }

    summary = model.calibrate(
        [completed, censored],
        action_targets=[
            _action_target(
                "calibration-complete",
                split="calibration",
                residual_wait_ms=150.0,
            )
        ],
    )

    assert summary["observation_counts"]["tool_terminal"] == 1
    assert (
        summary["observation_counts"][
            "tool_right_censored_excluded_from_terminal"
        ]
        == 1
    )
    assert "remaining_external_wait_ms" not in summary["observation_counts"]
    assert "remaining_external_wait_ms" not in summary["interval_slack"]
    assert summary["observation_counts"]["tool_wait_action_slack"] > 0
    assert summary["observation_counts"]["prepare_host_operational_tau"] == 1
    assert summary["observation_counts"]["prefetch_gpu_operational_tau"] == 1
    assert model.tool_survival_logit_scale > 0
    assert model.to_dict()["tool_survival_logit_scale"] == (
        model.tool_survival_logit_scale
    )


def test_tool_prediction_conditions_competing_risk_on_elapsed_wait(tmp_path) -> None:
    model = FrontierBeliefModel(model_version="tool-survival-v1")
    model.fit(
        [
            _tool_row("short-error", 10, "error"),
            _tool_row("long-a", 100, "success"),
            _tool_row("long-b", 110, "success"),
            _tool_row("long-c", 120, "success"),
        ]
    )
    features = LocalFrontierFeatures(
        invocation_id="worker",
        state="wait_tool",
        agent_definition_id="worker",
        tool_family="shell",
        elapsed_wait_ms=50,
        current_sequence_tokens=4096,
        active_tool_count=1,
        backend_pressure="active_family:1",
    )
    prediction = model.predict(features)
    assert prediction.tool_terminal_distribution["success"] > 0.85
    assert prediction.remaining_external_wait.quantile(0.5) >= 50

    unsupported_tail = model.predict(
        LocalFrontierFeatures(
            invocation_id="worker",
            state="wait_tool",
            agent_definition_id="worker",
            tool_family="shell",
            elapsed_wait_ms=500,
            current_sequence_tokens=4096,
            active_tool_count=1,
            backend_pressure="active_family:1",
        )
    )
    assert unsupported_tail.remaining_external_wait.values == ()
    assert "tool_wait_unavailable" in unsupported_tail.ood_reasons

    path = tmp_path / "tool-survival.json"
    model.save(path)
    assert FrontierBeliefModel.load(path).predict(features) == prediction


def test_tool_wait_belief_exposes_action_slack_probability() -> None:
    belief = WaitBelief(
        kind=WaitBeliefKind.TOOL,
        residual_duration=EmpiricalDistribution(
            (10.0, 100.0),
            (0.25, 0.75),
            4.0,
        ),
        terminal_distribution={"success": 1.0},
        support_level="exact",
    )

    assert belief.slack_probability(50.0) == pytest.approx(0.75)
    assert belief.slack_probability(100.0) == 0.0


def test_join_wait_is_structural_and_has_no_fitted_wall_clock() -> None:
    model = FrontierBeliefModel(model_version="join-structural-v1")
    model.fit(
        [
            _tool_row("tool-a", 100, "success"),
            _tool_row("tool-b", 200, "error"),
        ]
    )

    prediction = model.predict(
        LocalFrontierFeatures(
            invocation_id="parent",
            state="wait_join",
            agent_definition_id="supervisor",
            current_sequence_tokens=4096,
        )
    )

    assert prediction.wait_belief.kind == WaitBeliefKind.JOIN
    assert prediction.wait_belief.dependency_composed
    assert prediction.wait_belief.residual_duration.values == ()
    assert prediction.remaining_external_wait.values == ()
    assert prediction.support_for("join_dependency") == "structural"
    assert "join_wait" not in model.to_dict()["components"]


def test_reentry_state_learns_next_call_output_demand_without_service_time() -> None:
    model = FrontierBeliefModel(model_version="reentry-v1")
    model.fit(
        [
            _ready_row("r1", 80),
            _ready_row("r2", 96),
            _ready_row("r3", 112),
            _ready_row("r4", 128),
        ]
    )
    prediction = model.predict(
        LocalFrontierFeatures(
            invocation_id="worker",
            state="ready",
            agent_definition_id="worker",
            current_sequence_tokens=4096,
        )
    )
    # State-semantic decode (deepseek): ready invocations now learn the next
    # LLM output demand as their decode target instead of falling to global.
    assert prediction.remaining_decode_tokens.quantile(0.5) > 0
    assert prediction.next_output_tokens.quantile(0.5) > 0


def test_episode_weighted_evaluation_reports_calibration_and_ood() -> None:
    model = FrontierBeliefModel(model_version="train-v1")
    model.fit(
        [_row("d1", 10), _row("d2", 20), _row("d3", 15), _row("d4", 25)]
    )
    calibration = [
        _calibration_row("c1", 30),
        _calibration_row("c2", 35),
        _calibration_row("c3", 40),
    ]
    model.calibrate(calibration)
    metrics = evaluate_frontier_model(model, calibration)
    assert metrics["splits"] == ["calibration"]
    assert metrics["classification"]["boundary"]["episode_weight"] == 3
    assert 0 <= metrics["classification"]["boundary"]["ece_10"] <= 1
    assert (
        0
        <= metrics["scalar"]["remaining_decode_tokens"][
            "calibrated_interval_coverage"
        ]
        <= 1
    )
    assert (
        0
        <= metrics["scalar"]["remaining_decode_tokens"][
            "local_episode_interval_coverage"
        ]
        <= 1
    )
    assert metrics["ood_fallback_semantics"] == (
        "action_state_required_head_unavailable"
    )
    assert "legacy_composite_ood_rate" not in metrics
    assert (
        metrics["target_availability"]["remaining_decode_demand"][
            "available_rate"
        ]
        == 1
    )


def test_action_slack_evaluation_reports_binary_decision_metrics() -> None:
    model = FrontierBeliefModel(model_version="train-v1")
    model.fit(
        [_row("d1", 10), _row("d2", 20), _row("d3", 15), _row("d4", 25)]
    )
    calibration = [_calibration_row("c1", 30)]
    model.calibrate(calibration)
    target = {
        "schema_version": 4,
        "split": "calibration",
        "decision_id": "action-1",
        "workflow_id": "workflow-1",
        "invocation_id": "invocation-1",
        "tool_wait_episode_id": "tool-1",
        "agent_definition_id": "coder",
        "current_sequence_tokens": 32,
        "elapsed_wait_ms": 0.0,
        "active_tool_count": 1,
        "tool_family": "shell",
        "backend_class": "execute",
        "command_class": "execute",
        "boundary_history": ["tool"],
        "actions": {
            "prepare_host": {
                "outcome_known": True,
                "outcome": True,
                "operational_tau_ms": 10.0,
            }
        },
    }

    metrics = evaluate_frontier_model(model, calibration, [target])
    action = metrics["wait_slack"]["prepare_host|wait_tool|operational_tau"]

    assert action["available_prediction_rate"] == 0.0
    assert 0.0 <= action["accuracy_at_0_5"] <= 1.0
    assert 0.0 <= action["majority_baseline_accuracy"] <= 1.0
    assert 0.0 <= action["precision_at_0_5"] <= 1.0
    assert 0.0 <= action["recall_at_0_5"] <= 1.0
    assert 0.0 <= action["specificity_at_0_5"] <= 1.0
    assert 0.0 <= action["balanced_accuracy_at_0_5"] <= 1.0
    assert "climatology_brier" in action
    assert "brier_skill" in action
    assert 0.0 <= action["precision_at_0_9"] <= 1.0
    assert 0.0 <= action["recall_at_0_9"] <= 1.0
    assert 0.0 <= action["specificity_at_0_9"] <= 1.0
    assert 0.0 <= action["predicted_positive_rate"] <= 1.0
    assert action["actual_positive_rate"] == 0.0


def test_prefetch_threshold_prefers_recall_without_accepting_prior_precision() -> None:
    threshold, metrics = _recall_oriented_threshold(
        (
            (0.10, False, 1.0),
            (0.20, True, 1.0),
            (0.30, False, 1.0),
            (0.40, True, 1.0),
            (0.70, False, 1.0),
            (0.80, True, 1.0),
        )
    )

    assert threshold == 0.20
    assert metrics["recall_at_decision_threshold"] == 1.0
    assert metrics["precision_at_decision_threshold"] == 0.6
    assert metrics["recall_at_decision_threshold"] > 1.0 / 3.0


def test_action_timing_inverts_calibrated_threshold_for_raw_quantile() -> None:
    prediction = LocalFrontierPrediction(
        invocation_id="worker",
        boundary_distribution={"tool": 1.0},
        current_sequence_tokens=4096,
        remaining_decode_tokens=EmpiricalDistribution.empty(),
        remaining_external_wait=EmpiricalDistribution(
            (100.0, 200.0), (0.5, 0.5), 2.0
        ),
        tool_terminal_distribution={"success": 1.0},
        prompt_growth_tokens=EmpiricalDistribution.empty(),
        next_output_tokens=EmpiricalDistribution.empty(),
        support_level="exact",
        calibration_coverage=0.95,
        wait_belief=WaitBelief(
            kind=WaitBeliefKind.TOOL,
            residual_duration=EmpiricalDistribution(
                (100.0, 200.0), (0.5, 0.5), 2.0
            ),
            support_level="exact",
            support_detail="test",
        ),
        action_timing_calibration={
            "prefetch_gpu": {
                "logit_scale": 2.0,
                "logit_offset": 1.0,
                "decision_threshold": 0.5,
            }
        },
    )

    timing = prediction.action_timing("prefetch_gpu", 150.0)

    assert timing is not None
    assert timing.decision_threshold == 0.5
    assert timing.raw_decision_threshold == pytest.approx(0.3775406688)
    assert timing.raw_decision_threshold != timing.decision_threshold


def test_uncalibrated_action_timing_uses_operational_curve() -> None:
    prediction = LocalFrontierPrediction(
        invocation_id="worker",
        boundary_distribution={},
        current_sequence_tokens=4096,
        remaining_decode_tokens=EmpiricalDistribution.empty(),
        remaining_external_wait=EmpiricalDistribution(
            (10.0, 20.0), (0.5, 0.5), 2.0
        ),
        tool_terminal_distribution={"success": 1.0},
        prompt_growth_tokens=EmpiricalDistribution.empty(),
        next_output_tokens=EmpiricalDistribution.empty(),
        support_level="pooled",
        calibration_coverage=0.0,
        wait_belief=WaitBelief(
            kind=WaitBeliefKind.TOOL,
            residual_duration=EmpiricalDistribution(
                (10.0, 20.0), (0.5, 0.5), 2.0
            ),
            support_level="exact",
        ),
        operational_timing_curve=ActionTimingCurve(
            tau_ms=(100.0, 200.0),
            release_within_probability=(0.2, 0.8),
            support_level="pooled",
            training_support=10.0,
        ),
    )

    prepare = prediction.action_timing("prepare_host", 100.0)
    prefetch = prediction.action_timing("prefetch_gpu", 100.0)

    assert prepare is not None
    assert prefetch is not None
    assert prepare.favorable_probability == pytest.approx(0.8)
    assert prefetch.favorable_probability == pytest.approx(0.2)
    assert prepare.support_level == "pooled"


def test_composer_applies_known_join_all_instead_of_learning_it() -> None:
    graph = RuntimeCausalContextGraph()
    sequence = 0

    def emit(kind: RuntimeEventKind, **kwargs) -> None:
        nonlocal sequence
        sequence += 1
        graph.apply(
            RuntimeEvent(
                event_id=f"e{sequence}",
                ts_ms=float(sequence),
                kind=kind,
                workflow_id="workflow",
                **kwargs,
            )
        )

    emit(RuntimeEventKind.WORKFLOW_START)
    emit(RuntimeEventKind.INVOCATION_CREATE, invocation_id="parent", context_id="p")
    emit(RuntimeEventKind.INVOCATION_CREATE, invocation_id="a", context_id="a")
    emit(RuntimeEventKind.INVOCATION_CREATE, invocation_id="b", context_id="b")
    emit(
        RuntimeEventKind.JOIN_CREATE,
        join_id="join",
        member_invocation_ids=("a", "b"),
    )
    emit(RuntimeEventKind.JOIN_WAIT, invocation_id="parent", join_id="join")
    scope = BeliefScopeBuilder().build(graph, ("parent", "a", "b"))
    model = FrontierBeliefModel(model_version="dev-v1")
    model.fit(
        [_row("d1", 10), _row("d2", 20), _row("d3", 15), _row("d4", 25)]
    )
    predictions = {
        invocation_id: model.predict(
            LocalFrontierFeatures(
                invocation_id=invocation_id,
                state=graph.invocations[invocation_id].state.value,
                agent_definition_id="worker",
                current_sequence_tokens=4096,
            )
        )
        for invocation_id in scope.invocation_ids
    }
    readset = PredictiveEvidenceReadSet(
        graph_version=graph.graph_version,
        page_revision=0,
        topology_revision=0,
        fairness_revision=0,
        admission_revision=0,
        transfer_epoch=0,
        obligation_revision=0,
        lease_revision=0,
        grace_revision=0,
        parser_frontier_revision=0,
        model_version="dev-v1",
    )
    belief = FrontierScenarioComposer(particle_count=16, top_k=4).compose(
        graph=graph,
        scope=scope,
        local_predictions=predictions,
        generated_ts_ms=10,
        evidence_read_set=readset,
    )
    parent = next(
        item
        for item in belief.scenarios[0].outcomes
        if item.invocation_id == "parent"
    )
    assert parent.dependency_mode.value == "join_all"
    assert parent.dependency_invocation_ids == ("a", "b")
    assert parent.join_id == "join"
    assert belief.other_probability_mass + sum(
        item.probability_mass for item in belief.scenarios
    ) == 1.0


def test_action_projected_reduction_preserves_mass_and_conservative_envelope() -> None:
    graph = RuntimeCausalContextGraph()
    graph.apply(
        RuntimeEvent(
            event_id="projected-1",
            ts_ms=1.0,
            kind=RuntimeEventKind.WORKFLOW_START,
            workflow_id="workflow",
        )
    )
    graph.apply(
        RuntimeEvent(
            event_id="projected-2",
            ts_ms=2.0,
            kind=RuntimeEventKind.INVOCATION_CREATE,
            workflow_id="workflow",
            invocation_id="parent",
            context_id="parent-context",
        )
    )
    graph.apply(
        RuntimeEvent(
            event_id="projected-3",
            ts_ms=3.0,
            kind=RuntimeEventKind.TOOL_START,
            workflow_id="workflow",
            invocation_id="parent",
            attributes={"tool_family": "shell"},
        )
    )
    scope = BeliefScopeBuilder().build(graph, ("parent",))
    prediction = LocalFrontierPrediction(
        invocation_id="parent",
        boundary_distribution={"unknown": 1.0},
        current_sequence_tokens=4096,
        remaining_decode_tokens=EmpiricalDistribution((0.0,), (1.0,), 8.0),
        remaining_external_wait=EmpiricalDistribution(
            (10.0, 100.0, 1000.0), (0.4, 0.4, 0.2), 8.0
        ),
        tool_terminal_distribution={"success": 1.0},
        prompt_growth_tokens=EmpiricalDistribution(
            (16.0, 128.0, 512.0), (0.4, 0.4, 0.2), 8.0
        ),
        next_output_tokens=EmpiricalDistribution((16.0,), (1.0,), 8.0),
        support_level="backoff",
        calibration_coverage=0.95,
        ood_reasons=("boundary_unavailable",),
    )
    readset = PredictiveEvidenceReadSet(
        graph_version=graph.graph_version,
        page_revision=0,
        topology_revision=0,
        fairness_revision=0,
        admission_revision=0,
        transfer_epoch=0,
        obligation_revision=0,
        lease_revision=0,
        grace_revision=0,
        parser_frontier_revision=0,
        model_version="projected-v1",
    )

    belief = FrontierScenarioComposer(particle_count=32, top_k=4).compose(
        graph=graph,
        scope=scope,
        local_predictions={"parent": prediction},
        generated_ts_ms=10.0,
        evidence_read_set=readset,
        projection=ScenarioProjection.PREFETCH,
        target_invocation_id="parent",
    )

    assert belief.other_probability_mass == 0.0
    assert belief.other_policy.finite_risk_bound
    assert sum(item.probability_mass for item in belief.scenarios) == 1.0
    assert 1 <= len(belief.scenarios) <= 4
    for scenario in belief.scenarios:
        medoid = scenario.outcomes[0]
        conservative = scenario.feasibility_outcomes[0]
        assert scenario.projection == ScenarioProjection.PREFETCH
        assert conservative.prompt_growth_tokens >= medoid.prompt_growth_tokens
        assert (
            conservative.external_segments[0].residual_delay_ms
            <= medoid.external_segments[0].residual_delay_ms
        )


def test_composer_reuses_unchanged_invocation_particles() -> None:
    graph = RuntimeCausalContextGraph()
    sequence = 0

    def emit(kind: RuntimeEventKind, **kwargs) -> None:
        nonlocal sequence
        sequence += 1
        graph.apply(
            RuntimeEvent(
                event_id=f"cache-{sequence}",
                ts_ms=float(sequence),
                kind=kind,
                workflow_id="workflow",
                **kwargs,
            )
        )

    emit(RuntimeEventKind.WORKFLOW_START)
    emit(
        RuntimeEventKind.INVOCATION_CREATE,
        invocation_id="parent",
        context_id="parent-context",
    )
    emit(
        RuntimeEventKind.INVOCATION_CREATE,
        invocation_id="child",
        context_id="child-context",
    )
    emit(
        RuntimeEventKind.CALL,
        invocation_id="parent",
        target_invocation_id="child",
    )
    scope = BeliefScopeBuilder().build(graph, ("parent", "child"))
    predictions = {
        "parent": _fixed_prediction("parent", 0),
        "child": _fixed_prediction("child", 25),
    }
    composer = FrontierScenarioComposer(particle_count=16, top_k=4)

    first = composer.sample_particles(
        graph=graph,
        scope=scope,
        local_predictions=predictions,
        seed=17,
    )
    assert composer.local_particle_cache_stats() == (0, 2, 2)

    repeated = composer.sample_particles(
        graph=graph,
        scope=scope,
        local_predictions=predictions,
        seed=17,
    )
    assert repeated == first
    assert composer.local_particle_cache_stats() == (2, 2, 2)

    graph.invocations["parent"].updated_ts_ms += 100.0
    timestamp_only = composer.sample_particles(
        graph=graph,
        scope=scope,
        local_predictions=predictions,
        seed=17,
    )
    assert timestamp_only == first
    assert composer.local_particle_cache_stats() == (4, 2, 2)

    changed_predictions = {
        **predictions,
        "child": _fixed_prediction("child", 50),
    }
    changed = composer.sample_particles(
        graph=graph,
        scope=scope,
        local_predictions=changed_predictions,
        seed=17,
    )
    assert composer.local_particle_cache_stats() == (5, 3, 3)
    before_by_particle = tuple(
        {item.invocation_id: item for item in outcomes} for outcomes in first
    )
    after_by_particle = tuple(
        {item.invocation_id: item for item in outcomes} for outcomes in changed
    )
    assert all(
        before["parent"] == after["parent"]
        for before, after in zip(
            before_by_particle, after_by_particle, strict=True
        )
    )
    assert any(
        before["child"] != after["child"]
        for before, after in zip(
            before_by_particle, after_by_particle, strict=True
        )
    )


def test_composer_applies_blocking_child_and_message_dependencies() -> None:
    graph = RuntimeCausalContextGraph()
    sequence = 0

    def emit(kind: RuntimeEventKind, **kwargs) -> None:
        nonlocal sequence
        sequence += 1
        graph.apply(
            RuntimeEvent(
                event_id=f"dependency-{sequence}",
                ts_ms=float(sequence),
                kind=kind,
                workflow_id="workflow",
                **kwargs,
            )
        )

    emit(RuntimeEventKind.WORKFLOW_START)
    emit(RuntimeEventKind.INVOCATION_CREATE, invocation_id="parent", context_id="p")
    emit(RuntimeEventKind.INVOCATION_CREATE, invocation_id="child", context_id="c")
    emit(
        RuntimeEventKind.CALL,
        invocation_id="parent",
        target_invocation_id="child",
    )
    scope = BeliefScopeBuilder().build(graph, ("parent", "child"))
    readset = PredictiveEvidenceReadSet(
        graph_version=graph.graph_version,
        page_revision=0,
        topology_revision=0,
        fairness_revision=0,
        admission_revision=0,
        transfer_epoch=0,
        obligation_revision=0,
        lease_revision=0,
        grace_revision=0,
        parser_frontier_revision=0,
        model_version="dependency-v1",
    )
    composer = FrontierScenarioComposer(particle_count=4, top_k=2)
    belief = composer.compose(
        graph=graph,
        scope=scope,
        local_predictions={
            "parent": _fixed_prediction("parent", 0),
            "child": _fixed_prediction("child", 25),
        },
        generated_ts_ms=10,
        evidence_read_set=readset,
    )
    parent = next(
        item
        for item in belief.scenarios[0].outcomes
        if item.invocation_id == "parent"
    )
    assert parent.dependency_mode.value == "join_all"
    assert parent.dependency_invocation_ids == ("child",)
    assert parent.remaining_decode_tokens == 0

    message_graph = RuntimeCausalContextGraph()
    graph = message_graph
    sequence = 0
    emit(RuntimeEventKind.WORKFLOW_START)
    emit(RuntimeEventKind.INVOCATION_CREATE, invocation_id="source", context_id="s")
    emit(RuntimeEventKind.INVOCATION_CREATE, invocation_id="producer", context_id="t")
    emit(
        RuntimeEventKind.HANDOFF,
        invocation_id="source",
        target_invocation_id="producer",
    )
    scope = BeliefScopeBuilder().build(graph, ("source", "producer"))
    readset = PredictiveEvidenceReadSet(
        graph_version=graph.graph_version,
        page_revision=0,
        topology_revision=0,
        fairness_revision=0,
        admission_revision=0,
        transfer_epoch=0,
        obligation_revision=0,
        lease_revision=0,
        grace_revision=0,
        parser_frontier_revision=0,
        model_version="dependency-v1",
    )
    belief = composer.compose(
        graph=graph,
        scope=scope,
        local_predictions={
            "source": _fixed_prediction("source", 0),
            "producer": _fixed_prediction("producer", 40),
        },
        generated_ts_ms=10,
        evidence_read_set=readset,
    )
    source = next(
        item
        for item in belief.scenarios[0].outcomes
        if item.invocation_id == "source"
    )
    assert source.dependency_mode.value == "producer"
    assert source.dependency_invocation_ids == ("producer",)
