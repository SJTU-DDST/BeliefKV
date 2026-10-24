from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from tests.test_wait_prefetch_batch import provisional_join_batch_fixture
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import plan_prefetch_gpu_steps


@pytest.mark.parametrize("layout", ["full_only", "hybrid", "missing_required_state"])
def test_eos_transfer_size_reads_only_pools_needed_by_the_restore(layout):
    runtime, _key, original, cache, _ack = provisional_join_batch_fixture()
    runtime._borrowable_parent_headroom = False
    opportunity = replace(
        original, required_mamba_slots=1 if layout == "missing_required_state" else 0,
    )
    if layout != "hybrid":
        cache.cache_controller.mem_pool_host.entry_map.pop("mamba")
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=opportunity):
            selected = runtime._roll_final_stage()
        if layout == "missing_required_state":
            assert selected is None
            assert runtime._join_ticket is None
        else:
            assert selected is opportunity
            assert runtime._join_ticket is not None
            assert runtime._join_ticket.phase == "provisional"
            assert runtime.counts["final_stage_latest_start"] == 1
    finally:
        runtime.close()


def test_full_only_eos_submits_real_native_commands_before_join_completion():
    runtime, _key, original, cache, reserved = provisional_join_batch_fixture()
    runtime._borrowable_parent_headroom = False
    cache.cache_controller.mem_pool_host.entry_map.pop("mamba")
    anchors = replace(
        original.anchors,
        component_leaves=tuple(
            item for item in original.anchors.component_leaves if item[0] == 0
        ),
    )
    candidate = replace(
        original.candidate, anchors=anchors, missing_mamba_device_nodes=0,
        nodes=tuple(NS(**{
            **vars(node), "mamba_host_present": False, "mamba_device_present": False,
        }) for node in original.candidate.nodes),
    )
    steps = plan_prefetch_gpu_steps(candidate)
    opportunity = replace(
        original, anchors=anchors, candidate=candidate, step=steps[0], steps=steps,
        required_mamba_slots=0,
        headroom=replace(original.headroom, device_mamba_free_slots=0),
    )
    try:
        with patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=opportunity,
        ), patch.object(
            runtime, "_current_residency_budget",
            return_value=PrefetchResidencyBudget(1, 1024**3),
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert len(reserved) == len(steps) == 4
        assert runtime._join_ticket.phase == "provisional"
        assert not runtime.graph.joins["join"].satisfied
        assert runtime.physical_ledger.pending_count == 4
        for command in reserved:
            expected = runtime.physical_ledger.pending_expectation(command)
            assert expected.children[0].pool_bytes == (("kv", 40),)
        assert not runtime.physical_disabled
    finally:
        runtime.close()
