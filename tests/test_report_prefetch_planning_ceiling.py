from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_service import (
    NativeServiceSample, estimate_native_service, native_service_planning_ceiling,
)
from tests.test_online_workload_timing import report_fixture


def test_planning_ceiling_covers_supported_sizes_and_pool_shape_subsets():
    samples = [
        NativeServiceSample(
            2 ** (18 + index % 13), 5. + index, direction=direction,
            shape=shape, enqueue_to_submit_ms=float(index % 7),
        )
        for index in range(64)
        for direction in ("h2d", "d2h")
        for shape in ("unknown", "full", "mamba", "hybrid")
    ] + [(2 ** 20, 120.)] * 3
    for exponent in range(18, 32):
        maximum = 2 ** exponent
        service, enqueue = native_service_planning_ceiling(samples, maximum)
        for size in (1, max(1, maximum // 8), maximum // 2, maximum):
            for shape in ("unknown", "full", "mamba", "hybrid"):
                estimate = estimate_native_service(samples, size, shape=shape)
                if estimate is not None:
                    assert estimate.submit_to_ack_p90_ms <= service
                    assert (estimate.enqueue_to_submit_p90_ms or 0.) <= enqueue


@pytest.mark.parametrize("maximum", (0, -1, True, 1.5))
def test_unknown_or_invalid_payload_does_not_create_a_planning_ceiling(maximum):
    assert native_service_planning_ceiling([(100, 10.)], maximum) is None


def test_d2h_evidence_never_supports_the_h2d_ceiling():
    sample = NativeServiceSample(100, 10., direction="d2h", shape="full")
    assert native_service_planning_ceiling([sample], 100) is None


def install_cost_evidence(runtime, stage):
    runtime._context_tokens[stage.key.context_id] = (stage.key.context_epoch, 100_001, 0, False)
    runtime._native_cache = NS(cache_controller=NS(mem_pool_host=NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=20_480)),
        "mamba": NS(host_pool=NS(size_per_token=64_389_120)),
    })))
    runtime._native_service_samples.clear()
    runtime._h2d_samples.clear()
    runtime._h2d_full_service_samples.clear()
    runtime._native_service_samples.extend(
        NativeServiceSample(100_000_000, 50., shape="full") for _ in range(3)
    )


def test_distant_report_skips_native_capture_without_creating_capacity_or_actions(monkeypatch):
    runtime, _, stage = report_fixture(monkeypatch)
    try:
        install_cost_evidence(runtime, stage)
        ceiling = runtime._report_planning_ceiling_ms(stage.key)
        with patch.object(runtime, "_report_remaining_ms", return_value=2 * ceiling + 1.), \
            patch.object(runtime, "_wait_prefetch_opportunity",
                         side_effect=AssertionError("distant report needs no native capture")):
            assert runtime._roll_final_stage() is None
        assert runtime.counts["report_service_planning_deferred"] == 1
        assert runtime._critical_parent_demand is None
        assert runtime._join_ticket is None
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("lead_multiple", (0., 1., 2.))
def test_near_report_does_not_read_an_unusable_cost_ceiling(monkeypatch, lead_multiple):
    runtime, _, stage = report_fixture(monkeypatch)
    try:
        remaining = lead_multiple * runtime.prefetch_lead_ms
        with patch.object(runtime, "_report_remaining_ms", return_value=remaining), \
            patch.object(runtime, "_report_planning_ceiling_ms",
                         side_effect=AssertionError("near report cannot be deferred")), \
            patch.object(runtime, "_wait_prefetch_opportunity", return_value=None) as capture:
            assert runtime._roll_final_stage() is None
            capture.assert_called_once_with(stage.key, include_checkpoint_plan=True)
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("case", ("boundary", "unknown_input", "changed_epoch", "new_h2d", "legacy_h2d"))
def test_planning_ceiling_rechecks_boundaries_identity_and_new_timing_evidence(monkeypatch, case):
    runtime, _, stage = report_fixture(monkeypatch)
    try:
        install_cost_evidence(runtime, stage)
        ceiling = runtime._report_planning_ceiling_ms(stage.key)
        remaining = 2 * ceiling + (0. if case == "boundary" else 1.)
        if case == "unknown_input":
            runtime._context_tokens.clear()
        elif case == "changed_epoch":
            runtime._context_tokens[stage.key.context_id] = (stage.key.context_epoch + 1, 100_001, 0, False)
        elif case == "new_h2d":
            runtime._native_service_samples.append(
                NativeServiceSample(100_000_000, remaining, shape="full"),
            )
        elif case == "legacy_h2d":
            runtime._h2d_samples.append((100_000_000, remaining))
        with patch.object(runtime, "_report_remaining_ms", return_value=remaining), \
            patch.object(runtime, "_wait_prefetch_opportunity", return_value=None) as capture:
            assert runtime._roll_final_stage() is None
            capture.assert_called_once_with(stage.key, include_checkpoint_plan=True)
        assert runtime.counts["report_service_planning_deferred"] == 0
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()
