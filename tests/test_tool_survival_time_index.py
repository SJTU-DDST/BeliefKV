import math
import random

import pytest

from beliefkv.predictor.tool_survival import KaplanMeierCurve


def scalar_quantile(curve, elapsed, quantile):
    base = curve.survival(elapsed)
    if base <= 0:
        return math.inf
    for when, survival in curve.to_dict()["steps"]:
        if when < elapsed:
            continue
        if survival / base <= 1. - quantile:
            return max(0., when - elapsed)
    return math.inf


@pytest.mark.parametrize("size", (0, 1, 4, 8, 16, 32, 64, 128))
@pytest.mark.parametrize("completed_every", (1, 2, 7, 0))
def test_indexed_survival_preserves_scalar_conditional_quantiles(size, completed_every):
    rng = random.Random(21 + size)
    samples = [
        (
            float(rng.randrange(32)) / 7.,
            bool(completed_every and number % completed_every == 0),
        )
        for number in range(size)
    ]
    curve = KaplanMeierCurve().fit(samples)
    restored = KaplanMeierCurve.from_dict(curve.to_dict())
    times = {-1., 0., .1, 1., 100., math.inf}
    for duration, _ in samples:
        times.update((
            math.nextafter(duration, -math.inf), duration,
            math.nextafter(duration, math.inf),
        ))
    for elapsed in sorted(times):
        for quantile in (
            math.nextafter(0., 1.), .1, .5, .9, math.nextafter(1., 0.),
        ):
            expected = scalar_quantile(curve, elapsed, quantile)
            assert curve.remaining_quantile(elapsed, quantile) == expected
            assert restored.remaining_quantile(elapsed, quantile) == expected


def test_duplicate_serialized_steps_and_plateaus_keep_the_first_matching_quantile():
    curve = KaplanMeierCurve.from_dict({
        "sample_count": 64, "event_count": 40,
        "steps": [
            [10., 1.], [10., .9], [20., .9], [30., .75],
            [30., .5], [40., .5], [50., .25], [60., .1],
        ],
    })
    for elapsed in (-1., 0., 10., 15., 30., 40., 60., 100.):
        for q in (.1, .25, .5, .75, .9):
            assert curve.remaining_quantile(elapsed, q) == scalar_quantile(curve, elapsed, q)


@pytest.mark.parametrize("q", (-1., 0., 1., math.inf, math.nan))
def test_invalid_quantile_is_rejected(q):
    with pytest.raises(ValueError, match="q must be"):
        KaplanMeierCurve().fit([(1., True)]).remaining_quantile(0., q)


@pytest.mark.parametrize("samples", (
    [], [(100., False)], [(10., True), (20., True)],
    [(30., False), (40., True), (50., True)],
))
def test_refit_replaces_the_time_index_including_empty_and_censored_curves(samples):
    curve = KaplanMeierCurve().fit([(1., True), (2., True), (3., True)])
    curve.fit(samples)
    restored = KaplanMeierCurve.from_dict(curve.to_dict())
    expected_times = tuple(step[0] for step in curve.to_dict()["steps"])
    assert curve._step_times == restored._step_times == expected_times
    for when in (-1., 0., 1., 2., 3., 10., 30., 40., 50., 100., math.inf):
        expected = 1.
        for step_time, survival in curve.to_dict()["steps"]:
            if step_time > when:
                break
            expected = survival
        assert curve.survival(when) == restored.survival(when) == expected
