from copy import deepcopy

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_background_oracle_pair import MODEL_FIELDS, verify_pair
from scripts.train_workflow_work import write
from tests.test_workflow_work_oracle import source


def pair(tmp_path):
    configs = []
    for name, enabled in (("full", False), ("oracle", True)):
        artifact = source()
        artifact.update(
            remaining_work_ranking_enabled=enabled,
            admission_order="global_ready_remaining_path", free_dispatch_coalescing_window_ms=2000,
        )
        path = tmp_path / (name + ".json")
        write(path, artifact)
        common = {
            "AGENT_ADMISSION": "thunderagent_reference",
            "AGENT_FREE_CONTINUATION_ADMISSION": "1", "AGENT_CONTROL_INTERVAL_MS": "2000",
            "WORKFLOW_SIZE_RESUME": "1", "WORKFLOW_WORK_SOURCE": "oracle",
            "TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0", "HICACHE_SIZE_GB": "400",
            "BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS": "400",
        }
        for key in MODEL_FIELDS:
            common[key] = sha256_file(path) if key.endswith("SHA256") else str(path)
        configs.append({
            "common_environment": common, "source_registration": None,
            "gpu_execution": {"registered": False},
            "comparison_contract": {"workflow_work_ranking": enabled},
            "workflow_work_qualification": {
                "artifact": str(path), "artifact_sha256": sha256_file(path),
                "remaining_work_ranking_enabled": enabled,
            },
            "private_analysis_cohort": {"path": "/fixed/cohort", "delivered_to_scheduler": False},
            "workflow_arrivals": {"path": "/fixed/arrivals"},
        })
    return configs


def test_matched_ranking_pair_keeps_effective_controller_and_timing_identical(tmp_path):
    control, oracle = pair(tmp_path)
    before = deepcopy((control, oracle))
    checks = verify_pair(control, oracle)
    assert checks["effective_environments_equal_except_artifact_paths_and_hashes"]
    assert checks["artifacts_differ_only_in_remaining_work_ranking_enabled"]
    assert (control, oracle) == before
    assert not checks["GPU_action"]


@pytest.mark.parametrize("field,value", (
    ("AGENT_CONTROL_INTERVAL_MS", "1000"), ("BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS", "200"),
    ("TOOL_PREFETCH", "1"), ("HICACHE_SIZE_GB", "200"),
))
def test_cadence_feature_or_resource_differences_cannot_be_attributed_to_ranking(tmp_path, field, value):
    control, oracle = pair(tmp_path)
    oracle["common_environment"][field] = value
    with pytest.raises(ValueError, match="more than"):
        verify_pair(control, oracle)


@pytest.mark.parametrize("field", ("workflow_arrivals", "private_analysis_cohort"))
def test_different_traffic_or_measurement_members_are_rejected(tmp_path, field):
    control, oracle = pair(tmp_path)
    oracle[field]["path"] = "/different/input"
    with pytest.raises(ValueError, match="more than"):
        verify_pair(control, oracle)


def test_different_tool_truth_cannot_hide_in_the_oracle_artifact(tmp_path):
    control, oracle = pair(tmp_path)
    path = tmp_path / "changed.json"
    raw = source()
    raw["workflow_graphs"]["wf"][-1]["delay_s"] = 10.
    raw.update(
        remaining_work_ranking_enabled=True,
        admission_order="global_ready_remaining_path", free_dispatch_coalescing_window_ms=2000,
    )
    write(path, raw)
    for key in MODEL_FIELDS:
        oracle["common_environment"][key] = sha256_file(path) if key.endswith("SHA256") else str(path)
    oracle["workflow_work_qualification"].update(artifact=str(path), artifact_sha256=sha256_file(path))
    with pytest.raises(ValueError, match="more than"):
        verify_pair(control, oracle)


@pytest.mark.parametrize("leak", ("registration", "gpu"))
def test_preparation_cannot_be_misreported_as_a_registered_experiment(tmp_path, leak):
    control, oracle = pair(tmp_path)
    if leak == "registration":
        oracle["source_registration"] = "/old73/registration.json"
    else:
        oracle["gpu_execution"]["registered"] = True
    with pytest.raises(ValueError, match="unregistered"):
        verify_pair(control, oracle)


@pytest.mark.parametrize("field", ("path_alias", "hash_alias", "qualification_path", "qualification_hash"))
def test_runtime_aliases_and_review_cannot_refer_to_different_artifacts(tmp_path, field):
    control, oracle = pair(tmp_path)
    if field == "path_alias":
        oracle["common_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"] = "/different/oracle"
    elif field == "hash_alias":
        oracle["common_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"] = "0" * 64
    elif field == "qualification_path":
        oracle["workflow_work_qualification"]["artifact"] = "/different/oracle"
    else:
        oracle["workflow_work_qualification"]["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="same artifact"):
        verify_pair(control, oracle)
