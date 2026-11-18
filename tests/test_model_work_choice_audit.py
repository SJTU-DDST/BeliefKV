from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import (
    KIND as REFERENCE_KIND, SERVING_KIND, forecast_fingerprint,
)
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.audit_workflow_combination_choices import verify_model_work_pair
from scripts.prepare_model_work_combination_oracle import prepare
from tests.test_learned_work_ranking_review import learned_pair
from tests.test_workflow_work_oracle import source


def model_work_pair(tmp_path, *, continuous=True):
    control, learned = learned_pair(tmp_path)
    private = {"path": "fixed18.json", "sha256": "a" * 64, "delivered_to_scheduler": False}
    control["private_analysis_cohort"] = deepcopy(private)
    learned["private_analysis_cohort"] = deepcopy(private)
    path = Path(learned["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = "r5_frozen_batch_cost_proxy_capacity_v7"
    raw["admission_cost_profile"]["costs"]["selection_objective"] = "benefit"
    path.write_text(json.dumps(raw))
    for prefix in ("", "BELIEFKV_"):
        learned["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    learned["comparison_contract"]["combination_selection_objective"] = "benefit"
    model = WorkflowWorkModel(raw)
    labels = source()
    reference = {
        "kind": REFERENCE_KIND, "serving_kind": SERVING_KIND,
        "target": "remaining_logical_model_work", "reference_work": 20.,
        "work_thresholds": list(model.thresholds),
        "reference_and_initial_priors_use_training_labels_only": True,
        "forecast_sha256": forecast_fingerprint(raw),
    }
    continuous_model = NS(raw={
        **deepcopy(raw), "continuous_work_readout": reference,
        "metadata": {
            "continuous_work_source_model_sha256": learned["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"],
        },
    }) if continuous else None
    output = tmp_path / "exact_model_work.json"
    truth, oracle = prepare(learned, model, labels, output, continuous_model=continuous_model)
    output.write_text(json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n")
    return control, oracle, learned


@pytest.mark.parametrize("continuous", (False, True))
def test_exact_work_audit_binds_full_and_learned_reference_without_mutation(tmp_path, continuous):
    configs = model_work_pair(tmp_path, continuous=continuous)
    before = deepcopy(configs)
    paths = verify_model_work_pair(*configs)
    assert len(paths) == 3
    assert paths[-1] == Path(configs[1]["common_environment"]["WORKFLOW_WORK_MODEL"])
    assert configs == before


@pytest.mark.parametrize("change", (
    "tools", "private_oracle", "private_reference", "host_oracle", "host_reference",
    "private_public", "alias", "threshold", "profile", "continuous_reference", "artifact_hash",
))
def test_exact_work_audit_rejects_changed_traffic_resources_reference_or_threshold(tmp_path, change):
    control, oracle, learned = model_work_pair(tmp_path)
    if change == "tools":
        oracle["tool_timing_overlay"] = {"changed": True}
    elif change == "private_public":
        for config in (control, learned, oracle):
            config["private_analysis_cohort"]["delivered_to_scheduler"] = True
    elif change.startswith("private_"):
        target = oracle if change == "private_oracle" else learned
        target["private_analysis_cohort"]["sha256"] = "b" * 64
    elif change.startswith("host_"):
        target = oracle if change == "host_oracle" else learned
        target["common_environment"]["HOST_GB"] = "400"
    elif change == "alias":
        oracle["common_environment"]["BELIEFKV_WORKFLOW_WORK_SOURCE"] = "learned"
    elif change == "threshold":
        oracle["comparison_contract"]["minimum_promising_measured_mean_JCT_reduction"] = .9
    else:
        path = Path(oracle["common_environment"]["WORKFLOW_WORK_MODEL"])
        raw = json.loads(path.read_bytes())
        if change == "profile":
            raw["admission_cost_profile"]["costs"]["prefill_ms_per_token"] *= 2
        elif change == "continuous_reference":
            raw["combination_reference_source_model_sha256"] = "b" * 64
        else:
            raw["free_dispatch_coalescing_window_ms"] = 1000
        path.write_text(json.dumps(raw, ensure_ascii=False, allow_nan=False) + "\n")
        if change != "artifact_hash":
            for prefix in ("", "BELIEFKV_"):
                oracle["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    with pytest.raises(ValueError):
        verify_model_work_pair(control, oracle, learned)


def test_exact_work_audit_rejects_new_oracle_utility_without_the_same_learned_readout(tmp_path):
    from beliefkv.predictor.workflow_continuous_work import ADMISSION_UTILITY_KIND

    control, oracle, learned = model_work_pair(tmp_path)
    path = Path(oracle["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["combination_work_reference"]["admission_utility"] = {
        "kind": ADMISSION_UTILITY_KIND, "reference_work": 1.,
        "initial_class_expected_utility": [.1, .01, .001],
        "initial_prior_uses_training_labels_only": True,
    }
    path.write_text(json.dumps(raw))
    for prefix in ("", "BELIEFKV_"):
        oracle["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    with pytest.raises(ValueError, match="utility contracts differ"):
        verify_model_work_pair(control, oracle, learned)


def test_oracle_new_utility_preserves_exact_remaining_work_and_native_progress(tmp_path):
    from beliefkv.predictor.workflow_continuous_work import ADMISSION_UTILITY_KIND
    from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
    from tests.test_workflow_work_oracle import oracle_file

    _control, config, _learned = model_work_pair(tmp_path)
    raw = json.loads(Path(config["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    raw["combination_work_reference"]["admission_utility"] = {
        "kind": ADMISSION_UTILITY_KIND, "reference_work": 1.,
        "initial_class_expected_utility": [.1, .01, .001],
        "initial_prior_uses_training_labels_only": True,
    }
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    assert oracle.prediction("wf").remaining_work == 47.
    assert oracle.prediction("wf").effective_weight == 1. / 48.
    assert oracle.prediction("wf").reference_work == 20.
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 17)
    assert oracle.prediction("wf").remaining_work == 30.
    assert oracle.prediction("wf").effective_weight == 1. / 31.
    oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
    assert oracle.prediction("wf").remaining_work == 12.
    assert oracle.prediction("wf").effective_weight == 1. / 13.
    assert oracle.status()["continuous_reference_work"] == 20.
    assert oracle.status()["continuous_admission_utility"]["reference_work"] == 1.
