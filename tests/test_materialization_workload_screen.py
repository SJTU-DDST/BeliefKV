from copy import deepcopy
import json
import math

import pytest

from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from scripts.analyze_materialization_workload import (
    arrival_screen, capacity_screen, host_tokens, joined_steps,
)
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from scripts.prepare_subagent_materialization import baseline_configurations, prepare
from scripts.analyze_materialization_ready_supply import classify, request_epochs
from scripts.stop_after_measured_cohort import native_baseline_declared, replay_arm


def test_capacity_pressure_excludes_pending_first_input_from_old_state():
    combined = 100 + host_tokens(1)
    steps = [
        (0., 3., {
            "next_request_reusable_prior_prefix_union_tokens": combined - 10,
            "pending_input_or_latest_served_union_tokens": combined + 100,
        }, {"ready_paused": 0, "active_ready": 2}),
        (3., 5., {
            "next_request_reusable_prior_prefix_union_tokens": combined + 20,
            "pending_input_or_latest_served_union_tokens": combined + 100,
        }, {"ready_paused": 3, "active_ready": 4}),
    ]
    result = capacity_screen(steps, 100, 1)
    assert result["reusable_union_exceeds_capacity_fraction"] == .4
    assert result["pending_union_exceeds_capacity_fraction"] == 1.
    assert result["reusable_pressure_and_ready_paused_seconds"] == 2.
    assert result["time_mean_ready_paused"] == 1.2
    assert result["counterfactual_capacity_only_with_observed_service_and_states_fixed"]


def test_joined_observations_clip_to_exact_window_and_reject_unobserved_tails():
    demand = [{"arrival_offset_s": value} for value in (0., 60., 120.)]
    supply = [{"offset_s": value} for value in range(121)]
    joined = joined_steps(demand, supply, .5, 119.5)
    assert sum(right - left for left, right, _, _ in joined) == 119.
    assert joined[0][0] == .5 and joined[-1][1] == 119.5
    assert joined[59][2]["arrival_offset_s"] == 0.
    assert joined[60][2]["arrival_offset_s"] == 60.
    with pytest.raises(ValueError, match="cover the whole"):
        joined_steps(demand, supply, .5, 120.1)


def protocol():
    arrivals = permuted_poisson_arrival_plan(
        [f"w{index}" for index in range(60)], rate_per_second=1. / 60.,
        seed=35, permutation_seed=54, initial_workflows=20,
    )
    slots = arrivals["arrival_schedule"]
    cohort = declare_cohort(
        arrivals, warmup=20, measured=18, throughput_start_s=slots[20]["offset_seconds"],
        throughput_end_s=slots[37]["offset_seconds"], stop_after_measured=True,
    )
    return arrivals, cohort


def test_arrival_screen_accounts_for_complete_long_measured_tail_without_claiming_JCT():
    arrivals, cohort = protocol()
    latencies = {identity: 200_000. for identity in cohort["measured_workflow_ids"]}
    latencies[cohort["measured_workflow_ids"][0]] = 3_000_000.
    complete = {
        "cohort": cohort, "all_measured_completed": True,
        "measured_workflow_JCT_ms": latencies,
        "measured_cohort_completion_offset_s": max(
            row["offset_seconds"] + latencies[row["workflow_id"]] / 1000.
            for row in arrivals["arrival_schedule"] if row["workflow_id"] in latencies
        ),
        "common_window_decode_tokens_per_second": 10.,
        "common_window_output_tokens_per_second": 11.,
    }
    assessment = {
        "workflows": [
            {"workflow_id": row["workflow_id"], "model_work_units": 100.}
            for row in arrivals["arrival_schedule"]
        ],
    }
    expected = {
        f"request-{index}": {
            "llm_submit": [[row["workflow_id"], "owner", "context", 0], 200],
            "llm_result": [[row["workflow_id"], "owner", "context", 0], 90],
        }
        for index, row in enumerate(arrivals["arrival_schedule"])
    }
    result = arrival_screen(arrivals, cohort, complete, assessment, expected, (60., 30.))
    first, second = result["candidates"]
    assert first["nominal_new_output_tokens_per_second"] == 1.5
    assert second["nominal_new_output_tokens_per_second"] == 3.
    assert second["candidate_background_margin_s"] < first["candidate_background_margin_s"]
    assert not second["new_serving_performance_predicted"]
    assert second["no_paused_supply_or_JCT_prediction"]
    damaged = deepcopy(expected)
    damaged["request-0"]["llm_result"][0][0] = "another-workflow"
    with pytest.raises(ValueError, match="identities"):
        arrival_screen(arrivals, cohort, complete, assessment, damaged, (60.,))


def test_prepared_matched_pressure_changes_arrivals_without_changing_measured_members(tmp_path):
    arrivals, cohort = protocol()
    paths = {name: tmp_path / (name + ".json") for name in ("arrivals", "cohort", "base")}
    for name, value in (("arrivals", arrivals), ("cohort", cohort)):
        paths[name].write_text(json.dumps(value))
    previous = {
        "order": ["full"], "common_environment": {
            "AGENT_ADMISSION": "thunderagent_reference", "EXECUTION_FRONTIER_RESTORE": "0",
            "HICACHE_SIZE_GB": "400", "HOST_NUMA_MEMORY_POLICY": "interleave",
            "MODEL_CACHE_LAYOUT": "full_only", "HOST_NUMA_MEMORY_NODES": "0,1",
            "HOST_NUMA_PLACEMENT_CHECK": "diagnostic",
        },
        "workflow_arrivals": {
            "path": str(paths["arrivals"]), "sha256": sha256_file(paths["arrivals"]),
        },
        "private_analysis_cohort": {
            "path": str(paths["cohort"]), "sha256": sha256_file(paths["cohort"]),
            "delivered_to_scheduler": False,
        },
        "comparison_contract": {},
    }
    paths["base"].write_text(json.dumps(previous))
    output = tmp_path / "prepared"
    report = prepare(
        paths["base"], output, host_gb=200, mean_interval_s=55., include_baselines=True,
    )
    following = json.loads((output / "arrivals.json").read_bytes())
    analysis = json.loads((output / "analysis_cohort.json").read_bytes())
    validate_cohort(analysis, following)
    assert analysis["measured_workflow_ids"] == cohort["measured_workflow_ids"]
    for old, new in zip(arrivals["arrival_schedule"], following["arrival_schedule"]):
        assert old["workflow_id"] == new["workflow_id"]
        assert math.isclose(old["offset_seconds"] * 55. / 60., new["offset_seconds"])
    left, right = [
        json.loads((output / (label + ".json")).read_bytes())
        for label in ("control", "pressure")
    ]
    for config in (left, right):
        assert config["workflow_arrivals"]["sha256"] == sha256_file(output / "arrivals.json")
        assert config["common_environment"]["HICACHE_SIZE_GB"] == "200"
        assert config["common_environment"]["HOST_NUMA_PLACEMENT_CHECK"] == "diagnostic"
        config["common_environment"].pop("BELIEFKV_SUBAGENT_MATERIALIZATION")
        config["comparison_contract"].pop("subagent_materialization_policy")
        config["materialization_experiment"].pop("policy")
    assert left == right
    assert not report["original_arrivals_preserved"]
    assert report["fixed_measurement_members_order_and_seeded_draws_preserved"]
    assert report["matched_arms_have_identical_arrivals"]
    assert report["included_baselines"] == ["thunderagent_acting_decay", "native_matched"]
    for label in report["included_baselines"]:
        config = json.loads((output / (label + ".json")).read_bytes())
        assert config["workflow_arrivals"] == left["workflow_arrivals"]
        assert config["common_environment"]["HICACHE_SIZE_GB"] == "200"
    assert not report["GPU_action"]
    with pytest.raises(FileExistsError):
        prepare(paths["base"], output, host_gb=200, mean_interval_s=55.)


def test_external_controls_inherit_traffic_and_resources_and_disable_child_gate():
    control = {
        "order": ["full"], "common_environment": {
            "BELIEFKV_SUBAGENT_MATERIALIZATION": "off",
            "HICACHE_SIZE_GB": "200", "MAX_RUNNING_REQUESTS": "24",
        },
        "workflow_arrivals": {"path": "same-arrivals", "sha256": "same-arrivals-hash"},
        "private_analysis_cohort": {"delivered_to_scheduler": False},
        "comparison_contract": {"Full_only_policy": True},
    }
    results = baseline_configurations(control)
    for label, config in results.items():
        assert config["order"] == [label]
        assert config["common_environment"] == control["common_environment"]
        assert config["workflow_arrivals"] == control["workflow_arrivals"]
        assert config["private_analysis_cohort"] == control["private_analysis_cohort"]
        assert "Full_only_policy" not in config["comparison_contract"]
        settings = (
            paper_environment(config["common_environment"], label)
            if label == "thunderagent_acting_decay"
            else arm_environment(config["common_environment"], label)
        )
        assert settings["BELIEFKV_SUBAGENT_MATERIALIZATION"] == "off"
        assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"


def test_continuation_supply_uses_source_epoch_not_retry_step_counter():
    expected = {
        "first": {"llm_submit": [["w", "owner", "context", 0], 100]},
        "next": {"llm_submit": [["w", "owner", "context", 1], 200]},
    }
    programs = [
        {
            "key": dict(zip(
                ("root_workflow_id", "invocation_id", "context_id", "context_epoch"),
                item["llm_submit"][0],
            ), request_id=rid),
            "application_ready": True, "pending_before": True, "active_before": False,
            "marked_before": False, "steps": 99,
        }
        for rid, item in expected.items()
    ]
    result = classify({"programs": programs}, expected, request_epochs(expected))
    assert result["ready_paused"] == 2
    assert result["first_call_ready_paused"] == 1
    assert result["continuation_ready_paused"] == 1
    assert result["continuation_ready_paused_workflows"] == 1
    programs[1]["key"]["context_epoch"] = 100
    result = classify({"programs": programs}, expected, request_epochs(expected))
    assert result["unknown_source_request"] == 1
    assert result.get("continuation_ready_paused", 0) == 0


def test_prepared_native_baseline_can_use_the_production_fixed_cohort_stopper(tmp_path):
    control = {
        "order": ["full"], "common_environment": {
            "BELIEFKV_SUBAGENT_MATERIALIZATION": "off",
            "HICACHE_SIZE_GB": "200", "MAX_RUNNING_REQUESTS": "24",
        },
    }
    config = baseline_configurations(control)["native_matched"]
    path = tmp_path / "native_matched.json"
    path.write_text(json.dumps(config))
    registration = {
        "order": ["native_matched"], "experiment_configuration": str(path),
        "external_inputs": [{"path": str(path), "sha256": sha256_file(path)}],
        "run_root": str(tmp_path / "run"),
    }
    assert native_baseline_declared(registration)
    assert replay_arm(registration) == tmp_path / "run/native_matched"
    path.write_text(json.dumps({**config, "comparison_contract": {}}))
    assert not native_baseline_declared(registration)
