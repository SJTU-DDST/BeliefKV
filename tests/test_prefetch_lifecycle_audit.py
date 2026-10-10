import json

from scripts.audit_prefetch_lifecycle import (
    audit, prepare_host_lifetime_summary, prepare_restore_attribution, source_summary,
)


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
    assert result["summary"]["by_source"]["join_ticket"]["full_reused_commands"] == 1
    assert result["summary"]["by_source"]["join_ticket"]["full_pool_bytes_unknown_commands"] == 1
    [expired] = result["summary"]["by_source"]["join_ticket"]["full_reuse_by_residency"]
    assert expired["native_locked_at_registration"] is None
    assert expired["last_observed_release_reason"] == "service_window_expired"
    assert expired["full_reused_commands"] == 1
    assert expired["full_not_reused_commands"] == 0
    assert result["summary"]["by_source"]["tool_wait"]["acknowledged_commands"] == 0


def test_lifecycle_source_full_reuse_does_not_mix_handoff_or_mamba_only(tmp_path):
    sources = {"join": "join_ticket", "tool": "tool_wait", "handoff": "execution_handoff"}
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": command,
         "source": source, "workflow_id": "w", "context_id": command,
         "context_epoch": 1, "node_id": index, "ts_ms": 1.}
        for index, (command, source) in enumerate(sources.items(), 1)
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {"direction": "h2d", "node_ids": [index], "submit_ts_ms": 2.,
         "complete_ts_ms": 3., "tagged_child_commits": [
             {"command_id": command, "num_bytes": 640 if command == "tool" else 100,
              "num_tokens_by_pool": {"mamba": 1} if command == "tool" else {"kv": 5}},
         ]}
        for index, command in enumerate(sources, 1)
    ])
    write_rows(tmp_path / "server/physical_action_ack.jsonl", [
        {"command_id": command, "action": "PREFETCH_GPU",
         "pool_bytes": {"mamba": 640} if command == "tool" else {"kv": 100}}
        for command in sources
    ])
    write_rows(tmp_path / "server/physical_action_use.jsonl", [
        {"event": "beliefkv_prefetch_first_service", "command_id": command,
         "ack_ts_ms": 3., "first_service_ts_ms": 4. if command == "join" else 1003.,
         "full_node_reused": command != "join", "full_reuse_proof_version": 2}
        for command in sources
    ])
    summaries = audit(tmp_path)["summary"]["by_source"]
    assert summaries["join_ticket"]["full_verified_reused_bytes_known"] == 0
    assert summaries["join_ticket"]["ack_to_first_service_ms"]["p50"] == 1.
    assert summaries["tool_wait"]["full_transfer_commands"] == 0
    assert summaries["tool_wait"]["full_reused_commands"] == 0
    assert summaries["tool_wait"]["full_reuse_by_residency"] == []
    assert summaries["execution_handoff"]["full_verified_reused_bytes_known"] == 100
    assert summaries["execution_handoff"]["ack_to_first_service_ms"]["p50"] == 1000.


def test_lifecycle_rejects_inconsistent_ack_pool_bytes(tmp_path):
    import pytest

    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": "prefetch",
         "source": "join_ticket", "workflow_id": "w", "context_id": "c",
         "context_epoch": 1, "node_id": 1, "ts_ms": 1.},
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {"direction": "h2d", "node_ids": [1], "submit_ts_ms": 2., "complete_ts_ms": 3.,
         "tagged_child_commits": [{"command_id": "prefetch", "num_bytes": 100,
                                  "num_tokens_by_pool": {"kv": 5}}]},
    ])
    write_rows(tmp_path / "server/physical_action_ack.jsonl", [
        {"command_id": "prefetch", "action": "PREFETCH_GPU", "pool_bytes": {"kv": 101}},
    ])
    with pytest.raises(ValueError, match="physical ACK pool bytes disagree"):
        audit(tmp_path)


def test_prepare_restore_tracks_real_pool_receipts_and_later_writer():
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
    assert mamba["later_node_pool_d2h"] == [{"node_id": 10, "pool": "mamba", "ts_ms": 40.}]


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


def test_legacy_mamba_residual_does_not_replace_another_nodes_full_writer():
    prepared = {"prepare": {"command_id": "prepare", "node_id": 10, "ts_ms": 1.}}
    transfers = [
        {"direction": "d2h", "complete_ts_ms": 10., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}, "tagged_child_commits": [
             {"command_id": "prepare", "anchor_node_id": 10,
              "published_node_ids": [10], "num_tokens_by_pool": {"kv": 8}},
         ]},
        {"direction": "d2h", "complete_ts_ms": 20., "node_ids": [10, 20],
         "num_tokens_by_pool": {"kv": 4, "mamba": 1}, "tagged_child_commits": [
             {"command_id": "other", "anchor_node_id": 20,
              "published_node_ids": [20], "num_tokens_by_pool": {"kv": 4}},
         ]},
        {"direction": "h2d", "submit_ts_ms": 30., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}},
    ]
    result = prepare_restore_attribution(prepared, transfers)[0]
    assert not result["later_node_pool_d2h"]
    assert result["restores"][0]["matched_node_pools"] == [{"node_id": 10, "pool": "kv"}]


def test_later_d2h_distinguishes_intervening_host_eviction_from_unobserved_loss():
    prepared = {"prepare": {"command_id": "prepare", "node_id": 10, "ts_ms": 1.}}
    transfers = [
        {"direction": "d2h", "complete_ts_ms": 10., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}, "tagged_child_commits": [
             {"command_id": "prepare", "anchor_node_id": 10,
              "published_node_ids": [10], "num_tokens_by_pool": {"kv": 8}},
         ]},
        {"direction": "d2h", "complete_ts_ms": 20., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}},
    ]
    evictions = [
        {"node_id": 10, "pool": "full", "ts_ms": 9.},
        {"node_id": 10, "pool": "full", "ts_ms": 15.},
        {"node_id": 10, "pool": "mamba", "ts_ms": 16.},
        {"node_id": 20, "pool": "full", "ts_ms": 17.},
        {"node_id": 10, "pool": "full", "ts_ms": 21.},
    ]
    without_evidence = prepare_restore_attribution(prepared, transfers)[0]
    assert "host_evictions_between_writes" not in without_evidence["later_node_pool_d2h"][0]
    empty_evidence = prepare_restore_attribution(prepared, transfers, [])[0]
    assert empty_evidence["later_node_pool_d2h"][0]["host_evictions_between_writes"] == 0
    with_evidence = prepare_restore_attribution(prepared, transfers, evictions)[0]
    assert with_evidence["later_node_pool_d2h"][0]["host_evictions_between_writes"] == 1


def test_prepare_host_lifetime_keeps_restore_before_after_and_missing_evidence_distinct():
    prepared = {"prepare": {"command_id": "prepare", "node_id": 10, "ts_ms": 1.}}
    transfers = [
        {"direction": "d2h", "complete_ts_ms": 10., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}, "tagged_child_commits": [
             {"command_id": "prepare", "anchor_node_id": 10,
              "published_node_ids": [10], "num_tokens_by_pool": {"kv": 8}},
         ]},
        {"direction": "h2d", "submit_ts_ms": 11., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}},
        {"direction": "h2d", "submit_ts_ms": 16., "node_ids": [10],
         "num_tokens_by_pool": {"kv": 8}},
    ]
    evictions = [{"node_id": 10, "pool": "full", "ts_ms": 15.}]
    rows = prepare_restore_attribution(prepared, transfers, evictions)
    summary = prepare_host_lifetime_summary(rows, evictions)
    assert summary["command_categories"]["mixed"] == 1
    assert summary["restore_node_pool_associations"] == {
        "before_observed_eviction": 1, "after_observed_eviction": 1, "unknown": 0,
    }
    assert summary["by_pool"]["kv"]["commands_with_observed_eviction"] == 1
    assert summary["by_pool"]["kv"]["ack_to_first_observed_eviction_ms"]["p50"] == 5.
    unknown = prepare_host_lifetime_summary(rows, None)
    assert unknown["command_categories"]["unknown_eviction_evidence"] == 1
    assert unknown["restore_node_pool_associations"]["unknown"] == 2
    assert unknown["by_pool"]["kv"]["commands_with_observed_eviction"] is None
    missing = prepare_host_lifetime_summary(rows * 2, [])
    assert missing["prepare_identity_evidence_missing_commands"] == 2
    assert missing["repeated_context_epoch_node_creation_groups"] == 0
    empty = prepare_host_lifetime_summary(rows, [])
    assert empty["command_categories"]["before_observed_eviction_only"] == 1
    assert empty["by_pool"]["kv"]["commands_with_observed_eviction"] == 0


def test_prepare_lifetime_stops_at_next_writer_and_keeps_split_pools_separate():
    row = {
        "command_id": "prepare", "node_id": 10, "node_creation_time": 1.,
        "source": "join_prepare", "context_id": "ctx", "context_epoch": 1,
        "ack_ts_ms": 10., "prepared_pool_units": {"kv": 8, "mamba": 1},
        "published_node_ids": [10, 11], "restores": [
            {"submit_ts_ms": 20., "matched_node_pools": [
                {"node_id": 10, "pool": "kv"}, {"node_id": 11, "pool": "mamba"},
            ]},
        ],
        "later_node_pool_d2h": [
            {"node_id": 10, "pool": "kv", "ts_ms": 21.},
            {"node_id": 11, "pool": "kv", "ts_ms": 22.},
        ],
    }
    evictions = [
        {"node_id": 10, "pool": "mamba", "ts_ms": 9.},
        {"node_id": 11, "pool": "mamba", "ts_ms": 15.},
        {"node_id": 10, "pool": "full", "ts_ms": 23.},
        {"node_id": 11, "pool": "full", "ts_ms": 24.},
    ]
    summary = prepare_host_lifetime_summary([row], evictions)
    assert summary["command_categories"]["mixed"] == 1
    assert summary["by_pool"]["kv"]["commands_with_observed_eviction"] == 0
    assert summary["by_pool"]["mamba"]["commands_with_observed_eviction"] == 1
    assert summary["by_pool"]["mamba"]["ack_to_first_observed_eviction_ms"]["p50"] == 5.


def test_prepare_lifetime_repetition_separates_context_epoch_and_node_generation():
    def row(context="ctx", epoch=1, created=1.):
        return {
            "node_id": 10, "node_creation_time": created,
            "source": "join_prepare", "context_id": context, "context_epoch": epoch,
            "ack_ts_ms": 10., "prepared_pool_units": {"kv": 8},
            "published_node_ids": [10], "restores": [], "later_node_pool_d2h": [],
        }

    summary = prepare_host_lifetime_summary([
        row(), row(), row("other"), row(epoch=2), row(created=2.),
    ], [])
    assert summary["command_categories"]["no_restore"] == 5
    assert summary["repeated_context_epoch_node_creation_groups"] == 1
    assert summary["additional_prepares_on_same_identity"] == 1
    assert summary["max_commands_on_same_identity"] == 2


def test_native_reload_in_a_tagged_batch_keeps_its_pool_and_operation_bytes(tmp_path):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": "prefetch",
         "source": "execution_handoff", "workflow_id": "w", "context_id": "c",
         "context_epoch": 1, "node_id": 10, "ts_ms": 1.},
    ])
    native = {"command_id": None, "anchor_node_id": 10, "published_node_ids": [10],
              "num_tokens_by_pool": {"kv": 5}, "num_bytes": 100}
    other = {"command_id": "other", "anchor_node_id": 20, "published_node_ids": [20],
             "num_tokens_by_pool": {"mamba": 1}, "num_bytes": 640}
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {"direction": "h2d", "node_ids": [10], "submit_ts_ms": 2.,
         "complete_ts_ms": 3., "tagged_child_commits": [
             {**native, "command_id": "prefetch"},
         ]},
        {"direction": "h2d", "node_ids": [10, 20], "submit_ts_ms": 4.,
         "complete_ts_ms": 5., "actual_bytes": 740, "num_tokens_by_pool": {"kv": 5, "mamba": 1},
         "tagged_child_commits": [other], "node_commits": [native, other]},
    ])
    write_rows(tmp_path / "server/physical_action_use.jsonl", [
        {"event": "beliefkv_prefetch_first_service", "command_id": "prefetch",
         "ack_ts_ms": 3., "first_service_ts_ms": 10., "full_node_reused": False,
         "full_reuse_proof_version": 2},
    ])
    result = audit(tmp_path)
    [reload] = result["rows"][0]["native_reloads_before_first_service"]
    assert reload["actual_bytes"] == 100
    assert reload["pool_units"] == {"kv": 5}
    assert reload["prefetched_pool_overlap"] == ["kv"]
    assert reload["node_pool_evidence"] == "reconciled_native_receipt"
    assert result["summary"]["native_reloaded_before_first_service"] == 1
    assert result["summary"]["native_reloaded_prefetched_full_before_first_service"] == 1
    assert result["summary"]["native_reloaded_prefetched_mamba_before_first_service"] == 0
    assert result["summary"]["full_reuse_proof_versions"] == {"2": 1}
    assert result["summary"]["native_reload_pool_associations_by_evidence"]["kv"] == {
        "reconciled_native_receipt": 1, "legacy_batch_pool_presence": 0,
    }


def test_mamba_load_does_not_count_as_reloading_a_full_only_prefetch(tmp_path):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": "prefetch",
         "source": "tool_wait", "workflow_id": "w", "context_id": "c",
         "context_epoch": 1, "node_id": 10, "ts_ms": 1.},
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {"direction": "h2d", "node_ids": [10], "submit_ts_ms": 2.,
         "complete_ts_ms": 3., "tagged_child_commits": [
             {"command_id": "prefetch", "anchor_node_id": 10,
              "published_node_ids": [10], "num_tokens_by_pool": {"kv": 5}, "num_bytes": 100},
         ]},
        {"direction": "h2d", "node_ids": [10], "submit_ts_ms": 4.,
         "complete_ts_ms": 5., "actual_bytes": 640, "num_tokens_by_pool": {"mamba": 1}},
    ])
    write_rows(tmp_path / "server/physical_action_use.jsonl", [
        {"event": "beliefkv_prefetch_first_service", "command_id": "prefetch",
         "ack_ts_ms": 3., "first_service_ts_ms": 10., "full_node_reused": True},
    ])
    result = audit(tmp_path)
    [reload] = result["rows"][0]["native_reloads_before_first_service"]
    assert reload["pool_units"] == {"mamba": 1}
    assert reload["prefetched_pool_overlap"] == []
    assert result["summary"]["native_reloaded_before_first_service"] == 1
    assert result["summary"]["native_reloaded_prefetched_full_before_first_service"] == 0
    assert result["summary"]["native_reloaded_prefetched_mamba_before_first_service"] == 0
    assert result["summary"]["full_reuse_proof_versions"] == {"1": 1}
    assert result["summary"]["native_reload_pool_associations_by_evidence"]["kv"] == {
        "reconciled_native_receipt": 0, "legacy_batch_pool_presence": 0,
    }


def test_full_residency_groups_keep_reuse_bytes_and_missing_lock_evidence_distinct():
    def row(reused, *, locked=None, reason=None, pool_bytes=None, reloads=()):
        events = []
        if locked is not None:
            events.append({
                "event": "prefetch_residency_registered", "native_locked": locked,
            })
        if reason is not None:
            events.append({"event": "prefetch_residency_released", "reason": reason})
        return {
            "pool_units": {"kv": 5}, "pool_bytes": pool_bytes, "actual_bytes": 100,
            "full_first_service_reused": reused, "full_reuse_proof_version": 2,
            "lease_events": events, "native_reloads_before_first_service": list(reloads),
            "mamba_forward_verified": False,
            "ack_to_first_service_ms": 2000. if reused is not None else None,
        }

    lost = [{"prefetched_pool_overlap": ["kv"]}]
    rows = [
        row(True, locked=True, reason="service_window_expired", pool_bytes={"kv": 80}),
        row(False, locked=True, reason="service_window_expired", pool_bytes={"kv": 100}),
        row(False, locked=False, reason="native_residency_lost", pool_bytes={"kv": 60},
            reloads=lost),
        row(None, locked="false", reason="service_window_expired"),
        row(None),
    ]
    summary = source_summary(rows)
    groups = {
        (group["native_locked_at_registration"], group["last_observed_release_reason"]): group
        for group in summary["full_reuse_by_residency"]
    }
    expired = groups[True, "service_window_expired"]
    assert expired["full_transfer_commands"] == 2
    assert expired["full_reused_commands"] == expired["full_not_reused_commands"] == 1
    assert expired["full_transferred_bytes_known"] == 180
    assert expired["full_verified_reused_bytes_known"] == 80
    assert expired["ack_to_first_service_ms"]["p50"] == 2000.
    unprotected = groups[False, "native_residency_lost"]
    assert unprotected["native_reloaded_prefetched_full_before_first_service"] == 1
    assert groups[None, "service_window_expired"]["full_pool_bytes_unknown_commands"] == 1
    assert groups[None, None]["full_use_unknown_commands"] == 1
    for field in (
        "full_transfer_commands", "full_reused_commands", "full_not_reused_commands",
        "full_use_unknown_commands", "full_pool_bytes_unknown_commands",
        "full_transferred_bytes_known", "full_verified_reused_bytes_known",
        "native_reloaded_prefetched_full_before_first_service",
    ):
        assert sum(group[field] for group in groups.values()) == summary[field]


def test_residency_summary_keeps_last_release_and_does_not_infer_initial_lock():
    row = {
        "pool_units": {"kv": 5}, "pool_bytes": {"kv": 100}, "actual_bytes": 100,
        "full_first_service_reused": True, "full_reuse_proof_version": 2,
        "native_reloads_before_first_service": [], "mamba_forward_verified": False,
        "ack_to_first_service_ms": 10.,
        "lease_events": [
            {"event": "prefetch_residency_registered"},
            {"event": "prefetch_residency_released", "reason": "service_window_expired",
             "native_locked": True},
            {"event": "prefetch_residency_released", "reason": "first_service",
             "native_locked": False},
        ],
    }
    [group] = source_summary([row])["full_reuse_by_residency"]
    assert group["native_locked_at_registration"] is None
    assert group["last_observed_release_reason"] == "first_service"
    assert group["full_reused_commands"] == 1
