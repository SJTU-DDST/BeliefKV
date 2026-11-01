from copy import deepcopy

import pytest

from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.workflow_cohort import declare_cohort
from scripts.prepare_background_tail_extension import extend_population


def fixture():
    rows = [
        {
            "project": f"original/project{index}", "workflow_id": f"workflow{index}",
            "model_work_units": float(index + 1), "eligible": True, "rejection_reasons": [],
        }
        for index in range(8)
    ]
    arrivals = permuted_poisson_arrival_plan(
        [row["workflow_id"] for row in rows[:6]], rate_per_second=1 / 70,
        seed=35, permutation_seed=47, initial_workflows=2,
    )
    cohort = declare_cohort(
        arrivals, warmup=2, measured=2,
        throughput_start_s=arrivals["arrival_schedule"][2]["offset_seconds"],
        throughput_end_s=arrivals["arrival_schedule"][3]["offset_seconds"],
        stop_after_measured=True,
    )
    return {"selected_workflows": deepcopy(rows[:6])}, {"workflows": rows}, arrivals, cohort


def test_original_rows_and_fixed_measured_population_are_preserved():
    base, available, arrivals, cohort = fixture()
    population, extended, updated = extend_population(
        base, available, arrivals, cohort, extra_count=2, minimum_ratio=1.,
    )
    assert population["projects"] == 8
    assert population["selected_workflows"][:6] == base["selected_workflows"]
    assert extended["arrival_schedule"][:6] == arrivals["arrival_schedule"]
    assert updated["measured_workflow_ids"] == cohort["measured_workflow_ids"]
    assert updated["throughput_window_offset_seconds"] == cohort["throughput_window_offset_seconds"]
    assert population["GPU_registration"] is None
    assert population["formal_workload_accepted"] is False


@pytest.mark.parametrize("corrupt", ["project", "identity", "original_row", "eligibility", "spread"])
def test_ineligible_changed_or_duplicate_population_is_rejected(corrupt):
    base, available, arrivals, cohort = fixture()
    minimum = 1.
    if corrupt == "project":
        available["workflows"][-1]["project"] = available["workflows"][-2]["project"]
    elif corrupt == "identity":
        available["workflows"][-1]["workflow_id"] = available["workflows"][-2]["workflow_id"]
    elif corrupt == "original_row":
        available["workflows"][0]["model_work_units"] += 1
    elif corrupt == "eligibility":
        available["workflows"][-1]["eligible"] = False
    else:
        minimum = 100.
    with pytest.raises(ValueError):
        extend_population(base, available, arrivals, cohort, extra_count=2, minimum_ratio=minimum)
