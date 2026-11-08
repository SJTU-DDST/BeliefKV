from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.workflow_work_oracle import COMBINATION_WORK_KIND
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
    labels = ("Nonranking Full", "Learned R5", "Model-work Oracle")
    assert len({COLORS[label] for label in labels}) == 3
    render({label: deepcopy(raw) for label in labels}, (), tmp_path / "figures")
    provenance = json.loads((tmp_path / "figures/provenance.json").read_bytes())
    assert provenance["complete_fixed_cohort_JCT"] is True
    assert provenance["single_run_no_confidence_interval"] is True
    assert provenance["cohort"] == raw["cohort"]
    assert provenance["colors"]["Model-work Oracle"] == COLORS["Model-work Oracle"]
    assert (tmp_path / "figures/complete_JCT_and_decode.png").stat().st_size > 1000
