from copy import deepcopy
import json

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.start_workflow_ranking_disabled import verify_pair


def pair(tmp_path):
    enabled, disabled = {}, {}
    for spec, name, flag in ((enabled, "on", True), (disabled, "off", False)):
        path = tmp_path / (name + ".json")
        raw = {"prediction_parameters": [1, 2], "remaining_work_ranking_enabled": flag}
        path.write_text(json.dumps(raw))
        spec["common_environment"] = {
            "WORKFLOW_WORK_SOURCE": "learned", "WORKFLOW_COMBINATION_ADMISSION": "1",
            "BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS": "400",
        }
        for prefix in ("", "BELIEFKV_"):
            spec["common_environment"].update({
                prefix + "WORKFLOW_WORK_MODEL": str(path),
                prefix + "WORKFLOW_WORK_MODEL_SHA256": sha256_file(path),
            })
        for field in ("workflow_arrivals", "private_analysis_cohort", "tool_timing_overlay"):
            spec[field] = {"sha256": field}
    return enabled, disabled


def test_matched_control_retains_parameters_and_prediction_worker(tmp_path):
    specs = pair(tmp_path)
    before = deepcopy(specs)
    verify_pair(*specs)
    assert specs == before


@pytest.mark.parametrize("change", ("cadence", "arrivals", "cohort", "tools", "parameters", "switch"))
def test_matched_control_rejects_other_changes(tmp_path, change):
    enabled, disabled = pair(tmp_path)
    if change == "cadence":
        disabled["common_environment"]["BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS"] = "200"
    elif change in ("arrivals", "cohort", "tools"):
        field = {"arrivals": "workflow_arrivals", "cohort": "private_analysis_cohort",
                 "tools": "tool_timing_overlay"}[change]
        disabled[field] = {"changed": True}
    else:
        path = tmp_path / "off.json"
        raw = json.loads(path.read_bytes())
        raw["prediction_parameters" if change == "parameters" else "remaining_work_ranking_enabled"] = []
        path.write_text(json.dumps(raw))
        for prefix in ("", "BELIEFKV_"):
            disabled["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(path)
    with pytest.raises(ValueError):
        verify_pair(enabled, disabled)
