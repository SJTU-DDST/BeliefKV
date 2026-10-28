from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.runtime.native_transfer_policy import consumer_input_reserve
from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_policy_adaptation import demand_runtime


@pytest.mark.parametrize("state", ("empty", "selected", "unknown", "dllm", "legacy", "bad_bound"))
def test_actual_input_replaces_only_known_unselected_batch_bound(state):
    runtime = NS(
        _native_page_size=8, _native_running_batch=NS(reqs=[object()] * 3),
        _native_input_reserve=4096 if state != "bad_bound" else 8192,
    )
    budget = NS(reserve_full_tokens=4128, source="legacy" if state == "legacy" else "native_next_prefill")
    adder = None if state == "unknown" else NS(
        can_run_list=[object()] if state == "selected" else [],
        dllm_config=object() if state == "dllm" else None,
    )
    reserve = consumer_input_reserve(runtime, budget, 1, adder=adder)
    assert reserve.replaced_batch_input is (state == "empty")
    assert reserve.input_tokens == 1
    assert reserve.growth_tokens == (32 if state == "empty" else 4128)
    assert reserve.total_tokens == (33 if state == "empty" else 4129)


@pytest.mark.parametrize("selected", (False, True))
def test_fitting_consumer_does_not_trigger_tool_eviction_from_speculative_input_budget(
    demand_runtime, monkeypatch, selected,
):
    import beliefkv.runtime.predicted_tool_offload as module

    runtime, offloader, _, _, _, request, _ = demand_runtime
    key = runtime.visible[request.rid]
    runtime._read_request_reentry = Mock(return_value={"missing_full_tokens": 1024})
    runtime._request_restore_cost = Mock(return_value=(1024 * 64, 1))
    runtime._current_residency_budget = Mock(return_value=NS(
        reserve_full_tokens=4128, source="native_next_prefill",
    ))
    runtime._native_page_size = 8
    runtime._native_running_batch = NS(reqs=[object()] * 3)
    runtime._native_input_reserve = 4096
    offloader._admission_adder = NS(
        can_run_list=[object()] if selected else [], dllm_config=None,
    )
    monkeypatch.setattr(module, "observe_static_full_mamba_headroom", lambda _: NS(
        observable=True, device_full_free_tokens=2048,
    ))
    result = offloader._demand(request, key)
    assert result == ((key, 3105, 1024 * 64) if selected else None)
    assert runtime.counts["tool_offload_unnecessary_deficit_avoided"] == int(not selected)
