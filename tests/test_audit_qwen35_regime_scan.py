from __future__ import annotations

import json

from scripts.audit_qwen35_regime_scan import summarize


def test_deduplicates_observations_without_promoting_native_ack(tmp_path):
    opportunities = tmp_path / "opportunities"
    server = tmp_path / "server"
    opportunities.mkdir()
    server.mkdir()
    observation = {
        "event": "session_h2d_opportunity", "source": "tool_wait",
        "context_id": "ctx", "context_epoch": 8, "session_id": "session",
        "session_generation": 11, "node_id": 52, "node_creation_time": 286,
        "leaf_node_id": 4595, "leaf_creation_time": 157195,
        "fits_current_free_lists": True, "required_full_tokens": 0,
        "required_mamba_slots": 1,
        "prepare_fits_current_host_free_lists": True,
        "prepare_node_id": 1013, "prepare_node_creation_time": 57,
        "prepare_leaf_node_id": 4595, "prepare_leaf_creation_time": 157195,
        "prepare_required_full_tokens": 1, "prepare_required_mamba_slots": 0,
    }
    with (opportunities / "admission_opportunities.jsonl").open("w") as file:
        for ts in (1000, 2000, 3000):
            file.write(json.dumps({**observation, "ts_ms": ts}) + "\n")
    with (server / "transfer_telemetry.jsonl").open("w") as file:
        for ts, node in ((500, 52), (3500, 4721)):
            file.write(json.dumps({
                "direction": "h2d", "status": "completed",
                "complete_ts_ms": ts, "node_ids": [node],
            }) + "\n")
        file.write(json.dumps({
            "direction": "d2h", "status": "completed",
            "complete_ts_ms": 3500, "node_ids": [1013],
        }) + "\n")
    (server / "physical_action_ack.jsonl").write_text("")
    (server / "physical_action_use.jsonl").write_text("")
    (server / "native_telemetry_status.json").write_text(json.dumps({
        "writer_error": None,
        "host_block_eviction_attribution": {"recomputed_full_units": 1},
    }))
    (opportunities / "admission_opportunities_status.json").write_text(
        json.dumps({"complete": True})
    )
    for workflow, outcome, natural in (
        ("ok", "completed", True), ("stopped", "error", False)
    ):
        directory = tmp_path / "client_24" / "workflows" / workflow
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps({
            "outcome": outcome, "natural_terminal": natural,
        }))
    report = summarize(tmp_path)
    assert report["collection_complete"] is True
    assert report["workflow_results"] == 2
    assert report["natural_completions"] == 1
    assert report["h2d_snapshot_count"] == 3
    assert report["h2d_distinct_session_targets"] == [{
        "source": "tool_wait", "context_id": "ctx", "node_id": 52,
        "first_ts_ms": 1000, "last_ts_ms": 3000, "sample_count": 3,
        "required_full_tokens": 0, "required_mamba_slots": 1,
        "observed_span_ms": 2000,
    }]
    assert report["prepare_distinct_session_targets"] == 1
    assert report["native_ack_count"]["h2d"] == 2
    assert report["h2d_targets_with_later_native_h2d_node_id_only"] == 0
    assert report["prepare_targets_with_later_native_d2h_node_id_only"] == 1
    assert report["predictive_h2d_ack_count"] == 0
    assert report["verified_first_service_full_reuse_count"] == 0
    assert report["qualification"] == "not_inferred_from_snapshots_or_native_acks"
