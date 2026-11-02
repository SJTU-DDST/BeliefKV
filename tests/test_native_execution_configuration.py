from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from beliefkv.experiments import native_execution as execution
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as comparison
from tests.test_causal_comparison import reference

ROOT = Path(__file__).resolve().parents[1]
RELATIVE = Path("configs/execution/qwen3_coder_h200_exact_moe_20261101.json")


@pytest.fixture
def profile(tmp_path):
    checkout = tmp_path / "frozen"
    path = checkout / RELATIVE
    path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / RELATIVE, path)
    shutil.copytree(
        ROOT / "configs/execution/h200_moe_decode",
        checkout / "configs/execution/h200_moe_decode",
    )
    document = json.loads(path.read_bytes())
    common = {
        execution.CONFIG_KEY: str(RELATIVE), execution.HASH_KEY: sha256_file(path),
        "MODEL_PATH": document["model_path"], "MODEL_CACHE_LAYOUT": "full_only",
        "MAX_RUNNING_REQUESTS": "24", "CHUNKED_PREFILL_SIZE": "2048",
        "ENABLE_MIXED_CHUNK": "0", "DEVICE_FULL_TOKENS": "805198",
    }
    return checkout, path, common


def info_for(selected):
    return {
        "model_path": selected["model_path"], "dtype": "bfloat16", "kv_cache_dtype": "bfloat16",
        "attention_backend": "fa3", "max_total_tokens": 805198, "max_running_requests": 24,
        "tp_size": 1, "chunked_prefill_size": 2048, "enable_mixed_chunk": False,
        "page_size": 1,
        "cuda_graph_config": {
            phase: {**fields, "tc_compiler": "eager"} for phase, fields in selected["graph"].items()
        },
    }


def test_absent_profile_does_not_open_files_or_change_historical_configuration(tmp_path):
    assert execution.configuration({}, tmp_path / "absent") is None
    execution.verify_server_info(None, {})
    execution.verify_qualified_configuration(None, {})
    assert execution.observe(None, {}, None, tmp_path) is None
    assert execution.verify_completed_configuration({}) is None
    execution.require_source_binding(None, {}, tmp_path)


@pytest.mark.parametrize("arm", ("full", "native_matched", "thunderagent_acting_decay"))
def test_same_profile_reaches_every_actual_arm_and_rebases_inside_its_checkout(profile, reference, arm):
    checkout, _path, common = profile
    reference["arms"]["full"].update(common, HOST_SPLIT="auto")
    settings, flags, _socket = comparison.environment(
        reference, arm, checkout, checkout / "engine", checkout / arm,
        18998, checkout / "plugin", max_running_requests=24,
    )
    assert settings[execution.MOE_KEY] == str(checkout / "configs/execution/h200_moe_decode")
    graph = json.loads(flags[flags.index("--cuda-graph-config") + 1])
    assert graph == execution.GRAPH
    assert settings["MAX_RUNNING_REQUESTS"] == "24"
    assert settings["HICACHE_SIZE_GB"] == "200"
    assert settings["DEVICE_FULL_TOKENS"] == "805198"


@pytest.mark.parametrize("arm", ("full", "native_matched", "thunderagent_acting_decay"))
def test_prefill_candidate_uses_the_same_parameters_for_all_methods(profile, reference, arm):
    checkout, _, common = profile
    relative = Path("configs/execution/qwen3_coder_h200_exact_prefill_moe_20261101.json")
    path = checkout / relative
    shutil.copyfile(ROOT / relative, path)
    document = json.loads(path.read_bytes())
    moe = document["environment"][execution.MOE_KEY]
    shutil.copytree(ROOT / moe, checkout / moe)
    common.update({
        execution.CONFIG_KEY: str(relative), execution.HASH_KEY: sha256_file(path),
    })
    reference["arms"]["full"].update(common, HOST_SPLIT="auto")
    settings, flags, _ = comparison.environment(
        reference, arm, checkout, checkout / "engine", checkout / arm,
        18998, checkout / "plugin", max_running_requests=24,
    )
    assert settings[execution.MOE_KEY] == str(checkout / moe)
    assert json.loads(flags[flags.index("--cuda-graph-config") + 1]) == execution.GRAPH
    assert settings["MAX_RUNNING_REQUESTS"] == "24"
    assert settings["HICACHE_SIZE_GB"] == "200"
    assert settings["DEVICE_FULL_TOKENS"] == "805198"
    selected = execution.configuration(common, checkout)
    bound = {
        name: sha256_file(ROOT / name) for name in (
            "beliefkv/experiments/native_execution.py", "tests/test_native_execution_configuration.py",
            str(relative), str(Path(moe) / execution.KERNEL_FILE),
        )
    }
    for name in ("beliefkv/experiments/native_execution.py", "tests/test_native_execution_configuration.py"):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    execution.require_source_binding(selected, bound, checkout)
    execution.verify_server_info(selected, info_for(selected))


def test_packaged_prefill_table_preserves_every_untuned_native_lookup():
    original = {
        int(key): value for key, value in json.loads(
            (ROOT / "configs/execution/h200_moe_decode" / execution.KERNEL_FILE).read_bytes()
        ).items()
    }
    candidate = ROOT / "configs/execution/h200_moe_prefill_decode" / execution.KERNEL_FILE
    assert sha256_file(candidate) == "9425a7d46575f32d4b92570d6346034c7559a9c2af749f6d44820fca7cde09b2"
    tuned = {int(key): value for key, value in json.loads(candidate.read_bytes()).items()}
    for size in (*range(1, 8193), 16384, 65536, 262144):
        if not 256 <= size <= 2048:
            assert tuned[min(tuned, key=lambda key: abs(key - size))] == original[
                min(original, key=lambda key: abs(key - size))
            ]


@pytest.mark.parametrize("field,value", [
    ("MAX_RUNNING_REQUESTS", "12"), ("CHUNKED_PREFILL_SIZE", "4096"),
    ("ENABLE_MIXED_CHUNK", "1"), ("DEVICE_FULL_TOKENS", "805199"),
    ("MODEL_PATH", "another-model"), ("MODEL_CACHE_LAYOUT", "hybrid"),
    (execution.HASH_KEY, "bad"),
])
def test_other_resources_or_stale_config_hash_cannot_reuse_profile(profile, field, value):
    checkout, _path, common = profile
    common[field] = value
    with pytest.raises(ValueError, match="configuration"):
        execution.configuration(common, checkout)


@pytest.mark.parametrize("field,value", [
    ("dtype", "float16"), ("attention_backend", "another"),
    ("triton_version", "another"), ("engine_tree", "other"),
    ("device_KV_token_capacity", 805199),
])
def test_resigning_a_changed_manifest_does_not_bypass_the_qualified_profile(profile, field, value):
    checkout, path, common = profile
    document = json.loads(path.read_bytes())
    document[field] = value
    path.write_text(json.dumps(document))
    common[execution.HASH_KEY] = sha256_file(path)
    with pytest.raises(ValueError, match="bounded BF16/FA3"):
        execution.configuration(common, checkout)


@pytest.mark.parametrize("path", ("/outside/config.json", "../config.json"))
def test_external_configuration_paths_cannot_escape_the_frozen_checkout(profile, path):
    checkout, _path, common = profile
    common[execution.CONFIG_KEY] = path
    with pytest.raises(ValueError, match="frozen checkout"):
        execution.configuration(common, checkout)


def test_kernel_tampering_is_rejected_before_startup(profile):
    checkout, _path, common = profile
    selected = execution.configuration(common, checkout)
    Path(selected["kernel_path"]).write_text("{}")
    with pytest.raises(ValueError, match="kernel configuration"):
        execution.configuration(common, checkout)


def test_explicit_profile_cannot_reuse_a_CPU_manifest_omitting_its_sources(profile):
    checkout, _path, common = profile
    selected = execution.configuration(common, checkout)
    with pytest.raises(ValueError, match="CPU-bound"):
        execution.require_source_binding(selected, {}, checkout)


@pytest.mark.parametrize("defect", (None, "capacity", "precision", "graph", "backend"))
def test_actual_server_must_match_common_graph_precision_and_capacity(profile, defect):
    checkout, _path, common = profile
    selected = execution.configuration(common, checkout)
    info = info_for(selected)
    if defect == "capacity":
        info["max_total_tokens"] += 1
    elif defect == "precision":
        info["kv_cache_dtype"] = "fp8"
    elif defect == "graph":
        info["cuda_graph_config"]["decode"]["bs"] = [1, 2, 4]
    elif defect == "backend":
        info["attention_backend"] = "other"
    if defect:
        with pytest.raises(ValueError, match="shared execution profile"):
            execution.verify_server_info(selected, info)
    else:
        execution.verify_server_info(selected, info)


@pytest.mark.parametrize("defect", (
    None, "environment", "log", "missing_qualification", "missing_proof", "hidden_environment",
))
def test_worker_file_loading_and_fresh_gpu_qualification_are_bound(profile, tmp_path, defect):
    checkout, _path, common = profile
    selected = execution.configuration(common, checkout)
    proc, directory = tmp_path / "proc", tmp_path / "run"
    (proc / "42").mkdir(parents=True)
    directory.mkdir()
    value = "unrelated" if defect == "environment" else selected["environment"][execution.MOE_KEY]
    environment = b"UNRELATED=value\0"
    if defect != "hidden_environment":
        environment += (execution.MOE_KEY + "=" + value).encode() + b"\0"
    (proc / "42/environ").write_bytes(environment)
    (directory / "server.log").write_text(
        "" if defect == "log" else "Using MoE kernel config from " + selected["kernel_path"] + ".\n"
    )
    if defect in ("environment", "log"):
        with pytest.raises(ValueError):
            execution.observe(selected, info_for(selected), {"pid": 42, "start_ticks": "10"}, directory, proc_root=proc)
        return
    observed = execution.observe(
        selected, info_for(selected), {"pid": 42, "start_ticks": "10"}, directory, proc_root=proc,
    )
    registration = {
        "native_execution_configuration": selected["identity"],
        "native_execution_observation": observed,
    }
    if defect == "missing_qualification":
        registration = {}
    elif defect == "missing_proof":
        registration = deepcopy(registration)
        del registration["native_execution_observation"]["native_MoE_configuration_log_verified"]
    if defect in ("missing_qualification", "missing_proof"):
        with pytest.raises(ValueError, match="GPU"):
            execution.verify_qualified_configuration(selected, registration)
    else:
        execution.verify_qualified_configuration(selected, registration)
        assert observed["actual_MoE_configuration_directory"].startswith(str(checkout))
        assert observed["GPU_worker_environment_verified"] is (defect != "hidden_environment")
        assert "UNRELATED" not in json.dumps(observed)
        (directory / "server").mkdir()
        (directory / "server/server_info.json").write_text(json.dumps(info_for(selected)))
        (directory / "server/native_execution_configuration.json").write_text(json.dumps(observed))
        proof = execution.verify_completed_configuration({
            "reference_environment": common, "execution_checkout": str(checkout),
            "native_execution_configuration": selected["identity"],
        }, directory)
        assert proof["identity"] == selected["identity"]


@pytest.mark.parametrize("mode", ("legacy", "same", "different"))
def test_pair_review_rejects_different_MoE_profiles_even_with_identical_server_graphs(mode):
    from scripts.review_predictive_thunder_cohort import matching_traffic, TRAFFIC_FIELDS

    common = {name: {"fixed": name} for name in TRAFFIC_FIELDS}
    predictive, thunder = {**common, "order": ["full"]}, {**common, "order": ["thunderagent_acting_decay"]}
    if mode != "legacy":
        predictive["native_execution_configuration"] = {"hash": "same"}
        thunder["native_execution_configuration"] = {"hash": "other" if mode == "different" else "same"}
    if mode == "different":
        with pytest.raises(ValueError, match="shared native execution"):
            matching_traffic(predictive, thunder)
    else:
        matching_traffic(predictive, thunder)
