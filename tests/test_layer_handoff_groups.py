"""Bind independently declared D2H/H2D groups to the native reservation."""

from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
from pathlib import Path

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.layer_handoff import try_handoff
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_causal_comparison import preflight_runtime
from scripts.qualify_layer_handoff_model import validate_direction_groups
from tests.test_layer_handoff import native_handoff
from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_offload_before_admission import configuration


def test_selected_native_handoff_receives_independent_groups_and_keeps_reserved_pages(native_handoff):
    f = native_handoff
    f.runtime.layer_handoff_d2h_layers, f.runtime.layer_handoff_h2d_layers = 8, 4
    f.cache.cache_controller.mem_pool_host.anchor_entry.host_pool.transfer_handoff_layers = Mock()
    assert try_handoff(
        f.offloader, f.runtime.context_sessions["ctx-tool"],
        NS(node_id=f.donor.id, creation_time=f.donor.creation_time),
    )
    arguments = f.cache.cache_controller.l2_transfer_engine.submit_layer_handoff.call_args.kwargs
    assert arguments["donor_group_layers"] == 8 and arguments["consumer_group_layers"] == 4
    assert f.allocator.available_size() == 4
    assert f.cache._beliefkv_layer_handoff.reserved_tail.numel() == 16
    f.cache.sanity_check()


def test_runtime_constructor_and_preflight_bind_both_direction_groups(tmp_path):
    settings = arm_environment({
        **configuration(), "LAYER_HANDOFF": "1",
        "BELIEFKV_LAYER_HANDOFF_D2H_LAYERS": "8",
        "BELIEFKV_LAYER_HANDOFF_H2D_LAYERS": "4",
    }, "full")
    result = preflight_runtime(settings, str(tmp_path))
    assert result["verified_configuration"]["layer_handoff_d2h_layers"] == 8
    assert result["verified_configuration"]["layer_handoff_h2d_layers"] == 4


@pytest.mark.parametrize("direction", ("D2H", "H2D"))
@pytest.mark.parametrize("value", ("0", "65", "-1", "nan", "2.5"))
def test_runtime_rejects_invalid_group_sizes(monkeypatch, tmp_path, direction, value):
    settings = arm_environment({
        **configuration(), "LAYER_HANDOFF": "1",
        "BELIEFKV_LAYER_HANDOFF_" + direction + "_LAYERS": value,
    }, "full")
    for name, setting in settings.items():
        monkeypatch.setenv(name, setting)
    with pytest.raises(ValueError, match="groups"):
        NativeAdmissionRuntime(event_socket_path=str(tmp_path / "invalid.sock"))


def test_grouped_configuration_cannot_attach_the_old_per_layer_engine(monkeypatch, tmp_path):
    for name, value in arm_environment({
        **configuration(), "LAYER_HANDOFF": "1",
        "BELIEFKV_LAYER_HANDOFF_D2H_LAYERS": "8",
    }, "full").items():
        monkeypatch.setenv(name, value)
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "old-engine.sock"))
    try:
        with pytest.raises(ValueError, match="grouped native engine"):
            runtime.attach_native_cache(NS(begin_beliefkv_layer_handoff=Mock()))
    finally:
        runtime.close()


def test_grouped_model_cannot_reuse_per_layer_component_evidence():
    configuration = {"common_environment": {
        "BELIEFKV_LAYER_HANDOFF_D2H_LAYERS": "8",
        "BELIEFKV_LAYER_HANDOFF_H2D_LAYERS": "4",
    }}
    with pytest.raises(ValueError, match="direction groups"):
        validate_direction_groups(configuration, {})
    with pytest.raises(ValueError, match="direction groups"):
        validate_direction_groups(configuration, {"direction_groups": {"d2h": 4, "h2d": 8}})
    validate_direction_groups(configuration, {"direction_groups": {"d2h": 8, "h2d": 4}})


def test_new_profile_preserves_workload_and_tool_prefetch_cancellation():
    configs = Path(__file__).resolve().parents[1] / "configs/migration"
    before = json.loads((configs / "predictive_no_tool_prefetch_20261028.json").read_bytes())
    after = json.loads((configs / "predictive_join_blocked_layer_groups_20261028.json").read_bytes())
    old, new = before["common_environment"], after["common_environment"]
    assert {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)} == {
        "SGLANG_PATCH_FLAVOR", "AGENT_JOIN_BLOCKED_HANDOFF", "BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF",
        "BELIEFKV_LAYER_HANDOFF_D2H_LAYERS", "BELIEFKV_LAYER_HANDOFF_H2D_LAYERS",
    }
    assert after["tool_timing_overlay"] == before["tool_timing_overlay"]
    assert new["TOOL_PREFETCH"] == new["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert new["LAYER_HANDOFF"] == new["BELIEFKV_LAYER_HANDOFF"] == "1"
    assert after["gpu_execution"]["registered"] is False
