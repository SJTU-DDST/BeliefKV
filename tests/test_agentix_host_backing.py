import pytest

from beliefkv.runtime.agentix_capacity import AgentixHostBacking, AgentixNativeCapacity
from tests.test_agentix_native_capacity import waiting
from tests.test_agentix_native_preemption import native
from tests.test_agentix_swap import running
from tests.test_full_only_native_lifecycle import full_native


def backing(state, *, context=8, host=24):
    state.cache.host_pool_group.anchor_entry.host_pool.size = host
    capacity = AgentixNativeCapacity(
        state.actor.transfers, context_limit=context, decode_growth_tokens=2,
    )
    return AgentixHostBacking(capacity, state.actor)


def grow(state, req):
    old = req.kv.kv_allocated_len
    indices = state.allocator.alloc(1)
    assert indices is not None
    state.cache.req_to_token_pool.write((req.kv.req_pool_idx, slice(old, old + 1)), indices)
    req.kv.kv_allocated_len = req.kv.kv_committed_len = old + 1
    req.output_ids.append(99)


def test_backing_gate_retains_emergency_room_before_admitting_more_live_contexts(native):
    state = native
    first, second = running(state.cache, "first"), running(state.cache, "second")
    cold = waiting("cold", (50, 51, 52))
    budget = backing(state)
    budget.prepare([first, second, cold])
    assert not budget.draining
    assert budget.capacity.required_tokens([first, second]) == budget.normal_limit == 16
    assert budget.eligible(first) and budget.eligible(second)
    assert budget.can_fit(second, (first,))
    assert not budget.eligible(cold) and not budget.can_fit(cold, ())
    assert budget.status()["emergency_context_tokens"] == 8
    assert not state.writes and not state.loads


def test_growing_live_contexts_select_one_paper_ordered_call_until_it_finishes(native):
    state = native
    first, second = running(state.cache, "first"), running(state.cache, "second")
    third = running(state.cache, "third")
    cold = waiting("cold", (50, 51, 52))
    budget = backing(state, context=12, host=40)
    budget.prepare([first, second, third, cold])
    for req in (first, second, third):
        grow(state, req)
        grow(state, req)
    budget.prepare([first, second, third, cold])
    assert budget.draining and budget.single is None
    assert budget.eligible(first) and budget.eligible(second) and not budget.eligible(cold)
    assert budget.can_fit(first, ()) and not budget.can_fit(second, (first,))
    budget.selected((first.rid,))
    for _ in range(4):
        grow(state, first)
    budget.prepare([first, second, third, cold])
    assert budget.single == first.rid
    assert budget.eligible(first) and not budget.eligible(second)
    assert budget.capacity.required_tokens([first, second, third], include_growth=False) == 28
    assert budget.capacity._probe(first).known_tokens == 12
    budget.prepare([second, third, cold])
    assert not budget.draining and budget.single is None
    assert budget.eligible(cold) and budget.can_fit(cold, (second, third))
    assert not budget.status()["future_output_length_used"]


def test_backing_admission_counts_actual_shared_prefixes_once(native):
    state = native
    first = running(state.cache, "first")
    second = running(state.cache, "second", output=(90, 91))
    for req in (first, second):
        req._refresh_fill_ids()
        req.set_extend_range(0, req.effective_kv_committed_len())
        state.cache.cache_unfinished_req(req, chunked=True)
    cold = waiting("cold", (1, 2, 3, 4, 70))
    budget = backing(state)
    budget.prepare([first, second, cold])
    assert budget.capacity.required_tokens([first, second]) == 12
    assert budget.eligible(cold)
    assert budget.can_fit(cold, (first, second))


@pytest.mark.parametrize("context,host", ((8, 15), (128, 512)))
def test_backing_rejects_a_pool_that_cannot_support_its_progress_reserve(native, context, host):
    with pytest.raises(ValueError, match="maximum Host contexts"):
        backing(native, context=context, host=host)


def test_lost_backing_invariant_is_explicit_instead_of_silently_recomputing(native):
    state = native
    requests = [running(state.cache, str(index)) for index in range(4)]
    budget = backing(state)
    with pytest.raises(ValueError, match="reserve was exhausted"):
        budget.prepare(requests)
    assert all(req.kv.holds_kv for req in requests)
    assert not state.writes and not state.loads
