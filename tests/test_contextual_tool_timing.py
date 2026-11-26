import math
import random

import pytest

from beliefkv.predictor.contextual_tool_timing import (
    COMMAND_FEATURE_COUNT, INTERVAL_STARTS, DelayedBrierWeights,
    ToolCallHistory, ToolReturnDistribution, command_work_features, mixture_quantile,
)


def test_conditional_survival_and_quantiles_cross_interval_boundaries():
    distribution = ToolReturnDistribution((2., 4., *(1.,) * (len(INTERVAL_STARTS) - 2)))
    assert distribution.survival(.02, .03) == pytest.approx(math.exp(-(.005 * 2 + .025 * 4)))
    for age in (0., .025, .8, 192.):
        for probability in (.1, .5, .99):
            horizon = distribution.quantile(age, probability)
            assert distribution.survival(age, horizon) == pytest.approx(1. - probability)
    assert distribution.survival(.02, .03) * distribution.survival(.05, .4) == pytest.approx(
        distribution.survival(.02, .43)
    )


def test_same_command_family_has_different_work_vectors_without_path_identity():
    small = command_work_features("pytest -q tests/a.py::test_one")
    renamed = command_work_features("pytest -q tests/b.py::test_two")
    large = command_work_features("pytest -q tests/a.py tests/b.py tests/c.py")
    assert len(small) == COMMAND_FEATURE_COUNT
    assert small == renamed
    assert small != large
    assert all(math.isnan(value) for value in command_work_features("'unterminated"))


def test_history_excludes_pending_and_cancelled_calls_and_separates_inputs():
    history = ToolCallHistory()
    shape = ("docker", "test_suite")
    first = history.start("a", shape, "small")
    second = history.start("b", shape, "large")
    assert first[0] == 0
    assert second[0] == pytest.approx(math.log(2))
    assert math.isnan(second[3])
    history.end("a", shape, "small", .5, returned=True)
    history.end("b", shape, "large", 5., returned=False)
    exact = history.start("c", shape, "small")
    related = history.start("d", shape, "large")
    assert exact[3] == related[3] == pytest.approx(math.log1p(500))
    assert exact[5] == pytest.approx(math.log1p(500))
    assert math.isnan(related[5])


def test_delayed_weights_observe_only_resolved_labels():
    estimator = DelayedBrierWeights(2)
    forecasts = ((.4, .2), (.9, .8))
    assert estimator.forecast("first", forecasts) == (.5, .5)
    assert estimator.forecast("concurrent", forecasts) == (.5, .5)
    assert estimator.lanes == 2
    estimator.resolve("first", (1, 1))
    assert estimator.forecast("next", forecasts)[1] > .5
    estimator.resolve("concurrent", (None, None))
    assert estimator.forecast("censored_lane", forecasts) == (.5, .5)


def test_pathwise_brier_regret_under_reordered_feedback():
    randomizer = random.Random(21)
    estimator = DelayedBrierWeights(3)
    losses = [0.] * 3
    aggregate_loss = 0.
    waves = 8
    for wave in range(75):
        pending = []
        for batch in range(waves):
            identity = (wave, batch)
            probabilities = tuple(
                tuple(sorted((randomizer.random(), randomizer.random()), reverse=True))
                for _ in losses
            )
            weights = estimator.forecast(identity, probabilities)
            forecast = tuple(sum(w * p for w, p in zip(weights, column)) for column in zip(*probabilities))
            # Even a label selected adversarially after the forecast obeys the bound.
            labels = (1, int(forecast[1] < .5)) if batch else (1, None)
            pending.append((identity, labels, probabilities, forecast))
        for identity, labels, probabilities, forecast in reversed(pending):
            estimator.resolve(identity, labels)

            def loss(row):
                errors = [(p - label) ** 2 for p, label in zip(row, labels) if label is not None]
                return sum(errors) / len(errors)

            aggregate_loss += loss(forecast)
            losses = [total + loss(row) for total, row in zip(losses, probabilities)]
    assert estimator.lanes == waves
    assert aggregate_loss - min(losses) <= 2 * estimator.lanes * math.log(len(losses))


def test_mixture_quantile_supports_empirical_jumps():
    survivals = (
        lambda horizon: float(horizon < 1.),
        lambda horizon: float(horizon < 3.),
    )
    assert mixture_quantile(survivals, (.7, .3), .5, maximum_s=4.) == pytest.approx(1., abs=.001)
    assert mixture_quantile(survivals, (.7, .3), .9, maximum_s=4.) == pytest.approx(3., abs=.001)
