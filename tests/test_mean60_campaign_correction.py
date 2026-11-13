from copy import deepcopy
import math
from pathlib import Path

import pytest

from scripts import correct_mean60_matched_campaign as correction
from scripts import run_continuous_matched_campaign as campaign


def plan_fixture(tmp_path):
    arrivals = tmp_path / "arrivals.json"
    cohort = tmp_path / "cohort.json"
    measured = [f"workflow-{index}" for index in range(18)]
    campaign.write(arrivals, {
        "rate_per_second": 1. / 60., "initial_workflows": 20,
        "arrival_schedule": [{"workflow_id": str(index)} for index in range(100)],
    })
    campaign.write(cohort, {
        "measured_workflow_ids": measured,
        "background_submission_stops_when_cohort_finishes": True,
        "cohort_identity_available_to_scheduler": False,
    })
    plan = {
        "mean_interval_seconds": 60., "initial_workflows": 20, "workflows": 100,
        "arrivals": str(arrivals), "cohort": str(cohort),
        "fixed_measured_workflows": measured, "stages": {},
    }
    for label in campaign.LABELS:
        configuration = tmp_path / (label + ".json")
        campaign.write(configuration, {
            "workflow_arrivals": {"path": str(arrivals), "sha256": campaign.digest(arrivals)},
            "private_analysis_cohort": {
                "path": str(cohort), "sha256": campaign.digest(cohort),
                "delivered_to_scheduler": False,
            },
        })
        plan["stages"][label] = {
            "registration": f"workflow_original100_host400_mean60_{label}.registration.json",
            "configuration": str(configuration),
            "command": [
                "--workflow-arrivals", str(arrivals), "--experiment-config", str(configuration),
            ],
        }
    return plan


def test_actual_mean60_contract_accepts_all_five_matched_methods(tmp_path):
    plan = plan_fixture(tmp_path)
    correction.validate_mean60(plan)


@pytest.mark.parametrize("field,value", (
    ("mean_interval_seconds", 70.), ("initial_workflows", 12), ("workflows", 83),
))
def test_wrong_campaign_settings_are_rejected(tmp_path, field, value):
    plan = plan_fixture(tmp_path)
    plan[field] = value
    with pytest.raises(ValueError, match="actual mean60"):
        correction.validate_mean60(plan)


def test_mean60_label_cannot_conceal_actual_mean70_arrivals(tmp_path):
    plan = plan_fixture(tmp_path)
    actual = campaign.load(plan["arrivals"])
    actual["rate_per_second"] = 1. / 70.
    campaign.write(plan["arrivals"], actual)
    assert not math.isclose(actual["rate_per_second"], 1. / 60.)
    with pytest.raises(ValueError, match="actual mean60"):
        correction.validate_mean60(plan)


@pytest.mark.parametrize("label", campaign.LABELS)
def test_no_method_may_keep_wrong_arrival_command_or_old_live_full(tmp_path, label):
    plan = plan_fixture(tmp_path)
    changed = deepcopy(plan)
    changed["stages"][label]["command"][1] = "old_mean70_arrivals.json"
    with pytest.raises(ValueError, match="same real mean60"):
        correction.validate_mean60(changed)
    changed = deepcopy(plan)
    changed["stages"][label]["existing_watcher"] = {"pid": 1}
    with pytest.raises(ValueError, match="register fresh"):
        correction.validate_mean60(changed)


def test_fresh_identity_keeps_sampler_source_and_tools(tmp_path):
    previous = {
        "registration": "/ops/workflow_original100_host400_mean70_full_old.registration.json",
        "run_root": "/raw/workflow_original100_host400_mean70_full_old",
        "command": [
            "python", "frozen/run_causal_comparison.py",
            "--registration", "old-registration", "--run-root", "old-run", "--port", "19041",
            "--experiment-config", "old-config", "--workflow-arrivals", "old-arrivals",
            "--gpu-smoke", "same-source-smoke", "--sampling-calibration", "same-native-sampler",
            "--tool-duration-overlay", "same-tools", "--source-arm", "same-source",
        ],
        "existing_watcher": {"pid": 123},
    }
    stage = correction.corrected_stage(
        previous, "full", Path("new-config"), Path("mean60-arrivals"),
        tmp_path, 19046, "20261113_r1",
    )
    assert stage["existing_watcher"] is None
    assert "mean60_full_20261113_r1" in stage["registration"]
    for flag in ("--gpu-smoke", "--sampling-calibration", "--tool-duration-overlay", "--source-arm"):
        assert stage["command"][stage["command"].index(flag) + 1] == (
            previous["command"][previous["command"].index(flag) + 1]
        )
    assert previous["existing_watcher"] == {"pid": 123}


def test_display_names_match_the_frozen_plot_palette():
    from scripts.plot_workflow_cohort_comparison import COLORS

    assert set(correction.DISPLAY_NAMES) <= set(COLORS)
