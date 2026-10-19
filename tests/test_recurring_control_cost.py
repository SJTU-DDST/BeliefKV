from dataclasses import replace
from itertools import count
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalReceiptError,
    PhysicalTransactionLedger,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_prefetch_advance_and_maintenance import add_pending
from tests.test_wait_prefetch_batch import batch_fixture


@pytest.mark.parametrize("children", (1, 2))
@pytest.mark.parametrize("pool_bytes,total", (
    ((("kv", 20),), 20),
    ((("mamba", 15),), 15),
    ((("mamba", 15), ("kv", 20)), 35),
    ((("kv", 0), ("mamba", 15)), 15),
    ((["kv", 20], ["mamba", 0]), 25),
))
def test_registration_retains_pool_accounting_and_node_ownership(children, pool_bytes, total):
    ledger = PhysicalTransactionLedger()
    parts = tuple(
        PhysicalChildExpectation(index, (index,), pool_bytes, total)
        for index in range(children)
    )
    command = PhysicalActionExpectation(
        "load", "PREFETCH_GPU", "context", 0, parts, (("kv", 10), ("mamba", 5)),
        session_id="session", session_generation=1,
    )
    ledger.register(command)
    assert ledger.pending_action_bytes("PREFETCH_GPU") == children * total
    assert ledger._pending_nodes == set(range(children))
    assert tuple(ledger.pending_h2d_contexts) == (("context", "session", 1),)
    with pytest.raises(PhysicalReceiptError, match="already owned"):
        ledger.register(replace(command, command_id="collision"))
    ledger.cancel_unsubmitted("load")
    assert ledger.pending_count == 0
    assert not ledger._pending_nodes
    assert not ledger.pending_h2d_contexts
    assert ledger.pending_action_bytes("PREFETCH_GPU") == 0


@pytest.mark.parametrize("children", (1, 2))
@pytest.mark.parametrize("pool_bytes,total", (
    ((), 20),
    ((("kv", True),), 20),
    ((("kv", -10),), 20),
    ((("kv", 20.0),), 20),
    ((("kv", 11),), 20),
    ((("mamba", 6),), 20),
    ((("kv", 20), ("mamba", 5)), 24),
    ((("kv", 10), ("kv", 10)), 20),
    ((("mamba", 5), ("mamba", 5)), 20),
    ((("swa", 10),), 20),
    ((("", 10),), 20),
    ((([], 10),), 20),
    ((("kv",),), 20),
    (("kv",), 20),
))
def test_malformed_child_pool_rejection_is_atomic(children, pool_bytes, total):
    ledger = PhysicalTransactionLedger()
    valid = PhysicalChildExpectation(10, (10,), (("kv", 20),), 20)
    invalid = replace(
        valid, anchor_node_id=11, published_node_ids=(11,),
        pool_bytes=pool_bytes, num_bytes=total,
    )
    command = PhysicalActionExpectation(
        "load", "PREFETCH_GPU", "context", 0,
        (invalid,) if children == 1 else (valid, invalid),
        (("kv", 10), ("mamba", 5)),
    )
    with pytest.raises(PhysicalReceiptError):
        ledger.register(command)
    assert ledger.pending_count == 0
    assert not ledger._pending_nodes
    assert ledger.pending_action_bytes("PREFETCH_GPU") == 0
    assert not ledger.pending_h2d_contexts
    ledger.register(replace(command, children=(valid,)))


def test_empty_prediction_maintenance_never_postpones_a_new_record():
    runtime = NativeAdmissionRuntime()
    runtime._prediction_maintenance_interval_ms = 100.
    try:
        runtime._maintain_prediction_state(1000.)
        assert runtime.counts["prediction_maintenance_sweeps"] == 0
        runtime.tool_wait_hints["expired"] = NS(key=None, live=lambda key, now_ms: False)
        runtime._maintain_prediction_state(1001.)
        assert not runtime.tool_wait_hints
        assert runtime.counts["prediction_maintenance_sweeps"] == 1
        assert runtime.counts["tool_wait_expired"] == 1
    finally:
        runtime.close()


def test_empty_expiration_skips_cleanup_but_expired_transfers_retire_tracking():
    runtime, key, _, _, _ = batch_fixture("tool_wait")
    runtime._prediction_maintenance_interval_ms = 100.
    runtime.prefetch_discarded_callback = Mock()
    try:
        with patch("beliefkv.runtime.sglang_v0520_physical.monotonic", return_value=1.):
            add_pending(runtime, key)
            with patch.object(
                runtime, "_discard_prefetch_tracking",
                wraps=runtime._discard_prefetch_tracking,
            ) as cleanup:
                runtime.scheduler_step(defer_observations=True)
                cleanup.assert_not_called()
        assert runtime.physical_ledger.pending_count == 1
        assert "already-loading" in runtime._prefetch_steps
        with patch("beliefkv.runtime.sglang_v0520_physical.monotonic", return_value=32.):
            runtime.scheduler_step(defer_observations=True)
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime._prefetch_steps
        assert runtime.counts["physical_expired"] == 1
        runtime.prefetch_discarded_callback.assert_called_once_with(
            "already-loading", "physical_expired",
        )
    finally:
        runtime.close()


def test_physical_command_ids_use_one_namespace_per_runtime_beyond_receipt_history():
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.uuid4",
        side_effect=[NS(hex="a" * 32), NS(hex="b" * 32)],
    ) as namespace:
        runtimes = [NativeAdmissionRuntime(), NativeAdmissionRuntime()]
        try:
            assert namespace.call_count == 0
            commands = set()
            for runtime in runtimes:
                ledger = PhysicalTransactionLedger(history_size=4)
                for index in range(20):
                    kind = "prefetch" if index % 2 else "shadow"
                    identity = runtime._next_physical_command_id(kind)
                    assert identity not in commands
                    commands.add(identity)
                    ledger.register(PhysicalActionExpectation(
                        identity, "PREFETCH_GPU", "context", 0,
                        (PhysicalChildExpectation(1, (1,), (("kv", 10),), 10),),
                        (("kv", 10),),
                    ))
                    ledger.cancel_unsubmitted(identity)
                assert len(ledger._seen) == 4
            assert len(commands) == 40
            assert namespace.call_count == 2
        finally:
            for runtime in runtimes:
                runtime.close()


def test_inherited_namespace_and_sequence_are_separated_by_process():
    runtime = NativeAdmissionRuntime()
    other = NativeAdmissionRuntime()
    runtime._physical_command_namespace = other._physical_command_namespace = "a" * 32
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.os.getpid", return_value=101):
            first = runtime._next_physical_command_id("prefetch")
        with patch("beliefkv.runtime.sglang_v0520_runtime.os.getpid", return_value=102):
            inherited = other._next_physical_command_id("prefetch")
        assert first != inherited
    finally:
        runtime.close()
        other.close()


def test_physical_command_sequence_never_wraps_at_a_fixed_width_boundary():
    runtime = NativeAdmissionRuntime()
    runtime._physical_command_sequence = count(2 ** 64 - 1)
    try:
        identities = {runtime._next_physical_command_id("prefetch") for _ in range(3)}
        assert len(identities) == 3
    finally:
        runtime.close()
