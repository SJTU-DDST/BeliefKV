import json
from pathlib import Path

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from scripts.run_fresh_internal_comparison import campaign_environment


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "configs/migration/v123_reference_policy_20261022.json"


def test_v123_policy_is_a_reusable_reference_without_queuing_a_new_full():
    spec = json.loads(SPEC.read_bytes())
    assert spec["status"] == "historical_full_reused_no_new_full_queued"
    assert spec["performance_accepted"] is False
    assert spec["historical_full"]["completed_system_valid_workflows"] == 36
    assert spec["historical_full"]["native_agent_jct_eligible_workflows"] == 35
    environment = campaign_environment(
        {}, spec, Path("/frozen"), Path("/engine"), 18534,
    )
    full = arm_environment(environment, "full")
    expected = {
        "BELIEFKV_PREFILL_SCHEDULING_POLICY": "join_priority",
        "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY": "1",
        "BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY": "0",
        "BELIEFKV_ENABLE_RESIDENT_FIRST": "0",
        "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY": "0",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": "0",
        "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0",
        "BELIEFKV_TOOL_RESTORE_MODE": "execution",
        "BELIEFKV_RESTORE_TIERS": "dependency",
        "BELIEFKV_IDLE_PREPARE_POLICY": "cold",
        "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION": "0",
        "BELIEFKV_ENABLE_EXECUTION_HANDOFF": "0",
        "JOIN_PREFETCH_TRIGGER": "observed_eos",
        "MAX_RUNNING_REQUESTS": "8", "SERVER_RANDOM_SEED": "21",
        "SAMPLING_SEED": "21", "ROOT_COUNT": "36",
        "PARALLEL_WAIT_PREFETCH": "1", "WAIT_PREFETCH_CONTEXTS": "2",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.95",
    }
    assert {name: full[name] for name in expected} == expected


@pytest.mark.parametrize("mode", ("full", "join_priority_only", "native_matched"))
def test_future_v123_controls_keep_workload_seed_capacity_and_disable_continuity(mode):
    common = json.loads(SPEC.read_bytes())["common_environment"]
    environment = arm_environment(common, mode)
    for name in (
        "ROOT_COUNT", "MAX_RUNNING_REQUESTS", "SERVER_RANDOM_SEED", "SAMPLING_SEED",
        "MODEL_CONTEXT_TOKENS", "CONTEXT_WINDOW_TOKENS", "FANOUT_PROFILE",
        "WORKLOAD_MANIFEST", "HOST_SPLIT", "HICACHE_SIZE_GB",
        "ARRIVAL_BATCH_SIZE", "ARRIVAL_BATCH_INTERVAL_MS",
    ):
        assert environment[name] == common[name]
    assert environment["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    assert environment["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
