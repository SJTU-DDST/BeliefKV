from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import review_materialization_campaign as review
from scripts.compare_reported_workflow_cohorts import HOST_POOL_TELEMETRY, WRITE_BACK_CAP_SETTING
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from scripts.train_workflow_work import write
from tests.test_sustained_oracle_review import cohort_report


def completed_campaign(tmp_path, *, write_back_cap=32):
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
            "registration": str(tmp_path / label / "registration.json"),
        }
        plan["stages"][label] = stage
        write(Path(stage["registration"]), {
            "order": [stage["arm"]], "run_root": stage["run_root"],
            "reference_environment": {WRITE_BACK_CAP_SETTING: str(write_back_cap)},
        })
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
        path = Path(stage["run_root"]) / stage["arm"] / HOST_POOL_TELEMETRY
        path.write_text(json.dumps({
            "event": "host_pool_usage", "write_back_batch": {"max_nodes": write_back_cap},
        }) + "\n")
    path = tmp_path / "plan.json"
    write(path, plan)
    return path, plan


@pytest.mark.parametrize("cap", (1, 32))
def test_both_full_arms_are_compared_with_each_matched_baseline(tmp_path, monkeypatch, cap):
    plan_path, plan = completed_campaign(tmp_path, write_back_cap=cap)
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
        assert result["write_back_cap_comparison"] == {"status": "matched", "matched_max_nodes": cap}
        assert str(plan_path) in result["inputs_sha256"]
        assert str(tmp_path / (baseline + "_execution_proof.json")) in result["inputs_sha256"]
        assert result["performance_accepted"] is False
    primary = json.loads((output / "comparison.json").read_bytes())
    assert primary["groups"]["measured"]["mean_JCT_reduction_percent"] == pytest.approx(-20.)
    assert set(primary["native_write_back_batches"]) == set(plan["stages"])
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


@pytest.mark.parametrize("label", ("control", "thunderagent_acting_decay", "native_matched"))
@pytest.mark.parametrize("observed", (1, None))
def test_four_arm_review_requires_matching_batched_write_back(tmp_path, monkeypatch, label, observed):
    plan_path, plan = completed_campaign(tmp_path)
    stage = plan["stages"][label]
    path = Path(stage["run_root"]) / stage["arm"] / HOST_POOL_TELEMETRY
    if observed is None:
        path.unlink()
        error = f"effective native write-back cap is unknown for: {label}"
    else:
        registration = Path(stage["registration"])
        record = review.load(registration)
        record["reference_environment"][WRITE_BACK_CAP_SETTING] = str(observed)
        registration.write_text(json.dumps(record))
        path.write_text(json.dumps({
            "event": "host_pool_usage", "write_back_batch": {"max_nodes": observed},
        }) + "\n")
        error = "different effective native write-back caps"
    render = Mock()
    monkeypatch.setattr(review, "render", render)
    output = tmp_path / "results"
    with pytest.raises(ValueError, match=error):
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


@pytest.mark.parametrize("difference", (None, "settings", "source", "cohort", "work", "exit"))
def test_reuse_requires_completed_matching_control(tmp_path, monkeypatch, difference):
    plan_path, plan = completed_campaign(tmp_path)
    proof_by_registration = {}
    for label in ("control", "pressure"):
        stage = plan["stages"][label]
        directory = tmp_path / label
        registration = directory / "registration.json"
        stage["registration"] = str(registration)
        data = {
            **dict.fromkeys(TRAFFIC_FIELDS),
            "order": ["full"], "run_root": stage["run_root"],
            "reference_environment": {
                "BELIEFKV_SUBAGENT_MATERIALIZATION": "off" if label == "control" else "pressure_v2",
                "WORKFLOW_WORK_MODEL_SHA256": "predictor",
                WRITE_BACK_CAP_SETTING: "32",
            },
            "source_sha256": {
                **dict.fromkeys(review.D3_RUNTIME_SOURCES, label),
                "beliefkv/runtime/workflow_combination.py": "shared",
            },
        }
        if label == "control" and difference == "settings":
            data["reference_environment"]["WORKFLOW_WORK_MODEL_SHA256"] = "other"
        if label == "control" and difference == "source":
            data["source_sha256"]["beliefkv/runtime/workflow_combination.py"] = "other"
        registration.write_text(json.dumps(data))
        proof = review.load(tmp_path / (label + "_execution_proof.json"))
        summary = review.load(directory / "review/cohort.json")
        certificate = directory / "completion.json"
        measured_jct = dict(summary["measured_workflow_JCT_ms"])
        if label == "control" and difference == "cohort":
            measured_jct[next(iter(measured_jct))] += 1.
        write(certificate, {"measured_JCT_ms": measured_jct})
        proof.update(
            protocol="all_fixed_measured_workflows_completed",
            registration_sha256=review.sha256_file(registration),
            cohort=summary["cohort"], cohort_sha256="cohort",
            certificate=str(certificate), certificate_sha256=review.sha256_file(certificate),
            measured_requests=1,
        )
        if label == "control" and difference == "work":
            proof["work"]["request"][0] += 1
        if label == "control" and difference == "exit":
            proof["actual_owned_exit_verified"] = False
        proof_by_registration[registration] = proof
        write(directory / "review/review.json", {
            "measured_cohort_and_owned_exit_verified": True,
            "registration_sha256": proof["registration_sha256"],
            "complete_measured_native_work_verified": True,
            "background_right_censoring_explicit": True,
            "termination_protocol": proof["protocol"],
            "evaluation_cohort": proof["cohort"],
            "cohort_sha256": proof["cohort_sha256"],
            "measured_native_requests": proof["measured_requests"],
            "termination_certificate_sha256": proof["certificate_sha256"],
        })
    control, pressure = (plan["stages"][label] for label in ("control", "pressure"))
    cohort = tmp_path / "cohort.json"
    write(cohort, proof_by_registration[Path(control["registration"])]["cohort"])
    plan = {"GPU_stage_order": ["pressure"], "stages": {"pressure": pressure}, "cohort": str(cohort)}
    plan_path.write_text(json.dumps(plan))
    verify = Mock(side_effect=lambda registration, _cohort: proof_by_registration[registration])
    monkeypatch.setattr(review, "verify_execution", verify)
    render = Mock()
    monkeypatch.setattr(review, "render", render)
    reuse = Path(control["registration"]), Path(control["review"])
    output = tmp_path / "results"
    if difference is not None:
        with pytest.raises(ValueError, match="settings|sources|certificate|work|exit"):
            review.review(plan_path, output, reuse_control=reuse)
        assert not output.exists()
        render.assert_not_called()
        return

    review.review(plan_path, output, reuse_control=reuse)
    result = review.load(output / "comparison.json")
    assert result["reused_control_registration"] == str(reuse[0])
    assert result["groups"]["measured"]["mean_JCT_reduction_percent"] == pytest.approx(-20.)
    assert verify.call_count == 2
    assert str(reuse[0]) in result["inputs_sha256"]
