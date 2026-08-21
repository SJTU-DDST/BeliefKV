from __future__ import annotations

from dataclasses import replace

from beliefkv.policy.admission import (
    AdmissionCompileBudget,
    AdmissionRequest,
    AdmissionSideState,
    AdmissionTicketCompiler,
    ReclaimRequirement,
    DynamicWorkingSetCandidate,
    DynamicWorkingSetScheduler,
    ObservedAdmissionCandidate,
    ObservedAdmissionScheduler,
    ObservedAdmissionSnapshot,
    VisibleAdmissionIndex,
)


def _request(
    request_id: str,
    *,
    workflow_id: str = "wf",
    prompt_tokens: int = 2,
    output_tokens: int = 1,
) -> AdmissionRequest:
    return AdmissionRequest(
        request_id=request_id,
        workflow_id=workflow_id,
        invocation_id=f"inv-{request_id}",
        context_id=f"ctx-{request_id}",
        context_epoch=0,
        submitted_ts_ms=0,
        uncached_prompt_tokens=prompt_tokens,
        expected_output_tokens=output_tokens,
        kv_bytes_per_token=10,
        prompt_tokens=prompt_tokens,
    )


def _compile(
    index: VisibleAdmissionIndex,
    request_ids: tuple[str, ...],
    *,
    epoch: int = 1,
    hbm_bytes: int = 1_000,
    prefill_tokens: int = 1_000,
):
    return AdmissionTicketCompiler().compile(
        epoch=epoch,
        now_ms=10,
        ordered_request_ids=request_ids,
        entries={entry.request.request_id: entry for entry in index.entries()},
        budget=AdmissionCompileBudget(
            max_prefill_tokens=prefill_tokens,
            max_requests=32,
            max_candidates=64,
            available_hbm_bytes=hbm_bytes,
        ),
        source="observed_reactive",
        reason="bounded_fallback",
    )


def _candidate(
    request_id: str,
    *,
    workflow_id: str = "wf",
    causal_rank: int = 3,
    unblock_depth: int = 0,
    frontier_rank: int = 0,
    fair_rank: int = 0,
    wait_ms: float = 10.0,
    incremental_bytes: int = 100,
    action_unlock_value: float = 1.0,
    service_quantum_tokens: int = 1,
    hbm_envelope_bytes: int = 1,
    starvation: bool = False,
    policy_eligible: bool = True,
    native_index: int = 0,
) -> ObservedAdmissionCandidate:
    return ObservedAdmissionCandidate(
        request_id=request_id,
        workflow_id=workflow_id,
        invocation_id=f"inv-{request_id}",
        native_index=native_index,
        causal_rank=causal_rank,
        unblock_depth=unblock_depth,
        frontier_rank=frontier_rank,
        workflow_fair_rank=fair_rank,
        wait_ms=wait_ms,
        estimated_incremental_bytes=incremental_bytes,
        action_unlock_value=action_unlock_value,
        service_quantum_tokens=service_quantum_tokens,
        hbm_envelope_bytes=hbm_envelope_bytes,
        starvation=starvation,
        policy_eligible=policy_eligible,
    )


def _snapshot(
    *,
    running: int = 2,
    radix_locked: int = 200,
    running_private: int = 100,
    native_hbm: int = 500,
    native_requests: int = 8,
) -> ObservedAdmissionSnapshot:
    return ObservedAdmissionSnapshot(
        hbm_capacity_bytes=1_000,
        reserve_hbm_bytes=100,
        native_available_hbm_bytes=native_hbm,
        native_max_requests=native_requests,
        running_request_count=running,
        radix_locked_bytes=radix_locked,
        running_private_bytes=running_private,
    )


def test_observed_scheduler_orders_causal_progress_before_soft_fairness() -> None:
    scheduler = ObservedAdmissionScheduler(
        active_kv_high_watermark_ratio=0.8,
        minimum_active_requests=1,
    )
    decision = scheduler.decide(
        (
            _candidate(
                "a-normal",
                workflow_id="a",
                causal_rank=3,
                frontier_rank=1,
                fair_rank=1,
                native_index=0,
            ),
            _candidate(
                "b-normal",
                workflow_id="b",
                causal_rank=3,
                frontier_rank=0,
                fair_rank=0,
                native_index=1,
            ),
            _candidate(
                "a-straggler",
                workflow_id="a",
                causal_rank=0,
                unblock_depth=2,
                fair_rank=1,
                native_index=2,
            ),
        ),
        _snapshot(),
    )

    assert decision.ordered_request_ids == (
        "a-straggler",
        "b-normal",
        "a-normal",
    )
    assert decision.mode == "active_kv_bounded"
    assert decision.active_kv_budget_bytes == 720
    assert decision.active_growth_budget_bytes == 420


def test_observed_scheduler_holds_new_growth_above_active_kv_watermark() -> None:
    scheduler = ObservedAdmissionScheduler(
        active_kv_high_watermark_ratio=0.8,
        minimum_active_requests=1,
    )
    candidates = tuple(
        _candidate(f"r{index}", native_index=index) for index in range(4)
    )
    decision = scheduler.decide(
        candidates,
        _snapshot(running=4, radix_locked=800, running_private=100),
    )

    assert decision.mode == "active_kv_pressure_hold"
    assert decision.max_new_requests == 4
    assert decision.active_growth_budget_bytes == 0
    assert decision.active_kv_headroom_bytes == 0


def test_observed_scheduler_floor_is_bounded_and_work_conserving() -> None:
    scheduler = ObservedAdmissionScheduler(
        active_kv_high_watermark_ratio=0.8,
        minimum_active_requests=2,
    )
    candidates = tuple(
        _candidate(f"r{index}", native_index=index) for index in range(6)
    )
    decision = scheduler.decide(
        candidates,
        _snapshot(
            running=0,
            radix_locked=800,
            running_private=100,
            native_hbm=350,
        ),
    )

    assert decision.mode == "work_conserving_floor"
    assert decision.max_new_requests == 2
    assert decision.active_growth_budget_bytes == 350


def test_observed_scheduler_does_not_cap_one_workflow_to_one_ticket() -> None:
    scheduler = ObservedAdmissionScheduler(
        active_kv_high_watermark_ratio=1.0,
        minimum_active_requests=0,
    )
    candidates = tuple(
        _candidate(
            request_id,
            workflow_id="fanout",
            frontier_rank=index,
            native_index=index,
        )
        for index, request_id in enumerate(("a", "b", "c"))
    )
    decision = scheduler.decide(candidates, _snapshot())

    assert decision.ordered_request_ids == ("a", "b", "c")
    assert decision.max_new_requests == 3


def test_observed_scheduler_places_explicit_blockers_after_visible_work() -> None:
    scheduler = ObservedAdmissionScheduler(
        active_kv_high_watermark_ratio=1.0,
        minimum_active_requests=0,
    )
    decision = scheduler.decide(
        (
            _candidate(
                "restore",
                starvation=True,
                policy_eligible=False,
                native_index=0,
            ),
            _candidate("ready", native_index=1),
        ),
        _snapshot(),
    )

    assert decision.ordered_request_ids == ("ready", "restore")


def test_one_workflow_can_receive_multiple_tickets_without_reservation() -> None:
    index = VisibleAdmissionIndex()
    for request_id in ("a", "b", "c"):
        index.register(_request(request_id))

    result = _compile(index, ("a", "b", "c"))

    assert [ticket.request_id for ticket in result.tickets] == ["a", "b", "c"]
    assert index.reserved_bytes == 0


def test_allocator_backed_restore_credit_is_only_spendable_by_owner() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("restore", prompt_tokens=4, output_tokens=2))
    index.register(_request("ordinary", prompt_tokens=2, output_tokens=1))

    result = AdmissionTicketCompiler().compile(
        epoch=1,
        now_ms=10,
        ordered_request_ids=("restore", "ordinary"),
        entries={entry.request.request_id: entry for entry in index.entries()},
        budget=AdmissionCompileBudget(
            max_prefill_tokens=100,
            max_requests=2,
            max_candidates=2,
            # The allocator already removed restore's 60-byte reservation.
            available_hbm_bytes=30,
        ),
        source="restore_liveness",
        reason="allocator_backed_lease",
        reservation_credits={"restore": 60},
    )

    assert [ticket.request_id for ticket in result.tickets] == [
        "restore",
        "ordinary",
    ]
    assert result.tickets[0].reservation_credit_bytes == 60
    assert result.tickets[1].reservation_credit_bytes == 0


def test_wait_restore_skips_only_the_dependent_request() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("restore"), bundle_generations={"bundle": "g1"})
    index.register(_request("ready"))
    index.set_wait_restore("restore", ("bundle",), reason="h2d_inflight")

    result = _compile(index, ("restore", "ready"))

    assert [ticket.request_id for ticket in result.tickets] == ["ready"]
    assert ("restore", AdmissionSideState.WAIT_RESTORE.value) in result.skipped
    assert index.reserved_bytes == 0


def test_expired_epoch_and_local_prefix_change_invalidate_ticket() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("a"), bundle_generations={"bundle": "g1"})
    ticket = _compile(index, ("a",), epoch=3).tickets[0]

    expired = index.validate_ticket(ticket, epoch=4)
    assert not expired.valid
    assert expired.reasons == ("epoch_expired",)

    index.observe_prefix(
        "a",
        uncached_prompt_tokens=1,
        bundle_generations={"bundle": "g2"},
    )
    stale = index.validate_ticket(ticket, epoch=3)
    assert not stale.valid
    assert "prefix_generation" in stale.reasons
    assert "bundle_generation:bundle" in stale.reasons


def test_safe_prefix_rematch_updates_index_without_invalidating_its_ticket() -> None:
    index = VisibleAdmissionIndex()
    index.register(
        _request("a", prompt_tokens=8),
        bundle_generations={"bundle": "g1"},
    )
    ticket = _compile(index, ("a",), epoch=3).tickets[0]

    validation = index.validate_and_observe_prefix_rematch(
        ticket,
        epoch=3,
        uncached_prompt_tokens=3,
        bundle_generations={"bundle": "g1"},
    )

    assert validation.valid
    assert index.get("a").request.uncached_prompt_tokens == 3
    assert not index.validate_ticket(ticket, epoch=3).valid


def test_prefix_rematch_rejects_increased_demand_and_physical_change() -> None:
    index = VisibleAdmissionIndex()
    index.register(
        _request("a", prompt_tokens=4),
        bundle_generations={"bundle": "g1"},
    )
    ticket = _compile(index, ("a",), epoch=3).tickets[0]

    increased = index.validate_and_observe_prefix_rematch(
        ticket,
        epoch=3,
        uncached_prompt_tokens=5,
        bundle_generations={"bundle": "g1"},
    )
    changed = index.validate_and_observe_prefix_rematch(
        ticket,
        epoch=3,
        uncached_prompt_tokens=2,
        bundle_generations={"bundle": "g2"},
    )
    added = index.validate_and_observe_prefix_rematch(
        ticket,
        epoch=3,
        uncached_prompt_tokens=2,
        bundle_generations={"bundle": "g1", "new-bundle": "g1"},
    )

    assert increased.reasons == ("prefix_demand_increased",)
    assert changed.reasons == ("bundle_generation:bundle",)
    assert added.reasons == ("bundle_set_changed",)
    assert index.get("a").request.uncached_prompt_tokens == 4


def test_bundle_change_invalidates_only_the_dependent_ticket() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("a"), bundle_generations={"bundle-a": "g1"})
    index.register(_request("b"), bundle_generations={"bundle-b": "g1"})
    result = _compile(index, ("a", "b"), epoch=7)
    by_request = result.by_request_id

    a_validation = index.validate_ticket(
        by_request["a"],
        epoch=7,
        bundle_generations={"bundle-a": "g2"},
    )
    b_validation = index.validate_ticket(
        by_request["b"],
        epoch=7,
        bundle_generations={"bundle-b": "g1"},
    )

    assert not a_validation.valid
    assert a_validation.reasons == ("bundle_generation:bundle-a",)
    assert b_validation.valid


def test_prompt_change_does_not_invalidate_an_unrelated_request() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("a"))
    index.register(_request("b"))
    result = _compile(index, ("a", "b"), epoch=8)

    index.update_request(
        replace(_request("a"), uncached_prompt_tokens=3, prompt_tokens=3),
        prompt_changed=True,
    )

    assert not index.validate_ticket(result.by_request_id["a"], epoch=8).valid
    assert index.validate_ticket(result.by_request_id["b"], epoch=8).valid


def test_compiler_issues_a_chunk_ticket_for_an_oversized_prompt() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("large", prompt_tokens=20, output_tokens=0))
    index.register(_request("small", prompt_tokens=2, output_tokens=0))

    result = _compile(
        index,
        ("large", "small"),
        hbm_bytes=1_000,
        prefill_tokens=10,
    )

    assert [ticket.request_id for ticket in result.tickets] == ["small", "large"]
    assert result.tickets[0].estimated_prefill_tokens == 2
    assert result.tickets[0].epoch_incremental_bytes == 20
    assert result.tickets[1].estimated_prefill_tokens == 8
    assert result.tickets[1].estimated_incremental_bytes == 200
    assert result.tickets[1].epoch_incremental_bytes == 80


def test_compiler_batches_short_prefills_before_one_chunked_tail() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("large-a", prompt_tokens=20, output_tokens=0))
    index.register(_request("short-a", prompt_tokens=2, output_tokens=0))
    index.register(_request("short-b", prompt_tokens=3, output_tokens=0))
    index.register(_request("large-b", prompt_tokens=30, output_tokens=0))

    result = _compile(
        index,
        ("large-a", "short-a", "short-b", "large-b"),
        hbm_bytes=1_000,
        prefill_tokens=10,
    )

    assert [ticket.request_id for ticket in result.tickets] == [
        "short-a",
        "short-b",
        "large-a",
    ]
    assert [ticket.estimated_prefill_tokens for ticket in result.tickets] == [2, 3, 5]
    assert ("large-b", "single_chunked_request_budget") in result.skipped


def test_priority_oversized_prompt_keeps_bounded_chunk_progress() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("large", prompt_tokens=100, output_tokens=0))
    for suffix in range(5):
        index.register(
            _request(f"short-{suffix}", prompt_tokens=2, output_tokens=0)
        )

    result = _compile(
        index,
        ("large", *(f"short-{suffix}" for suffix in range(5))),
        hbm_bytes=2_000,
        prefill_tokens=10,
    )

    assert result.tickets[-1].request_id == "large"
    assert result.tickets[-1].estimated_prefill_tokens >= 3
    assert sum(ticket.estimated_prefill_tokens for ticket in result.tickets) <= 10
    assert any(ticket.request_id.startswith("short-") for ticket in result.tickets)


def test_priority_chunk_is_trimmed_to_reserved_hbm_budget() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("large", prompt_tokens=100, output_tokens=0))
    index.register(_request("short", prompt_tokens=2, output_tokens=0))

    result = _compile(
        index,
        ("large", "short"),
        hbm_bytes=50,
        prefill_tokens=10,
    )

    assert [ticket.request_id for ticket in result.tickets] == ["short", "large"]
    assert result.tickets[-1].estimated_prefill_tokens == 3
    assert sum(ticket.epoch_incremental_bytes for ticket in result.tickets) == 50


def test_dynamic_working_set_keeps_slots_full_and_enables_replacement() -> None:
    scheduler = DynamicWorkingSetScheduler(
        max_workflows=4,
        pressure_enter_ratio=0.8,
        pressure_exit_ratio=0.7,
        minimum_ready_requests=2,
        minimum_hold_epochs=0,
    )
    candidates = (
        DynamicWorkingSetCandidate("fair", 2, 0, 1.0),
        DynamicWorkingSetCandidate("unlock", 2, 1, 5.0),
        DynamicWorkingSetCandidate("tail", 2, 2, 0.5),
    )

    fill = scheduler.decide(
        candidates,
        epoch=1,
        hbm_used_bytes=500,
        hbm_capacity_bytes=1_000,
        native_request_slots=6,
    )
    pressure = scheduler.decide(
        candidates,
        epoch=2,
        hbm_used_bytes=950,
        hbm_capacity_bytes=1_000,
        native_request_slots=6,
    )

    assert fill.active_workflow_ids == ("unlock", "fair", "tail")
    assert fill.target_ready_requests == 6
    assert not fill.pressure_actions_enabled
    assert pressure.active_workflow_ids == ("unlock", "fair", "tail")
    assert pressure.target_ready_requests == 6
    assert pressure.selected_ready_requests == 6
    assert pressure.pressure_actions_enabled


def test_dynamic_working_set_preserves_mandatory_restore_above_hard_window() -> None:
    scheduler = DynamicWorkingSetScheduler(
        max_workflows=1,
        pressure_enter_ratio=0.8,
        pressure_exit_ratio=0.7,
        minimum_ready_requests=1,
        minimum_hold_epochs=0,
    )
    decision = scheduler.decide(
        (
            DynamicWorkingSetCandidate("restore-a", 1, 2, 0.0, mandatory=True),
            DynamicWorkingSetCandidate("restore-b", 1, 3, 0.0, mandatory=True),
            DynamicWorkingSetCandidate("unlock", 4, 0, 5.0),
        ),
        epoch=1,
        hbm_used_bytes=900,
        hbm_capacity_bytes=1_000,
        native_request_slots=4,
    )

    assert decision.active_workflow_ids == ("restore-a", "restore-b")


def test_dynamic_working_set_uses_hysteresis_for_pressure_actions() -> None:
    scheduler = DynamicWorkingSetScheduler(
        max_workflows=2,
        pressure_enter_ratio=0.8,
        pressure_exit_ratio=0.7,
        minimum_ready_requests=1,
        minimum_hold_epochs=2,
    )
    candidates = (DynamicWorkingSetCandidate("wf", 1, 0, 1.0),)

    first = scheduler.decide(
        candidates,
        epoch=1,
        hbm_used_bytes=750,
        hbm_capacity_bytes=1_000,
        native_request_slots=1,
    )
    entered = scheduler.decide(
        candidates,
        epoch=3,
        hbm_used_bytes=900,
        hbm_capacity_bytes=1_000,
        native_request_slots=1,
    )
    held = scheduler.decide(
        candidates,
        epoch=4,
        hbm_used_bytes=600,
        hbm_capacity_bytes=1_000,
        native_request_slots=1,
    )
    exited = scheduler.decide(
        candidates,
        epoch=5,
        hbm_used_bytes=600,
        hbm_capacity_bytes=1_000,
        native_request_slots=1,
    )

    assert not first.pressure_actions_enabled
    assert entered.pressure_actions_enabled
    assert held.pressure_actions_enabled
    assert not exited.pressure_actions_enabled


def test_dynamic_working_set_uses_fair_rank_only_after_unlock_value() -> None:
    scheduler = DynamicWorkingSetScheduler(
        max_workflows=1,
        pressure_enter_ratio=0.8,
        pressure_exit_ratio=0.7,
        minimum_ready_requests=1,
        minimum_hold_epochs=0,
    )
    decision = scheduler.decide(
        (
            DynamicWorkingSetCandidate("fair", 1, 0, 0.0),
            DynamicWorkingSetCandidate("deep-tail", 1, 20, 100.0),
        ),
        epoch=1,
        hbm_used_bytes=900,
        hbm_capacity_bytes=1_000,
        native_request_slots=1,
    )

    assert decision.active_workflow_ids == ("deep-tail",)


def test_compiler_trims_priority_chunk_after_short_request_uses_hbm() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("large", prompt_tokens=20, output_tokens=0))
    index.register(_request("small", prompt_tokens=2, output_tokens=0))

    result = _compile(
        index,
        ("large", "small"),
        hbm_bytes=50,
        prefill_tokens=10,
    )

    assert [ticket.request_id for ticket in result.tickets] == ["small", "large"]
    assert result.tickets[-1].estimated_prefill_tokens == 3
    assert sum(ticket.epoch_incremental_bytes for ticket in result.tickets) == 50


def test_transition_barrier_fails_closed_until_reopened() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("a"), transition_generation=1)
    index.set_policy_blocked("a", reason="transition_open")

    blocked = _compile(index, ("a",), epoch=1)
    assert not blocked.tickets
    assert blocked.skipped == (("a", "policy_blocked"),)

    index.set_transition_generation("a", 2)
    index.set_visible("a")
    visible = _compile(index, ("a",), epoch=2)
    assert [ticket.request_id for ticket in visible.tickets] == ["a"]


def test_bounded_hbm_skip_emits_short_fragment_reclaim_requirement() -> None:
    index = VisibleAdmissionIndex()
    index.register(
        AdmissionRequest(
            request_id="blocked",
            workflow_id="wf",
            invocation_id="inv",
            context_id="ctx",
            context_epoch=2,
            submitted_ts_ms=1.0,
            uncached_prompt_tokens=20,
            expected_output_tokens=100,
            kv_bytes_per_token=10,
            fixed_overhead_bytes=20,
            prompt_tokens=100,
        )
    )

    result = AdmissionTicketCompiler().compile(
        epoch=3,
        now_ms=11.0,
        ordered_request_ids=("blocked",),
        entries={entry.request.request_id: entry for entry in index.entries()},
        budget=AdmissionCompileBudget(
            max_prefill_tokens=32,
            max_requests=1,
            max_candidates=1,
            available_hbm_bytes=0,
            decode_quantum_tokens=8,
            allocator_guard_bytes=30,
        ),
        source="joint_bounded_seed",
        reason="bounded_hbm",
    )

    assert result.skipped == (("blocked", "bounded_hbm_budget"),)
    assert result.reclaim_requirements == (
        ReclaimRequirement(
            beneficiary_request_id="blocked",
            required_startup_bytes=50,
            required_growth_bytes=280,
            current_prefix_bytes=800,
            waited_ms=10.0,
            skip_reason="bounded_hbm_budget",
        ),
    )


def test_ticket_reserves_only_a_bounded_decode_quantum() -> None:
    index = VisibleAdmissionIndex()
    index.register(_request("long-decode", prompt_tokens=2, output_tokens=100))

    result = AdmissionTicketCompiler().compile(
        epoch=1,
        now_ms=10,
        ordered_request_ids=("long-decode",),
        entries={entry.request.request_id: entry for entry in index.entries()},
        budget=AdmissionCompileBudget(
            max_prefill_tokens=32,
            max_requests=1,
            max_candidates=1,
            available_hbm_bytes=180,
            decode_quantum_tokens=16,
        ),
        source="joint_bounded_seed",
        reason="bounded_fragment",
    )

    assert [ticket.request_id for ticket in result.tickets] == ["long-decode"]
    assert result.tickets[0].epoch_incremental_bytes == 180
    assert result.tickets[0].estimated_incremental_bytes == 1_020
    assert not result.reclaim_requirements


def test_observed_scheduler_uses_maxweight_before_fairness_within_causal_class() -> None:
    scheduler = ObservedAdmissionScheduler(
        active_kv_high_watermark_ratio=1.0,
        minimum_active_requests=0,
    )
    decision = scheduler.decide(
        (
            _candidate(
                "fair-heavy",
                workflow_id="fair",
                fair_rank=0,
                action_unlock_value=1.0,
                service_quantum_tokens=4,
                hbm_envelope_bytes=400,
            ),
            _candidate(
                "useful-light",
                workflow_id="throughput",
                fair_rank=10,
                action_unlock_value=2.0,
                service_quantum_tokens=8,
                hbm_envelope_bytes=100,
                native_index=1,
            ),
        ),
        _snapshot(),
    )

    assert decision.ordered_request_ids[:2] == (
        "useful-light",
        "fair-heavy",
    )


def test_observed_scheduler_starvation_floor_overrides_maxweight() -> None:
    scheduler = ObservedAdmissionScheduler(
        active_kv_high_watermark_ratio=1.0,
        minimum_active_requests=0,
    )
    decision = scheduler.decide(
        (
            _candidate("old", wait_ms=30_001, starvation=True),
            _candidate(
                "high-utility",
                action_unlock_value=100,
                service_quantum_tokens=100,
                hbm_envelope_bytes=1,
                native_index=1,
            ),
        ),
        _snapshot(),
    )

    assert decision.ordered_request_ids[0] == "old"


def test_dynamic_working_set_uses_short_fragment_maxweight() -> None:
    scheduler = DynamicWorkingSetScheduler(
        max_workflows=1,
        pressure_enter_ratio=0.8,
        pressure_exit_ratio=0.7,
        minimum_ready_requests=1,
        minimum_hold_epochs=0,
    )
    decision = scheduler.decide(
        (
            DynamicWorkingSetCandidate(
                "fair-heavy",
                1,
                0,
                2.0,
                startup_bytes=1000,
                gpu_work_tokens=1,
            ),
            DynamicWorkingSetCandidate(
                "efficient",
                1,
                10,
                1.0,
                startup_bytes=100,
                gpu_work_tokens=10,
            ),
        ),
        epoch=1,
        hbm_used_bytes=900,
        hbm_capacity_bytes=1_000,
        native_request_slots=1,
    )

    assert decision.active_workflow_ids == ("efficient",)
