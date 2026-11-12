from copy import deepcopy

import pytest

from scripts.prepare_matched_background_baselines import (
    LABELS,
    configuration,
    validate_configuration,
)


@pytest.fixture
def full_configuration():
    return {
        "order": ["full"],
        "source_registration": "/source/registration.json",
        "candidate_source_arm": "/source/complete",
        "workflow_arrivals": {"path": "/traffic/arrivals.json", "sha256": "a" * 64},
        "private_analysis_cohort": {"path": "/traffic/cohort.json", "sha256": "b" * 64},
        "tool_timing_overlay": {"path": "/tools/timing.json", "sha256": "c" * 64},
        "stream_response_metrics": True,
        "workflow_work_qualification": {"model": "/models/private.json"},
        "oracle_information": {"source": "/oracle/future.json"},
        "source_CPU_binding": {"source": "/old/binding.json"},
        "common_environment": {
            "ROOT_COUNT": "100", "HICACHE_SIZE_GB": "400",
            "MODEL_PATH": "/models/qwen", "MODEL_CACHE_LAYOUT": "full_only",
            "MAX_RUNNING_REQUESTS": "24", "CHUNKED_PREFILL_SIZE": "2048",
            "ENABLE_MIXED_CHUNK": "0", "DEVICE_FULL_TOKENS": "805198",
            "HOST_NUMA_NODE": "1", "HOST_NUMA_MEMORY_POLICY": "interleave",
            "HOST_NUMA_MEMORY_NODES": "0,1", "HOST_NUMA_PLACEMENT_CHECK": "diagnostic",
            "AGENT_ADMISSION": "thunderagent_reference",
            "AGENT_FREE_CONTINUATION_ADMISSION": "1",
            "AGENT_FREE_DISPATCH_INTERVAL_MS": "2000",
            "AGENT_CONTROL_INTERVAL_MS": "2000",
            "WORKFLOW_SIZE_RESUME": "1", "WORKFLOW_COMBINATION_ADMISSION": "1",
            "WORKFLOW_WORK_SOURCE": "oracle", "WORKFLOW_WORK_MODEL": "/oracle/future.json",
            "WORKFLOW_WORK_MODEL_SHA256": "d" * 64,
            "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD": "1",
            "BELIEFKV_ENABLE_PREPARE_HOST": "1", "BELIEFKV_ENABLE_TOOL_PREFETCH": "0",
        },
    }


@pytest.mark.parametrize("label", LABELS)
def test_matching_baseline_preserves_traffic_and_resources_without_mutating_full(
    full_configuration, label,
):
    before = deepcopy(full_configuration)
    result = configuration(full_configuration, label)
    common = result["common_environment"]
    assert full_configuration == before
    assert result["workflow_arrivals"] == before["workflow_arrivals"]
    assert result["private_analysis_cohort"] == before["private_analysis_cohort"]
    assert common["ROOT_COUNT"] == "100" and common["HICACHE_SIZE_GB"] == "400"
    assert common["HOST_NUMA_PLACEMENT_CHECK"] == "diagnostic"
    assert common["AGENT_CONTROL_INTERVAL_MS"] == "5000"
    assert common["BELIEFKV_WORKFLOW_WORK_SOURCE"] == "learned"
    assert common["BELIEFKV_WORKFLOW_WORK_MODEL"] == ""
    assert common["BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD"] == "0"
    assert common["BELIEFKV_ENABLE_PREPARE_HOST"] == "0"
    assert result["gpu_execution"]["registered"] is False
    if label == "thunderagent_acting_decay":
        assert common["BELIEFKV_PAPER_BASELINE"] == (
            "thunderagent_public_single_backend_acting_decay"
        )
        assert result["comparison_contract"]["paper_program_identity"] == "invocation_id"
    else:
        assert common["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "native_matched"
        assert result["native_reference"]["repeat_full_native_replay"] is True
    result["private_analysis_cohort"]["path"] = "/different"
    assert full_configuration == before


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("corruption", (
    "arrival", "cohort", "host", "numa", "future", "prediction", "cadence", "registration",
))
def test_invalid_matching_contract_is_rejected(full_configuration, label, corruption):
    result = configuration(full_configuration, label)
    if corruption == "arrival":
        result["workflow_arrivals"]["path"] = "/different/arrivals.json"
    elif corruption == "cohort":
        result["private_analysis_cohort"]["path"] = "/different/cohort.json"
    elif corruption == "host":
        result["common_environment"]["HICACHE_SIZE_GB"] = "200"
    elif corruption == "numa":
        result["common_environment"]["HOST_NUMA_PLACEMENT_CHECK"] = "strict"
    elif corruption == "future":
        result["oracle_information"] = {"source": "/oracle/future.json"}
    elif corruption == "prediction":
        result["common_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"] = "/oracle/future.json"
    elif corruption == "cadence":
        result["common_environment"]["AGENT_CONTROL_INTERVAL_MS"] = "2000"
    else:
        result["gpu_execution"]["registered"] = True
    with pytest.raises(ValueError):
        validate_configuration(full_configuration, result, label)


def test_native_requires_explicit_same_work_replay_declaration(full_configuration):
    result = configuration(full_configuration, "native_matched")
    result["native_reference"]["repeat_full_native_replay"] = False
    with pytest.raises(ValueError, match="replay/termination"):
        validate_configuration(full_configuration, result, "native_matched")


def test_only_requested_system_baselines_can_be_prepared(full_configuration):
    with pytest.raises(ValueError, match="acting-decay"):
        configuration(full_configuration, "thunderagent")
