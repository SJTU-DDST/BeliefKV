from copy import deepcopy

import pytest

from scripts.audit_workflow_admission_funnel import compare_propagation, competing_categories, inspect
from tests.test_workflow_size_selection_audit import choice, service


def population():
    return [
        {"workflow_id": "workflow-" + owner, "project": owner, "eligible": True, "size_tier": "medium"}
        for owner in ("short", "small")
    ]


def selection():
    row = choice()
    row["logical_ready_paused_before_resume"] = {"short": True, "small": True}
    return row


def positive(owner, instant, *, phase="decode", delta=1, attempt=0):
    row = service(owner, instant, attempt=attempt)
    row["request_samples"][0].update(phase=phase, token_delta=delta)
    return row


def status():
    return {
        "event": "admission_runtime_state",
        "agent_admission": {
            "counts": {"free_continuation_admitted": 1},
            "workflow_size_resume_scope": "paused_within_original_category",
            "workflow_size_other_admission_paths": "unchanged_join_and_free_continuation",
        },
    }


def test_remaining_rank_competition_does_not_require_initial_short_long_tiers():
    result = inspect([selection(), status()], [
        positive("short", 10050.), positive("small", 14000.),
    ], population())
    assert result["counts"]["capacity_competition_within_reference_category"] == 1
    assert result["initial_size_tier_competition"]["controls_with_ready_short_long_capacity_competition"] == 0
    assert result["counts"]["changed_winners_with_following_positive_service"] == 1
    assert result["actual_winner_lead_over_reference_only_loser_ms"]["p50"] == 3950.


def test_empty_budget_no_paused_candidates_and_unfit_ready_candidates_are_different():
    zero, no_paused, too_large = (selection() for _ in range(3))
    for row, capacity in ((zero, 100), (no_paused, 1000)):
        row.update(
            capacity_tokens=capacity, ordered=[], selected_tokens=0,
            selected_in_priority_order=[], logical_ready_paused_before_resume={}, candidates=[],
        )
    too_large.update(capacity_tokens=200, selected_tokens=0, selected_in_priority_order=[])
    for item in too_large["candidates"]:
        item["active_after_pressure"] = False
    result = inspect([zero, no_paused, too_large, status()], [], population())
    assert result["counts"]["empty_no_budget"] == 1
    assert result["counts"]["empty_with_positive_budget"] == 1
    assert result["counts"]["no_ready_candidate_fits_individually"] == 1


@pytest.mark.parametrize("variant", ("zero", "wrong_attempt", "revoked", "before_only"))
def test_only_following_positive_exact_attempt_work_can_consume_a_choice(variant):
    row = selection()
    services = [positive("short", 10050.)]
    if variant == "zero":
        services[0]["request_samples"][0]["token_delta"] = 0
    elif variant == "wrong_attempt":
        services[0]["request_samples"][0]["attempt_id"] = 1
    elif variant == "revoked":
        row["candidates"][0]["active_after_pressure"] = False
    else:
        services[0]["service_start_monotonic_ms"] = 9000.
    result = inspect([row, status()], services, population())
    assert not result["counts"].get("changed_winners_with_following_positive_service", 0)


def test_request_overlap_or_prefill_is_not_mistaken_for_a_decode_token():
    result = inspect([selection(), status()], [positive("short", 10010., phase="prefill")], population())
    winner = result["changed_decisions"][0]["winners"][0]
    assert winner["first_positive_service_after_choice_ms"] == 10.
    assert winner["first_positive_decode_after_choice_ms"] is None


def test_immediate_pressure_revocation_has_no_winner_service_lead():
    row = selection()
    row["candidates"][0]["active_after_pressure"] = False
    result = inspect([row, status()], [
        positive("short", 10050.), positive("small", 14000.),
    ], population())
    assert result["counts"]["changed_winners_revoked_by_immediate_pressure"] == 1
    assert result["actual_winner_lead_over_reference_only_loser_ms"] == {"count": 0}


def test_repeated_episodes_are_not_unique_changed_request_attempts():
    first, second = selection(), selection()
    second["monotonic_s"] = 10.02
    result = inspect([first, second, status()], [positive("short", 10050.)], population())
    assert result["counts"]["changed_winners"] == 2
    assert result["counts"]["unique_changed_winner_attempts"] == 1
    assert result["counts"]["unique_changed_winner_attempts_with_positive_service"] == 1


def test_cross_category_counterfactual_is_distinct_from_allowed_local_ordering():
    row = selection()
    row["ordered"][1][1] = 1
    meta = {item["key"]["invocation_id"]: item for item in row["candidates"]}
    assert competing_categories(row, meta) == []
    assert len(competing_categories(row, meta, ignore_categories=True)) == 1


@pytest.mark.parametrize("variant", ("missing", "different_population", "different_clock"))
def test_incomplete_bindings_are_rejected(variant):
    row = selection()
    services = [positive("short", 10050.)]
    if variant == "missing":
        del row["logical_ready_paused_before_resume"]
    elif variant == "different_population":
        row["candidates"][0]["key"]["root_workflow_id"] = "other"
    else:
        other = deepcopy(services[0])
        other["service_start_monotonic_ms"] += 100
        other["monotonic_clock_domain"] = "other"
        services.append(other)
    with pytest.raises(ValueError):
        inspect([row, status()], services, population())


def propagation_fixture():
    report = inspect([selection(), status()], [positive("short", 10050.)], population())
    summary = {
        "workflow_arrival_origin_monotonic_ms": 1000.,
        "workflow_arrival_clock_domain": "same-host",
        "workflow_arrivals": {"arrival_schedule": [
            {"workflow_id": "workflow-short", "offset_seconds": 0.},
            {"workflow_id": "workflow-small", "offset_seconds": 0.},
        ]},
        "workflow_elapsed_ms": {"workflow-short": 20000., "workflow-small": 30000.},
    }
    receipt = {
        "request_id": "request-short", "exact_tokens_verified": True,
        "native_receipt": {"identity": ["workflow-short", "short", "context-short", 0],
                           "clock_domain": "same-host", "session_id": "candidate-session"},
        "response_metrics": {"first_output_monotonic_ms": 2000.},
        "native_complete_monotonic_ms": 3000., "prompt_tokens": 100, "output_tokens": 10,
        "prompt_sha256": "same-prompt", "output_sha256": "same-output",
    }
    return report, summary, receipt


def test_local_request_advance_can_be_offset_by_other_workflow_delays():
    report, candidate, actual = propagation_fixture()
    control, previous = deepcopy(candidate), deepcopy(actual)
    control["workflow_elapsed_ms"].update({"workflow-short": 25000., "workflow-small": 20000.})
    previous["response_metrics"]["first_output_monotonic_ms"] += 1000.
    previous["native_complete_monotonic_ms"] += 1000.
    previous["native_receipt"]["session_id"] = "different-control-session"
    result = compare_propagation(report, candidate, control, [actual], [previous])
    assert result["requests"][0]["advancement_s"]["first_output_s"] == 1.
    assert result["summary"]["selected_workflow_JCT_advancement_sum_s"] == 5.
    assert result["summary"]["other_workflow_JCT_advancement_sum_s"] == -10.
    assert result["summary"]["whole_population_mean_JCT_advancement_s"] == -2.5


@pytest.mark.parametrize("variant", ("clock", "tokens", "missing_output", "identity", "arrivals"))
def test_propagation_rejects_mismatched_work_clocks_or_unobserved_output(variant):
    report, candidate, actual = propagation_fixture()
    control, previous = deepcopy(candidate), deepcopy(actual)
    if variant == "clock":
        previous["native_receipt"]["clock_domain"] = "other"
    elif variant == "tokens":
        previous["output_sha256"] = "changed"
    elif variant == "missing_output":
        previous["response_metrics"].clear()
    elif variant == "identity":
        previous["native_receipt"]["identity"][1] = "other"
    else:
        control["workflow_arrivals"]["arrival_schedule"][0]["offset_seconds"] = 2.
    with pytest.raises(ValueError):
        compare_propagation(report, candidate, control, [actual], [previous])


def test_development_arrival_selection_uses_fixed_slot_geometry_and_retains_every_trial():
    from experiments.reports.workflow_mix_20261029.screen_main_arrivals import screen, SEEDS
    from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan

    ids = [str(i) for i in range(18)]
    result = screen(ids, rate=.01, makespan=1800.)
    assert [row["seed"] for row in result["trials"]] == list(SEEDS)
    eligible = [row for row in result["trials"] if row["eligible_by_slot_geometry"]]
    assert result["selected_seed"] == (eligible[0]["seed"] if eligible else None)
    assert result["scope"]["unconditional_Poisson_sample_claimed"] is False
    assert result["scope"]["GPU_policy_results_used_for_seed_selection"] is False
    for row in result["trials"]:
        plan = permuted_poisson_arrival_plan(
            ids, rate_per_second=.01, seed=row["seed"], permutation_seed=21, initial_workflows=5,
        )
        assert row["arrival_schedule_sha256"] == plan["arrival_schedule_sha256"]
        assert sum(row["gaps_s"]) == pytest.approx(row["last_submission_s"])
        assert row["eligible_by_slot_geometry"] == (
            row["first_gap_s"] <= 100. and row["maximum_gap_s"] <= 300.
            and row["last_submission_s"] <= 1800.
        )


def test_arrival_window_guard_rejects_a_burst_even_without_a_large_gap():
    from experiments.reports.workflow_mix_20261029.screen_main_arrivals import screen

    ids = [str(i) for i in range(18)]
    result = screen(ids, rate=.01, makespan=1800., seeds=(28,), window_ratio=(.8, 1.2))
    assert result["selected_seed"] is None
    assert result["trials"][0]["first_gap_s"] <= 100.
    assert result["trials"][0]["maximum_gap_s"] <= 300.
    assert "submission_window_too_bursty_for_expected_rate" in result["trials"][0]["reasons"]


def test_normalized_seed_selection_is_independent_of_control_speed():
    from experiments.reports.workflow_mix_20261029.screen_main_arrivals import screen

    ids = [str(i) for i in range(18)]
    common = {"seeds": tuple(range(32, 48)), "window_ratio": (.8, 1.2)}
    first = screen(ids, rate=.009, makespan=1800., **common)
    second = screen(ids, rate=.0045, makespan=3600., **common)
    assert first["selected_seed"] == second["selected_seed"]
    assert first["selected_seed"] is not None
    for left, right in zip(first["trials"], second["trials"], strict=True):
        assert left["eligible_by_slot_geometry"] == right["eligible_by_slot_geometry"]
        assert left["reasons"] == right["reasons"]
        assert left["last_submission_s"] * 2 == pytest.approx(right["last_submission_s"])


@pytest.mark.parametrize("schedule_drift", (False, True))
def test_arrival_entry_binds_the_screen_to_the_frozen_generator(tmp_path, monkeypatch, schedule_drift):
    from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
    from experiments.reports.workflow_mix_20261029 import screen_main_arrivals as entry

    ids = [str(i) for i in range(18)]
    makespan = 2149.987822508812
    control, review, population_path = (
        tmp_path / name for name in ("control.json", "review.json", "population.json")
    )
    entry.write(control, {})
    entry.write(review, {"metrics": {"makespan_s": makespan}})
    entry.write(population_path, {"workflows": [{"workflow_id": value} for value in ids]})
    output = tmp_path / "arrivals"
    monkeypatch.setattr(entry.sys, "argv", [
        "screen_main_arrivals.py", "--control", str(control), "--control-review", str(review),
        "--population", str(population_path), "--output-dir", str(output),
        "--seed-start", "32", "--seed-stop", "47",
        "--expected-window-min-ratio", ".8", "--expected-window-max-ratio", "1.2",
    ])

    def prepare(command, *, cwd, check):
        assert check is True and cwd == entry.ROOT
        seed = int(command[command.index("--arrival-seed") + 1])
        assert seed == 32
        # Match the real calibration arithmetic, including floating-point order.
        frozen = permuted_poisson_arrival_plan(
            ids, rate_per_second=len(ids) / makespan * .9,
            seed=seed + int(schedule_drift), permutation_seed=21, initial_workflows=5,
        )
        output.mkdir()
        entry.write(output / "arrivals.json", frozen)

    monkeypatch.setattr(entry.subprocess, "run", prepare)
    if schedule_drift:
        with pytest.raises(ValueError, match="screened and frozen arrival schedules differ"):
            entry.main()
    else:
        entry.main()
    evidence = entry.causal.load(output / "selection.json")
    assert evidence["frozen_arrival_schedule_match_verified"] is not schedule_drift
    assert str((output / "arrivals.json").resolve()) in evidence["inputs_sha256"]
    assert evidence["GPU_action"] is False
