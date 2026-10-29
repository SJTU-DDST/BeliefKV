from copy import deepcopy

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.qualify_paper_prefix_transfers import (
    QUALIFIED_SOURCES, REQUIRED_EVIDENCE, ROOT, validate_report,
)


def report():
    return {
        "status": "native_prefix_transfers_qualified",
        "native_engine": {"actual_tree": "tree"},
        "native_patch_sha256": "patch",
        "source_sha256": {name: sha256_file(ROOT / name) for name in QUALIFIED_SOURCES},
        **dict.fromkeys(REQUIRED_EVIDENCE, True),
        "native_radix_session": {
            "session_generation": 1, "peer_generation": 2,
            "history_prefix_tokens": 8, "shared_prefix_tokens": 4,
            "final_device_free_tokens": 128, "native_ack_tokens": {"d2h": 12, "h2d": 12},
            "source_and_successor_requests_registered": True,
            "poisoned_pages_restored_with_exact_all_layer_bytes": True,
            "native_session_provenance_preserved": True, "shared_peer_preserved": True,
        },
        "whole_llm_scheduler_or_baseline_qualified": False,
        "performance_accepted": False,
    }


def test_fully_bound_physical_evidence_is_only_an_interface_qualification():
    validate_report(report(), engine_tree="tree", patch_sha256="patch")


@pytest.mark.parametrize("field", REQUIRED_EVIDENCE)
def test_missing_physical_property_cannot_qualify(field):
    observed = report()
    del observed[field]
    with pytest.raises(ValueError, match="GPU evidence"):
        validate_report(observed, engine_tree="tree", patch_sha256="patch")


@pytest.mark.parametrize("field,value", (
    ("history_prefix_tokens", 0),
    ("shared_prefix_tokens", 8),
    ("final_device_free_tokens", 127),
    ("native_ack_tokens", {"d2h": 12, "h2d": 8}),
    ("session_generation", True),
    ("session_generation", 2),
    ("source_and_successor_requests_registered", False),
    ("poisoned_pages_restored_with_exact_all_layer_bytes", False),
    ("native_session_provenance_preserved", False),
    ("shared_peer_preserved", False),
))
def test_session_claim_requires_its_own_native_evidence(field, value):
    observed = report()
    observed["native_radix_session"][field] = value
    with pytest.raises(ValueError, match="GPU evidence"):
        validate_report(observed, engine_tree="tree", patch_sha256="patch")


@pytest.mark.parametrize("change", (
    "tree", "patch", "source", "baseline_claim", "performance_claim", "status",
))
def test_stale_or_overclaimed_qualification_is_rejected(change):
    observed = deepcopy(report())
    if change == "tree":
        observed["native_engine"]["actual_tree"] = "other"
    elif change == "patch":
        observed["native_patch_sha256"] = "other"
    elif change == "source":
        observed["source_sha256"][QUALIFIED_SOURCES[1]] = "other"
    elif change == "baseline_claim":
        observed["whole_llm_scheduler_or_baseline_qualified"] = True
    elif change == "performance_claim":
        observed["performance_accepted"] = True
    else:
        observed["status"] = "cpu_tests_only"
    with pytest.raises(ValueError, match="GPU evidence"):
        validate_report(observed, engine_tree="tree", patch_sha256="patch")
