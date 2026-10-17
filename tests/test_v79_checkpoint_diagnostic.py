from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from beliefkv.experiments.arrival_schedule import arrival_schedule_digest
from scripts import start_v79_checkpoint_diagnostic_20261016 as driver
from scripts.start_v79_checkpoint_diagnostic_20261016 import (
    EXPECTED_SETTINGS, MATCHED_FIELDS, POLICY, build_plan, collection_environment,
    complete_collection, continuation_sample, matched_predecessor_plan,
    startup_configuration,
)


def reference():
    result = {key: None for key in MATCHED_FIELDS}
    result.update(deepcopy(EXPECTED_SETTINGS))
    ids = [f"task-{index}" for index in range(156)]
    rows = [
        {"instance_id": identity, "offset_seconds": index * 15.0}
        for index, identity in enumerate(ids)
    ]
    result.update(
        workload_instance_ids_in_manifest_order=ids, arrival_schedule=rows,
        arrival_schedule_sha256=arrival_schedule_digest(rows),
        shared_system_configuration={"write_back_batch_nodes": 32},
        sglang_patch_sha256="unchanged-engine",
    )
    return result


def plan():
    return build_plan(reference(), commit="frozen", manifest="/fixed/manifest.json")


def test_full_diagnostic_keeps_workload_and_policy_and_does_not_mutate_reference():
    before = reference()
    saved = deepcopy(before)
    result = build_plan(before, commit="candidate", manifest="/manifest")
    assert before == saved
    assert result["order"] == ["native"]
    assert result["policy_configuration"] == {"native": POLICY}
    assert result["checkpoint_path_diagnostic"]["generation_prefix_compatibility"] is True
    assert result["checkpoint_path_diagnostic"]["raw_output_token_logging"] is False
    assert all(result[key] == before[key] for key in MATCHED_FIELDS)
    assert result["formal_acceptance"] is False
    environment = collection_environment(result)
    assert environment["BELIEFKV_CHECKPOINT_PATH_SHADOW"] == "1"
    assert environment["BELIEFKV_RECORD_OUTPUT_TOKEN_IDS"] == "0"
    assert environment["NATIVE_POLICY_BASELINE"] == "1"
    assert environment["AB_MODE"] == "off"
    assert environment["SGLANG_HICACHE_WRITE_BACK_BATCH_FRONTIER"] == "0"
    assert environment["SGLANG_NATIVE_SESSION_CLOSE_RECLAIM"] == "0"


def test_frozen_script_keeps_authoritative_main_and_experiment_paths(tmp_path):
    relocated = tmp_path / ".worktrees" / "candidate" / "scripts" / Path(driver.__file__).name
    relocated.parent.mkdir(parents=True)
    relocated.write_text(Path(driver.__file__).read_text())
    spec = importlib.util.spec_from_file_location("_relocated_v79_driver", relocated)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROOT == relocated.parents[1]
    assert module.BASE == driver.BASE
    assert module.MAIN == driver.MAIN
    assert module.APP == driver.APP
    assert module.RUN == driver.RUN
    assert module.CPU == driver.CPU


@pytest.mark.parametrize("field,value", [
    ("root_count", 108), ("max_completion_tokens", 16384),
    ("sandbox_command_timeout_s", 300), ("host_split", "70:30"),
    ("mamba_full_memory_ratio", 0.5), ("server_running", 32),
    ("workflow_arrival_mode", "batched"),
])
def test_diagnostic_cannot_silently_change_workload(field, value):
    changed = reference()
    changed[field] = value
    with pytest.raises(ValueError):
        build_plan(changed, commit="candidate", manifest="/manifest")


@pytest.mark.parametrize("field", ["arrival_schedule_sha256", "workload_sha256", "host_split"])
def test_registered_predecessor_must_match_the_new_workload(field):
    previous, candidate = reference(), plan()
    matched_predecessor_plan(previous, candidate)
    previous[field] = "different"
    with pytest.raises(ValueError, match="predecessor tasks"):
        matched_predecessor_plan(previous, candidate)


def fixture_collection():
    ids = [f"task-{index}" for index in range(156)]
    trace = {
        "workflow_lifecycle_valid": True, "llm_pairing_valid": True, "tool_pairing_valid": True,
    }
    summary = {
        "workflow_count": 156, "completed_workflows": 156,
        "native_agent_jct_eligible_workflows": 156,
        "workflows": [
            {"instance_id": identity, "outcome": "completed",
             "native_agent_jct_eligible": True, "error": None, "trace": deepcopy(trace)}
            for identity in ids
        ],
    }
    telemetry = {
        "writer_error": None, "dropped_records": 0, "failed_records": 0,
        "writer_queue_depth": 0, "pending_batch_count": 0, "pending_request_count": 0,
    }
    return summary, telemetry, ids, list(reversed(ids))


def test_complete_collection_preserves_successful_native_gate():
    args = fixture_collection()
    assert complete_collection(*args, 0)["native_gate_passed"] is True


@pytest.mark.parametrize("outcome", ["completed", "incomplete"])
def test_gate_failure_is_collected_without_relabeling_or_waiving_it(outcome):
    args = fixture_collection()
    args[0]["workflows"][0].update(outcome=outcome, native_agent_jct_eligible=False)
    args[0]["native_agent_jct_eligible_workflows"] = 155
    args[0]["completed_workflows"] = 155 + int(outcome == "completed")
    before = deepcopy(args)
    evidence = complete_collection(*args, 1)
    assert args == before
    assert evidence["native_gate_passed"] is False
    assert evidence["original_client_exit_code"] == 1


@pytest.mark.parametrize("kind", [
    "missing_task", "duplicate_finish", "wrong_task", "task_error", "invalid_pairing",
    "wrong_completed", "wrong_eligible", "missing_telemetry", "pending_request",
    "writer_error", "unexpected_exit", "gate_exit_disagrees",
])
def test_partial_or_faulty_collection_cannot_authorize_next_experiment(kind):
    summary, telemetry, expected, finished = fixture_collection()
    exit_code = 0
    if kind == "missing_task":
        summary["workflows"].pop()
    elif kind == "duplicate_finish":
        finished[0] = finished[1]
    elif kind == "wrong_task":
        summary["workflows"][0]["instance_id"] = "other"
    elif kind == "task_error":
        summary["workflows"][0]["error"] = "server failed"
    elif kind == "invalid_pairing":
        summary["workflows"][0]["trace"]["llm_pairing_valid"] = False
    elif kind == "wrong_completed":
        summary["completed_workflows"] = 155
    elif kind == "wrong_eligible":
        summary["native_agent_jct_eligible_workflows"] = 155
    elif kind == "missing_telemetry":
        telemetry.pop("pending_request_count")
    elif kind == "pending_request":
        telemetry["pending_request_count"] = 1
    elif kind == "writer_error":
        telemetry["writer_error"] = "disk full"
    else:
        exit_code = 2 if kind == "unexpected_exit" else 1
    with pytest.raises(ValueError):
        complete_collection(summary, telemetry, expected, finished, exit_code)


def startup():
    candidate = plan()
    serving = {
        "status": "ready", "max_running_requests": 48, "context_length": 131072,
        "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
        "enable_mixed_chunk": False, "enable_prefill_delayer": False,
        "min_free_slots_delay": None, "enable_beliefkv_admission": False,
        "mamba_max_states_per_path": -1, "mamba_full_memory_ratio": 0.9,
        "mamba_radix_cache_strategy": "extra_buffer",
    }
    telemetry = {
        "checkpoint_path_shadow": True, "record_output_token_ids": False,
        "writer_error": None, "native_session_close_reclaim": None,
        "write_back_batch": {"max_nodes": 32},
    }
    manifest = {
        "arrival_schedule": candidate["arrival_schedule"],
        "arrival_schedule_sha256": candidate["arrival_schedule_sha256"],
        "client_inflight_root_window": 156, "config": {"concurrency": 156},
    }
    return serving, telemetry, manifest, {"full": 100, "mamba": 90}, candidate, {"full": 100, "mamba": 90}


def test_actual_native_diagnostic_startup():
    assert startup_configuration(*startup())["enable_beliefkv_admission"] is False


@pytest.mark.parametrize("kind", [
    "probe_off", "raw_ids", "close_gc", "admission", "path_cap", "delayer",
    "mamba_strategy", "batch", "capacity", "arrival",
])
def test_diagnostic_startup_requires_actual_behavior_not_environment_only(kind):
    args = list(startup())
    if kind == "probe_off":
        args[1]["checkpoint_path_shadow"] = False
    elif kind == "raw_ids":
        args[1]["record_output_token_ids"] = True
    elif kind == "close_gc":
        args[1]["native_session_close_reclaim"] = {"enabled": True}
    elif kind == "admission":
        args[0]["enable_beliefkv_admission"] = True
    elif kind == "path_cap":
        args[0]["mamba_max_states_per_path"] = 2
    elif kind == "delayer":
        args[0]["enable_prefill_delayer"] = True
    elif kind == "mamba_strategy":
        args[0]["mamba_radix_cache_strategy"] = "other"
    elif kind == "batch":
        args[1]["write_back_batch"]["max_nodes"] = 1
    elif kind == "capacity":
        args[3]["mamba"] -= 1
    else:
        args[2]["arrival_schedule_sha256"] = "changed"
    with pytest.raises(ValueError):
        startup_configuration(*args)


def test_continuation_observer_uses_complete_rows_and_actual_probe(tmp_path):
    path = tmp_path / "events.jsonl"
    assert continuation_sample(path) is None
    record = {
        "event": "generation_continuation_prefix_probe",
        "common_prior_transcript_prefix_tokens": 10,
    }
    path.write_text('{"event":"other"}\n' + json.dumps(record) + '\n{"partial":')
    assert continuation_sample(path) == record
