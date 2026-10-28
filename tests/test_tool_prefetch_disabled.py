"""Explicit tool-prefetch cancellation keeps predictive offload independent."""

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_policy_adaptation import demand_runtime


@pytest.mark.parametrize("name", ("TOOL_PREFETCH", "BELIEFKV_ENABLE_TOOL_PREFETCH"))
@pytest.mark.parametrize("mode", ("full", "kv_only"))
def test_declared_tool_prefetch_off_is_honored_without_an_ablation_label(name, mode):
    settings = arm_environment({
        name: "0", "TOOL_PREFETCH_PRIORITY_MS": "1500",
        "PREDICTED_TOOL_OFFLOAD": "1",
    }, mode)
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert settings["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"] == "0"
    assert settings["BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD"] == "1"
    assert settings["ENABLE_TOOL_TIMING"] == settings["ONLINE_TOOL_TIMING"] == "1"
    assert settings["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "1"


@pytest.mark.parametrize("value", ("false", "", "2", 0, False))
def test_invalid_tool_prefetch_declaration_is_rejected(value):
    with pytest.raises(ValueError, match="tool prefetch"):
        arm_environment({"TOOL_PREFETCH": value}, "full")


def test_canonical_tool_prefetch_setting_wins_in_a_direct_environment():
    settings = arm_environment({
        "TOOL_PREFETCH": "1", "BELIEFKV_ENABLE_TOOL_PREFETCH": "0",
    }, "full")
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"


def test_fresh_experiment_clears_an_inherited_tool_prefetch_alias():
    path = comparison.ROOT / "configs/migration/predictive_host_demand_layer_handoff_20261028.json"
    spec = json.loads(path.read_bytes())
    spec["common_environment"]["TOOL_PREFETCH"] = "0"
    reference = {"arms": {"full": {"BELIEFKV_ENABLE_TOOL_PREFETCH": "1"}}}
    prepared = comparison.experiment_reference(reference, spec)
    settings = arm_environment(prepared["arms"]["full"], "full")
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert reference["arms"]["full"]["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "1"
    spec["common_environment"]["BELIEFKV_ENABLE_TOOL_PREFETCH"] = "1"
    with pytest.raises(ValueError, match="conflicting predeclared"):
        comparison.experiment_reference(reference, spec)


def test_declared_off_reaches_runtime_observation_and_skips_prefetch_work(monkeypatch, tmp_path):
    common = {"TOOL_PREFETCH": "0", "TOOL_PREFETCH_PRIORITY_MS": "1500"}
    settings = arm_environment(common, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + name, settings[name])
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "disabled.sock"))
    try:
        runtime.tool_wait_hints["not-a-live-context"] = object()
        runtime.inspect_context_h2d_opportunity = Mock(
            side_effect=AssertionError("disabled tool prefetch must not inspect KV"),
        )
        runtime.dispatch_tool_prefetch()
        assert not runtime.counts["tool_prefetch_scans"]
        assert not runtime.counts["tool_prefetch_issued"]
        assert runtime._tool_ticket is None and not runtime._tool_survival_probes
        runtime.inspect_context_h2d_opportunity.assert_not_called()
        runtime.tool_wait_hints.clear()
        runtime._opportunity_writer = Mock()
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda _path: state)
        plan = {"reference_environment": common}
        comparison.observed_configuration(plan, "full", tmp_path)
        state["tool_prefetch"] = True
        with pytest.raises(ValueError, match="tool_prefetch"):
            comparison.observed_configuration(plan, "full", tmp_path)
    finally:
        runtime.tool_wait_hints.clear()
        runtime.close()


def test_disabling_tool_prefetch_preserves_native_predicted_tool_release(demand_runtime):
    runtime, controller, _cache, allocator, donor, _request, _writes = demand_runtime
    runtime.enable_tool_prefetch = False
    controller.dispatch()
    assert donor.component_data[0].value is None
    assert donor.component_data[0].host_value is not None
    assert allocator.available_size() == 36
    assert not runtime.counts["tool_prefetch_scans"]
    assert not runtime.counts["tool_prefetch_issued"]


def test_followup_configuration_keeps_overlap_and_cancels_only_tool_prefetch():
    directory = comparison.ROOT / "configs/migration"
    previous = comparison.load(directory / "predictive_host_demand_layer_handoff_20261028.json")
    updated = comparison.load(directory / "predictive_no_tool_prefetch_20261028.json")
    old, new = previous["common_environment"], updated["common_environment"]
    assert {
        name for name in old.keys() | new.keys() if old.get(name) != new.get(name)
    } == {
        "TOOL_PREFETCH", "BELIEFKV_ENABLE_TOOL_PREFETCH",
        "TOOL_PREFETCH_PRIORITY_MS", "BELIEFKV_TOOL_PREFETCH_PRIORITY_MS",
    }
    assert all(new[name] == "0" for name in (
        "TOOL_PREFETCH", "BELIEFKV_ENABLE_TOOL_PREFETCH",
        "TOOL_PREFETCH_PRIORITY_MS", "BELIEFKV_TOOL_PREFETCH_PRIORITY_MS",
    ))
    assert new["LAYER_HANDOFF"] == new["BELIEFKV_LAYER_HANDOFF"] == "1"
    assert updated["comparison_contract"]["layer_handoff_enabled_for_implementation_audit"] is True
    assert updated["gpu_execution"]["registered"] is False
    assert updated["performance_accepted"] is False


def test_future_profile_cannot_reenable_tool_prefetch_through_priority_cli(monkeypatch, tmp_path):
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps({"arms": {"full": {}}}))
    monkeypatch.setattr(comparison.fresh, "verify", Mock())
    monkeypatch.setattr(comparison.fresh, "check_hold", Mock())
    args = NS(
        arms=["full"], after_registration=None, maximum_replay_slowdown=None,
        registration=tmp_path / "unallocated.registration.json", run_root=tmp_path / "unallocated",
        reference=reference, retention_config=None, tool_prefetch_priority_ms=1500.,
        experiment_config=(
            comparison.ROOT / "configs/migration/predictive_no_tool_prefetch_20261028.json"
        ),
    )
    with pytest.raises(ValueError, match="tool prefetch is disabled"):
        comparison.register(args)
    assert not args.registration.exists() and not args.run_root.exists()
