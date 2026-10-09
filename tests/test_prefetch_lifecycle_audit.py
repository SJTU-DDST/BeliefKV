import json

from scripts.audit_prefetch_lifecycle import audit, prepare_restore_attribution


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_lifecycle_keeps_expired_but_reused_and_missing_use_distinct(tmp_path):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": "prefetch1",
         "source": "join_ticket", "workflow_id": "w", "context_id": "c",
         "context_epoch": 1, "node_id": 10, "ts_ms": 100., "child_request_id": "child"},
        {"event": "prefetch_native_issued", "command_id": "prefetch2",
         "source": "tool_wait", "workflow_id": "w", "context_id": "other",
         "context_epoch": 2, "node_id": 20, "ts_ms": 400.},
        {"event": "prefetch_residency_released", "command_id": "prefetch1",
         "reason": "service_window_expired", "ts_ms": 120.},
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [{
        "direction": "h2d", "node_ids": [10], "submit_ts_ms": 101.,
        "complete_ts_ms": 110., "submit_to_ack_ms": 9., "enqueue_to_submit_ms": 1.,
        "tagged_child_commits": [{"command_id": "prefetch1", "num_bytes": 100,
                                 "num_tokens_by_pool": {"kv": 5}}],
    }])
    write_rows(tmp_path / "server/runtime_events.sglang.jsonl", [{
        "kind": "llm_result", "ts_ms": 102., "attributes": {"request_id": "child"},
    }])
    write_rows(tmp_path / "server/physical_action_use.jsonl", [{
        "event": "beliefkv_prefetch_first_service", "command_id": "prefetch1",
        "ack_ts_ms": 110., "first_service_ts_ms": 200., "full_node_reused": True,
    }])
    result = audit(tmp_path)
    assert result["summary"]["join_submitted_before_eos"] == 1
    assert result["summary"]["full_reused"] == 1
    assert result["summary"]["full_use_unknown"] == 1
    assert result["rows"][0]["lease_events"][0]["reason"] == "service_window_expired"
    assert result["summary"]["redemoted_before_first_service"] == 0
    assert result["rows"][1]["actual_bytes"] is None


def test_prepare_restore_tracks_real_pool_receipts_and_superseding_writer():
    prepared = {
        "prepare-full": {"command_id": "prepare-full", "node_id": 10, "ts_ms": 1.},
        "prepare-mamba": {"command_id": "prepare-mamba", "node_id": 10, "ts_ms": 2.},
    }

    def receipt(node, pools, command=None, split=None):
        return {
            "command_id": command, "anchor_node_id": node,
            "published_node_ids": split or [node], "num_tokens_by_pool": pools,
        }

    transfers = [
        {"direction": "d2h", "complete_ts_ms": 10., "node_ids": [10, 11, 20],
         "node_commits": [
             receipt(10, {"kv": 8}, "prepare-full", [10, 11]),
             receipt(20, {"kv": 4}),
         ]},
        {"direction": "d2h", "complete_ts_ms": 20., "node_ids": [10],
         "node_commits": [receipt(10, {"mamba": 1}, "prepare-mamba")]},
        {"direction": "h2d", "submit_ts_ms": 30., "node_ids": [10, 20],
         "node_commits": [receipt(10, {"kv": 4}), receipt(20, {"mamba": 1})]},
        {"direction": "d2h", "complete_ts_ms": 40., "node_ids": [10],
         "node_commits": [receipt(10, {"mamba": 1})]},
        {"direction": "h2d", "submit_ts_ms": 50., "node_ids": [10],
         "node_commits": [receipt(10, {"mamba": 1})]},
        {"direction": "h2d", "submit_ts_ms": 60., "node_ids": [11],
         "node_commits": [receipt(11, {"kv": 4}, "prefetch")]},
    ]
    full, mamba = prepare_restore_attribution(prepared, transfers)
    assert [row["source"] for row in full["restores"]] == ["native_h2d", "controlled_h2d"]
    assert full["restores"][0]["matched_node_pools"] == [{"node_id": 10, "pool": "kv"}]
    assert full["restores"][1]["matched_node_pools"] == [{"node_id": 11, "pool": "kv"}]
    assert not mamba["restores"]
    assert mamba["superseded_node_pools"] == [{"node_id": 10, "pool": "mamba", "ts_ms": 40.}]


def test_prepare_restore_legacy_merge_never_attributes_other_tagged_child_nodes():
    prepared = {"prepare": {"command_id": "prepare", "node_id": 10, "ts_ms": 1.}}
    transfers = [
        {"direction": "d2h", "complete_ts_ms": 10., "node_ids": [10, 20],
         "num_tokens_by_pool": {"kv": 12}, "tagged_child_commits": [
             {"command_id": "prepare", "anchor_node_id": 10,
              "published_node_ids": [10], "num_tokens_by_pool": {"kv": 8}},
             {"command_id": "other", "anchor_node_id": 20,
              "published_node_ids": [20], "num_tokens_by_pool": {"kv": 4}},
         ]},
        {"direction": "h2d", "submit_ts_ms": 11., "node_ids": [20],
         "num_tokens_by_pool": {"kv": 4}},
        {"direction": "h2d", "submit_ts_ms": 12., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}},
    ]
    result = prepare_restore_attribution(prepared, transfers)[0]
    assert result["published_node_ids"] == [10]
    assert len(result["restores"]) == 1
    assert result["restores"][0]["submit_ts_ms"] == 12.
    assert result["restores"][0]["node_pool_evidence"] == "legacy_batch_pool_presence"
