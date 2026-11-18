from copy import deepcopy
import json

import pytest

from scripts import review_materialization_runtime as readout
from scripts.review_materialization_runtime import (
    NativeWindows, SUPPLY_FIELDS, integrate_supply, supply_samples,
    verified_performance,
)


def event(kind, when, owner="a", **attributes):
    return {
        "kind": kind, "ts_ms": when, "workflow_id": "w",
        "invocation_id": owner, "attributes": attributes,
    }


def state(when, *, held=(), gate=True, active=(), paused=("a",)):
    return {
        "monotonic_ms": when, "ts_ms": when + 100000., "physical_disabled": False,
        "agent_admission": {
            "active_invocation_ids": list(active), "paused_invocation_ids": list(paused),
            "capacity_tokens": 100, "logical_active_tokens": 0,
        },
        "subagent_materialization": {"held_request_ids": list(held)} if gate else None,
    }


def source():
    return {
        "first": {"llm_submit": [["w", "a", "c", 0]]},
        "next": {"llm_submit": [["w", "a", "c", 1]]},
    }


def test_jct_mean_uses_exact_member_values_rather_than_summing_distribution_fields():
    cohort = {"measured_workflow_ids": ["short", "long"]}
    values = {"short": 1000., "long": 3000.}
    report = {
        "cohort": cohort, "all_measured_completed": True, "normal_tails_removed": False,
        "measured_workflow_JCT_ms": values,
        "measured_complete_workflow_JCT_ms": {
            "count": 2, "mean": 2000., "p50": 2000., "p75": 2500., "p90": 2800.,
            "p95": 2900., "p99": 2980., "max": 3000.,
        },
        "common_window_output_tokens_per_second": 20.,
        "common_window_decode_tokens_per_second": 19.,
    }
    result = verified_performance(report, cohort, {"measured_JCT_ms": values})
    assert result["mean_complete_measured_JCT_s"] == 2.
    with pytest.raises(ValueError):
        verified_performance(report, cohort, {"measured_JCT_ms": {"short": 1000., "long": 2000.}})
    report["measured_complete_workflow_JCT_ms"]["mean"] = 1.
    with pytest.raises(ValueError):
        verified_performance(report, cohort, {"measured_JCT_ms": values})


def test_live_held_initial_child_does_not_count_as_existing_continuation_supply():
    events = [event("workflow_start", 0), event("llm_submit", 1, request_id="first")]
    rows, _ = supply_samples(
        events, [state(10, held=("first",)), state(1010, held=("first",))],
        {"arrival_schedule": [{"workflow_id": "w", "offset_seconds": 0}]},
        source(), gate_enabled=True,
    )
    assert rows[0]["ready_paused"] == 1
    assert rows[0]["held_ready_paused"] == 1
    assert rows[0]["ready_paused_excluding_held"] == 0
    assert rows[0]["continuation_ready_paused"] == 0


def test_real_completed_prefix_continuation_remains_supply_while_tool_blocker_does_not():
    events = [
        event("workflow_start", 0), event("llm_submit", 1, request_id="first"),
        event("llm_result", 2, request_id="first"),
        event("tool_start", 3, tool_run_id="t"),
        event("llm_submit", 4, request_id="next"), event("tool_end", 20, tool_run_id="t"),
    ]
    rows, _ = supply_samples(
        events, [state(10), state(1010)],
        {"arrival_schedule": [{"workflow_id": "w", "offset_seconds": 0}]},
        source(), gate_enabled=True,
    )
    assert rows[0]["tool_paused"] == 1
    assert rows[0]["continuation_ready_paused"] == 0
    assert rows[1]["continuation_ready_paused"] == 1
    assert rows[1]["continuation_ready_workflows"] == 1


@pytest.mark.parametrize("change", ("missing_status", "held_continuation", "unknown", "off"))
def test_unknown_or_inconsistent_gate_identity_is_rejected(change):
    events = [
        event("workflow_start", 0), event("llm_submit", 1, request_id="first"),
        event("llm_result", 2, request_id="first"), event("llm_submit", 3, request_id="next"),
    ]
    states = [state(10), state(1010)]
    gate = change != "off"
    if change == "missing_status":
        states[0]["subagent_materialization"] = None
    else:
        states[0]["subagent_materialization"]["held_request_ids"] = [
            "unknown" if change == "unknown" else "next"
        ]
    with pytest.raises(ValueError):
        supply_samples(
            events, states, {"arrival_schedule": [{"workflow_id": "w", "offset_seconds": 0}]},
            source(), gate_enabled=gate,
        )


def test_time_weighting_clips_boundaries_and_does_not_fill_a_long_observation_gap():
    rows = [
        {"offset_s": when, **dict.fromkeys(SUPPLY_FIELDS, count)}
        for when, count in ((0., 2), (1., 4), (4., 8), (5., 16))
    ]
    result = integrate_supply(rows, (.5, 4.5))
    assert result["covered_s"] == 1
    assert result["coverage_fraction"] == .25
    assert result["time_weighted_mean"]["continuation_ready_paused"] == 5


def native_row(forward, start, end, *, phase="decode", size=2):
    return {
        "event": "gpu_service_sample", "forward_iter": forward,
        "phase": phase, "service_start_monotonic_ms": start,
        "complete_monotonic_ms": end, "complete_ts_ms": end + 10000,
        "monotonic_clock_domain": "same", "service_elapsed_ms": end - start,
        "batch_size": size, "cuda_graph_replayed": True,
        "request_samples": [
            {"request_id": "r" + str(index), "sequence_tokens_before": 10,
             "token_delta": 1, "token_delta_semantics": "observed_output_ids_delta"}
            for index in range(size)
        ],
    }


def test_native_intervals_are_unioned_but_decode_geometry_counts_each_forward_once():
    native = NativeWindows({"common": (.5, 2.5)}, 0, [1, 2, 4], disable_padding=False)
    native.observe(native_row(1, 0, 1000))
    native.observe(native_row(2, 800, 2000))
    native.observe(native_row(3, 2000, 3000, phase="prefill"))
    result = native.report(3)["common"]
    assert result["native_worker_interval_union_s"] == 2.
    assert result["no_native_worker_interval_s"] == 0.
    assert result["decode"]["batches"] == 1
    assert result["decode"]["mean_batch"] == 2


def test_migration_exposure_preserves_ack_uncertainty_at_both_window_boundaries():
    def transfer(command, timestamp):
        return {
            "event": "transfer_telemetry", "status": "completed", "direction": "h2d",
            "command_id": command, "complete_ts_ms": timestamp,
            "num_tokens_by_pool": {"kv": 2}, "actual_bytes": 16,
        }

    records = [transfer(str(i), when) for i, when in enumerate((1009., 1011., 1014., 1020., 1022.))]
    settings = dict(expected=5, bytes_per_token=8, window_ms=(1000., 1010.), offsets=(10., 12.))
    report = readout.completed_migrations(records, **settings)
    assert report["observed"]["h2d"]["tokens"] == 10
    assert report["common"]["h2d"]["records"] == 1
    assert report["ambiguous_boundary"]["h2d"]["records"] == 2
    assert report["common"]["d2h"]["bytes"] == 0
    with pytest.raises(ValueError, match="record count"):
        readout.completed_migrations(records[:-1], **settings)
    records[-1]["command_id"] = "0"
    with pytest.raises(ValueError, match="identity"):
        readout.completed_migrations(records, **settings)


def test_host_loss_does_not_bill_unresolved_attribution_as_executed_prefill():
    case = {
        "request_id": "next", "workflow_id": "w", "invocation_id": "a", "context_id": "c",
        "context_epoch": 1, "distinct_attributed_tokens": 8,
        "identity_and_geometry_supported": True, "distinct_observed_prefill_tokens": 7,
        "sampled_lost_prefix_tokens": 8, "distinct_observed_prefill_tokens_in_window": 3,
        "sampled_lost_prefix_tokens_in_window": 4,
    }
    report = {"cases": [case]}
    prefill = {"common": 40, "measured": 100, "observed": 400}
    result = readout.host_loss_summary(report, prefill, source(), {"w"})
    assert result["common"]["distinct_Host_loss_tokens"] == 3
    assert result["common"]["Host_loss_prefill_fraction"] == .1
    assert result["measured"]["executed_Host_loss_tokens"] == 8
    case["identity_and_geometry_supported"] = False
    result = readout.host_loss_summary(report, prefill, source(), {"w"})
    assert result["common"]["executed_Host_loss_tokens"] == 0
    assert result["measured"]["unresolved_attributed_tokens_before_window_assignment"] == 8


def test_pressure_readout_requires_complete_comparable_work_and_service_window():
    control = {
        "registration": "control", "materialization_gate_enabled": False,
        "complete_measured_work_fresh_drain_actual_exit_verified": True,
        "measured_work_sha256": "work", "actual_capacity": {"host_full_tokens": 12},
        "wall_clock_performance": {
            "mean_complete_measured_JCT_s": 8., "common_window_output_tokens_per_second": 5.,
            "common_window_decode_tokens_per_second": 4.,
        },
        "kv_pressure": {"host_loss": {
            "common": {"executed_Host_loss_tokens": 20}, "measured": {"executed_Host_loss_tokens": 80},
        }},
        "native": {"common": {"window_offset_s": [1., 4.]}}, "supply": {}, "control_timing": {},
        "prefix_work": {"supported_distinct_old_prefix_tokens_measured": 80},
    }
    pressure = deepcopy(control)
    pressure.update(registration="pressure", materialization_gate_enabled=True)
    pressure["kv_pressure"]["host_loss"]["common"]["executed_Host_loss_tokens"] = 10
    pressure["wall_clock_performance"]["mean_complete_measured_JCT_s"] = 6.
    pair = readout.compare_pressure_readouts(control, pressure)
    assert pair["measured_mean_JCT_reduction_fraction"] == .25
    assert pair["executed_Host_loss_token_changes"] == {"common": -10, "measured": 0}
    pressure["native"]["common"]["window_offset_s"] = [1., 5.]
    with pytest.raises(ValueError, match="common window"):
        readout.compare_pressure_readouts(control, pressure)


@pytest.mark.parametrize("change", ("duplicate", "backward", "missing", "domain", "duration"))
def test_unhealthy_or_incomplete_native_observations_are_rejected(change):
    native = NativeWindows({"common": (0, 5)}, 0, [1, 2, 4], disable_padding=False)
    native.observe(native_row(1, 1000, 2000))
    row = native_row(2, 2000, 3000)
    if change == "duplicate":
        row["forward_iter"] = 1
    elif change == "backward":
        row = native_row(2, 500, 1500)
    elif change == "domain":
        row["monotonic_clock_domain"] = "other"
    elif change == "duration":
        row["service_elapsed_ms"] += 1
    with pytest.raises(ValueError):
        native.observe(row)
        native.report(3 if change == "missing" else 2)


def test_cpu_waiter_can_publish_a_second_heartbeat_and_preserves_interruption(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"stages": {"control": {}}}))
    (tmp_path / "state.json").write_text(json.dumps({"status": "waiting_resources_control"}))
    calls = []

    def tick(_seconds):
        calls.append(json.loads((tmp_path / "readout/state.json").read_text()))
        if len(calls) == 2:
            raise InterruptedError("bounded test stop")

    monkeypatch.setattr(readout.time, "sleep", tick)
    with pytest.raises(InterruptedError, match="bounded test stop"):
        readout.watch_pair(plan, tmp_path / "readout")
    assert len(calls) == 2
    assert all(row["status"] == "waiting_completed_control" for row in calls)
    final = json.loads((tmp_path / "readout/state.json").read_text())
    assert final["status"] == "failed_CPU_readout"
    assert final["GPU_action"] is False


@pytest.mark.parametrize("status", (
    "failed", "interrupted", "stopped_preserve_materialization_campaign",
    "interrupted_preserve_materialization_campaign",
))
def test_cpu_waiter_stops_when_the_watched_campaign_is_terminal(tmp_path, monkeypatch, status):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"stages": {"control": {}}}))
    (tmp_path / "state.json").write_text(json.dumps({"status": status}))

    def unexpected_wait(_seconds):
        raise AssertionError("a terminal campaign must not be polled again")

    monkeypatch.setattr(readout.time, "sleep", unexpected_wait)
    with pytest.raises(RuntimeError, match="original GPU campaign stopped: " + status):
        readout.watch_pair(plan, tmp_path / "readout")

    final = json.loads((tmp_path / "readout/state.json").read_text())
    assert final["status"] == "failed_CPU_readout"
    assert status in final["error"]
    assert final["completed_readouts"] == []
    assert final["GPU_action"] is False
