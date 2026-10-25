import pytest

from beliefkv.experiments.internal_comparison import validate_runtime
from beliefkv.experiments.paper_baselines import DISABLED_POLICIES, RUNTIME_ARMS
from scripts import run_causal_comparison as comparison
from tests.test_causal_comparison import reference


def test_external_arm_has_its_own_identity_without_changing_internal_controls(
    reference, tmp_path,
):
    assert "thunderagent" in comparison.ALL_CONTROLS
    assert "thunderagent" not in comparison.CONTROLS
    settings, flags, socket = comparison.environment(
        reference, "thunderagent", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    assert settings["BELIEFKV_PAPER_BASELINE"] == RUNTIME_ARMS["thunderagent"]
    assert settings["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "scheduler_only"
    assert settings["BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY"] == "0"
    assert settings["BELIEFKV_ENABLE_RESIDENT_FIRST"] == "0"
    assert settings["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    assert settings["SERVER_RANDOM_SEED"] == settings["SAMPLING_SEED"] == "21"
    assert "--enable-beliefkv-admission" in flags and socket is not None


def test_external_arm_real_cpu_constructor_accepts_the_generated_environment(
    reference, tmp_path,
):
    settings, _flags, _socket = comparison.environment(
        reference, "thunderagent", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    result = comparison.preflight_runtime(settings, str(tmp_path))
    assert result["actual_runtime_constructor_accepted"] is True
    assert result["runtime_configuration_verified"] is True


def test_external_runtime_must_be_declared_and_cannot_pass_as_our_scheduler():
    observed = {
        **{name: False for name in DISABLED_POLICIES},
        "internal_comparison_mode": "scheduler_only",
        "prefill_scheduling_policy": "workflow", "prefill_ordering_mode": "workflow",
        "physical_disabled": False,
        "paper_baseline": {
            "policy": RUNTIME_ARMS["thunderagent"], "program_identity": "invocation_id",
            "future_information_used": False, "native_capacity_authority_preserved": True,
        },
    }
    validate_runtime("scheduler_only", observed, external_policy="thunderagent")
    with pytest.raises(ValueError, match="not declared"):
        validate_runtime("scheduler_only", observed)
    observed["paper_baseline"]["future_information_used"] = True
    with pytest.raises(ValueError, match="information"):
        validate_runtime("scheduler_only", observed, external_policy="thunderagent")


def test_continuum_uses_native_pressure_priority_without_unconditional_preemption(
    reference, tmp_path,
):
    settings, flags, _socket = comparison.environment(
        reference, "continuum_public", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    assert settings["BELIEFKV_PAPER_BASELINE"] == RUNTIME_ARMS["continuum_public"]
    assert "--enable-priority-scheduling" in flags
    assert "--disable-priority-preemption" in flags
    assert "--schedule-low-priority-values-first" in flags
    assert flags[flags.index("--retraction-policy") + 1] == "priority"


def test_concur_is_an_agent_window_with_explicit_join_compatibility(reference, tmp_path):
    settings, flags, _socket = comparison.environment(
        reference, "concur_observed_join", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    assert settings["BELIEFKV_PAPER_BASELINE"] == "concur_paper_observed_join"
    assert settings["BELIEFKV_CONCUR_INITIAL_AGENTS"] == settings["ROOT_COUNT"]
    assert settings["BELIEFKV_CONCUR_INTERVAL_MS"] == "1000"
    assert "--enable-priority-scheduling" not in flags
    assert "--enable-beliefkv-admission" in flags
