import __future__
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import (
    DISABLED_POLICIES, environment as paper_environment, validate_state,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_offload_before_admission import configuration


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("field", ("DEMAND_PREFILL_PIPELINE", "BELIEFKV_DEMAND_PREFILL_PIPELINE"))
def test_pipeline_is_explicit_full_only_and_every_other_arm_masks_it(mode, field):
    settings = arm_environment({**configuration(), field: "1"}, mode)
    expected = "1" if mode == "full" else "0"
    assert settings["DEMAND_PREFILL_PIPELINE"] == expected
    assert settings["BELIEFKV_DEMAND_PREFILL_PIPELINE"] == expected


@pytest.mark.parametrize("value", ("true", "", "2"))
def test_invalid_pipeline_configuration_is_rejected(value):
    with pytest.raises(ValueError, match="demand prefill"):
        arm_environment({**configuration(), "DEMAND_PREFILL_PIPELINE": value}, "full")


def test_pipeline_requires_existing_agent_admission():
    with pytest.raises(ValueError, match="Full agent"):
        arm_environment({"DEMAND_PREFILL_PIPELINE": "1"}, "full")


@pytest.mark.parametrize("method", ("thunderagent_acting_decay", "kvflow_observed_graph"))
def test_paper_settings_remove_pipeline_without_changing_their_policy(method):
    settings = paper_environment({**configuration(), "DEMAND_PREFILL_PIPELINE": "1"}, method)
    assert settings["BELIEFKV_DEMAND_PREFILL_PIPELINE"] == "0"
    assert settings["DEMAND_PREFILL_PIPELINE"] == "0"


def test_paper_observer_rejects_actual_pipeline_leakage():
    state = {
        "internal_comparison_mode": "scheduler_only", "agent_admission": None,
        **dict.fromkeys(DISABLED_POLICIES, False), "demand_prefill_pipeline": True,
    }
    with pytest.raises(ValueError, match="native demand pipeline"):
        validate_state("thunderagent_acting_decay", state)


@pytest.mark.parametrize("enabled", (False, True))
def test_actual_runtime_preflight_observes_default_off_and_enabled_full(enabled, tmp_path):
    settings = arm_environment({
        **configuration(), "DEMAND_PREFILL_PIPELINE": str(int(enabled)),
    }, "full")
    result = comparison.preflight_runtime(settings, str(tmp_path))
    assert result["verified_configuration"]["demand_prefill_pipeline_enabled"] is enabled


@pytest.mark.parametrize("actual", (False, None))
def test_formal_observer_refuses_missing_or_disabled_pipeline(monkeypatch, tmp_path, actual):
    plan = {
        "reference_environment": {**configuration(), "DEMAND_PREFILL_PIPELINE": "1"},
        "source_plan": {"requests": 0},
    }
    snapshot = {"predicted_tool_offload": True, "tool_offload_before_admission": True}
    if actual is not None:
        snapshot["demand_prefill_pipeline"] = actual
    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda *_args: snapshot)
    with pytest.raises(ValueError, match="effective demand prefill pipeline"):
        comparison.observed_configuration(plan, "full", tmp_path, final=True)


@pytest.mark.parametrize("mode", ("native_matched", "full", "thunderagent_acting_decay"))
def test_actual_native_constructor_keeps_the_pipeline_isolated(full_native, monkeypatch, tmp_path, mode):
    checkout = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    source = checkout / "python/sglang/srt/managers/scheduler.py"
    cls = next(
        node for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "Scheduler"
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "init_beliefkv_runtime"
    )
    common = {**configuration(), "DEMAND_PREFILL_PIPELINE": "1"}
    settings = (
        paper_environment(common, mode) if mode == "thunderagent_acting_decay"
        else arm_environment(common, mode)
    )
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    for name in ("BELIEFKV_NATIVE_TELEMETRY_DIR", "BELIEFKV_ADMISSION_TELEMETRY_DIR"):
        monkeypatch.delenv(name, raising=False)
    cache, _, _, _, _ = full_native
    null = object()
    scheduler = NS(
        tree_cache=cache, server_args=NS(
            enable_beliefkv_admission=mode != "native_matched", tp_size=1,
            hicache_host_memory_mode="cache",
            beliefkv_event_socket_path=str(tmp_path / "events.sock"),
        ),
        ps=NS(tp_size=1, pp_size=1), disaggregation_mode=null,
        spec_algorithm=NS(is_none=lambda: True), idle_sleeper=None,
        _beliefkv_on_native_transfer_commit=lambda _: None,
    )
    scope = {"os": os, "DisaggregationMode": NS(NULL=null), "__file__": str(source)}
    exec(compile(
        ast.Module(body=[method], type_ignores=[]), str(source), "exec",
        flags=__future__.annotations.compiler_flag,
    ), scope)
    try:
        scope["init_beliefkv_runtime"](scheduler)
        pipeline = scheduler._beliefkv_demand_pipeline
        assert (pipeline is not None) is (mode == "full")
        if mode == "native_matched":
            assert scheduler.beliefkv_runtime is None
        elif mode == "full":
            assert pipeline.runtime is scheduler.beliefkv_runtime
            assert scheduler.beliefkv_runtime._demand_prefill_pipeline is pipeline
        else:
            assert scheduler.beliefkv_runtime._paper_baseline.policy_name == (
                "thunderagent_public_single_backend_acting_decay"
            )
    finally:
        if scheduler.beliefkv_runtime is not None:
            scheduler.beliefkv_runtime.close()
