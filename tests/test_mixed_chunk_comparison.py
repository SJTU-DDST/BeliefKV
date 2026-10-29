import json
from pathlib import Path

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from scripts.prepare_mixed_chunk_comparison import prepare, validate_profiles
from scripts.run_causal_comparison import prefill_server_parameters

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
THUNDER = ROOT / "experiments/reports/prefill2048_mean120_20261029/thunderagent_acting_decay.json"


def test_mixed_pair_changes_only_shared_serving_flag_and_preserves_policies():
    candidates = prepare(FULL, THUNDER)
    assert validate_profiles(candidates) == {
        "full": FULL, "thunderagent_acting_decay": THUNDER,
    }
    for method, path in (("full", FULL), ("thunderagent_acting_decay", THUNDER)):
        base = json.loads(path.read_bytes())
        candidate = candidates[method]
        expected = {**base["common_environment"], "ENABLE_MIXED_CHUNK": "1"}
        assert candidate["common_environment"] == expected
        assert candidate["workflow_arrivals"] == base["workflow_arrivals"]
        assert candidate["tool_timing_overlay"] == base["tool_timing_overlay"]
        assert candidate["source_registration"] == base["source_registration"]
        assert candidate["serving_profile_experiment"]["shared_serving_tuning_not_agent_policy_innovation"]
        assert candidate["gpu_execution"]["requires_matched_mixed_control"]
        assert "complete_control" not in candidate["gpu_execution"]
        assert not candidate["gpu_execution"]["registered"]
        assert prefill_server_parameters({}, expected) == {
            "chunked_prefill_size": 2048, "enable_mixed_chunk": True,
        }
    full = arm_environment(candidates["full"]["common_environment"], "full")
    thunder = paper_environment(
        candidates["thunderagent_acting_decay"]["common_environment"], "thunderagent_acting_decay",
    )
    assert full["BELIEFKV_AGENT_CONTROL_INTERVAL_MS"] == "2000"
    assert thunder["BELIEFKV_AGENT_CONTROL_INTERVAL_MS"] == "5000"
    assert full["BELIEFKV_ENABLE_PREPARE_HOST"] == "1"
    assert thunder["BELIEFKV_ENABLE_PREPARE_HOST"] == "0"


@pytest.mark.parametrize("field", (
    "MODEL_PATH", "MAX_RUNNING_REQUESTS", "CHUNKED_PREFILL_SIZE",
    "SERVER_RANDOM_SEED", "HICACHE_SIZE_GB",
))
def test_mixed_pair_refuses_unmatched_shared_parameters(tmp_path, field):
    base = json.loads(THUNDER.read_bytes())
    base["common_environment"][field] = "different"
    path = tmp_path / "thunder.json"
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="shared serving parameters differ"):
        prepare(FULL, path)


@pytest.mark.parametrize("field", ("source_registration", "workflow_arrivals", "tool_timing_overlay"))
def test_mixed_pair_refuses_different_work_or_exogenous_timing(tmp_path, field):
    base = json.loads(THUNDER.read_bytes())
    base[field] = {"changed": True}
    path = tmp_path / "thunder.json"
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="identical recorded work"):
        prepare(FULL, path)


@pytest.mark.parametrize("field", ("TOOL_PREFETCH", "EXECUTION_FRONTIER_RESTORE", "LAYER_HANDOFF"))
def test_shared_serving_candidate_cannot_reenable_user_disabled_features(tmp_path, field):
    base = json.loads(FULL.read_bytes())
    base["common_environment"][field] = "1"
    if field == "LAYER_HANDOFF":
        base["common_environment"]["BELIEFKV_LAYER_HANDOFF"] = "1"
    path = tmp_path / "full.json"
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError):
        prepare(path, THUNDER)


@pytest.mark.parametrize("field,value", (
    ("AGENT_CONTROL_INTERVAL_MS", "500"),
    ("AGENT_FREE_DISPATCH_INTERVAL_MS", "500"),
    ("WORKFLOW_SIZE_RESUME", "1"),
    ("ENABLE_MIXED_CHUNK", "0"),
    ("CHUNKED_PREFILL_SIZE", "1024"),
    ("BELIEFKV_LAYER_HANDOFF_FREE_EXTENT", "1"),
))
def test_launch_validation_rejects_policy_or_serving_drift_after_preparation(field, value):
    candidates = prepare(FULL, THUNDER)
    candidates["full"]["common_environment"][field] = value
    with pytest.raises(ValueError, match="changed more than"):
        validate_profiles(candidates)


def test_launch_validation_rechecks_base_content_and_both_methods(tmp_path):
    base = tmp_path / "full.json"
    base.write_bytes(FULL.read_bytes())
    candidates = prepare(base, THUNDER)
    base.write_text(base.read_text() + "\n")
    with pytest.raises(ValueError, match="base configuration changed"):
        validate_profiles(candidates)
    with pytest.raises(ValueError, match="both declared methods"):
        validate_profiles({"full": candidates["full"]})
