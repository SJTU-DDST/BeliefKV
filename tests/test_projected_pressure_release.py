import json
from pathlib import Path
import random

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.policy.thunder_agent_migration import MigratingProgram, ThunderAgentMigration
from tests.test_agent_working_set_runtime import runtime


def policy(*, mode="projected_release"):
    result = ThunderAgentMigration(
        1000, retain_blocked=True, pressure_pause_policy=mode,
    )
    for name in ("a", "b", "c"):
        result.programs[name] = MigratingProgram(
            name, 260, steps=2, active=True, pending=True, in_flight=True,
        )
    return result


def marked(value):
    return {item.identity for item in value.programs.values() if item.marked_for_pause}


def test_reference_still_marks_every_running_program_without_crediting_capacity():
    value = policy(mode="reference")
    value.rebalance(now=10.)
    assert marked(value) == {"a", "b", "c"}
    assert value.remaining_capacity() == -80


def test_only_one_future_release_is_planned_for_an_eighty_token_deficit():
    value = policy()
    value.rebalance(now=10.)
    assert marked(value) == {"a"}
    assert all(item.active and item.in_flight for item in value.programs.values())
    assert value.remaining_capacity() == -80
    value.arrive("waiting", 100, now=11.)
    assert not value.allowed("waiting")
    assert value.remaining_capacity() == -80


def test_repeated_control_reuses_existing_marks_until_actual_completion():
    value = policy()
    value.rebalance(now=10.)
    value.rebalance(now=16.)
    assert marked(value) == {"a"}
    assert value.counts["pressure_planned_pauses"] == 1
    assert value.remaining_capacity() == -80


def test_an_unstarted_queued_request_is_not_treated_as_a_future_release():
    value = policy()
    value.programs["a"].in_flight = False
    value.rebalance(now=10.)
    assert marked(value) == {"b"}
    assert value.programs["a"].active
    assert value.remaining_capacity() == -80


def test_without_inflight_work_no_future_release_or_capacity_is_invented():
    value = policy()
    for item in value.programs.values():
        item.in_flight = False
    value.rebalance(now=10.)
    assert not marked(value)
    assert value.remaining_capacity() == -80
    assert value.counts["pressure_projected_shortfall_tokens_last"] == 80
    assert all(value.allowed(identity) for identity in value.programs)


def test_actual_completion_releases_only_the_selected_program():
    value = policy()
    value.rebalance(now=10.)
    value.arrive("waiting", 100, now=11.)
    value.completed("a", 260, now=12.)
    assert value.remaining_capacity() == 280
    assert not value.programs["a"].active
    assert value.programs["b"].active and value.programs["c"].active
    value.rebalance(now=16.)
    assert value.allowed("waiting")
    assert value.remaining_capacity() == 80
    assert value.counts["paused"] == 1


def test_additional_observed_growth_requires_only_enough_additional_marks():
    value = policy()
    value.rebalance(now=10.)
    value.generated("b", 660)
    value.rebalance(now=16.)
    assert marked(value) == {"a", "c"}
    assert value.remaining_capacity() == -480


@pytest.mark.parametrize("protection", ("critical", "restore"))
def test_pressure_does_not_choose_a_protected_join_consumer(protection):
    value = policy()
    if protection == "critical":
        value.programs["a"].critical = True
    else:
        value.programs["a"].restore_until = 100.
    value.rebalance(now=10.)
    assert marked(value) == {"b"}
    assert value.remaining_capacity() == -80


def test_settled_acting_budget_is_released_before_any_running_program_is_marked():
    value = policy()
    value.programs["a"].acting = True
    value.programs["a"].pending = value.programs["a"].in_flight = False
    value.rebalance(now=10.)
    assert not value.programs["a"].active
    assert not marked(value)
    assert value.remaining_capacity() == 280


def test_protected_programs_can_leave_a_shortfall_without_fictitious_credit():
    value = policy()
    for item in value.programs.values():
        item.critical = True
    value.rebalance(now=10.)
    assert not marked(value)
    assert value.remaining_capacity() == -80
    assert value.counts["pressure_projected_shortfall_tokens_last"] == 80


@pytest.mark.parametrize("value", ("unknown", "", None, True))
def test_unsupported_pressure_policy_is_rejected(value):
    with pytest.raises(ValueError, match="projected pressure release"):
        ThunderAgentMigration(1000, retain_blocked=True, pressure_pause_policy=value)


def test_projected_release_requires_the_retained_full_policy():
    with pytest.raises(ValueError, match="retained agent"):
        ThunderAgentMigration(1000, pressure_pause_policy="projected_release")


@pytest.mark.parametrize("label", ("thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph"))
def test_projected_release_cannot_leak_into_a_paper_control(label):
    settings = paper_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_PRESSURE_PAUSE_POLICY": "projected_release",
    }, label)
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"
    assert settings["AGENT_PRESSURE_PAUSE_POLICY"] == "reference"
    assert settings["BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY"] == "reference"


def test_full_environment_preserves_the_explicit_projected_release_choice():
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_PRESSURE_PAUSE_POLICY": "projected_release",
        "EXECUTION_FRONTIER_RESTORE": "0",
    }, "full")
    assert settings["BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY"] == "projected_release"


def test_actual_runtime_binds_and_reports_the_candidate(monkeypatch, tmp_path):
    live = runtime(
        monkeypatch, tmp_path, strategy="thunderagent_retained",
        pressure_pause_policy="projected_release",
    )
    try:
        assert live.agent_pressure_pause_policy == "projected_release"
        assert live._agent_admission.policy.pressure_pause_policy == "projected_release"
        state = live._agent_admission.status()
        assert state["pressure_pause_policy"] == "projected_release"
        assert state["planned_release_grants_capacity"] is False
    finally:
        live.close()


def test_join_restore_cannot_spend_a_future_release_before_completion():
    value = policy()
    value.rebalance(now=10.)
    value.arrive("join", 100, now=11.)
    assert not value.reserve_restore("join", now=11.1, critical=True)
    assert value.restore_owner is None
    assert value.remaining_capacity() == -80
    assert not value.allowed("join")


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_planned_marks_cover_the_shortfall_without_changing_actual_capacity(seed):
    rng = random.Random(seed)
    value = ThunderAgentMigration(
        1000, retain_blocked=True, pressure_pause_policy="projected_release",
    )
    for index in range(8):
        name = str(index)
        value.programs[name] = MigratingProgram(
            name, rng.randrange(40, 400), steps=2, active=True,
            pending=True, in_flight=True,
        )
    before = value.remaining_capacity()
    assert before < 0
    value.rebalance(now=10.)
    credits = [
        item.tokens + value.buffer_tokens
        for item in value.programs.values() if item.marked_for_pause
    ]
    assert sum(credits) >= -before
    assert sum(credits) - max(credits) < -before
    assert value.remaining_capacity() == before
    assert all(item.active and item.in_flight for item in value.programs.values())


def test_candidate_changes_only_the_declared_common_pressure_policy():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_demand_lending_20261027.json").read_bytes())
    candidate = json.loads((root / "configs/migration/predictive_projected_pressure_20261027.json").read_bytes())
    common = dict(candidate["common_environment"])
    assert common.pop("AGENT_PRESSURE_PAUSE_POLICY") == "projected_release"
    assert common == base["common_environment"]
    assert candidate["order"] == ["full"]
    assert candidate["tool_timing_overlay"] == base["tool_timing_overlay"]
    assert candidate["gpu_execution"] == {"registered": False}
