from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.benchmark_qwen35_execution import (
    cuda_activities,
    diagnostic_inputs,
    predecessor_ready,
    validate_component_shape,
)


PLAN = {"run_root": "/run/v166", "order": ["full", "scheduler_only"]}
SHAPE = {
    "hidden_size": 2048, "linear_num_key_heads": 16,
    "linear_num_value_heads": 32, "linear_key_head_dim": 128,
    "linear_value_head_dim": 128,
}


@pytest.mark.parametrize("phase", (
    "preflight", "waiting_previous_causal_comparison",
    "waiting_resources_full", "starting_full", "running_full",
    "waiting_resources_scheduler_only", "starting_scheduler_only", "running_scheduler_only",
))
def test_live_predecessor_always_waits(phase):
    state = {"run_root": PLAN["run_root"], "status": phase,
             "watcher": {"pid": 11}, "owned": [{"pid": 12}]}
    assert not predecessor_ready(PLAN, state, lambda row: True)


@pytest.mark.parametrize("field", ("watcher", "server", "driver", "owned"))
def test_completed_comparison_keeps_gpu_until_every_owned_process_exits(field):
    state = {
        "run_root": PLAN["run_root"], "status": "collected_performance_review_pending",
        "completed_arms": PLAN["order"],
    }
    state[field] = [{"pid": 12}] if field == "owned" else {"pid": 12}
    assert not predecessor_ready(PLAN, state, lambda row: row["pid"] == 12)
    assert predecessor_ready(PLAN, state, lambda row: False)


def test_completed_first_arm_does_not_authorize_gpu_diagnostic():
    state = {
        "run_root": PLAN["run_root"], "status": "collected_performance_review_pending",
        "completed_arms": ["full"],
    }
    with pytest.raises(ValueError, match="every registered arm"):
        predecessor_ready(PLAN, state, lambda row: False)


def test_missing_live_handle_is_an_error_and_never_restarts_comparison():
    state = {"run_root": PLAN["run_root"], "status": "running_full", "watcher": {"pid": 11}}
    with pytest.raises(ValueError, match="disappeared"):
        predecessor_ready(PLAN, state, lambda row: False)


@pytest.mark.parametrize("status", ("stopped_for_investigation", "failed", "unknown"))
def test_failed_or_unrecognized_predecessor_does_not_start_gpu_work(status):
    with pytest.raises(ValueError, match="not a completed or live"):
        predecessor_ready(PLAN, {"run_root": PLAN["run_root"], "status": status}, lambda row: False)


def test_wrong_run_state_is_rejected():
    with pytest.raises(ValueError, match="another run"):
        predecessor_ready(PLAN, {"run_root": "/run/other"}, lambda row: False)


def test_geometry_comes_from_actual_model_configuration():
    assert validate_component_shape({"text_config": SHAPE}) == SHAPE


def test_cuda_toolchain_comes_from_flat_actual_comparison_environment():
    prefix, model = diagnostic_inputs({
        "reference_environment": {"CUDA_HOME": "/toolchains/cu13"},
        "source_server_parameters": {"model_path": "/models/qwen35"},
    })
    assert str(prefix) == "/toolchains/cu13"
    assert str(model) == "/models/qwen35/config.json"


@pytest.mark.parametrize("value", (None, "relative/toolchain", 123))
def test_missing_or_relative_cuda_toolchain_is_rejected(value):
    with pytest.raises(ValueError, match="CUDA prefix"):
        diagnostic_inputs({
            "reference_environment": {"CUDA_HOME": value},
            "source_server_parameters": {"model_path": "/models/qwen35"},
        })


@pytest.mark.parametrize("field,value", (
    ("hidden_size", 0), ("linear_num_key_heads", True),
    ("linear_num_value_heads", 33), ("linear_key_head_dim", 64),
))
def test_invalid_or_unqualified_geometry_is_rejected(field, value):
    config = deepcopy(SHAPE)
    config[field] = value
    with pytest.raises(ValueError):
        validate_component_shape({"text_config": config})


def test_cuda_activity_accounting_does_not_double_count_cpu_parents():
    events = [
        SimpleNamespace(name="parent", device_type="DeviceType.CPU", device_time_total=1000.0),
        SimpleNamespace(name="core", device_type="DeviceType.CUDA", device_time_total=12.0),
        SimpleNamespace(name="core", device_type="DeviceType.CUDA", device_time_total=8.0),
        SimpleNamespace(name="copy", device_type="DeviceType.CUDA", device_time_total=3.0),
    ]
    assert cuda_activities(events) == [
        {"name": "core", "calls": 2, "total_us": 20.0},
        {"name": "copy", "calls": 1, "total_us": 3.0},
    ]


@pytest.mark.parametrize("duration", (float("nan"), float("inf"), -1.0))
def test_invalid_profiler_activity_is_not_silently_reported_as_zero(duration):
    with pytest.raises(ValueError, match="invalid CUDA"):
        cuda_activities([SimpleNamespace(
            name="bad", device_type="DeviceType.CUDA", device_time_total=duration,
        )])


def test_missing_cuda_activity_is_not_accepted_as_fast_execution():
    with pytest.raises(ValueError, match="no timed CUDA"):
        cuda_activities([])
