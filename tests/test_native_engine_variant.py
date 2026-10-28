"""Replay must bind the explicit layer engine, not arbitrary native edits."""

from pathlib import Path
import os

import pytest

from beliefkv.experiments.native_engine_variant import (
    COMMON_TREE, LAYER_TREE, GROUPED_LAYER_TREE, SIBLING_CAPACITY_TREE,
    DEMAND_PREFILL_PIPELINE_TREE, engine_patch,
)
from beliefkv.experiments.prepare_ack_engine import prepare_ack_engine_compatibility


def test_explicit_layer_patch_is_immutable():
    root = Path(__file__).resolve().parents[1]
    assert engine_patch(root, LAYER_TREE).name == "sglang-layer-handoff-runtime-20261027.patch"
    assert engine_patch(root, COMMON_TREE).name == "sglang-v0.5.20-beliefkv-staging.patch"
    assert engine_patch(root, GROUPED_LAYER_TREE).name == "sglang-layer-handoff-groups-runtime-20261028.patch"
    assert engine_patch(root, SIBLING_CAPACITY_TREE).name == "sglang-sibling-capacity-runtime-20261028.patch"
    assert engine_patch(root, DEMAND_PREFILL_PIPELINE_TREE).name == (
        "sglang-demand-prefill-pipeline-runtime-20261028.patch"
    )


def test_layer_variant_does_not_accept_a_changed_patch(tmp_path):
    (tmp_path / "patches").mkdir()
    (tmp_path / "patches/sglang-layer-handoff-runtime-20261027.patch").write_text("changed")
    with pytest.raises(ValueError, match="reviewed"):
        engine_patch(tmp_path, LAYER_TREE)


def test_actual_layer_tree_binds_common_engine_compatibility():
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    source = {
        "engine_tree": "6437d7deeacc533f0d795ba166dfd5256b3f44b2",
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }
    result = prepare_ack_engine_compatibility(engine, source, candidate_tree=LAYER_TREE)
    assert result["common_engine_tree"] == COMMON_TREE
    assert result["common_engine_compatibility"]["native_method_projection_matches"]
    assert result["requires_fresh_gpu_components_model_and_position_smoke"]
    assert not result["layer_handoff_default_enabled"]
    altered = {**source, "environment": {**source["environment"], "PREPARE_HOST": "1"}}
    with pytest.raises(ValueError, match="native source"):
        prepare_ack_engine_compatibility(engine, altered, candidate_tree=LAYER_TREE)


def test_grouped_tree_is_explicit_and_still_requires_real_gpu_and_model_proofs():
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    source = {
        "engine_tree": "6437d7deeacc533f0d795ba166dfd5256b3f44b2",
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }
    result = prepare_ack_engine_compatibility(engine, source, candidate_tree=GROUPED_LAYER_TREE)
    assert result["policy"] == "explicit_grouped_layer_handoff_engine_variant"
    assert result["requires_fresh_gpu_components_model_and_position_smoke"] is True
    assert result["layer_handoff_default_enabled"] is False


def test_capacity_tree_does_not_inherit_previous_grouped_gpu_qualification():
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    source = {
        "engine_tree": "6437d7deeacc533f0d795ba166dfd5256b3f44b2",
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }
    result = prepare_ack_engine_compatibility(engine, source, candidate_tree=SIBLING_CAPACITY_TREE)
    assert result["policy"] == "explicit_sibling_capacity_engine_variant"
    assert result["candidate_engine_tree"] == SIBLING_CAPACITY_TREE
    assert result["requires_fresh_gpu_components_model_and_position_smoke"]
    assert not result["layer_handoff_default_enabled"]


def test_demand_pipeline_engine_requires_fresh_proofs_and_its_default_off_projection():
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    source = {
        "engine_tree": "6437d7deeacc533f0d795ba166dfd5256b3f44b2",
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }
    result = prepare_ack_engine_compatibility(
        engine, source, candidate_tree=DEMAND_PREFILL_PIPELINE_TREE,
    )
    assert result["policy"] == "explicit_native_demand_pipeline_engine_variant"
    assert result["requires_fresh_gpu_components_model_and_position_smoke"]
    assert not result["demand_prefill_pipeline_default_enabled"]


def test_demand_pipeline_patch_cannot_be_replaced_by_arbitrary_native_edits(tmp_path):
    (tmp_path / "patches").mkdir()
    (tmp_path / "patches/sglang-demand-prefill-pipeline-runtime-20261028.patch").write_text("changed")
    with pytest.raises(ValueError, match="native demand pipeline engine patch"):
        engine_patch(tmp_path, DEMAND_PREFILL_PIPELINE_TREE)
