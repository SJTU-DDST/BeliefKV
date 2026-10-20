"""Inherited FULL victims wake each current owner without scanning all sessions."""

import pytest

from tests.test_causal_full_opportunity_wakeup import shared_owner_fixture


@pytest.mark.parametrize("invalid", (None, "old_generation", "closed_session", "unknown_owner"))
def test_inherited_victim_uses_native_owner_generations_without_direct_leaf_ids(invalid):
    runtime, owners, cache, node = shared_owner_fixture()
    first, second = owners
    node.component_data[0].session_ids = None
    records = [(key.session_id, key.session_generation) for key in owners]
    if invalid == "old_generation":
        records[0] = (first.session_id, first.session_generation - 1)
    elif invalid == "closed_session":
        cache.session_refs._closed_session_ids.add(first.session_id)
    elif invalid == "unknown_owner":
        records[0] = ("unknown", 1)
    cache.beliefkv_handoff_last_victims[0]["full_session_owners"] = tuple(records)
    try:
        runtime._invalidate_causal_full_donor_observations({0: 4})
        assert second not in runtime._tool_opportunity_cache
        assert second not in runtime._semantic_target_cache
        assert (first in runtime._tool_opportunity_cache) == (invalid is not None)
        assert runtime.counts["cold_full_owner_index_built"] == 1
        assert runtime.counts["wait_transfer_observation_invalidated"] == (
            2 if invalid is None else 1
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("malformed", (None, (), (("unknown",),)))
def test_unobservable_inherited_owners_do_not_clear_all_contexts(malformed):
    runtime, owners, cache, node = shared_owner_fixture()
    node.component_data[0].session_ids = None
    cache.beliefkv_handoff_last_victims[0]["full_session_owners"] = malformed
    try:
        runtime._invalidate_causal_full_donor_observations({0: 4})
        assert all(key in runtime._tool_opportunity_cache for key in owners)
        assert runtime.counts["wait_transfer_observation_invalidated"] == 0
        assert runtime.counts["cold_full_owner_index_built"] == 1
    finally:
        runtime.close()
