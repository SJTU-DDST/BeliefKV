"""Real native eviction between model completion and a short tool's reentry."""

from array import array
from types import SimpleNamespace as NS

import pytest

from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_continuity import workflow_round


@pytest.mark.parametrize("temporary_native_pin", (False, True))
def test_short_tool_age_credit_does_not_preserve_a_native_resident_prefix(
    full_native, monkeypatch, temporary_native_pin,
):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams
    from test_beliefkv_terminal_capacity import insert_session

    monkeypatch.setattr("time.monotonic", lambda: 12.)
    runtime, other, successor, reader = workflow_round(monkeypatch)
    cache, allocator, writes, loads, _commits = full_native
    successor.origin_input_ids = array("q", (10, 11, 12, 13, 14))
    leaf, generation = insert_session(
        cache, [10, 11, 12, 13], successor.session_id,
    )
    assert generation == successor.session_generation
    assert cache.prepare_beliefkv_spawn_full(
        session_id=successor.session_id, session_generation=generation,
    )["issued"]
    cache.writing_check(finish_count=1)
    cache.tree_core.set_hicache_enabled()
    full = leaf.component_data[0]
    host_copy = full.host_value
    before = allocator.available_size()
    live = cache.inspect_beliefkv_reentry(successor)
    assert live["device_checkpoint_tokens"] == 4
    native_pin = cache.inc_lock_ref(leaf.id) if temporary_native_pin else None
    hot_other = reader.return_value

    def inspect(request, **_kwargs):
        return (
            cache.inspect_beliefkv_reentry(request)
            if request is successor else hot_other
        )

    reader.side_effect = inspect
    try:
        # There is no active request to preempt: only completed immutable KV.
        evicted = cache.evict(EvictParams(num_tokens=4))
        assert evicted.num_tokens_evicted == (0 if temporary_native_pin else 4)
        assert allocator.available_size() == before + (0 if temporary_native_pin else 4)
        observed = cache.inspect_beliefkv_reentry(successor)
        assert observed["device_checkpoint_tokens"] == (4 if temporary_native_pin else 0)
        assert observed["missing_full_tokens"] == (0 if temporary_native_pin else 4)
        key = runtime.visible[successor.rid]
        assert runtime._tool_continuity.inherited_arrival(key, arrival=12.) is not None
        plan = runtime.plan_native_prefill(
            [other, successor], running_batch=NS(reqs=[]), adder=None,
        )
        assert plan.prioritized[0].request_id == (
            successor.rid if temporary_native_pin else other.rid
        )
        assert full.host_value is host_copy
        assert len(writes) == 1 and not loads
        assert not runtime.physical_ledger.pending_count
    finally:
        if native_pin is not None:
            cache.dec_lock_ref(leaf.id, native_pin.to_dec_params())
        runtime.close()
    assert full.lock_ref == 0
