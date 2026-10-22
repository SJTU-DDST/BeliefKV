import pytest

from scripts.audit_subagent_retention_capacity import (
    analyze, coexistence_scenario, headroom_checks, recompute_proxy, sweep,
)


def test_retention_sweep_uses_concurrent_intervals_and_clips_to_observed_window():
    result = sweep([
        (-1, 4, {"contexts": 1, "bytes": 10}),
        (2, 7, {"contexts": 1, "bytes": 30}),
        (7, 20, {"contexts": 1, "bytes": 20}),
        (9, 9, {"contexts": 1, "bytes": 1000}),
    ], 0, 10)
    assert result["peak"] == {"contexts": 2, "bytes": 40}
    assert result["time_mean"] == {"contexts": 1.2, "bytes": 25.}
    assert result["byte_seconds_or_context_seconds"]["bytes"] == pytest.approx(.25)


def test_recompute_proxy_excludes_unrelated_and_first_touch_records():
    report = recompute_proxy([
        {"event": "first_touch", "uncached_prompt_tokens": 1000000},
        {
            "event": "context_prefix_reuse_probe",
            "common_previously_served_input_tokens": 100000,
            "previously_served_prefix_recompute_proxy_tokens": 128,
        },
        {
            "event": "context_prefix_reuse_probe",
            "common_previously_served_input_tokens": 20000,
            "previously_served_prefix_recompute_proxy_tokens": 8192,
        },
    ])
    assert report["requests"] == 2
    assert report["missing_previously_served_prefix_proxy_tokens"] == 8320
    assert report["missing_fraction_of_common"] == pytest.approx(8320 / 120000)
    assert report["requests_missing_at_least_4096_tokens"] == 1
    assert report["missing_tokens_in_large_misses"] == 8192


def test_pressure_checks_do_not_confuse_free_tokens_with_activity_gauges():
    result = headroom_checks([
        {"event": "admission_runtime_state", "free_full_tokens_before": 1000000},
        {"event": "critical_parent_headroom", "capacity": {
            "free_full_tokens_before": 128, "target_full_tokens": 4096,
            "full_target_satisfied": False, "mamba_target_satisfied": True,
        }},
        {"event": "wait_prefetch_capacity_reclaimed", "capacity_reclaim": {
            "free_full_tokens_before": 64, "target_full_tokens": 512,
            "full_target_satisfied": True,
        }},
    ])
    assert result["checks"] == 2
    assert result["free_full_tokens_before"]["p50"] == 96
    assert result["full_target_unsatisfied"] == 1
    assert result["mamba_target_unsatisfied"] == 0


def test_coexistence_uses_time_aligned_peak_instead_of_adding_separate_peaks():
    host = [
        {"ts_ms": 0, "pools": {"full": {"used_bytes": 100}, "mamba": {"used_bytes": 10}}},
        {"ts_ms": 5, "pools": {"full": {"used_bytes": 20}, "mamba": {"used_bytes": 5}}},
    ]
    intervals = [(5, 10, {"served_input_no_sharing_full_bytes": 50,
                          "one_mamba_slot_per_context_bytes": 1})]
    result = coexistence_scenario(intervals, host, 0, 10)
    assert result["peak"]["served_input_full_accounting_bytes"] == 100
    assert result["peak"]["one_mamba_slot_per_context_bytes"] == 10
    assert result["time_mean"]["served_input_full_accounting_bytes"] == 85


def test_trace_audit_uses_release_time_and_replays_identical_prefixes(tmp_path):
    import json

    def records(relative, rows):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    capacity = {
        "device_full_bytes": 10000, "device_full_tokens": 100,
        "device_mamba_bytes": 1000, "device_mamba_slots": 10,
        "host_full_bytes": 50000, "host_full_tokens": 500,
        "host_mamba_bytes": 10000, "host_mamba_slots": 100,
    }
    census = tmp_path / "server/native_capacity_census.json"
    census.parent.mkdir()
    census.write_text(json.dumps({"capacity": capacity}))
    records("opportunities/admission_opportunities.jsonl", [
        {"event": "admission_runtime_state", "ts_ms": 1000, "monotonic_ms": 100},
        {"event": "terminal_child_cache_reclaimed", "ts_ms": 1500,
         "invocation_scope": "dynamic_subagent", "invocation_id": "child",
         "freed_device_units": {"0": 10}, "freed_host_units": {"0": 2},
         "bytes_per_unit": {"0": 100}},
        {"event": "admission_runtime_state", "ts_ms": 1800, "monotonic_ms": 900},
    ])
    records("server/host_pool_telemetry.jsonl", [
        {"event": "host_pool_usage", "ts_ms": timestamp, "pools": {
            pool: {"used_bytes": used, "used_fraction": .2, "high_water_bytes": used}
            for pool, used in (("full", 10000), ("mamba", 2000))
        }} for timestamp in (1000, 1200, 1500, 1800)
    ])
    records("client_1/sglang_metrics.jsonl", [
        {"monotonic_ts_ms": timestamp, "num_running_reqs": 1} for timestamp in (200, 900)
    ])
    native = records("server/runtime_events.sglang.jsonl", [
        {"kind": "llm_submit", "ts_ms": 1100, "invocation_id": "child", "context_id": "context",
         "attributes": {"request_id": "request", "prompt_tokens": 20}},
        {"kind": "llm_result", "ts_ms": 1300, "invocation_id": "child",
         "attributes": {"request_id": "request"}},
        {"kind": "llm_submit", "ts_ms": 1800, "invocation_id": "root", "context_id": "root-context",
         "attributes": {"request_id": "pending", "prompt_tokens": 50}},
    ])
    records("server/eviction_attribution.jsonl", [])
    records("client_1/workflows/workflow/runtime_events.deepagents.jsonl", [
        {"kind": "invocation_create", "ts_ms": 200, "invocation_id": "child",
         "workflow_id": "workflow", "relation_type": "spawn", "agent_instance_id": "handle"},
        {"kind": "return", "ts_ms": 500, "invocation_id": "child", "workflow_id": "workflow"},
        {"kind": "workflow_end", "ts_ms": 800, "workflow_id": "workflow"},
    ])
    report = analyze(tmp_path)
    retained = report["retain_until_workflow_end_counterfactual"]
    assert report["returned_dynamic_children"] == 1
    assert report["returned_child_completed_input_tokens"]["p50"] == 20
    assert retained["time_mean"]["served_input_no_sharing_full_bytes"] == pytest.approx(2000 * 300 / 700)
    assert retained["time_mean"]["observed_discarded_full_union_lower_bytes"] == pytest.approx(1000 * 200 / 700)
    assert retained["peak"]["observed_discarded_full_union_upper_bytes"] == 1200
    with native.open("a") as output:
        output.write(json.dumps({"kind": "irrelevant", "ts_ms": 10000}) + "\n")
    assert analyze(tmp_path, report["input_jsonl_prefixes"]) == report
