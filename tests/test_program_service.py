import math

import pytest

from beliefkv.policy.program_service import ProgramServiceReference


@pytest.mark.parametrize("mode", ("plas", "atlas"))
def test_serial_calls_inherit_completed_executor_service(mode):
    table = ProgramServiceReference(mode)
    assert table.arrive("a", "root").inherited_service_ms == 0
    assert table.complete("a", executor_service_ms=4.) == 4.
    assert table.arrive("b", "root").inherited_service_ms == 4.
    assert table.complete("b", executor_service_ms=3.) == 7.


@pytest.mark.parametrize("mode,expected", (("plas", 10.), ("atlas", 7.)))
def test_parallel_calls_sum_work_or_max_inherited_path_without_completion_order_bias(mode, expected):
    for first, second in (("a", "b"), ("b", "a")):
        table = ProgramServiceReference(mode)
        table.arrive("initial", "root")
        table.complete("initial", executor_service_ms=2.)
        table.arrive("a", "root")
        table.arrive("b", "root")
        durations = {"a": 3., "b": 5.}
        for identity in (first, second):
            table.complete(identity, executor_service_ms=durations[identity])
        assert table.service_ms["root"] == expected
        assert table.arrive("joined", "root").inherited_service_ms == expected


def test_blocked_wall_clock_and_service_from_other_programs_never_charge_a_program():
    table = ProgramServiceReference("atlas")
    table.arrive("a", "blocked-root")
    table.complete("a", executor_service_ms=1.)
    table.arrive("other", "other-root")
    table.complete("other", executor_service_ms=10000.)
    assert table.arrive("return-from-tools", "blocked-root").inherited_service_ms == 1.
    assert table.service_ms["blocked-root"] == 1.


def test_lower_restore_cost_cannot_overtake_an_earlier_service_band():
    table = ProgramServiceReference("atlas")
    for program, elapsed in (("small", 1.), ("large", 20.)):
        table.arrive(program, program)
        table.complete(program, executor_service_ms=elapsed)
        table.arrive(program + "-next", program)
    assert table.order_ready(
        ["large-next", "small-next"], band_edges_ms=(10.,),
        exposed_restore_ms={"small-next": 1000., "large-next": 0.},
    ) == ["small-next", "large-next"]


def test_restore_tiebreak_is_optional_stable_and_requires_evidence_for_the_whole_band():
    table = ProgramServiceReference("plas")
    for identity in "abcd":
        table.arrive(identity, identity)
    ready = list("abcd")
    assert table.order_ready(ready, band_edges_ms=()) == ready
    assert table.order_ready(
        ready, band_edges_ms=(), exposed_restore_ms={"a": 5., "b": 0.},
    ) == ready
    assert table.order_ready(
        ready, band_edges_ms=(), exposed_restore_ms={"a": 5., "b": 0., "c": 1., "d": 1.},
    ) == list("bcda")


@pytest.mark.parametrize("value", (-1., math.nan, math.inf, True, "3"))
def test_invalid_service_does_not_consume_the_call_or_corrupt_program_account(value):
    table = ProgramServiceReference("atlas")
    table.arrive("a", "root")
    with pytest.raises(ValueError):
        table.complete("a", executor_service_ms=value)
    assert "a" in table.calls
    assert table.service_ms == {"root": 0.}


def test_program_lifecycle_and_duplicate_ready_calls_are_explicit():
    table = ProgramServiceReference("atlas")
    table.arrive("a", "root")
    with pytest.raises(ValueError):
        table.arrive("a", "another")
    with pytest.raises(ValueError):
        table.forget("root")
    with pytest.raises(ValueError):
        table.order_ready(["a", "a"], band_edges_ms=())
    table.complete("a", executor_service_ms=1.)
    with pytest.raises(KeyError):
        table.complete("a", executor_service_ms=1.)
    table.forget("root")
    assert not table.service_ms and not table.calls


@pytest.mark.parametrize("edges", ((0.,), (1., 1.), (2., 1.), (math.nan,), (True,)))
def test_bad_discretization_is_rejected(edges):
    with pytest.raises(ValueError):
        ProgramServiceReference("atlas").order_ready([], band_edges_ms=edges)
