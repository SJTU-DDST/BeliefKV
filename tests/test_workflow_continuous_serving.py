from copy import deepcopy
import math
from types import SimpleNamespace

import pytest

from beliefkv.policy.workflow_combination import prediction_weight
from beliefkv.predictor.workflow_continuous_work import (
    ContinuousWorkReadout, serving_contract,
)
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.runtime.workflow_work_worker import (
    ContinuousDistributionWorkPrediction, WorkflowWorkWorker,
)
from tests.test_workflow_continuous_work import model, observed, training_rows
from tests.test_workflow_work_distribution import root, start


def continuous_model():
    raw = deepcopy(model().raw)
    raw["continuous_work_readout"] = serving_contract(model(), training_rows())
    return WorkflowWorkModel(raw)


def test_single_pass_preserves_probabilities_and_continuous_resolution(monkeypatch):
    serving = continuous_model()
    old = model()
    readout = ContinuousWorkReadout(old, serving.continuous_readout.contract)
    projection = serving.task_projection("ab")
    calls = []
    score = serving.conditional_tree.tree.score
    monkeypatch.setattr(
        serving.conditional_tree.tree, "score",
        lambda values: (calls.append(1), score(values))[1],
    )
    results = []
    for value in (0., 2., 4.):
        state = observed(value)
        probability, continuous = serving.predict(projection, state, with_work=True)
        expected = old.predict(old.task_projection("ab"), state)
        assert probability == expected
        assert continuous == readout.predict(
            old.task_projection("ab"), state, probabilities=expected,
            support="calibrated_distribution",
        )
        results.append(continuous)
    assert len(calls) == 2
    assert results[1]["remaining_work_point"] == pytest.approx(24000.)
    assert results[2]["remaining_work_point"] == pytest.approx(230000.)
    assert results[1]["distribution_effective_weight"] > results[2]["distribution_effective_weight"]


@pytest.mark.parametrize("change", ("tree", "initial_prior", "reference", "missing_binding"))
def test_continuous_contract_rejects_changed_or_invalid_forecasts(change):
    raw = deepcopy(continuous_model().raw)
    if change == "tree":
        raw["conditional_progress_tree_head"]["calibration_offset"] += 1.
    elif change == "initial_prior":
        raw["continuous_work_readout"]["initial_class_expected_utility"] = [0., .5, 1.]
    elif change == "reference":
        raw["continuous_work_readout"]["reference_work"] = 0.
    else:
        raw["continuous_work_readout"].pop("forecast_sha256")
    with pytest.raises(ValueError):
        WorkflowWorkModel(raw)


def test_legacy_artifacts_require_explicit_opt_in():
    legacy = model()
    assert legacy.continuous_readout is None
    with pytest.raises(ValueError, match="explicit continuous-work"):
        legacy.predict(legacy.task_projection("ab"), observed(2.), with_work=True)


def test_worker_publishes_and_invalidates_continuous_cache(monkeypatch):
    from beliefkv.runtime import workflow_work_worker as module

    clock = [100.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    serving = continuous_model()
    worker = WorkflowWorkWorker("", "", model=serving, autostart=False)
    try:
        worker.submit("wf", "event", start())
        worker.submit("wf", "event", root())
        worker.submit("wf", "started", "r", "root", "c", (1, 2))
        worker.submit("wf", "generated", "r", 2)
        worker.process_pending()
        first = worker.prediction("wf")
        assert isinstance(first, ContinuousDistributionWorkPrediction)
        assert first.work_source == "calibrated_continuous_tree_log_work"
        assert first.log1p_remaining_work == pytest.approx(math.log1p(24000.))
        assert prediction_weight(first) == first.effective_weight < .25
        assert worker.status()["admission_work_weight"] == "expected_training_referenced_continuous_work_utility"
        worker.submit("wf", "generated", "r", 4)
        clock[0] += 1.
        worker.process_pending()
        assert prediction_weight(worker.prediction("wf")) < prediction_weight(first)
        clock[0] += 3.1
        assert worker.prediction("wf") is None
    finally:
        worker.close()


@pytest.mark.parametrize("invalid", (math.nan, math.inf, -.1, 1.1, True))
def test_admission_refuses_invalid_continuous_utility(invalid):
    with pytest.raises(ValueError):
        prediction_weight(SimpleNamespace(effective_weight=invalid))


def test_continuous_weight_reaches_real_combination_admission(monkeypatch, tmp_path):
    from tests import test_workflow_combination_runtime as combination
    from tests.test_workflow_work_distribution_runtime import distribution_configuration
    import time

    values = distribution_configuration(tmp_path)
    monkeypatch.setattr(combination, "configured", lambda _path: values)
    runtime, admission, _environment = combination.runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = SimpleNamespace(record=records.append)
    try:
        for name in ("a", "b", "c"):
            admission._work._predictions[name] = ContinuousDistributionWorkPrediction(
                (0., 0., 1.), 2, 1, time.monotonic(), "model", "calibrated_distribution",
                time.monotonic() + 3., 1, .9 if name == "a" else .05,
                math.log1p(1000. if name == "a" else 100000.), .5, 10000., "test_continuous",
            )
        admission.tick()
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"a"}
        choice = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert {row["identity"]: row["weight"] for row in choice["candidates"]} == {
            "a": .9, "b": .05, "c": .05,
        }
        assert choice["predictions"]["a"]["log1p_remaining_work"] == math.log1p(1000.)
    finally:
        runtime._opportunity_writer = None
        runtime.close()
