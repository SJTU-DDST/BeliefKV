import ast
import os
from pathlib import Path
import subprocess

import pytest

from beliefkv.experiments import agentix_engine as port
from beliefkv.experiments.native_engine_variant import (
    AGENTIX_PATCH_SHA256, AGENTIX_TREE, COMMON_TREE, engine_patch,
)
from beliefkv.experiments.prepare_ack_engine import prepare_ack_engine_compatibility
from beliefkv.experiments.source_provenance import sha256_file


@pytest.fixture
def sources():
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    return engine, {
        name: subprocess.check_output(
            ["git", "show", COMMON_TREE + ":" + name], cwd=engine, text=True,
        )
        for name in (port.SCHEDULER, port.BATCH)
    }


def test_agentix_optional_hooks_project_exactly_to_common_compute_and_transfers(sources):
    engine, original = sources
    for name, projection in (
        (port.SCHEDULER, port.project_scheduler), (port.BATCH, port.project_batch),
    ):
        projected = projection(original[name], (engine / name).read_text())
        assert ast.dump(ast.parse(projected)) == ast.dump(ast.parse(original[name]))
    evidence = port.verify_native_hooks(engine)
    assert evidence["constructor_binds_scheduler_after_cache"]
    assert evidence["uncheckpointed_retraction_checked_before_release"]
    assert not evidence["native_compute_copy_cache_and_sampler_changed"]
    assert not evidence["native_victim_order_changed"]


@pytest.mark.parametrize("fault", ("missing_attach", "wrong_order", "extra_change", "missing_abort_guard"))
def test_projection_rejects_unbound_or_broadened_native_hooks(sources, fault):
    engine, original = sources
    name = port.BATCH if fault == "missing_abort_guard" else port.SCHEDULER
    candidate = (engine / name).read_text()
    if fault == "missing_attach":
        candidate = candidate.replace("        runtime.attach_native_scheduler(self)\n", "")
    elif fault == "wrong_order":
        candidate = candidate.replace(
            "        runtime.attach_native_cache(self.tree_cache)\n"
            "        runtime.attach_native_scheduler(self)\n",
            "        runtime.attach_native_scheduler(self)\n"
            "        runtime.attach_native_cache(self.tree_cache)\n",
        )
    elif fault == "missing_abort_guard":
        candidate = candidate.replace(
            '            if before_release is not None:\n'
            '                before_release(last_req, reason="decode_oom_abort")\n', "",
        )
    else:
        candidate += "\nUNREVIEWED_COMPUTE_CHANGE = True\n"
    projection = port.project_batch if name == port.BATCH else port.project_scheduler
    with pytest.raises(ValueError):
        projection(original[name], candidate)


def test_agentix_patch_is_pinned_and_does_not_enable_the_formal_baseline():
    from beliefkv.experiments.paper_baselines import RUNTIME_ARMS

    root = Path(__file__).resolve().parents[1]
    patch = engine_patch(root, AGENTIX_TREE)
    assert patch.name == "sglang-agentix-common-runtime-20261029.patch"
    assert sha256_file(patch) == AGENTIX_PATCH_SHA256
    assert "agentix_atlas" not in RUNTIME_ARMS
    with pytest.raises(ValueError, match="reviewed explicit"):
        port.verify_qualification_engine(root, COMMON_TREE)


def test_agentix_common_variant_requires_its_own_real_gpu_and_model_proofs(sources):
    engine, _ = sources
    result = prepare_ack_engine_compatibility(engine, {
        "engine_tree": "6437d7deeacc533f0d795ba166dfd5256b3f44b2",
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }, candidate_tree=AGENTIX_TREE)
    assert result["policy"] == "explicit_agentix_common_native_hook_variant"
    assert result["common_engine_tree"] == COMMON_TREE
    assert result["common_engine_compatibility"]["native_method_projection_matches"]
    assert result["requires_fresh_gpu_components_model_and_position_smoke"]
    assert not result["native_scheduler_bindings"]["native_compute_copy_cache_and_sampler_changed"]
