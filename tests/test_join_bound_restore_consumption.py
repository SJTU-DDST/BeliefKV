"""A JOIN restore issued for a bound consumer must follow that same request."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from tests.test_sglang_v0520_runtime import req, select
from tests.test_tool_predictive_transfers import locked_runtime, submit_restored_request


def bound_restore_fixture():
    runtime, _, node, action, receipt, _ = locked_runtime()
    target = submit_restored_request(runtime)
    consumer = runtime.visible[target.rid]
    lease = runtime._prefetch_service_leases[action.command_id]
    runtime._prefetch_service_leases[action.command_id] = replace(
        lease, key=consumer, source="join_ticket", demand_ready=False,
        reentry_ready_at=None, client_submitted_at=None,
        expires_at=lease.acknowledged_at + 2.,
    )
    runtime._lease_refresh_signature = None
    return runtime, consumer, target, node, action, receipt


def test_exact_bound_join_consumer_gets_existing_demand_lease_and_bounded_priority():
    runtime, key, target, _, action, receipt = bound_restore_fixture()
    lease = runtime._prefetch_service_leases[action.command_id]
    ordinary = req("ordinary")
    runtime.register_visible_request(ordinary)
    try:
        with patch("time.monotonic", return_value=lease.acknowledged_at + .1):
            runtime._refresh_prefetch_service_leases()
            observed = runtime._prefetch_service_leases[action.command_id]
            assert observed.key == key
            assert observed.demand_ready
            assert observed.expires_at == lease.acknowledged_at + 10.
            with patch.object(runtime, "_causal_rank", side_effect=lambda *_args: (0, 0)):
                assert select(runtime, [ordinary, target]).candidates[0] is target
                runtime.on_prefill_candidate_result(target, admitted=True, result="ok")
                for _ in range(4):
                    assert select(runtime, [ordinary, target]).candidates[0] is ordinary
                    runtime.on_prefill_candidate_result(ordinary, admitted=True, result="ok")
                assert select(runtime, [ordinary, target]).candidates[0] is target
            runtime.on_batch_completed(NS(reqs=[target]))
        assert not runtime._prefetch_service_leases
        runtime._native_cache.tree_core.dec_lock_ref.assert_called_once_with(1, receipt)
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "tool_source", "not_current", "not_visible", "ready", "active_tool",
    "active_child", "attempt", "generation", "context_epoch", "terminal",
))
def test_same_request_exception_requires_current_executable_join_identity(change):
    runtime, key, target, _, action, _ = bound_restore_fixture()
    lease = runtime._prefetch_service_leases[action.command_id]
    try:
        if change == "tool_source":
            runtime._prefetch_service_leases[action.command_id] = replace(lease, source="tool_wait")
        elif change == "not_current":
            runtime.context_sessions[key.context_id] = replace(key, request_id="different")
        elif change == "not_visible":
            runtime.visible.pop(key.request_id)
        elif change == "ready":
            runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
        elif change == "active_tool":
            runtime.graph.invocations[key.invocation_id].active_tool_calls = {"tool": ("shell", 1.)}
        elif change == "active_child":
            runtime.graph.invocations[key.invocation_id].blocking_child_ids = {"child"}
        elif change == "attempt":
            runtime.context_sessions[key.context_id] = runtime.visible[key.request_id] = replace(
                key, attempt_id=key.attempt_id + 1,
            )
        elif change == "generation":
            runtime.context_sessions[key.context_id] = runtime.visible[key.request_id] = replace(
                key, session_generation=key.session_generation + 1,
            )
        elif change == "context_epoch":
            runtime.graph.contexts[key.context_id].epoch += 2
        else:
            runtime.graph.invocations[key.invocation_id].state = InvocationState.DONE
        with patch("time.monotonic", return_value=lease.acknowledged_at + .1):
            runtime._refresh_prefetch_service_leases()
        assert not any(item.demand_ready for item in runtime._prefetch_service_leases.values())
        assert not runtime.physical_disabled
    finally:
        runtime.close()
