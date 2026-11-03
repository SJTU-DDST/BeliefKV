from copy import deepcopy

import pytest

from scripts.audit_synchronous_control_costs import (
    bracket_counters, inspect_opportunities, match_probe_work, seconds, summarize_probe_rows,
    timing_counters,
)


def snapshot(when, amount):
    return {"monotonic_ms": when, "counters_seconds": {"admission.tool_cpu_ns": amount}}


def test_common_window_bounds_do_not_interpolate_or_double_count_cumulative_samples():
    rows = [snapshot(0., 0.), snapshot(2., 2.), snapshot(8., 5.), snapshot(10., 8.)]
    result = bracket_counters(rows, (1., 9.))
    assert result["counters"]["admission.tool_cpu_ns"] == {
        "latest_snapshot_cumulative_seconds": 8.,
        "common_window_accounted_counter_seconds": {"lower": 3., "upper": 8.},
    }


@pytest.mark.parametrize("change", ("time", "counter"))
def test_counter_resets_and_out_of_order_snapshots_are_rejected(change):
    rows = [snapshot(1., 2.), snapshot(2., 3.)]
    rows[1]["monotonic_ms" if change == "time" else "counters_seconds"] = (
        0. if change == "time" else {"admission.tool_cpu_ns": 1.}
    )
    with pytest.raises(ValueError):
        bracket_counters(rows, (1., 2.))


def test_missing_boundary_is_unknown_and_new_counter_starts_at_zero():
    result = bracket_counters([snapshot(2., 3.)], (1., 4.))
    assert not result["complete_common_window_brackets"]
    assert "common_window_accounted_counter_seconds" not in result["counters"]["admission.tool_cpu_ns"]


def test_counter_units_preserve_async_worker_separation_and_ignore_plain_counts():
    result = timing_counters({
        "agent_admission": {
            "counts": {"tool_event_replacement_cpu_ns": 1000000000, "control_ticks": 30},
            "workflow_work_predictor": {"counts": {"worker_thread_cpu_us": 2000000}},
        },
    })
    assert result == {
        "admission.tool_event_replacement_cpu_ns": 1.,
        "workflow_predictor.worker_thread_cpu_us": 2.,
    }
    assert seconds("control_and_observation_cpu_ns_before_write", 1000000000) == 1.
    with pytest.raises(ValueError):
        seconds("cpu_ns", float("nan"))


def test_missing_periodic_profile_is_unknown_not_a_zero_total():
    states = [{
        "event": "admission_runtime_state", "monotonic_ms": when,
        "final": False, "profile_shared_path": False,
        "agent_admission": {"counts": {"tool_event_replacement_cpu_ns": int(when * 1e6)}},
    } for when in (0., 10.)]
    result = inspect_opportunities(states, (1., 9.))
    assert result["latest_snapshot"]["profile_shared_path"] is False
    assert result["latest_snapshot"]["final"] is False
    assert "admission.workflow_size_control_cpu_us" not in result["counters"]


def test_sampled_phases_are_not_expanded_to_the_eligible_population():
    row = {
        "event": "prefill_turnover_sample", "monotonic_clock_domain": "same",
        "thread_cpu_clock_domain": "thread", "forward_iter": 1,
        "phases": {"run_batch": {
            "start_monotonic_ms": 10., "end_monotonic_ms": 12.,
            "wall_ms": 2., "thread_cpu_ms": 1.,
        }},
    }
    result = summarize_probe_rows([row], (5., 15.))
    phase = result["common_window"]["phases"]["run_batch"]
    assert phase["thread_cpu_ms"]["sum"] == 1.
    assert phase["thread_cpu_ms"]["count"] == 1
    assert not summarize_probe_rows([row], (15., 20.))["common_window"]["phases"]
    invalid = deepcopy(row)
    invalid["phases"]["run_batch"]["wall_ms"] = -1.
    with pytest.raises(ValueError):
        summarize_probe_rows([invalid], (5., 15.))
    with pytest.raises(ValueError, match="duplicate"):
        summarize_probe_rows([row, row], (5., 15.))


def test_adjacent_partition_uses_recorded_attribution_and_preserves_its_total():
    row = {
        "event": "prefill_turnover_sample", "monotonic_clock_domain": "same",
        "thread_cpu_clock_domain": "thread", "forward_iter": 1,
        "phases": {"run_batch": {
            "start_monotonic_ms": 10., "end_monotonic_ms": 12.,
            "wall_ms": 2., "thread_cpu_ms": 1.,
        }},
        "adjacent_transition": {"attribution": {
            "status": "observed", "wall_ms": 5., "thread_cpu_ms": 3.,
            "segments": [
                {"kind": "phase", "name": "successor_selection", "wall_ms": 4., "thread_cpu_ms": 2.},
                {"kind": "between_phases", "from": "successor_selection", "to": "observer",
                 "wall_ms": 1., "thread_cpu_ms": 1.},
            ],
        }},
    }
    result = summarize_probe_rows([row], (5., 15.))["common_window"]
    assert result["counts"]["complete_adjacent_attributions"] == 1
    assert result["adjacent_phase_segments"]["successor_selection"]["thread_cpu_ms"]["sum"] == 2.
    row["adjacent_transition"]["attribution"]["thread_cpu_ms"] = 8.
    with pytest.raises(ValueError, match="conserve"):
        summarize_probe_rows([row], (5., 15.))


@pytest.mark.parametrize("change", (None, "request", "geometry", "missing"))
def test_only_exact_native_work_can_qualify_a_phase_sample(change):
    probe = {
        "monotonic_clock_domain": "same", "forward_iter": 1,
        "request_ids": ["r"], "batch_size": 1, "extend_tokens": 4, "prefix_tokens": 12,
    }
    service = {
        "monotonic_clock_domain": "same", "forward_iter": 1, "batch_size": 1,
        "request_samples": [{"request_id": "r", "token_delta": 4, "sequence_tokens_before": 12}],
    }
    if change == "request":
        service["request_samples"][0]["request_id"] = "another"
    elif change == "geometry":
        service["request_samples"][0]["sequence_tokens_before"] = 20
    matched, excluded = match_probe_work([probe], {} if change == "missing" else {("same", 1): service})
    assert matched == ([probe] if change is None else [])
    assert len(excluded) == (0 if change is None else 1)
