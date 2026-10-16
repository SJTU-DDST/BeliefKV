from copy import deepcopy
from pathlib import Path

import pytest

from scripts import start_v78_native_order_20261016 as candidate
from tests.test_native_close_baseline import reference
from tests.test_resume_semantic_h2d_ab import plan as resume_plan


@pytest.fixture
def v76(reference):
    return {
        **deepcopy(reference), "order": ["reactive"],
        "code_commit": "old", "sglang_patch_sha256": "patch",
        "policy_configuration": {"reactive": deepcopy(candidate.POLICY)},
        "workload_manifest": "/old/manifest",
        "semantic_artifact": "/models/semantic.json",
        "h2d_seed_artifact": "/models/h2d.json",
        "tool_timing_artifact": "/models/tool.json",
        "transfer_service_seed": "/models/service.json",
    }


def make_plan(v76):
    return candidate.build_plan(v76, commit="new", patch_sha="patch", manifest=Path("/new/manifest"))


def test_plan_keeps_v76_application_resource_and_policy_fields(v76):
    before = deepcopy(v76)
    plan = make_plan(v76)
    assert v76 == before
    assert all(plan[field] == v76[field] for field in candidate.MATCHED_FIELDS)
    assert plan["policy_configuration"]["reactive"] == {
        **candidate.POLICY, "prefill_ordering_mode": "native",
    }
    assert plan["prefill_ordering_mode"] == "native"
    assert plan["capacity_bypass_policy"] == "native_capacity_only;runtime_custom_bypass_disabled"
    plan["arrival_schedule"][0]["offset_seconds"] = 999
    assert v76 == before


@pytest.mark.parametrize("field,value", [
    ("root_count", 108), ("host_split", "30:70"), ("max_completion_tokens", 16384),
    ("sandbox_command_timeout_s", 3600), ("completion_gate_enabled", True),
    ("workflow_arrival_rate_per_second", 1.), ("order", ["predictive_h2d"]),
])
def test_plan_rejects_changed_v76_scenario(v76, field, value):
    v76[field] = value
    with pytest.raises(ValueError):
        make_plan(v76)


@pytest.mark.parametrize("change", ["engine", "writeback", "prefetch", "prepare"])
def test_plan_rejects_mixing_other_engine_or_policy_changes(v76, change):
    if change == "engine":
        v76["sglang_patch_sha256"] = "different"
    elif change == "writeback":
        v76["shared_system_configuration"]["write_back_batch_nodes"] = 1
    else:
        v76["policy_configuration"]["reactive"][
            "predictive_h2d" if change == "prefetch" else "prepare_host"
        ] = change == "prefetch"
    with pytest.raises(ValueError):
        make_plan(v76)


def test_environment_enables_native_order_and_keeps_v76_nonordering_work(v76):
    source = resume_plan()
    before = deepcopy(source)
    environment = candidate.collection_environment(make_plan(v76), source)
    assert source == before
    assert environment["ARM_ORDER"] == "reactive"
    assert environment["BELIEFKV_NATIVE_PREFILL_ORDER"] == "1"
    assert environment["PREPARE_HOST"] == "1"
    assert environment["EXECUTION_HANDOFF"] == "0"
    assert environment["ENABLE_TOOL_TIMING"] == "1"
    assert environment["RESUME_PENDING"] == "1"
    assert environment["WORKLOAD_MANIFEST"] == "/new/manifest"
    assert environment["SGLANG_PATCH_FLAVOR"] == "staging"
    assert environment["SGLANG_NATIVE_SESSION_CLOSE_RECLAIM"] == "0"
    assert environment["BELIEFKV_RECORD_OUTPUT_TOKEN_IDS"] == "0"
    assert environment["NATIVE_REFERENCE_RUN"] == str(candidate.REFERENCE)


@pytest.mark.parametrize("change", ["digest", "order", "duplicate"])
def test_plan_rejects_changed_arrival_identities_or_digest(v76, change):
    if change == "digest":
        v76["arrival_schedule_sha256"] = "changed"
    elif change == "order":
        v76["arrival_schedule"].reverse()
    else:
        v76["workload_instance_ids_in_manifest_order"][1] = v76["workload_instance_ids_in_manifest_order"][0]
    with pytest.raises(ValueError, match="arrival"):
        make_plan(v76)


def test_main_helper_cannot_substitute_for_the_frozen_checkout():
    with pytest.raises(ValueError, match="exact frozen V78"):
        candidate.verify_source({"helper": {"path": "/other/helper", "sha256": "different"}})


@pytest.mark.parametrize("supervisor,driver", [(True, False), (False, True), (True, True)])
def test_live_v77_is_waited_for_even_if_state_says_completed(supervisor, driver):
    assert candidate.prerequisite_status({"status": "completed"}, supervisor, driver) == "waiting_for_complete_v77"


@pytest.mark.parametrize("status", ["running_native_close", "stopped_for_diagnosis", "driver_failed"])
def test_missing_v77_handle_without_completion_is_not_a_restart_signal(status):
    with pytest.raises(ValueError, match="V77 ended"):
        candidate.prerequisite_status({"status": status}, False, False)


def test_complete_v77_allows_resource_check():
    assert candidate.prerequisite_status({"status": "completed"}, False, False) == "ready_for_resource_check"


@pytest.fixture
def startup(v76):
    plan = make_plan(v76)
    return {
        "serving": {
            "status": "ready", "max_running_requests": 48, "context_length": 131072,
            "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
            "enable_mixed_chunk": False, "enable_prefill_delayer": False,
            "min_free_slots_delay": None, "enable_beliefkv_admission": True,
        },
        "telemetry": {
            "writer_error": None, "record_output_token_ids": False,
            "write_back_batch": {"max_nodes": 32},
        },
        "runtime": {
            **candidate.RUNTIME, "counts": {"native_prefill_order_plans": 1},
        },
        "manifest": {
            "arrival_schedule": deepcopy(plan["arrival_schedule"]),
            "arrival_schedule_sha256": plan["arrival_schedule_sha256"],
            "client_inflight_root_window": 156, "config": {"concurrency": 156},
        },
        "capacity": {"full": 10, "mamba": 10},
        "reference_capacity": {"full": 10, "mamba": 10}, "plan": plan,
    }


def test_startup_requires_exercised_native_order_despite_configured_priorities(startup):
    candidate.startup_configuration(**startup)
    assert startup["runtime"]["resident_first"] is True
    assert startup["runtime"]["final_stage_priority"] is True


@pytest.mark.parametrize("change", [
    "workflow_order", "unused_order", "predictive", "resident_order", "attained_work",
    "close_gc", "output_ids", "pool", "arrivals", "concurrency", "delayer",
    "writeback", "writer", "disabled_ledger", "handoff",
])
def test_startup_rejects_wrong_modes_or_unmatched_runtime_evidence(startup, change):
    if change == "workflow_order":
        startup["runtime"]["prefill_ordering_mode"] = "workflow"
    elif change in ("unused_order", "predictive", "resident_order", "attained_work"):
        key = {
            "unused_order": "native_prefill_order_plans", "predictive": "prefetch_native_issued",
            "resident_order": "resident_first_ordered", "attained_work": "workflow_work_head_ordered",
        }[change]
        startup["runtime"]["counts"][key] = int(change != "unused_order")
    elif change == "close_gc":
        startup["telemetry"]["native_session_close_reclaim"] = {"enabled": True}
    elif change == "output_ids":
        startup["telemetry"]["record_output_token_ids"] = True
    elif change == "pool":
        startup["capacity"]["full"] += 1
    elif change == "arrivals":
        startup["manifest"]["arrival_schedule"][0]["offset_seconds"] += 1
    elif change == "concurrency":
        startup["manifest"]["config"]["concurrency"] = 48
    elif change == "delayer":
        startup["serving"]["enable_prefill_delayer"] = True
    elif change == "writeback":
        startup["telemetry"]["write_back_batch"]["max_nodes"] = 1
    elif change == "writer":
        startup["telemetry"]["writer_error"] = "failed"
    else:
        startup["runtime"]["physical_disabled" if change == "disabled_ledger" else "execution_handoff"] = True
    with pytest.raises(ValueError):
        candidate.startup_configuration(**startup)
