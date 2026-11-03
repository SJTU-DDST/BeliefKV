from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_residency_pause import install
from tests.test_reference_tool_replacement import pair, run
from tests.test_tool_policy_adaptation import demand_runtime


def test_impossible_single_donor_budget_skips_native_prefix_inspection(
    demand_runtime, monkeypatch,
):
    runtime, admission, controller, _adder = parts = pair(demand_runtime)
    admission.policy.programs["consumer"].tokens = 100
    inspect = Mock(side_effect=AssertionError("an impossible replacement must not inspect a prefix"))
    monkeypatch.setattr(runtime, "_read_request_reentry", inspect)
    assert not run(parts)
    inspect.assert_not_called()
    assert admission.counts["tool_event_replacement_logical_window_skips"] == 1
    assert demand_runtime[4].component_data[0].value is not None
    assert admission.policy.programs["tool"].active
    assert not admission.policy.programs["consumer"].active
    assert admission.policy.programs["hot"].active
    assert not runtime.physical_ledger.pending_count


def test_logical_prefilter_rechecks_current_capacity_and_allows_physical_only_demand(demand_runtime):
    _runtime, admission, controller, _adder = pair(demand_runtime)
    key = admission.latest["consumer"]
    donors = (admission.latest["tool"],)
    admission.policy.programs["consumer"].tokens = 50
    assert not controller.replacement._logical_window_can_fit(key, donors)
    admission.policy.capacity_tokens += 20
    assert controller.replacement._logical_window_can_fit(key, donors)
    admission.policy.programs["consumer"].tokens = 1
    assert controller.replacement._logical_window_can_fit(key, ())


def test_logical_prefilter_keeps_the_original_bounded_rotating_donor_window(demand_runtime):
    _runtime, admission, controller, _adder = pair(demand_runtime)
    key = admission.latest["consumer"]
    donor_key = admission.latest["tool"]
    donors = tuple(replace(donor_key, invocation_id=f"donor-{index}") for index in range(9))
    for index, donor in enumerate(donors):
        admission.policy.programs[donor.invocation_id] = NS(
            tokens=200 if index == 8 else 2, active=True, acting=False,
        )
    admission.policy.programs["consumer"].tokens = 100
    admission.policy.capacity_tokens = sum(
        owner.tokens for owner in admission.policy.programs.values() if owner.active
    ) + 4
    assert not controller.replacement._logical_window_can_fit(key, donors)
    controller.cursor = 1
    assert controller.replacement._logical_window_can_fit(key, donors)
    admission.policy.programs[donors[-1].invocation_id].active = False
    admission.policy.capacity_tokens -= 200
    assert not controller.replacement._logical_window_can_fit(key, donors)


@pytest.mark.parametrize("path", ("replacement", "ordinary"))
def test_one_fresh_headroom_read_is_shared_with_the_same_demand_budget(
    demand_runtime, monkeypatch, path,
):
    from beliefkv.runtime import sglang_v0520_runtime

    if path == "replacement":
        runtime, admission, controller, _adder = pair(demand_runtime)
    else:
        runtime, admission, controller = install(demand_runtime)
    key, request = admission.latest["consumer"], demand_runtime[5]
    runtime._observe_native_admission_capacity(
        running_batch=NS(reqs=[]),
        adder=NS(max_running_requests=4, rem_input_tokens=8, rem_chunk_tokens=8, page_size=1),
    )
    monkeypatch.setattr(
        sglang_v0520_runtime, "observe_static_full_mamba_headroom",
        Mock(side_effect=AssertionError("the same demand must reuse its fresh free-list observation")),
    )
    demand = (
        controller.replacement.demand(request, key, refresh=True) if path == "replacement"
        else controller._demand(request, key)
    )
    assert demand is not None and demand[0 if path == "replacement" else 1] > 0
    assert runtime._residency_headroom.observable


def test_one_permitted_donor_check_reuses_its_exact_consumer_demand(demand_runtime, monkeypatch):
    runtime, admission, controller, adder = pair(demand_runtime)
    request, key = demand_runtime[5], admission.latest["consumer"]
    donor = admission.latest["tool"]
    runtime._observe_native_admission_capacity(running_batch=NS(reqs=[]), adder=adder)
    controller._admission_adder = adder
    controller.replacement.consumer = (request, key)
    controller.replacement.donor = donor
    inspect = Mock(wraps=controller.replacement.demand)
    monkeypatch.setattr(controller.replacement, "demand", inspect)
    result = controller._demand(request, key, donor)
    assert result is not None and result[0] == key
    inspect.assert_called_once_with(request, key)
    assert admission.policy.programs["tool"].active
    assert not admission.policy.programs["consumer"].active
    assert not runtime.physical_ledger.pending_count


def test_permitted_demand_is_rechecked_after_capacity_changes(demand_runtime):
    runtime, admission, controller, adder = pair(demand_runtime)
    request, key = demand_runtime[5], admission.latest["consumer"]
    donor = admission.latest["tool"]
    runtime._observe_native_admission_capacity(running_batch=NS(reqs=[]), adder=adder)
    controller._admission_adder = adder
    controller.replacement.consumer = (request, key)
    controller.replacement.donor = donor
    assert controller._demand(request, key, donor) is not None
    admission.policy.programs["consumer"].tokens = 100
    assert controller._demand(request, key, donor) is None
    admission.policy.programs["consumer"].tokens = 10
    assert controller._demand(request, key, donor) is not None
    assert demand_runtime[4].component_data[0].value is not None


@pytest.mark.parametrize("change", ("consumer", "donor", "paused_donor", "no_deficit"))
def test_shared_demand_preserves_replacement_identity_and_capacity_guards(
    demand_runtime, monkeypatch, change,
):
    runtime, admission, controller, adder = pair(demand_runtime)
    request, key = demand_runtime[5], admission.latest["consumer"]
    donor = admission.latest["tool"]
    runtime._observe_native_admission_capacity(running_batch=NS(reqs=[]), adder=adder)
    controller.replacement.consumer = (request, key)
    controller.replacement.donor = donor
    if change == "consumer":
        controller.replacement.consumer = None
    elif change == "donor":
        controller.replacement.donor = replace(donor, invocation_id="another-donor")
    elif change == "paused_donor":
        admission.policy.programs["tool"].active = False
        monkeypatch.setattr(controller.replacement, "demand", lambda *_args: (1, 1, 0))
    else:
        monkeypatch.setattr(controller.replacement, "demand", lambda *_args: (0, 0, 0))
    assert controller.replacement._permitted_demand(request, key, donor) is None
    assert not controller.replacement.permits(request, key, donor)
