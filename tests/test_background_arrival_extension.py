import pytest

from beliefkv.experiments.replay_arrivals import (
    extend_poisson_arrival_plan, permuted_poisson_arrival_plan, poisson_arrival_plan,
    substitute_background_arrivals, validate_arrival_plan,
)
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort


def original():
    return permuted_poisson_arrival_plan(
        list("abcdef"), rate_per_second=1 / 70, seed=35, permutation_seed=47,
        initial_workflows=2,
    )


def test_extension_preserves_every_arrival_and_continues_the_original_random_stream():
    parent = original()
    extended = extend_poisson_arrival_plan(parent, ["g", "h"])
    order = [row["workflow_id"] for row in parent["arrival_schedule"]]
    expected = poisson_arrival_plan(
        [*order, "g", "h"], rate_per_second=1 / 70, seed=35, initial_workflows=2,
    )
    assert extended["arrival_schedule"] == expected["arrival_schedule"]
    assert extended["arrival_schedule"][:6] == parent["arrival_schedule"]
    assert validate_arrival_plan(extended, list("abcdefgh")) == extended
    assert extended["prefix_plan"] == parent


def test_parent_plan_is_not_mutated_or_aliased():
    parent, saved = original(), original()
    extended = extend_poisson_arrival_plan(parent, ["g"])
    extended["prefix_plan"]["arrival_schedule"][0]["offset_seconds"] = 100
    assert parent == saved


def test_fixed_measured_members_window_and_termination_rule_survive_background_extension():
    parent = original()
    start, end = (parent["arrival_schedule"][index]["offset_seconds"] for index in (2, 3))
    before = declare_cohort(
        parent, warmup=2, measured=2, throughput_start_s=start, throughput_end_s=end,
        stop_after_measured=True,
    )
    extended = extend_poisson_arrival_plan(parent, ["g", "h"])
    after = declare_cohort(
        extended, warmup=2, measured=2, throughput_start_s=start, throughput_end_s=end,
        stop_after_measured=True,
    )
    validate_cohort(after, extended)
    for key in (
        "warmup_workflow_ids", "measured_workflow_ids", "throughput_window_offset_seconds",
        "termination_rule", "complete_JCT_including_normal_tails",
    ):
        assert before[key] == after[key]
    assert after["background_workflow_ids"] == [*before["background_workflow_ids"], "g", "h"]


@pytest.mark.parametrize("extra", [[], ["a"], ["g", "g"]])
def test_duplicate_or_empty_background_is_rejected(extra):
    with pytest.raises(ValueError, match="unique workflow"):
        extend_poisson_arrival_plan(original(), extra)


@pytest.mark.parametrize("corrupt", ["time", "rate", "prefix", "population", "order"])
def test_forged_extension_or_original_prefix_is_rejected(corrupt):
    extended = extend_poisson_arrival_plan(original(), ["g", "h"])
    if corrupt == "time":
        extended["arrival_schedule"][-1]["offset_seconds"] += 1
    elif corrupt == "rate":
        extended["rate_per_second"] *= 2
    elif corrupt == "prefix":
        extended["prefix_plan"]["arrival_schedule"][2]["offset_seconds"] += 1
    elif corrupt == "population":
        extended["additional_workflow_ids"][-1] = "different"
    else:
        extended["arrival_schedule"][0], extended["arrival_schedule"][1] = (
            extended["arrival_schedule"][1], extended["arrival_schedule"][0],
        )
    with pytest.raises(ValueError):
        validate_arrival_plan(extended, list("abcdefgh"))


def test_validation_rejects_an_incomplete_executor_population():
    with pytest.raises(ValueError, match="declared population"):
        validate_arrival_plan(extend_poisson_arrival_plan(original(), ["g"]), list("abcdef"))


def test_plain_poisson_extension_keeps_its_nonwarm_random_stream():
    parent = poisson_arrival_plan(["a", "b"], rate_per_second=.1, seed=21)
    extended = extend_poisson_arrival_plan(parent, ["c"])
    assert extended["arrival_schedule"] == poisson_arrival_plan(
        ["a", "b", "c"], rate_per_second=.1, seed=21,
    )["arrival_schedule"]
    validate_arrival_plan(extended, ["a", "b", "c"])


def test_substituted_prefix_can_be_extended_without_reshuffling_or_undoing_replacements():
    parent = substitute_background_arrivals(
        original(), [{"old_workflow_id": original()["arrival_schedule"][-1]["workflow_id"],
                      "new_workflow_id": "replacement"}], preserved_prefix_workflows=4,
    )
    extended = extend_poisson_arrival_plan(parent, ["new1", "new2"])
    identities = [row["workflow_id"] for row in parent["arrival_schedule"]]
    expected = poisson_arrival_plan(
        [*identities, "new1", "new2"], rate_per_second=1 / 70, seed=35, initial_workflows=2,
    )
    assert extended["arrival_schedule"] == expected["arrival_schedule"]
    assert extended["prefix_plan"] == parent
    assert extended["arrival_schedule"][:6] == parent["arrival_schedule"]
    validate_arrival_plan(extended, [*identities, "new1", "new2"])


def test_substituted_prefix_provenance_is_still_validated_before_extension():
    parent = substitute_background_arrivals(
        original(), [{"old_workflow_id": original()["arrival_schedule"][-1]["workflow_id"],
                      "new_workflow_id": "replacement"}], preserved_prefix_workflows=4,
    )
    parent["replacements"][0]["new_workflow_id"] = "forged-replacement"
    with pytest.raises(ValueError, match="substituted arrivals"):
        extend_poisson_arrival_plan(parent, ["new"])
