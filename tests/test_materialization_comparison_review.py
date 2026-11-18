from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from scripts import review_materialization_campaign as review
from scripts.train_workflow_work import write
from tests.test_sustained_oracle_review import cohort_report


def completed_campaign(tmp_path):
    measured = {
        "control": (100., 300.),
        "pressure": (120., 360.),
        "thunderagent_acting_decay": (150., 450.),
        "native_matched": (200., 600.),
    }
    plan = {"stages": {}}
    for label in review.LABELS:
        stage = {
            "review": str(tmp_path / label / "review"),
            "run_root": str(tmp_path / label / "run"),
            "arm": "full" if label in ("control", "pressure") else label,
        }
        plan["stages"][label] = stage
        write(tmp_path / (label + "_execution_proof.json"), {
            "actual_owned_exit_verified": True,
            "work": {"request": [10, 2, "prompt", "output", {"type": "stop", "matched": 1}]},
        })
        write(
            tmp_path / label / "review/cohort.json",
            cohort_report(measured=measured[label]),
        )
        write(
            tmp_path / label / "run" / stage["arm"] / "server/native_capacity_census.json",
            {"capacity": {"device_full_tokens": 805198, "host_full_tokens": 2034506}},
        )
    path = tmp_path / "plan.json"
    write(path, plan)
    return path, plan


def test_both_full_arms_are_compared_with_each_matched_baseline(tmp_path, monkeypatch):
    plan_path, plan = completed_campaign(tmp_path)
    render = Mock()
    monkeypatch.setattr(review, "render", render)
    output = tmp_path / "results"
    review.review(plan_path, output, baselines=True)

    expected = (
        ("thunderagent_acting_decay", "control", 100. / 3),
        ("thunderagent_acting_decay", "pressure", 20.),
        ("native_matched", "control", 50.),
        ("native_matched", "pressure", 40.),
    )
    for baseline, candidate, gain in expected:
        suffix = "_control_comparison.json" if candidate == "control" else "_comparison.json"
        result = json.loads((output / (baseline + suffix)).read_bytes())
        assert result["reference_arm"] == baseline
        assert result["candidate_arm"] == candidate
        assert result["groups"]["measured"]["mean_JCT_reduction_percent"] == pytest.approx(gain)
        assert result["actual_capacities_equal"] and result["complete_measured_work_equal"]
        assert str(plan_path) in result["inputs_sha256"]
        assert str(tmp_path / (baseline + "_execution_proof.json")) in result["inputs_sha256"]
        assert result["performance_accepted"] is False
    primary = json.loads((output / "comparison.json").read_bytes())
    assert primary["groups"]["measured"]["mean_JCT_reduction_percent"] == pytest.approx(-20.)
    assert primary["performance_accepted"] is False
    assert len(render.call_args.args[0]) == len(plan["stages"])


@pytest.mark.parametrize("label", ("thunderagent_acting_decay", "native_matched"))
@pytest.mark.parametrize("change", ("work", "capacity", "actual_exit"))
def test_baseline_mismatch_is_rejected_before_publishing_comparisons(
    tmp_path, monkeypatch, label, change,
):
    plan_path, plan = completed_campaign(tmp_path)
    stage = plan["stages"][label]
    if change == "capacity":
        path = tmp_path / label / "run" / stage["arm"] / "server/native_capacity_census.json"
        record = json.loads(path.read_bytes())
        record["capacity"]["host_full_tokens"] += 1
    else:
        path = tmp_path / (label + "_execution_proof.json")
        record = json.loads(path.read_bytes())
        if change == "actual_exit":
            record["actual_owned_exit_verified"] = False
        else:
            record["work"]["request"][0] += 1
    path.write_text(json.dumps(record))
    render = Mock()
    monkeypatch.setattr(review, "render", render)
    output = tmp_path / "results"
    with pytest.raises(ValueError, match="exit|work|capacities"):
        review.review(plan_path, output, baselines=True)
    assert not output.exists()
    render.assert_not_called()


def test_pair_only_review_does_not_require_unfinished_baselines(tmp_path, monkeypatch):
    plan_path, plan = completed_campaign(tmp_path)
    pair_plan = deepcopy(plan)
    pair_plan["stages"] = {
        label: stage for label, stage in plan["stages"].items()
        if label in ("control", "pressure")
    }
    plan_path.write_text(json.dumps(pair_plan))
    render = Mock()
    monkeypatch.setattr(review, "render", render)
    output = tmp_path / "results"
    review.review(plan_path, output)
    assert (output / "comparison.json").is_file()
    assert not list(output.glob("*_control_comparison.json"))
    assert set(render.call_args.args[0]) == {"Full control", "Full + materialization"}
