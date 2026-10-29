"""Do not promote the old donor-larger-than-consumer proof to free-extent proof."""

import json
import os
from pathlib import Path

import pytest

from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
from beliefkv.experiments.prepare_ack_engine import prepare_ack_engine_compatibility
from scripts.qualify_layer_handoff_model import (
    validate_free_extent_components, validate_free_extent_model,
)


def component_fixture():
    scenes = []
    for delayed in (False, True):
        scenes.append({
            "donor_tokens": 24, "reused_tokens": 24, "restored_tokens": 32,
            "free_extent_tokens": 8, "reserved_tail_tokens": 0,
            "delayed_final_donor_layer": delayed, "initial_free_tokens": 12,
            "post_submit_free_tokens": 4, "final_free_tokens": 4,
            "all_donor_host_and_consumer_device_kv_verified": True,
            "protected_resident_kv_unchanged": True,
            "old_donor_reader_completed_before_overwrite": True,
            "first_consumer_layer_read_before_final_donor_ack": delayed,
            "actual_native_pool_layer_counter_and_accessors_used": True,
            "donor_early_return_blocked_until_backup_ack": True,
        })
    return {"free_extent_scenarios": scenes}


@pytest.mark.parametrize("change", (
    "missing", "only_one", "no_larger_restore", "free_credit", "reserve_spent",
    "missing_byte_proof", "missing_old_reader", "no_early_consumer", "integer_delay",
))
def test_model_entry_refuses_incomplete_or_old_component_proofs(change):
    report = component_fixture()
    scenes = report["free_extent_scenarios"]
    if change == "missing":
        report.clear()
    elif change == "only_one":
        scenes.pop()
    elif change == "no_larger_restore":
        scenes[0].update(reused_tokens=32, donor_tokens=32, free_extent_tokens=0)
    elif change == "free_credit":
        scenes[0]["post_submit_free_tokens"] = 12
    elif change == "reserve_spent":
        scenes[0]["final_free_tokens"] = 0
    elif change == "missing_byte_proof":
        scenes[0]["all_donor_host_and_consumer_device_kv_verified"] = False
    elif change == "missing_old_reader":
        scenes[0]["old_donor_reader_completed_before_overwrite"] = False
    elif change == "no_early_consumer":
        scenes[1]["first_consumer_layer_read_before_final_donor_ack"] = False
    else:
        scenes[1]["delayed_final_donor_layer"] = 1
    with pytest.raises(ValueError):
        validate_free_extent_components(
            {"common_environment": {"BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "1"}}, report,
        )


def test_current_components_are_required_only_for_the_opt_in_extension():
    assert validate_free_extent_components({"common_environment": {}}, {}) is False
    assert validate_free_extent_components(
        {"common_environment": {"BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "1"}},
        component_fixture(),
    ) is True


@pytest.mark.parametrize("field,value", (
    ("free_extent_tokens", 0), ("donor_tokens", 33),
    ("restored_tokens", 31), ("reserved_tail_tokens", 1), ("reused_tokens", True),
))
def test_unforced_model_must_actually_consume_the_new_geometry(field, value):
    handoff = component_fixture()["free_extent_scenarios"][0]
    validate_free_extent_model(handoff)
    handoff[field] = value
    with pytest.raises(ValueError, match="actual model"):
        validate_free_extent_model(handoff)


def test_new_engine_keeps_default_off_and_requires_all_fresh_gpu_proofs():
    source = {
        "engine_tree": "6437d7deeacc533f0d795ba166dfd5256b3f44b2",
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }
    result = prepare_ack_engine_compatibility(
        Path(os.environ["SGLANG_SOURCE_CHECKOUT"]), source, candidate_tree=FREE_EXTENT_TREE,
    )
    assert result["policy"] == "explicit_layer_free_extent_engine_variant"
    assert result["requires_fresh_gpu_components_model_and_position_smoke"] is True
    assert result["free_extent_default_enabled"] is False
    assert result["demand_prefill_pipeline_default_enabled"] is False
    assert result["layer_handoff_default_enabled"] is False
    root = Path(__file__).resolve().parents[1]
    assert engine_patch(root, FREE_EXTENT_TREE).name == "sglang-layer-free-extent-runtime-20261029.patch"


def test_new_engine_rejects_changed_patch(tmp_path):
    patch = tmp_path / "patches/sglang-layer-free-extent-runtime-20261029.patch"
    patch.parent.mkdir()
    patch.write_text("changed")
    with pytest.raises(ValueError, match="free extent engine patch"):
        engine_patch(tmp_path, FREE_EXTENT_TREE)


def test_prepared_candidate_changes_only_handoff_implementation_and_keeps_arrivals():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_demand_prefill_pipeline_20261028.json").read_bytes())
    candidate = json.loads((root / "experiments/reports/layer_free_extent_20261029/full_warm_start.json").read_bytes())
    control = json.loads((root / "experiments/reports/warm_start_thunder_20261029/thunderagent.json").read_bytes())
    before, after = base["common_environment"], candidate["common_environment"]
    assert {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)} == {
        "SGLANG_PATCH_FLAVOR", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT",
    }
    assert candidate["workflow_arrivals"] == control["workflow_arrivals"]
    assert candidate["tool_timing_overlay"] == control["tool_timing_overlay"]
    assert candidate["order"] == ["full"]
    assert after["TOOL_PREFETCH"] == after["EXECUTION_FRONTIER_RESTORE"] == "0"
    assert candidate["gpu_execution"]["registered"] is False
