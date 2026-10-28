from copy import deepcopy

import pytest

from scripts.qualify_demand_prefill_pipeline import SCENARIOS, validate_scenarios


def observations():
    return [{
        "scenario": name, "staged_members": members,
        "real_native_adder_and_request_receipts": True,
        "resident_accessor_completed_before_h2d_ack": True,
        "all_consumer_kv_bytes_equal": True,
        "early_fallback_fence_verified": name == "no_decode_fallback",
        "cancelled_shared_prefix_fence_verified": name == "abort_shared_prefix",
        "model_forward_qualified": False, "performance_accepted": False,
        "cleanup": {
            "final_device_free_tokens": 128, "initial_host_free_tokens": 257,
            "final_host_free_tokens": 257,
        },
        "geometry": {
            "device_tokens": 128, "host_tokens": 257, "layers": 2, "bytes_per_token": 1024,
        },
        "staged_native_ack": {
            "exact_native_ack_matches_verified": True,
            "counts": {
                "matched_staging_episodes": 1, "native_selected_requests": members,
                "native_full_tokens": 32 * members, "native_h2d_bytes": 32 * members * 1024,
            },
        },
    } for name in SCENARIOS for members in [2 if name == "selected_batch" else 1]]


def test_component_qualifier_requires_all_cases_and_stays_separate_from_model_proof():
    validate_scenarios(observations())


@pytest.mark.parametrize("fault", (
    "missing_case", "repeated_case", "wrong_batch", "no_overlap", "bad_kv",
    "leaked_device", "leaked_host", "wrong_geometry", "no_ack", "bad_bytes",
    "fallback_unproven", "cancellation_unproven", "claims_model", "claims_performance",
))
def test_incomplete_component_evidence_does_not_qualify(fault):
    rows = observations()
    if fault == "missing_case":
        rows.pop()
    elif fault == "repeated_case":
        rows[1] = deepcopy(rows[0])
    elif fault == "wrong_batch":
        rows[1]["staged_members"] = 1
    elif fault == "no_overlap":
        rows[0]["resident_accessor_completed_before_h2d_ack"] = False
    elif fault == "bad_kv":
        rows[0]["all_consumer_kv_bytes_equal"] = False
    elif fault == "leaked_device":
        rows[0]["cleanup"]["final_device_free_tokens"] = 127
    elif fault == "leaked_host":
        rows[0]["cleanup"]["final_host_free_tokens"] = 256
    elif fault == "wrong_geometry":
        rows[0]["geometry"]["bytes_per_token"] = 512
    elif fault == "no_ack":
        rows[0]["staged_native_ack"]["exact_native_ack_matches_verified"] = False
    elif fault == "bad_bytes":
        rows[0]["staged_native_ack"]["counts"]["native_h2d_bytes"] -= 1
    elif fault == "fallback_unproven":
        rows[2]["early_fallback_fence_verified"] = False
    elif fault == "cancellation_unproven":
        rows[3]["cancelled_shared_prefix_fence_verified"] = False
    elif fault == "claims_model":
        rows[0]["model_forward_qualified"] = True
    else:
        rows[0]["performance_accepted"] = True
    with pytest.raises(ValueError, match="native demand"):
        validate_scenarios(rows)
