from copy import deepcopy
import json
import sys

import pytest

from beliefkv.experiments.replay_arrivals import (
    permuted_poisson_arrival_plan, substitute_background_arrivals,
)
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from scripts.prepare_arrival_rate_followup import followup_protocol
from scripts.prepare_background_tail_extension import extend_population, main


def fixture():
    rows = [
        {
            "project": f"original/project{index}", "workflow_id": f"workflow{index}",
            "model_work_units": float(index + 1), "eligible": True, "rejection_reasons": [],
            "task_id": f"task{index}", "source_capture": f"capture{index % 2}",
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


def substituted_fixture():
    base, available, arrivals, cohort = fixture()
    replaced = arrivals["arrival_schedule"][-1]["workflow_id"]
    for row in [*base["selected_workflows"], *available["workflows"]]:
        if row["workflow_id"] == replaced:
            row["workflow_id"] = "replacement"
    arrivals = substitute_background_arrivals(
        arrivals, [{"old_workflow_id": replaced, "new_workflow_id": "replacement"}],
        preserved_prefix_workflows=4,
    )
    cohort = declare_cohort(
        arrivals, warmup=2, measured=2,
        throughput_start_s=cohort["throughput_window_offset_seconds"][0],
        throughput_end_s=cohort["throughput_window_offset_seconds"][1], stop_after_measured=True,
    )
    return base, available, arrivals, cohort


def test_retimed_substituted_population_extends_only_background_and_retains_fixed_members():
    base, available, original, cohort = substituted_fixture()
    retimed, analysis = followup_protocol(original, cohort, mean_interval_seconds=50.)
    population, extended, updated = extend_population(
        base, available, retimed, analysis, extra_count=2, minimum_ratio=1.,
    )
    assert population["selected_workflows"][:6] == base["selected_workflows"]
    assert extended["prefix_plan"]["replacements"] == original["replacements"]
    assert extended["arrival_schedule"][:6] == retimed["arrival_schedule"]
    assert updated["measured_workflow_ids"] == cohort["measured_workflow_ids"]
    assert updated["throughput_window_offset_seconds"] == analysis["throughput_window_offset_seconds"]
    assert updated["termination_rule"] == "all_fixed_measured_workflows_completed"
    assert updated["background_workflow_ids"] == [
        *cohort["background_workflow_ids"], "workflow6", "workflow7",
    ]
    validate_cohort(updated, extended)


def test_inventory_extension_CLI_keeps_first_original_variants_and_reports_static_coverage(
    tmp_path, monkeypatch,
):
    base, available, arrivals, cohort = substituted_fixture()
    original = deepcopy((base, available, arrivals, cohort))
    start = {row["workflow_id"]: row["offset_seconds"] for row in arrivals["arrival_schedule"]}
    elapsed = {key: 1000. for key in cohort["measured_workflow_ids"]}
    report = {
        "cohort": cohort, "all_measured_completed": True, "measured_workflow_JCT_ms": elapsed,
        "measured_cohort_completion_offset_s": max(start[key] + value / 1000. for key, value in elapsed.items()),
    }
    paths = {}
    content = {
        "base": base, "accepted": {"population": {"model_work_units": {"p80_p20": 1.}}},
        "arrivals": arrivals, "cohort": cohort, "completion": report,
        "inventory1": {"workflows": available["workflows"][:6]},
        "inventory2": {"workflows": [
            {**available["workflows"][0], "workflow_id": "alternative", "model_work_units": 100.},
            *available["workflows"][6:],
        ]},
    }
    for name, data in content.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(data))
    output = tmp_path / "prepared"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr("scripts.prepare_background_tail_extension.os.getpriority", lambda *_: 19)
    monkeypatch.setattr(sys, "argv", [
        "prepare_background_tail_extension.py", "--source-population", str(paths["base"]),
        "--accepted-population", str(paths["accepted"]),
        "--arrivals", str(paths["arrivals"]), "--cohort", str(paths["cohort"]),
        "--available-inventory", str(paths["inventory1"]),
        "--available-inventory", str(paths["inventory2"]),
        "--previous-cohort-report", str(paths["completion"]),
        "--extra-workflows", "2", "--mean-interval-seconds", "50",
        "--output-dir", str(output),
    ])
    main()
    assert (base, available, arrivals, cohort) == original
    prepared = json.loads((output / "preparation.json").read_bytes())
    population = json.loads((output / "population_plan.json").read_bytes())
    extended = json.loads((output / "arrivals.json").read_bytes())
    updated = json.loads((output / "analysis_cohort.json").read_bytes())
    validate_cohort(updated, extended)
    assert prepared["available_distinct_original_projects"] == 8
    assert len(prepared["duplicate_project_variants_retained"]) == 1
    assert prepared["arrival_rate_changed"]
    assert not prepared["same_original_project_rows_and_arrival_prefix"]
    assert prepared["same_original_project_rows_and_fixed_measured_members"]
    assert population["selected_workflows"][:6] == base["selected_workflows"]
    assert prepared["previous_measured_JCT_static_coverage_screen"]["candidate_schedule_covers_static_tail"]
    assert not prepared["GPU_action"] and not prepared["formal_workload_accepted"]
