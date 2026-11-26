import json

import pytest

from scripts.audit_d2_tool_wait_trace import Archive, measure


def write_records(directory, name, rows):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def request(rid, *, workflow="donor", epoch=0):
    return dict(
        request_id=rid, workflow_id=workflow, invocation_id=f"{workflow}-agent",
        context_id=f"{workflow}-context", context_epoch=epoch, attempt_id=0,
        session_id=f"{workflow}-session", session_generation=1,
    )


def replay(kind, when, key, attrs=None):
    return {
        "kind": kind, "ts_ms": when,
        **{field: key[field] for field in ("workflow_id", "invocation_id", "context_id", "context_epoch")},
        "attributes": attrs if attrs is not None else {"request_id": key["request_id"]},
    }


@pytest.mark.parametrize(
    "valid_restore_ack,repeat_restore", [(True, False), (False, False), (True, True)],
)
def test_tool_wait_audit_distinguishes_real_gpu_progress_from_restore_coverage(
    tmp_path, valid_restore_ack, repeat_restore,
):
    wall = 100_000
    origin = request("origin")
    other = request("other", workflow="consumer")
    successor = request("next", epoch=1)
    tool = {"tool_run_id": "run", "tool_call_id": "call"}
    write_records(tmp_path, "replay/runtime_events.replay.jsonl", [
        replay("workflow_start", 100, origin),
        replay("llm_submit", 200, origin),
        replay("llm_submit", 250, other),
        replay("llm_result", 285, origin, {"request_id": "origin", "tool_call_count": 1}),
        replay("tool_start", 300, origin, tool),
        replay("tool_end", 2300, origin, tool),
        replay("llm_submit", 2310, successor),
        replay("workflow_end", 2600, origin, {"outcome": "completed"}),
    ])
    stage = {
        **successor, "event": "native_demand_restore_staged", "monotonic_ms": 2401,
        "ts_ms": wall + 2401, "monotonic_clock_domain": "clock", "requests": [successor],
        "native_batch_size": 1, "native_submit_ts_ms": wall + 2400,
        "native_bytes": 512, "native_full_tokens": 4, "native_node_ids": [1],
    }
    repeated = dict(
        stage, native_batch_size=2, requests=[successor, other],
        ts_ms=wall + 2440, monotonic_ms=2440, native_submit_ts_ms=wall + 2438,
        native_node_ids=[9], native_bytes=384, native_full_tokens=3,
    )
    write_records(tmp_path, "opportunities/admission_opportunities.jsonl", [
        dict(event="admission_runtime_state", ts_ms=wall, monotonic_ms=0, tool_offload_min_survival=.75),
        dict(
            origin, event="tool_wait_forecast", ts_ms=wall + 310, observed_monotonic_ms=309,
            expires_monotonic_ms=1000, p10_ms=1800, p50_ms=1800, p90_ms=1800,
            tool_offload_wait_ms=1000, release_cdf=[[1800, 1.]],
            residual_support_detail="online_run_command",
        ),
        dict(
            origin, event="prepare_native_issued", ts_ms=wall + 320, source="tool_wait",
            command_id="backup", node_id=1, node_creation_time=7,
        ),
        dict(
            origin, event="predicted_tool_kv_evicted", ts_ms=wall + 400,
            dispatch_stage="event_tool_replacement", beneficiary_request_id="other",
            beneficiary_context_id=other["context_id"], node_id=1, creation_time=7,
            freed_units={"0": 4}, remaining_threshold_ms=1000,
        ),
        dict(
            event="agent_predicted_tool_replacement", ts_ms=wall + 401, monotonic_s=.401,
            consumer=other, donor=origin,
            physical_capacity_authority="settled_private_release_then_fresh_native_budget",
        ),
        stage,
        dict(stage, event="native_demand_restore_ready", ts_ms=wall + 2420, monotonic_ms=2420),
        *([
            repeated,
            dict(
                repeated, event="native_demand_restore_ready",
                ts_ms=wall + 2449, monotonic_ms=2449, waiting_checks=3,
            ),
        ] if repeat_restore else []),
    ])
    write_records(tmp_path, "server/physical_action_ack.jsonl", [
        dict(
            origin, action="PREPARE_HOST", command_id="backup", ts_ms=wall + 350,
            node_ids=[1], pool_bytes={"kv": 512},
        ),
    ])
    restore_ack = {
        "event": "transfer_telemetry", "direction": "h2d",
        "telemetry_origin": "native_hicache_ack_v0520", "status": "completed",
        "node_ids": [1 if valid_restore_ack else 2], "submit_ts_ms": wall + 2400,
        "complete_ts_ms": wall + 2419, "actual_bytes": 512, "num_tokens_by_pool": {"kv": 4},
    }
    write_records(tmp_path, "server/transfer_telemetry.jsonl", [
        restore_ack,
        *([dict(
            restore_ack, node_ids=[9], submit_ts_ms=wall + 2438, complete_ts_ms=wall + 2448,
            actual_bytes=384, num_tokens_by_pool={"kv": 3},
        )] if repeat_restore else []),
    ])
    write_records(tmp_path, "server/runtime_events.sglang.jsonl", [
        replay("llm_submit", wall + 210, origin, {"request_id": "origin", "cached_tokens_host": 12}),
    ])
    write_records(tmp_path, "server/runtime_audit.jsonl", [
        dict(
            event="gpu_service_sample", monotonic_clock_domain="clock",
            launch_monotonic_ms=start, complete_monotonic_ms=start + 10,
            request_samples=[dict(key, token_delta=progress)],
        )
        for key, start, progress in ((other, 390, 0), (other, 450, 5), (successor, 2450, 5))
    ])
    report = measure(Archive(tmp_path), cohort={"measured_workflow_ids": ["donor"]}, survival=None)
    calls = report["call_counts_and_timing"]["all"]
    assert calls["tool_starts"] == calls["tool_ends_matched"] == 1
    assert calls["prior_Host_evidence"]["logical_Host_hit_at_origin_ingress"] == 1
    assert calls["prior_Host_evidence"]["same_epoch_explicit_prepare_ACK"] == 0
    assert calls["fixed_gate_label_screen"]["within_100ms_over_horizon_with_prior_Host_evidence"] == 1
    assert calls["calls_with_prepare_ACK_before_end"] == 1
    assert calls["release_paths"]["predicted"]["FULL_tokens"] == 4
    assert report["positive_node_release_records_by_stage"]["event_tool_replacement"][
        "same_epoch_node_prepare_ACK_before_release"
    ] == 1
    service = report["matched_event_replacement_grant_service"]
    assert service["first_positive_service_before_donor_tool_end"] == 1
    assert service["release_to_first_service_ms"]["p50"] == 50
    assert service["served_consumers_submitted_before_donor_tool_start"] == 1
    predicted = report["released_round_restore"]["predicted"]
    restore = predicted["counts"]
    assert restore["successors_with_first_positive_service"] == 1
    assert restore.get("successors_with_exact_staged_H2D_ACK", 0) == int(valid_restore_ack)
    assert restore.get("successors_with_matching_released_and_restored_node_id", 0) == int(valid_restore_ack)
    phases = predicted["staged_restore_phases_ms"]["fixed18"]
    if valid_restore_ack:
        assert phases["first_stage_READY_to_launch"]["p50"] == 30
        assert phases["last_stage_READY_to_launch"]["p50"] == (1 if repeat_restore else 30)
        assert phases["last_stage_ACK_to_launch"]["p50"] == (2 if repeat_restore else 31)
    else:
        assert not phases
    if repeat_restore:
        assert restore["successors_with_repeated_restore_stages"] == 1
        assert phases["repeat_staged_wait_with_recorded_deferral"]["p50"] == 9
    assert report["observed_jct"]["fixed18"]["mean_JCT_s"] == 2.5


def test_tool_end_and_observer_coverage_can_be_missing_independently(tmp_path):
    origin = request("origin")
    write_records(tmp_path, "replay/runtime_events.replay.jsonl", [
        replay("workflow_start", 100, origin),
        replay("llm_submit", 200, origin),
        replay("tool_start", 300, origin, {"tool_run_id": "run", "tool_call_id": "call"}),
    ])
    report = measure(Archive(tmp_path), cohort={}, survival=None)
    calls = report["call_counts_and_timing"]["all"]
    assert calls["tool_starts"] == 1
    assert calls["tool_ends_matched"] == 0
    assert calls["tool_duration_ms"] == {"count": 0}
    assert report["input_prefixes"]["server/runtime_audit.jsonl"] == {"status": "missing"}
    assert report["wall_to_monotonic"]["usable_offset"] is False


@pytest.mark.parametrize("path", [
    "free", "periodic", "competing_release", "intervening_pause",
    "repeated_grant", "foreign_attempt", "reentry", "late_queue", "wrong_tool",
])
def test_tool_start_release_requires_a_bounded_exact_grant_and_first_worker_service(tmp_path, path):
    wall = 100_000
    origin = request("origin")
    other = request("other", workflow="consumer")
    successor = request("next", epoch=1)
    tool = {"tool_run_id": "run", "tool_call_id": "call"}
    write_records(tmp_path, "replay/runtime_events.replay.jsonl", [
        replay("workflow_start", 100, origin),
        replay("llm_submit", 200, origin),
        replay("llm_submit", 250, other),
        replay("llm_result", 285, origin, {"request_id": "origin", "tool_call_count": 1}),
        replay("tool_start", 300, origin, tool),
        replay("tool_end", 2300, origin, tool),
        replay("llm_submit", 2310, successor),
    ])

    def journal(event, when, **fields):
        return dict(event=event, ts_ms=wall + when, monotonic_s=when / 1000, **fields)

    paused = journal(
        "agent_full_offload_paused", 395, key=origin, source="predicted_tool_offload",
        observed_missing_full_tokens=4, released_logical_tokens=60,
    )
    released = journal(
        "predicted_tool_kv_evicted", 400, **origin, dispatch_stage="tool_start",
        tool_call_id="call" if path == "wrong_tool" else "run", tool_start_monotonic_ms=300,
        node_id=1, freed_units={"0": 4},
    )
    extra = []
    if path == "competing_release":
        extra.append(journal(
            "terminal_workflow_root_cache_reclaimed", 420, freed_device_units={"0": 5},
        ))
    if path == "periodic":
        grant = journal(
            "agent_periodic_admission_decision", 450,
            resumed_program_ids=[other["invocation_id"]], programs=[dict(
                key=other, invocation_id=other["invocation_id"], active_before=False,
                active_after_pressure=True, marked_after_pressure=False,
                application_ready=True, pending_before=True,
            )],
        )
    else:
        grant = journal(
            "agent_dispatch_free_capacity_admitted", 450,
            programs=[{"key": other}], required_tokens=80, remaining_before_tokens=100,
            remaining_after_tokens=20, native_capacity_authority_preserved=True,
        )
    after = []
    if path == "intervening_pause":
        after.append(journal("agent_full_offload_paused", 480, key=other))
    if path == "repeated_grant":
        after.append(dict(grant, ts_ms=wall + 480, monotonic_s=.48))
    write_records(tmp_path, "opportunities/admission_opportunities.jsonl", [
        journal("admission_runtime_state", 0, monotonic_ms=0, agent_control_interval_ms=2000),
        paused, released, *extra, grant, *after,
        journal("agent_periodic_admission_decision", 520, resumed_program_ids=[], programs=[]),
    ])
    native_queue_when = 410 if path == "late_queue" else 270
    write_records(tmp_path, "server/runtime_events.sglang.jsonl", [
        replay("llm_submit", wall + native_queue_when, other),
    ])
    samples = [
        (dict(other, attempt_id=1) if path == "foreign_attempt" else other, 475, 505),
        (successor, 2380, 2400),
    ]
    if path == "reentry":
        samples.insert(0, (other, 280, 290))
    write_records(tmp_path, "server/runtime_audit.jsonl", [
        dict(
            event="gpu_service_sample", monotonic_clock_domain="clock", launch_monotonic_ms=launch,
            service_start_monotonic_ms=worker, complete_monotonic_ms=worker + 5,
            request_samples=[dict(key, token_delta=5)],
        )
        for key, launch, worker in samples
    ])
    report = measure(Archive(tmp_path), cohort={"measured_workflow_ids": ["consumer"]}, survival=None)
    attribution = report["tool_start_release_grant_service"]
    assert attribution["verified_tool_start_release_calls"] == int(path != "wrong_tool")
    assert report["released_round_restore"]["tool_start"]["counts"].get(
        "successors_with_first_positive_service", 0
    ) == int(path != "wrong_tool")
    candidates = 0 if path in {"late_queue", "wrong_tool"} else 2 if path == "repeated_grant" else 1
    links = attribution["candidate_links"]
    assert len(links) == candidates
    if not links:
        return
    link = links[0]
    assert link["matched_logical_pause"]
    assert link["released_FULL_tokens"] == 4
    assert link["release_to_grant_ms"] == 50
    assert link["grant_budget_would_not_fit_without_recorded_pause_delta"] == (path != "periodic")
    assert link["ambiguous_association"] == (path in {"competing_release", "repeated_grant"})
    assert link["grant_to_first_worker_service_ms"] == (None if path in {"foreign_attempt", "reentry"} else 55)
    summary = attribution["summaries"]["fixed18_consumers"]
    assert summary["one_to_one_with_bounded_first_worker_service"] == int(path in {"free", "periodic"})


@pytest.mark.parametrize("path", ["exact", "different_attempt", "wrong_clock", "decode"])
def test_recorded_tool_start_consumer_requires_its_own_grant_and_prefill(path):
    from scripts.audit_d2_tool_wait_trace import ToolCall, proactive_service_paths

    donor, consumer = request("donor"), request("consumer", workflow="consumer")
    call = ToolCall(replay("llm_submit", 200, donor), 300, ("tool", "run"), 700)
    release = {
        **donor, "event": "predicted_tool_kv_evicted", "ts_ms": 100_400, "freed_units": {"0": 4},
        "tool_start_release_monotonic_ms": 402,
        "monotonic_clock_domain": "other" if path == "wrong_clock" else "clock",
        "tool_start_consumer_key": dict(consumer, attempt_id=1) if path == "different_attempt" else consumer,
    }
    grant = {
        "event": "agent_dispatch_free_capacity_admitted", "ts_ms": 100_450, "monotonic_s": .45,
        "programs": [{"key": consumer}],
    }
    key = (
        consumer["workflow_id"], consumer["invocation_id"], consumer["context_id"],
        consumer["context_epoch"], "consumer", consumer["attempt_id"],
        consumer["session_id"], consumer["session_generation"],
    )

    def service_for(rid, *, require_prefill):
        assert rid == "consumer" and require_prefill
        return None if path == "decode" else (key, 462, 470, 463)

    links = proactive_service_paths(
        [(0, release), (1, grant)], {call: [(release, 400, 4)]},
        {"consumer": replay("llm_submit", 100_375, consumer)}, service_for,
        offset=100_000, window_ms=2000, domains={"clock"},
    )
    if path in {"different_attempt", "wrong_clock"}:
        assert not links
    else:
        [link] = links
        assert link["consumer_binding"] == "recorded"
        assert link["release_to_grant_ms"] == 48
        assert link["grant_to_first_worker_service_ms"] == (13 if path == "exact" else None)


@pytest.fixture
def shortfall_trace(tmp_path):
    wall = 100_000
    origin = request("origin")
    consumer = request("other", workflow="consumer")
    tool = {"tool_call_id": "call"}
    release = dict(
        origin, event="predicted_tool_kv_evicted", ts_ms=wall + 400,
        dispatch_stage="tool_start", tool_start_release_trigger="native_prewrite_shortfall",
        tool_call_id="call", tool_start_monotonic_ms=300, node_id=1, freed_units={"0": 4},
    )
    selection = dict(
        event="prefill_turnover_sample", forward_iter=20, monotonic_clock_domain="clock",
        request_ids=["other"], launch_monotonic_ms=415,
        phases={"selection": {"start_monotonic_ms": 390, "end_monotonic_ms": 410}},
    )
    batch = dict(
        event="gpu_service_sample", phase="prefill", forward_iter=20, monotonic_clock_domain="clock",
        launch_monotonic_ms=415, service_start_monotonic_ms=430, complete_monotonic_ms=500,
        timing_semantics_version="gpu_service_interval_v1",
        request_samples=[dict(consumer, output_token_delta=5)],
    )
    rows = {
        "replay": [
            replay("workflow_start", 100, origin),
            replay("llm_submit", 200, origin),
            replay("llm_submit", 250, consumer),
            replay("tool_start", 300, origin, tool),
            replay("tool_end", 480, origin, tool),
        ],
        "journal": [
            dict(event="admission_runtime_state", ts_ms=wall, monotonic_ms=0),
            release,
            dict(release, node_id=2, ts_ms=wall + 403, freed_units={"0": 6}),
        ],
        "native": [replay("llm_submit", wall + 270, consumer)],
        "audit": [
            dict(
                batch, phase="decode", forward_iter=19, launch_monotonic_ms=280,
                service_start_monotonic_ms=282, complete_monotonic_ms=285,
                request_samples=[dict(consumer, output_token_delta=0)],
            ),
            batch, selection,
        ],
        "receipts": [],
    }

    def run(archive=None):
        for label, filename in (
            ("replay", "replay/runtime_events.replay.jsonl"),
            ("journal", "opportunities/admission_opportunities.jsonl"),
            ("native", "server/runtime_events.sglang.jsonl"),
            ("audit", "server/runtime_audit.jsonl"),
            ("receipts", "replay/native_requests.jsonl"),
        ):
            write_records(tmp_path, filename, rows[label])
        return measure(archive or Archive(tmp_path), cohort={}, survival=None)

    return rows, run, consumer


def test_prewrite_shortfall_groups_release_nodes_and_uses_worker_start(tmp_path, shortfall_trace):
    _, run, consumer = shortfall_trace

    class OneAuditScan(Archive):
        def records(self, name, *args, **kwargs):
            if name == "server/runtime_audit.jsonl":
                assert name not in self.inputs
            yield from super().records(name, *args, **kwargs)

    report = run(OneAuditScan(tmp_path))
    metric = report["tool_start_prewrite_shortfall_service"]
    assert metric["verified_release_calls"] == metric["sampled_selection_records"] == 1
    assert metric["verified_release_node_records"] == 2
    assert metric["single_observed"] == 1
    assert metric["ambiguous"] == metric["unmatched"] == 0
    link, = metric["observations"]
    assert link["selection_identity"] == ("clock", 20)
    assert link["selection_spans_monotonic_ms"] == [(390, 410)]
    assert link["release_node_records"] == 2
    assert link["released_FULL_tokens"] == 10
    assert link["first_release_monotonic_ms"] == 400
    assert link["last_release_monotonic_ms"] == 403
    assert link["consumers"] == [{
        "key": ("consumer", "consumer-agent", "consumer-context", 0, consumer["request_id"],
                0, "consumer-session", 1),
        "first_worker_start_monotonic_ms": 430,
    }]
    assert report["tool_start_release_grant_service"]["candidate_links"] == []
    assert report["positive_node_release_records_by_stage"]["tool_start"][
        "native_prewrite_shortfall_node_records"
    ] == 2
    assert report["input_prefixes"]["server/runtime_audit.jsonl"]["records"] == 3


@pytest.mark.parametrize("peer_end", (440, 425, 390))
def test_shared_prewrite_requires_all_donors_and_uses_the_first_return(shortfall_trace, peer_end):
    rows, run, _ = shortfall_trace
    donor = request("origin")
    peer = request("peer", workflow="peer")
    peer_tool = {"tool_call_id": "peer-call"}
    rows["replay"].extend([
        replay("llm_submit", 255, peer), replay("tool_start", 320, peer, peer_tool),
        replay("tool_end", peer_end, peer, peer_tool),
    ])
    rows["journal"][1:] = [dict(
        event="tool_start_shared_kv_evicted", ts_ms=100_400, node_id=1, freed_units={"0": 32},
        primary_request_id="origin",
        donors=[
            dict(donor, tool_call_id="call", tool_start_monotonic_ms=300),
            dict(peer, tool_call_id="peer-call", tool_start_monotonic_ms=320),
        ],
    )]
    report = run()
    counts = report["tool_start_shared_release_counts"]
    assert counts["positive_release_records"] == 1 and counts["released_FULL_tokens"] == 32
    metric = report["tool_start_prewrite_shortfall_service"]
    if peer_end < 400:
        assert counts["donor_group_unverified"] == 1
        assert metric["observations"] == []
        return
    assert counts["all_donors_verified_in_tool_wait"] == 1
    assert metric["verified_release_calls"] == 2 and metric["verified_release_node_records"] == 1
    assert report["released_round_restore"]["tool_start_shared"]["counts"]["released_origin_rounds"] == 2
    link, = metric["observations"]
    assert link["shared_donor_request_ids"] == ["origin", "peer"]
    assert link["released_FULL_tokens"] == 32
    assert link["status"] == ("single_observed" if peer_end == 440 else "unmatched")


@pytest.mark.parametrize(("path", "reason", "count"), [
    ("competing_release", "competing_positive_releases", 1),
    ("competing_donor", "competing_positive_releases", 2),
    ("multi_consumer", "multiple_first_service_consumers", 1),
    ("multi_selection", "multiple_selected_requests", 1),
    ("duplicate_selection", "multiple_selection_samples", 1),
    ("duplicate_service", "multiple_service_batches", 1),
    ("overlapping_selections", "multiple_selections_for_donor_call", 2),
])
def test_prewrite_shortfall_marks_ambiguous_observations(shortfall_trace, path, reason, count):
    rows, run, _ = shortfall_trace
    _, batch, selection = rows["audit"]
    if path == "competing_release":
        rows["journal"].append(dict(
            event="terminal_workflow_root_cache_reclaimed", ts_ms=100_407, freed_device_units={"0": 5},
        ))
    elif path == "competing_donor":
        donor = request("second", workflow="second")
        tool = {"tool_call_id": "second"}
        rows["replay"].extend([
            replay("llm_submit", 250, donor), replay("tool_start", 310, donor, tool),
            replay("tool_end", 470, donor, tool),
        ])
        rows["journal"].append(dict(
            rows["journal"][1], **donor, node_id=3, ts_ms=100_407,
            tool_call_id="second", tool_start_monotonic_ms=310,
        ))
    elif path in {"multi_consumer", "multi_selection"}:
        peer = request("peer", workflow="peer")
        rows["replay"].append(replay("llm_submit", 260, peer))
        rows["native"].append(replay("llm_submit", 100_275, peer))
        selection["request_ids"].append("peer")
        batch["request_samples"].append(dict(peer, token_delta=5 if path == "multi_consumer" else 0))
    elif path == "duplicate_selection":
        rows["audit"].append(dict(selection))
    elif path == "duplicate_service":
        rows["audit"].append(dict(batch, request_samples=[]))
    else:
        rows["audit"].append(dict(selection, forward_iter=21))
    metric = run()["tool_start_prewrite_shortfall_service"]
    assert metric["single_observed"] == metric["unmatched"] == 0
    assert metric["ambiguous"] == count
    assert all(reason in link["ambiguity_reasons"] for link in metric["observations"])
    if path in {"multi_consumer", "multi_selection"}:
        assert len(metric["observations"][0]["consumers"]) == (2 if path == "multi_consumer" else 1)


@pytest.mark.parametrize(("path", "reason"), [
    ("missing_selection", "selection_unobserved"),
    ("incomplete_selection", "selection_unobserved"),
    ("outside_selection", "selection_unobserved"),
    ("missing_service", "service_batch_unobserved"),
    ("wrong_forward", "service_batch_unobserved"),
    ("wrong_clock", "service_batch_unobserved"),
    ("wrong_launch", "selection_launch_disagreement"),
    ("earlier_positive", "first_service_not_joined"),
    ("earlier_unidentified_positive", "first_service_not_joined"),
    ("tied_first", "first_service_not_joined"),
    ("wrong_timing_version", "first_service_not_joined"),
    ("wrong_receipt_session_generation", "first_service_not_joined"),
    ("multiple_attempts", "first_service_not_joined"),
    ("missing_ingress", "native_ingress_before_release_unverified"),
    ("late_ingress", "native_ingress_before_release_unverified"),
    ("wrong_ingress_owner", "native_ingress_before_release_unverified"),
    ("missing_worker_start", "worker_start_before_return_unverified"),
    ("worker_at_return", "worker_start_before_return_unverified"),
])
def test_prewrite_shortfall_reports_unmatched_evidence(shortfall_trace, path, reason):
    rows, run, consumer = shortfall_trace
    earlier, batch, selection = rows["audit"]
    if path == "missing_selection":
        rows["audit"].remove(selection)
    elif path == "incomplete_selection":
        selection["event"] = "prefill_turnover_incomplete"
    elif path == "outside_selection":
        selection["phases"]["selection"]["end_monotonic_ms"] = 399
    elif path == "missing_service":
        rows["audit"].remove(batch)
    elif path == "wrong_forward":
        batch["forward_iter"] = 21
    elif path == "wrong_clock":
        selection["monotonic_clock_domain"] = "other-clock"
    elif path == "wrong_launch":
        selection["launch_monotonic_ms"] = 414
    elif path in {"earlier_positive", "earlier_unidentified_positive", "multiple_attempts"}:
        member = earlier["request_samples"][0]
        member["token_delta"] = 5
        if path == "earlier_unidentified_positive":
            member.pop("session_generation")
        if path == "multiple_attempts":
            member["attempt_id"] = 1
    elif path == "tied_first":
        rows["audit"].append(dict(batch, forward_iter=21))
    elif path == "wrong_timing_version":
        batch["timing_semantics_version"] = "unrecognized"
    elif path == "wrong_receipt_session_generation":
        rows["receipts"].append(dict(
            request_id=consumer["request_id"], exact_tokens_verified=True,
            native_receipt=dict(
                identity=["consumer", "consumer-agent", "consumer-context", 0],
                clock_domain="clock", complete_monotonic_ms=510,
                session_id=consumer["session_id"], session_generation=2,
            ),
        ))
    elif path == "missing_ingress":
        rows["native"].clear()
    elif path == "late_ingress":
        rows["native"][0]["ts_ms"] = 100_401
    elif path == "wrong_ingress_owner":
        rows["native"][0]["context_epoch"] = 1
    elif path == "missing_worker_start":
        batch.pop("service_start_monotonic_ms")
    else:
        batch["service_start_monotonic_ms"] = 480
    metric = run()["tool_start_prewrite_shortfall_service"]
    assert metric["verified_release_node_records"] == 2
    assert metric["single_observed"] == metric["ambiguous"] == 0
    assert metric["unmatched"] == 1
    link, = metric["observations"]
    assert link["consumers"] == []
    if "reason" in link:
        assert link["reason"] == reason
    else:
        assert link["unmatched_selection_members"] == {reason: 1}


def test_prewrite_shortfall_counts_unbracketed_nodes_of_a_matched_call(shortfall_trace):
    rows, run, _ = shortfall_trace
    rows["journal"][-1]["ts_ms"] = 100_411
    metric = run()["tool_start_prewrite_shortfall_service"]
    assert metric["single_observed"] == metric["unmatched"] == 1
    assert metric["verified_release_calls"] == 1
    assert metric["release_calls_without_sampled_selection"] == 0
    assert metric["release_node_records_without_sampled_selection"] == 1
    assert [link["release_node_records"] for link in metric["observations"]] == [1, 1]


@pytest.mark.parametrize("path", ["wrong_tool", "missing_return", "no_full_release", "other_trigger"])
def test_prewrite_shortfall_requires_a_validated_same_wait_full_release(shortfall_trace, path):
    rows, run, _ = shortfall_trace
    if path == "missing_return":
        rows["replay"].pop()
    else:
        field, value = {
            "wrong_tool": ("tool_call_id", "other-call"),
            "no_full_release": ("freed_units", {"0": 0, "2": 4}),
            "other_trigger": ("tool_start_release_trigger", "forecast"),
        }[path]
        for row in rows["journal"][1:]:
            row[field] = value
    metric = run()["tool_start_prewrite_shortfall_service"]
    assert metric["verified_release_calls"] == metric["verified_release_node_records"] == 0
    assert metric["observations"] == []
