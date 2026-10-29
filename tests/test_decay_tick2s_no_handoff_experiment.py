from copy import deepcopy
from pathlib import Path

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from experiments.reports.decay_tick2s_no_handoff_20261030 import queue_full
from scripts import run_causal_comparison as causal


def configuration():
    specification = causal.load(queue_full.CONFIGURATION)
    control = causal.load(queue_full.CONTROL)
    return specification, control


def test_pending_full_disables_both_physical_handoff_flags_and_keeps_join_recovery():
    specification, control = configuration()
    queue_full.validate_configuration(specification, control)
    settings = arm_environment(specification["common_environment"], "full")
    for name in ("BELIEFKV_LAYER_HANDOFF", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT", "LAYER_HANDOFF"):
        assert settings[name] == "0"
    assert settings["BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF"] == "1"
    assert settings["BELIEFKV_AGENT_CONTROL_INTERVAL_MS"] == "2000"
    assert settings["BELIEFKV_DEMAND_PREFILL_PIPELINE"] == "1"
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"


@pytest.mark.parametrize("change", ("handoff", "free_extent", "tick", "prefill", "mixed", "arrivals", "tool_prefetch"))
def test_queue_rejects_changed_declared_policy_or_shared_serving_contract(change):
    specification, control = configuration()
    specification = deepcopy(specification)
    common = specification["common_environment"]
    if change == "handoff":
        common.update(LAYER_HANDOFF="1", BELIEFKV_LAYER_HANDOFF="1")
    elif change == "free_extent":
        common["BELIEFKV_LAYER_HANDOFF_FREE_EXTENT"] = "1"
    elif change == "tick":
        common.update(AGENT_CONTROL_INTERVAL_MS="5000", BELIEFKV_AGENT_CONTROL_INTERVAL_MS="5000")
    elif change == "prefill":
        common["CHUNKED_PREFILL_SIZE"] = "4096"
    elif change == "mixed":
        common["ENABLE_MIXED_CHUNK"] = "1"
    elif change == "arrivals":
        specification["workflow_arrivals"]["sha256"] = "0" * 64
    else:
        common.update(TOOL_PREFETCH="1", BELIEFKV_ENABLE_TOOL_PREFETCH="1")
    with pytest.raises(ValueError):
        queue_full.validate_configuration(specification, control)


def test_replacement_qualifies_current_sources_without_relaunching_the_paper_control(tmp_path):
    template = causal.load(queue_full.OLD_QUEUE)["commands"]["full"]
    before = list(template)
    cpu = {"report": {"path": str(tmp_path / "cpu.xml")}, "source_manifest": {"path": str(tmp_path / "sources.json")}}
    command = queue_full.command_from_template(template, cpu, tmp_path / "native.json")
    assert template == before
    assert command[1] == str(queue_full.ROOT / "scripts/run_position_replay_calibration.py")
    assert Path(command[command.index("--experiment-config") + 1]) == queue_full.CONFIGURATION
    assert command[command.index("--comparison-registration") + 1].endswith(
        "decay_tick2s_no_handoff_20261030_r1.registration.json"
    )
    assert command[command.index("--port") + 1] == "18883"
    assert "--after-registration" not in command
    for name in ("--source-registration", "--reference", "--engine", "--engine-report"):
        assert command[command.index(name) + 1] == before[before.index(name) + 1]
