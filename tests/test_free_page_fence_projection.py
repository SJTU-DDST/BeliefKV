import os
from pathlib import Path
import subprocess

import pytest

from beliefkv.experiments.free_page_h2d_fence import (
    ALLOCATOR, CONTROLLER, FILES, SCHEDULER, verify_free_page_fence_projection,
)
from beliefkv.experiments.internal_comparison import arm_environment
from scripts.run_causal_comparison import validate_free_page_fence


@pytest.fixture
def sources():
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the free-page fence candidate engine")
    checkout = Path(checkout)
    reference = "6437d7deeacc533f0d795ba166dfd5256b3f44b2"
    before = {
        name: subprocess.check_output(["git", "-C", str(checkout), "show", reference + ":" + name], text=True)
        for name in FILES
    }
    candidate = {name: (checkout / name).read_text() for name in FILES}
    if "_beliefkv_demand_pipeline" in candidate[SCHEDULER]:
        from beliefkv.experiments.demand_prefill_pipeline_engine import project_disabled_scheduler
        from beliefkv.experiments.native_engine_variant import SIBLING_CAPACITY_TREE

        reviewed = subprocess.check_output(
            ["git", "-C", str(checkout), "show", SIBLING_CAPACITY_TREE + ":" + SCHEDULER],
            text=True,
        )
        candidate[SCHEDULER] = project_disabled_scheduler(reviewed, candidate[SCHEDULER])
    return before, candidate


def test_reviewed_default_off_changes_project_exactly_to_the_captured_engine(sources):
    result = verify_free_page_fence_projection(*sources)
    assert result["native_method_projection_matches"]
    assert not result["default_enabled"]
    assert result["requires_no_free_since_boundary_at_allocation"]
    assert result["unproven_or_different_boundary_batch_uses_original_fence"]
    assert result["same_proof_applies_to_native_demand"]


@pytest.mark.parametrize("fault", (
    "no_free_generation", "default_on", "wrong_stream", "extra_pool",
    "no_overlap_guard", "no_binding", "unrelated", "extra_file",
))
def test_unreviewed_or_unsafe_engine_changes_cannot_reuse_projection(sources, fault):
    original, candidate = sources
    candidate = dict(candidate)
    if fault == "no_free_generation":
        candidate[ALLOCATOR] = candidate[ALLOCATOR].replace("self.beliefkv_free_generation += 1", "pass")
    elif fault == "default_on":
        candidate[CONTROLLER] = candidate[CONTROLLER].replace(
            'os.environ.get("BELIEFKV_FREE_PAGE_H2D_FENCE", "0")', 'os.environ.get("BELIEFKV_FREE_PAGE_H2D_FENCE", "1")',
        )
    elif fault == "wrong_stream":
        candidate[CONTROLLER] = candidate[CONTROLLER].replace(
            "event.record(stream=self.load_fence_stream)", "event.record(stream=None)",
        )
    elif fault == "extra_pool":
        candidate[CONTROLLER] = candidate[CONTROLLER].replace("not child.pool_transfers", "True")
    elif fault == "no_overlap_guard":
        candidate[SCHEDULER] = candidate[SCHEDULER].replace(
            "and self.enable_overlap and self.spec_algorithm.is_none()", "and self.spec_algorithm.is_none()",
        )
    elif fault == "no_binding":
        candidate[CONTROLLER] = candidate[CONTROLLER].replace("self._beliefkv_bind_free_page_fence(op)", "pass")
    elif fault == "unrelated":
        candidate[ALLOCATOR] += "\nunreviewed = True\n"
    else:
        candidate["extra.py"] = "unrelated = True"
    with pytest.raises(ValueError, match="free-page|outside"):
        verify_free_page_fence_projection(original, candidate)


@pytest.mark.parametrize("mode", ("full", "native_matched", "join_priority_only", "scheduler_only", "kv_only"))
def test_common_engine_flag_is_explicit_and_preserved_across_policy_controls(mode):
    settings = arm_environment({"FREE_PAGE_H2D_FENCE": "1"}, mode)
    assert settings["FREE_PAGE_H2D_FENCE"] == settings["BELIEFKV_FREE_PAGE_H2D_FENCE"] == "1"
    assert arm_environment({}, mode)["BELIEFKV_FREE_PAGE_H2D_FENCE"] == "0"


@pytest.mark.parametrize("value", ("2", "true", 1, None))
def test_invalid_flag_values_are_rejected(value):
    with pytest.raises(ValueError, match="free-page"):
        arm_environment({"FREE_PAGE_H2D_FENCE": value}, "full")


@pytest.mark.parametrize("fault", (
    None, "requested", "enabled", "supported", "no_boundary", "bad_counter", "disabled_execution",
))
def test_comparison_requires_observed_enablement_and_real_boundary_counts(fault):
    plan = {"reference_environment": {"FREE_PAGE_H2D_FENCE": "1"}}
    observed = {
        "requested": True, "enabled": True, "supported": True,
        "counts": {"free_boundary_events": 1, "free_event_batches": 0, "forward_stream_batches": 2},
    }
    if fault in ("requested", "enabled", "supported"):
        observed[fault] = False
    elif fault == "no_boundary":
        observed["counts"]["free_boundary_events"] = 0
    elif fault == "bad_counter":
        observed["counts"]["free_event_batches"] = True
    elif fault == "disabled_execution":
        plan["reference_environment"]["FREE_PAGE_H2D_FENCE"] = "0"
        observed["requested"] = observed["enabled"] = False
    if fault:
        with pytest.raises(ValueError, match="free-page"):
            validate_free_page_fence(plan, {"native_free_page_h2d_fence": observed})
    else:
        validate_free_page_fence(plan, {"native_free_page_h2d_fence": observed})
        validate_free_page_fence({}, {})
