from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.composite_workload import SERVING_FIELDS, verify_serving_engine_sources
from beliefkv.experiments.native_engine_variant import (
    COMMON_TREE, FREE_EXTENT_TREE, LAYER_TREE, reviewed_capture_composition,
)
from scripts import prepare_composite_workload as prep
from scripts.register_composite_source import replay_origin


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def original_sources(tmp_path):
    by_source, proofs, selected, plans = {}, [], [], {}
    for name, tree in (("left", COMMON_TREE), ("right", FREE_EXTENT_TREE)):
        arm = tmp_path / name / "native_capture"
        info = {
            **dict.fromkeys(SERVING_FIELDS, None), "model_path": "/same/model",
            "enable_beliefkv_admission": False, "dtype": "bfloat16",
        }
        capacity = {
            "pool_layout": "static_full_only", "device_full_tokens": 1000,
            "host_full_tokens": 2000, "device_mamba_slots": 0, "host_mamba_slots": 0,
        }
        write(arm / "server/server_info.json", info)
        write(arm / "server/native_capacity_census.json", {"capacity": capacity})
        plan = {"engine_tree": tree, "hold_file": "/same/hold", "environment": {}}
        write(arm.parent / "capture_plan.json", plan)
        client = write(arm / "client_1/manifest.json", {})
        task = {
            "instance_id": "task-" + name,
            "dataset_provenance": {"original_scenario": "scenario-" + name},
        }
        workload = write(tmp_path / (name + "-workload.json"), {
            "archive_sha256": "same-original-archive", "workloads": [task],
        })
        row = {
            "source_capture": str(arm), "project": "locobench/" + name,
            "task_id": task["instance_id"], "protocol_sha256": "protocol-" + name,
        }
        by_source[str(arm)] = [row]
        proofs.append({"workload": str(workload), "client_manifest": str(client)})
        selected.append(row)
        plans[arm] = plan
    return by_source, proofs, selected, plans


def test_reviewed_engine_mix_is_explicit_and_retains_one_common_replay_engine(original_sources):
    by_source, proofs, selected, origins = original_sources
    with pytest.raises(ValueError, match="same model"):
        prep.serving_artifacts(by_source, proofs, selected)
    artifacts, inputs = prep.serving_artifacts(
        by_source, proofs, selected, allow_reviewed_native_engine_variants=True,
    )
    serving = artifacts["serving_source.json"]
    assert serving["engine_tree"] == FREE_EXTENT_TREE
    assert serving["engine_variant_compatibility"] == reviewed_capture_composition(
        (COMMON_TREE, FREE_EXTENT_TREE),
    )
    assert replay_origin(origins, serving)["engine_tree"] == FREE_EXTENT_TREE
    assert len(artifacts["workload_manifest.json"]["workloads"]) == 2
    assert len([name for name in inputs if name.endswith("/capture_plan.json")]) == 2


@pytest.mark.parametrize("change", (
    "unknown_tree", "other_reviewed_tree", "different_dtype", "different_capacity",
    "admission_enabled", "mamba_pool",
))
def test_explicit_mix_still_rejects_unreviewed_engines_and_changed_serving_conditions(
    original_sources, change,
):
    by_source, proofs, selected, _origins = original_sources
    arms = [Path(name) for name in by_source]
    if change in ("unknown_tree", "other_reviewed_tree"):
        path = arms[1].parent / "capture_plan.json"
        data = json.loads(path.read_bytes())
        data["engine_tree"] = "unknown" if change == "unknown_tree" else LAYER_TREE
        write(path, data)
    elif change == "different_dtype":
        path = arms[1] / "server/server_info.json"
        data = json.loads(path.read_bytes())
        data["dtype"] = "float16"
        write(path, data)
    elif change == "different_capacity":
        path = arms[1] / "server/native_capacity_census.json"
        data = json.loads(path.read_bytes())
        data["capacity"]["device_full_tokens"] += 1
        write(path, data)
    else:
        for arm in arms:
            path = arm / (
                "server/server_info.json" if change == "admission_enabled"
                else "server/native_capacity_census.json"
            )
            data = json.loads(path.read_bytes())
            if change == "admission_enabled":
                data["enable_beliefkv_admission"] = True
            else:
                data["capacity"]["device_mamba_slots"] = 1
            write(path, data)
    with pytest.raises(ValueError):
        prep.serving_artifacts(
            by_source, proofs, selected, allow_reviewed_native_engine_variants=True,
        )


def test_materialized_mix_rechecks_original_resource_files_and_engine_contract(
    original_sources, tmp_path,
):
    by_source, proofs, selected, _origins = original_sources
    artifacts, originals = prep.serving_artifacts(
        by_source, proofs, selected, allow_reviewed_native_engine_variants=True,
    )
    destination = tmp_path / "composite"
    for name, data in artifacts.items():
        write(destination / name, data)
    declaration = {
        "sources": [{"source_capture": name} for name in by_source],
        "original_inputs_sha256": originals,
    }
    verify_serving_engine_sources(destination, declaration)
    path = next(Path(name) for name in originals if name.endswith("/server_info.json"))
    data = json.loads(path.read_bytes())
    data["dtype"] = "float16"
    write(path, data)
    with pytest.raises(ValueError, match="binding changed"):
        verify_serving_engine_sources(destination, declaration)


@pytest.mark.parametrize("change", ("missing_declaration", "wrong_target", "unknown_target", "unknown_source"))
def test_registration_cannot_select_an_engine_outside_the_declared_common_target(
    original_sources, change,
):
    by_source, proofs, selected, origins = original_sources
    artifacts, _inputs = prep.serving_artifacts(
        by_source, proofs, selected, allow_reviewed_native_engine_variants=True,
    )
    serving = deepcopy(artifacts["serving_source.json"])
    if change == "missing_declaration":
        serving.pop("engine_variant_compatibility")
    elif change == "wrong_target":
        serving["engine_tree"] = COMMON_TREE
    elif change == "unknown_target":
        serving["engine_tree"] = "unknown"
    else:
        next(iter(origins.values()))["engine_tree"] = "unknown"
    with pytest.raises(ValueError):
        replay_origin(origins, serving)


def test_legacy_single_engine_keeps_its_existing_identity_without_a_mixed_contract(original_sources):
    by_source, proofs, selected, origins = original_sources
    arm = next(iter(by_source))
    artifacts, _inputs = prep.serving_artifacts({arm: by_source[arm]}, proofs[:1], selected[:1])
    serving = artifacts["serving_source.json"]
    assert serving["engine_tree"] == COMMON_TREE
    assert "engine_variant_compatibility" not in serving
    assert replay_origin({Path(arm): origins[Path(arm)]}, serving)["engine_tree"] == COMMON_TREE
