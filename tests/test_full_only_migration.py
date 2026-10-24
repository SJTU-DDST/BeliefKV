from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.sampling_profiles import sampling_parameters
from beliefkv.runtime.sglang_v0520_observer import (
    observe_static_full_mamba, observe_static_full_mamba_headroom,
    observe_static_full_mamba_host_usage,
)
from beliefkv.runtime.sglang_v0520_physical import (
    capture_action_local_shadow, has_host_backed_missing_copy,
    inspect_session_h2d_opportunity, missing_prepare_full_prefix_tokens, shadow_backup_steps,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
from scripts import run_causal_comparison as controls
from scripts import run_causal_source_capture as capture
from tests.test_sglang_v0520_observer import ComponentType
from tests.test_sglang_v0520_physical import prepare_prefix_fixture
from tests.test_causal_source_capture import reference, disjoint_manifest


def full_only_fixture(*, backed=True, input_tokens=4):
    cache, anchors, nodes = prepare_prefix_fixture(backed=backed, input_tokens=input_tokens)
    cache.tree_components = (ComponentType.FULL,)
    cache.token_to_kv_pool_allocator._kvcache = cache.token_to_kv_pool_allocator._kvcache.full_kv_pool
    cache.host_pool_group.entry_map.pop("mamba")
    cache.req_to_token_pool = NS(size=24, available_size=lambda: 24)
    for node in nodes.values():
        state = node.component_data[2]
        state.value = state.host_value = state.session_ids = None
        state.session_ref = 0
    anchors = replace(anchors, component_leaves=(anchors.component_leaves[0],))
    return cache, anchors, nodes


def test_full_only_capacity_and_free_lists_do_not_require_a_state_allocator():
    cache, _, _ = full_only_fixture()
    observed = observe_static_full_mamba(cache)
    assert observed["pool_layout"] == "static_full_only"
    assert observed["device_full_bytes"] == observed["device_total_bytes"] == 260
    assert observed["host_full_bytes"] == observed["host_total_bytes"] == 500
    assert all(observed[name] == 0 for name in (
        "device_mamba_bytes", "device_mamba_slots", "host_mamba_bytes", "host_mamba_slots",
    ))
    assert capture.validated_capacity(observed) == observed
    free = observe_static_full_mamba_headroom(cache)
    assert free.observable and free.device_full_free_tokens == 10
    assert free.device_mamba_free_slots == free.host_mamba_free_slots == 0
    usage = observe_static_full_mamba_host_usage(cache)
    assert usage.observable and usage.host_full_used_bytes == 100
    assert usage.host_mamba_used_slots == usage.host_mamba_used_bytes == 0


def test_full_only_telemetry_census_and_usage_share_the_real_pool_set(tmp_path):
    cache, _, _ = full_only_fixture()
    cache.tree_core.set_beliefkv_host_eviction_observer = lambda observer: None
    cache.full_evictable_size = lambda: 0
    audit = NativeReactiveTelemetry(tmp_path / "telemetry")
    try:
        audit.record_capacity(cache)
        audit.record_host_pool_usage(cache)
        assert set(audit._host_pool_geometry) == set(audit._host_pool_high_water) == {"full"}
        assert audit._host_pool_high_water["full"] == {"used_units": 5, "used_bytes": 100}
    finally:
        audit.close()
    records = [
        json.loads(raw) for raw in (tmp_path / "telemetry/host_pool_telemetry.jsonl").read_text().splitlines()
    ]
    assert set(records[0]["pools"]) == set(records[0]["device_pools"]) == {"full"}
    assert records[0]["device_pools"]["full"]["observable"] is True
    assert records[0]["pools"]["full"]["used_fraction"] == 0.2


@pytest.mark.parametrize("field,value", (
    ("device_mamba_bytes", 1), ("host_mamba_slots", 1),
    ("host_total_bytes", 0), ("device_full_tokens", True),
))
def test_full_only_capacity_rejects_fictitious_or_invalid_pools(field, value):
    cache, _, _ = full_only_fixture()
    with pytest.raises(ValueError, match="capacity census"):
        capture.validated_capacity({**observe_static_full_mamba(cache), field: value})


@pytest.mark.parametrize("input_tokens,expected", ((0, 0), (2, 2), (3, 2), (4, 4), (8, 8)))
def test_full_only_h2d_uses_input_prefix_without_requiring_mamba(input_tokens, expected):
    cache, anchors, nodes = full_only_fixture(input_tokens=input_tokens)
    for node in nodes.values():
        node.component_data[0].value = None
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) == bool(expected)
    observed = inspect_session_h2d_opportunity(
        cache, anchors, max_steps=8, skip_empty_restore=True, include_checkpoint_plan=True,
    )
    assert observed.required_full_tokens == expected
    assert observed.required_mamba_slots == 0
    assert not any(step.include_mamba for step in observed.steps)
    if expected:
        assert observed.fits_current_free_lists is True
        assert observed.checkpoint_plan.full_tokens == expected
        assert observed.checkpoint_plan.mamba_slots == 0


def test_full_only_incremental_prepare_covers_the_reusable_input():
    cache, anchors, nodes = full_only_fixture(backed=False)
    assert missing_prepare_full_prefix_tokens(cache, anchors) == 3
    candidate = capture_action_local_shadow(cache, anchors, include_non_actionable=True)
    steps = shadow_backup_steps(candidate)
    assert [step.node_id for step in steps] == [1, 2]
    assert all(not step.include_mamba for step in steps)
    nodes[1].component_data[0].host_value = range(2)
    nodes[2].component_data[0].host_value = range(2)
    assert missing_prepare_full_prefix_tokens(cache, anchors) == 0
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is False


def test_full_only_pending_transfer_and_replaced_anchor_keep_native_validation():
    cache, anchors, nodes = full_only_fixture()
    nodes[1].component_data[0].value = None
    nodes[1].load_back_pending_id = 11
    observed = inspect_session_h2d_opportunity(cache, anchors, max_steps=8)
    assert observed.step is None
    nodes[1].load_back_pending_id = None
    changed = replace(anchors, component_leaves=((0, ((3, 999.),)),))
    assert inspect_session_h2d_opportunity(cache, changed).step is None


def test_runtime_uses_full_only_capacity_and_has_zero_state_reserve(tmp_path):
    cache, _, _ = full_only_fixture()
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "events.sock"))
    try:
        runtime.attach_native_cache(cache)
        runtime._native_max_running = 24
        runtime._native_prefill_slots = 2
        runtime._native_running_batch = NS(reqs=[])
        runtime._native_input_reserve = 2
        budget = runtime._current_residency_budget(include_evictable=True)
        assert runtime._residency_pool_bytes == (20, 0)
        assert budget.source == "native_next_prefill"
        assert budget.reserve_mamba_slots == budget.evictable_mamba_slots == 0
        assert runtime._prepare_pressure([object()], NS(reqs=[]), observe_static_full_mamba_headroom(cache)) == (True, False)
    finally:
        runtime.close()


def test_new_model_generation_uses_its_declared_profile():
    assert sampling_parameters("qwen3_coder") == {
        "temperature": 0.7, "top_p": 0.8, "presence_penalty": 0.0,
        "extra_body": {"top_k": 20, "min_p": 0.0, "repetition_penalty": 1.05},
    }


def test_new_model_experiment_has_no_ablations_or_hybrid_artifacts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "configs/migration/qwen3_coder_24project_running24_20261024.json").read_text())
    reference = controls.experiment_reference({"arms": {"full": {}}}, spec)
    common = reference["arms"]["full"]
    assert spec["order"] == ["full"]
    assert common["ROOT_COUNT"] == common["MAX_RUNNING_REQUESTS"] == "24"
    assert common["DEVICE_MAMBA_SLOTS"] == common["REASONING_PARSER"] == ""
    assert common["HOST_SPLIT"] == "auto"
    assert common["FANOUT_PROFILE"] == "native_in_graph_dynamic_nested"
    assert all(common[name] == "" for name in (
        "TOOL_TIMING_ARTIFACT", "TRANSFER_SERVICE_SEED", "H2D_SEED_ARTIFACT",
    ))
    settings, flags, _ = controls.environment(
        reference, "full", root, tmp_path / "engine", tmp_path / "arm",
        18580, tmp_path / "plugin", sampling_mode="position", max_running_requests=24,
    )
    assert "--mamba-full-memory-ratio" not in flags
    assert settings["BELIEFKV_FULL_MAMBA_HOST_SPLIT"] == "auto"
    assert settings["MODEL_PATH"] == common["MODEL_PATH"]
    assert settings["BELIEFKV_ONLINE_TOOL_TIMING"] == "1"
    assert settings["TOOL_CONTINUITY"] == settings["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "1"


def test_new_model_capture_discards_the_old_capacity_and_uses_the_same_declared_work(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "configs/migration/qwen3_coder_24project_running24_20261024.json").read_text())
    plan, checkout = reference(tmp_path)
    source = tmp_path / spec["common_environment"]["WORKLOAD_MANIFEST"]
    frozen = checkout / spec["common_environment"]["WORKLOAD_MANIFEST"]
    for path in (source, frozen):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(disjoint_manifest(24)))
    monkeypatch.setattr(capture, "ROOT", tmp_path)
    observed = capture.environment(
        plan, checkout, tmp_path / "engine", tmp_path / "run", 18580,
        source_profile="nested_oneshot", source_workload=source,
        experiment=spec, max_running_requests=24, sampling_profile="qwen3_coder",
    )
    assert observed["NATIVE_POLICY_BASELINE"] == "1"
    assert observed["ROOT_COUNT"] == observed["MAX_RUNNING_REQUESTS"] == "24"
    assert observed["DEVICE_FULL_TOKENS"] == observed["DEVICE_MAMBA_SLOTS"] == ""
    assert observed["FANOUT_PROFILE"] == "native_in_graph_dynamic_nested"
    assert observed["MODEL_PATH"] == spec["common_environment"]["MODEL_PATH"]
    assert observed["CONTEXT_TOKENIZER_PATH"] == observed["MODEL_PATH"]
    assert observed["SAMPLING_PROFILE"] == "qwen3_coder"
    assert observed["WORKLOAD_MANIFEST"] == str(frozen)
    assert observed["BELIEFKV_ENABLE_PREPARE_HOST"] == observed["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"


def test_new_model_population_is_project_disjoint_and_uses_original_change_tasks():
    root = Path(__file__).resolve().parents[1]
    manifest = capture.project_disjoint_workload(
        root / "configs/workloads/locobench_agent_24project_changes_qwen3_coder_20261024.json",
        expected_workflows=24,
    )
    categories = [row["dataset_provenance"]["task_category"] for row in manifest["workloads"]]
    assert {name: categories.count(name) for name in set(categories)} == {
        "feature_implementation": 8, "cross_file_refactoring": 8, "integration_testing": 8,
    }
    assert manifest["tokenizer"] == "/srv/ai/models/Qwen/Qwen3-Coder-30B-A3B-Instruct"
