from copy import deepcopy

import pytest

from scripts.audit_wait_safe_prefix import audit_safe_prefix, host_interval_at_safe_cap


@pytest.mark.parametrize(
    "device,host,cap,inside,outside",
    [
        (100, 80, 120, 20, 60),
        (100, 80, 99, 0, 80),
        (100, 80, 180, 80, 0),
        (100, 80, 200, 80, 0),
        (0, 80, 0, 0, 80),
        (100, 0, 120, 0, 0),
    ],
)
def test_host_interval_partition(device, host, cap, inside, outside):
    native = {"attributes": {"cached_tokens_device": device, "cached_tokens_host": host}}
    result = host_interval_at_safe_cap(native, cap)
    assert result["host_hit_tokens_within_safe_input_cap"] == inside
    assert result["host_hit_tokens_beyond_safe_input_cap"] == outside
    assert inside + outside == host


@pytest.mark.parametrize("bad", [None, -1, True, 1.5, "12"])
def test_unknown_or_invalid_cap_does_not_become_zero(bad):
    native = {"attributes": {"cached_tokens_device": 1, "cached_tokens_host": 10}}
    assert host_interval_at_safe_cap(native, bad) is None


def fixture():
    wait = {
        "source": "join_ticket", "workflow_id": "workflow", "context_id": "context",
        "invocation_id": "invocation", "context_epoch": 0, "join_id": "join",
        "completion_ts_ms": 100, "next_request_id": "request",
        "first_service_ts_ms": 130,
    }
    plan = {
        **wait, "event": "wait_prefetch_plan", "ts_ms": 90,
        "context_epoch": 1, "reusable_input_tokens": 120,
        "reason": "no_restore_plan", "no_step_reason": "no_reusable_input_restore_step",
        "host_backed_full_missing_device_tokens": 0,
        "host_only_mamba_nodes_in_observed_closure": 1,
        "unbacked_full_nodes": 0,
    }
    native = {
        **wait, "kind": "llm_submit", "context_epoch": 1,
        "attributes": {
            "request_id": "request", "cached_tokens_device": 100,
            "cached_tokens_host": 80,
        },
    }
    return wait, plan, native


def test_last_strict_pre_completion_plan_and_join_epoch_transition():
    wait, plan, native = fixture()
    future = {**plan, "ts_ms": 100, "reusable_input_tokens": 200}
    older = {**plan, "ts_ms": 89, "reusable_input_tokens": 110}
    result = audit_safe_prefix([wait], [future, plan, older], [native])
    row = result["rows"][0]
    assert row["safe_input_cap_tokens"] == 120
    assert row["plan_to_first_service_ms"] == 40
    assert row["host_interval"]["host_hit_tokens_within_safe_input_cap"] == 20
    group = result["by_source"]["join_ticket"]["by_last_pre_completion_reason"]
    assert group["no_reusable_input_restore_step"]["expanded_closure_without_host_only_full_waits"] == 1


def test_tool_round_does_not_borrow_other_epochs_plan():
    wait, plan, native = fixture()
    wait.update(source="tool_wait", join_id=None)
    plan.update(source="tool_wait", join_id=None)
    result = audit_safe_prefix([wait], [plan], [native])
    assert result["rows"][0]["safe_input_cap_tokens"] is None


def test_missing_native_result_is_unknown():
    wait, plan, _ = fixture()
    result = audit_safe_prefix([wait], [plan], [])
    assert result["rows"][0]["host_interval"] is None
    group = result["by_source"]["join_ticket"]["by_last_pre_completion_reason"]
    assert group["no_reusable_input_restore_step"]["unknown_safe_cap_or_native_hit_waits"] == 1


def test_empty_shortcut_is_not_an_expanded_closure_observation():
    wait, plan, native = fixture()
    plan.update(no_step_reason="no_host_backed_missing_copy", unbacked_full_nodes=None)
    result = audit_safe_prefix([wait], [plan], [native])
    assert result["rows"][0]["latest_plan_expanded_closure"] is False
    group = result["by_source"]["join_ticket"]["by_last_pre_completion_reason"]
    assert group["no_host_backed_missing_copy"]["expanded_closure_waits"] == 0


def test_wrong_context_native_binding_is_rejected():
    wait, plan, native = fixture()
    native["context_id"] = "other"
    with pytest.raises(ValueError, match="identity mismatch"):
        audit_safe_prefix([wait], [plan], [native])


def test_duplicate_request_or_wait_is_rejected():
    wait, plan, native = fixture()
    with pytest.raises(ValueError, match="duplicate native"):
        audit_safe_prefix([wait], [plan], [native, deepcopy(native)])
    with pytest.raises(ValueError, match="duplicate wait"):
        audit_safe_prefix([wait, deepcopy(wait)], [plan], [native])
