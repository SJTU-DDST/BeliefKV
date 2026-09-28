import json
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.calibrate_frontier_belief import main


class _Model:
    model_version = "train-v1"

    def calibrate(self, rows, *, target_coverage, action_targets):
        assert len(rows) == 2
        assert list(action_targets) == []
        return {
            "observation_counts": {
                "remaining_to_return_ms": 12,
                "next_output_tokens": 16,
            }
        }

    def save(self, path, *, metadata):
        path.write_text(json.dumps(metadata), encoding="utf-8")


def _inputs(tmp_path: Path, *, pcie: int = 1):
    model = tmp_path / "train.json"
    model.write_text(json.dumps({
        "metadata": {
            "fit_projects": ["django/django"],
            "runtime_environment_contract_digests": ["runtime-match"],
        }
    }), encoding="utf-8")
    rows = [
        {"project": "astropy/astropy"},
        {"project": "sphinx-doc/sphinx"},
    ]
    manifest = {
        "source": {
            "collection_contract": {
                "plan_id": "qwen35-native-reactive-v0520-v1-calibration-66root",
            },
            "runtime_environment_contract": {},
        },
        "training_readiness": {
            "join_reentry_eligible_count": 12,
            "remaining_decode_demand_eligible_request_count": 16,
            "pcie_service_eligible_count": pcie,
        },
    }
    return model, rows, manifest


def test_native_heads_only_calibration_never_grants_action_eligibility(
    tmp_path: Path,
) -> None:
    model, rows, manifest = _inputs(tmp_path)
    result = tmp_path / "calibrated.json"
    with (
        patch(
            "scripts.calibrate_frontier_belief.FrontierBeliefModel.from_dict",
            return_value=_Model(),
        ),
        patch(
            "scripts.calibrate_frontier_belief.load_evaluation_rows",
            return_value=(rows, [manifest]),
        ),
        patch(
            "scripts.calibrate_frontier_belief.runtime_environment_digest",
            return_value="runtime-match",
        ),
    ):
        assert main([
            "--model", str(model), "--dataset-dir", str(tmp_path / "calibration"),
            "--native-heads-only", "--output", str(result),
        ]) == 0
    metadata = json.loads(result.read_text(encoding="utf-8"))
    assert metadata["calibration_status"] == "calibrated_native_heads_only"
    assert metadata["action_calibration_status"] == "unavailable_no_action_targets"
    assert metadata["online_eligible"] is False
    assert metadata["predictive_action_eligible"] is False


def test_native_heads_only_rejects_unverified_pcie_labels(tmp_path: Path) -> None:
    model, rows, manifest = _inputs(tmp_path, pcie=0)
    result = tmp_path / "calibrated.json"
    with (
        patch(
            "scripts.calibrate_frontier_belief.FrontierBeliefModel.from_dict",
            return_value=_Model(),
        ),
        patch(
            "scripts.calibrate_frontier_belief.load_evaluation_rows",
            return_value=(rows, [manifest]),
        ),
        patch(
            "scripts.calibrate_frontier_belief.runtime_environment_digest",
            return_value="runtime-match",
        ),
    ):
        with pytest.raises(SystemExit, match="required measured labels"):
            main([
                "--model", str(model), "--dataset-dir", str(tmp_path / "calibration"),
                "--native-heads-only", "--output", str(result),
            ])
    assert not result.exists()


def test_native_event_targets_calibrate_on_disjoint_project_trace(
    tmp_path: Path,
) -> None:
    model, _, manifest = _inputs(tmp_path)
    raw_model = json.loads(model.read_text())
    raw_model["metadata"].update({
        "action_target_semantics": "observed_tool_release_horizons_only",
        "native_event_horizons_ms": [
            50.0, 100.0, 250.0, 500.0, 1000.0, 2000.0, 5000.0,
        ],
    })
    model.write_text(json.dumps(raw_model), encoding="utf-8")
    directory = tmp_path / "calibration"
    directory.mkdir()
    waits = directory / "external_waits.jsonl"
    rows = []
    with waits.open("w", encoding="utf-8") as stream:
        for index, project in enumerate(("astropy/astropy", "sphinx-doc/sphinx")):
            workflow = f"workflow-{index}"
            rows.append({
                "project": project, "split": "calibration",
                "decision_id": f"decision-{index}", "workflow_id": workflow,
                "timestamp_ms": 100.0,
                "invocations": [{
                    "invocation_id": "worker", "state": "wait_tool",
                    "agent_definition_id": "worker",
                }],
                "labels": [{
                    "invocation_id": "worker",
                    "target_training_eligible": {"external_wait": True},
                }],
            })
            stream.write(json.dumps({
                "workflow_id": workflow, "invocation_id": "worker",
                "tool_call_id": f"tool-{index}", "start_ts_ms": 20.0,
                "terminal_ts_ms": 150.0 + index * 200.0,
            }) + "\n")
    manifest["tables"] = {"external_waits": {
        "path": waits.name,
        "sha256": hashlib.sha256(waits.read_bytes()).hexdigest(),
    }}

    class _EventModel(_Model):
        def calibrate(self, values, *, target_coverage, action_targets):
            assert len(values) == 2
            assert len(action_targets) == 2
            assert action_targets[0]["observed_reward_ms"] is None
            return {"observation_counts": {
                "remaining_to_return_ms": 12, "next_output_tokens": 16,
                "native_event_timing": 14,
            }}

    result = tmp_path / "calibrated.json"
    with (
        patch(
            "scripts.calibrate_frontier_belief.FrontierBeliefModel.from_dict",
            return_value=_EventModel(),
        ),
        patch(
            "scripts.calibrate_frontier_belief.load_evaluation_rows",
            return_value=(rows, [manifest]),
        ),
        patch(
            "scripts.calibrate_frontier_belief.runtime_environment_digest",
            return_value="runtime-match",
        ),
    ):
        assert main([
            "--model", str(model), "--dataset-dir", str(directory),
            "--native-heads-only", "--output", str(result),
        ]) == 0
    metadata = json.loads(result.read_text())
    assert metadata["calibration_action_target_count"] == 2
    assert metadata["action_calibration_status"] == "event_timing_only"
    assert metadata["predictive_action_eligible"] is False


def test_event_timing_rows_cannot_claim_physical_action_calibration(
    tmp_path: Path,
) -> None:
    model, _, _ = _inputs(tmp_path)
    targets = tmp_path / "event_targets.jsonl"
    targets.write_text(json.dumps({
        "schema_version": 4,
        "row_type": "native_event_timing_target",
        "decision_id": "decision", "invocation_id": "child",
    }) + "\n", encoding="utf-8")
    with patch(
        "scripts.calibrate_frontier_belief.FrontierBeliefModel.from_dict",
        return_value=_Model(),
    ):
        with pytest.raises(SystemExit, match="cannot satisfy physical action"):
            main([
                "--model", str(model), "--dataset-dir", str(tmp_path),
                "--action-target", str(targets),
                "--action-target-report", str(tmp_path / "report.json"),
                "--coverage-report", str(tmp_path / "coverage.json"),
                "--output", str(tmp_path / "physical.json"),
            ])
