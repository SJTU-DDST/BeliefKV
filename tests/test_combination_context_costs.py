from copy import deepcopy
from dataclasses import replace

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate as Candidate, CombinationCosts as Costs,
    CombinationResources as Resources, _evaluate, choose_combination,
)
from scripts.prepare_calibrated_combination_oracle import context_prefill_costs


def costs():
    return Costs(
        .083, .004, (1., 2., 3.), 40000.,
        prefill_ms_per_token_by_context=((8192, .03), (32768, .06), (262144, .3)),
    )


@pytest.mark.parametrize("tokens,context,expected", (
    (0, 65536, 0.), (100, 100, 3.), (100, 10000, 6.),
    (200, 8292, 9.), (200, 32868, 36.), (40000, 40000, 3889.92),
))
def test_prefill_curve_integrates_only_missing_input_over_context_ranges(tokens, context, expected):
    assert costs().prefill_cost(tokens, context) == pytest.approx(expected)


@pytest.mark.parametrize("tokens,context", ((101, 100), (1, 262145), (-1, 100), (1, 1.5)))
def test_unsupported_input_geometry_is_rejected(tokens, context):
    with pytest.raises(ValueError, match="outside frozen cost support"):
        costs().prefill_cost(tokens, context)


@pytest.mark.parametrize("curve", (
    ((8192, .03), (8192, .06)), ((0, .03),), ((8192, 0.),),
    ((8192, .06), (32768, .03)), ((8192, float("nan")),), ((8192, True),),
))
def test_cost_curve_rejects_unsupported_or_nonmonotone_buckets(curve):
    with pytest.raises(ValueError, match="context prefill cost"):
        replace(costs(), prefill_ms_per_token_by_context=curve)


def test_legacy_constant_cost_profile_keeps_original_evaluation_exactly():
    legacy = replace(costs(), prefill_ms_per_token_by_context=())
    assert legacy.prefill_cost(100, 200000) == 100 * .083
    row = Candidate("a", "a", 100, 0, 0, 1., "a", input_tokens=20, context_tokens=100)
    resources = Resources(200, 10000, 2048, 2)
    assert _evaluate([row], resources, legacy)["incremental_cost_ms"] == 20 * .083 + 16.


def test_context_cost_changes_set_choice_without_changing_workflow_weight_or_capacity():
    rows = [
        Candidate("long-context", "a", 20100, 0, 0, 1., "a", input_tokens=100, context_tokens=20000),
        Candidate("short-context", "b", 1100, 0, 1, 1., "b", input_tokens=100, context_tokens=1000),
    ]
    resources = Resources(20100, 10000, 2048, 1)
    legacy = choose_combination(
        rows, ["long-context"], resources, replace(costs(), prefill_ms_per_token_by_context=()),
        clock=lambda: 0,
    )
    assert legacy["selected"] == ("long-context",)
    actual = choose_combination(rows, ["long-context"], resources, costs(), clock=lambda: 0)
    assert actual["selected"] == ("short-context",)
    assert actual["selected_evaluation"]["benefit"] == actual["reference_evaluation"]["benefit"] == 1.
    assert actual["selected_evaluation"]["input_tokens"] == actual["reference_evaluation"]["input_tokens"] == 100


def test_context_integration_runs_once_per_candidate_not_per_enumerated_subset(monkeypatch):
    calls, original = [], Costs.prefill_cost

    def observe(self, tokens, context):
        calls.append((tokens, context))
        return original(self, tokens, context)

    monkeypatch.setattr(Costs, "prefill_cost", observe)
    rows = [
        Candidate(str(index), str(index), 1100, 0, index, 1., str(index),
                  input_tokens=100, context_tokens=1000)
        for index in range(3)
    ]
    result = choose_combination(
        rows, ["0"], Resources(2200, 10000, 2048, 2), costs(), clock=lambda: 0,
    )
    assert result["search_complete"] and result["evaluated_subsets"] == 7
    assert len(calls) == 3


def calibration():
    profile = {
        "prefill_ms_per_token": .083, "h2d_ms_per_token": .004,
        "decode_ms_by_width": [1., 2., 3.], "reference_context_tokens": 40000.,
    }
    prefix = {"complete_prefix_bytes": 10, "complete_prefix_sha256": "sealed", "first_read_limit_bytes": 10}
    original = {
        "registration": "/control", "completed_control_cost_proxy": profile,
        "input_jsonl_prefixes": {"/runtime_audit.jsonl": prefix},
    }
    diagnostic = {
        "source_registration": "/control", "frozen_current_prefill_ms_per_token": profile["prefill_ms_per_token"],
        "input_jsonl_prefixes": deepcopy(original["input_jsonl_prefixes"]),
        "single_request_prefill_batches_at_least_256_tokens_by_context_after_upper_bound": {
            name: {"batch_count": 50, "ms_per_input_token": {"count": 50, "p50": .03 + index * .04}}
            for index, name in enumerate(("8192", "32768", "65536", "131072", "at_least_131072"))
        },
        "scope": {"same_completed_control": True, "GPU_action": False,
                  "sequence_tokens_before_is_native_prefix_not_total_input": True},
    }
    return original, diagnostic


def test_context_calibration_keeps_constant_intrinsic_proxy_and_all_supported_ranges():
    original, diagnostic = calibration()
    result = context_prefill_costs(diagnostic, original)
    assert result["prefill_ms_per_token"] == original["completed_control_cost_proxy"]["prefill_ms_per_token"]
    assert [bound for bound, _rate in result["prefill_ms_per_token_by_context"]] == [
        8192, 32768, 65536, 131072, 262144,
    ]
    assert "prefill_ms_per_token_by_context" not in original["completed_control_cost_proxy"]


def test_artifact_binds_context_admission_costs_without_changing_intrinsic_path_proxy():
    from scripts.prepare_calibrated_combination_oracle import calibrated_artifact
    from tests.test_calibrated_combination_preparation import calibration as base_calibration, original

    report = base_calibration()
    observed, diagnostic = calibration()
    report.update(registration=observed["registration"], input_jsonl_prefixes=observed["input_jsonl_prefixes"])
    diagnostic["frozen_current_prefill_ms_per_token"] = report["completed_control_cost_proxy"]["prefill_ms_per_token"]
    before = original()
    result = calibrated_artifact(
        before, report, calibration_path="/costs", calibration_sha256="a" * 64,
        context_calibration=diagnostic, context_calibration_path="/context-costs",
        context_calibration_sha256="b" * 64,
    )
    assert result["requests"] == before["requests"]
    assert result["workflow_graphs"] == before["workflow_graphs"]
    assert result["model_prefill_seconds_per_token"] == .00008
    assert result["admission_cost_profile"]["costs"]["prefill_ms_per_token_by_context"]
    binding = result["calibrated_model_time"]["admission_prefill_context_calibration"]
    assert binding["report_sha256"] == "b" * 64
    assert binding["remaining_intrinsic_model_path_uses_original_global_prefill_proxy"]


@pytest.mark.parametrize("change", ("source", "prefix", "sparse", "geometry", "bucket"))
def test_context_calibration_rejects_foreign_incomplete_or_sparse_observations(change):
    original, diagnostic = calibration()
    if change == "source":
        diagnostic["source_registration"] = "/other"
    elif change == "prefix":
        diagnostic["input_jsonl_prefixes"]["/runtime_audit.jsonl"]["complete_prefix_bytes"] = 9
    elif change == "sparse":
        diagnostic["single_request_prefill_batches_at_least_256_tokens_by_context_after_upper_bound"]["8192"]["batch_count"] = 29
    elif change == "geometry":
        diagnostic["scope"]["sequence_tokens_before_is_native_prefix_not_total_input"] = False
    else:
        diagnostic["single_request_prefill_batches_at_least_256_tokens_by_context_after_upper_bound"].pop("32768")
    with pytest.raises(ValueError):
        context_prefill_costs(diagnostic, original)
