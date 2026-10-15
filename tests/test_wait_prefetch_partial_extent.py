from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.native_transfer_policy import (
    PrefetchResidencyBudget, TransferStartWindow, native_residency_budget,
)
from beliefkv.runtime.sglang_v0520_physical import (
    inspect_session_h2d_opportunity, plan_prefetch_gpu_steps,
)
from tests.test_sglang_v0520_physical import prefetch_node
from tests.test_wait_prefetch_batch import batch_fixture, provisional_join_batch_fixture


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize(
    "free_full,reserve,byte_limit,page_size,expected_tokens,reclaimed_full",
    ((3, 1, 1024, 1, 2, 0), (4, 1, 1024, 2, 2, 0),
     (10, 1, 25, 1, 2, 0), (1, 1, 1024, 1, 0, 0),
     (2, 1, 1024, 2, 0, 0), (1, 1, 1024, 1, 2, 2)),
)
def test_partial_first_extent_reaches_native_enqueue_without_consuming_reserve(
    source, free_full, reserve, byte_limit, page_size, expected_tokens, reclaimed_full,
):
    fixture = (
        provisional_join_batch_fixture() if source == "join_ticket"
        else batch_fixture(source)
    )
    runtime, key, original, cache, _ = fixture
    runtime._h2d_samples.extend(((20, 10.), (40, 10.)) * 3)
    if runtime._semantic_worker is not None:
        runtime._semantic_worker.error = ""
    runtime._native_page_size = page_size
    nodes = {node.node_id: node for node in original.candidate.nodes}
    headroom = [replace(original.headroom, device_full_free_tokens=free_full)]
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    if reclaimed_full:
        cache.beliefkv_join_pressure_candidates_by_component = {0: ((99, 100.),)}

    def reclaim(**kwargs):
        headroom[0] = replace(
            headroom[0], device_full_free_tokens=free_full + reclaimed_full,
        )
        return {0: reclaimed_full} if reclaimed_full else {}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)

    def split(**kwargs):
        assert kwargs["node_id"] == 1
        assert kwargs["session_id"] == key.session_id
        assert kwargs["prefix_tokens"] == expected_tokens
        node = nodes[1]
        nodes[5] = prefetch_node(
            5, node.parent_id, node.creation_time,
            full_host=expected_tokens, key_tokens=expected_tokens,
        )
        nodes[1] = NS(
            **{**vars(node), "parent_id": 5,
               "full_host_tokens": node.full_host_tokens - expected_tokens,
               "key_tokens": node.key_tokens - expected_tokens},
        )
        return 5, node.creation_time

    cache.split_beliefkv_prefetch_full_prefix = Mock(side_effect=split)

    def enqueue(**kwargs):
        outcomes = []
        for node_id, _, include_mamba, command in kwargs["nodes"]:
            assert node_id == 5
            assert not include_mamba
            tokens = nodes[node_id].full_host_tokens
            op = NS(
                beliefkv_command_id=command, node_ids=[node_id],
                host_indices=tuple(range(tokens)), device_indices=tuple(range(tokens)),
                pool_transfers=None,
            )
            assert kwargs["beliefkv_before_enqueue"](op)
            outcomes.append(NS(issued=True, node_id=node_id, load_started=True))
        return tuple(outcomes)

    cache.prefetch_gpu_session_nodes.side_effect = enqueue

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, original.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    def closure(_cache, node_id, **kwargs):
        chain = []
        while node_id is not None:
            node = nodes[node_id]
            chain.append(node)
            node_id = node.parent_id
        return NS(observable=True, nodes=tuple(chain))

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
                1, byte_limit, reserve_full_tokens=reserve,
            )), \
            patch.object(runtime, "_publish_parent_pressure_candidates"), \
            patch.object(runtime, "_live_parent_pressure_node", return_value=True), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  side_effect=closure), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  side_effect=lambda *args: headroom[0]):
            if source == "tool_wait":
                runtime.dispatch_tool_prefetch()
            else:
                runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == int(expected_tokens > 0), (
            dict(runtime.counts), records,
        )
        assert cache.split_beliefkv_prefetch_full_prefix.call_count == int(expected_tokens > 0)
        assert not runtime.physical_disabled
        if expected_tokens:
            assert headroom[0].device_full_free_tokens - expected_tokens >= reserve
            assert expected_tokens * 10 <= byte_limit
            assert nodes[4].mamba_host_present
            submitted = next(
                row for row in records
                if row["event"] == "wait_prefetch_plan" and row["reason"] == "submitted"
            )
            assert submitted["planned_pool_bytes"] == {"kv": expected_tokens * 10, "mamba": 0}
            assert submitted["partial_full_tokens"] == 0
            assert submitted["host_backed_full_missing_device_tokens"] == 16
            assert submitted["unplanned_host_backed_full_tokens"] == 16 - expected_tokens
            assert submitted["host_backed_mamba_missing_device_nodes"] == 0
            assert submitted["host_only_mamba_nodes_in_observed_closure"] == 1
            assert submitted["restore_batch_scope"] == "partial_observed_host_backed_full"
            split_record = next(
                row for row in records if row["event"] == "wait_prefetch_full_extent_split"
            )
            assert split_record["suffix_node_id"] == 1
            assert split_record["fragment_node_id"] == 5
            assert split_record["fragment_full_tokens"] == expected_tokens
            assert runtime.counts["wait_prefetch_full_extent_split_tokens"] == expected_tokens
            command = next(iter(runtime._prefetch_steps))
            runtime.on_native_transfer_commit(NS(
                direction="h2d", status="completed", node_ids=(5,),
                num_tokens_by_pool=(("kv", expected_tokens),),
                child_commits=(NS(
                    command_id=command, anchor_node_id=5, published_node_ids=(5,),
                    num_tokens_by_pool=(("kv", expected_tokens),),
                    num_bytes=expected_tokens * 10,
                ),),
            ))
            assert runtime.physical_ledger.pending_count == 0
            assert runtime.completed_physical_actions[-1].num_bytes == expected_tokens * 10
        else:
            cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("reclaim_succeeds", (False, True))
def test_zero_native_budget_can_reclaim_a_backed_waiting_session_at_latest_start(
    source, reclaim_succeeds,
):
    fixture = (
        provisional_join_batch_fixture() if source == "join_ticket"
        else batch_fixture(source)
    )
    runtime, key, opportunity, cache, _ = fixture
    runtime._h2d_samples.extend(((40, 10.),) * 3)
    if runtime._semantic_worker is not None:
        runtime._semantic_worker.error = ""
    headroom = [replace(opportunity.headroom, device_full_free_tokens=1)]
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    cache.beliefkv_join_pressure_candidates_by_component = {0: ((99, 100.),)}

    def reclaim(**kwargs):
        assert kwargs["exclude_node_ids"] == (0, 1, 2, 3, 4)
        if runtime.physical_ledger.pending_count:
            assert kwargs["full_tokens"] == 13
            assert runtime.physical_ledger.pending_count == 1
            return {}
        assert kwargs["full_tokens"] == 5
        if reclaim_succeeds:
            headroom[0] = replace(headroom[0], device_full_free_tokens=5)
        return {0: 4} if reclaim_succeeds else {}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          side_effect=lambda **kwargs: replace(
                              opportunity, headroom=headroom[0],
                          )), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(
                             1, 1024, reserve_full_tokens=1,
                         )), \
            patch.object(runtime, "_publish_parent_pressure_candidates"), \
            patch.object(runtime, "_live_parent_pressure_node", return_value=True), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )):
            if source == "tool_wait":
                runtime.dispatch_tool_prefetch()
            else:
                runtime.dispatch_join_prefetch()
        assert cache.reclaim_beliefkv_handoff_capacity.call_count == (
            2 if reclaim_succeeds else 1
        )
        assert runtime.counts["wait_prefetch_backed_capacity_probe"] == 1
        assert not runtime.physical_disabled
        if reclaim_succeeds:
            assert runtime.physical_ledger.pending_count == 1
            assert headroom[0].device_full_free_tokens - 4 == 1
            cache.prefetch_gpu_session_nodes.assert_called_once()
            reclaimed = next(
                row for row in records
                if row["event"] == "wait_prefetch_capacity_reclaimed"
            )
            assert reclaimed["capacity_reclaim_probe"]
        else:
            cache.prefetch_gpu_session_nodes.assert_not_called()
            assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("page_size", (1, 2))
@pytest.mark.parametrize("outcome", ("reclaimed", "no_victim", "reserve_grew"))
def test_zero_native_byte_budget_rechecks_actual_capacity_after_backed_probe(
    source, page_size, outcome,
):
    fixture = (
        provisional_join_batch_fixture() if source == "join_ticket"
        else batch_fixture(source)
    )
    runtime, key, opportunity, cache, _ = fixture
    runtime._h2d_samples.extend(((40, 10.),) * 3)
    if runtime._semantic_worker is not None:
        runtime._semantic_worker.error = ""
    runtime._native_page_size = page_size
    headroom = [replace(
        opportunity.headroom, device_full_free_tokens=page_size,
        device_mamba_free_slots=1,
    )]
    input_reserve = [0]
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    cache.beliefkv_join_pressure_candidates_by_component = {0: ((99, 100.),)}

    def budget(**kwargs):
        return native_residency_budget(
            running_requests=0, max_running_requests=48,
            available_request_rows=1, prefill_slots=1, page_size=page_size,
            input_token_reserve=input_reserve[0],
            full_free_tokens=headroom[0].device_full_free_tokens,
            mamba_free_slots=headroom[0].device_mamba_free_slots,
            full_bytes_per_token=10, mamba_bytes_per_slot=20,
            protected_bytes=0,
        )

    def reclaim(**kwargs):
        assert kwargs["exclude_node_ids"] == (0, 1, 2, 3, 4)
        assert kwargs["full_tokens"] == 2 * page_size
        assert kwargs["mamba_slots"] == 1
        assert runtime.physical_ledger.pending_count == 0
        if outcome == "no_victim":
            return {}
        headroom[0] = replace(headroom[0], device_full_free_tokens=page_size + 4)
        if outcome == "reserve_grew":
            input_reserve[0] = 4
        return {0: 4}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    original_enqueue = cache.prefetch_gpu_session_nodes.side_effect

    def enqueue(**kwargs):
        assert budget().byte_limit == 40
        outcomes = original_enqueue(**kwargs)
        headroom[0] = replace(
            headroom[0],
            device_full_free_tokens=headroom[0].device_full_free_tokens - 4 * len(outcomes),
        )
        return outcomes

    cache.prefetch_gpu_session_nodes.side_effect = enqueue
    try:
        assert budget().byte_limit == 0
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          side_effect=lambda **kwargs: replace(
                              opportunity, headroom=headroom[0],
                          )), \
            patch.object(runtime, "_current_residency_budget", side_effect=budget), \
            patch.object(runtime, "_publish_parent_pressure_candidates"), \
            patch.object(runtime, "_live_parent_pressure_node", return_value=True), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )):
            if source == "tool_wait":
                runtime.dispatch_tool_prefetch()
            else:
                runtime.dispatch_join_prefetch()
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert runtime.counts["wait_prefetch_backed_capacity_probe"] == 1
        assert not runtime.physical_disabled
        expected = outcome == "reclaimed"
        assert runtime.physical_ledger.pending_count == int(expected)
        if expected:
            cache.prefetch_gpu_session_nodes.assert_called_once()
            assert tuple(
                node[0] for node in cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]
            ) == (1,)
            assert headroom[0].device_full_free_tokens == page_size
            assert budget().byte_limit == 0
        else:
            cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", ("not_cold", "excluded", "before_start_window"))
@pytest.mark.parametrize("native_zero_bytes", (False, True))
def test_zero_budget_reclaim_probe_does_not_evict_without_a_live_timed_opportunity(
    blocked, native_zero_bytes,
):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    runtime._h2d_samples.extend(((40, 10.),) * 3)
    cache.beliefkv_join_pressure_candidates_by_component = {
        0: ((1 if blocked == "excluded" else 99, 100.),),
    }
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={0: 4})
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          return_value=replace(
                              opportunity, headroom=replace(
                                  opportunity.headroom, device_full_free_tokens=1,
                              ),
                          )), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(
                             1, 0 if native_zero_bytes else 1024, reserve_full_tokens=1,
                             source="native_next_prefill" if native_zero_bytes else
                             "legacy_capacity_unavailable",
                         )), \
            patch.object(runtime, "_live_parent_pressure_node",
                         return_value=blocked != "not_cold"), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(10., 0., 50.)):
            runtime.dispatch_tool_prefetch()
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
def test_explicit_zero_byte_budget_does_not_authorize_backed_capacity_probe(source):
    fixture = (
        provisional_join_batch_fixture() if source == "join_ticket"
        else batch_fixture(source)
    )
    runtime, _, opportunity, cache, _ = fixture
    runtime._h2d_samples.extend(((40, 10.),) * 3)
    cache.beliefkv_join_pressure_candidates_by_component = {0: ((99, 100.),)}
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={0: 4})
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          return_value=replace(
                              opportunity, headroom=replace(
                                  opportunity.headroom, device_full_free_tokens=1,
                              ),
                          )), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(
                             1, 0, reserve_full_tokens=1,
                         )), \
            patch.object(runtime, "_live_parent_pressure_node", return_value=True), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)):
            if source == "tool_wait":
                runtime.dispatch_tool_prefetch()
            else:
                runtime.dispatch_join_prefetch()
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("byte_limit,tail_target", ((1024, 8), (60, 6)))
@pytest.mark.parametrize("wait_ends_after_enqueue", (False, True))
def test_partial_enqueue_reclaims_only_the_bounded_safe_input_tail_without_waiting(
    source, byte_limit, tail_target, wait_ends_after_enqueue,
):
    fixture = (
        provisional_join_batch_fixture() if source == "join_ticket"
        else batch_fixture(source)
    )
    runtime, key, original, cache, _ = fixture
    runtime._h2d_samples.extend(((20, 10.), (40, 10.)) * 3)
    if runtime._semantic_worker is not None:
        runtime._semantic_worker.error = ""
    nodes = original.candidate.nodes[:3] + (
        prefetch_node(3, 2, 4, full_host=4, mamba_host=True, key_tokens=4),
        prefetch_node(4, 3, 5, full_host=4, key_tokens=4),
    )
    anchors = replace(
        original.anchors,
        component_leaves=((0, ((4, 5),)), (2, ((3, 4),))),
        reusable_input_tokens=12,
    )
    candidate = replace(original.candidate, anchors=anchors, nodes=nodes)
    steps = plan_prefetch_gpu_steps(candidate)
    assert len(steps) == 3
    opportunity = replace(
        original, anchors=anchors, candidate=candidate, steps=steps, step=steps[0],
        required_full_tokens=12,
        host_backed_full_missing_device_tokens=16,
        headroom=replace(original.headroom, device_full_free_tokens=5),
    )
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

    def reclaim(**kwargs):
        assert runtime.physical_ledger.pending_count == 1, (dict(runtime.counts), records)
        assert kwargs["full_tokens"] == tail_target + 1
        assert kwargs["mamba_slots"] == 1
        assert kwargs["exclude_node_ids"] == (0, 1, 2, 3, 4)
        assert not runtime.completed_physical_actions
        return {0: tail_target}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    original_enqueue = cache.prefetch_gpu_session_nodes.side_effect

    def enqueue(**kwargs):
        outcomes = original_enqueue(**kwargs)
        if wait_ends_after_enqueue:
            if source == "tool_wait":
                runtime._tool_ticket = None
            else:
                runtime._join_ticket = None
        return outcomes

    cache.prefetch_gpu_session_nodes.side_effect = enqueue
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          return_value=opportunity), \
             patch.object(runtime, "_current_residency_budget",
                          return_value=PrefetchResidencyBudget(
                              1, byte_limit, reserve_full_tokens=1, reserve_mamba_slots=1,
                          )), \
             patch.object(runtime, "_publish_parent_pressure_candidates"), \
             patch.object(runtime, "_h2d_start_window",
                          return_value=TransferStartWindow(200., 0., 500.)):
            if source == "tool_wait":
                runtime.dispatch_tool_prefetch()
            else:
                runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == 1
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert not cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"][0][2]
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
        if wait_ends_after_enqueue:
            cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        else:
            cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
            row = next(
                row for row in records
                if row["event"] == "wait_prefetch_tail_capacity_reclaimed"
            )
            assert row["safe_input_tail_full_tokens"] == 8
            assert row["budgeted_tail_full_tokens"] == tail_target
            assert row["reserve_full_tokens"] == 1
            assert row["freed_units"] == {0: tail_target}
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize(
    "case,free_full,free_mamba,byte_limit",
    (
        ("fit", 17, 2, 180),
        ("full_reserve_shortfall", 16, 2, 180),
        ("mamba_reserve_shortfall", 17, 1, 180),
        ("byte_shortfall", 17, 2, 179),
        ("unobservable", 17, 2, 180),
        ("unobservable_missing_pool", 17, 2, 180),
        ("partial_extent", 17, 2, 180),
        ("capacity_probe", 17, 2, 180),
        ("nothing_freed", 17, 2, 180),
    ),
)
def test_successful_reclaim_reuses_only_a_complete_reserved_burst_plan(
    source, case, free_full, free_mamba, byte_limit,
):
    runtime, key, original, cache, _ = batch_fixture(source)
    opportunity = replace(
        original, fits_current_free_lists=False,
        capacity_budget={"requested_full_tokens": 16, "requested_mamba_slots": 1},
        partial_full_step=original.step if case == "partial_extent" else None,
        partial_full_tokens=2 if case == "partial_extent" else 0,
        capacity_reclaim_probe=case == "capacity_probe",
    )
    headroom = replace(
        original.headroom, observable=not case.startswith("unobservable"),
        device_full_free_tokens=free_full, device_mamba_free_slots=free_mamba,
    )
    diagnosis = {"freed_units": {0: 8}}
    cache.beliefkv_handoff_last_capacity = diagnosis
    cache.reclaim_beliefkv_handoff_capacity = Mock(
        return_value={} if case == "nothing_freed" else {0: 8},
    )
    if case == "unobservable_missing_pool":
        cache.cache_controller.mem_pool_host.entry_map.pop("mamba")
    fallback = replace(
        original, step=None, steps=(), no_step_reason="fresh_target_unavailable",
    )
    try:
        with patch.object(runtime, "_publish_parent_pressure_candidates"), \
             patch.object(runtime, "_wait_prefetch_opportunity", return_value=fallback) as inspect, \
             patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
                 1, byte_limit, reserve_full_tokens=1, reserve_mamba_slots=1,
             )), \
             patch("beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
                   return_value=headroom):
            result = runtime._reclaim_wait_prefetch_capacity(
                key, opportunity, source=source,
            )
        assert result.capacity_reclaim == {**diagnosis, "waiting_candidate_checks": []}
        if case == "fit":
            inspect.assert_not_called()
            assert result.steps == original.steps
            assert result.candidate is original.candidate
            assert result.headroom is headroom
            assert result.fits_current_free_lists
            assert result.capacity_budget["full_limit_tokens"] == 16
            assert result.capacity_budget["mamba_limit_slots"] == 1
            assert result.capacity_budget["remaining_byte_limit"] == 180
            assert result.capacity_budget["capacity_refresh_source"] == "post_reclaim_free_lists"
            assert runtime.counts["wait_prefetch_reclaim_plan_reused"] == 1
        else:
            inspect.assert_called_once_with(key, fit_current_capacity=True)
            assert result.step is None
            assert result.no_step_reason == "fresh_target_unavailable"
            assert not runtime.counts["wait_prefetch_reclaim_plan_reused"]
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("changed", (False, True))
def test_reused_reclaim_plan_still_recaptures_target_before_native_enqueue(changed):
    runtime, key, original, cache, _ = batch_fixture("tool_wait")
    original = replace(
        original, headroom=replace(original.headroom, device_full_free_tokens=1),
    )
    after = replace(original.headroom, device_full_free_tokens=100)
    final = original
    if changed:
        steps = (replace(original.steps[0], creation_time=100),) + original.steps[1:]
        final = replace(original, step=steps[0], steps=steps)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={0: 99})
    reads = []

    def inspect(**kwargs):
        reads.append(kwargs)
        return original if len(reads) == 1 else replace(final, headroom=after)

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
             patch.object(runtime, "_publish_parent_pressure_candidates"), \
             patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
                 1, 1024, reserve_full_tokens=1, reserve_mamba_slots=1,
                 evictable_full_tokens=100,
             )), \
             patch.object(runtime, "_h2d_start_window",
                          return_value=TransferStartWindow(200., 0., 500.)), \
             patch("beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
                   return_value=after):
            runtime.dispatch_tool_prefetch()
        assert runtime.counts["wait_prefetch_reclaim_plan_reused"] == 1
        assert len(reads) == 2
        assert runtime.counts["prefetch_burst_plan_changed"] == int(changed)
        assert cache.prefetch_gpu_session_nodes.call_count == int(not changed)
        assert runtime.physical_ledger.pending_count == (0 if changed else 4)
        assert not runtime.physical_disabled
    finally:
        runtime.close()
