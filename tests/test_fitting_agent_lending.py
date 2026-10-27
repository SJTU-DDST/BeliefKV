"""Bounded fitting loans preserve ready agents and native capacity authority."""

from itertools import combinations
import json
from pathlib import Path
import random
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.policy.thunder_agent_migration import MigratingProgram, ThunderAgentMigration
from tests.test_agent_working_set_runtime import runtime
from tests.test_demand_agent_lending import demand_lending
from tests.test_full_only_native_lifecycle import full_native


def policy(credits=(500, 200), required=180, *, selection="fit", observe=None):
    value = ThunderAgentMigration(
        sum(credits) + 300, retain_blocked=True, recall_join_loans=True,
        lending_selection=selection, observe_lending=observe,
    )
    for index, credit in enumerate(credits):
        name = f"donor-{index}"
        value.programs[name] = MigratingProgram(
            name, credit - 100, active=True, acting=True, blocked="elapsed_tool",
            queue_sequence=index,
        )
    value.programs["running"] = MigratingProgram(
        "running", 200, active=True, pending=True, in_flight=True,
    )
    value.programs["target"] = MigratingProgram(
        "target", required - 100, pending=True, steps=2,
    )
    value.next_control = 100.
    return value


def paused(value):
    return {
        name for name, item in value.programs.items()
        if name.startswith("donor-") and not item.active
    }


@pytest.mark.parametrize("selection,expected", (
    ("largest", {"donor-0"}), ("fit", {"donor-1"}),
))
def test_a_small_gap_does_not_require_pausing_the_largest_owner(selection, expected):
    events = []
    value = policy(selection=selection, observe=events.append)
    assert value.lend_backed_blocked(lambda _: True, now=10.) == "target"
    assert paused(value) == expected
    assert value.allowed("target")
    assert value.programs["running"].in_flight
    assert value.used_tokens <= value.capacity
    assert events[0]["lending_selection"] == selection
    assert events[0]["required_budget_tokens"] == 180


def test_multiple_fitting_donors_avoid_an_unnecessary_large_loan():
    value = policy((500, 350, 200), required=550)
    value.lend_backed_blocked(lambda _: True, now=10.)
    assert paused(value) == {"donor-1", "donor-2"}
    assert value.remaining_capacity() == 0


@pytest.mark.parametrize("seed", range(24))
def test_selected_budget_is_minimal_over_only_currently_proven_owners(seed):
    generator = random.Random(seed)
    credits = [generator.randint(101, 1000) for _ in range(8)]
    required = generator.randint(101, sum(credits) + 1)
    eligible = {f"donor-{index}" for index in range(8) if generator.random() < .8}
    value = policy(credits, required)
    before = value.remaining_capacity()
    value.lend_backed_blocked(lambda name: name in eligible, now=10.)
    feasible = [
        (sum(credits[index] for index in indices), len(indices))
        for count in range(1, 9)
        for indices in combinations(range(8), count)
        if all(f"donor-{index}" in eligible for index in indices)
        and sum(credits[index] for index in indices) >= required
    ]
    if feasible:
        assert value.allowed("target")
        actual = paused(value)
        assert (
            sum(credits[int(name.removeprefix("donor-"))] for name in actual), len(actual),
        ) == min(feasible)
        assert value.remaining_capacity() >= 0
    else:
        assert not paused(value)
        assert not value.allowed("target")
        assert value.remaining_capacity() == before


@pytest.mark.parametrize("protection", ("pending", "in_flight", "critical", "restore"))
def test_fitting_does_not_expand_existing_donor_permissions(protection):
    value = policy()
    item = value.programs["donor-1"]
    if protection == "restore":
        item.restore_until = 100.
    else:
        setattr(item, protection, True)
    proof = Mock(return_value=True)
    value.lend_backed_blocked(proof, now=10.)
    assert paused(value) == {"donor-0"}
    assert "donor-1" not in [call.args[0] for call in proof.call_args_list]


def test_false_proof_cannot_select_the_smaller_donor():
    value = policy()
    value.lend_backed_blocked(lambda name: name != "donor-1", now=10.)
    assert paused(value) == {"donor-0"}


def test_join_preference_is_retained_when_budget_and_owner_count_tie():
    value = policy((200, 200))
    value.programs["donor-1"].blocked = "join"
    value.lend_backed_blocked(lambda _: True, now=10.)
    assert paused(value) == {"donor-1"}


def test_insufficient_budget_does_not_pause_a_partial_set():
    value = policy(required=800)
    value.lend_backed_blocked(lambda _: True, now=10.)
    assert not paused(value)
    assert not value.allowed("target")
    assert not value.counts["paused"]


def test_candidate_proofs_remain_bounded_and_cached_across_eight_targets():
    value = policy(tuple(range(200, 209)), required=1000)
    for index in range(8):
        value.programs[f"waiting-{index}"] = MigratingProgram(
            f"waiting-{index}", 1200, pending=True, steps=2,
        )
    proof = Mock(return_value=False)
    value.lend_backed_blocked(proof, now=10.)
    assert proof.call_count == 8
    assert len({call.args[0] for call in proof.call_args_list}) == 8
    assert not paused(value)


@pytest.mark.parametrize("full_native", (1024,), indirect=True)
def test_fitting_lending_grants_no_device_pages_or_copies(demand_lending):
    live, agents, _, allocator, node, _, target, writes, loads = demand_lending
    agents.lending_selection = agents.policy.lending_selection = "fit"
    available = allocator.available_size()
    agents.tick()
    assert agents.allows(live.visible[target.rid])
    assert allocator.available_size() == available
    assert node.component_data[0].value is not None
    assert node.component_data[0].host_value is None
    assert not writes and not loads
    assert not live.physical_ledger.pending_count
    assert agents.status()["lending_selection"] == "fit"


COMMON = {
    "AGENT_ADMISSION": "thunderagent_retained",
    "AGENT_BLOCKED_LENDING": "backed_demand_recall",
    "AGENT_BLOCKED_BUDGET_PROOF": "observed_demand",
    "AGENT_LENDING_SELECTION": "fit",
}


@pytest.mark.parametrize("mode", MODES)
def test_fitting_selection_is_explicit_and_full_only(mode):
    settings = arm_environment(COMMON, mode)
    assert settings["BELIEFKV_AGENT_LENDING_SELECTION"] == (
        "fit" if mode == "full" else "largest"
    )


@pytest.mark.parametrize("label", ("thunderagent", "thunderagent_acting_decay"))
def test_paper_policies_keep_the_original_selection(label):
    settings = paper_environment(COMMON, label)
    assert settings["AGENT_LENDING_SELECTION"] == "largest"
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"


@pytest.mark.parametrize("proof", ("predicted_pressure", "observed_blocked"))
def test_fitting_does_not_add_native_path_scans_to_old_proof_modes(proof):
    with pytest.raises(ValueError, match="fitting lending"):
        arm_environment({**COMMON, "AGENT_BLOCKED_BUDGET_PROOF": proof}, "full")


@pytest.mark.parametrize("setting", ("", "random", None, 1))
def test_invalid_selection_is_rejected(setting):
    with pytest.raises(ValueError, match="fitting lending"):
        ThunderAgentMigration(1000, retain_blocked=True, lending_selection=setting)


def test_fitting_requires_retained_admission():
    with pytest.raises(ValueError, match="fitting lending"):
        ThunderAgentMigration(1000, lending_selection="fit")


def test_actual_runtime_constructor_binds_and_reports_fitting(monkeypatch, tmp_path):
    live = runtime(
        monkeypatch, tmp_path, capacity=1000, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall", blocked_budget_proof="observed_demand",
        lending_selection="fit",
    )
    try:
        assert live.agent_lending_selection == "fit"
        assert live._agent_admission.policy.lending_selection == "fit"
        assert live._agent_admission.status()["lending_selection"] == "fit"
    finally:
        live.close()


def test_declared_candidate_keeps_all_existing_shared_conditions():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_projected_pressure_20261027.json").read_bytes())
    candidate = json.loads((root / "configs/migration/predictive_fitting_lending_20261027.json").read_bytes())
    assert candidate["common_environment"] == {
        **base["common_environment"], "AGENT_LENDING_SELECTION": "fit",
    }
    assert candidate["tool_timing_overlay"] == base["tool_timing_overlay"]
    assert candidate["control_reuse"] == base["control_reuse"]
    assert candidate["order"] == ["full"]
