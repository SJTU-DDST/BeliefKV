"""Exercise the declared cadence with the actual pinned native scheduler code."""

import __future__
import ast
import os
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from scripts import run_causal_comparison as causal
from scripts.review_prefill_decode_cadence import matching_conditions
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from tests.test_causal_comparison import reference


@pytest.mark.parametrize("value", ("0", "1"))
def test_cadence_is_declared_in_actual_serving_parameters_without_other_resource_changes(value):
    source = {
        "model_path": "fixed", "max_running_requests": 24, "hicache_size": 200,
        "disable_overlap_schedule": False,
    }
    result = causal.prefill_server_parameters(source, {
        "CHUNKED_PREFILL_SIZE": "2048", "ENABLE_MIXED_CHUNK": "0",
        "PREFILL_DECODE_INTERVAL": value,
    })
    assert result == {
        **source, "chunked_prefill_size": 2048, "enable_mixed_chunk": False,
        "prefill_decode_interval": int(value),
    }


@pytest.mark.parametrize("value", ("", "-1", "2", "1.0", "01", None, True, 1))
def test_cadence_rejects_implicit_or_unbounded_settings(value):
    with pytest.raises(ValueError, match="cadence"):
        causal.prefill_server_parameters({}, {"PREFILL_DECODE_INTERVAL": value})


def test_cadence_requires_its_own_nonmixed_experiment():
    with pytest.raises(ValueError, match="cadence"):
        causal.prefill_server_parameters({}, {
            "PREFILL_DECODE_INTERVAL": "1", "ENABLE_MIXED_CHUNK": "1",
        })


def test_undeclared_cadence_preserves_the_historical_parameter_dictionary():
    assert causal.prefill_server_parameters({}, {}) == {
        "chunked_prefill_size": 4096, "enable_mixed_chunk": False,
    }


@pytest.mark.parametrize("arm", ("full", "native_matched", "thunderagent_acting_decay"))
def test_cadence_reaches_each_common_serving_environment(reference, tmp_path, arm):
    reference["arms"]["full"].update(PREFILL_DECODE_INTERVAL="1", MAX_RUNNING_REQUESTS="24")
    settings, _flags, _socket = causal.environment(
        reference, arm, tmp_path, tmp_path / "engine", tmp_path / "arm",
        18992, tmp_path / "plugin", max_running_requests=24,
    )
    assert settings["PREFILL_DECODE_INTERVAL"] == "1"
    assert settings["CHUNKED_PREFILL_SIZE"] == "4096"
    assert settings["MAX_RUNNING_REQUESTS"] == "24"


def native_scheduler(*, interval, running_empty, chunk):
    source = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]) / "python/sglang/srt/managers/scheduler.py"
    cls = next(node for node in ast.parse(source.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == "Scheduler")
    names = ("get_next_batch_to_run", "_should_defer_prefill", "_arm_prefill_decode_interval")
    methods = [
        next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
        for name in names
    ]
    scope = {
        "scheduler_stage_method": lambda _stage: lambda function: function,
        "SCHEDULER_STAGE_GET_NEXT_BATCH": object(),
        "NextBatchPlan": lambda **fields: NS(**fields), "set_schedule_time_batch": Mock(),
    }
    exec(compile(
        ast.Module(body=methods, type_ignores=[]), str(source), "exec",
        flags=__future__.annotations.compiler_flag,
    ), scope)
    decode = NS(is_empty=lambda: running_empty, is_prefill_only=False,
                forward_mode=NS(is_extend=lambda: False))
    prefill = NS(forward_mode=NS(is_extend=lambda: True))
    scheduler = NS(
        process_pending_chunked_abort=Mock(), _process_hicache_events=Mock(),
        beliefkv_runtime=None, enable_fpm=False, dllm_config=None, enable_hisparse=False,
        chunked_req=chunk, _beliefkv_before_batch_selection=lambda *_args: False,
        require_mlp_sync=False, prefill_decode_interval=interval,
        _prefill_decode_interval_remaining=0,
        get_new_batch_prefill=Mock(return_value=NS(batch_to_run=prefill, running_batch=decode)),
        update_running_batch=Mock(return_value=decode),
        dp_attn_adapter=NS(
            maybe_prepare_mlp_sync_batch=lambda batch, **_kwargs: batch,
            maybe_convert_decode_to_extend=lambda batch: batch,
        ),
        ngram_embedding_manager=NS(prepare_for_forward=lambda batch, **_kwargs: batch),
    )
    for name in names:
        setattr(scheduler, name, MethodType(scope[name], scheduler))
    return scheduler, decode, prefill


@pytest.mark.parametrize("interval", (0, 1))
@pytest.mark.parametrize("running_empty", (False, True))
@pytest.mark.parametrize("has_chunk", (False, True))
def test_actual_native_selection_bounds_decode_insertion_and_preserves_chunk_owner(
    interval, running_empty, has_chunk,
):
    class Chunk:
        extend_range = NS(end=32)
        prefix_indices = range(32)

    chunk = Chunk() if has_chunk else None
    scheduler, decode, prefill = native_scheduler(
        interval=interval, running_empty=running_empty, chunk=chunk,
    )
    assert scheduler.get_next_batch_to_run(decode, None).batch_to_run is prefill
    second = scheduler.get_next_batch_to_run(decode, None)
    if interval:
        assert second.batch_to_run is (None if running_empty else decode)
        assert scheduler.get_new_batch_prefill.call_count == 1
        assert scheduler.update_running_batch.call_count == (0 if running_empty else 1)
    else:
        assert second.batch_to_run is prefill
        assert scheduler.get_new_batch_prefill.call_count == 2
    assert scheduler.get_next_batch_to_run(decode, None).batch_to_run is prefill
    assert scheduler.chunked_req is chunk


@pytest.mark.parametrize("change", (None, *TRAFFIC_FIELDS, "policy", "interval"))
def test_complete_cadence_pair_refuses_changes_to_traffic_KV_and_Full_policy(change):
    control = {"order": ["full"], **{name: {"fixed": name} for name in TRAFFIC_FIELDS}}
    control["source_server_parameters"] = {"max_running_requests": 24, "chunked_prefill_size": 2048}
    candidate = {**control, "source_server_parameters": {
        **control["source_server_parameters"], "prefill_decode_interval": 1,
    }}
    old = {"common_environment": {"KV_POOL": "fixed", "TOOL_PREFETCH": "0"}}
    new = {"common_environment": {**old["common_environment"], "PREFILL_DECODE_INTERVAL": "1"}}
    if change == "policy":
        new["common_environment"]["TOOL_PREFETCH"] = "1"
    elif change == "interval":
        candidate["source_server_parameters"]["prefill_decode_interval"] = 2
    elif change == "source_server_parameters":
        candidate["source_server_parameters"]["chunked_prefill_size"] = 1024
    elif change is not None:
        candidate[change] = {"changed": change}
    if change is None:
        matching_conditions(control, candidate, old, new)
    else:
        with pytest.raises(ValueError):
            matching_conditions(control, candidate, old, new)
