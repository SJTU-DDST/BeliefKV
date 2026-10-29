from copy import deepcopy
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import paper_runtime


def runtime(monkeypatch, tmp_path, interval):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_CONTROL_INTERVAL_MS": str(interval),
        "TOOL_PREFETCH": "0", "TOOL_RESTORE_MODE": "speculative",
        "LAYER_HANDOFF": "0", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "0",
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "control.sock"))
    live.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=500),
    ))
    return live


def test_two_second_full_tick_uses_elapsed_decay_and_native_pressure(monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    live = runtime(monkeypatch, tmp_path, 2000)
    adapter = live._agent_admission
    try:
        first, second = request("one", "a", tokens=300), request("two", "b", tokens=200)
        live.register_visible_request(first)
        live.register_visible_request(second)
        adapter.tick(now=100.)
        assert adapter.next_tick == 102.
        clock[0] = 100.2
        adapter.completed(NS(reqs=[first]), ("one",))
        adapter.tick(now=101.99)
        assert adapter.counts["control_ticks"] == 1
        adapter.tick(now=102.)
        assert adapter.counts["control_ticks"] == 2
        assert adapter.next_tick == 104.
        assert adapter.policy.acting_since["a"] == 100.2
        assert adapter.policy.programs["b"].active
        assert not adapter.policy.programs["a"].active
        assert adapter.policy.remaining_capacity() >= 0
        status = adapter.status()
        assert status["decay_half_life_s"] == 1.
        assert status["control_interval_ms"] == 2000.
        validate_reference_admission(status, control_interval_ms=2000)
        with pytest.raises(ValueError, match="reference ThunderAgent"):
            validate_reference_admission(status)
        assert not live.layer_handoff_enabled
    finally:
        live.close()


def test_reference_paper_adapter_keeps_five_seconds_and_one_second_half_life():
    paper = ThunderAgentDecayAdapter(paper_runtime())
    try:
        paper.attach(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=500)))
        paper.tick(now=100.)
        assert paper.next_tick == 105.
        assert paper.status()["control_interval_ms"] == 5000.
        assert paper.status()["decay_half_life_s"] == 1.
    finally:
        paper.close()


@pytest.mark.parametrize("mode", MODES)
def test_control_interval_only_reaches_the_declared_full_composition(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference", "AGENT_CONTROL_INTERVAL_MS": "2000",
    }, mode)
    assert settings["BELIEFKV_AGENT_CONTROL_INTERVAL_MS"] == ("2000" if mode == "full" else "5000")


@pytest.mark.parametrize("label", ("thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph"))
def test_full_interval_cannot_change_paper_control(label):
    settings = paper_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "BELIEFKV_AGENT_CONTROL_INTERVAL_MS": "2000",
    }, label)
    assert settings["BELIEFKV_AGENT_CONTROL_INTERVAL_MS"] == "5000"


@pytest.mark.parametrize("value", ("", "2000.0", "0", "499", "5001", "\uff12\uff10\uff10\uff10", True, 2000))
def test_invalid_interval_is_rejected_before_registration(value):
    with pytest.raises(ValueError, match="control interval"):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_reference", "AGENT_CONTROL_INTERVAL_MS": value,
        }, "full")


def test_model_preflight_binds_the_actual_interval_and_disabled_layer_handoff(tmp_path):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_CONTROL_INTERVAL_MS": "2000",
        "TOOL_RESTORE_MODE": "speculative", "TOOL_PREFETCH": "0",
        "LAYER_HANDOFF": "0", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "0",
    }, "full")
    result = comparison.preflight_runtime(settings, str(tmp_path))
    assert result["verified_configuration"]["agent_control_interval_ms"] == 2000
    assert not result["verified_configuration"]["layer_handoff_enabled"]


@pytest.mark.parametrize("value", (True, 2000., 0, 10000))
def test_reference_validator_requires_an_explicit_valid_integer_declaration(monkeypatch, tmp_path, value):
    live = runtime(monkeypatch, tmp_path, 2000)
    try:
        with pytest.raises(ValueError, match="control interval"):
            validate_reference_admission(deepcopy(live._agent_admission.status()), control_interval_ms=value)
    finally:
        live.close()
