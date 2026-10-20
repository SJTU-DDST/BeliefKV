"""An unrelated JOIN owner cannot inherit another invocation's restore protection."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from tests.test_prepare_publication_batch import publication_fixture


def join_owners(runtime):
    return tuple(
        key for key in runtime.context_sessions.values()
        if runtime.graph.invocations[key.invocation_id].state is InvocationState.WAIT_JOIN
    )


@pytest.mark.parametrize("beneficiary", ("live", "expired", "terminal"))
def test_foreign_join_owner_does_not_validate_or_inherit_an_unrelated_demand(beneficiary):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, _, cache, _, _, actions, _ = publication_fixture()
        target, donor = join_owners(runtime)
        demand = (target, 100, 1, 999. if beneficiary == "expired" else 1002.)
        runtime._critical_parent_demand = demand
        if beneficiary == "terminal":
            runtime.graph.invocations[target.invocation_id].state = InvocationState.DONE
        try:
            with patch.object(
                runtime, "_live_critical_parent_demand",
                side_effect=AssertionError("this invocation is not the beneficiary"),
            ):
                assert runtime._live_parent_pressure_key(donor)
            assert runtime._critical_parent_demand is demand
            assert not actions and not runtime.physical_ledger.pending_count
        finally:
            runtime.close()


@pytest.mark.parametrize("expired", (False, True))
def test_matching_beneficiary_keeps_the_fresh_demand_and_expiry_check(expired):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, _, _, _, _, actions, _ = publication_fixture()
        target, _ = join_owners(runtime)
        runtime._critical_parent_demand = (target, 100, 1, 999. if expired else 1002.)
        try:
            with patch.object(
                runtime, "_live_critical_parent_demand",
                wraps=runtime._live_critical_parent_demand,
            ) as live:
                assert runtime._live_parent_pressure_key(target) is expired
            live.assert_called_once_with()
            assert (runtime._critical_parent_demand is None) is expired
            assert not actions and not runtime.physical_ledger.pending_count
        finally:
            runtime.close()


@pytest.mark.parametrize("fresh_key", ("current", "unrelated"))
def test_same_invocation_other_checkpoint_still_uses_the_fresh_final_key(fresh_key):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, _, _, _, _, actions, _ = publication_fixture()
        target, other = join_owners(runtime)
        origin = replace(target, request_id="old-checkpoint-request")
        runtime._critical_parent_demand = (origin, 100, 1, 1002.)
        # Model the existing live helper's final-key proof, not raw tuple equality.
        proven = target if fresh_key == "current" else other
        try:
            with patch.object(
                runtime, "_live_critical_parent_demand",
                return_value=(proven, 100, 1, 1002.),
            ) as live:
                assert runtime._live_parent_pressure_key(target) is (fresh_key != "current")
            live.assert_called_once_with()
            assert not actions and not runtime.physical_ledger.pending_count
        finally:
            runtime.close()


def test_absent_demand_skips_its_helper_without_skipping_owner_terminal_validation():
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, _, _, _, _, actions, _ = publication_fixture()
        target, _ = join_owners(runtime)
        try:
            with patch.object(
                runtime, "_live_critical_parent_demand",
                side_effect=AssertionError("no demand to validate"),
            ), patch.object(runtime, "_terminal", wraps=runtime._terminal) as terminal:
                assert runtime._live_parent_pressure_key(target)
            assert any(call.args == (target,) for call in terminal.call_args_list)
            assert not actions and not runtime.physical_ledger.pending_count
        finally:
            runtime.close()
