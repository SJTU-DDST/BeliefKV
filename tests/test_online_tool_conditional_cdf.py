"""Online distributions agree with the scalar censored-survival reference."""

from math import isfinite, nextafter, ulp

import pytest

from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.predictor.tool_survival import KaplanMeierCurve
from tests.test_online_tool_history_revision import completed, ready


HISTORIES = (
    tuple((100. * number, True) for number in range(1, 9)),
    ((100., True),) * 8,
    tuple((100. * number, number % 3 != 0) for number in range(1, 17)),
    ((100., False), (100., True), (200., True), (200., False)) * 4,
    ((100., True),) * 8 + ((180_000., False),) * 4,
    tuple((nextafter(float(number), 0.), True) for number in range(1, 9)),
)


@pytest.mark.parametrize("history", HISTORIES)
@pytest.mark.parametrize("scope", ("workflow", "run"))
def test_online_cdf_matches_scalar_reference_at_boundaries_and_surviving_tails(history, scope):
    model = OnlineWorkloadToolTiming()
    source_workflow = "workflow" if scope == "workflow" else "other"
    for number, (duration, observed) in enumerate(history):
        completed(
            model, number, workflow=source_workflow,
            duration=duration, censored=not observed,
        )
    ready(model)
    curve = KaplanMeierCurve().fit(list(history))
    elapsed_times = {0., ulp(2.) / 2, 0.1, 10., 100_000., 180_001.}
    for duration, _ in history:
        elapsed_times.update((
            nextafter(duration, 0.), duration, nextafter(duration, float("inf")),
        ))
    for elapsed in sorted(elapsed_times):
        # Use the actual subtraction performed by the model, including rounding.
        now = 10_000. + elapsed
        age = max(0., now - 10_000.)
        quantiles = tuple(curve.remaining_quantile(age, q) for q in (.1, .5, .9))
        value = model.estimate("workflow", "agent", now_ms=now)
        if not all(isfinite(quantile) for quantile in quantiles):
            assert value is None
            continue
        assert (value.p10_ms, value.p50_ms, value.p90_ms) == quantiles
        assert value.scope == scope
        assert value.samples == len(history)
        endpoints = sorted({duration - age for duration, _ in history if duration > age})
        assert value.release_cdf == tuple(
            (future, 1. - curve.conditional_survival(age, future))
            for future in endpoints
        )


def test_censored_workflow_tail_falls_back_to_completed_run_history():
    model = OnlineWorkloadToolTiming()
    for number in range(8):
        completed(model, number, workflow="other", duration=1000. + number * 100.)
    for number in range(8, 12):
        completed(model, number, duration=100., censored=True)
    ready(model)
    value = model.estimate("workflow", "agent", now_ms=10_500.)
    curve = KaplanMeierCurve().fit(
        [(1000. + number * 100., True) for number in range(8)] + [(100., False)] * 4,
    )
    assert value.scope == "run"
    assert (value.p10_ms, value.p50_ms, value.p90_ms) == tuple(
        curve.remaining_quantile(500., q) for q in (.1, .5, .9)
    )
    assert model.estimate("workflow", "agent", now_ms=12_000.) is None
