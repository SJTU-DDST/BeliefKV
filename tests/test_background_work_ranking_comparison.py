from copy import deepcopy

import pytest

from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.workflow_cohort import declare_cohort
from scripts.prepare_background_work_ranking_comparison import rebind_labels
from scripts.run_background_work_ranking_comparison import command_with, validate_preparation
from tests.test_workflow_work_oracle import source as oracle_source


def test_background_labels_preserve_policy_thresholds_and_do_not_alias_inputs():
    old = oracle_source()
    old["source_binding"] = {"source_arm": "/old"}
    old["remaining_work_ranking_enabled"] = False
    labels = deepcopy(old)
    labels["work_thresholds"] = [50., 500.]
    labels["source_binding"] = {"source_arm": "/extended"}
    saved = deepcopy((old, labels))
    result = rebind_labels(old, labels)
    assert result["source_binding"] == labels["source_binding"]
    assert result["work_thresholds"] == old["work_thresholds"]
    assert result["remaining_work_ranking_enabled"] is False
    result["requests"]["one"]["output_tokens"] += 1
    assert (old, labels) == saved


def traffic():
    arrivals = permuted_poisson_arrival_plan(
        [f"workflow{i}" for i in range(24)], rate_per_second=1 / 70,
        seed=35, permutation_seed=47, initial_workflows=2,
    )
    cohort = declare_cohort(
        arrivals, warmup=2, measured=18,
        throughput_start_s=arrivals["arrival_schedule"][2]["offset_seconds"],
        throughput_end_s=arrivals["arrival_schedule"][19]["offset_seconds"],
        stop_after_measured=True,
    )
    source = {
        "selected_workflows": [{"workflow_id": row["workflow_id"]}
                               for row in arrivals["arrival_schedule"]],
        "population": {"model_work_units": {"p80_p20": 2.65}},
    }
    prep = {
        "kind": "matched_background_extension_work_ranking_preparation",
        "workflows": 24, "initial_workflows": 2, "mean_interval_seconds": 70.,
        "fixed_measured_workflows": list(cohort["measured_workflow_ids"]),
        "GPU_stage_order": ["full", "oracle", "learned"],
        "static_completion_coverage_screen": {"candidate_schedule_covers_static_tail": True},
        "minimum_model_work_p80_p20": 2.6,
    }
    return prep, arrivals, cohort, source


def test_execution_identity_is_derived_from_the_verified_new_population_and_traffic():
    validate_preparation(*traffic())


@pytest.mark.parametrize("corrupt", ("rate", "population", "initial", "cohort", "coverage", "spread"))
def test_changed_traffic_or_uncovered_tail_is_rejected_before_gpu_registration(corrupt):
    prep, arrivals, cohort, source = traffic()
    if corrupt == "rate":
        prep["mean_interval_seconds"] = 60.
    elif corrupt == "population":
        prep["workflows"] += 1
    elif corrupt == "initial":
        prep["initial_workflows"] += 1
    elif corrupt == "cohort":
        prep["fixed_measured_workflows"].reverse()
    elif corrupt == "coverage":
        prep["static_completion_coverage_screen"]["candidate_schedule_covers_static_tail"] = False
    else:
        prep["minimum_model_work_p80_p20"] = 3.
    with pytest.raises(ValueError):
        validate_preparation(prep, arrivals, cohort, source)


def test_stage_rebinding_preserves_unrelated_options_and_rejects_ambiguous_commands():
    old = ["/python", "/launcher", "--source-arm", "/old", "--port", "19035", "--arms", "full"]
    new = command_with(old, {"--source-arm": "/extended", "--port": 19041})
    assert new == ["/python", "/launcher", "--source-arm", "/extended", "--port", "19041", "--arms", "full"]
    assert old[3] == "/old"
    with pytest.raises(ValueError):
        command_with(old, {"--missing": "value"})
    with pytest.raises(ValueError):
        command_with(old + ["--port", "19042"], {"--port": 19043})
