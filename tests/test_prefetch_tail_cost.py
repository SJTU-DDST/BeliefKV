from collections import OrderedDict
from dataclasses import asdict, replace
import pickle
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalReceiptError,
    PhysicalActionCompleted, PrefetchLoadStep, plan_prefetch_gpu_steps,
    prefetch_expectation_from_native_op,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture, pinned_issuance_tracker
from scripts.benchmark_prefetch_capture_cpu import fixture as capture_fixture, load_methods
from tests.test_sglang_v0520_physical import Pool, native_prefetch


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("depth", (63, 64, 65, 96))
def test_complete_full_submission_skips_tail_work_only_when_no_full_remains(source, depth):
    runtime, _, cache, _, actions, _, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=depth,
        native_budget=True, tail_reclaim=True,
    )
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    try:
        with patch("time.monotonic", lambda: now), patch(
            "beliefkv.runtime.sglang_v0520_runtime.plan_prefetch_gpu_steps",
            wraps=plan_prefetch_gpu_steps,
        ) as tail_plan:
            dispatch()
        assert len(actions) == min(depth, 64)
        assert runtime.physical_ledger.pending_count == min(depth, 64)
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
        if depth <= 64:
            tail_plan.assert_not_called()
            cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        else:
            tail_plan.assert_called_once()
            assert cache.reclaim_beliefkv_handoff_capacity.call_count == 1
            kwargs = cache.reclaim_beliefkv_handoff_capacity.call_args.kwargs
            reserve = runtime._residency_budget.reserve_full_tokens
            assert kwargs["full_tokens"] == (depth - 64) * 128 + reserve
            assert set(kwargs["exclude_node_ids"]) == set(range(depth + 1))
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
def test_missing_full_outside_the_legal_input_plan_does_not_prove_all_full_submitted(source):
    runtime, key, cache, _, actions, _, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=64,
        native_budget=True, tail_reclaim=True,
    )
    runtime._context_tokens[key.context_id] = (0, 63 * 128 + 1, 0, False)
    cache.tree_core.node_by_id(63).component_data[2].host_value = (0,)
    original = runtime._wait_prefetch_opportunity
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})

    def without_cached_plan(*args, **kwargs):
        opportunity = original(*args, **kwargs)
        assert opportunity.required_full_tokens == 63 * 128
        assert opportunity.candidate.missing_full_device_tokens == 64 * 128
        return replace(opportunity, checkpoint_plan=None)

    runtime._wait_prefetch_opportunity = without_cached_plan
    try:
        with patch("time.monotonic", lambda: now), patch(
            "beliefkv.runtime.sglang_v0520_runtime.plan_prefetch_gpu_steps",
            wraps=plan_prefetch_gpu_steps,
        ) as tail_plan:
            dispatch()
        assert len(actions) == 63
        tail_plan.assert_called_once()
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("field", ("leaf_node_id", "node_id"))
@pytest.mark.parametrize("invalid", (True, -1, 1.0, None))
def test_prefetch_scalar_identity_validation_keeps_strict_node_ids(field, invalid):
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    step = replace(step, **{field: invalid})
    if field == "node_id":
        operation.node_ids = [invalid]
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)


@pytest.mark.parametrize("field", ("leaf_creation_time", "creation_time"))
@pytest.mark.parametrize("invalid", (True, -1, float("nan"), float("inf"), None))
def test_prefetch_scalar_identity_validation_keeps_finite_creation_times(field, invalid):
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    step = replace(step, **{field: invalid})
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)


@pytest.mark.parametrize("counts", (
    {}, {"kv": True}, {"kv": -1}, {"kv": 2.0},
    {"kv": 2, "mamba": 0}, {"kv": 2, "swa": 0}, {"mamba": 2},
))
def test_single_pool_validation_rejects_malformed_or_extra_native_counts(counts):
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    controller._num_tokens_by_pool = lambda operation: counts
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)


def test_compact_prefetch_records_keep_dataclass_copy_serialization_and_metadata():
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    action = prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)
    child = replace(
        action.children[0], anchor_creation_time=4,
        host_destination_indices=(("kv", (1, 2)),),
    )
    action = replace(action, children=(child,))
    assert isinstance(step, PrefetchLoadStep)
    assert isinstance(child, PhysicalChildExpectation)
    assert isinstance(action, PhysicalActionExpectation)
    for record in (step, child, action):
        restored = pickle.loads(pickle.dumps(record))
        assert asdict(restored) == asdict(record)
        assert asdict(replace(record)) == asdict(record)
    assert action.children[0].host_destination_indices == (("kv", (1, 2)),)


@pytest.mark.parametrize("full,mamba,sidecar,pools,total", (
    (2, 0, None, (("kv", 20),), 20),
    (2, 1, None, (("kv", 20), ("mamba", 5)), 25),
    (0, 2, None, (("kv", 0), ("mamba", 10)), 10),
    (2, 0, Pool.KV, (("kv", 20),), 26),
    (2, 1, Pool.KV, (("kv", 20), ("mamba", 5)), 31),
    (0, 2, Pool.MAMBA, (("kv", 0), ("mamba", 10)), 16),
))
def test_single_node_issuance_preserves_zero_pools_and_sidecar_total_without_ack(
    full, mamba, sidecar, pools, total,
):
    step, operation, controller = native_prefetch(full=full, mamba=mamba, sidecar=sidecar)
    expected = prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)
    runtime = NativeAdmissionRuntime()
    records = []
    runtime.prefetch_issued_callback = records.append
    runtime.physical_ledger.register(expected)
    try:
        runtime._record_prefetch_issued(
            "prefetch-1", step, "tool_wait", 100., NS(load_started=True),
        )
        assert records == [PhysicalActionCompleted(
            "prefetch-1", "PREFETCH_GPU", step.key.context_id, step.key.context_epoch,
            (step.node_id,), pools, total, "tool_wait",
        )]
        assert runtime.physical_ledger.pending_expectation("prefetch-1") == expected
        assert runtime.physical_ledger.pending_count == 1
        assert not runtime.completed_physical_actions
    finally:
        runtime.close()


def test_dispatch_benchmark_uses_pinned_callback_methods_in_the_baseline(monkeypatch):
    tracker, _ = pinned_issuance_tracker("HEAD")
    root = NS(parent=None)
    node = NS(
        id=1, parent=root, key=tuple(range(8)), creation_time=2.,
        component_data=(NS(value=object()), NS(), NS(value=None)),
    )
    audit = tracker.__new__(tracker)
    audit._cache = NS(tree_core=NS(node_by_id={1: node}.__getitem__))
    audit._pending_prefetch_use = OrderedDict()
    audit._unacked_prefetch_use = {}
    audit._prefetch_prefix_diagnostics = False
    current_track = Mock(side_effect=AssertionError("baseline reused the current callback"))
    current_path = Mock(side_effect=AssertionError("baseline reused the current path helper"))
    monkeypatch.setattr(NativeReactiveTelemetry, "_track_prefetch_use", current_track)
    monkeypatch.setattr(NativeReactiveTelemetry, "_node_key_path", current_path)
    action = NS(command_id="restore", node_ids=(1,))
    audit.on_prefetch_issued(action)
    current_track.assert_not_called()
    current_path.assert_not_called()
    assert audit._unacked_prefetch_use == {"restore": action}
    assert audit._pending_prefetch_use["restore"][1][0][:4] == (1, 2., node, 8)
    assert audit._pending_prefetch_use["restore"][2:] == (None, {})


def test_capture_benchmark_keeps_its_static_path_helper_pinned(monkeypatch):
    source = subprocess.check_output(
        ["git", "show", "HEAD:beliefkv/runtime/v0520_native_telemetry.py"],
        cwd=Path(__file__).resolve().parents[1], text=True,
    )
    methods = load_methods(source, "HEAD:beliefkv/runtime/v0520_native_telemetry.py")
    audit, _, actions, _ = capture_fixture(
        methods, lambda tokens, **kwargs: tokens,
        tokens=8, extents=1, diagnostics=False,
    )
    audit._pending_prefetch_use = OrderedDict()
    audit._unacked_prefetch_use = {}
    current_path = Mock(side_effect=AssertionError("capture baseline reused current path helper"))
    monkeypatch.setattr(NativeReactiveTelemetry, "_node_key_path", current_path)
    audit.on_prefetch_issued(actions[0])
    current_path.assert_not_called()
    assert audit._pending_prefetch_use[actions[0].command_id][1][0][3] == 8
