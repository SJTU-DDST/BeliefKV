"""CPU-only checks of native merged ACK transaction credit."""

from dataclasses import replace
from types import SimpleNamespace as NS
from enum import Enum
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_physical import (
    ActionLocalPrefetchCandidate,
    ContextSessionAnchors,
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PhysicalReceiptError,
    PhysicalTransactionLedger,
    PrefetchLoadStep,
    capture_action_local_shadow,
    next_prefetch_gpu_step,
    next_shadow_backup_step,
    prefetch_expectation_from_native_op,
    shadow_expectation_from_native_op,
    ShadowBackupStep,
)


def test_native_shadow_expectation_uses_exact_operation_before_ack():
    class Pool(str, Enum):
        KV = "kv"
        MAMBA = "mamba"

    step = ShadowBackupStep(
        PrefillCandidateKey("r", "w", "i", "c", 3, 0, "s", 4),
        12, 5, 11, 4,
    )
    group = NS(entry_map={
        Pool.KV: NS(host_pool=NS(size_per_token=10)),
        Pool.MAMBA: NS(host_pool=NS(size_per_token=5)),
    })
    op = NS(beliefkv_command_id="prepare-1", node_ids=[11],
            device_indices=(0, 1), host_indices=(2, 3))
    controller = NS(
        mem_pool_host=group,
        _num_tokens_by_pool=lambda operation: {"kv": 2, "mamba": 1},
        _transfer_num_bytes=lambda operation: 32,
    )
    expected = shadow_expectation_from_native_op("prepare-1", step, op, controller)
    assert expected.children == (
        PhysicalChildExpectation(11, (11,), (("kv", 20), ("mamba", 5)), 32),
    )
    assert expected.session_id == "s"
    assert expected.session_generation == 4
    ledger = PhysicalTransactionLedger()
    ledger.register(expected)
    assert ledger.pending_count == 1
    assert ledger.observe(
        ack(receipt(command="prepare-1", anchor=11, published=(11,),
                    kv=2, mamba=1, total=32), nodes=(11,)),
        live_context_epochs={"c": 3},
        live_context_sessions={"c": ("s", 4)},
    )[0].num_bytes == 32
    assert ledger.pending_count == 0

    # A MAMBA-only backup can have zero FULL tokens and still transfer bytes.
    op.device_indices = ()
    op.host_indices = ()
    controller._num_tokens_by_pool = lambda operation: {"kv": 0, "mamba": 1}
    controller._transfer_num_bytes = lambda operation: 8
    assert shadow_expectation_from_native_op(
        "prepare-1", step, op, controller
    ).children[0] == PhysicalChildExpectation(
        11, (11,), (("kv", 0), ("mamba", 5)), 8
    )


@pytest.mark.parametrize("change", (
    lambda op, ctrl: setattr(op, "beliefkv_command_id", "other"),
    lambda op, ctrl: setattr(op, "node_ids", [12]),
    lambda op, ctrl: setattr(op, "host_indices", ()),
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"swa": 1}),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: 1),
    lambda op, ctrl: setattr(
        ctrl.mem_pool_host.entry_map["kv"].host_pool, "size_per_token", 0
    ),
))
def test_native_shadow_expectation_rejects_invalid_operation(change):
    step = ShadowBackupStep(
        PrefillCandidateKey("r", "w", "i", "c", 3, 0, "s", 4),
        12, 5, 11, 4,
    )
    op = NS(beliefkv_command_id="prepare-1", node_ids=[11],
            device_indices=(0, 1), host_indices=(2, 3))
    controller = NS(
        mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
        }),
        _num_tokens_by_pool=lambda _: {"kv": 2},
        _transfer_num_bytes=lambda _: 20,
    )
    change(op, controller)
    with pytest.raises(PhysicalReceiptError):
        shadow_expectation_from_native_op("prepare-1", step, op, controller)


class Pool(str, Enum):
    KV = "kv"
    MAMBA = "mamba"
    DRAFT = "draft"


def native_prefetch(*, full=2, mamba=1, sidecar=Pool.KV):
    step = PrefetchLoadStep(
        PrefillCandidateKey("r", "w", "i", "c", 3, 0, "s", 4),
        12, 5, 11, 4,
    )
    kv_host, kv_device = tuple(range(full)), tuple(range(10, 10 + full))
    mamba_host, mamba_device = tuple(range(mamba)), tuple(range(20, 20 + mamba))
    transfers = []
    if mamba:
        transfers.append(NS(
            name=Pool.MAMBA, host_indices=mamba_host,
            device_indices=mamba_device, indices_from_pool=None,
        ))
    if sidecar is not None:
        source_indices = (kv_host, kv_device) if sidecar == Pool.KV else (
            mamba_host, mamba_device
        )
        transfers.append(NS(
            name=Pool.DRAFT, host_indices=source_indices[0],
            device_indices=source_indices[1], indices_from_pool=sidecar,
        ))
    op = NS(
        beliefkv_command_id="prefetch-1", node_ids=[11],
        host_indices=kv_host, device_indices=kv_device, pool_transfers=transfers,
    )
    sizes = {Pool.KV: 10, Pool.MAMBA: 5, Pool.DRAFT: 3}
    group = NS(entry_map={
        name: NS(host_pool=NS(size_per_token=size))
        for name, size in sizes.items()
    })

    def counts(operation):
        result = {"kv": len(operation.device_indices)}
        for transfer in operation.pool_transfers or []:
            if transfer.indices_from_pool is None:
                result[transfer.name.value] = len(transfer.host_indices)
        return result

    def total(operation):
        sources = {Pool.KV: len(operation.host_indices)}
        sources.update({
            transfer.name: len(transfer.host_indices)
            for transfer in operation.pool_transfers or []
            if transfer.indices_from_pool is None
        })
        return sources[Pool.KV] * sizes[Pool.KV] + sum(
            sources[transfer.indices_from_pool] * sizes[transfer.name]
            if transfer.indices_from_pool is not None
            else len(transfer.host_indices) * sizes[transfer.name]
            for transfer in operation.pool_transfers or []
        )

    controller = NS(
        mem_pool_host=group,
        _num_tokens_by_pool=counts,
        _transfer_num_bytes=total,
    )
    return step, op, controller


def test_native_prefetch_expectation_credits_only_completed_h2d_ack():
    step, op, controller = native_prefetch()
    expectation = prefetch_expectation_from_native_op(
        "prefetch-1", step, op, controller
    )
    assert expectation == PhysicalActionExpectation(
        command_id="prefetch-1", action="PREFETCH_GPU",
        context_id="c", context_epoch=3,
        children=(PhysicalChildExpectation(
            11, (11,), (("kv", 20), ("mamba", 5)), 31,
        ),),
        pool_bytes_per_token=(("kv", 10), ("mamba", 5)),
        session_id="s", session_generation=4,
    )
    ledger = PhysicalTransactionLedger()
    ledger.register(expectation)
    assert ledger.pending_count == 1
    assert ledger.observe(
        ack(nodes=(99,), direction="h2d", kv=0, mamba=0),
        live_context_epochs={"c": 3},
        live_context_sessions={"c": ("s", 4)},
    ) == ()
    assert ledger.pending_count == 1
    (completed,) = ledger.observe(
        ack(
            receipt(
                command="prefetch-1", anchor=11, published=(11,),
                kv=2, mamba=1, total=31,
            ),
            nodes=(11,), direction="h2d",
        ),
        live_context_epochs={"c": 3},
        live_context_sessions={"c": ("s", 4)},
    )
    assert completed.action == "PREFETCH_GPU"
    assert completed.num_bytes == 31
    assert completed.pool_bytes == (("kv", 20), ("mamba", 5))
    assert ledger.pending_count == 0


def test_native_prefetch_mamba_only_has_zero_full_and_derived_bytes():
    step, op, controller = native_prefetch(full=0, mamba=2, sidecar=Pool.MAMBA)
    expectation = prefetch_expectation_from_native_op(
        "prefetch-1", step, op, controller
    )
    assert expectation.children == (
        PhysicalChildExpectation(11, (11,), (("kv", 0), ("mamba", 10)), 16),
    )
    ledger = PhysicalTransactionLedger()
    ledger.register(expectation)
    (completed,) = ledger.observe(
        ack(
            receipt(
                command="prefetch-1", anchor=11, published=(11,),
                kv=0, mamba=2, total=16,
            ),
            nodes=(11,), direction="h2d", kv=0, mamba=2,
        ),
        live_context_epochs={"c": 3},
        live_context_sessions={"c": ("s", 4)},
    )
    assert completed.num_bytes == 16


def test_native_prefetch_full_only_without_aux_transfers():
    step, op, controller = native_prefetch(mamba=0, sidecar=None)
    op.pool_transfers = None
    expectation = prefetch_expectation_from_native_op(
        "prefetch-1", step, op, controller
    )
    assert expectation.children == (
        PhysicalChildExpectation(11, (11,), (("kv", 20),), 20),
    )


def test_native_prefetch_rejects_empty_transfer_and_orphan_sidecar():
    step, op, controller = native_prefetch(full=0, mamba=0, sidecar=None)
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, op, controller)
    step, op, controller = native_prefetch(full=0, mamba=1, sidecar=Pool.KV)
    with pytest.raises(PhysicalReceiptError, match="sidecar"):
        prefetch_expectation_from_native_op("prefetch-1", step, op, controller)


@pytest.mark.parametrize("change", (
    lambda op, ctrl: setattr(op, "beliefkv_command_id", "other"),
    lambda op, ctrl: setattr(op, "node_ids", [12]),
    lambda op, ctrl: setattr(op, "host_indices", ()),
    lambda op, ctrl: setattr(op.pool_transfers[0], "device_indices", ()),
    lambda op, ctrl: setattr(op.pool_transfers[0], "name", Pool.KV),
    lambda op, ctrl: setattr(op.pool_transfers[1], "indices_from_pool", Pool.DRAFT),
    lambda op, ctrl: setattr(op.pool_transfers[1], "host_indices", tuple(range(2))),
    lambda op, ctrl: setattr(op.pool_transfers[1], "name", Pool.MAMBA),
    lambda op, ctrl: setattr(op.pool_transfers[1], "device_indices", ()),
    lambda op, ctrl: setattr(
        ctrl.mem_pool_host.entry_map[Pool.DRAFT].host_pool, "size_per_token", 0
    ),
    lambda op, ctrl: setattr(
        ctrl, "_num_tokens_by_pool", lambda _: {"kv": 2, "mamba": 1, "draft": 2}
    ),
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": 1}),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: 25),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: 30),
))
def test_native_prefetch_rejects_invalid_operation_or_sidecar(change):
    step, op, controller = native_prefetch()
    change(op, controller)
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, op, controller)


@pytest.mark.parametrize("leaf_id,created,session", [
    (-1, 4, 4),
    (12, float("nan"), 4),
    (12, 4, None),
])
def test_native_prefetch_rejects_invalid_provenance(leaf_id, created, session):
    step, op, controller = native_prefetch()
    step = replace(
        step, leaf_node_id=leaf_id, leaf_creation_time=created,
        key=replace(step.key, session_generation=session),
    )
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, op, controller)


@pytest.mark.parametrize("direction,status,total", [
    ("d2h", "completed", 31),
    ("h2d", "completed", 25),
    ("h2d", "pending", 31),
])
def test_native_prefetch_requires_complete_matching_ack(direction, status, total):
    step, op, controller = native_prefetch()
    ledger = PhysicalTransactionLedger()
    ledger.register(prefetch_expectation_from_native_op(
        "prefetch-1", step, op, controller
    ))
    with pytest.raises(PhysicalReceiptError):
        ledger.observe(
            ack(
                receipt(
                    command="prefetch-1", anchor=11, published=(11,),
                    kv=2, mamba=1, total=total,
                ),
                nodes=(11,), direction=direction, status=status,
            ),
            live_context_epochs={"c": 3},
            live_context_sessions={"c": ("s", 4)},
        )
    assert ledger.pending_count == 0


def test_native_prefetch_stale_session_cannot_credit_ack():
    step, op, controller = native_prefetch()
    ledger = PhysicalTransactionLedger()
    ledger.register(prefetch_expectation_from_native_op(
        "prefetch-1", step, op, controller
    ))
    with pytest.raises(PhysicalReceiptError, match="session"):
        ledger.observe(
            ack(
                receipt(
                    command="prefetch-1", anchor=11, published=(11,),
                    kv=2, mamba=1, total=31,
                ),
                nodes=(11,), direction="h2d",
            ),
            live_context_epochs={"c": 3},
            live_context_sessions={"c": ("s", 5)},
        )
    assert ledger.pending_count == 0


def test_shadow_candidate_is_context_local_and_read_only():
    anchors = ContextSessionAnchors(
        key=PrefillCandidateKey(
            "request", "workflow", "invocation", "context", 1, 0, "session", 2
        ),
        component_leaves=((0, ((11, 4),)), (2, ((11, 4),))),
        captured_monotonic_s=12.0,
    )
    root = NS(
        node_id=0, parent_id=None, creation_time=1,
        full_device_tokens=0, full_host_tokens=0,
        mamba_device_present=False, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    leaf = NS(
        node_id=11, parent_id=0, creation_time=4,
        full_device_tokens=10, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(leaf, root)),
    ) as observe:
        candidate = capture_action_local_shadow(object(), anchors)
        assert candidate is not None
        assert [node.node_id for node in candidate.nodes] == [0, 11]
        assert candidate.missing_full_host_tokens == 10
        assert candidate.missing_mamba_host_nodes == 1
        assert observe.call_count == 2
        assert capture_action_local_shadow(object(), anchors, max_nodes=1) is None
        leaf.pending_write_id = 11
        assert capture_action_local_shadow(object(), anchors) is None
        leaf.pending_write_id = None
        leaf.full_host_tokens = 10
        leaf.mamba_host_present = True
        assert capture_action_local_shadow(object(), anchors) is None


def test_shadow_candidate_rejects_changed_leaf_and_inconsistent_views():
    anchors = ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1),
        ((0, ((11, 4),)), (2, ((11, 4),))),
        10.0,
    )
    leaf = NS(
        node_id=11, parent_id=None, creation_time=5,
        full_device_tokens=1, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(leaf,)),
    ):
        assert capture_action_local_shadow(object(), anchors) is None
    leaf.creation_time = 4
    changed = NS(**vars(leaf))
    changed.full_device_tokens = 2
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        side_effect=(
            NS(observable=True, nodes=(leaf,)),
            NS(observable=True, nodes=(changed,)),
        ),
    ):
        assert capture_action_local_shadow(object(), anchors) is None


def test_partial_host_shadow_selects_parent_before_session_leaf():
    anchors = ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1),
        ((0, ((12, 5),)), (2, ((12, 5),))),
        10.0,
    )
    parent = NS(
        node_id=11, parent_id=None, creation_time=4,
        full_device_tokens=4, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    leaf = NS(
        node_id=12, parent_id=11, creation_time=5,
        full_device_tokens=8, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    candidate = NS(anchors=anchors, nodes=(leaf, parent))
    step = next_shadow_backup_step(candidate)
    assert (
        step.leaf_node_id, step.leaf_creation_time, step.node_id,
        step.creation_time, step.key,
    ) == (12, 5, 11, 4, anchors.key)
    parent.full_host_tokens = 4
    parent.mamba_host_present = True
    step = next_shadow_backup_step(candidate)
    assert step.node_id == 12
    leaf.pending_write_id = 12
    assert next_shadow_backup_step(candidate) is None
    leaf.pending_write_id = None
    leaf.full_host_tokens = 8
    leaf.mamba_host_present = True
    assert next_shadow_backup_step(candidate) is None


def test_partial_shadow_rejects_orphan_and_cyclic_ancestry():
    anchors = ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1),
        ((0, ((12, 5),)), (2, ((12, 5),))),
        10.0,
    )
    node = NS(
        node_id=12, parent_id=99, creation_time=5,
        full_device_tokens=8, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    assert next_shadow_backup_step(NS(anchors=anchors, nodes=(node,))) is None
    node.parent_id = 12
    assert next_shadow_backup_step(NS(anchors=anchors, nodes=(node,))) is None


def test_shadow_step_never_selects_mamba_only_leaf_without_full_provenance():
    anchors = ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1),
        ((0, ((11, 4),)), (2, ((12, 5),))),
        10.0,
    )
    full = NS(
        node_id=11, parent_id=None, creation_time=4,
        full_device_tokens=8, full_host_tokens=8,
        mamba_device_present=True, mamba_host_present=True,
        pending_write_id=None, pending_load_id=None,
    )
    mamba_only = NS(
        node_id=12, parent_id=11, creation_time=5,
        full_device_tokens=8, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    assert next_shadow_backup_step(NS(
        anchors=anchors, nodes=(full, mamba_only)
    )) is None


def prefetch_node(
    node_id, parent_id, created, *,
    full_gpu=0, full_host=0, mamba_gpu=False, mamba_host=False,
):
    return NS(
        node_id=node_id, parent_id=parent_id, creation_time=created,
        full_device_tokens=full_gpu, full_host_tokens=full_host,
        mamba_device_present=mamba_gpu, mamba_host_present=mamba_host,
        pending_write_id=None, pending_load_id=None,
    )


def prefetch_anchors(full_leaf=12, mamba_leaf=12):
    return ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 3, 0, "s", 4),
        ((0, ((full_leaf, 5),)), (2, ((mamba_leaf, 5),))),
        10.0,
    )


def test_prefetch_capture_and_select_root_first_full_host_only():
    anchors = prefetch_anchors()
    root = prefetch_node(0, None, 1)
    parent = prefetch_node(11, 0, 4, full_host=4)
    leaf = prefetch_node(12, 11, 5, full_host=8)
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(leaf, parent, root)),
    ):
        candidate = capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        )
        assert isinstance(candidate, ActionLocalPrefetchCandidate)
        assert candidate.missing_full_device_tokens == 12
        assert candidate.missing_mamba_device_nodes == 0
        step = next_prefetch_gpu_step(candidate)
        assert step == PrefetchLoadStep(anchors.key, 12, 5, 11, 4)
        assert capture_action_local_shadow(object(), anchors, max_nodes=2,
                                           for_prefetch=True) is None
        parent.full_device_tokens = 4
        candidate = capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        )
        assert next_prefetch_gpu_step(candidate) == PrefetchLoadStep(
            anchors.key, 12, 5, 12, 5
        )
        parent.pending_load_id = 1
        assert capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        ) is None


def test_prefetch_mamba_only_needs_full_gpu_and_host_state():
    anchors = prefetch_anchors()
    root = prefetch_node(0, None, 1)
    leaf = prefetch_node(12, 0, 5, full_gpu=8, mamba_host=True)
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(leaf, root)),
    ):
        candidate = capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        )
        assert candidate.missing_full_device_tokens == 0
        assert candidate.missing_mamba_device_nodes == 1
        assert next_prefetch_gpu_step(candidate) == PrefetchLoadStep(
            anchors.key, 12, 5, 12, 5
        )
        leaf.full_device_tokens = 0
        assert capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        ) is None
        leaf.full_device_tokens = 8
        leaf.mamba_host_present = False
        assert capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        ) is None


def test_prefetch_without_cpu_kv_or_with_pending_never_selects():
    anchors = prefetch_anchors()
    root = prefetch_node(0, None, 1)
    leaf = prefetch_node(12, 0, 5, full_gpu=8, mamba_gpu=True)
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(leaf, root)),
    ):
        assert capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        ) is None
        leaf.full_device_tokens = 0
        assert capture_action_local_shadow(
            object(), anchors, for_prefetch=True,
        ) is None
    leaf.full_host_tokens = 8
    root.pending_write_id = 7
    assert next_prefetch_gpu_step(NS(anchors=anchors, nodes=(leaf, root))) is None


def test_prefetch_rejects_missing_cyclic_cross_node_and_stale_leaf():
    anchors = prefetch_anchors()
    root = prefetch_node(0, None, 1)
    leaf = prefetch_node(12, 0, 5, full_host=8)
    candidate = NS(anchors=anchors, nodes=(leaf, root))
    assert next_prefetch_gpu_step(candidate) == PrefetchLoadStep(
        anchors.key, 12, 5, 12, 5
    )
    leaf.parent_id = 99
    assert next_prefetch_gpu_step(candidate) is None
    leaf.parent_id = 12
    assert next_prefetch_gpu_step(candidate) is None
    leaf.parent_id = 0
    leaf.creation_time = 6
    assert next_prefetch_gpu_step(candidate) is None
    leaf.creation_time = 5
    unrelated = prefetch_node(13, 0, 6, mamba_host=True, full_gpu=8)
    cross = ContextSessionAnchors(
        anchors.key, ((0, ((12, 5),)), (2, ((13, 6),))), 10.0,
    )
    assert next_prefetch_gpu_step(
        NS(anchors=cross, nodes=(leaf, root, unrelated))
    ) is None
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        side_effect=(
            NS(observable=True, nodes=(leaf, root)),
            NS(observable=True, nodes=(unrelated, root)),
        ),
    ):
        assert capture_action_local_shadow(
            object(), cross, for_prefetch=True,
        ) is None


def test_prefetch_selection_rejects_malformed_closure():
    anchors = prefetch_anchors()
    root = prefetch_node(0, None, 1)
    leaf = prefetch_node(12, 0, 5, full_host=8)
    candidate = NS(anchors=anchors, nodes=(leaf, root))
    leaf.full_host_tokens = True
    assert next_prefetch_gpu_step(candidate) is None
    leaf.full_host_tokens = 8
    root.creation_time = float("nan")
    assert next_prefetch_gpu_step(candidate) is None
    root.creation_time = 1
    leaf.parent_id = -1
    assert next_prefetch_gpu_step(candidate) is None
    leaf.parent_id = 0
    malformed = ContextSessionAnchors(
        anchors.key, ((0, (("bad", 5),)), (2, ((12, 5),))), 10.0,
    )
    assert next_prefetch_gpu_step(NS(anchors=malformed, nodes=(leaf, root))) is None


def child(anchor, published, kv, mamba, total):
    return PhysicalChildExpectation(
        anchor, published, (("kv", kv), ("mamba", mamba)), total
    )


def expected(command="cmd", *, action="PREPARE_HOST", children=None, epoch=3):
    return PhysicalActionExpectation(
        command_id=command,
        action=action,
        context_id="ctx",
        context_epoch=epoch,
        children=children or (child(11, (11, 12), 20, 5, 32),),
        pool_bytes_per_token=(("kv", 10), ("mamba", 5)),
    )


def receipt(command="cmd", *, anchor=11, published=(11, 12), kv=2, mamba=1, total=32):
    return NS(
        command_id=command,
        anchor_node_id=anchor,
        published_node_ids=published,
        num_tokens_by_pool=(("kv", kv), ("mamba", mamba)),
        num_bytes=total,
    )


def ack(*receipts, direction="d2h", nodes=(11, 12), kv=2, mamba=1, status="completed"):
    return NS(
        direction=direction, status=status, node_ids=nodes,
        num_tokens_by_pool=(("kv", kv), ("mamba", mamba)),
        child_commits=receipts,
    )


def test_merged_native_and_tagged_ack_credits_only_matched_child():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    event = ack(receipt(), nodes=(11, 12, 99), kv=5, mamba=1)
    completed, = ledger.observe(event, live_context_epochs={"ctx": 3})
    assert completed.command_id == "cmd"
    assert completed.node_ids == (11, 12)
    assert completed.pool_bytes == (("kv", 20), ("mamba", 5))
    assert completed.num_bytes == 32
    assert ledger.pending_count == 0
    with pytest.raises(PhysicalReceiptError, match="unknown"):
        ledger.observe(event, live_context_epochs={"ctx": 3})


def test_two_tagged_children_in_same_merged_ack():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected("first"))
    ledger.register(expected(
        "second", action="PREPARE_HOST",
        children=(child(13, (13,), 10, 0, 10),),
    ))
    event = ack(
        receipt(command="first"),
        receipt(command="second", anchor=13, published=(13,), kv=1, mamba=0, total=10),
        nodes=(11, 12, 13), kv=3, mamba=1,
    )
    assert {item.command_id for item in ledger.observe(
        event, live_context_epochs={"ctx": 3},
    )} == {"first", "second"}
    assert ledger.pending_count == 0


def test_invalid_child_of_merged_ack_cannot_credit_other_command():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected("first"))
    ledger.register(expected(
        "second", children=(child(13, (13,), 10, 0, 10),),
    ))
    event = ack(
        receipt(command="first"),
        receipt(command="second", anchor=13, published=(13,), kv=2, mamba=0, total=10),
        nodes=(11, 12, 13), kv=4, mamba=1,
    )
    with pytest.raises(PhysicalReceiptError, match="mismatch"):
        ledger.observe(event, live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


def test_merged_ack_missing_one_tagged_child_rejects_all_credit():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected("first"))
    ledger.register(expected(
        "second", children=(child(13, (13,), 10, 0, 10),),
    ))
    with pytest.raises(PhysicalReceiptError, match="omitted"):
        ledger.observe(
            ack(receipt(command="first"), nodes=(11, 12, 13), kv=3, mamba=1),
            live_context_epochs={"ctx": 3},
        )
    assert ledger.pending_count == 0


def test_overlapping_pending_node_or_unbounded_publication_rejected():
    ledger = PhysicalTransactionLedger(max_nodes=2)
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="already owned"):
        ledger.register(expected("other"))
    with pytest.raises(PhysicalReceiptError, match="node bound"):
        ledger.register(expected(
            "oversized", children=(child(21, (21, 22, 23), 20, 5, 32),),
        ))


def test_rejected_native_enqueue_releases_reservation_without_reusing_identity():
    ledger = PhysicalTransactionLedger(max_pending=1)
    ledger.register(expected())
    ledger.cancel_unsubmitted("cmd")
    assert ledger.pending_count == 0
    with pytest.raises(PhysicalReceiptError, match="reused"):
        ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="unknown"):
        ledger.cancel_unsubmitted("cmd")
    ledger.register(expected("other"))
    with pytest.raises(PhysicalReceiptError, match="unknown"):
        ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


def test_partially_acknowledged_command_cannot_cancel_reservation():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected(children=(
        child(11, (11, 12), 20, 5, 32),
        child(13, (13,), 10, 0, 10),
    )))
    assert ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3}) == ()
    with pytest.raises(PhysicalReceiptError, match="partially acknowledged"):
        ledger.cancel_unsubmitted("cmd")
    assert ledger.pending_count == 1


def test_two_child_command_waits_for_full_reconciliation_across_acks():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected(children=(
        child(11, (11, 12), 20, 5, 32),
        child(13, (13,), 10, 0, 10),
    )))
    assert ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3}) == ()
    second = ack(
        receipt(anchor=13, published=(13,), kv=1, mamba=0, total=10),
        nodes=(13,), kv=1, mamba=0,
    )
    completed, = ledger.observe(second, live_context_epochs={"ctx": 3})
    assert completed.node_ids == (11, 12, 13)
    assert completed.num_bytes == 42


def test_partial_ack_with_no_child_credit_fails_closed():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="missing child credit"):
        ledger.observe(ack(nodes=(11, 12)), live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


@pytest.mark.parametrize("event", [
    ack(receipt(), receipt(), nodes=(11, 12), kv=4, mamba=2),
    ack(receipt(command="unknown")),
    ack(receipt(kv=1)),
    ack(receipt(total=31)),
    ack(receipt(published=(11,))),
    ack(receipt(), status="pending"),
    ack(receipt(), direction="h2d"),
    ack(receipt(), kv=1),
    ack(
        NS(
            command_id="cmd", anchor_node_id=11, published_node_ids=(11, 12),
            num_tokens_by_pool=(("kv", "bad"),), num_bytes=32,
        )
    ),
])
def test_malformed_duplicate_unknown_and_mismatch_poison_credit(event):
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError):
        ledger.observe(event, live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


def test_stale_context_epoch_and_replay_do_not_credit():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="epoch"):
        ledger.observe(ack(receipt()), live_context_epochs={"ctx": 4})
    with pytest.raises(PhysicalReceiptError):
        ledger.register(expected())
    ledger.register(expected("next"))
    with pytest.raises(PhysicalReceiptError, match="epoch"):
        ledger.observe(
            ack(receipt(command="next")), live_context_epochs={"ctx": True},
        )


def test_session_generation_must_still_match_at_ack():
    from dataclasses import replace

    ledger = PhysicalTransactionLedger()
    ledger.register(replace(
        expected(), session_id="session", session_generation=4
    ))
    with pytest.raises(PhysicalReceiptError, match="session"):
        ledger.observe(
            ack(receipt()),
            live_context_epochs={"ctx": 3},
            live_context_sessions={"ctx": ("session", 5)},
        )
    assert ledger.pending_count == 0
    ledger.register(replace(
        expected("current"), session_id="session", session_generation=5
    ))
    (completed,) = ledger.observe(
        ack(receipt("current")),
        live_context_epochs={"ctx": 3},
        live_context_sessions={"ctx": ("session", 5)},
    )
    assert completed.command_id == "current"


def test_h2d_child_and_mamba_only_pool_counts():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected(
        action="PREFETCH_GPU",
        children=(child(11, (11,), 0, 10, 10),),
    ))
    completed, = ledger.observe(
        ack(
            receipt(published=(11,), kv=0, mamba=2, total=10),
            direction="h2d", nodes=(11,), kv=0, mamba=2,
        ),
        live_context_epochs={"ctx": 3},
    )
    assert completed.action == "PREFETCH_GPU"
    assert completed.pool_bytes == (("kv", 0), ("mamba", 10))


def test_bounds_expiry_and_late_receipt_never_gain_credit(monkeypatch):
    import beliefkv.runtime.sglang_v0520_physical as physical

    clock = [0.0]
    monkeypatch.setattr(physical, "monotonic", lambda: clock[0])
    ledger = PhysicalTransactionLedger(max_pending=1, max_age_s=1)
    ledger.register(expected())
    with pytest.raises(PhysicalReceiptError):
        ledger.register(expected("other"))
    clock[0] = 2.0
    assert ledger.expire() == ("cmd",)
    assert ledger.pending_count == 0
    with pytest.raises(PhysicalReceiptError):
        ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3})


def test_missing_child_credit_with_unknown_nodes_remains_pending_then_expires(monkeypatch):
    import beliefkv.runtime.sglang_v0520_physical as physical

    clock = [0.0]
    monkeypatch.setattr(physical, "monotonic", lambda: clock[0])
    ledger = PhysicalTransactionLedger(max_age_s=1)
    ledger.register(expected())
    assert ledger.observe(
        ack(nodes=(99,), kv=1, mamba=0), live_context_epochs={"ctx": 3},
    ) == ()
    assert ledger.pending_count == 1
    clock[0] = 1.0
    assert ledger.expire() == ("cmd",)
