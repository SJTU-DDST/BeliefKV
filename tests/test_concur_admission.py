import pytest

from beliefkv.policy.concur_admission import ConcurAdmission
from beliefkv.policy.paper_baselines import ConcurWindow


def policy(initial=2):
    return ConcurAdmission(ConcurWindow(initial, maximum=512))


def test_agent_holds_its_permit_across_tool_wait_instead_of_reentering_fifo():
    state = policy(1)
    state.arrive("a")
    state.begin("a")
    state.completed("a")
    state.arrive("b")
    assert state.agents["a"].active and not state.agents["b"].active
    state.arrive("a")
    assert state.agents["a"].active and state.agents["a"].pending


def test_window_reduction_waits_for_in_flight_calls_to_finish():
    state = policy(2)
    for identity in ("old", "new"):
        state.arrive(identity)
        state.begin(identity)
    state.window.observe(sample_id=1, gpu_usage=.9, gpu_hit_rate=.1)
    state.rebalance()
    assert state.window.window == 1
    assert state.agents["old"].active and state.agents["new"].active
    assert state.agents["new"].pause_at_boundary
    assert not state.agents["old"].pause_at_boundary
    state.completed("new")
    assert not state.agents["new"].active and state.agents["old"].active


def test_repeated_rebalancing_does_not_mark_every_running_agent_for_pause():
    state = policy(4)
    for identity in ("a", "b", "c", "d"):
        state.arrive(identity)
        state.begin(identity)
    state.window.window = 2
    for _ in range(4):
        state.rebalance()
        assert sum(item.pause_at_boundary for item in state.agents.values()) == 2
        assert all(item.active for item in state.agents.values())


def test_idle_boundary_pauses_precede_marking_running_calls():
    state = policy(2)
    state.arrive("idle")
    state.arrive("running")
    state.begin("running")
    state.window.window = 1
    state.rebalance()
    assert not state.agents["idle"].active
    assert state.agents["running"].active and not state.agents["running"].pause_at_boundary


def test_window_growth_can_revoke_a_deferred_boundary_pause():
    state = policy(2)
    for identity in ("a", "b"):
        state.arrive(identity)
        state.begin(identity)
    state.window.window = 1
    state.rebalance()
    assert state.agents["b"].pause_at_boundary
    state.window.window = 2
    state.rebalance()
    assert not state.agents["b"].pause_at_boundary
    state.completed("b")
    assert state.agents["b"].active


def test_only_pending_agents_are_resumed_in_fifo_order():
    state = policy(1)
    for identity in ("a", "b", "c"):
        state.arrive(identity)
    state.forget("a")
    assert state.agents["b"].active and not state.agents["c"].active
    state.completed("b")
    state.window.window = 2
    state.rebalance()
    assert state.agents["b"].active and state.agents["c"].active


def test_observed_join_yields_a_parent_permit_without_an_extra_priority_rule():
    state = policy(1)
    state.arrive("parent")
    state.begin("parent")
    state.completed("parent")
    state.arrive("child")
    assert not state.agents["child"].active
    assert state.set_blocked("parent", True)
    assert not state.agents["parent"].active and state.agents["child"].active
    state.begin("child")
    state.completed("child")
    state.forget("child")
    state.set_blocked("parent", False)
    assert not state.agents["parent"].active
    state.arrive("parent")
    assert state.agents["parent"].active


def test_join_compatibility_never_pauses_an_in_flight_parent():
    state = policy(1)
    state.arrive("parent")
    state.begin("parent")
    state.arrive("child")
    state.set_blocked("parent", True)
    assert state.agents["parent"].active and not state.agents["child"].active
    state.completed("parent")
    assert not state.agents["parent"].active and state.agents["child"].active


def test_missing_gpu_hit_samples_do_not_invent_a_congestion_signal():
    window = ConcurWindow(8, maximum=512)
    assert window.observe(sample_id=0, gpu_usage=.9, gpu_hit_rate=None) == 8
    assert window.observe(sample_id=1, gpu_usage=.1, gpu_hit_rate=None) == 10
    assert window.observe(sample_id=2, gpu_usage=.9, gpu_hit_rate=.1) == 5


def test_unadmitted_or_duplicate_calls_are_rejected():
    state = policy(1)
    state.arrive("a")
    state.arrive("b")
    with pytest.raises(ValueError, match="permit"):
        state.begin("b")
    with pytest.raises(ValueError, match="pending"):
        state.arrive("a")
