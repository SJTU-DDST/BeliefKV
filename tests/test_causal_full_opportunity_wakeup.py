"""Shared cold FULL demotion makes previously empty restore targets visible."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.native_transfer_policy import TransferStartWindow
from tests.test_causal_full_donor import fixture as owner_fixture
from tests.test_join_confirmed_reentry import add_second_join
from tests.test_pressure_opportunity_wakeup import wakeup_fixture


def shared_owner_fixture():
    runtime, first, second = owner_fixture()
    owners = (first, second)
    full = NS(value=None, host_value=(1, 2, 3, 4), session_ids={key.session_id for key in owners})
    node = NS(creation_time=17., component_data={0: full})
    cache = NS(
        tree_core=NS(node_by_id=Mock(return_value=node)),
        session_refs=NS(
            _closed_session_ids=set(),
            _session_generations={key.session_id: key.session_generation for key in owners},
        ),
        beliefkv_handoff_last_victims=[{
            "node_id": 71, "creation_time": 17., "scope": "causal_waiting_full",
            "freed_units": {0: 4},
        }],
    )
    runtime.attach_native_cache(cache)
    assert runtime._live_cold_full_owners(tuple(
        (key.session_id, key.session_generation) for key in owners
    ))
    for key in owners:
        runtime._tool_opportunity_cache[key] = (100., None)
        runtime._semantic_target_cache[key] = (100., False)
    return runtime, owners, cache, node


def causal_tool_fixture():
    runtime, key, positive, empty, cache, clock = wakeup_fixture()
    runtime.enable_prepare_host = True
    beneficiary = add_second_join(runtime)
    runtime._set_critical_parent_demand(beneficiary, 20, 0, clock[0] + 10.)
    node = NS(
        creation_time=positive.step.creation_time,
        component_data={0: NS(
            value=(1, 2, 3, 4), host_value=(1, 2, 3, 4),
            session_ids={key.session_id},
        )},
    )
    cache.tree_core = NS(node_by_id=Mock(return_value=node))
    cache.session_refs = NS(
        _closed_session_ids=set(),
        _session_generations={key.session_id: key.session_generation},
    )
    cache.beliefkv_handoff_last_victims = []
    cache.beliefkv_handoff_last_capacity = {"full_target_satisfied": True}

    def reclaim(**_):
        assert runtime._live_cold_full_owners(((key.session_id, key.session_generation),))
        node.component_data[0].value = None
        cache.beliefkv_handoff_last_victims = [{
            "node_id": positive.step.node_id,
            "creation_time": positive.step.creation_time,
            "scope": "causal_waiting_full", "freed_units": {0: 4},
        }]
        return {0: 4}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    return runtime, key, positive, empty, cache, clock, beneficiary


def test_shared_owners_are_invalidated_once_without_rebuilding_the_owner_index():
    runtime, owners, cache, _ = shared_owner_fixture()
    unrelated = replace(owners[0], context_id="unrelated", session_id="unrelated")
    runtime._tool_opportunity_cache[unrelated] = (100., None)
    runtime._semantic_target_cache[unrelated] = (100., False)
    cache.beliefkv_handoff_last_victims *= 2
    try:
        with patch.object(
            runtime, "_invalidate_wait_transfer_observation",
            wraps=runtime._invalidate_wait_transfer_observation,
        ) as invalidate:
            runtime._invalidate_causal_full_donor_observations({0: 8})
        assert {call.args[0] for call in invalidate.call_args_list} == set(owners)
        assert invalidate.call_count == 2
        assert runtime.counts["wait_transfer_observation_invalidated"] == 2
        assert runtime.counts["cold_full_owner_index_built"] == 1
        assert runtime._tool_opportunity_cache == {unrelated: (100., None)}
        assert runtime._semantic_target_cache == {unrelated: (100., False)}
    finally:
        runtime.close()


@pytest.mark.parametrize("state", (
    "zero_release", "mamba_only_release", "unreferenced_scope", "tracked_scope",
    "zero_victim_full", "mamba_only_victim", "different_creation",
    "device_present", "no_host_copy", "missing_node", "missing_component",
    "malformed_victim", "stale_index_revision", "replaced_index_source",
    "no_observations_or_hints",
))
def test_noncausal_or_unobservable_release_does_not_clear_owner_observations(state):
    runtime, owners, cache, node = shared_owner_fixture()
    freed = {0: 4}
    victim = cache.beliefkv_handoff_last_victims[0]
    if state == "zero_release":
        freed = {0: 0}
    elif state == "mamba_only_release":
        freed = {2: 1}
    elif state == "unreferenced_scope":
        victim["scope"] = "unreferenced_cache"
    elif state == "tracked_scope":
        victim["scope"] = "waiting_agent"
    elif state == "zero_victim_full":
        victim["freed_units"] = {0: 0}
    elif state == "mamba_only_victim":
        victim["freed_units"] = {2: 1}
    elif state == "different_creation":
        node.creation_time += 1
    elif state == "device_present":
        node.component_data[0].value = (1, 2, 3, 4)
    elif state == "no_host_copy":
        node.component_data[0].host_value = None
    elif state == "missing_node":
        cache.tree_core.node_by_id.side_effect = KeyError
    elif state == "missing_component":
        node.component_data = {}
    elif state == "malformed_victim":
        cache.beliefkv_handoff_last_victims = [None]
    elif state == "stale_index_revision":
        runtime._context_sessions_revision += 1
    elif state == "replaced_index_source":
        runtime.context_sessions = dict(runtime.context_sessions)
    elif state == "no_observations_or_hints":
        runtime._tool_opportunity_cache.clear()
        runtime._semantic_target_cache.clear()
    try:
        before = (
            dict(runtime._tool_opportunity_cache), dict(runtime._semantic_target_cache),
        )
        with patch.object(runtime, "_live_cold_full_owners") as rebuild:
            runtime._invalidate_causal_full_donor_observations(freed)
        rebuild.assert_not_called()
        assert runtime.counts["cold_full_owner_index_built"] == 1
        assert (
            runtime._tool_opportunity_cache, runtime._semantic_target_cache,
        ) == before
        if state in (
            "zero_release", "mamba_only_release", "stale_index_revision",
            "replaced_index_source", "no_observations_or_hints",
        ):
            cache.tree_core.node_by_id.assert_not_called()
        assert all(key in runtime.context_sessions.values() for key in owners)
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("closed", "new_generation", "context_rebound", "missing_owner"))
def test_stale_owner_is_ignored_without_hiding_another_current_owner(change):
    runtime, (first, second), cache, node = shared_owner_fixture()
    if change == "closed":
        cache.session_refs._closed_session_ids.add(first.session_id)
    elif change == "new_generation":
        cache.session_refs._session_generations[first.session_id] += 1
    elif change == "context_rebound":
        runtime.context_sessions[first.context_id] = replace(
            first, session_generation=first.session_generation + 1,
        )
    elif change == "missing_owner":
        del runtime._cold_full_owner_index[first.session_id]
    try:
        runtime._invalidate_causal_full_donor_observations({0: 4})
        assert first in runtime._tool_opportunity_cache
        assert first in runtime._semantic_target_cache
        assert second not in runtime._tool_opportunity_cache
        assert second not in runtime._semantic_target_cache
        assert runtime.counts["wait_transfer_observation_invalidated"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("already_due", (False, True))
def test_actual_critical_reclaim_wakes_a_tool_before_its_old_empty_cache_expires(already_due):
    runtime, key, positive, empty, cache, clock, _ = causal_tool_fixture()
    current = [empty]
    try:
        with patch("time.monotonic", new=lambda: clock[0]), patch.object(
            runtime, "inspect_context_h2d_opportunity", side_effect=lambda **_: current[0],
        ) as inspect, patch.object(
            runtime, "_h2d_start_window", return_value=TransferStartWindow(100., 0., 200.),
        ):
            runtime.dispatch_tool_prefetch()
            expiry = runtime._tool_opportunity_cache[key][0]
            runtime._semantic_target_cache[key] = (expiry, False)
            current[0] = positive
            clock[0] += .01
            assert clock[0] * 1000. < expiry
            if already_due:
                runtime._tool_prefetch_next_ms = 0.
            with patch.object(
                runtime, "_wait_prefetch_opportunity",
                return_value=NS(candidate=NS(nodes=())),
            ), patch.object(
                runtime, "_publish_parent_pressure_candidates",
            ), patch(
                "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
                return_value=replace(positive.headroom, device_full_free_tokens=0),
            ):
                assert runtime._reclaim_critical_parent_headroom()
            runtime.dispatch_tool_prefetch()
        assert inspect.call_count == 2
        assert cache.reclaim_beliefkv_handoff_capacity.call_count == 1
        assert cache.prefetch_gpu_session_nodes.call_count == 1
        assert runtime.physical_ledger.pending_count == 1
        assert runtime.counts["wait_transfer_observation_invalidated"] == 1
        assert runtime.counts["cold_full_owner_index_built"] == 1
        assert not runtime.physical_disabled
    finally:
        runtime.close()
