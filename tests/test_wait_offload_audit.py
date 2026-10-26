import json

import pytest

from scripts.audit_wait_offload import audit


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def arm_fixture(tmp_path, *, paired_clock=True):
    identity = {
        "workflow_id": "workflow", "invocation_id": "child",
        "context_id": "context", "context_epoch": 0,
    }
    write_jsonl(tmp_path / "replay/runtime_events.replay.jsonl", [
        {**identity, "kind": "llm_submit", "ts_ms": 100., "attributes": {}},
        {**identity, "kind": "tool_start", "ts_ms": 110., "attributes": {
            "tool_run_id": "tool", "tool_name": "execute",
        }},
        {**identity, "kind": "tool_end", "ts_ms": 5110., "attributes": {
            "tool_run_id": "tool", "status": "success",
        }},
    ])
    clock = [{
        "event": "admission_runtime_state",
        "ts_ms": 1000100., "monotonic_ms": 100.,
    }] if paired_clock else []
    write_jsonl(tmp_path / "opportunities/admission_opportunities.jsonl", [
        *clock,
        {**identity, "event": "prepare_native_issued", "ts_ms": 1000200.,
         "command_id": "backup", "source": "tool_wait"},
        {**identity, "event": "parent_pressure_demoted", "ts_ms": 1000300.,
         "source": "tool_wait", "freed_units": {"0": 4}},
    ])
    write_jsonl(tmp_path / "server/physical_action_ack.jsonl", [{
        "command_id": "backup", "action": "PREPARE_HOST", "pool_bytes": {"kv": 16},
    }])
    capacity = {
        "host_full_bytes": 64, "host_full_tokens": 16,
        "host_mamba_bytes": 0, "host_mamba_slots": 0,
    }
    (tmp_path / "server/native_capacity_census.json").write_text(
        json.dumps({"capacity": capacity}),
    )
    return capacity


def test_replay_tool_offload_uses_paired_state_clock_and_full_only_geometry(tmp_path):
    arm_fixture(tmp_path)
    result = audit(tmp_path)
    summary = result["summary"]
    assert summary["observed_complete_tools_with_context"] == 1
    assert summary["prepare_backups_by_source"]["tool_wait"]["actual_pool_bytes"]["kv"] == 16
    demotion = summary["hbm_demotion_by_source"]["tool_wait"]
    assert demotion["released_full_bytes"] == 16
    assert demotion["released_mamba_bytes"] == 0
    assert result["tool_demotion_decisions"][0]["actual_remaining_ms"] == 4810.
    assert summary["tool_demotion_timing"]["first_decision_per_tool_long_wait_precision"] == 1.
    assert any(
        seal["path"].endswith("runtime_events.replay.jsonl")
        for seal in result["source_seals"]
    )


def test_tool_offload_without_observed_clock_pair_still_fails(tmp_path):
    arm_fixture(tmp_path, paired_clock=False)
    with pytest.raises(ValueError, match="no paired scheduler clock"):
        audit(tmp_path)


def test_tool_offload_rejects_nonfinite_paired_clock(tmp_path):
    arm_fixture(tmp_path)
    write_jsonl(tmp_path / "opportunities/admission_opportunities.jsonl", [{
        "event": "admission_runtime_state",
        "ts_ms": 1000100., "monotonic_ms": float("nan"),
    }])
    with pytest.raises(ValueError, match="clock sample"):
        audit(tmp_path)


def test_legacy_unpaired_state_does_not_replace_preceding_census_clock(tmp_path):
    arm_fixture(tmp_path)
    path = tmp_path / "opportunities/admission_opportunities.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["event"] = "safe_point_census"
    rows.insert(1, {"event": "admission_runtime_state", "ts_ms": 1000150.})
    write_jsonl(path, rows)
    result = audit(tmp_path)
    assert result["tool_demotion_decisions"][0]["actual_remaining_ms"] == 4810.


def test_absent_mamba_slots_cannot_hide_positive_host_allocation(tmp_path):
    capacity = arm_fixture(tmp_path)
    capacity["host_mamba_bytes"] = 16
    (tmp_path / "server/native_capacity_census.json").write_text(
        json.dumps({"capacity": capacity}),
    )
    with pytest.raises(ValueError, match="Host mamba pool geometry"):
        audit(tmp_path)
