from pathlib import Path

import pytest

from scripts.resume_semantic_h2d_ab import resume_environment, update_predictive_revision


def plan():
    return {
        "code_commit": "old-predictive",
        "sglang_patch_sha256": "same-patch",
        "arm_revisions": {
            "native": {"code_commit": "collected-native", "sglang_patch_sha256": "native-patch"},
            "predictive_h2d": {"code_commit": "old-predictive", "sglang_patch_sha256": "same-patch"},
        },
        "revision_transition": {"native_frozen_plan": "ab_plan.native_frozen.json"},
        "root_count": 156, "workflow_arrival_batch_size": 108,
        "workflow_arrival_batch_interval_ms": 3600000,
        "order": ["native", "predictive_h2d"],
        "host_split": "80:20", "sampling_seed": 21, "repetition_id": 0,
        "activation_wall_clock_seconds": 14400.,
        "semantic_artifact": "/models/semantic.json", "semantic_artifact_sha256": "frozen",
        "policy_configuration": {"predictive_h2d": {"prepare_host": True}},
        "h2d_seed_artifact": "/models/h2d.json", "tool_timing_artifact": "/models/tool.json",
        "transfer_service_seed": "/models/service.json", "tool_timing_enabled": True,
        "prefetch_lead_ms": 500, "semantic_work_statistic": "center",
        "eos_protocol_window_ms": 250, "fanout_profile": "native_in_graph_2to4",
        "child_final_report_shadow": True, "workload_manifest": "/tasks/manifest.json",
        "arrival_schedule": [{"instance_id": "task", "offset_seconds": 3600.}],
    }


def test_resume_preserves_collected_native_and_all_non_revision_configuration():
    original = plan()
    changed = update_predictive_revision(original, "new-predictive", "same-patch")
    assert changed["arm_revisions"]["native"] == original["arm_revisions"]["native"]
    assert changed["arm_revisions"]["predictive_h2d"]["code_commit"] == "new-predictive"
    for name in original.keys() - {"code_commit", "arm_revisions", "revision_transition"}:
        assert changed[name] == original[name]
    assert original["code_commit"] == "old-predictive"


def test_resume_derives_environment_from_frozen_configuration_including_arrivals():
    environment = resume_environment(plan(), Path("/run"), python="/python", port=18454)
    assert environment["RESUME_PENDING"] == "1"
    assert environment["ROOT_COUNT"] == "156"
    assert environment["ARRIVAL_BATCH_SIZE"] == "108"
    assert environment["ARRIVAL_BATCH_INTERVAL_MS"] == "3600000"
    assert environment["CHILD_FINAL_REPORT_SHADOW"] == "1"
    assert environment["PREPARE_HOST"] == "1"
    assert environment["ARM_ORDER"] == "native predictive_h2d"
    assert environment["SEMANTIC_REPORT_ARTIFACT"] == "/models/semantic.json"


def test_resume_retains_historical_native_reference_across_development_iterations():
    configuration = plan()
    configuration.update(order=["predictive_h2d"], native_reference_run="/native-reference")
    environment = resume_environment(configuration, Path("/next"), python="/python", port=18454)
    assert environment["ARM_ORDER"] == "predictive_h2d"
    assert environment["NATIVE_REFERENCE_RUN"] == "/native-reference"


def test_resume_preserves_selected_reference_paths_and_clears_unspecified_paths():
    configuration = plan()
    environment = resume_environment(configuration, Path("/next"), python="/python", port=18454)
    assert environment["NATIVE_REFERENCE_RUN"] == ""
    assert environment["NATIVE_REFERENCE_TIMELINE"] == ""
    assert environment["NATIVE_REFERENCE_CACHE"] == ""
    configuration.update(
        native_reference_run="/native-reference",
        native_reference_timeline="/native-reference/timelines/native.clock_v2.json.gz",
        native_reference_cache="/native-reference/native.clock_v2.json",
    )
    snapshot = dict(configuration)
    environment = resume_environment(configuration, Path("/next"), python="/python", port=18454)
    for field in ("native_reference_run", "native_reference_timeline", "native_reference_cache"):
        assert environment[field.upper()] == configuration[field]
    assert configuration == snapshot


def test_resume_preserves_explicit_numa_policy_and_legacy_binding():
    configuration = plan()
    environment = resume_environment(configuration, Path("/run"), python="/python", port=18454)
    assert environment["HOST_NUMA_MEMORY_POLICY"] == "bind"
    configuration["host_numa_memory_policy"] = "preferred"
    environment = resume_environment(configuration, Path("/run"), python="/python", port=18454)
    assert environment["HOST_NUMA_MEMORY_POLICY"] == "preferred"


def test_resume_retains_poisson_rate_and_arrival_seed():
    configuration = plan()
    configuration.update(
        workflow_arrival_mode="poisson", workflow_arrival_batch_size=0,
        workflow_arrival_batch_interval_ms=0,
        workflow_arrival_rate_per_second=1 / 15, workflow_arrival_seed=21,
    )
    environment = resume_environment(configuration, Path("/run"), python="/python", port=18454)
    assert environment["ARRIVAL_BATCH_SIZE"] == "0"
    assert environment["ARRIVAL_BATCH_INTERVAL_MS"] == "0"
    assert environment["ARRIVAL_RATE_PER_SECOND"] == str(1 / 15)
    assert environment["ARRIVAL_SEED"] == "21"


def test_resume_preserves_small_workload_comparison_population():
    configuration = plan()
    configuration["primary_comparison_policy"] = {
        "tail_active_workflows": 4, "warmup_seconds": 0.,
    }
    environment = resume_environment(configuration, Path("/run"), python="/python", port=18454)
    assert environment["TAIL_ACTIVE_WORKFLOWS"] == "4"
    assert environment["WARMUP_SECONDS"] == "0.0"


@pytest.mark.parametrize("required", [None, False, True])
def test_resume_preserves_frozen_delegation_check_and_legacy_default(required):
    configuration = plan()
    if required is not None:
        configuration["delegation_shape_required_before_native"] = required
    environment = resume_environment(configuration, Path("/run"), python="/python", port=18454)
    assert environment["REQUIRE_DELEGATION_SHAPE"] == str(int(bool(required)))


@pytest.mark.parametrize("legacy_common", [None, False, True])
def test_resume_retains_frozen_frontend_config_without_inherited_defaults(legacy_common):
    configuration = plan()
    if legacy_common is not None:
        configuration["chat_token_block_cache"] = legacy_common
    environment = resume_environment(configuration, Path("/run"), python="/python", port=18454)
    expected = str(int(bool(legacy_common)))
    assert environment["NATIVE_CHAT_TOKEN_BLOCK_CACHE"] == expected
    assert environment["PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE"] == expected
    configuration["chat_token_block_cache_by_arm"] = {
        "native": False, "predictive_h2d": True,
    }
    environment = resume_environment(configuration, Path("/run"), python="/python", port=18454)
    assert environment["NATIVE_CHAT_TOKEN_BLOCK_CACHE"] == "0"
    assert environment["PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE"] == "1"
