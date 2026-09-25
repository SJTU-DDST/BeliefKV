import json
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
