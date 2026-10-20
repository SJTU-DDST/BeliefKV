"""Reuse the terminal proof supplied by the current tool-wait predicate."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from tests.test_causal_full_donor import fixture


def tool_owner_fixture(*, critical, stamp=1000.):
    runtime, parent, donor = fixture()
    invocation = runtime.graph.invocations[donor.invocation_id]
    invocation.state = InvocationState.WAIT_TOOL
    invocation.active_tool_calls = {"shell": ("execute", 0.)}
    if critical:
        runtime._set_critical_parent_demand(parent, 10, 1, stamp + 10.)
    else:
        runtime.tool_wait_hints[donor.context_id] = NativeToolWaitHint(
            donor, 60000., 120000., 180000., stamp * 1000.,
            stamp * 1000. + 3600000., "a" * 64, invocation.updated_ts_ms,
        )
    return runtime, parent, donor


@pytest.mark.parametrize("critical", (False, True))
def test_live_tool_owner_uses_one_terminal_validation_from_the_long_wait_path(critical):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, _, donor = tool_owner_fixture(critical=critical)
        calls = []
        original = runtime._terminal

        def terminal(key):
            calls.append(key)
            return original(key)

        runtime._terminal = terminal
        try:
            assert runtime._live_parent_pressure_key(donor)
            assert calls.count(donor) == 1
        finally:
            runtime.close()


@pytest.mark.parametrize("critical", (False, True))
@pytest.mark.parametrize("change", (
    "workflow_ended", "context_advanced", "invocation_done", "session_rebound",
    "prepare_disabled", "physical_disabled",
))
def test_tool_terminal_proof_never_allows_stale_or_disabled_owners(critical, change):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, _, donor = tool_owner_fixture(critical=critical)
        try:
            if change == "workflow_ended":
                runtime.graph.workflows[donor.root_workflow_id].end_ts_ms = 500.
            elif change == "context_advanced":
                runtime.graph.contexts[donor.context_id].epoch += 1
            elif change == "invocation_done":
                runtime.graph.invocations[donor.invocation_id].state = InvocationState.DONE
            elif change == "session_rebound":
                runtime.context_sessions[donor.context_id] = replace(donor, session_generation=3)
            elif change == "prepare_disabled":
                runtime.enable_prepare_host = False
            else:
                runtime.physical_disabled = True
            assert not runtime._live_parent_pressure_key(donor)
        finally:
            runtime.close()


@pytest.mark.parametrize("change", ("expired", "revision", "short_wait"))
def test_ordinary_tool_wait_still_requires_a_current_long_forecast(change):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, _, donor = tool_owner_fixture(critical=False)
        try:
            hint = runtime.tool_wait_hints[donor.context_id]
            if change == "expired":
                hint = replace(hint, expires_monotonic_ms=999999.)
            elif change == "revision":
                hint = replace(hint, invocation_revision_ts_ms=hint.invocation_revision_ts_ms + 1.)
            else:
                hint = replace(hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.)
            runtime.tool_wait_hints[donor.context_id] = hint
            assert not runtime._live_parent_pressure_key(donor)
        finally:
            runtime.close()


def test_critical_tool_wait_does_not_use_an_expired_demand_as_a_terminal_proof():
    clock = [1000.]
    with patch("time.monotonic", new=lambda: clock[0]):
        runtime, _, donor = tool_owner_fixture(critical=True)
        try:
            clock[0] = 1011.
            assert not runtime._live_parent_pressure_key(donor)
            assert runtime._critical_parent_demand is None
        finally:
            runtime.close()


@pytest.mark.parametrize("change", ("workflow_ended", "context_advanced"))
def test_join_branch_rechecks_terminal_state_even_with_an_unchanged_parent_cache(change):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, parent, _ = fixture()
        try:
            assert runtime._join_parent_key("join") == parent
            if change == "workflow_ended":
                runtime.graph.workflows[parent.root_workflow_id].end_ts_ms = 500.
            else:
                runtime.graph.contexts[parent.context_id].epoch += 1
            assert not runtime._live_parent_pressure_key(parent)
        finally:
            runtime.close()
