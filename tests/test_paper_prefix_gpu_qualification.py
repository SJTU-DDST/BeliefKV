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
