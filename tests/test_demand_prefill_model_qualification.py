from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from scripts import qualify_demand_prefill_model as qualifier
from beliefkv.experiments.native_engine_variant import DEMAND_PREFILL_PIPELINE_TREE, engine_patch


def observations():
    stage = {
        "event": "native_demand_restore_staged", "monotonic_ms": 10.,
        "native_submit_ts_ms": 100., "monotonic_clock_domain": "clock",
        "native_full_tokens": qualifier.PREFIX_TOKENS,
        "requests": [{"request_id": "demand-model:b_demand"}],
    }
    member = {
        "request_id": "demand-model:a_resident", "workflow_id": "demand-model",
        "invocation_id": "a", "context_id": "a", "context_epoch": 0, "output_tokens": 20,
    }
    progress = {
        "event": "native_demand_restore_decode_completed_pending",
        "native_submit_ts_ms": 100., "monotonic_clock_domain": "clock",
        "ack_pending_at_completed_batch_observation": True,
        "batch_launch_monotonic_ms": 11., "completion_observed_monotonic_ms": 12.,
        "forward_iter": 17, "completed_decode_requests": [member],
    }
    worker = {
        "event": "gpu_service_sample", "phase": "decode", "forward_iter": 17,
        "monotonic_clock_domain": "clock", "request_samples": [{**member, "token_delta": 1}],
    }
    return [stage, progress], [worker]


def test_demand_progress_requires_real_post_stage_model_results_and_pending_native_ack():
    journals, workers = observations()
    result = qualifier.verify_progress(journals, workers)
    assert result["resident_decode_completed_while_native_ack_pending"]
    assert result["ordinary_native_h2d_without_artificial_delay"]
    assert result["completed_batches"][0]["forward_iter"] == 17


@pytest.mark.parametrize("fault", (
    "no_stage", "repeated_stage", "short_host_prefix", "no_completed_decode", "ack_done",
    "wrong_transfer", "wrong_clock", "old_launch", "early_observation", "cold_request_only",
    "missing_worker", "wrong_worker", "prefill_worker", "no_token_progress", "wrong_context",
))
def test_selection_logs_or_unrelated_workers_cannot_qualify_model_overlap(fault):
    journals, workers = observations()
    if fault == "no_stage":
        journals.pop(0)
    elif fault == "repeated_stage":
        journals.append(deepcopy(journals[0]))
    elif fault == "short_host_prefix":
        journals[0]["native_full_tokens"] = 1
    elif fault == "no_completed_decode":
        journals.pop()
    elif fault == "ack_done":
        journals[1]["ack_pending_at_completed_batch_observation"] = False
    elif fault == "wrong_transfer":
        journals[1]["native_submit_ts_ms"] += 1.
    elif fault == "wrong_clock":
        journals[1]["monotonic_clock_domain"] = "foreign"
    elif fault == "old_launch":
        journals[1]["batch_launch_monotonic_ms"] = 9.
    elif fault == "early_observation":
        journals[1]["completion_observed_monotonic_ms"] = 10.
    elif fault == "cold_request_only":
        journals[1]["completed_decode_requests"][0]["request_id"] = "demand-model:b_demand"
    elif fault == "missing_worker":
        workers.clear()
    elif fault == "wrong_worker":
        workers[0]["forward_iter"] += 1
    elif fault == "prefill_worker":
        workers[0]["phase"] = "prefill"
    elif fault == "no_token_progress":
        workers[0]["request_samples"][0]["token_delta"] = 0
    else:
        workers[0]["request_samples"][0]["context_epoch"] += 1
    with pytest.raises(ValueError, match="model|decode|Host"):
        qualifier.verify_progress(journals, workers)


def outputs():
    return {
        name: {"output_token_ids": [17, 18], "output_logprobs": [-.2, -.1]}
        for name in qualifier.LABELS
    }


def test_both_resident_and_restored_models_require_independent_reference_outputs():
    assert qualifier.compare_outputs(outputs())["actual_greedy_tokens_equal"]


def test_native_diagnostic_reports_output_differences_without_qualifying_the_model():
    rows = outputs()
    rows["a_resident"]["output_token_ids"][1] += 1
    rows["b_demand"]["output_logprobs"][0] -= .1
    diagnostics = qualifier.output_diagnostics(rows)
    assert diagnostics["a_resident"]["first_differing_output_ordinal"] == 2
    assert not diagnostics["a_resident"]["output_token_ids_equal"]
    assert diagnostics["b_demand"]["maximum_logprob_delta"] > .05
    with pytest.raises(ValueError, match="greedy"):
        qualifier.compare_outputs(rows)


def test_continuation_reference_requires_both_complete_independent_initial_histories():
    rows = outputs()
    rows.update({name: deepcopy(rows[original])
                 for name, original in qualifier.REFERENCE_INITIALS.items()})
    assert set(qualifier.compare_outputs(rows)["maximum_logprob_delta"]) == {
        "a_resident", "b_demand", "a_initial", "b_initial",
    }
    rows["a_reference_initial"]["output_token_ids"][0] += 1
    with pytest.raises(ValueError, match="greedy"):
        qualifier.compare_outputs(rows)
    rows.pop("b_reference_initial")
    with pytest.raises(ValueError, match="every unforced"):
        qualifier.compare_outputs(rows)


def test_reference_initial_calls_keep_eight_tokens_and_long_continuations_keep_512():
    assert qualifier.generation_document("a_reference_initial")["generated_tokens"] == 8
    assert qualifier.generation_document("b_reference_initial")["generated_tokens"] == 8
    for label in ("a_resident", "a_reference"):
        assert qualifier.generation_document(label)["generated_tokens"] == 512


@pytest.mark.parametrize("deterministic", (False, True))
def test_numerical_mode_is_explicit_and_observed_at_actual_model_server(deterministic):
    flags = qualifier.numerical_flags(61440, deterministic)
    assert ("--enable-deterministic-inference" in flags) is deterministic
    info = {
        "enable_deterministic_inference": deterministic, "attention_backend": "fa3",
        "sampling_backend": "pytorch" if deterministic else "flashinfer",
        "max_running_requests": 24, "max_total_tokens": 61440,
        "context_length": 65536, "enable_session_radix_cache": True,
        "disable_radix_cache": False, "enable_custom_logit_processor": False,
        "hicache_io_backend": "kernel",
    }
    assert qualifier.validate_model_parameters(
        info, deterministic=deterministic, capacity=61440,
    ) == info
    info["enable_deterministic_inference"] = not deterministic
    with pytest.raises(ValueError, match="actual model"):
        qualifier.validate_model_parameters(info, deterministic=deterministic, capacity=61440)


@pytest.mark.parametrize("field", (
    "sampling_backend", "max_running_requests", "enable_session_radix_cache",
    "disable_radix_cache", "enable_custom_logit_processor", "hicache_io_backend",
))
def test_deterministic_numerics_cannot_hide_changed_actual_cache_or_sampler(field):
    info = {
        "enable_deterministic_inference": True, "attention_backend": "fa3",
        "sampling_backend": "pytorch", "max_running_requests": 24, "max_total_tokens": 61440,
        "context_length": 65536, "enable_session_radix_cache": True,
        "disable_radix_cache": False, "enable_custom_logit_processor": False,
        "hicache_io_backend": "kernel",
    }
    info.pop(field)
    with pytest.raises(ValueError, match="actual model"):
        qualifier.validate_model_parameters(info, deterministic=True, capacity=61440)


@pytest.mark.parametrize("fault", ("missing", "resident_token", "restored_token", "logprob", "nan"))
def test_correct_usage_cannot_hide_wrong_model_tokens_or_logprobs(fault):
    rows = outputs()
    if fault == "missing":
        rows.pop("a_reference")
    elif fault in ("resident_token", "restored_token"):
        rows["a_resident" if fault == "resident_token" else "b_demand"]["output_token_ids"][0] += 1
    else:
        rows["a_resident"]["output_logprobs"][0] = float("nan") if fault == "nan" else -.5
    with pytest.raises(ValueError, match="qualifier|greedy|logprob"):
        qualifier.compare_outputs(rows)


def test_actual_launcher_accepts_the_registered_demand_pipeline_patch():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/launch_qwen35_native_v0520.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    patch = engine_patch(root, DEMAND_PREFILL_PIPELINE_TREE)
    assert (
        'demand_prefill_pipeline)\n'
        f'      sglang_patch="${{BELIEFKV_ROOT}}/patches/{patch.name}"\n'
        '      ;;'
    ) in script.read_text()
