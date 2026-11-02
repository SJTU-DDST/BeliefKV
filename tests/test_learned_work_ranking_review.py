from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.workflow_work_oracle import KIND
from scripts.review_learned_work_ranking import verify_learned_combination_pair, verify_learned_traffic
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from tests.test_workflow_work_distribution import distribution_raw
from tests.test_matched_work_ranking_review import pair


def learned_pair(tmp_path):
    control, learned = pair(tmp_path)
    old = Path(control["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(old.read_bytes())
    raw["kind"] = KIND
    old.write_text(json.dumps(raw))
    for prefix in ("", "BELIEFKV_"):
        control["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(old)
    artifact = distribution_raw()
    artifact.update(
        free_dispatch_coalescing_window_ms=2000,
        admission_cost_profile={
            "kind": "r5_frozen_batch_cost_proxy_capacity_v6", "identity": "frozen-cost",
            "costs": {
                "prefill_ms_per_token": .01, "h2d_ms_per_token": .001,
                "decode_ms_by_width": [1.] * 24, "reference_context_tokens": 10000.,
                "decode_cost_basis": "full_batch",
            },
        },
    )
    path = Path(learned["common_environment"]["WORKFLOW_WORK_MODEL"])
    path.write_text(json.dumps(artifact))
    for prefix in ("", "BELIEFKV_"):
        learned["common_environment"].update({
            prefix + "WORKFLOW_WORK_MODEL_SHA256": sha256_file(path),
            prefix + "WORKFLOW_WORK_SOURCE": "learned",
            prefix + "WORKFLOW_COMBINATION_ADMISSION": "1",
        })
    learned["comparison_contract"].update(
        workflow_combination_admission=True, future_information_used=False,
        physical_capacity_credit=False, matched_pair_changes_only_remaining_work_ranking=False,
    )
    return control, learned


def test_learned_pair_is_joint_change_without_modifying_original_artifacts(tmp_path):
    control, learned = learned_pair(tmp_path)
    before = deepcopy((control, learned))
    assert len(verify_learned_combination_pair(control, learned)) == 2
    assert (control, learned) == before


@pytest.mark.parametrize("change", ("tools", "host", "alias", "source", "window", "future", "ranking_only",
                                  "physical_credit", "threshold", "profile", "graph"))
def test_learned_pair_rejects_changed_work_or_false_policy_declarations(tmp_path, change):
    control, learned = learned_pair(tmp_path)
    if change == "tools":
        learned["tool_timing_overlay"] = {"changed": True}
    elif change == "host":
        learned["common_environment"]["HOST_GB"] = "400"
    elif change == "alias":
        learned["common_environment"]["BELIEFKV_WORKFLOW_WORK_SOURCE"] = "oracle"
    elif change == "source":
        for prefix in ("", "BELIEFKV_"):
            learned["common_environment"][prefix + "WORKFLOW_WORK_SOURCE"] = "oracle"
    elif change in ("future", "ranking_only", "physical_credit", "threshold"):
        name, value = {
            "future": ("future_information_used", True),
            "ranking_only": ("matched_pair_changes_only_remaining_work_ranking", True),
            "physical_credit": ("physical_capacity_credit", True),
            "threshold": ("minimum_promising_measured_mean_JCT_reduction", .01),
        }[change]
        learned["comparison_contract"][name] = value
    else:
        path = Path(learned["common_environment"]["WORKFLOW_WORK_MODEL"])
        raw = json.loads(path.read_bytes())
        if change == "window":
            raw["free_dispatch_coalescing_window_ms"] = 1000
        elif change == "graph":
            raw["workflow_graphs"] = {}
        else:
            raw["admission_cost_profile"]["kind"] = "r5_frozen_batch_cost_proxy_capacity_v4"
        path.write_text(json.dumps(raw))
        for prefix in ("", "BELIEFKV_"):
            learned["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    with pytest.raises(ValueError):
        verify_learned_combination_pair(control, learned)


def test_learned_execution_requires_exact_work_resources_and_no_oracle_information():
    control = {name: "same" for name in (
        *TRAFFIC_FIELDS, "expected_replay_capacity", "host_capacity_experiment",
        "native_execution_configuration",
    )}
    control["order"] = ["full"]
    learned = deepcopy(control)
    verify_learned_traffic(control, learned)
    for name in ("engine_tree", "host_capacity_experiment", "workflow_arrivals"):
        changed = deepcopy(learned)
        changed[name] = "different"
        with pytest.raises(ValueError):
            verify_learned_traffic(control, changed)
    learned["oracle_information"] = {"future": "available"}
    with pytest.raises(ValueError, match="future"):
        verify_learned_traffic(control, learned)
