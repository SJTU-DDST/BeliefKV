from copy import deepcopy
import math

import pytest

from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from scripts.prepare_arrival_rate_followup import followup_configuration, followup_protocol


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
