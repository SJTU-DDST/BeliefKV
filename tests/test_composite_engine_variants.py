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


@pytest.fixture
def one_page_sources(original_sources):
    by_source, proofs, selected, origins = original_sources
    arms = [Path(name) for name in by_source]
    for index, arm in enumerate(arms):
        path = arm / "server/server_info.json"
        info = json.loads(path.read_bytes())
        info["page_size"] = 1
        write(path, info)
        tokens = 1000 + index
        capacity = {
            "pool_layout": "static_full_only",
            "device_full_tokens": tokens, "device_full_bytes": (tokens + 1) * 64,
            "device_total_bytes": (tokens + 1) * 64,
            "device_mamba_slots": 0, "device_mamba_bytes": 0,
            "host_full_tokens": 2000, "host_full_bytes": 128000, "host_total_bytes": 128000,
            "host_mamba_slots": 0, "host_mamba_bytes": 0,
        }
        write(arm / "server/native_capacity_census.json", {"capacity": capacity})
    return by_source, proofs, selected, origins


@pytest.mark.parametrize("same_engine", (False, True))
def test_explicit_capture_page_projection_preserves_raw_census_and_binds_common_replay(
    one_page_sources, tmp_path, same_engine,
):
    by_source, proofs, selected, origins = one_page_sources
    arms = [Path(name) for name in by_source]
    if same_engine:
        path = arms[1].parent / "capture_plan.json"
        data = json.loads(path.read_bytes())
        data["engine_tree"] = COMMON_TREE
        write(path, data)
    reference = arms[0] / "server/native_capacity_census.json"
    raw_before = {arm: (arm / "server/native_capacity_census.json").read_bytes() for arm in arms}
    with pytest.raises(ValueError, match="same model"):
        prep.serving_artifacts(by_source, proofs, selected, allow_reviewed_native_engine_variants=True)
    artifacts, originals = prep.serving_artifacts(
        by_source, proofs, selected, allow_reviewed_native_engine_variants=True,
        replay_capacity=reference,
    )
    assert artifacts["server/native_capacity_census.json"] == json.loads(raw_before[arms[0]])
    contract = artifacts["serving_source.json"]["capture_capacity_contract"]
    assert contract["source_device_token_slack"] == {str(arms[0]): 0, str(arms[1]): 1}
    assert contract["bytes_per_token"] == 64
    assert contract["matched_natural_native_baseline_available"] is False
    assert originals[str(reference)] == contract["reference_sha256"]
    assert all((arm / "server/native_capacity_census.json").read_bytes() == raw_before[arm] for arm in arms)
    destination = tmp_path / "projected"
    for name, data in artifacts.items():
        write(destination / name, data)
    declaration = {
        "sources": [{"source_capture": name} for name in by_source],
        "original_inputs_sha256": originals,
    }
    verify_serving_engine_sources(destination, declaration)

    path = destination / "serving_source.json"
    changed = deepcopy(artifacts["serving_source.json"])
    changed["capture_capacity_contract"]["source_device_token_slack"][str(arms[1])] = 0
    write(path, changed)
    with pytest.raises(ValueError, match="resource contract"):
        verify_serving_engine_sources(destination, declaration)
    write(path, artifacts["serving_source.json"])
    declaration["original_inputs_sha256"] = {
        name: digest for name, digest in originals.items() if name != str(reference)
    }
    with pytest.raises(ValueError):
        verify_serving_engine_sources(destination, declaration)


@pytest.mark.parametrize("change", (
    "too_small", "two_pages", "wrong_bytes", "wrong_total", "host_tokens", "host_bytes",
    "mamba", "layout", "dtype", "page_size", "target_below_all", "target_above_one",
    "noninteger_tokens", "invalid_target_bytes", "target_mamba",
))
def test_capture_page_projection_rejects_other_resource_changes(one_page_sources, tmp_path, change):
    by_source, proofs, selected, _origins = one_page_sources
    arms = [Path(name) for name in by_source]
    reference = arms[0] / "server/native_capacity_census.json"
    if change in ("dtype", "page_size"):
        path = arms[1] / "server/server_info.json"
        data = json.loads(path.read_bytes())
        data["dtype" if change == "dtype" else "page_size"] = "float16" if change == "dtype" else 2
        write(path, data)
    elif change in ("target_below_all", "target_above_one", "invalid_target_bytes", "target_mamba"):
        data = json.loads(reference.read_bytes())
        target = data["capacity"]
        if change in ("target_below_all", "target_above_one"):
            target["device_full_tokens"] += -1 if change == "target_below_all" else 1
            target["device_full_bytes"] = target["device_total_bytes"] = (target["device_full_tokens"] + 1) * 64
        elif change == "invalid_target_bytes":
            target["device_full_bytes"] += 1
            target["device_total_bytes"] += 1
        else:
            target["device_mamba_slots"] = 1
        reference = write(tmp_path / "other-census.json", data)
    else:
        path = arms[1] / "server/native_capacity_census.json"
        data = json.loads(path.read_bytes())
        capacity = data["capacity"]
        if change in ("too_small", "two_pages"):
            capacity["device_full_tokens"] = 999 if change == "too_small" else 1002
            capacity["device_full_bytes"] = capacity["device_total_bytes"] = (capacity["device_full_tokens"] + 1) * 64
        else:
            name, value = {
                "wrong_bytes": ("device_full_bytes", 1),
                "wrong_total": ("device_total_bytes", 1),
                "host_tokens": ("host_full_tokens", 2001),
                "host_bytes": ("host_full_bytes", 128064),
                "mamba": ("device_mamba_slots", 1),
                "layout": ("pool_layout", "dynamic"),
                "noninteger_tokens": ("device_full_tokens", 1001.0),
            }[change]
            capacity[name] = value
        write(path, data)
    with pytest.raises(ValueError):
        prep.serving_artifacts(
            by_source, proofs, selected, allow_reviewed_native_engine_variants=True,
            replay_capacity=reference,
        )
