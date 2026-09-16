from __future__ import annotations

from beliefkv.predictor.action_targets import (
    OperationalActionTargetContract,
    TransferAnchor,
    build_action_target_rows,
)
from beliefkv.predictor.structured_frontier import (
    EmpiricalDistribution,
    LocalFrontierPrediction,
    WaitBelief,
    WaitBeliefKind,
)


def _contract() -> OperationalActionTargetContract:
    return OperationalActionTargetContract(
        contract_id="test-contract",
        deployment_profile_id="test-profile",
        kv_bytes_per_token=10,
        commit_guard_ms=0.0,
        anchors=(
            TransferAnchor("d2h", 100, 100.0, 1, "test"),
            TransferAnchor("h2d", 100, 200.0, 1, "test"),
        ),
    )


def _decision(
    decision_id: str,
    workflow_id: str,
    timestamp_ms: float,
) -> dict:
    return {
        "decision_id": decision_id,
        "workflow_id": workflow_id,
        "split": "train",
        "timestamp_ms": timestamp_ms,
        "invocations": [
            {
                "invocation_id": "worker",
                "agent_definition_id": "worker",
                "state": "wait_tool",
                "current_sequence_tokens": 10,
                "boundary_history": ["tool"],
            }
        ],
        "labels": [
            {
                "invocation_id": "worker",
                "target_training_eligible": {"external_wait": True},
            }
        ],
    }


def test_completed_wait_builds_action_specific_operational_targets() -> None:
    rows, report = build_action_target_rows(
        [_decision("decision-1", "workflow-1", 100.0)],
        [
            {
                "workflow_id": "workflow-1",
                "invocation_id": "worker",
                "tool_call_id": "tool-1",
                "tool_name": "pytest",
                "tool_family": "shell",
                "backend_class": "subprocess",
                "start_ts_ms": 50.0,
                "terminal_ts_ms": 250.0,
                "observed_duration_ms": 200.0,
                "censored": False,
            }
        ],
        _contract(),
    )

    assert len(rows) == 1
    assert rows[0]["command_class"] == "pytest"
    assert rows[0]["actions"]["prepare_host"]["outcome"] is True
    assert rows[0]["actions"]["prefetch_gpu"]["outcome"] is True
    assert report["counts"]["prepare_host_known"] == 1
    assert report["online_eligibility"] is False


def test_right_censor_only_labels_horizons_proven_before_censor() -> None:
    rows, _ = build_action_target_rows(
        [_decision("decision-2", "workflow-2", 50.0)],
        [
            {
                "workflow_id": "workflow-2",
                "invocation_id": "worker",
                "tool_call_id": "tool-2",
                "tool_name": "pytest",
                "tool_family": "shell",
                "backend_class": "subprocess",
                "start_ts_ms": 0.0,
                "terminal_ts_ms": None,
                "observed_duration_ms": 200.0,
                "censored": True,
            }
        ],
        _contract(),
    )

    assert rows[0]["actions"]["prepare_host"]["outcome_known"] is True
    assert rows[0]["actions"]["prepare_host"]["outcome"] is True
    assert rows[0]["actions"]["prefetch_gpu"]["outcome_known"] is False
    assert rows[0]["actions"]["prefetch_gpu"]["outcome"] is None


def test_release_within_is_complement_of_release_after() -> None:
    belief = WaitBelief(
        kind=WaitBeliefKind.TOOL,
        residual_duration=EmpiricalDistribution(
            values=(50.0, 500.0),
            probability_mass=(0.25, 0.75),
            support=4.0,
        ),
        support_level="exact",
    )

    assert belief.release_after_probability(100.0) == 0.75
    assert belief.release_within_probability(100.0) == 0.25


def test_local_prediction_publishes_action_specific_timing_quality() -> None:
    belief = WaitBelief(
        kind=WaitBeliefKind.TOOL,
        residual_duration=EmpiricalDistribution(
            values=(50.0, 500.0),
            probability_mass=(0.25, 0.75),
            support=4.0,
        ),
        support_level="exact",
    )
    empty = EmpiricalDistribution.empty()
    prediction = LocalFrontierPrediction(
        invocation_id="worker",
        boundary_distribution={},
        current_sequence_tokens=4096,
        remaining_decode_tokens=empty,
        remaining_external_wait=belief.residual_duration,
        tool_terminal_distribution={"success": 1.0},
        prompt_growth_tokens=empty,
        next_output_tokens=empty,
        support_level="exact",
        calibration_coverage=0.9,
        wait_belief=belief,
        action_timing_calibration={
            "prepare_host": {
                "logit_scale": 1.0,
                "logit_offset": 0.0,
                "brier_skill": 0.2,
                "balanced_accuracy_at_0_5": 0.7,
                "episode_weight": 8.0,
            },
            "prefetch_gpu": {
                "logit_scale": 1.0,
                "logit_offset": 0.0,
                "brier_skill": -0.1,
                "balanced_accuracy_at_0_5": 0.45,
                "episode_weight": 8.0,
                "decision_threshold": 0.2,
                "precision_at_decision_threshold": 0.6,
                "recall_at_decision_threshold": 0.8,
            },
        },
    )

    prepare = prediction.action_timing("prepare_host", 100.0)
    prefetch = prediction.action_timing("prefetch_gpu", 100.0)

    assert prepare is not None and prepare.favorable_probability == 0.75
    assert prepare.informative
    assert prefetch is not None and prefetch.favorable_probability == 0.25
    assert prefetch.decision_threshold == 0.2
    assert prefetch.precision_at_decision_threshold == 0.6
    assert prefetch.recall_at_decision_threshold == 0.8
    assert not prefetch.informative
    assert (
        LocalFrontierPrediction.from_dict(prediction.to_dict())
        .action_timing("prepare_host", 100.0)
        == prepare
    )
