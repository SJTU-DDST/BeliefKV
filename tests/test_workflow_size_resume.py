from copy import deepcopy
from dataclasses import asdict
import random

import pytest

from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet


def policy(capacity, buckets, groups=None):
    return WorkflowSizeWorkingSet(
        capacity, buffer_tokens=0, rank=lambda identity, now: buckets.get(identity, 1),
        sibling_group=(groups or {}).get,
    )


@pytest.mark.parametrize("capacity,expected", ((7, {"a1", "a2"}), (5, {"a1", "c"})))
def test_documented_selection_fills_with_fitting_subsets(capacity, expected):
    live = policy(capacity, {"a1": 0, "a2": 0, "b": 1, "c": 2}, {"a1": "j", "a2": "j"})
    live.programs = {
        name: ThunderProgram(name, size, steps=2, pending=True, queue_sequence=i)
        for i, (name, size) in enumerate((("b", 4), ("c", 2), ("a1", 3), ("a2", 3)))
    }
    assert set(live.tick(now=10.)) == expected
    assert live.remaining_capacity() >= 0


def test_original_category_precedes_predicted_bucket():
    live = policy(5, {"continuation": 2, "new": 0, "acting": 0})
    live.programs = {
        "new": ThunderProgram("new", 5, steps=1, pending=True),
        "acting": ThunderProgram("acting", 5, steps=2, acting=True),
        "continuation": ThunderProgram("continuation", 5, steps=2, pending=True),
    }
    assert live.tick(now=10.) == ("continuation",)

def test_explicit_global_oracle_crosses_category_only_for_actual_ready_candidates():
    ranks = {"long": 100., "short": 1., "blocked": 0.}
    live = WorkflowSizeWorkingSet(
        10, buffer_tokens=0, rank=lambda name, now: ranks[name],
        sibling_group=lambda name: None, rank_before_category=True,
        observe_ready=lambda item: item.identity != "blocked",
    )
    live.programs = {
        "long": ThunderProgram("long", 7, steps=2, pending=True),
        "short": ThunderProgram("short", 7, steps=1, pending=True),
        "blocked": ThunderProgram("blocked", 7, steps=2, acting=True),
    }
    assert live.tick(now=10.) == ("short",)
    assert live.last_selection["rank_before_category"]
    assert [row[0] for row in live.last_selection["ordered"]] == ["short", "long"]

def test_combination_reference_uses_original_order_before_work_or_sibling_bias():
    seen = {}

    def choose(ordered, reference, capacity):
        seen.update(order=tuple(p.identity for p in ordered),
                    reference=tuple(p.identity for p in reference), capacity=capacity)
        return reference

    live = WorkflowSizeWorkingSet(
        10, buffer_tokens=0, rank=lambda name, now: 0 if name == "short" else 2,
        sibling_group=lambda name: "group" if name in ("short", "peer") else None,
        select_set=choose,
    )
    live.programs = {
        name: ThunderProgram(name, size, steps=2, pending=True, queue_sequence=index)
        for index, (name, size) in enumerate((("short", 9), ("small", 4), ("peer", 5)))
    }
    assert set(live.tick(now=10.)) == {"small", "peer"}
    assert seen == {"order": ("small", "peer", "short"),
                    "reference": ("small", "peer"), "capacity": 10}


def test_sibling_preference_is_within_class_and_allows_partial_group():
    live = policy(7, {"a": 0, "b": 0, "c": 0}, {"a": "join", "c": "join"})
    live.programs = {
        name: ThunderProgram(name, tokens, steps=2, pending=True, queue_sequence=i)
        for i, (name, tokens) in enumerate((("a", 2), ("b", 3), ("c", 5)))
    }
    assert set(live.tick(now=10.)) == {"a", "c"}
    assert live.last_selection["selected_in_priority_order"] == ("a", "c")


@pytest.mark.parametrize("seed", (21, 43, 79, 107))
def test_uncertain_no_group_exactly_matches_reference_over_pressure_lifecycles(seed):
    rng = random.Random(seed)
    reference = ThunderActingDecayWorkingSet(2000)
    candidate = WorkflowSizeWorkingSet(
        2000, rank=lambda identity, now: 1, sibling_group=lambda identity: None,
    )
    clock = 100.
    for turn in range(350):
        clock += rng.choice((.1, .8, 2., 5.))
        pending = [p for p in reference.programs.values() if p.pending and p.active]
        ready = [str(i) for i in range(9)
                 if str(i) not in reference.programs or not reference.programs[str(i)].pending]
        if pending and (not ready or rng.random() < .55):
            item = rng.choice(pending)
            size = item.tokens + rng.randrange(1, 100)
            for live in (reference, candidate):
                live.completed(item.identity, size, now=clock)
        elif ready:
            identity, size = rng.choice(ready), rng.randrange(80, 1000)
            for live in (reference, candidate):
                live.arrive(identity, size, now=clock)
        assert reference.tick(now=clock) == candidate.tick(now=clock)
        assert {name: asdict(p) for name, p in reference.programs.items()} == {
            name: asdict(p) for name, p in candidate.programs.items()
        }
        assert reference.acting_since == candidate.acting_since


def test_decay_is_retained_and_original_pressure_can_pause_old_active():
    live = policy(10, {"new": 0})
    live.programs = {
        "old": ThunderProgram("old", 8, steps=2, active=True, acting=True),
        "new": ThunderProgram("new", 6, steps=2, pending=True),
    }
    live.acting_since["old"] = 1.
    assert live.tick(now=10.) == ("new",)
    assert not live.programs["old"].active
    assert live.programs["new"].active
    assert live.last_resume_capacity_tokens == 10


def test_resume_filter_still_excludes_a_blocked_parent_even_if_short():
    live = policy(10, {"parent": 0, "child": 2})
    live.resume_eligible = lambda item: item.identity != "parent"
    live.programs = {
        name: ThunderProgram(name, 8, steps=2, pending=True)
        for name in ("parent", "child")
    }
    assert live.tick(now=10.) == ("child",)


def test_no_budget_keeps_reference_fast_path_without_candidate_or_prediction_checks():
    def unexpected(*args):
        raise AssertionError("no-budget tick inspected a resume candidate")
    live = WorkflowSizeWorkingSet(
        10, buffer_tokens=1, rank=unexpected, sibling_group=unexpected, resume_eligible=unexpected,
    )
    live.programs = {
        "old": ThunderProgram("old", 9, steps=2, pending=True, active=True),
        "new": ThunderProgram("new", 1, steps=2, pending=True),
    }
    assert live.tick(now=10.) == ()
    assert live.last_selection["ordered"] == ()
    assert live.last_selection["selected_in_priority_order"] == ()
