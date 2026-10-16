"""Keep historical CPU comparisons honest about timing and live controls."""

from collections import Counter

import pytest

from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import (
    NativePrefillSelection, select_native_prefill_candidates,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
import scripts.benchmark_prefill_live_checks as benchmark
from scripts.benchmark_prefill_live_checks import measure


@pytest.mark.parametrize("method", ("scheduler_step", "plan_native_prefill"))
def test_comparison_rejects_a_missing_baseline_parent_timing_scope(method):
    unmeasured = type(
        "UnmeasuredBaseline", (NativeAdmissionRuntime,),
        {method: getattr(NativeAdmissionRuntime, method).__wrapped__},
    )
    with pytest.raises(AssertionError, match="same parent timing"):
        measure(
            unmeasured, CausalFrontierScheduler,
            workflows=2, profiling=True, scenario="stable", iterations=1,
        )


@pytest.mark.parametrize("profiling", (False, True))
@pytest.mark.parametrize("scenario", (
    "stable", "local_round", "message_control",
    "final_stage_control", "disabled_priority_control",
))
def test_same_runtime_preserves_admission_graph_and_timing_control(scenario, profiling):
    result = measure(
        NativeAdmissionRuntime, CausalFrontierScheduler,
        workflows=2, profiling=profiling, scenario=scenario, iterations=2,
    )
    assert result["plan_selection_graph_and_counters_equal"]
    counts = result["timing_phase_counts_by_arm"]
    assert counts["baseline"] == counts["candidate"]
    if profiling:
        assert counts["baseline"]["scheduler_maintenance"] == 22
        assert counts["baseline"]["admission_plan"] == 22
    else:
        assert counts["baseline"] == {}


def test_complete_cycle_comparison_uses_the_distinct_baseline_selector(monkeypatch):
    calls = Counter()

    def baseline(*args, **kwargs):
        calls["baseline"] += 1
        return select_native_prefill_candidates(*args, **kwargs)

    def current(*args, **kwargs):
        calls["candidate"] += 1
        return select_native_prefill_candidates(*args, **kwargs)

    monkeypatch.setattr(benchmark, "select_native_prefill_candidates", current)
    result = measure(
        NativeAdmissionRuntime, CausalFrontierScheduler, workflows=2,
        profiling=True, scenario="local_round", iterations=2,
        baseline_selection=baseline,
    )
    assert result["plan_selection_graph_and_counters_equal"]
    assert calls == Counter(baseline=22, candidate=22)


def test_complete_cycle_comparison_rejects_selection_drift():
    with pytest.raises(AssertionError, match="selection"):
        measure(
            NativeAdmissionRuntime, CausalFrontierScheduler, workflows=2,
            profiling=False, scenario="stable", iterations=1,
            baseline_selection=lambda *args, **kwargs: NativePrefillSelection((), ()),
        )
