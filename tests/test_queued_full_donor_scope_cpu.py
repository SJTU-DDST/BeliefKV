"""Collect queue donors once while retaining live checks at native reclaim."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from tests.test_queued_full_donor import queued_fixture
from tests.test_sglang_v0520_runtime import req


class ObservedQueue(list):
    def __init__(self, requests):
        super().__init__(requests)
        self.traversals = 0
        self.visited = 0

    def __iter__(self):
        self.traversals += 1
        for request in super().__iter__():
            self.visited += 1
            yield request


def queued_many_fixture(count):
    runtime, parent, donor, request, cache = queued_fixture()
    donors = [(donor, request)]
    for index in range(1, count):
        key = replace(
            donor, request_id=f"queued-request-{index}", invocation_id=f"queued-agent-{index}",
            context_id=f"queued-context-{index}", session_id=f"queued-session-{index}",
        )
        candidate = req(key.request_id)
        candidate.beliefkv_metadata.update(
            root_workflow_id=key.root_workflow_id, invocation_id=key.invocation_id,
            context_id=key.context_id, context_epoch=key.context_epoch,
        )
        candidate.session_id, candidate.session_generation = key.session_id, key.session_generation
        candidate.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
        candidate.origin_input_ids, candidate.output_ids = [10, 11, 12, 13, 14], []
        candidate.is_retracted = candidate.retracted_stain = False
        runtime.graph.invocations[key.invocation_id] = replace(
            runtime.graph.invocations[donor.invocation_id],
            invocation_id=key.invocation_id, context_id=key.context_id,
        )
        runtime.graph.contexts[key.context_id] = replace(
            runtime.graph.contexts[donor.context_id],
            context_id=key.context_id, invocation_ids={key.invocation_id},
        )
        runtime.graph.workflows[key.root_workflow_id].invocation_ids.add(key.invocation_id)
        runtime.visible[key.request_id] = runtime.context_sessions[key.context_id] = key
        runtime._context_session_changed(key.context_id)
        donors.append((key, candidate))
    runtime._capacity_waiting_queue = ObservedQueue([
        runtime._capacity_waiting_queue[0], *(candidate for _, candidate in donors),
    ])
    return runtime, parent, donors, cache


@pytest.mark.parametrize("count", (1, 8, 64, 128, 600))
def test_scope_collection_is_linear_but_native_owner_check_rereads_live_queue(count):
    runtime, parent, donors, cache = queued_many_fixture(count)
    queue = runtime._capacity_waiting_queue
    try:
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == min(count, 511)
            assert queue.traversals == 1
            assert queue.visited == min(count + 1, 512)
            key, candidate = donors[min(count, 511) - 1]
            owners = ((key.session_id, key.session_generation),)
            assert cache.beliefkv_cold_full_owner_validator(owners)
            assert queue.traversals == 2
            queue.remove(candidate)
            assert not cache.beliefkv_cold_full_owner_validator(owners)
            assert queue.traversals == 3
            if count > 511:
                beyond, _ = donors[511]
                assert not cache.beliefkv_cold_full_owner_validator((
                    (beyond.session_id, beyond.session_generation),
                ))
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("row", "allocated", "committed", "output", "native_generation"))
def test_current_tail_position_never_substitutes_for_native_request_state(change):
    runtime, parent, donors, cache = queued_many_fixture(1)
    key, candidate = donors[0]
    try:
        if change == "row":
            candidate.kv.req_pool_idx = 0
        elif change == "allocated":
            candidate.kv.kv_allocated_len = 1
        elif change == "committed":
            candidate.kv.kv_committed_len = 1
        elif change == "output":
            candidate.output_ids.append(42)
        else:
            candidate.session_generation += 1
        original = cache.beliefkv_cold_full_owner_validator
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == 0
            assert cache.beliefkv_cold_full_owner_validator is original
            assert not original(((key.session_id, key.session_generation),))
    finally:
        runtime.close()
