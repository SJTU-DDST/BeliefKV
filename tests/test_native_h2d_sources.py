import json

import pytest

from scripts.audit_native_h2d_sources import acknowledged_prefetch_sources, audit
from scripts.audit_native_memory_opportunity import audit as memory_audit


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_mixed_batch_separates_anticipation_handoff_unknown_and_native(tmp_path):
    sources = {"join": "join_ticket", "tool": "tool_wait", "handoff": "execution_handoff"}
    write_rows(tmp_path / "server/physical_action_ack.jsonl", [
        {"action": "PREFETCH_GPU", "command_id": command}
        for command in (*sources, "unknown")
    ])
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": command, "source": source}
        for command, source in sources.items()
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [{
        "direction": "h2d", "actual_bytes": 1000,
        "num_tokens_by_pool": {"kv": 20, "mamba": 2},
        "tagged_child_commits": [
            {"command_id": "join", "num_bytes": 100, "num_tokens_by_pool": {"kv": 5}},
            {"command_id": "tool", "num_bytes": 200, "num_tokens_by_pool": {"kv": 10}},
            {"command_id": "handoff", "num_bytes": 300, "num_tokens_by_pool": {"mamba": 1}},
            {"command_id": "unknown", "num_bytes": 40, "num_tokens_by_pool": {"kv": 2}},
        ],
    }])
    result = audit(tmp_path)
    counts = result["counts"]
    assert result["schema_version"] == 2
    assert counts["predictive_bytes"] == 300
    assert counts["execution_handoff_bytes"] == 300
    assert counts["unknown_controlled_bytes"] == 40
    assert counts["controlled_bytes"] == 640
    assert counts["native_bytes"] == 360
    assert counts["total_bytes"] == counts["controlled_bytes"] + counts["native_bytes"]
    assert counts["mixed_batches"] == counts["mixed_source_batches"] == 1
    assert result["predictive_commands"] == 2
    assert result["execution_handoff_commands"] == result["unknown_controlled_commands"] == 1
    assert result["controlled_commands"] == 4
    assert result["predictive_pool_units"] == {"kv": 15}
    assert result["execution_handoff_pool_units"] == {"mamba": 1}
    assert result["unknown_controlled_pool_units"] == {"kv": 2}
    assert result["native_pool_units"] == {"kv": 3, "mamba": 1}


def test_ack_source_fallback_does_not_promote_unidentified_tagged_bytes(tmp_path):
    write_rows(tmp_path / "server/physical_action_ack.jsonl", [
        {"action": "PREFETCH_GPU", "command_id": "handoff", "source": "execution_handoff"},
        {"action": "PREPARE_HOST", "command_id": "prepare", "source": "join_ticket"},
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [{
        "direction": "h2d", "actual_bytes": 150, "num_tokens_by_pool": {"kv": 15},
        "tagged_child_commits": [
            {"command_id": "handoff", "num_bytes": 100, "num_tokens_by_pool": {"kv": 10}},
            {"command_id": "unidentified", "num_bytes": 50, "num_tokens_by_pool": {"kv": 5}},
        ],
    }])
    result = audit(tmp_path)
    assert result["counts"].get("predictive_bytes", 0) == 0
    assert result["counts"]["execution_handoff_bytes"] == 100
    assert result["counts"]["unknown_controlled_bytes"] == 50
    assert result["counts"]["native_bytes"] == 0
    assert result["controlled_commands"] == 2
    assert result["predictive_commands"] == 0


def test_preloaded_ack_sources_reuse_records_and_keep_issue_evidence(tmp_path):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": "join", "source": "join_ticket"},
        {"event": "prefetch_native_issued", "command_id": "not_acked", "source": "tool_wait"},
    ])
    assert acknowledged_prefetch_sources(tmp_path, [
        {"action": "PREFETCH_GPU", "command_id": "join"},
        {"action": "PREFETCH_GPU", "command_id": "handoff", "source": "execution_handoff"},
        {"action": "PREPARE_HOST", "command_id": "prepare", "source": "tool_wait"},
    ]) == {"join": "join_ticket", "handoff": "execution_handoff"}


def test_native_arm_and_unknown_payload_are_not_invented_as_predictive(tmp_path):
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {"direction": "d2h", "actual_bytes": 500},
        {"direction": "h2d", "actual_bytes": 200, "num_tokens_by_pool": {"kv": 10}},
        {"direction": "h2d", "actual_bytes": None, "num_tokens_by_pool": {"kv": 5}},
    ])
    result = audit(tmp_path)
    assert result["counts"]["controller_batches"] == 2
    assert result["counts"]["unknown_payload_batches"] == 1
    assert result["counts"]["native_only_batches"] == 1
    assert result["counts"]["total_bytes"] == result["counts"]["native_bytes"] == 200
    assert result["native_pool_units"] == {"kv": 10}
    assert result["controlled_commands"] == 0


@pytest.mark.parametrize("size, units", [(50, 20), (200, 5)])
def test_nonconserving_child_receipts_do_not_produce_negative_native_totals(tmp_path, size, units):
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [{
        "direction": "h2d", "actual_bytes": size, "num_tokens_by_pool": {"kv": units},
        "tagged_child_commits": [
            {"command_id": "unknown", "num_bytes": 100, "num_tokens_by_pool": {"kv": 10}},
        ],
    }])
    with pytest.raises(ValueError, match="exceed batch payload"):
        audit(tmp_path)


def test_memory_budget_keeps_handoff_and_mixed_batch_intervals_distinct(tmp_path):
    (tmp_path / "client_2").mkdir()
    (tmp_path / "client_2/summary.json").write_text(json.dumps({"duration_seconds": 10}))
    (tmp_path / "server").mkdir()
    (tmp_path / "server/native_telemetry_status.json").write_text("{}")
    write_rows(tmp_path / "server/physical_action_ack.jsonl", [
        {"action": "PREFETCH_GPU", "command_id": "join", "source": "join_ticket"},
        {"action": "PREFETCH_GPU", "command_id": "handoff", "source": "execution_handoff"},
    ])
    common = {
        "direction": "h2d", "actual_bytes": 100, "num_tokens_by_pool": {"kv": 10},
        "transfer_stream_elapsed_ms": 3., "submit_to_ack_ms": 10.,
    }
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {**common, "tagged_child_commits": [
            {"command_id": "join", "num_bytes": 100, "num_tokens_by_pool": {"kv": 10}},
        ]},
        {**common, "tagged_child_commits": [
            {"command_id": "handoff", "num_bytes": 100, "num_tokens_by_pool": {"kv": 10}},
        ]},
        {**common, "tagged_child_commits": [
            {"command_id": "join", "num_bytes": 50, "num_tokens_by_pool": {"kv": 5}},
        ]},
    ])
    result = memory_audit(tmp_path)
    budget = result["transfer_budget"]
    assert budget["predictive_h2d"]["actual_bytes"] == 100
    assert budget["execution_handoff_h2d"]["actual_bytes"] == 100
    assert budget["mixed_h2d"]["actual_bytes"] == 100
    assert budget["mixed_h2d"]["cuda_event_interval"]["sum_ms"] == 3.
    assert sum(group["actual_bytes"] for group in budget.values()) == 300
    assert result["transfer_source_counts"]["counts"]["predictive_bytes"] == 150
