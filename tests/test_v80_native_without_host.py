from copy import deepcopy

import pytest

from scripts.start_v79_checkpoint_diagnostic_20261016 import EXPECTED_SETTINGS, MATCHED_FIELDS
from scripts.start_v80_native_without_host_20261016 import (
    DEVICE_FIELDS, HOST_FIELDS, build_plan, collection_environment, startup_configuration,
)
from beliefkv.experiments.arrival_schedule import arrival_schedule_digest


def fixture():
    ids = [f"task-{number}" for number in range(156)]
    arrivals = [{"instance_id": name, "offset_seconds": number * 15.0}
                for number, name in enumerate(ids)]
    reference = {
        **{key: None for key in MATCHED_FIELDS}, **deepcopy(EXPECTED_SETTINGS),
        "workload_instance_ids_in_manifest_order": ids, "arrival_schedule": arrivals,
        "arrival_schedule_sha256": arrival_schedule_digest(arrivals),
        "shared_system_configuration": {"write_back_batch_nodes": 32},
        "sglang_patch_sha256": "unchanged-engine",
    }
    capacity = {
        "pool_layout": "static_separate_full_mamba", "device_full_tokens": 1798992,
        "device_mamba_slots": 513, "device_full_bytes": 36843376640,
        "device_mamba_bytes": 33096007680, "device_total_bytes": 69939384320,
        "host_full_tokens": 7812501, "host_mamba_slots": 622,
        "host_full_bytes": 160000020480, "host_mamba_bytes": 40050032640,
        "host_total_bytes": 200050053120,
    }
    plan = build_plan(reference, capacity, commit="fixed", manifest="/fixed/manifest")
    return reference, capacity, plan


def test_no_host_control_changes_only_host_and_explicitly_pins_the_same_hbm():
    reference, capacity, plan = fixture()
    assert all(plan[key] == reference[key] for key in MATCHED_FIELDS
               if key not in {"host_gb", "demand_restore_policy"})
    assert reference["host_gb"] == plan["host_budget_gb"] == 200
    assert plan["host_gb"] == 0
    assert plan["fixed_device_capacity"] == {key: capacity[key] for key in DEVICE_FIELDS}
    env = collection_environment(plan)
    assert env["HICACHE_SIZE_GB"] == "0"
    assert env["DEVICE_FULL_TOKENS"] == "1798992"
    assert env["DEVICE_MAMBA_SLOTS"] == "513"
    assert env["BELIEFKV_CHECKPOINT_PATH_SHADOW"] == "0"
    assert env["BELIEFKV_ENABLE_PREPARE_HOST"] == "0"
    assert env["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "0"
    assert env["NATIVE_POLICY_BASELINE"] == "1"


def startup_fixture():
    _, capacity, plan = fixture()
    serving = {
        "status": "ready", "max_running_requests": 48, "context_length": 131072,
        "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
        "enable_mixed_chunk": False, "enable_prefill_delayer": False,
        "min_free_slots_delay": None, "enable_beliefkv_admission": False,
        "enable_hierarchical_cache": False, "enable_session_radix_cache": True,
        "mamba_max_states_per_path": -1, "mamba_full_memory_ratio": 0.9,
        "mamba_radix_cache_strategy": "extra_buffer",
        "max_mamba_cache_size": 513, "max_total_tokens": 1798992,
    }
    telemetry = {
        "host_tier_enabled": False, "checkpoint_path_shadow": False,
        "record_output_token_ids": False, "writer_error": None,
    }
    manifest = {
        "arrival_schedule": plan["arrival_schedule"],
        "arrival_schedule_sha256": plan["arrival_schedule_sha256"],
        "client_inflight_root_window": 156,
        "config": {"concurrency": 156, "native_policy_baseline": True},
    }
    capacity.update({key: 0 for key in HOST_FIELDS})
    return serving, telemetry, manifest, capacity, plan


def test_actual_no_host_startup_requires_same_physical_device_census():
    startup_configuration(*startup_fixture())


@pytest.mark.parametrize("fault", [
    "host_flag", "host_allocation", "device_full", "device_mamba",
    "session_disabled", "extra_ordering", "path_cap", "diagnostic_enabled",
    "arrival_stream", "native_policy", "writer_error",
])
def test_no_host_startup_rejects_unmatched_resources_work_or_policy(fault):
    serving, telemetry, manifest, capacity, plan = startup_fixture()
    if fault == "host_flag":
        serving["enable_hierarchical_cache"] = True
    elif fault == "host_allocation":
        capacity["host_mamba_slots"] = 1
    elif fault == "device_full":
        capacity["device_full_tokens"] += 1
    elif fault == "device_mamba":
        capacity["device_mamba_slots"] += 1
    elif fault == "session_disabled":
        serving["enable_session_radix_cache"] = False
    elif fault == "extra_ordering":
        serving["enable_beliefkv_admission"] = True
    elif fault == "path_cap":
        serving["mamba_max_states_per_path"] = 2
    elif fault == "diagnostic_enabled":
        telemetry["checkpoint_path_shadow"] = True
    elif fault == "arrival_stream":
        manifest["arrival_schedule_sha256"] = "changed"
    elif fault == "native_policy":
        manifest["config"]["native_policy_baseline"] = False
    elif fault == "writer_error":
        telemetry["writer_error"] = "broken"
    with pytest.raises(ValueError):
        startup_configuration(serving, telemetry, manifest, capacity, plan)
