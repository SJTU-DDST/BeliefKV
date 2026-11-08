from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.workflow_work_oracle import (
    COMBINATION_WORK_KIND, CONTINUOUS_COMBINATION_WORK_KIND, CONTINUOUS_PRESSURE_WORK_KIND,
    TOOL_BAND_PRESSURE_ORDER,
)
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
from scripts import finish_model_work_oracle as finish
from scripts.plot_workflow_cohort_comparison import COLORS, render
from tests.test_workflow_cohort_comparison_plot import report


def pair():
    old = {
        "WORKFLOW_WORK_SOURCE": "learned", "BELIEFKV_WORKFLOW_WORK_SOURCE": "learned",
        "WORKFLOW_WORK_MODEL": "learned.json", "BELIEFKV_WORKFLOW_WORK_MODEL": "learned.json",
        "WORKFLOW_WORK_MODEL_SHA256": "old", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "old",
        "WORKFLOW_COMBINATION_ADMISSION": "1", "HICACHE_SIZE_GB": "400",
        "TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0",
    }
    new = deepcopy(old)
    for prefix in ("", "BELIEFKV_"):
        new[prefix + "WORKFLOW_WORK_SOURCE"] = "oracle"
        new[prefix + "WORKFLOW_WORK_MODEL"] = "oracle.json"
        new[prefix + "WORKFLOW_WORK_MODEL_SHA256"] = "new"
    control = {"common_environment": old, "order": ["full"], "workflow_arrivals": "fixed"}
    candidate = {"common_environment": new, "order": ["full"], "workflow_arrivals": "fixed"}
    model = {
        "work_thresholds": [100., 1000.],
        "admission_cost_profile": {"kind": "r5_frozen_batch_cost_proxy_capacity_v7"},
        "free_dispatch_coalescing_window_ms": 2000,
    }
    truth = {
        **deepcopy(model), "kind": COMBINATION_WORK_KIND,
        "combination_work_readout": "three_bins", "admission_order": "r5_combination_only",
        "remaining_work_ranking_enabled": True,
    }
    return control, candidate, model, truth


def test_same_objective_verification_does_not_modify_evidence():
    values = pair()
    before = deepcopy(values)
    finish.verify_same_objective(*values)
    assert values == before


@pytest.mark.parametrize("field", (
    "capacity", "arrivals", "source_alias", "thresholds", "costs", "window",
    "global_order", "continuous", "disabled", "native_arm",
))
def test_cpu_finish_rejects_a_different_policy_or_traffic(field):
    control, candidate, model, truth = pair()
    if field == "capacity":
        candidate["common_environment"]["HICACHE_SIZE_GB"] = "200"
    elif field == "arrivals":
        candidate["workflow_arrivals"] = "changed"
    elif field == "source_alias":
        candidate["common_environment"]["BELIEFKV_WORKFLOW_WORK_SOURCE"] = "learned"
    elif field == "thresholds":
        truth["work_thresholds"] = [101., 1000.]
    elif field == "costs":
        truth["admission_cost_profile"]["kind"] = "another"
    elif field == "window":
        truth["free_dispatch_coalescing_window_ms"] = 1000
    elif field == "global_order":
        truth["admission_order"] = "global_ready_remaining_path"
    elif field == "continuous":
        truth["combination_work_readout"] = "continuous"
    elif field == "disabled":
        truth["remaining_work_ranking_enabled"] = False
    else:
        candidate["order"] = ["native_matched"]
    with pytest.raises(ValueError, match="differs"):
        finish.verify_same_objective(control, candidate, model, truth)


def test_cpu_coordinator_replacement_cannot_signal_a_serving_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(finish, "require_cpu", lambda: None)
    execution_path = tmp_path / "execution_plan.json"
    reg_path = tmp_path / "run.registration.json"
    actor = {
        "pid": 1, "start_ticks": "a",
        "argv": ["start_r5_model_work_oracle_retry_20261108.py"],
    }
    observer = {"pid": 4, "start_ticks": "d"}
    execution = {"registration": str(reg_path)}
    values = {
        execution_path: execution,
        execution_path.parent / "execution_state.json": {
            "status": "waiting_fixed_18_model_work_oracle", "process": actor,
        },
        reg_path.with_suffix(".state.json"): {
            "status": "running_full", "watcher": actor,
            "server": {"pid": 2}, "driver": {"pid": 3},
        },
        reg_path.with_name("run.cohort.registration.json").with_suffix(".state.json"): {
            "process": observer,
        },
    }
    monkeypatch.setattr(finish.causal, "load", lambda path: values[path])
    monkeypatch.setattr(finish.causal.reservation, "alive", lambda owner: True)
    monkeypatch.setattr(finish.os, "kill", lambda *args: pytest.fail("signaled serving work"))
    with pytest.raises(ValueError, match="CPU coordinator"):
        finish.register(execution_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_explicit_model_work_oracle_label_renders_with_preserved_measurement(tmp_path):
    raw = report()
    labels = ("Nonranking Full", "Learned R5", "Model-work Oracle", "Continuous-work Oracle")
    assert len({COLORS[label] for label in labels}) == len(labels)
    render({label: deepcopy(raw) for label in labels}, (), tmp_path / "figures")
    provenance = json.loads((tmp_path / "figures/provenance.json").read_bytes())
    assert provenance["complete_fixed_cohort_JCT"] is True
    assert provenance["single_run_no_confidence_interval"] is True
    assert provenance["cohort"] == raw["cohort"]
    assert provenance["colors"]["Model-work Oracle"] == COLORS["Model-work Oracle"]
    assert (tmp_path / "figures/complete_JCT_and_decode.png").stat().st_size > 1000


def test_continuous_truth_finish_preserves_declared_selector_and_reference():
    control, candidate, model, truth = pair()
    truth.update(
        kind=CONTINUOUS_COMBINATION_WORK_KIND, combination_work_readout="continuous",
        combination_reference_source_model_sha256="old",
        combination_work_reference={
            "forecast_sha256": forecast_fingerprint(model),
            "work_thresholds": model["work_thresholds"], "reference_work": 200.,
            "reference_and_initial_priors_use_training_labels_only": True,
        },
    )
    finish.verify_same_objective(control, candidate, model, truth)
    truth["combination_reference_source_model_sha256"] = "another-model"
    with pytest.raises(ValueError, match="training reference"):
        finish.verify_same_objective(control, candidate, model, truth)


def test_finish_waits_for_live_observers_first_atomic_publication(tmp_path, monkeypatch):
    path = tmp_path / "observer.state.json"
    pulses = []
    monkeypatch.setattr(finish.causal.reservation, "alive", lambda owner: True)
    monkeypatch.setattr(finish.time, "monotonic", lambda: 0.)
    monkeypatch.setattr(finish.time, "sleep", lambda _: path.write_text("{}"))
    finish.wait_observer_publication(path, {"pid": 1}, lambda: pulses.append(1))
    assert path.is_file()
    assert pulses == [1]


@pytest.mark.parametrize("alive", (False, True))
def test_initial_observer_publication_has_bounded_missing_owner_handling(tmp_path, monkeypatch, alive):
    path = tmp_path / "missing.state.json"
    ticks = iter((0., 31.))
    monkeypatch.setattr(finish.causal.reservation, "alive", lambda owner: alive)
    monkeypatch.setattr(finish.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(finish.time, "sleep", lambda _: pytest.fail("unbounded wait"))
    with pytest.raises(TimeoutError if alive else ValueError, match="cohort observer"):
        finish.wait_observer_publication(path, {"pid": 1}, lambda: None)


def test_published_observer_needs_no_startup_delay(tmp_path, monkeypatch):
    path = tmp_path / "observer.state.json"
    path.write_text("{}")
    monkeypatch.setattr(finish.time, "sleep", lambda _: pytest.fail("delayed an existing observer"))
    finish.wait_observer_publication(path, {}, lambda: pytest.fail("unnecessary progress pulse"))


def pressure_pair():
    control, candidate, model, truth = pair()
    for config in (control, candidate):
        config["common_environment"]["AGENT_PRESSURE_PAUSE_POLICY"] = "workflow_tool_aware"
    truth.update(
        kind=CONTINUOUS_PRESSURE_WORK_KIND, combination_work_readout="continuous",
        admission_order="r5_combination_and_tool_band_pressure",
        pressure_work_order=TOOL_BAND_PRESSURE_ORDER,
        combination_reference_source_model_sha256="old",
        combination_work_reference={
            "forecast_sha256": forecast_fingerprint(model),
            "work_thresholds": model["work_thresholds"], "reference_work": 200.,
            "reference_and_initial_priors_use_training_labels_only": True,
        },
    )
    return control, candidate, model, truth


def test_pressure_finish_requires_explicit_permission_to_compare_the_extended_scope():
    values = pressure_pair()
    before = deepcopy(values)
    with pytest.raises(ValueError, match="differs"):
        finish.verify_same_objective(*values)
    finish.verify_same_objective(*values, allow_tool_band_pressure=True)
    assert values == before
    scope = finish.comparison_scope(values[-1])
    assert scope["same_R5_selector_and_declared_work_readout_verified"]
    assert not scope["same_noncombination_policy_verified"]
    assert not scope["same_R5_selector_and_three_bin_utility_verified"]
    assert scope["not_a_pure_prediction_error_comparison"]
    assert scope["work_used_for_tool_band_pressure"]


@pytest.mark.parametrize("field", ("scope", "tool_policy", "reference", "workload", "selector"))
def test_pressure_finish_still_rejects_unrelated_changes(field):
    control, candidate, model, truth = pressure_pair()
    if field == "scope":
        truth["pressure_work_order"] = "global"
    elif field == "tool_policy":
        for config in (control, candidate):
            config["common_environment"]["AGENT_PRESSURE_PAUSE_POLICY"] = "workflow_remaining"
    elif field == "reference":
        truth["combination_work_reference"]["reference_and_initial_priors_use_training_labels_only"] = False
    elif field == "workload":
        candidate["workflow_arrivals"] = "different"
    else:
        truth["admission_cost_profile"] = {"kind": "different"}
    with pytest.raises(ValueError, match="differs"):
        finish.verify_same_objective(
            control, candidate, model, truth, allow_tool_band_pressure=True,
        )


def test_legacy_finish_cannot_accept_a_hidden_pressure_declaration():
    control, candidate, model, truth = pair()
    truth["pressure_work_order"] = TOOL_BAND_PRESSURE_ORDER
    with pytest.raises(ValueError, match="differs"):
        finish.verify_same_objective(control, candidate, model, truth, allow_tool_band_pressure=True)


def test_pressure_method_has_a_distinct_label_and_preserves_complete_plot_population(tmp_path):
    labels = (
        "Nonranking Full", "Learned R5", "Model-work Oracle",
        "Continuous-work Oracle", finish.method_label(pressure_pair()[-1]),
    )
    assert len({COLORS[label] for label in labels}) == len(labels)
    raw = report()
    render({label: deepcopy(raw) for label in labels}, (), tmp_path / "figures")
    provenance = json.loads((tmp_path / "figures/provenance.json").read_bytes())
    assert provenance["cohort"] == raw["cohort"]
    assert provenance["complete_fixed_cohort_JCT"]
    assert provenance["colors"][labels[-1]] == "#000000"
