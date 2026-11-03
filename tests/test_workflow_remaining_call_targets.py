from copy import deepcopy

import numpy as np
import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES
from beliefkv.predictor.workflow_work_training import remaining_call_counts, remaining_call_targets
from scripts.audit_workflow_call_decomposition import per_call_costs
from scripts.train_workflow_work import fit
from tests.test_workflow_work_distribution_training import row


def complete_rows():
    result = []
    for snapshot, (attained, root, child) in enumerate(((0., 0, 0), (30., 0, 0), (100., 1, 0), (200., 1, 1))):
        features = dict.fromkeys(R5_FEATURES, 0.)
        features.update(
            completed_calls=root + child, root_completed_calls=root, child_completed_calls=child,
            output_tokens=attained, root_output_tokens=min(attained, 100.),
            child_output_tokens=max(0., attained - 100.),
        )
        result.append({
            "workflow": "original", "snapshot": snapshot, "features": list(features.values()),
            "total_work": 200., "attained_work": attained, "remaining_work": 200. - attained,
        })
    return result


def test_call_labels_preserve_partial_current_service_and_do_not_change_online_inputs():
    rows = complete_rows()
    saved = deepcopy(rows)
    counts, final = remaining_call_counts(rows)
    assert counts.tolist() == [[1., 1.], [1., 1.], [0., 1.], [0., 0.]]
    assert final == {"original": {"total": 2, "root": 1, "child": 1}}
    assert rows == saved
    changed = deepcopy(rows)
    changed[-1]["features"][R5_FEATURES.index("completed_calls")] = 3.
    changed[-1]["features"][R5_FEATURES.index("child_completed_calls")] = 2.
    updated, _final = remaining_call_counts(changed)
    assert updated[0].tolist() == [1., 2.]
    assert changed[:-1] == rows[:-1]


@pytest.mark.parametrize("change", ("tail", "missing", "duplicate", "reverse", "fractional", "conservation"))
def test_remaining_call_supervision_rejects_incomplete_or_invalid_original_snapshots(change):
    rows = complete_rows()
    if change == "tail":
        rows[-1].update(remaining_work=10., attained_work=190.)
    elif change == "missing":
        del rows[1]
    elif change == "duplicate":
        rows.append(deepcopy(rows[-1]))
    elif change == "reverse":
        rows[1]["features"][R5_FEATURES.index("completed_calls")] = 1.
        rows[1]["features"][R5_FEATURES.index("child_completed_calls")] = 1.
    elif change == "fractional":
        rows[1]["features"][R5_FEATURES.index("completed_calls")] = .5
    else:
        rows[1]["remaining_work"] = 10.
    with pytest.raises(ValueError):
        remaining_call_counts(rows)


def test_call_target_normalization_uses_supplied_training_mass_only():
    rows = complete_rows()
    weights = np.asarray((.1, .2, .3, .4))
    target, normalization = remaining_call_targets(rows, weights)
    assert np.average(target, axis=0, weights=weights) == pytest.approx((0., 0.), abs=1e-12)
    assert np.asarray(normalization["log_scale"]).min() >= .25
    with pytest.raises(ValueError, match="normalization"):
        remaining_call_targets(rows, (-1., 0., 0., 0.))
    assert per_call_costs(rows) == {"total": 100., "root": 100., "child": 100.}


def test_optional_call_supervision_preserves_default_training_and_exports_no_online_head(monkeypatch):
    from beliefkv.predictor import workflow_work_training
    from beliefkv.predictor.workflow_work import R5_SCHEMA

    def tasks(prefix):
        return [
            {**row(prefix + str(index)), **state, "workflow": prefix + str(index)}
            for index in range(4) for state in complete_rows()
        ]

    training, calibration = tasks("train-"), tasks("cal-")
    settings = {
        "epochs": 2, "split_mode": "workflow", "feature_schema": R5_SCHEMA,
        "hidden_width": 4, "vocabulary_size": 4, "work_thresholds": (80., 150.),
    }
    raw = fit(training, calibration, **settings)
    with monkeypatch.context() as context:
        def forbidden(*args):
            raise AssertionError("disabled auxiliary labels must not be read")

        context.setattr(workflow_work_training, "remaining_call_targets", forbidden)
        assert raw == fit(training, calibration, remaining_calls_loss_weight=0., **settings)
    supervised = fit(training, calibration, remaining_calls_loss_weight=.1, **settings)
    contract = supervised["metadata"]["remaining_call_supervision"]
    assert contract["offline_complete_execution_labels_only"]
    assert not contract["auxiliary_head_exported"] and contract["online_model_dimensions_unchanged"]
    assert supervised["features"] == raw["features"]
    assert np.asarray(supervised["dynamic_weight"]).shape == np.asarray(raw["dynamic_weight"]).shape
    assert set(supervised) == set(raw)
    with pytest.raises(ValueError, match="regularization"):
        fit(training, calibration, remaining_calls_loss_weight=-1., **settings)
