from copy import deepcopy

import pytest

from beliefkv.experiments import native_execution
from scripts.audit_common_execution_window import matching_actual_engines, matching_conditions, reduction
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS


def pair():
    reference = {
        "order": ["full"],
        **{key: None for key in TRAFFIC_FIELDS},
        "reference_environment": {"TOOL_PREFETCH": "0", "MAX_RUNNING_REQUESTS": "24"},
    }
    candidate = deepcopy(reference)
    candidate["reference_environment"].update({
        native_execution.CONFIG_KEY: "configs/execution/common.json",
        native_execution.HASH_KEY: "pinned",
    })
    candidate["native_execution_configuration"] = {"configuration_sha256": "pinned"}
    return reference, candidate


def test_same_policy_and_traffic_allow_only_explicit_common_execution_change():
    matching_conditions(*pair())


@pytest.mark.parametrize("key", [
    "source_arm", "workflow_arrivals", "tool_timing_overlay", "sampling_mode",
])
def test_changed_traffic_cannot_be_called_an_engine_cost_comparison(key):
    reference, candidate = pair()
    candidate[key] = "changed"
    with pytest.raises(ValueError, match="changed traffic"):
        matching_conditions(reference, candidate)


def test_agent_policy_change_is_not_attributed_to_engine_configuration():
    reference, candidate = pair()
    candidate["reference_environment"]["TOOL_PREFETCH"] = "1"
    with pytest.raises(ValueError, match="agent policy"):
        matching_conditions(reference, candidate)


def test_different_methods_cannot_be_used_as_before_after_engine_evidence():
    reference, candidate = pair()
    candidate["order"] = ["thunderagent_acting_decay"]
    with pytest.raises(ValueError, match="same policy"):
        matching_conditions(reference, candidate)


def test_new_explicit_engine_metadata_requires_actual_engine_verification_separately():
    reference, candidate = pair()
    reference["source_server_parameters"] = {"chunked_prefill_size": 2048}
    candidate["source_server_parameters"] = {
        "chunked_prefill_size": 2048, "attention_backend": "fa3", "tp_size": 1,
        "max_total_tokens": 805198, "cuda_graph_config": {"decode": "exact"},
    }
    matching_conditions(reference, candidate)
    candidate["source_server_parameters"]["chunked_prefill_size"] = 1024
    with pytest.raises(ValueError, match="source server"):
        matching_conditions(reference, candidate)


@pytest.mark.parametrize("change", ["kv_precision", "chunk_size", "host_capacity", "prefill_capture"])
def test_actual_engine_and_capacity_mismatches_are_still_rejected(change):
    before = {
        "kv_cache_dtype": "bfloat16", "chunked_prefill_size": 2048,
        "cuda_graph_config": {"prefill": {"max_bs": 2048}},
    }
    after, capacity = deepcopy(before), {"device": 805198, "host": 2034506}
    other_capacity = dict(capacity)
    if change == "kv_precision":
        after["kv_cache_dtype"] = "fp8"
    elif change == "chunk_size":
        after["chunked_prefill_size"] = 1024
    elif change == "host_capacity":
        other_capacity["host"] += 1
    else:
        after["cuda_graph_config"]["prefill"]["max_bs"] = 1024
    with pytest.raises(ValueError, match="actual"):
        matching_actual_engines(before, after, capacity, other_capacity)


def test_zero_reference_cost_has_no_defined_relative_gain():
    assert reduction(10., 8.) == pytest.approx(20.)
    assert reduction(0., 1.) is None
    assert reduction(None, 1.) is None
