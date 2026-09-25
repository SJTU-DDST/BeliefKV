from __future__ import annotations

import json
import sys

import pytest

from beliefkv.predictor.action_targets import (
    OperationalActionTargetContract,
    TransferAnchor,
    build_action_target_rows,
    load_action_target_rows,
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
    assert rows[0]["actions"]["prepare_host"]["observed_reward_ms"] is None
    assert rows[0]["actions"]["prefetch_gpu"]["transfer_evidence"] == (
        "estimated_byte_scaled_anchor"
    )
    assert rows[0]["observed_physical_execution"] is None


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


def _frontier(
    decision_id: str,
    timestamp_ms: float,
    state: str,
    *,
    invocation_id: str = "parent",
    trigger_kind: str = "tool_end",
    context_id: str = "ctx",
    request_id: str | None = None,
    state_elapsed_ms: float = 0.0,
    join_eligible: bool = False,
    resources: dict | None = None,
) -> dict:
    return {
        "decision_id": decision_id,
        "workflow_id": "workflow",
        "timestamp_ms": timestamp_ms,
        "split": "train",
        "trigger_kind": trigger_kind,
        "trigger_id": f"event-{decision_id}",
        "observed_resources": resources or {},
        "invocations": [{
            "invocation_id": invocation_id,
            "context_id": context_id,
            "state": state,
            "request_id": request_id,
            "current_sequence_tokens": 10,
            "state_elapsed_ms": state_elapsed_ms,
        }],
        "labels": [{
            "invocation_id": invocation_id,
            "target_training_eligible": {"join_wait": join_eligible},
        }],
    }


def test_join_parent_window_requires_eligible_closed_members() -> None:
    decision = _frontier("join", 100.0, "wait_join", join_eligible=True)
    join = {
        "workflow_id": "workflow",
        "invocation_id": "parent",
        "reentry_kind": "join",
        "reentry_id": "join:1",
        "wait_start_ts_ms": 80.0,
        "reentry_ts_ms": 350.0,
        "terminal_status": "satisfied",
        "training_eligible": True,
        "member_invocation_ids": ["child-a", "child-b"],
        "member_outcomes": [
            {"invocation_id": "child-a", "return_ts_ms": 200.0},
            {"invocation_id": "child-b", "return_ts_ms": 340.0},
        ],
    }
    rows, report = build_action_target_rows([decision], [], _contract(), [join])
    observation = rows[0]
    assert observation["row_type"] == "reactive_action_observation"
    assert observation["candidate_action"] == "join_parent_prefetch_gpu"
    assert observation["timing_training_eligible"] is True
    assert observation["timing"]["observed_residual_ms"] == 250.0
    assert observation["timing"]["estimated_h2d_p95_ms"] == 200.0
    assert observation["timing"]["estimated_latest_start_ts_ms"] == 150.0
    assert observation["observed_reward_ms"] is None
    assert observation["physical_eligibility"] is None
    assert report["counts"]["join_parent_prefetch_gpu_timing_eligible"] == 1
    assert report["counts"].get("rows", 0) == 0
    assert report["counts"]["reactive_observation_rows"] == 1

    join["member_outcomes"][1]["return_ts_ms"] = None
    join["training_eligible"] = False
    rows, _ = build_action_target_rows([decision], [], _contract(), [join])
    assert rows[0]["timing"]["observed_boundary_ts_ms"] == 350.0
    assert rows[0]["timing_training_eligible"] is False


def test_child_resume_uses_observed_return_transition_not_future_tool_wait() -> None:
    before = _frontier("child-wait", 100.0, "wait_child")
    after = _frontier("child-return", 280.0, "ready", trigger_kind="return")
    rows, _ = build_action_target_rows([before, after], [], _contract())
    resume = next(row for row in rows if row["candidate_action"] == "child_resume_prefetch_gpu")
    assert resume["timing_training_eligible"] is True
    assert resume["timing"]["observed_boundary_ts_ms"] == 280.0
    assert resume["timing"]["observed_source"] == "observed_frontier_resume_on_return"

    after["trigger_kind"] = "message"
    rows, _ = build_action_target_rows([before, after], [], _contract())
    resume = next(row for row in rows if row["candidate_action"] == "child_resume_prefetch_gpu")
    assert resume["timing_training_eligible"] is False
    assert resume["timing"]["observed_boundary_ts_ms"] is None

    after["trigger_kind"] = "return"
    after["invocations"][0]["state_elapsed_ms"] = None
    rows, _ = build_action_target_rows([before, after], [], _contract())
    resume = next(row for row in rows if row["candidate_action"] == "child_resume_prefetch_gpu")
    assert resume["timing_training_eligible"] is False


def test_child_resume_accepts_eligible_explicit_reentry() -> None:
    before = _frontier("child-wait", 100.0, "wait_child")
    after = _frontier("reactivate", 260.0, "ready", trigger_kind="reactivate")
    reentry = {
        "workflow_id": "workflow",
        "invocation_id": "parent",
        "reentry_kind": "reactivate",
        "reentry_id": "resume-1",
        "reentry_ts_ms": 260.0,
        "training_eligible": True,
    }
    rows, _ = build_action_target_rows(
        [before, after], [], _contract(), [reentry]
    )
    resume = next(row for row in rows if row["candidate_action"] == "child_resume_prefetch_gpu")
    assert resume["timing_training_eligible"] is True
    assert resume["timing"]["evidence_id"] == "resume-1"
    assert resume["timing"]["observed_source"] == "observed_reactivate_reentry"

    reentry["training_eligible"] = False
    rows, _ = build_action_target_rows([before, after], [], _contract(), [reentry])
    resume = next(row for row in rows if row["candidate_action"] == "child_resume_prefetch_gpu")
    assert resume["timing_training_eligible"] is False
    assert resume["timing"]["observed_boundary_ts_ms"] == 260.0


def test_admission_window_and_pcie_resource_evidence_do_not_claim_kv_release() -> None:
    resource = {
        "availability": "observed_resource_snapshot",
        "snapshot_ts_ms": 90.0,
        "hbm_used_bytes": 700,
        "hbm_capacity_bytes": 1000,
        "host_used_bytes": 100,
        "host_capacity_bytes": 200,
    }
    ready = _frontier("ready", 100.0, "ready", resources=resource)
    ready["trigger_kind"] = "transfer_completion"
    ready["trigger_id"] = "transfer:copy-1"
    running = _frontier(
        "admitted", 270.0, "running_llm",
        trigger_kind="llm_submit", request_id="request-1",
    )
    transfer = {
        "command_id": "copy-1",
        "command_kind": "restore_context",
        "direction": "h2d",
        "status": "completed",
        "complete_ts_ms": 100.0,
        "actual_bytes": 80,
        "transfer_stream_elapsed_ms": 3.0,
        "training_eligible_service_curve": True,
    }
    rows, _ = build_action_target_rows(
        [ready, running], [], _contract(), [], [transfer]
    )
    admission = rows[0]
    assert admission["candidate_action"] == "admission_prefetch_gpu"
    assert admission["timing_training_eligible"] is True
    assert admission["timing"]["observed_source"] == "observed_llm_submit"
    assert admission["timing"]["estimated_latest_start_ts_ms"] == 70.0
    evidence = admission["kv_evidence"]
    assert evidence["observed_available_hbm_bytes"] == 300
    assert evidence["observed_available_host_bytes"] == 100
    assert evidence["coincident_completed_transfer"]["actual_bytes"] == 80
    assert evidence["observed_released_kv_bytes"] is None
    assert evidence["observed_available_kv_bytes"] is None
    assert admission["actions"] == {}

    running["invocations"][0]["context_id"] = "another-context"
    rows, _ = build_action_target_rows([ready, running], [], _contract())
    assert rows[0]["timing_training_eligible"] is False

    running["invocations"][0]["context_id"] = "ctx"
    running["invocations"][0]["state_elapsed_ms"] = None
    rows, _ = build_action_target_rows([ready, running], [], _contract())
    assert rows[0]["timing"]["observed_boundary_ts_ms"] is None


def test_export_cli_reads_optional_evidence_and_v4_loader_keeps_tool_labels(
    tmp_path, monkeypatch,
) -> None:
    from scripts.export_p6_action_targets import main

    def write(name: str, values: list[dict]) -> None:
        (tmp_path / name).write_text(
            "".join(json.dumps(value) + "\n" for value in values),
            encoding="utf-8",
        )

    write("frontier_decision_points.jsonl", [
        _decision("tool", "workflow", 100.0),
        _frontier("join", 100.0, "wait_join", join_eligible=True),
    ])
    write("external_waits.jsonl", [{
        "workflow_id": "workflow",
        "invocation_id": "worker",
        "tool_call_id": "tool-1",
        "start_ts_ms": 50.0,
        "terminal_ts_ms": 250.0,
    }])
    write("reentries.jsonl", [{
        "workflow_id": "workflow",
        "invocation_id": "parent",
        "reentry_kind": "join",
        "reentry_id": "join:1",
        "wait_start_ts_ms": 80.0,
        "reentry_ts_ms": 350.0,
        "terminal_status": "satisfied",
        "training_eligible": True,
        "member_invocation_ids": ["child"],
        "member_outcomes": [{"return_ts_ms": 340.0}],
    }])
    write("pcie_operations.jsonl", [])
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps({
        "schema_version": 4,
        "contract_id": "test-contract",
        "deployment_profile_id": "test-profile",
        "kv_bytes_per_token": 10,
        "commit_guard_ms": 0,
        "anchors": [item.__dict__ for item in _contract().anchors],
    }), encoding="utf-8")
    output, report_path = tmp_path / "targets.jsonl", tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "export_p6_action_targets", "--dataset-dir", str(tmp_path),
        "--contract", str(contract_path), "--output", str(output),
        "--report", str(report_path),
    ])
    assert main() == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    tool_rows = load_action_target_rows([output])
    assert len(tool_rows) == 1
    assert tool_rows[0]["actions"]["prepare_host"]["outcome_known"]
    assert json.loads(report_path.read_text(encoding="utf-8"))["counts"][
        "join_parent_prefetch_gpu_timing_eligible"
    ] == 1


def test_native_observation_only_does_not_apply_legacy_bf16_kv_anchor(
    tmp_path, monkeypatch,
) -> None:
    from scripts.export_p6_action_targets import main

    (tmp_path / "frontier_decision_points.jsonl").write_text(
        json.dumps(_frontier("join", 100.0, "wait_join", join_eligible=True)) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "reentries.jsonl").write_text(
        json.dumps({
            "workflow_id": "workflow", "invocation_id": "parent",
            "reentry_kind": "join", "reentry_id": "join:1",
            "wait_start_ts_ms": 80.0, "reentry_ts_ms": 350.0,
            "terminal_status": "satisfied", "training_eligible": True,
            "member_invocation_ids": ["child"],
            "member_outcomes": [{"return_ts_ms": 340.0}],
        }) + "\n",
        encoding="utf-8",
    )
    output, report = tmp_path / "targets.jsonl", tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "export_p6_action_targets", "--dataset-dir", str(tmp_path),
        "--native-reactive-only", "--output", str(output), "--report", str(report),
    ])
    assert main() == 0
    row = json.loads(output.read_text(encoding="utf-8").strip())
    assert row["timing"]["observed_residual_ms"] == 250.0
    assert row["timing"]["estimated_h2d_p95_ms"] is None
    assert row["timing"]["estimated_latest_start_ts_ms"] is None
    assert row["kv_evidence"]["estimated_context_kv_bytes"] is None
    assert row["observed_reward_ms"] is None
    assert not load_action_target_rows([output])
    assert json.loads(report.read_text(encoding="utf-8"))["online_eligibility"] is False


def test_join_reentry_eligibility_does_not_require_next_boundary_label() -> None:
    from beliefkv.predictor.action_targets import build_native_reactive_observations

    decision = _frontier("join", 100.0, "wait_join", join_eligible=False)
    reentry = {
        "workflow_id": "workflow", "invocation_id": "parent",
        "reentry_kind": "join", "reentry_id": "join:1",
        "wait_start_ts_ms": 80.0, "reentry_ts_ms": 350.0,
        "terminal_status": "satisfied", "training_eligible": True,
        "member_invocation_ids": ["child"],
        "member_outcomes": [{"return_ts_ms": 340.0}],
    }
    rows, report = build_native_reactive_observations([decision], [reentry], [])
    assert rows[0]["timing_training_eligible"] is True
    assert rows[0]["timing_episode_id"] == "join:1"
    assert rows[0]["physical_eligibility"] is None
    assert report["eligible_distinct_timing_episodes"] == {
        "join_parent_prefetch_gpu": 1
    }


def test_export_rejects_qwen35_with_legacy_bf16_anchor_before_loading_data(
    tmp_path, monkeypatch, capsys,
) -> None:
    from scripts.export_p6_action_targets import main

    (tmp_path / "dataset_manifest.json").write_text(json.dumps({
        "source": {"runtime_environment_contract": {"server_identity": {
            "served_model_name": "Qwen3.5-35B-A3B",
        }}}
    }), encoding="utf-8")
    contract_path = tmp_path / "legacy.json"
    contract_path.write_text(json.dumps({
        "schema_version": 4,
        "contract_id": "legacy",
        "deployment_profile_id": "h200_bf16_perf_v1",
        "kv_bytes_per_token": 98304,
        "commit_guard_ms": 25,
        "anchors": [item.__dict__ for item in _contract().anchors],
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "export_p6_action_targets", "--dataset-dir", str(tmp_path),
        "--contract", str(contract_path),
        "--output", str(tmp_path / "targets.jsonl"),
        "--report", str(tmp_path / "report.json"),
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "legacy BF16" in capsys.readouterr().err


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
