from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment, validate_runtime
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_fresh_internal_comparison import campaign_environment


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "configs/migration/v108_eos_reference_policy_20261022.json"


def test_v108_eos_recipe_restores_tool_residency_and_stage_policy_without_report_prediction():
    spec = json.loads(SPEC.read_bytes())
    common = campaign_environment({}, spec, Path("/frozen"), Path("/engine"), 18534)
    env = arm_environment(common, "full")
    expected = {
        "BELIEFKV_PREFILL_SCHEDULING_POLICY": "workflow",
        "BELIEFKV_ENABLE_RESIDENT_FIRST": "1",
        "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY": "1",
        "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY": "1",
        "BELIEFKV_TOOL_RESTORE_MODE": "speculative",
        "BELIEFKV_ENABLE_TOOL_PREFETCH": "1",
        "BELIEFKV_IDLE_PREPARE_POLICY": "all",
        "BELIEFKV_RESTORE_TIERS": "complete",
        "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION": "1",
        "JOIN_PREFETCH_TRIGGER": "observed_eos",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": "0",
        "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0",
        "BELIEFKV_ENABLE_EXECUTION_HANDOFF": "0",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21",
        "NATIVE_CHAT_TOKEN_BLOCK_CACHE": "1", "PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE": "1",
    }
    assert {name: env[name] for name in expected} == expected
    assert spec["status"] == "future_policy_selected_no_new_gpu_run_registered"


def test_v108_eos_effective_runtime_preserves_tools_and_disables_child_work_forecast(
    monkeypatch, tmp_path,
):
    common = json.loads(SPEC.read_bytes())["common_environment"]
    env = arm_environment(common, "full")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM", "JOIN_PREFETCH_TRIGGER"):
        monkeypatch.setenv("BELIEFKV_" + name, env[name])
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "runtime.sock"))
    runtime._opportunity_writer = Mock()
    try:
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        validate_runtime(
            "full", state, tool_continuity=False, execution_frontier_restore=False,
            scheduling_policy="workflow", tool_restore_mode="speculative",
            idle_prepare_policy="all", restore_tiers="complete", residency_protection=True,
        )
        assert state["tool_prefetch"] is True
        assert state["join_prefetch_trigger"] == "observed_eos"
        assert state["selective_join_prefetch"]["enabled"] is False
        assert state["resident_first"] is True
        assert state["final_stage_priority"] is True
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("field,value", (
    ("SERVER_RANDOM_SEED", "488387661"), ("MAX_RUNNING_REQUESTS", "12"),
    ("JOIN_PREFETCH_TRIGGER", "selective_report_or_eos"),
    ("TOOL_CONTINUITY", "1"), ("PREFILL_SCHEDULING_POLICY", "join_priority"),
))
def test_recipe_rejects_uncontrolled_old_settings_or_a_silent_policy_change(field, value):
    spec = deepcopy(json.loads(SPEC.read_bytes()))
    spec["common_environment"][field] = value
    with pytest.raises(ValueError):
        campaign_environment({}, spec, Path("/frozen"), Path("/engine"), 18534)
