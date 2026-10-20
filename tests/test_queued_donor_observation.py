"""Native FULL release invalidates only current donor residency observations."""

from types import SimpleNamespace as NS

import pytest

from tests.test_queued_full_donor import queued_fixture


@pytest.mark.parametrize("stale_generation", (False, True))
def test_freed_queue_donor_drops_current_residency_hints_but_keeps_other_identities(stale_generation):
    runtime, parent, donor, _, cache = queued_fixture()
    try:
        assert not runtime._live_cold_full_owners(((donor.session_id, donor.session_generation),))
        runtime._residency_ordering_hints[donor] = (1000., {"missing_full_tokens": 0})
        runtime._reentry_observations[donor] = (1000., {"missing_full_tokens": 0})
        runtime._residency_ordering_hints[parent] = (1000., {"missing_full_tokens": 0})
        runtime._reentry_observations[parent] = (1000., {"missing_full_tokens": 0})
        cache.tree_core = NS(node_by_id=lambda _: NS(
            creation_time=3., component_data={0: NS(value=None, host_value=[1, 2])},
        ))
        cache.session_refs = NS(
            _session_generations={donor.session_id: donor.session_generation},
            _closed_session_ids=set(),
        )
        cache.beliefkv_handoff_last_victims = ({
            "scope": "causal_waiting_full", "freed_units": {0: 2},
            "node_id": 7, "creation_time": 3.,
            "full_session_owners": ((
                donor.session_id, donor.session_generation - int(stale_generation),
            ),),
        },)
        runtime._invalidate_causal_full_donor_observations({0: 2})
        assert (donor in runtime._residency_ordering_hints) == stale_generation
        assert (donor in runtime._reentry_observations) == stale_generation
        assert parent in runtime._residency_ordering_hints
        assert parent in runtime._reentry_observations
    finally:
        runtime.close()
