"""Free request rows must not protect every unstarted request from FULL reclaim."""

from dataclasses import replace

import pytest

from tests import test_queued_full_donor as native_donor_tests
from tests.test_queued_full_donor import queued_fixture
from tests.test_waiting_full_tail_backup import native_cache


@pytest.mark.parametrize("free_rows", [2, 8, 24])
def test_surplus_request_rows_do_not_hide_a_cold_queue_tail(free_rows):
    runtime, parent, donor, _, cache = queued_fixture()
    runtime._residency_budget = replace(
        runtime._residency_budget, request_slots=free_rows,
    )
    original = cache.beliefkv_cold_full_owner_validator
    try:
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == 1
            assert cache.beliefkv_cold_full_owner_validator((
                (donor.session_id, donor.session_generation),
            ))
        assert cache.beliefkv_cold_full_owner_validator is original
    finally:
        runtime.close()


def test_actual_queue_head_remains_protected_after_a_tail_moves_forward():
    runtime, parent, donor, request, cache = queued_fixture()
    runtime._residency_budget = replace(runtime._residency_budget, request_slots=24)
    try:
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == 1
            runtime._capacity_waiting_queue = [request]
            assert not cache.beliefkv_cold_full_owner_validator((
                (donor.session_id, donor.session_generation),
            ))
    finally:
        runtime.close()


@pytest.mark.parametrize("critical_kind", ["joined_parent", "last_child"])
@pytest.mark.parametrize("change_when", ["before_collection", "during_reclaim"])
def test_other_ready_join_dependencies_are_protected_at_each_owner_check(
    critical_kind, change_when,
):
    runtime, parent, donor, request, cache = queued_fixture()

    def mark_critical():
        if critical_kind == "joined_parent":
            join = runtime.graph.joins["other-join"]
            join.satisfied = True
            runtime._join_priority_pending[donor.invocation_id] = join.join_id
            assert runtime._ready_join_parent(request, donor)
        else:
            join = runtime.graph.joins["join"]
            join.member_invocation_ids = {donor.invocation_id}
            join.completed_member_ids.clear()
            runtime._join_by_invocation[donor.invocation_id] = {join.join_id}
            assert runtime._last_join_child_has_waiting_parent(donor)

    try:
        if change_when == "before_collection":
            mark_critical()
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == int(change_when == "during_reclaim")
            if change_when == "during_reclaim":
                mark_critical()
            assert not cache.beliefkv_cold_full_owner_validator((
                (donor.session_id, donor.session_generation),
            ))
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "guard", ["none", "full_lock", "mamba_lock", "pending_write", "pending_load"],
)
def test_actual_native_reclaim_with_surplus_rows_keeps_copy_and_dma_guards(
    native_cache, monkeypatch, guard,
):
    def surplus_rows_fixture():
        runtime, parent, donor, request, cache = queued_fixture()
        runtime._residency_budget = replace(
            runtime._residency_budget, request_slots=24,
        )
        return runtime, parent, donor, request, cache

    monkeypatch.setattr(native_donor_tests, "queued_fixture", surplus_rows_fixture)
    native_donor_tests.test_actual_native_full_reclaim_keeps_mamba_and_obeys_physical_guards(
        native_cache, guard,
    )
