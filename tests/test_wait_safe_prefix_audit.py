from copy import deepcopy

import pytest

from scripts.audit_wait_safe_prefix import (
    audit_safe_prefix, handoff_dispatch_exposure, host_interval_at_safe_cap, native_lifetimes,
    queue_lifetime_exposure, wait_occupancy_targets,
)


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


def test_queue_wait_exposure_conserves_ties_and_zero_wait_requests():
    lifetimes = {
        "incumbent": {"queue": 0, "launch": 0, "finish": 10},
        "target": {"queue": 2, "launch": 8, "finish": 11},
        "tied": {"queue": 8, "launch": 8, "finish": 12},
    }
    result = queue_lifetime_exposure(
        lifetimes, {"targets": {"target", "tied", "missing"}}, request_limit=1,
    )
    group = result["groups"]["targets"]
    assert group["queue_to_first_launch_request_ms"] == 6
    assert group["request_ms_by_launched_unfinished"] == {1: 6}
    assert group["wait_fraction_at_or_above_limit"] == 1
    assert group["unknown_lifetime_targets"] == 1
    assert result["maximum_observed_launched_unfinished"] == 3


def test_queue_wait_counts_changes_in_incumbent_lifetimes():
    result = queue_lifetime_exposure({
        "a": {"queue": 0, "launch": 0, "finish": 5},
        "b": {"queue": 1, "launch": 2, "finish": 6},
        "target": {"queue": 1, "launch": 7, "finish": 8},
    }, {"targets": {"target"}}, request_limit=2)
    group = result["groups"]["targets"]
    assert group["request_ms_by_launched_unfinished"] == {0: 1, 1: 2, 2: 3}
    assert group["wait_fraction_at_or_above_limit"] == .5
    assert group["wait_fraction_at_or_above_limit_minus_one"] == pytest.approx(5 / 6)


@pytest.mark.parametrize(
    "row",
    [
        {"queue": 2, "launch": 1, "finish": 3},
        {"queue": 1, "launch": 3, "finish": 2},
        {"queue": 0, "launch": float("nan"), "finish": 2},
    ],
)
def test_queue_wait_rejects_invalid_lifetime(row):
    with pytest.raises(ValueError, match="lifetime ordering"):
        queue_lifetime_exposure({"target": row}, {"targets": {"target"}}, request_limit=48)


def test_native_lifetimes_match_monotonic_clock_and_keep_open_requests_unknown():
    _, _, native = fixture()
    native["ts_ms"] = 110
    end = {
        **native, "kind": "llm_result",
        "attributes": {
            "request_id": "request", "complete_monotonic_ms": 30,
            "monotonic_clock_domain": "clock",
        },
    }
    sample = {
        "event": "gpu_service_sample", "sample_id": "native-service-000000001",
        "launch_monotonic_ms": 20,
        "complete_monotonic_ms": 25, "complete_ts_ms": 125,
        "monotonic_clock_domain": "clock",
        "request_samples": [{
            **{key: native[key] for key in (
                "workflow_id", "invocation_id", "context_id", "context_epoch",
            )},
            "request_id": "request",
        }],
    }
    opened = {**native, "attributes": {**native["attributes"], "request_id": "open"}}
    result, coverage = native_lifetimes([native, end, opened], [sample])
    assert result == {"request": {"queue": 10, "launch": 20, "finish": 30}}
    assert coverage["requests_without_closed_observed_lifetime"] == 1
    assert coverage["missing_service_sequence_numbers"] == 0
    assert coverage["service_requests_outside_native_prefix_unique"] == 0
    end["attributes"]["monotonic_clock_domain"] = "other"
    with pytest.raises(ValueError, match="inconsistent native request lifetime"):
        native_lifetimes([native, end], [sample])


def test_native_service_prefix_gaps_and_unbound_requests_remain_explicit():
    service = {
        "event": "gpu_service_sample", "sample_id": "native-service-000000003",
        "launch_monotonic_ms": 20, "complete_monotonic_ms": 25,
        "complete_ts_ms": 125, "monotonic_clock_domain": "clock",
        "request_samples": [{"request_id": "not_in_native_prefix"}],
    }
    lifetimes, coverage = native_lifetimes([], [service])
    assert lifetimes == {}
    assert coverage["missing_service_sequence_numbers"] == 2
    assert coverage["service_requests_outside_native_prefix_unique"] == 1
    assert coverage["first_unbound_service_launch_monotonic_ms"] == 20
    with pytest.raises(ValueError, match="reversed service sequence"):
        native_lifetimes([], [service, deepcopy(service)])


def test_occupancy_targets_deduplicate_requests_within_overlapping_views():
    row = {
        "next_request_id": "request", "source": "tool_wait",
        "host_interval": {"host_hit_tokens_at_service": 10},
        "last_pre_completion_no_step_reason": "no_reusable_input_restore_step",
        "early_started_and_reused_full_bytes": 100,
    }
    targets = wait_occupancy_targets([row, deepcopy(row)])
    assert all(ids == {"request"} for ids in targets.values())
    assert len(targets) == 3


def dispatch_fixture():
    wait, _, native = fixture()
    wait.update(
        completion_ts_ms=210, client_submit_ts_ms=220, native_arrival_ts_ms=230,
        host_interval={"host_hit_tokens_at_service": 10},
        last_pre_completion_no_step_reason="prefetch_capacity_budget",
    )
    native["ts_ms"] = 230
    lifetimes = {"request": {"queue": 130, "launch": 150, "finish": 160}}
    natives = [native]
    for index, (when, workflow) in enumerate((
        (110, "other"), (115, "workflow"), (120, "other"),
        (125, "other"), (130, "other"), (145, "workflow"), (150, "other"),
    )):
        rid = f"other-{index}"
        lifetimes[rid] = {"queue": when, "launch": when, "finish": when + 5}
        natives.append({
            **native, "workflow_id": workflow,
            "attributes": {"request_id": rid},
        })
    return wait, lifetimes, natives


def test_handoff_counts_half_open_windows_and_excludes_target_and_decode_redispatches():
    wait, lifetimes, natives = dispatch_fixture()
    result = handoff_dispatch_exposure(lifetimes, [wait], natives)
    intervals = result["rows"][0]["intervals"]
    assert intervals["completion_to_client_submit"] == {
        "elapsed_ms": 10, "other_requests_first_launched": 2,
        "same_workflow_requests_first_launched": 1,
        "other_workflow_requests_first_launched": 1,
    }
    assert intervals["client_submit_to_native_arrival"]["other_requests_first_launched"] == 2
    assert intervals["native_arrival_to_first_launch"]["other_requests_first_launched"] == 2
    assert intervals["native_arrival_to_first_launch"]["elapsed_ms"] == 20
    assert result["groups"]["all_observed_next_requests"]["observed_wait_events"] == 1
    assert "join_ticket:prefetch_capacity_budget:later_host_hit" in result["groups"]


def test_zero_handoff_and_zero_queue_have_observed_zero_counts():
    wait, lifetimes, natives = dispatch_fixture()
    wait.update(completion_ts_ms=230, client_submit_ts_ms=230)
    lifetimes["request"]["launch"] = 130
    result = handoff_dispatch_exposure(lifetimes, [wait], natives)
    assert all(stage["elapsed_ms"] == stage["other_requests_first_launched"] == 0
               for stage in result["rows"][0]["intervals"].values())


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"completion_ts_ms": None}, "missing_or_invalid_boundary"),
        ({"client_submit_ts_ms": float("nan")}, "missing_or_invalid_boundary"),
        ({"completion_ts_ms": 225}, "unordered_boundaries"),
        ({"client_submit_ts_ms": 235}, "unordered_boundaries"),
    ],
)
def test_unknown_or_unordered_handoff_is_not_a_zero_wait(changes, reason):
    wait, lifetimes, natives = dispatch_fixture()
    wait.update(changes)
    result = handoff_dispatch_exposure(lifetimes, [wait], natives)
    group = result["groups"]["all_observed_next_requests"]
    assert group["observed_wait_events"] == 0
    assert group["excluded_wait_events"] == {reason: 1}
    assert result["rows"] == []


def test_open_native_lifetime_handoff_remains_unknown():
    wait, lifetimes, natives = dispatch_fixture()
    del lifetimes["request"]
    result = handoff_dispatch_exposure(lifetimes, [wait], natives)
    assert result["groups"]["all_observed_next_requests"]["excluded_wait_events"] == {
        "missing_closed_lifetime": 1,
    }


def test_handoff_rejects_arrival_or_identity_mismatch():
    wait, lifetimes, natives = dispatch_fixture()
    wait["native_arrival_ts_ms"] += 1
    with pytest.raises(ValueError, match="arrival mismatch"):
        handoff_dispatch_exposure(lifetimes, [wait], natives)
    wait["native_arrival_ts_ms"] -= 1
    wait["workflow_id"] = "wrong"
    with pytest.raises(ValueError, match="identity mismatch"):
        handoff_dispatch_exposure(lifetimes, [wait], natives)


def test_handoff_rejects_unbound_lifetime_and_duplicate_registration():
    wait, lifetimes, natives = dispatch_fixture()
    with pytest.raises(ValueError, match="without native registration"):
        handoff_dispatch_exposure(lifetimes, [wait], natives[:-1])
    with pytest.raises(ValueError, match="duplicate native registration"):
        handoff_dispatch_exposure(lifetimes, [wait], [*natives, deepcopy(natives[0])])


def test_distinct_waits_sharing_a_request_do_not_borrow_each_others_cohort():
    wait, lifetimes, natives = dispatch_fixture()
    other_wait = {
        **wait, "source": "tool_wait", "join_id": None,
        "last_pre_completion_no_step_reason": "no_reusable_input_restore_step",
        "early_started_and_reused_full_bytes": 100,
    }
    groups = handoff_dispatch_exposure(lifetimes, [wait, other_wait], natives)["groups"]
    assert groups["all_observed_next_requests"]["observed_wait_events"] == 2
    for name in (
        "join_ticket:prefetch_capacity_budget:later_host_hit",
        "tool_wait:no_reusable_input_restore_step:later_host_hit",
        "tool_wait:early_reused_full",
    ):
        assert groups[name]["observed_wait_events"] == 1
