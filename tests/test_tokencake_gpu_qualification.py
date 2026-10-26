from copy import deepcopy

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.qualify_tokencake_runtime import (
    QUALIFIED_SOURCES, REQUIRED_EVIDENCE, ROOT, SCENARIOS, validate_report,
)


def report():
    return {
        "status": "tokencake_native_lifecycle_qualified",
        "engine_tree": "native", "native_patch_sha256": "patch",
        "whole_model_or_performance_qualified": False, "performance_accepted": False,
        "source_sha256": {name: sha256_file(ROOT / name) for name in QUALIFIED_SOURCES},
        **dict.fromkeys(REQUIRED_EVIDENCE, True),
        "scenarios": {
            name: {
                "status": "complete", "final_uncommitted_tokens": 64,
                "grants": [] if name == "early_d2h" else [4, 2, 1, 1] if name == "full_upload" else [4],
                "native_ack_tokens": {
                    "d2h": 8,
                    "h2d": 0 if name == "early_d2h" else 8 if name in ("full_upload", "early_h2d") else 4,
                },
                "native_demand_h2d_tokens": 4 if name == "early_h2d" else 0,
                "capacity_counts": {"upload_reservations_consumed": 1},
                "temporal_counts": {"paper_upload_consumers": 1},
            } for name in SCENARIOS
        },
    }


def test_complete_source_bound_lifecycle_evidence_has_no_performance_claim():
    validate_report(report(), engine_tree="native", patch_sha256="patch")


@pytest.mark.parametrize("missing", REQUIRED_EVIDENCE)
def test_missing_actual_lifecycle_property_is_not_qualified(missing):
    result = report()
    del result[missing]
    with pytest.raises(ValueError, match="lifecycle evidence"):
        validate_report(result, engine_tree="native", patch_sha256="patch")


@pytest.mark.parametrize("change", (
    "engine", "patch", "source", "performance", "model", "status", "missing_scenario",
    "failed_scenario", "capacity_leak", "grants", "copied_bytes", "native_demand",
    "reservation_consumer", "temporal_consumer",
))
def test_flags_alone_cannot_cover_failed_bytes_capacity_or_real_consumer(change):
    result = deepcopy(report())
    if change == "engine":
        result["engine_tree"] = "different"
    elif change == "patch":
        result["native_patch_sha256"] = "different"
    elif change == "source":
        result["source_sha256"][QUALIFIED_SOURCES[1]] = "different"
    elif change == "performance":
        result["performance_accepted"] = True
    elif change == "model":
        result["whole_model_or_performance_qualified"] = True
    elif change == "status":
        result["status"] = "cpu_only"
    elif change == "missing_scenario":
        del result["scenarios"]["early_h2d"]
    elif change == "failed_scenario":
        result["scenarios"]["expiry"]["status"] = "failed"
    elif change == "capacity_leak":
        result["scenarios"]["pressure"]["final_uncommitted_tokens"] = 63
    elif change == "grants":
        result["scenarios"]["full_upload"]["grants"] = [8]
    elif change == "copied_bytes":
        result["scenarios"]["full_upload"]["native_ack_tokens"]["h2d"] = 16
    elif change == "native_demand":
        result["scenarios"]["early_h2d"]["native_demand_h2d_tokens"] = 0
    elif change == "reservation_consumer":
        result["scenarios"]["full_upload"]["capacity_counts"]["upload_reservations_consumed"] = 0
    else:
        result["scenarios"]["full_upload"]["temporal_counts"]["paper_upload_consumers"] = 0
    with pytest.raises(ValueError, match="lifecycle evidence"):
        validate_report(result, engine_tree="native", patch_sha256="patch")
