from copy import deepcopy
import json
import math
import sys

import pytest

from beliefkv.experiments.replay_arrivals import (
    extend_poisson_arrival_plan, permuted_poisson_arrival_plan, substitute_background_arrivals,
    validate_arrival_plan,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from scripts.prepare_arrival_rate_followup import (
    completion_coverage_screen, followup_configuration, followup_protocol, main, supply_window,
)


def protocol():
    arrivals = permuted_poisson_arrival_plan(
        [f"w{i}" for i in range(56)], rate_per_second=1. / 90.,
        seed=35, permutation_seed=54, initial_workflows=8,
    )
    slots = arrivals["arrival_schedule"]
    cohort = declare_cohort(
        arrivals, warmup=12, measured=18, throughput_start_s=slots[12]["offset_seconds"],
        throughput_end_s=slots[29]["offset_seconds"], stop_after_measured=True,
    )
    return arrivals, cohort


def test_rate_change_preserves_all_draws_order_and_measured_members():
    arrivals, cohort = protocol()
    following, analysis = followup_protocol(arrivals, cohort, mean_interval_seconds=60.)
    validate_cohort(analysis, following)
    for old, new in zip(arrivals["arrival_schedule"], following["arrival_schedule"]):
        assert old["workflow_id"] == new["workflow_id"]
        assert math.isclose(new["offset_seconds"], old["offset_seconds"] * 2. / 3.)
    for name in ("warmup_workflow_ids", "measured_workflow_ids", "background_workflow_ids"):
        assert analysis[name] == cohort[name]
    assert analysis["schema_version"] == 2
    assert analysis["complete_JCT_including_normal_tails"]
    assert not analysis["cohort_identity_available_to_scheduler"]
    assert analysis["throughput_window_offset_seconds"][1] < following["arrival_schedule"][-1]["offset_seconds"]


@pytest.mark.parametrize("mean", (0., -1., float("inf"), float("nan"), True, "60"))
def test_invalid_intensity_is_rejected(mean):
    with pytest.raises(ValueError):
        followup_protocol(*protocol(), mean_interval_seconds=mean)


def test_changed_slots_or_completed_order_members_cannot_be_frozen_as_rate_only():
    arrivals, cohort = protocol()
    changed = deepcopy(arrivals)
    changed["arrival_schedule"][-1]["offset_seconds"] += 1.
    with pytest.raises(ValueError):
        followup_protocol(changed, cohort, mean_interval_seconds=60.)
    cohort["measured_workflow_ids"].reverse()
    with pytest.raises(ValueError):
        followup_protocol(arrivals, cohort, mean_interval_seconds=60.)


def test_every_nonarrival_setting_is_preserved_and_cohort_is_private(tmp_path):
    path = tmp_path / "arrivals.json"
    path.write_text("{}")
    previous = {
        "order": ["full"], "common_environment": {"TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0"},
        "workflow_arrivals": {"path": "old", "sha256": "old"},
        "comparison_contract": {"workflow_work_ranking": False},
    }
    result = followup_configuration(previous, path)
    assert result["common_environment"] == previous["common_environment"]
    assert result["comparison_contract"] == previous["comparison_contract"]
    assert result["workflow_arrivals"] != previous["workflow_arrivals"]
    assert "cohort" not in result and "analysis_cohort" not in result
    assert previous["workflow_arrivals"]["path"] == "old"


def test_larger_initial_population_keeps_original_tasks_and_private_measured_members():
    arrivals, cohort = protocol()
    following, analysis = followup_protocol(
        arrivals, cohort, mean_interval_seconds=80., initial_workflows=12,
    )
    validate_cohort(analysis, following)
    assert following["initial_workflows"] == 12
    assert all(row["offset_seconds"] == 0. for row in following["arrival_schedule"][:12])
    assert following["arrival_schedule"][12]["offset_seconds"] > 0.
    assert [row["workflow_id"] for row in following["arrival_schedule"]] == [
        row["workflow_id"] for row in arrivals["arrival_schedule"]
    ]
    for name in ("warmup_workflow_ids", "measured_workflow_ids", "background_workflow_ids"):
        assert analysis[name] == cohort[name]
    assert analysis["schema_version"] == 2
    assert not analysis["cohort_identity_available_to_scheduler"]


def test_initial_population_change_preserves_the_shared_seeded_interarrival_draw_prefix():
    arrivals, cohort = protocol()
    following, _ = followup_protocol(
        arrivals, cohort, mean_interval_seconds=80., initial_workflows=12,
    )
    old = [0.] + [row["offset_seconds"] for row in arrivals["arrival_schedule"][8:]]
    new = [0.] + [row["offset_seconds"] for row in following["arrival_schedule"][12:]]
    for old_left, old_right, new_left, new_right in zip(old, old[1:], new, new[1:]):
        assert math.isclose(new_right - new_left, (old_right - old_left) * 80. / 90.)
    assert not math.isclose(
        following["arrival_schedule"][12]["offset_seconds"],
        arrivals["arrival_schedule"][12]["offset_seconds"] * 80. / 90.,
    )


@pytest.mark.parametrize("initial", (0, -1, True, "12", float("inf"), 13, 56))
def test_invalid_initial_population_or_initial_measured_members_are_rejected(initial):
    with pytest.raises(ValueError, match="initial workflows"):
        followup_protocol(
            *protocol(), mean_interval_seconds=80., initial_workflows=initial,
        )


def test_explicit_unchanged_initial_population_preserves_the_original_rate_only_protocol():
    arrivals, cohort = protocol()
    assert followup_protocol(
        arrivals, cohort, mean_interval_seconds=60., initial_workflows=8,
    ) == followup_protocol(arrivals, cohort, mean_interval_seconds=60.)


def test_rate_only_followup_retains_legacy_initial_measured_members():
    arrivals = permuted_poisson_arrival_plan(
        [f"w{i}" for i in range(56)], rate_per_second=1. / 90.,
        seed=35, permutation_seed=54, initial_workflows=16,
    )
    cohort = declare_cohort(
        arrivals, warmup=12, measured=18, throughput_start_s=0.,
        throughput_end_s=arrivals["arrival_schedule"][29]["offset_seconds"],
        stop_after_measured=True,
    )
    default = followup_protocol(arrivals, cohort, mean_interval_seconds=60.)
    explicit = followup_protocol(
        arrivals, cohort, mean_interval_seconds=60., initial_workflows=16,
    )
    assert default == explicit
    assert default[0]["initial_workflows"] == 16


def substituted_protocol():
    arrivals, cohort = protocol()
    following = substitute_background_arrivals(
        arrivals, [{
            "old_workflow_id": arrivals["arrival_schedule"][31]["workflow_id"],
            "new_workflow_id": "background-identity-that-would-change-the-sort",
        }], preserved_prefix_workflows=30,
    )
    return following, declare_cohort(
        following, warmup=12, measured=18,
        throughput_start_s=cohort["throughput_window_offset_seconds"][0],
        throughput_end_s=cohort["throughput_window_offset_seconds"][1], stop_after_measured=True,
    )


@pytest.mark.parametrize("initial", (8, 12))
def test_substituted_background_retiming_keeps_slots_and_original_permutation(initial):
    arrivals, cohort = substituted_protocol()
    saved = deepcopy(arrivals)
    following, analysis = followup_protocol(
        arrivals, cohort, mean_interval_seconds=60., initial_workflows=initial,
    )
    assert arrivals == saved
    assert following["schema_version"] == 6
    assert following["replacements"] == arrivals["replacements"]
    assert following["preserved_prefix_workflows"] == 30
    assert following["original_plan"]["permutation_seed"] == 54
    assert [row["workflow_id"] for row in following["arrival_schedule"]] == [
        row["workflow_id"] for row in arrivals["arrival_schedule"]
    ]
    expected, _ = followup_protocol(
        *protocol(), mean_interval_seconds=60., initial_workflows=initial,
    )
    assert [row["offset_seconds"] for row in following["arrival_schedule"]] == [
        row["offset_seconds"] for row in expected["arrival_schedule"]
    ]
    for field in ("warmup_workflow_ids", "measured_workflow_ids", "background_workflow_ids"):
        assert analysis[field] == cohort[field]
    validate_cohort(analysis, following)


@pytest.mark.parametrize("mean", (50., 60., 70.))
@pytest.mark.parametrize("initial", (8, 12))
def test_extended_background_retiming_preserves_every_identity_and_random_draw(mean, initial):
    base, cohort = substituted_protocol()
    arrivals = extend_poisson_arrival_plan(base, [f"extra-{i}" for i in range(10)])
    slots = arrivals["arrival_schedule"]
    cohort = declare_cohort(
        arrivals, warmup=12, measured=18, throughput_start_s=slots[12]["offset_seconds"],
        throughput_end_s=slots[29]["offset_seconds"], stop_after_measured=True,
    )
    before = deepcopy(arrivals)
    following, analysis = followup_protocol(
        arrivals, cohort, mean_interval_seconds=mean, initial_workflows=initial,
    )
    assert arrivals == before
    assert following["mode"] == "prefix_extended_poisson"
    assert following["additional_workflow_ids"] == arrivals["additional_workflow_ids"]
    assert following["prefix_plan"]["replacements"] == base["replacements"]
    assert following["prefix_plan"]["original_plan"]["permutation_seed"] == 54
    assert [row["workflow_id"] for row in following["arrival_schedule"]] == [
        row["workflow_id"] for row in arrivals["arrival_schedule"]
    ]
    if initial == 8:
        for old, new in zip(arrivals["arrival_schedule"], following["arrival_schedule"]):
            assert new["offset_seconds"] == pytest.approx(old["offset_seconds"] * mean / 90.)
    validate_arrival_plan(following, [row["workflow_id"] for row in slots])
    validate_cohort(analysis, following)
    for name in ("warmup_workflow_ids", "measured_workflow_ids", "background_workflow_ids"):
        assert analysis[name] == cohort[name]


def write_json(path, content):
    path.write_text(json.dumps(content))
    return path


def private_configuration(tmp_path, arrivals, cohort):
    arrival_path = write_json(tmp_path / "previous_arrivals.json", arrivals)
    cohort_path = write_json(tmp_path / "previous_cohort.json", cohort)
    return {
        "order": ["full"], "common_environment": {"HOST_GB": "400", "LAYER_HANDOFF": "0"},
        "workflow_arrivals": {"path": str(arrival_path), "sha256": sha256_file(arrival_path)},
        "private_analysis_cohort": {
            "path": str(cohort_path), "sha256": sha256_file(cohort_path),
            "delivered_to_scheduler": False,
        },
    }


def test_retimed_configuration_updates_private_cohort_and_preserves_serving_settings(tmp_path):
    arrivals, cohort = substituted_protocol()
    previous = private_configuration(tmp_path, arrivals, cohort)
    saved = deepcopy(previous)
    following, analysis = followup_protocol(arrivals, cohort, mean_interval_seconds=60.)
    arrival_path = write_json(tmp_path / "new_arrivals.json", following)
    cohort_path = write_json(tmp_path / "new_cohort.json", analysis)
    result = followup_configuration(previous, arrival_path, cohort_path)
    assert previous == saved
    assert result["common_environment"] == previous["common_environment"]
    assert result["private_analysis_cohort"] == {
        "path": str(cohort_path.resolve()), "sha256": sha256_file(cohort_path),
        "delivered_to_scheduler": False,
    }
    assert result["private_analysis_cohort"] != previous["private_analysis_cohort"]
    assert "analysis_cohort" not in result


@pytest.mark.parametrize("change", ("no-new-reference", "exposed-to-scheduler", "old-file-changed"))
def test_stale_or_exposed_private_cohort_cannot_survive_retiming(tmp_path, change):
    arrivals, cohort = protocol()
    previous = private_configuration(tmp_path, arrivals, cohort)
    following, analysis = followup_protocol(arrivals, cohort, mean_interval_seconds=60.)
    arrival_path = write_json(tmp_path / "new_arrivals.json", following)
    cohort_path = write_json(tmp_path / "new_cohort.json", analysis)
    if change == "no-new-reference":
        cohort_path = None
    elif change == "exposed-to-scheduler":
        previous["private_analysis_cohort"]["delivered_to_scheduler"] = True
    else:
        write_json(tmp_path / "previous_cohort.json", {"changed": True})
    with pytest.raises(ValueError):
        followup_configuration(previous, arrival_path, cohort_path)


def sampled_supply(tmp_path, cohort):
    registration = write_json(tmp_path / "registration.json", {"sample": "completed-control"})
    start, end = cohort["throughput_window_offset_seconds"]
    middle = (start + end) / 2
    return registration, {
        "registration": str(registration.resolve()),
        "inputs_sha256": {str(registration.resolve()): sha256_file(registration)},
        "observed_through_offset_s": end + 100,
        "scope": {"sampled_state_not_exact_control_decisions": True},
        "sample_counts": {"common_window": 5},
        "bins": [
            {"start_s": start, "end_s": middle, "samples": 3},
            {"start_s": middle, "end_s": end, "samples": 2},
        ],
    }


def test_sampled_supply_can_support_preparation_without_claiming_GPU_utilization(tmp_path):
    _, cohort = protocol()
    registration, audit = sampled_supply(tmp_path, cohort)
    result = supply_window(audit, registration, cohort)
    assert result["sample_counts"] == {"common_window": 5}
    assert not result["time_weighted"]
    assert not result["physical_counterfactual_verified"]
    assert result["kind"] == "sampled_admission_supply_without_GPU_utilization"


@pytest.mark.parametrize("change", (
    "registration", "hash", "partial", "hole", "overlap", "wrong-window", "sample-count", "scope",
))
def test_other_registration_partial_or_inconsistent_supply_is_rejected(tmp_path, change):
    _, cohort = protocol()
    registration, audit = sampled_supply(tmp_path, cohort)
    if change == "registration":
        audit["registration"] = "another-registration"
    elif change == "hash":
        audit["inputs_sha256"][str(registration.resolve())] = "changed"
    elif change == "partial":
        audit["observed_through_offset_s"] = cohort["throughput_window_offset_seconds"][1] - 1
    elif change == "hole":
        audit["bins"][1]["start_s"] += 1
    elif change == "overlap":
        audit["bins"][1]["start_s"] -= 1
    elif change == "wrong-window":
        audit["bins"][-1]["end_s"] += 1
    elif change == "sample-count":
        audit["sample_counts"]["common_window"] += 1
    else:
        audit["scope"]["sampled_state_not_exact_control_decisions"] = False
    with pytest.raises(ValueError):
        supply_window(audit, registration, cohort)


def completion_report(arrivals, cohort):
    rows = {row["workflow_id"]: row["offset_seconds"] for row in arrivals["arrival_schedule"]}
    elapsed = {key: 1000. for key in cohort["measured_workflow_ids"]}
    first = cohort["measured_workflow_ids"][0]
    elapsed[first] = (arrivals["arrival_schedule"][-1]["offset_seconds"] - rows[first] - 50) * 1000.
    return {
        "cohort": deepcopy(cohort), "all_measured_completed": True,
        "measured_workflow_JCT_ms": elapsed,
        "measured_cohort_completion_offset_s": max(rows[key] + value / 1000. for key, value in elapsed.items()),
    }


def test_static_coverage_screen_exposes_tail_exhaustion_without_predicting_new_JCTs():
    arrivals, cohort = substituted_protocol()
    report = completion_report(arrivals, cohort)
    original = completion_coverage_screen(arrivals, cohort, report, arrivals)
    following, _ = followup_protocol(arrivals, cohort, mean_interval_seconds=30.)
    candidate = completion_coverage_screen(arrivals, cohort, report, following)
    assert original["candidate_background_margin_s"] == pytest.approx(50)
    assert original["candidate_schedule_covers_static_tail"]
    assert candidate["candidate_background_margin_s"] < 0
    assert not candidate["candidate_schedule_covers_static_tail"]
    assert not candidate["new_serving_performance_predicted"]
    assert not candidate["ready_supply_or_steady_state_proven"]
    assert not candidate["formal_workload_accepted"]


@pytest.mark.parametrize("change", ("incomplete", "missing", "extra", "nan", "zero", "bool", "end"))
def test_coverage_screen_rejects_incomplete_foreign_or_inconsistent_JCTs(change):
    arrivals, cohort = protocol()
    report = completion_report(arrivals, cohort)
    first = cohort["measured_workflow_ids"][0]
    if change == "incomplete":
        report["all_measured_completed"] = False
    elif change == "missing":
        report["measured_workflow_JCT_ms"].pop(first)
    elif change == "extra":
        report["measured_workflow_JCT_ms"]["foreign"] = 1000
    elif change == "end":
        report["measured_cohort_completion_offset_s"] += 1
    else:
        report["measured_workflow_JCT_ms"][first] = {
            "nan": float("nan"), "zero": 0, "bool": True,
        }[change]
    with pytest.raises(ValueError):
        completion_coverage_screen(arrivals, cohort, report, arrivals)


def test_CPU_preparation_handles_substitutions_private_cohort_and_static_tail_warning(tmp_path, monkeypatch):
    arrivals, cohort = substituted_protocol()
    configuration = private_configuration(tmp_path, arrivals, cohort)
    config_path = write_json(tmp_path / "configuration.json", configuration)
    registration, audit = sampled_supply(tmp_path, cohort)
    write_json(registration, {"workflow_arrivals": arrivals})
    audit["inputs_sha256"][str(registration.resolve())] = sha256_file(registration)
    for name in ("previous_arrivals.json", "previous_cohort.json"):
        path = tmp_path / name
        audit["inputs_sha256"][str(path.resolve())] = sha256_file(path)
    audit_path = write_json(tmp_path / "supply.json", audit)
    report_path = write_json(tmp_path / "completion.json", completion_report(arrivals, cohort))
    output = tmp_path / "prepared"
    monkeypatch.setattr(sys, "argv", [
        "prepare_arrival_rate_followup.py",
        "--previous-registration", str(registration),
        "--previous-configuration", str(config_path),
        "--previous-cohort", str(tmp_path / "previous_cohort.json"),
        "--supply-audit", str(audit_path),
        "--previous-cohort-report", str(report_path),
        "--mean-interval-seconds", "30", "--output-dir", str(output),
    ])
    main()
    preparation = json.loads((output / "preparation.json").read_bytes())
    prepared = json.loads((output / "full.json").read_bytes())
    following = json.loads((output / "arrivals.json").read_bytes())
    analysis = json.loads((output / "analysis_cohort.json").read_bytes())
    validate_cohort(analysis, following)
    assert analysis["measured_workflow_ids"] == cohort["measured_workflow_ids"]
    assert prepared["common_environment"] == configuration["common_environment"]
    assert prepared["private_analysis_cohort"]["path"] == str(output / "analysis_cohort.json")
    assert following["replacements"] == arrivals["replacements"]
    assert preparation["private_analysis_cohort_reference_updated"]
    assert preparation["all_serving_settings_other_than_arrivals_identical"]
    assert not preparation["previous_measured_JCT_static_coverage_screen"]["candidate_schedule_covers_static_tail"]
    assert preparation["GPU_action"] is preparation["oracle_registered"] is False
    assert not preparation["formal_workload_accepted"]
