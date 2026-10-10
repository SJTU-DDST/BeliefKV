import json

from scripts.audit_prefetch_lifecycle import (
    audit, http_transport_waits, prepare_host_lifetime_summary, prepare_restore_attribution,
    prepare_selection_summary, source_summary, wait_event_attribution,
)


def test_prepare_burst_ack_attribution_does_not_multiply_first_extent_estimates():
    result = prepare_selection_summary([{
        "source": "join_prepare", "command_id": "first",
        "burst_command_ids": ["first", "second", "unacked"],
        "transfer_bytes": 80, "reclaimable_pressured_bytes": 0,
    }], [{"command_id": "first"}, {"command_id": "second"}])
    assert result["candidate_records"] == 1
    assert result["burst_records"] == 1
    assert result["explicit_burst_commands"] == 3
    assert result["acknowledged_burst_commands"] == 2
    selected = result["by_source"]["join_prepare"]
    assert selected["acknowledged_command_records"] == 1
    assert selected["selected_transfer_bytes_known"] == 80
    assert selected["zero_direct_reclaim_potential_commands"] == 1


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_wait_events_deduplicate_node_commands_and_keep_native_residual_demand_separate(tmp_path):
    join = {
        "source": "join_ticket", "workflow_id": "w", "context_id": "c",
        "context_epoch": 1, "join_id": "j", "invocation_id": "parent",
        "session_id": "s", "session_generation": 1,
    }
    observations = [
        {**join, "event": "wait_prefetch_plan", "ts_ms": 90.,
         "reason": "start_window", "planned_full_tokens": tokens}
        for tokens in (8, 8, 6)
    ]
    issues = {
        command: {**join, "event": "prefetch_native_issued", "ts_ms": 91.}
        for command in ("early-1", "early-2", "late")
    }
    actions = [
        {**join, "command_id": command, "submit_ts_ms": submit, "ack_ts_ms": 95.,
         "pool_bytes": {"kv": amount}, "full_first_service_reused": True,
         "first_service_request_id": "next", "first_service_context_epoch": 2}
        for command, submit, amount in (("early-1", 92., 20), ("early-2", 93., 30), ("late", 105., 50))
    ] + [{
        "command_id": "handoff", "source": "execution_handoff", "request_id": "next",
        "pool_bytes": {"kv": 80},
    }]
    write_rows(tmp_path / "client_1/workflows/example/runtime_events.deepagents.jsonl", [
        {"kind": "join_satisfied", "workflow_id": "w", "join_id": "j", "ts_ms": 20.},
        {"kind": "llm_submit", "workflow_id": "w", "context_id": "c",
         "invocation_id": "parent", "context_epoch": 2, "ts_ms": 30.,
         "attributes": {"request_id": "next"}},
    ])
    write_rows(tmp_path / "server/runtime_events.sglang.jsonl", [{
        "kind": "llm_submit", "workflow_id": "w", "context_id": "c",
        "invocation_id": "parent", "context_epoch": 2, "ts_ms": 120.,
        "attributes": {"request_id": "next", "cached_tokens_host": 3},
    }])
    write_rows(tmp_path / "client_1/workflows/example/child_stream_content.jsonl", [{
        "event": "llm_request_http_transport", "request_id": "next",
        "http_request_start_ts_ms": 112., "http_body_sent_ts_ms": 115.,
        "http_response_headers_ts_ms": 121.,
    }])
    write_rows(tmp_path / "server/runtime_audit.jsonl", [{
        "event": "gpu_service_sample", "service_start_ts_ms": 200.,
        "request_samples": [{"request_id": "next", "workflow_id": "w",
                             "context_id": "c", "context_epoch": 2}],
    }, {
        "event": "gpu_restore_dependency_wait", "request_ids": ["next", "other"],
        "gpu_layer_dependency_wait_ms": 5.,
    }])
    (tmp_path / "server/native_telemetry_status.json").write_text(json.dumps({
        "host_pool_evidence": {"full": {"bytes_per_unit": 10}},
    }))
    result = wait_event_attribution(tmp_path, observations, issues, actions, 80.)
    assert result["summary"]["observed_wait_events"] == 1
    [row] = result["rows"]
    assert row["max_observed_planned_full_tokens"] == 8
    assert row["max_observed_planned_full_bytes"] == 80
    assert row["node_command_count"] == 3
    assert row["early_started_and_reused_full_bytes"] == 50
    assert row["remaining_native_full_host_hit_bytes"] == 30
    assert row["demand_handoff_full_bytes_known"] == 80
    assert row["completion_to_client_submit_ms"] == 10.
    assert row["client_submit_to_native_arrival_ms"] == 10.
    assert row["client_submit_to_http_request_ms"] == 2.
    assert row["http_request_to_body_sent_ms"] == 3.
    assert row["body_sent_to_native_arrival_ms"] == 5.
    assert row["http_request_to_response_headers_ms"] == 9.
    assert row["client_submit_to_first_service_ms"] == 90.
    assert row["submit_to_first_service_ms"] == 80.
    assert row["last_ack_to_first_service_ms"] == 105.
    assert row["sampled_batch_restore_dependency_wait_ms"] == [5.]
    actions[0]["first_service_request_id"] = "later"
    actions[1]["first_service_context_epoch"] = 3
    mismatched = wait_event_attribution(tmp_path, observations, issues, actions, 80.)
    assert mismatched["rows"][0]["early_started_and_reused_full_bytes"] == 0
    missing = wait_event_attribution(tmp_path, [], issues, actions, 80.)
    assert missing["rows"][0]["max_observed_planned_full_tokens"] is None
    assert missing["rows"][0]["max_observed_planned_full_bytes"] is None
    assert missing["summary"]["by_source"]["join_ticket"]["planned_full_bytes_unknown_events"] == 1


def test_tool_event_waits_for_all_tools_and_leaves_unknown_evidence_unknown(tmp_path):
    tool = {
        "source": "tool_wait", "workflow_id": "w", "context_id": "t",
        "context_epoch": 2, "invocation_id": "tool", "active_tool_ids": ["short", "long"],
    }
    observations = [{
        **tool, "event": "wait_prefetch_plan", "ts_ms": 90.,
        "planned_full_tokens": 8, "reason": "start_window",
    }]
    issues = {"early": {**tool, "event": "prefetch_native_issued", "ts_ms": 91.}}
    actions = [{
        **tool, "command_id": "early", "submit_ts_ms": 110., "ack_ts_ms": None,
        "pool_bytes": None, "full_first_service_reused": True,
    }]
    write_rows(tmp_path / "client_1/workflows/example/runtime_events.deepagents.jsonl", [
        {"kind": "tool_end", "workflow_id": "w", "invocation_id": "tool",
         "ts_ms": when, "attributes": {"tool_run_id": identity}}
        for identity, when in (("short", 100.), ("long", 120.))
    ])
    result = wait_event_attribution(tmp_path, observations, issues, actions, 0.)
    [row] = result["rows"]
    assert row["completion_ts_ms"] is None
    assert row["observed_tools_completion_ts_ms"] == 120.
    assert row["completion_evidence"] == "tool_request_round_unproven"
    assert row["early_started_and_reused_full_bytes"] == 0
    assert row["first_service_ts_ms"] is None
    assert row["client_submit_to_native_arrival_ms"] is None
    assert row["client_submit_to_http_request_ms"] is None
    assert row["body_sent_to_native_arrival_ms"] is None
    assert row["client_submit_to_first_service_ms"] is None
    assert row["remaining_native_full_host_hit_bytes"] is None
    assert result["summary"]["by_source"]["tool_wait"]["remaining_native_full_evidence_unknown_events"] == 1


def test_http_waits_keep_missing_ambiguous_and_unordered_boundaries_unknown():
    attempt = {
        "http_request_start_ts_ms": 110., "http_body_sent_ts_ms": 115.,
        "http_response_headers_ts_ms": 121.,
    }
    for attempts in ([], [attempt, attempt]):
        waits = http_transport_waits(attempts, 100., 120.)
        assert waits["http_transport_attempts_observed"] == len(attempts)
        assert all(value is None for name, value in waits.items() if name.endswith("_ms"))
    for sent in (None, 109., 130., float("nan")):
        waits = http_transport_waits([{**attempt, "http_body_sent_ts_ms": sent}], 100., 120.)
        assert waits["client_submit_to_http_request_ms"] == 10.
        assert waits["http_request_to_body_sent_ms"] is None
        assert waits["body_sent_to_native_arrival_ms"] is None
    waits = http_transport_waits([attempt], 111., 120.)
    assert waits["client_submit_to_http_request_ms"] is None
    assert waits["http_request_to_response_headers_ms"] is None


def test_tool_boundary_includes_sequential_calls_outside_sampled_active_set(tmp_path):
    tool = {
        "source": "tool_wait", "workflow_id": "w", "context_id": "t",
        "context_epoch": 2, "invocation_id": "tool", "active_tool_ids": ["first"],
    }
    observations = [{
        **tool, "event": "wait_prefetch_plan", "ts_ms": 100.,
        "planned_full_tokens": 8, "reason": "start_window",
    }]
    issues = {"early": {**tool, "event": "prefetch_native_issued", "ts_ms": 101.}}
    actions = [{
        **tool, "command_id": "early", "submit_ts_ms": 135., "ack_ts_ms": 138.,
        "pool_bytes": {"kv": 20}, "full_first_service_reused": True,
        "first_service_request_id": "next", "first_service_context_epoch": 3,
    }]
    path = tmp_path / "client_1/workflows/example/runtime_events.deepagents.jsonl"
    requests = [
        {"kind": "llm_submit", "workflow_id": "w", "context_id": "t",
         "invocation_id": "tool", "context_epoch": epoch, "ts_ms": when,
         "attributes": {"request_id": rid}}
        for epoch, when, rid in ((2, 90., "origin"), (3, 160., "next"))
    ]
    tools = [
        {"kind": kind, "workflow_id": "w", "invocation_id": "tool",
         "ts_ms": when, "attributes": {"tool_run_id": identity}}
        for kind, identity, when in (
            ("tool_start", "first", 95.), ("tool_end", "first", 110.),
            ("tool_start", "second", 115.), ("tool_end", "second", 140.),
            ("tool_start", "later-round", 160.),
        )
    ]
    write_rows(path, requests + tools)
    result = wait_event_attribution(tmp_path, observations, issues, actions, 0.)
    [row] = result["rows"]
    assert row["observed_tool_call_count"] == 2
    assert row["completion_ts_ms"] == 140.
    assert row["completion_evidence"] == "whole_tool_request_round"
    assert row["early_started_and_reused_full_bytes"] == 20
    assert row["early_ready_and_reused_full_bytes"] == 20
    assert row["completion_to_client_submit_ms"] == 20.
    assert result["summary"]["by_source"]["tool_wait"]["completion_boundary_unknown_events"] == 0
    write_rows(path, [row for row in requests + tools
                      if (row.get("attributes") or {}).get("tool_run_id") != "second"
                      or row["kind"] != "tool_end"])
    incomplete = wait_event_attribution(tmp_path, observations, issues, actions, 0.)
    assert incomplete["rows"][0]["completion_ts_ms"] is None
    assert incomplete["rows"][0]["early_started_and_reused_full_bytes"] == 0


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


def test_prepare_selection_matches_only_command_identity_and_keeps_legacy_unknown():
    def selection(command=None, source="join_prepare", reclaim=0, size=20):
        return {
            "command_id": command, "source": source,
            "reclaimable_pressured_bytes": reclaim, "transfer_bytes": size,
            "missing_full_prefix_tokens": 100,
        }

    summary = prepare_selection_summary([
        selection("acked"), selection("unacked", reclaim=40, size=40),
        selection(), selection("tool", source="tool_wait", reclaim=20),
    ], [{"command_id": "acked"}, {"command_id": "tool"}])
    assert summary["candidate_records"] == 4
    assert summary["records_without_command_id"] == 1
    join = summary["by_source"]["join_prepare"]
    assert join["acknowledged_command_records"] == 1
    assert join["direct_reclaim_potential_commands"] == 1
    assert join["zero_direct_reclaim_potential_commands"] == 2
    assert join["selected_transfer_bytes_known"] == 80
    assert join["selected_transfer_bytes_unknown_records"] == 0
    assert join["direct_reclaim_potential_unknown_commands"] == 0
    assert join["zero_direct_reclaim_selected_transfer_bytes_known"] == 40
    assert join["missing_full_prefix_tokens"]["p50"] == 100
    tool = summary["by_source"]["tool_wait"]
    assert tool["acknowledged_command_records"] == 1
    assert tool["zero_direct_reclaim_potential_commands"] == 0
    legacy = prepare_selection_summary([{"source": "join_prepare"}], [])
    legacy_join = legacy["by_source"]["join_prepare"]
    assert legacy["records_without_command_id"] == 1
    assert legacy_join["direct_reclaim_potential_unknown_commands"] == 1
    assert legacy_join["zero_direct_reclaim_potential_commands"] == 0
    assert legacy_join["selected_transfer_bytes_unknown_records"] == 1
    assert legacy_join["missing_full_prefix_tokens"] == {"count": 0}


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
