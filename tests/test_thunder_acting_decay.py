from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.experiments.paper_baselines import DISABLED_POLICIES, environment, validate_state
from beliefkv.policy.paper_baselines import (
    ThunderActingDecayWorkingSet, ThunderProgram, ThunderWorkingSet,
)
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_causal_comparison import reference
from tests.test_paper_baseline_runtime import request


def blocked(policy_type=ThunderActingDecayWorkingSet):
    policy = policy_type(1000)
    policy.arrive("acting", 800, now=0.)
    policy.completed("acting", 800, now=0.)
    policy.arrive("waiting", 700, now=.1)
    return policy


@pytest.mark.parametrize("elapsed,expected", ((0., 100), (1., 500), (2., 700), (10., 900)))
def test_only_tokens_decay_and_the_full_per_agent_buffer_remains(elapsed, expected):
    policy = blocked()
    assert policy.remaining_capacity_with_decay(now=elapsed) == expected
    assert policy.remaining_capacity() == 100


def test_public_optional_resume_can_replace_an_acting_owner_without_discounting_pressure():
    candidate, default = blocked(), blocked(ThunderWorkingSet)
    assert candidate.tick(now=5.) == ("waiting",)
    assert candidate.programs["waiting"].active
    assert not candidate.programs["acting"].active
    assert candidate.remaining_capacity() == 200
    assert candidate.last_resume_capacity_tokens == 875
    assert default.tick(now=5.) == ()
    assert default.programs["acting"].active and not default.programs["waiting"].active


def test_a_short_tool_gets_no_capacity_handover():
    policy = blocked()
    assert policy.tick(now=.05) == ()
    assert policy.programs["acting"].active and not policy.programs["waiting"].active


def test_new_program_arrival_still_uses_undiscounted_capacity():
    policy = ThunderActingDecayWorkingSet(1000)
    policy.arrive("acting", 800, now=0.)
    policy.completed("acting", 800, now=0.)
    assert policy.remaining_capacity_with_decay(now=10.) == 900
    policy.arrive("new", 700, now=10.)
    assert not policy.programs["new"].active
    assert policy.remaining_capacity() == 100


def test_a_tool_return_immediately_restores_its_full_reasoning_charge():
    policy = blocked()
    policy.arrive("acting", 850, now=10.)
    assert "acting" not in policy.acting_since
    assert policy.remaining_capacity_with_decay(now=100.) == 50
    assert policy.remaining_capacity() == 50


def test_author_decay_does_not_apply_the_configurable_acting_weight():
    policy = ThunderActingDecayWorkingSet(1000, acting_weight=.25)
    policy.arrive("a", 800, now=0.)
    policy.completed("a", 800, now=0.)
    assert policy.remaining_capacity() == 700
    assert policy.remaining_capacity_with_decay(now=0.) == 100


def test_decayed_budget_does_not_credit_running_reasoning_or_its_pause_mark():
    policy = ThunderActingDecayWorkingSet(500)
    policy.arrive("running", 300, now=0.)
    policy.generated("running", 800)
    assert policy.remaining_capacity_with_decay(now=100.) == -400
    policy.tick(now=100.)
    assert policy.programs["running"].marked_for_pause
    assert policy.programs["running"].active
    assert policy.remaining_capacity() == -400
    policy.completed("running", 802, now=100.1)
    assert not policy.programs["running"].active


def test_paused_acting_program_keeps_its_observed_transition_until_a_new_request():
    policy = ThunderActingDecayWorkingSet(1000)
    policy.arrive("a", 800, now=0.)
    policy.programs["a"].marked_for_pause = True
    policy.completed("a", 800, now=1.)
    assert not policy.programs["a"].active
    assert policy.acting_since["a"] == 1.
    policy.tick(now=2.)
    assert policy.acting_since["a"] == 1.
    policy.arrive("a", 810, now=3.)
    assert "a" not in policy.acting_since


def test_retirement_removes_decay_history_and_identity_reuse_starts_cleanly():
    policy = blocked()
    policy.forget("acting")
    assert "acting" not in policy.acting_since
    policy.arrive("acting", 400, now=100.)
    assert not policy.programs["acting"].acting
    assert "acting" not in policy.acting_since


@pytest.mark.parametrize("when", (float("nan"), float("inf"), -1.))
def test_decay_rejects_invalid_control_timestamps(when):
    policy = blocked()
    with pytest.raises(ValueError):
        policy.tick(now=when)
    assert policy._resume_now is None
    assert policy.programs["acting"].active


def test_decay_cannot_use_a_future_acting_transition():
    policy = blocked()
    policy.acting_since["acting"] = 20.
    with pytest.raises(ValueError, match="precedes"):
        policy.remaining_capacity_with_decay(now=10.)


def test_passive_author_record_without_a_timestamp_is_not_given_an_invented_age():
    policy = ThunderActingDecayWorkingSet(1000)
    policy.programs["a"] = ThunderProgram("a", 800, acting=True, active=True)
    assert policy.remaining_capacity_with_decay(now=10.) == 900


def test_real_runtime_selects_the_separate_decay_arm_without_our_policy(monkeypatch):
    for name, value in environment({}, "thunderagent_acting_decay").items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime()
    live.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
    ))
    try:
        actor, waiting = request("actor", "a", tokens=800), request("waiting", "b", tokens=700)
        live.register_visible_request(actor)
        live.on_batch_completed(NS(reqs=[actor]))
        live.register_visible_request(waiting)
        adapter = live._paper_baseline
        assert isinstance(adapter.policy, ThunderActingDecayWorkingSet)
        assert not adapter.policy.programs["b"].active
        adapter.policy.acting_since["a"] -= 5.
        adapter.next_tick = 0.
        adapter.tick(now=time.monotonic())
        assert adapter.policy.programs["b"].active
        assert not adapter.policy.programs["a"].active
        plan = live.plan_native_prefill([waiting], running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            [waiting], plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert selected.candidates == (waiting,)
        state = {
            **{name: False for name in DISABLED_POLICIES},
            "internal_comparison_mode": "scheduler_only",
            "agent_admission": None, "paper_baseline": adapter.status(),
        }
        validate_state("thunderagent_acting_decay", state)
        assert not live._custom_kv_management and live._agent_admission is None
        assert not live.enable_join_parent_priority and not live.enable_tool_prefetch
    finally:
        live.close()
    assert not adapter.policy.acting_since


@pytest.mark.parametrize("field,value", (
    ("acting_token_decay", False), ("decay_half_life_s", True),
    ("decay_half_life_s", 2.), ("decay_scope", "all_capacity_checks"),
    ("decay_age_source", "future_trace_tool_end"),
    ("undiscounted_capacity_enforced", False), ("physical_capacity_credit", True),
))
def test_effective_state_rejects_a_different_or_oracle_decay_policy(monkeypatch, field, value):
    from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter

    for name, entry in environment({}, "thunderagent_acting_decay").items():
        monkeypatch.setenv(name, entry)
    live = NativeAdmissionRuntime()
    try:
        assert isinstance(live._paper_baseline, ThunderAgentDecayAdapter)
        declared = live._paper_baseline.status()
        declared[field] = value
        state = {
            **{name: False for name in DISABLED_POLICIES},
            "internal_comparison_mode": "scheduler_only",
            "agent_admission": None, "paper_baseline": declared,
        }
        with pytest.raises(ValueError, match="acting decay"):
            validate_state("thunderagent_acting_decay", state)
    finally:
        live.close()


def test_causal_operator_constructs_the_independent_decay_arm(reference, tmp_path):
    settings, _, _ = comparison.environment(
        reference, "thunderagent_acting_decay", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    result = comparison.preflight_runtime(settings, str(tmp_path))
    assert result["runtime_configuration_verified"] is True
    assert settings["BELIEFKV_PAPER_BASELINE"] == "thunderagent_public_single_backend_acting_decay"
    assert settings["BELIEFKV_AGENT_JOIN_DEPENDENCY_ADMISSION"] == "0"
    assert settings["BELIEFKV_ENABLE_PREPARE_HOST"] == "0"
