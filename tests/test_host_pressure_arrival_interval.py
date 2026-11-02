from copy import deepcopy
import math

import pytest

from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan, substitute_background_arrivals
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts.prepare_host_pool_pressure import pressure_protocol


@pytest.mark.parametrize("substituted", (False, True))
@pytest.mark.parametrize("interval", (70., 50., 35.))
def test_interval_changes_only_timing_with_same_work_order_rng_and_fixed_measurement_members(substituted, interval):
    previous = permuted_poisson_arrival_plan(
        [f"w{index}" for index in range(73)], rate_per_second=1 / 70.,
        seed=35, permutation_seed=47, initial_workflows=20,
    )
    if substituted:
        previous = substitute_background_arrivals(previous, [{
            "old_workflow_id": previous["arrival_schedule"][-1]["workflow_id"],
            "new_workflow_id": "original_replacement",
        }], preserved_prefix_workflows=38)
    before = deepcopy(previous)
    plan, cohort = pressure_protocol(previous, mean_interval_s=interval)
    assert before == previous
    assert plan["seed"] == previous["seed"]
    assert plan["rate_per_second"] == pytest.approx(1 / interval)
    old_members = [value["workflow_id"] for value in previous["arrival_schedule"]]
    assert [value["workflow_id"] for value in plan["arrival_schedule"]] == old_members
    assert cohort["measured_workflow_ids"] == old_members[20:38]
    assert not cohort["cohort_identity_available_to_scheduler"]
    assert cohort["background_submission_stops_when_cohort_finishes"]
    for old, new in zip(previous["arrival_schedule"], plan["arrival_schedule"]):
        assert new["offset_seconds"] == pytest.approx(old["offset_seconds"] * interval / 70.)
    validate_cohort(cohort, plan)


@pytest.mark.parametrize("interval", (True, 0, -1, math.inf, math.nan, "50"))
def test_invalid_mean_interval_is_rejected_without_mutating_the_source(interval):
    previous = permuted_poisson_arrival_plan(
        [f"w{index}" for index in range(73)], rate_per_second=1 / 70.,
        seed=35, permutation_seed=47, initial_workflows=20,
    )
    with pytest.raises(ValueError, match="finite positive"):
        pressure_protocol(previous, mean_interval_s=interval)
