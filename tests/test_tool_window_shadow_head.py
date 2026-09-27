import json

import numpy as np
import pytest

from beliefkv.predictor.tool_window_shadow import (
    FrozenToolWindowShadow, feature_row,
)
from scripts.pilot_cold_child_tool_long import _shape_matrix
from scripts.pilot_tool_return_window_100ms import evaluate, export_frozen_shadow


def _row(project, index):
    return {
        "project": project, "workflow": f"{project}-{index % 6}",
        "invocation": f"{project}-{index % 6}-child",
        "tool_call_id": f"{project}-{index}",
        "input_sha256": f"hash-{project}-{index}",
        "start_ts_ms": index * 2000.,
        "duration_ms": float(800 if index % 3 else 200),
        "status": "success",
        "shape": "python_inline_simple",
        "input_chars": 250,
        "other_workflow_2s_peers": 1,
        "project_class_completed_support": 4,
        "project_class_duration_median_ms": 700,
        "project_input_neighbor_duration_ms": 850,
        "project_input_neighbor_support": 6,
    }


def test_runtime_columns_match_offline_shape_matrix():
    row = _row("django", 1)
    vocabulary = {"python_inline_simple": 0}
    attrs = {
        "observed_command_shape": row["shape"],
        "input_chars": row["input_chars"],
        "project_class_inflight_other_workflow_2s_peers": (
            row["other_workflow_2s_peers"]
        ),
        "project_class_completed_support": (
            row["project_class_completed_support"]
        ),
        "project_class_duration_median_ms": (
            row["project_class_duration_median_ms"]
        ),
        "project_input_neighbor_duration_ms": (
            row["project_input_neighbor_duration_ms"]
        ),
        "project_input_neighbor_support": (
            row["project_input_neighbor_support"]
        ),
    }
    np.testing.assert_array_equal(
        feature_row(attrs, vocabulary),
        _shape_matrix(
            [row], vocabulary, include_live_peers=True,
            include_duration_priors=True,
        ),
    )


def test_frozen_artifact_loads_and_rejects_model_mutation(tmp_path):
    train = [
        _row(project, index)
        for project in ("django", "pydata", "pytest-dev")
        for index in range(54)
    ]
    training = evaluate(train)
    training["frozen_workflows"] = 18
    # Synthetic folds do not need to meet the production evidence gate.
    training["arms"]["shape_size_peers_history"][
        "exploratory_training_threshold"
    ] = .8
    source = tmp_path / "train_manifest.json"
    source.write_text(json.dumps({"instance_ids": ["django__one"]}))
    artifact = export_frozen_shadow(
        train, training, train_manifest=source,
        directory=tmp_path / "frozen",
    )
    head = FrozenToolWindowShadow(artifact)
    estimate = head.estimate({
        "observed_command_shape": "python_inline_simple",
        "input_chars": 250,
        "project_class_inflight_other_workflow_2s_peers": 1,
        "project_class_completed_support": 4,
        "project_class_duration_median_ms": 700,
        "project_input_neighbor_duration_ms": 850,
        "project_input_neighbor_support": 6,
    })
    assert 0 <= estimate.probability <= 1
    assert estimate.total_eta_ms >= 600
    assert estimate.global_eta_ms >= 600
    with pytest.raises(FileExistsError):
        export_frozen_shadow(
            train, training, train_manifest=source,
            directory=tmp_path / "frozen",
        )
    model = artifact.parent / "classifier.txt"
    model.write_bytes(model.read_bytes() + b"\nchanged\n")
    with pytest.raises(ValueError, match="checksum"):
        FrozenToolWindowShadow(artifact)
