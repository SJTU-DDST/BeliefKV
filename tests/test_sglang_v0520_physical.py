"""CPU-only checks of native merged ACK transaction credit."""

from dataclasses import replace
from types import SimpleNamespace as NS
from enum import Enum
from itertools import product
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_physical import (
    ActionLocalPrefetchCandidate,
    ActionLocalShadowCandidate,
    ContextSessionAnchors,
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PhysicalReceiptError,
    PhysicalTransactionLedger,
    PrefetchLoadStep,
    capture_action_local_shadow,
    has_host_backed_missing_copy,
    inspect_session_h2d_opportunity,
    missing_prepare_full_prefix_tokens,
    next_prefetch_gpu_step,
    plan_prefetch_gpu_steps,
    next_shadow_backup_step,
    shadow_backup_steps,
    prefetch_expectation_from_native_op,
    shadow_expectation_from_native_op,
    ShadowBackupStep,
)


def prepare_prefix_fixture(*, input_tokens=4, second_leaf=False, backed=False):
    from tests.test_sglang_v0520_observer import (
        ComponentData, UnifiedTreeCore, UnifiedTreeNode, _static_cache,
    )

    cache = _static_cache()
    nodes = {}
    for node_id, parent_id, tokens in ((0, None, 0), (1, 0, 2), (2, 1, 2), (3, 2, 4), (4, 1, 2)):
        full = range(tokens) if tokens else None
        components = {}
        for component, value, host in (
            (0, full, full if backed else range(1) if node_id == 1 else None),
            (2, (0,) if tokens else None, None),
        ):
            components[component] = ComponentData(
                value=value, host_value=host, lock_ref=0, host_lock_ref=0,
                session_ref=1, session_ids={"s"},
            )
        nodes[node_id] = UnifiedTreeNode(
            id=node_id, creation_time=node_id + 1.,
            parent=nodes.get(parent_id), key=range(tokens), component_data=components,
            write_through_pending_id=None, load_back_pending_id=None,
        )
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    leaves = ((3, 4.), (4, 5.)) if second_leaf else ((3, 4.),)
    anchors = ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1),
        ((0, leaves), (2, leaves)), 1., reusable_input_tokens=input_tokens,
    )
    return cache, anchors, nodes


@pytest.mark.parametrize("input_tokens", (0, 2, 3, 4, 8))
@pytest.mark.parametrize("second_leaf", (False, True))
@pytest.mark.parametrize("backed", (False, True))
def test_prepare_prefix_hint_matches_full_planner_and_excludes_output_tail(
    input_tokens, second_leaf, backed,
):
    cache, anchors, _ = prepare_prefix_fixture(
        input_tokens=input_tokens, second_leaf=second_leaf, backed=backed,
    )
    candidate = capture_action_local_shadow(cache, anchors, include_non_actionable=True)
    step = next_shadow_backup_step(candidate)
    assert missing_prepare_full_prefix_tokens(cache, anchors) == (
        step.missing_full_prefix_tokens if step is not None else 0
    )


def test_prepare_prefix_hint_refreshes_after_host_backup_or_eviction():
    cache, anchors, nodes = prepare_prefix_fixture()
    assert missing_prepare_full_prefix_tokens(cache, anchors) == 3
    nodes[1].component_data[0].host_value = range(2)
    assert missing_prepare_full_prefix_tokens(cache, anchors) == 2
    nodes[2].component_data[0].host_value = range(2)
    assert missing_prepare_full_prefix_tokens(cache, anchors) == 0
    nodes[1].component_data[0].host_value = None
    assert missing_prepare_full_prefix_tokens(cache, anchors) == 2


def test_prepare_prefix_hint_preserves_backed_input_nodes_but_excludes_output_tail():
    cache, anchors, nodes = prepare_prefix_fixture(second_leaf=True)
    for node_id in (1, 3, 4):
        nodes[node_id].component_data[0].host_value = nodes[node_id].component_data[0].value
    cache.tree_core.evictable_device_leaves = (nodes[3], nodes[4])
    backed = []
    assert missing_prepare_full_prefix_tokens(cache, anchors, backed_nodes=backed) == 2
    assert backed == [(4, 5.)]


def test_prepare_prefix_hint_does_not_publish_partial_or_replaced_backed_identities():
    cache, anchors, nodes = prepare_prefix_fixture(second_leaf=True)
    nodes[4].component_data[0].host_value = nodes[4].component_data[0].value
    cache.tree_core.evictable_device_leaves = (nodes[3], nodes[4])
    backed = []
    assert missing_prepare_full_prefix_tokens(cache, anchors, backed_nodes=backed) == 3
    assert backed == [(4, 5.)]
    lookup = cache.tree_core.node_by_id
    cache.tree_core.node_by_id = lambda node_id: (
        NS(**vars(nodes[4])) if node_id == 4 else lookup(node_id)
    )
    unchanged = [(99, 100.)]
    assert missing_prepare_full_prefix_tokens(
        cache, anchors, backed_nodes=unchanged,
    ) is None
    assert unchanged == [(99, 100.)]


@pytest.mark.parametrize("change", (
    lambda anchors, nodes: replace(anchors, reusable_input_tokens=None),
    lambda anchors, nodes: replace(anchors, reusable_input_tokens=-1),
    lambda anchors, nodes: replace(anchors, component_leaves=((0, ((3, 5.),)), (2, ((3, 5.),)))),
    lambda anchors, nodes: (setattr(nodes[1], "parent", nodes[3]), anchors)[1],
    lambda anchors, nodes: (setattr(nodes[2], "key", None), anchors)[1],
))
def test_unobservable_prepare_prefix_hint_retains_full_validation_path(change):
    cache, anchors, nodes = prepare_prefix_fixture()
    assert missing_prepare_full_prefix_tokens(cache, change(anchors, nodes)) is None


def test_prepare_prefix_hint_respects_ancestry_bound():
    cache, anchors, _ = prepare_prefix_fixture()
    assert missing_prepare_full_prefix_tokens(cache, anchors, max_nodes=3) is None


@pytest.mark.parametrize("node_id", (1, 2, 3))
@pytest.mark.parametrize("pool", (0, 2))
def test_missing_copy_hint_detects_live_host_only_extents_and_state(node_id, pool):
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    assert has_host_backed_missing_copy(cache, anchors) is False
    data = nodes[node_id].component_data[pool]
    data.host_value = data.value
    data.value = None
    assert has_host_backed_missing_copy(cache, anchors) is True
    data.value = data.host_value
    assert has_host_backed_missing_copy(cache, anchors) is False
    data.value = None
    data.host_value = None
    assert has_host_backed_missing_copy(cache, anchors) is False


@pytest.mark.parametrize("second_leaf", (False, True))
@pytest.mark.parametrize("input_tokens", (0, 2, 4, 8))
@pytest.mark.parametrize("full_missing", (False, True))
@pytest.mark.parametrize("mamba_missing", (False, True))
def test_missing_copy_shortcut_never_skips_a_complete_restore_plan(
    second_leaf, input_tokens, full_missing, mamba_missing,
):
    cache, anchors, nodes = prepare_prefix_fixture(
        backed=True, second_leaf=second_leaf, input_tokens=input_tokens,
    )
    for node in nodes.values():
        for pool, missing in ((0, full_missing), (2, mamba_missing)):
            data = node.component_data[pool]
            data.host_value = data.value
            if missing:
                data.value = None
    normal = inspect_session_h2d_opportunity(cache, anchors, max_steps=8)
    optimized = inspect_session_h2d_opportunity(
        cache, anchors, max_steps=8, skip_empty_restore=True,
    )
    assert optimized.steps == normal.steps
    if normal.step is not None:
        assert optimized == normal
        assert has_host_backed_missing_copy(cache, anchors) is True


@pytest.mark.parametrize("change", (
    lambda anchors, nodes: replace(anchors, component_leaves=((0, ((3, 5.),)), (2, ((3, 5.),)))),
    lambda anchors, nodes: (setattr(nodes[1], "parent", nodes[3]), anchors)[1],
    lambda anchors, nodes: (setattr(nodes[2], "component_data", {}), anchors)[1],
    lambda anchors, nodes: replace(anchors, component_leaves=((0, ()), (2, ((3, 4.),)))),
))
def test_unknown_missing_copy_hint_uses_authoritative_snapshot(change):
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    anchors = change(anchors, nodes)
    assert has_host_backed_missing_copy(cache, anchors) is None
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.capture_action_local_shadow",
        return_value=None,
    ) as capture:
        result = inspect_session_h2d_opportunity(cache, anchors, skip_empty_restore=True)
    capture.assert_called_once()
    assert result.no_step_reason == "closure_unobservable"


def test_empty_restore_shortcut_skips_summary_allocation_without_hiding_unknown_detail():
    cache, anchors, _ = prepare_prefix_fixture(backed=True, second_leaf=True)
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.capture_action_local_shadow",
        side_effect=AssertionError("empty restore should not allocate a detailed closure"),
    ):
        result = inspect_session_h2d_opportunity(cache, anchors, skip_empty_restore=True)
    assert result.no_step_reason == "no_host_backed_missing_copy"
    assert result.host_backed_full_missing_device_tokens == 0
    assert result.unbacked_full_nodes is None
    assert result.candidate is None


@pytest.mark.parametrize("input_tokens", (0, 2, 4, 7, 8))
@pytest.mark.parametrize("second_leaf", (False, True))
def test_input_state_shortcut_preserves_mixed_checkpoint_restore_plans(
    input_tokens, second_leaf,
):
    for states in product(("device", "host", "absent"), repeat=4 if second_leaf else 3):
        cache, anchors, nodes = prepare_prefix_fixture(
            backed=True, second_leaf=second_leaf, input_tokens=input_tokens,
        )
        for node_id, state in enumerate(states, 1):
            data = nodes[node_id].component_data[2]
            data.value = (0,) if state == "device" else None
            data.host_value = (0,) if state == "host" else None
        normal = inspect_session_h2d_opportunity(cache, anchors, max_steps=8)
        optimized = inspect_session_h2d_opportunity(
            cache, anchors, max_steps=8, skip_empty_restore=True,
        )
        assert optimized.steps == normal.steps, (input_tokens, second_leaf, states)
        if normal.step is not None:
            assert optimized == normal


def test_input_state_shortcut_ignores_older_and_generated_states_without_claiming_their_absence():
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    for node_id in (1, 3):
        state = nodes[node_id].component_data[2]
        state.host_value, state.value = state.value, None
    assert has_host_backed_missing_copy(cache, anchors) is True
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is False
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.capture_action_local_shadow",
        side_effect=AssertionError("historical state should not expand a closure"),
    ):
        result = inspect_session_h2d_opportunity(cache, anchors, skip_empty_restore=True)
    assert result.step is None
    assert result.host_backed_full_missing_device_tokens == 0
    assert result.host_backed_mamba_missing_device_nodes is None
    assert result.unbacked_full_nodes is None


def test_input_state_shortcut_refreshes_after_selected_state_demoted():
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    state = nodes[1].component_data[2]
    state.host_value, state.value = state.value, None
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is False
    selected = nodes[2].component_data[2]
    selected.host_value, selected.value = selected.value, None
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is True
    result = inspect_session_h2d_opportunity(cache, anchors, skip_empty_restore=True)
    assert [step.node_id for step in result.steps] == [2]
    assert result.required_mamba_slots == 1


def test_input_state_shortcut_keeps_uncapped_mode_and_unknown_geometry_conservative():
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    state = nodes[1].component_data[2]
    state.host_value, state.value = state.value, None
    assert has_host_backed_missing_copy(
        cache, replace(anchors, reusable_input_tokens=None), input_checkpoint_only=True,
    ) is True
    nodes[2].key = None
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is None


@pytest.mark.parametrize("enum_pool_name", (False, True))
def test_native_shadow_expectation_uses_exact_operation_before_ack(enum_pool_name):
    class Pool(str, Enum):
        KV = "kv"
        MAMBA = "mamba"

    step = ShadowBackupStep(
        PrefillCandidateKey("r", "w", "i", "c", 3, 0, "s", 4),
        12, 5, 11, 4, include_mamba=True,
    )
    group = NS(entry_map={
        Pool.KV: NS(host_pool=NS(size_per_token=10)),
        Pool.MAMBA: NS(host_pool=NS(size_per_token=5)),
    })
    mamba_host_indices = (7,)
    op = NS(beliefkv_command_id="prepare-1", node_ids=[11],
            device_indices=(0, 1), host_indices=(2, 3),
            pool_transfers=[NS(
                name=Pool.MAMBA if enum_pool_name else "mamba",
                host_indices=mamba_host_indices, indices_from_pool=None,
            )])
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
    assert expected.children[0].anchor_creation_time == 4
    assert dict(expected.children[0].host_destination_indices)["kv"] is op.host_indices
    assert dict(expected.children[0].host_destination_indices)["mamba"] is mamba_host_indices
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


def test_prepare_ack_crosses_only_one_epoch_with_same_live_native_session():
    obligation = replace(expected(), session_id="s", session_generation=1)
    for epoch, generation, accepted in ((4, 1, True), (5, 1, False), (4, 2, False)):
        ledger = PhysicalTransactionLedger()
        ledger.register(obligation)
        assert ledger.pending_transfer_sessions == (("ctx", 3, "s", 1),)
        if accepted:
            result = ledger.observe(
                ack(receipt()), live_context_epochs={"ctx": epoch},
                live_context_sessions={"ctx": ("s", generation)},
            )
            assert result[0].action == "PREPARE_HOST"
        else:
            with pytest.raises(PhysicalReceiptError):
                ledger.observe(
                    ack(receipt()), live_context_epochs={"ctx": epoch},
                    live_context_sessions={"ctx": ("s", generation)},
                )


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
    assert ledger.pending_action_count("PREFETCH_GPU") == 1
    assert ledger.pending_action_count("PREPARE_HOST") == 0
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


@pytest.mark.parametrize("sidecar", (None, Pool.KV))
@pytest.mark.parametrize("full", (1, 128, 4096))
def test_full_shadow_preserves_sidecar_bytes_and_original_host_destinations(sidecar, full):
    original, operation, controller = native_prefetch(full=full, mamba=0, sidecar=sidecar)
    step = ShadowBackupStep(
        original.key, original.leaf_node_id, original.leaf_creation_time,
        original.node_id, original.creation_time, include_mamba=False,
    )
    obligation = shadow_expectation_from_native_op("prefetch-1", step, operation, controller)
    part = obligation.children[0]
    assert part.pool_bytes == (("kv", full * 10),)
    assert part.num_bytes == full * (10 if sidecar is None else 13)
    assert part.anchor_creation_time == step.creation_time
    assert part.host_destination_indices[0][0] == "kv"
    assert part.host_destination_indices[0][1] is operation.host_indices
    assert obligation.pool_bytes_per_token == (("kv", 10),)
    ledger = PhysicalTransactionLedger()
    ledger.register(obligation)
    assert ledger.pending_action_bytes("PREPARE_HOST") == part.num_bytes
    ledger.cancel_unsubmitted(obligation.command_id)
    assert ledger.pending_count == 0


@pytest.mark.parametrize("change", (
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": True}),
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": 1.}),
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": -1}),
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": 2}),
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": 1, "mamba": True}),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: True),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: 10.),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: 9),
    lambda op, ctrl: setattr(ctrl.mem_pool_host.entry_map[Pool.KV].host_pool, "size_per_token", True),
    lambda op, ctrl: setattr(ctrl.mem_pool_host.entry_map[Pool.KV].host_pool, "size_per_token", 10.),
    lambda op, ctrl: setattr(ctrl.mem_pool_host.entry_map[Pool.KV].host_pool, "size_per_token", 0),
))
def test_full_shadow_rejects_malformed_native_counts_bytes_and_geometry(change):
    original, operation, controller = native_prefetch(full=1, mamba=0, sidecar=None)
    step = ShadowBackupStep(
        original.key, original.leaf_node_id, original.leaf_creation_time,
        original.node_id, original.creation_time, include_mamba=False,
    )
    change(operation, controller)
    with pytest.raises(PhysicalReceiptError):
        shadow_expectation_from_native_op("prefetch-1", step, operation, controller)


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
        assert observe.call_count == 1
        assert capture_action_local_shadow(object(), anchors, max_nodes=1) is None
        leaf.pending_write_id = 11
        assert capture_action_local_shadow(object(), anchors) is None
        leaf.pending_write_id = None
        leaf.full_host_tokens = 10
        assert capture_action_local_shadow(object(), anchors) is None
        diagnostic = capture_action_local_shadow(
            object(), anchors, include_non_actionable=True,
        )
        assert diagnostic.missing_full_host_tokens == 0
        assert diagnostic.missing_mamba_host_nodes == 1
        assert next_shadow_backup_step(diagnostic) is None
        leaf.mamba_host_present = True
        assert capture_action_local_shadow(object(), anchors) is None
        backed = capture_action_local_shadow(
            object(), anchors, include_non_actionable=True,
        )
        assert backed is not None
        assert backed.missing_full_host_tokens == 0
        assert backed.missing_mamba_host_nodes == 0
        assert next_shadow_backup_step(backed) is None


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
    other = NS(**{**vars(leaf), "node_id": 12, "parent_id": 11, "creation_time": 5})
    distinct_anchors = replace(anchors, component_leaves=(
        (0, ((11, 4),)), (2, ((12, 5),)),
    ))
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        side_effect=(
            NS(observable=True, nodes=(leaf,)),
            NS(observable=True, nodes=(other, changed)),
        ),
    ):
        assert capture_action_local_shadow(object(), distinct_anchors) is None
    changed_generation = replace(anchors, component_leaves=(
        (0, ((11, 4),)), (2, ((11, 5),)),
    ))
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(leaf,)),
    ):
        assert capture_action_local_shadow(object(), changed_generation) is None


def test_capture_reuses_a_mamba_ancestor_and_refreshes_the_next_call():
    from beliefkv.runtime.sglang_v0520_observer import observe_unified_node_closure

    cache, anchors, nodes = prepare_prefix_fixture()
    anchors = replace(anchors, component_leaves=(
        (0, ((3, 4.),)), (2, ((2, 3.),)),
    ))
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        wraps=observe_unified_node_closure,
    ) as observe:
        candidate = capture_action_local_shadow(
            cache, anchors, for_prefetch=True, include_non_actionable=True,
        )
        assert candidate is not None
        assert observe.call_count == 1
        assert {node.node_id for node in candidate.nodes} == {0, 1, 2, 3}
        assert candidate.missing_full_device_tokens == 0

        nodes[2].component_data[0].value = None
        nodes[2].component_data[0].host_value = range(2)
        nodes[2].component_data[2].value = None
        nodes[2].component_data[2].host_value = (0,)
        candidate = capture_action_local_shadow(
            cache, anchors, for_prefetch=True, include_non_actionable=True,
        )
        assert observe.call_count == 2
        assert candidate.missing_full_device_tokens == 2
        checkpoint = next(node for node in candidate.nodes if node.node_id == 2)
        assert checkpoint.full_device_tokens == 0
        assert checkpoint.mamba_host_present and not checkpoint.mamba_device_present


def test_capture_rejects_a_changed_mamba_generation_on_the_full_path():
    from beliefkv.runtime.sglang_v0520_observer import observe_unified_node_closure

    cache, anchors, _ = prepare_prefix_fixture()
    anchors = replace(anchors, component_leaves=(
        (0, ((3, 4.),)), (2, ((2, 99.),)),
    ))
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        wraps=observe_unified_node_closure,
    ) as observe:
        assert capture_action_local_shadow(cache, anchors) is None
        assert observe.call_count == 1


def test_capture_still_reads_a_full_child_after_its_mamba_ancestor():
    from beliefkv.runtime.sglang_v0520_observer import observe_unified_node_closure

    cache, anchors, _ = prepare_prefix_fixture()
    anchors = replace(anchors, component_leaves=(
        (2, ((2, 3.),)), (0, ((3, 4.),)),
    ))
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        wraps=observe_unified_node_closure,
    ) as observe:
        candidate = capture_action_local_shadow(cache, anchors)
        assert candidate is not None
        assert observe.call_count == 2
        assert {node.node_id for node in candidate.nodes} == {0, 1, 2, 3}


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
    full_gpu=0, full_host=0, mamba_gpu=False, mamba_host=False, key_tokens=None,
):
    return NS(
        node_id=node_id, parent_id=parent_id, creation_time=created,
        full_device_tokens=full_gpu, full_host_tokens=full_host,
        mamba_device_present=mamba_gpu, mamba_host_present=mamba_host,
        pending_write_id=None, pending_load_id=None,
        key_tokens=key_tokens,
    )


def prefetch_anchors(full_leaf=12, mamba_leaf=12):
    return ContextSessionAnchors(
        PrefillCandidateKey("r", "w", "i", "c", 3, 0, "s", 4),
        ((0, ((full_leaf, 5),)), (2, ((mamba_leaf, 5),))),
        10.0,
    )


def test_h2d_observer_separates_resident_unbacked_and_blocked_closures():
    anchors = prefetch_anchors()
    root = prefetch_node(0, None, 1)
    leaf = prefetch_node(12, 0, 5, full_gpu=8, mamba_gpu=True)
    headroom = NS(
        observable=True, device_full_free_tokens=100,
        device_mamba_free_slots=4,
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=headroom,
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        side_effect=lambda cache, node_id, max_nodes:
        NS(observable=True, nodes=(leaf, root)),
    ):
        resident = inspect_session_h2d_opportunity(object(), anchors)
        assert resident.no_step_reason == "already_device_resident"
        assert resident.host_backed_full_missing_device_tokens == 0
        leaf.full_device_tokens = 0
        leaf.mamba_device_present = False
        unbacked = inspect_session_h2d_opportunity(object(), anchors)
        assert unbacked.no_step_reason == "no_host_backed_step"
        assert unbacked.unbacked_full_nodes == 1
        assert unbacked.unbacked_mamba_leaves == 1
        leaf.full_host_tokens = 8
        backed = inspect_session_h2d_opportunity(object(), anchors)
        assert backed.step is not None
        assert backed.host_backed_full_missing_device_tokens == 8

    parent = prefetch_node(11, 0, 4)
    leaf.parent_id = 11
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=headroom,
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(leaf, parent, root)),
    ):
        blocked = inspect_session_h2d_opportunity(object(), anchors)
        assert blocked.step is None
        assert blocked.no_step_reason == "host_backed_step_blocked"
        assert blocked.blocked_detail == "parent_full_not_device"
        assert blocked.host_backed_full_missing_device_tokens == 8


@pytest.mark.parametrize("failure,detail", (
    ("ancestry_cycle", "invalid or cyclic node ancestry"),
    ("changed_anchor", "replaced_leaf_anchor"),
    ("pending_write", "pending_write_transfer"),
    ("pending_load", "pending_load_transfer"),
))
def test_h2d_opportunity_preserves_capture_failure_without_rewalking(failure, detail):
    from beliefkv.runtime.sglang_v0520_observer import observe_unified_node_closure

    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    if failure == "ancestry_cycle":
        nodes[1].parent = nodes[3]
    elif failure == "changed_anchor":
        nodes[3].creation_time += 1
    elif failure == "pending_write":
        nodes[3].write_through_pending_id = 1
    else:
        nodes[3].load_back_pending_id = 1
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        wraps=observe_unified_node_closure,
    ) as observe:
        result = inspect_session_h2d_opportunity(cache, anchors)
    assert observe.call_count == 1
    assert result.no_step_reason == "closure_unobservable"
    assert result.blocked_detail == detail
    assert result.step is None and not result.steps
    assert result.candidate is None and result.fits_current_free_lists is None
    assert result.host_backed_full_missing_device_tokens is None


@pytest.mark.parametrize("depth", (65, 300))
@pytest.mark.parametrize("backed", (False, True))
def test_deep_prefix_planning_clips_output_and_preserves_transfer_budget(depth, backed):
    from tests.test_sglang_v0520_observer import _node

    cache, anchors, _ = prepare_prefix_fixture()
    checkpoint = depth - 4
    nodes = {0: _node(0)}
    for node_id in range(1, depth + 1):
        node = _node(
            node_id, nodes[node_id - 1],
            device=() if backed else [node_id],
            host=[node_id] if backed else (),
        )
        node.key = (node_id,)
        node.component_data[2].value = (0,) if not backed and node_id == checkpoint else None
        node.component_data[2].host_value = (0,) if backed and node_id == checkpoint else None
        nodes[node_id] = node
    nodes[0].key = ()
    cache.tree_core.node_by_id = nodes.__getitem__
    anchors = replace(
        anchors, reusable_input_tokens=checkpoint,
        component_leaves=(
            (0, ((depth, nodes[depth].creation_time),)),
            (2, ((checkpoint, nodes[checkpoint].creation_time),)),
        ),
    )
    failures = []
    candidate = capture_action_local_shadow(
        cache, anchors, for_prefetch=backed,
        include_non_actionable=True, on_failure=failures.append,
    )
    assert candidate is not None and len(candidate.nodes) == depth + 1
    assert failures == []
    assert capture_action_local_shadow(cache, anchors, max_nodes=64) is None
    if backed:
        steps = plan_prefetch_gpu_steps(candidate, max_steps=16)
        assert [step.node_id for step in steps] == list(range(1, 17))
        assert all(not step.include_mamba for step in steps)
        full_plan = plan_prefetch_gpu_steps(candidate)
        assert [step.node_id for step in full_plan] == list(range(1, checkpoint + 1))
        assert [step.node_id for step in full_plan if step.include_mamba] == [checkpoint]
        ledger_plan = plan_prefetch_gpu_steps(candidate, max_steps=64)
        assert ledger_plan == full_plan[:64]
        for node_id in range(1, checkpoint):
            nodes[node_id].component_data[0].value = [node_id]
        refreshed = capture_action_local_shadow(cache, anchors, for_prefetch=True)
        assert [(step.node_id, step.include_mamba) for step in plan_prefetch_gpu_steps(refreshed)] == [
            (checkpoint, True),
        ]
        opportunity = inspect_session_h2d_opportunity(cache, anchors)
        assert opportunity.required_full_tokens == opportunity.required_mamba_slots == 1
    else:
        assert missing_prepare_full_prefix_tokens(cache, anchors) == checkpoint
        assert missing_prepare_full_prefix_tokens(cache, anchors, max_nodes=64) is None
        steps = shadow_backup_steps(candidate, max_steps=16)
        assert [step.node_id for step in steps] == list(range(1, 17))
        assert steps[0].missing_full_prefix_tokens == checkpoint
        assert all(not step.include_mamba for step in steps)
    nodes[1].parent = nodes[depth]
    assert capture_action_local_shadow(cache, anchors, for_prefetch=backed) is None
    assert missing_prepare_full_prefix_tokens(cache, anchors) is None
    nodes[1].parent = nodes[0]
    nodes[depth].creation_time += 1
    assert capture_action_local_shadow(cache, anchors, for_prefetch=backed) is None


def test_capture_failure_callback_does_not_change_successful_candidate():
    cache, anchors, _ = prepare_prefix_fixture(backed=True)
    failures = []
    original = capture_action_local_shadow(
        cache, anchors, for_prefetch=True, include_non_actionable=True,
    )
    observed = capture_action_local_shadow(
        cache, anchors, for_prefetch=True, include_non_actionable=True,
        on_failure=failures.append,
    )
    assert original is not None and original == observed
    assert failures == []


def test_h2d_observer_reports_mamba_leaf_outside_full_session_path():
    anchors = prefetch_anchors(full_leaf=12, mamba_leaf=13)
    root = prefetch_node(0, None, 1)
    full_leaf = prefetch_node(12, 0, 5, full_gpu=8)
    mamba_leaf = prefetch_node(
        13, 0, 5, full_gpu=8, mamba_host=True,
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=NS(observable=True, device_full_free_tokens=100,
                        device_mamba_free_slots=4),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        side_effect=lambda cache, node_id, max_nodes: NS(
            observable=True,
            nodes=(full_leaf if node_id == 12 else mamba_leaf, root),
        ),
    ):
        observed = inspect_session_h2d_opportunity(object(), anchors)
    assert observed.step is None
    assert observed.no_step_reason == "host_backed_step_blocked"
    assert observed.blocked_detail == "mamba_leaf_outside_full_path"


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
        assert step == PrefetchLoadStep(anchors.key, 12, 5, 11, 4, include_mamba=False)
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


def test_prefetch_uses_input_checkpoint_instead_of_generated_output_leaf():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=10)
    root = prefetch_node(0, None, 1, key_tokens=0)
    prompt = prefetch_node(
        11, 0, 4, full_gpu=8, mamba_host=True, key_tokens=8,
    )
    output = prefetch_node(
        12, 11, 5, full_host=4, mamba_host=True, key_tokens=4,
    )
    candidate = ActionLocalPrefetchCandidate(anchors, (root, prompt, output), 4, 1)
    assert next_prefetch_gpu_step(candidate) == PrefetchLoadStep(
        anchors.key, 12, 5, 11, 4,
    )
    prompt.mamba_device_present = True
    assert next_prefetch_gpu_step(candidate) is None


@pytest.mark.parametrize("tail_key_tokens", (-1, True, 1.5, None))
def test_prefetch_rejects_invalid_suffix_length_after_reusable_checkpoint(tail_key_tokens):
    anchors = replace(prefetch_anchors(), reusable_input_tokens=8)
    root = prefetch_node(0, None, 1, key_tokens=0)
    checkpoint = prefetch_node(
        11, 0, 4, full_host=8, mamba_host=True, key_tokens=8,
    )
    output = prefetch_node(
        12, 11, 5, full_host=4, mamba_host=True, key_tokens=tail_key_tokens,
    )
    candidate = ActionLocalPrefetchCandidate(anchors, (output, root, checkpoint), 12, 0)
    assert plan_prefetch_gpu_steps(candidate) == ()


def test_prefetch_does_not_restore_full_beyond_the_only_reusable_mamba_boundary():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=10)
    root = prefetch_node(0, None, 1, key_tokens=0)
    prompt = prefetch_node(11, 0, 4, full_gpu=8, key_tokens=8)
    output = prefetch_node(
        12, 11, 5, full_host=4, mamba_host=True, key_tokens=4,
    )
    candidate = ActionLocalPrefetchCandidate(anchors, (root, prompt, output), 4, 0)
    assert next_prefetch_gpu_step(candidate) is None


def test_input_checkpoint_prefetch_still_restores_full_ancestors_first():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    root = prefetch_node(0, None, 1, key_tokens=0)
    ancestor = prefetch_node(11, 0, 4, full_host=8, key_tokens=8)
    checkpoint = prefetch_node(
        12, 11, 5, full_host=4, mamba_host=True, key_tokens=4,
    )
    candidate = ActionLocalPrefetchCandidate(anchors, (root, ancestor, checkpoint), 12, 0)
    assert next_prefetch_gpu_step(candidate).node_id == 11
    ancestor.full_device_tokens = 8
    assert next_prefetch_gpu_step(candidate).node_id == 12


def test_prefetch_burst_plans_full_ancestors_and_only_reusable_state():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    root = prefetch_node(0, None, 1, key_tokens=0)
    ancestor = prefetch_node(11, 0, 4, full_host=8, mamba_host=True, key_tokens=8)
    checkpoint = prefetch_node(12, 11, 5, full_host=4, mamba_host=True, key_tokens=4)
    candidate = ActionLocalPrefetchCandidate(anchors, (root, ancestor, checkpoint), 12, 0)
    steps = plan_prefetch_gpu_steps(candidate)
    assert [(step.node_id, step.include_mamba) for step in steps] == [(11, False), (12, True)]
    assert plan_prefetch_gpu_steps(candidate, max_steps=1) == steps[:1]
    ancestor.pending_load_id = 11
    assert plan_prefetch_gpu_steps(candidate) == ()


@pytest.mark.parametrize("max_steps", (None, 1, 2, 8))
@pytest.mark.parametrize("extra_leaf", (False, True))
def test_prefetch_nonmonotonic_node_ids_preserve_ancestry_and_leaf_provenance(
    max_steps, extra_leaf,
):
    root = prefetch_node(20, None, 120, key_tokens=0)
    ancestor = prefetch_node(3, 20, 103, full_host=4, key_tokens=4)
    checkpoint = prefetch_node(
        11, 3, 111, full_host=4, mamba_host=True, key_tokens=4,
    )
    output = prefetch_node(
        2, 11, 102, full_host=4, mamba_host=True, key_tokens=4,
    )
    nodes = (output, checkpoint, root, ancestor)
    full_leaves, mamba_leaves = ((2, 102),), ((11, 111),)
    expected = [(3, False, 2, 102), (11, True, 2, 102)]
    if extra_leaf:
        sibling = prefetch_node(
            5, 3, 105, full_host=4, mamba_host=True, key_tokens=4,
        )
        nodes += (sibling,)
        full_leaves += ((5, 105),)
        mamba_leaves += ((5, 105),)
        expected.insert(1, (5, True, 5, 105))
    anchors = replace(
        prefetch_anchors(), reusable_input_tokens=8,
        component_leaves=((0, full_leaves), (2, mamba_leaves)),
    )
    candidate = ActionLocalPrefetchCandidate(anchors, nodes, 12 + 4 * extra_leaf, 0)
    steps = plan_prefetch_gpu_steps(candidate, max_steps=max_steps)
    assert [
        (step.node_id, step.include_mamba, step.leaf_node_id, step.leaf_creation_time)
        for step in steps
    ] == expected[:max_steps]
    assert all(step.key == anchors.key for step in steps)
    assert all(step.creation_time == 100 + step.node_id for step in steps)


def test_prefetch_burst_capacity_counts_all_extents_and_selected_state():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    nodes = (
        prefetch_node(12, 11, 5, full_host=4, mamba_host=True, key_tokens=4),
        prefetch_node(11, 0, 4, full_host=8, mamba_host=True, key_tokens=8),
        prefetch_node(0, None, 1, key_tokens=0),
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=nodes),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=NS(observable=True, device_full_free_tokens=10, device_mamba_free_slots=1),
    ):
        opportunity = inspect_session_h2d_opportunity(object(), anchors, max_steps=16)
    assert len(opportunity.steps) == 2
    assert opportunity.required_full_tokens == 12
    assert opportunity.required_mamba_slots == 1
    assert opportunity.fits_current_free_lists is False


def test_prefetch_burst_uses_fitting_prefix_without_a_new_closure_snapshot():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    nodes = (
        prefetch_node(12, 11, 5, full_host=4, mamba_host=True, key_tokens=4),
        prefetch_node(11, 0, 4, full_host=8, mamba_host=True, key_tokens=8),
        prefetch_node(0, None, 1, key_tokens=0),
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=nodes),
    ) as closure, patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=NS(observable=True, device_full_free_tokens=10, device_mamba_free_slots=0),
    ):
        opportunity = inspect_session_h2d_opportunity(
            object(), anchors, max_steps=16, fit_current_capacity=True,
        )
    assert len(opportunity.steps) == 1
    assert opportunity.steps[0].node_id == 11
    assert opportunity.required_full_tokens == 8
    assert opportunity.required_mamba_slots == 0
    assert opportunity.fits_current_free_lists is True
    closure.assert_called_once()


@pytest.mark.parametrize("max_steps,fit_current_capacity", ((1, False), (16, True), (16, False)))
def test_checkpoint_cost_keeps_the_complete_plan_before_issue_limits(
    max_steps, fit_current_capacity,
):
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    nodes = (
        prefetch_node(12, 11, 5, full_host=4, mamba_host=True, key_tokens=4),
        prefetch_node(11, 0, 4, full_host=8, mamba_host=True, key_tokens=8),
        prefetch_node(0, None, 1, key_tokens=0),
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=nodes),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=NS(observable=True, device_full_free_tokens=8, device_mamba_free_slots=0),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.plan_prefetch_gpu_steps",
        wraps=plan_prefetch_gpu_steps,
    ) as planner:
        opportunity = inspect_session_h2d_opportunity(
            object(), anchors, max_steps=max_steps,
            fit_current_capacity=fit_current_capacity, include_checkpoint_plan=True,
        )
    planner.assert_called_once()
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.plan_prefetch_gpu_steps",
        side_effect=AssertionError("replanned the same checkpoint"),
    ):
        blocked = replace(
            opportunity, step=None, steps=(), required_full_tokens=0, required_mamba_slots=0,
        )
        cost = NativeAdmissionRuntime._report_restore_cost_plan(blocked)
    assert [(step.node_id, step.include_mamba) for step in cost.steps] == [
        (11, False), (12, True),
    ]
    assert cost.required_full_tokens == 12 and cost.required_mamba_slots == 1
    assert blocked.step is None and blocked.required_full_tokens == 0
    assert len(opportunity.steps) == (1 if max_steps == 1 or fit_current_capacity else 2)


def test_checkpoint_cost_is_recaptured_after_native_residency_changes():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    ancestor = prefetch_node(11, 0, 4, full_host=8, key_tokens=8)
    checkpoint = prefetch_node(12, 11, 5, full_host=4, mamba_host=True, key_tokens=4)
    nodes = (checkpoint, ancestor, prefetch_node(0, None, 1, key_tokens=0))
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=nodes),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=NS(observable=True, device_full_free_tokens=20, device_mamba_free_slots=1),
    ):
        first = inspect_session_h2d_opportunity(
            object(), anchors, include_checkpoint_plan=True,
        )
        ancestor.full_device_tokens = 8
        second = inspect_session_h2d_opportunity(
            object(), anchors, include_checkpoint_plan=True,
        )
        checkpoint.pending_load_id = 1
        in_flight = inspect_session_h2d_opportunity(
            object(), anchors, include_checkpoint_plan=True,
        )
    assert first.checkpoint_plan.full_tokens == 12
    assert second.checkpoint_plan.full_tokens == 4
    assert [step.node_id for step in second.checkpoint_plan.steps] == [12]
    assert in_flight.checkpoint_plan.steps == () and in_flight.step is None


def test_full_ancestor_restore_does_not_require_its_historical_mamba_slot():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    root = prefetch_node(0, None, 1, key_tokens=0)
    ancestor = prefetch_node(11, 0, 4, full_host=8, mamba_host=True, key_tokens=8)
    checkpoint = prefetch_node(
        12, 11, 5, full_host=4, mamba_host=True, key_tokens=4,
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=(checkpoint, ancestor, root)),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=NS(
            observable=True, device_full_free_tokens=8, device_mamba_free_slots=0,
        ),
    ):
        opportunity = inspect_session_h2d_opportunity(object(), anchors)
    assert opportunity.step.node_id == 11
    assert not opportunity.step.include_mamba
    assert opportunity.required_mamba_slots == 0
    assert opportunity.fits_current_free_lists
    ancestor.full_device_tokens = 8
    candidate = ActionLocalPrefetchCandidate(anchors, (root, ancestor, checkpoint), 4, 1)
    step = next_prefetch_gpu_step(candidate)
    assert step.node_id == 12 and step.include_mamba


def test_prepare_extends_full_prefix_without_copying_any_mamba_state():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    root = prefetch_node(0, None, 1, key_tokens=0)
    ancestor = prefetch_node(11, 0, 4, full_gpu=8, mamba_gpu=True, key_tokens=8)
    checkpoint = prefetch_node(12, 11, 5, full_gpu=4, mamba_gpu=True, key_tokens=4)
    candidate = ActionLocalShadowCandidate(anchors, (root, ancestor, checkpoint), 12, 2)
    step = next_shadow_backup_step(candidate)
    assert step.node_id == 11 and not step.include_mamba
    assert step.missing_full_prefix_tokens == 12
    ancestor.full_host_tokens = 8
    step = next_shadow_backup_step(candidate)
    assert step.node_id == 12 and not step.include_mamba
    assert step.missing_full_prefix_tokens == 4
    checkpoint.full_host_tokens = 4
    assert next_shadow_backup_step(candidate) is None
    assert not ancestor.mamba_host_present
    assert not checkpoint.mamba_host_present


def test_prepare_burst_skips_backed_full_and_keeps_ancestor_order_and_budget():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    root = prefetch_node(0, None, 1, key_tokens=0)
    backed = prefetch_node(10, 0, 3, full_gpu=4, full_host=4, key_tokens=4)
    ancestor = prefetch_node(11, 10, 4, full_gpu=4, mamba_gpu=True, key_tokens=4)
    checkpoint = prefetch_node(12, 11, 5, full_gpu=4, mamba_gpu=True, key_tokens=4)
    candidate = ActionLocalShadowCandidate(
        anchors, (checkpoint, ancestor, root, backed), 8, 2,
    )
    steps = shadow_backup_steps(candidate)
    assert [(step.node_id, step.missing_full_prefix_tokens, step.include_mamba)
            for step in steps] == [(11, 8, False), (12, 4, False)]
    assert shadow_backup_steps(candidate, max_steps=1) == steps[:1]
    assert next_shadow_backup_step(candidate) == steps[0]
    ancestor.full_host_tokens = 4
    assert [step.node_id for step in shadow_backup_steps(candidate)] == [12]
    checkpoint.full_host_tokens = 4
    assert shadow_backup_steps(candidate) == ()
    ancestor.full_host_tokens = 0
    assert [step.node_id for step in shadow_backup_steps(candidate)] == [11]


@pytest.mark.parametrize("pending_field", ("pending_write_id", "pending_load_id"))
def test_prepare_burst_does_not_plan_through_foreign_pending_ancestor(pending_field):
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    root = prefetch_node(0, None, 1, key_tokens=0)
    ancestor = prefetch_node(11, 0, 4, full_gpu=8, key_tokens=8)
    checkpoint = prefetch_node(12, 11, 5, full_gpu=4, mamba_gpu=True, key_tokens=4)
    setattr(ancestor, pending_field, 99)
    candidate = ActionLocalShadowCandidate(anchors, (root, ancestor, checkpoint), 12, 1)
    assert shadow_backup_steps(candidate) == ()


def test_prepare_burst_stays_on_one_leaf_and_excludes_generated_output():
    key = prefetch_anchors().key
    anchors = ContextSessionAnchors(
        key, ((0, ((13, 6), (14, 7))), (2, ((12, 5), (14, 7)))),
        10., reusable_input_tokens=12,
    )
    nodes = (
        prefetch_node(0, None, 1, key_tokens=0),
        prefetch_node(11, 0, 4, full_gpu=8, key_tokens=8),
        prefetch_node(12, 11, 5, full_gpu=4, mamba_gpu=True, key_tokens=4),
        prefetch_node(13, 12, 6, full_gpu=2000, mamba_gpu=True, key_tokens=2000),
        prefetch_node(14, 11, 7, full_gpu=4, mamba_gpu=True, key_tokens=4),
    )
    steps = shadow_backup_steps(ActionLocalShadowCandidate(anchors, nodes, 2016, 3))
    assert [(step.node_id, step.leaf_node_id) for step in steps] == [(11, 13), (12, 13)]


@pytest.mark.parametrize("include_tail", (False, True))
def test_prepare_single_leaf_uses_ancestry_order_with_nonmonotone_node_ids(include_tail):
    cache, anchors, nodes = prepare_prefix_fixture()
    remapped = {0: 42, 1: 17, 2: 3, 3: 9, 4: 99}
    for previous_id, node in nodes.items():
        node.id = remapped[previous_id]
    current = {node.id: node for node in nodes.values()}
    cache.tree_core.node_by_id = current.__getitem__
    anchors = replace(
        anchors, component_leaves=((0, ((9, 4.),)), (2, ((9, 4.),))),
    )
    candidate = capture_action_local_shadow(cache, anchors)
    assert candidate is not None
    before = [(node.id, node.component_data[0].host_value) for node in nodes.values()]
    steps = shadow_backup_steps(
        candidate, reclaim_tail_leaves=frozenset({9}) if include_tail else frozenset(),
    )
    assert [(step.node_id, step.missing_full_prefix_tokens) for step in steps] == (
        [(17, 7), (3, 6), (9, 4)] if include_tail else [(17, 3), (3, 2)]
    )
    assert all(step.leaf_node_id == 9 and not step.include_mamba for step in steps)
    assert shadow_backup_steps(
        candidate, max_steps=1,
        reclaim_tail_leaves=frozenset({9}) if include_tail else frozenset(),
    ) == steps[:1]
    assert [(node.id, node.component_data[0].host_value) for node in nodes.values()] == before


def test_prepare_prefix_budget_excludes_generation_after_reusable_checkpoint():
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    root = prefetch_node(0, None, 1, key_tokens=0)
    ancestor = prefetch_node(11, 0, 4, full_gpu=8, full_host=8, key_tokens=8)
    checkpoint = prefetch_node(12, 11, 5, full_gpu=4, mamba_gpu=True, key_tokens=4)
    generated = prefetch_node(13, 12, 6, full_gpu=2000, mamba_gpu=True, key_tokens=2000)
    anchors = replace(anchors, component_leaves=((0, ((13, 6),)), (2, ((13, 6),))))
    candidate = ActionLocalShadowCandidate(
        anchors, (root, ancestor, checkpoint, generated), 2004, 2,
    )
    step = next_shadow_backup_step(candidate)
    assert step.node_id == 12
    assert step.missing_full_prefix_tokens == 4


def test_full_only_receipt_rejects_unselected_mamba_before_enqueue():
    step, operation, controller = native_prefetch()
    with pytest.raises(PhysicalReceiptError, match="historical Mamba"):
        prefetch_expectation_from_native_op(
            "prefetch-1", replace(step, include_mamba=False), operation, controller,
        )
    with pytest.raises(PhysicalReceiptError, match="historical Mamba"):
        shadow_expectation_from_native_op(
            "prefetch-1",
            ShadowBackupStep(step.key, 12, 5, 11, 4, include_mamba=False),
            operation, controller,
        )


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


def split_shadow_case():
    parent = NS(
        id=535, creation_time=99, parent=None,
        component_data={0: NS(host_value=[100]), 2: NS(host_value=None)},
    )
    anchor = NS(
        id=37, creation_time=25, parent=parent,
        component_data={0: NS(host_value=[101]), 2: NS(host_value=7)},
    )
    nodes = {535: parent, 37: anchor}
    cache = NS(tree_core=NS(node_by_id=nodes.get))
    part = PhysicalChildExpectation(
        37, (37,), (("kv", 20), ("mamba", 5)), 25,
        anchor_creation_time=25,
        host_destination_indices=(("kv", [100, 101]), ("mamba", [7])),
    )
    obligation = expected(children=(part,))
    event = ack(
        receipt(anchor=37, published=(535, 37), total=25),
        nodes=(535, 37),
    )
    return obligation, event, cache, nodes


def test_d2h_split_ack_uses_actual_publication_and_exact_host_destinations():
    obligation, event, cache, _ = split_shadow_case()
    ledger = PhysicalTransactionLedger()
    ledger.register(obligation)
    completed, = ledger.observe(
        event, live_context_epochs={"ctx": 3}, native_cache=cache,
    )
    assert completed.node_ids == (535, 37)
    assert completed.pool_bytes == obligation.children[0].pool_bytes
    assert completed.num_bytes == 25
    assert ledger.pending_count == 0
    assert ledger._published_by_command == {}


@pytest.mark.parametrize("damage", (
    "foreign_parent", "recycled_anchor", "foreign_host_range",
    "foreign_mamba_slot", "missing_cache", "wrong_bytes", "h2d",
))
def test_split_ack_does_not_relax_identity_capacity_or_host_ownership(damage):
    obligation, event, cache, nodes = split_shadow_case()
    if damage == "foreign_parent":
        nodes[37].parent = None
    elif damage == "recycled_anchor":
        nodes[37].creation_time = 26
    elif damage == "foreign_host_range":
        nodes[535].component_data[0].host_value = [999]
    elif damage == "foreign_mamba_slot":
        nodes[37].component_data[2].host_value = 8
    elif damage == "missing_cache":
        cache = None
    elif damage == "wrong_bytes":
        event.child_commits[0].num_bytes = 26
    elif damage == "h2d":
        obligation = replace(obligation, action="PREFETCH_GPU")
        event.direction = "h2d"
    ledger = PhysicalTransactionLedger()
    ledger.register(obligation)
    with pytest.raises(PhysicalReceiptError):
        ledger.observe(event, live_context_epochs={"ctx": 3}, native_cache=cache)
    assert ledger.pending_count == 0
    assert ledger._published_by_command == {}


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
    assert ledger.pending_action_count("PREPARE_HOST") == 1
    ledger.cancel_unsubmitted("cmd")
    assert ledger.pending_count == 0
    assert ledger.pending_action_count("PREPARE_HOST") == 0
    with pytest.raises(PhysicalReceiptError, match="reused"):
        ledger.register(expected())
    with pytest.raises(PhysicalReceiptError, match="unknown"):
        ledger.cancel_unsubmitted("cmd")
    ledger.register(expected("other"))
    assert ledger.pending_action_count("PREPARE_HOST") == 1
    with pytest.raises(PhysicalReceiptError, match="unknown"):
        ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0
    assert ledger.pending_action_count("PREPARE_HOST") == 0


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
    assert ledger.pending_action_count("PREPARE_HOST") == 1


def test_two_child_command_waits_for_full_reconciliation_across_acks():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected(children=(
        child(11, (11, 12), 20, 5, 32),
        child(13, (13,), 10, 0, 10),
    )))
    assert ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3}) == ()
    assert ledger.pending_action_count("PREPARE_HOST") == 1
    second = ack(
        receipt(anchor=13, published=(13,), kv=1, mamba=0, total=10),
        nodes=(13,), kv=1, mamba=0,
    )
    completed, = ledger.observe(second, live_context_epochs={"ctx": 3})
    assert completed.node_ids == (11, 12, 13)
    assert completed.num_bytes == 42
    assert ledger.pending_action_count("PREPARE_HOST") == 0


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
    assert ledger.pending_action_count("PREPARE_HOST") == 0
    assert ledger.pending_action_count("PREFETCH_GPU") == 0


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


@pytest.mark.parametrize("transition", ("pending", "partial_ack", "invalidated", "cancelled", "completed"))
def test_untagged_ack_overlap_follows_entire_pending_command_lifetime(transition):
    ledger = PhysicalTransactionLedger()
    ledger.register(expected(children=(
        child(11, (11, 12), 20, 5, 32),
        child(13, (13,), 10, 0, 10),
    )))
    if transition in ("partial_ack", "completed"):
        assert ledger.observe(ack(receipt()), live_context_epochs={"ctx": 3}) == ()
    if transition == "completed":
        ledger.observe(ack(
            receipt(anchor=13, published=(13,), kv=1, mamba=0, total=10),
            nodes=(13,), kv=1, mamba=0,
        ), live_context_epochs={"ctx": 3})
    elif transition == "cancelled":
        ledger.cancel_unsubmitted("cmd")
    elif transition == "invalidated":
        ledger.invalidate_context("ctx")
    unrelated = ack(nodes=(99,), kv=1, mamba=0)
    assert ledger.observe(unrelated, live_context_epochs={"ctx": 3}) == ()
    overlap = ack(nodes=(99, 12), kv=2, mamba=0)
    if transition in ("cancelled", "completed"):
        assert ledger.observe(overlap, live_context_epochs={"ctx": 3}) == ()
    else:
        with pytest.raises(PhysicalReceiptError, match="missing child credit"):
            ledger.observe(overlap, live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0


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


def test_h2d_ack_forwards_only_one_epoch_in_the_same_native_session():
    from dataclasses import replace

    ledger = PhysicalTransactionLedger()
    ledger.register(replace(
        expected(action="PREFETCH_GPU"),
        session_id="session", session_generation=4,
    ))
    (completed,) = ledger.observe(
        ack(receipt(), direction="h2d"),
        live_context_epochs={"ctx": 4},
        live_context_sessions={"ctx": ("session", 4)},
    )
    assert completed.context_epoch == 3
    for live_epoch, live_session in ((5, ("session", 4)), (4, ("session", 5))):
        other = PhysicalTransactionLedger()
        other.register(replace(
            expected(action="PREFETCH_GPU"),
            session_id="session", session_generation=4,
        ))
        with pytest.raises(PhysicalReceiptError, match="epoch/session"):
            other.observe(
                ack(receipt(), direction="h2d"),
                live_context_epochs={"ctx": live_epoch},
                live_context_sessions={"ctx": live_session},
            )


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
    assert ledger.pending_action_count("PREFETCH_GPU") == 0


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
    assert ledger.pending_action_count("PREPARE_HOST") == 0
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
    assert ledger.pending_action_count("PREPARE_HOST") == 0


def test_pending_action_counts_do_not_traverse_unrelated_transactions():
    ledger = PhysicalTransactionLedger()
    for index in range(8):
        ledger.register(expected(
            f"command-{index}",
            action="PREPARE_HOST" if index % 2 == 0 else "PREFETCH_GPU",
            children=(child(100 + index, (100 + index,), 10, 0, 10),),
        ))

    class NoTraversal(dict):
        def values(self):
            raise AssertionError("action count must not traverse transactions")

        def items(self):
            raise AssertionError("action count must not traverse transactions")

        def __iter__(self):
            raise AssertionError("action count must not traverse transactions")

    original = ledger._pending
    with patch.object(ledger, "_pending", NoTraversal(original)):
        assert ledger.pending_action_count("PREPARE_HOST") == 4
        assert ledger.pending_action_count("PREFETCH_GPU") == 4
        assert ledger.pending_action_count("unknown") == 0
        assert ledger.pending_count == 8
    for index in range(8):
        ledger.cancel_unsubmitted(f"command-{index}")
        assert sum(
            ledger.pending_action_count(action) for action in ("PREPARE_HOST", "PREFETCH_GPU")
        ) == ledger.pending_count


def test_invalid_merged_receipt_retires_both_action_counts():
    ledger = PhysicalTransactionLedger()
    ledger.register(expected("prepare"))
    ledger.register(expected(
        "load", action="PREFETCH_GPU",
        children=(child(13, (13,), 10, 0, 10),),
    ))
    assert ledger.pending_action_count("PREPARE_HOST") == 1
    assert ledger.pending_action_count("PREFETCH_GPU") == 1
    with pytest.raises(PhysicalReceiptError, match="unknown"):
        ledger.observe(ack(receipt(command="missing")), live_context_epochs={"ctx": 3})
    assert ledger.pending_count == 0
    assert ledger.pending_action_count("PREPARE_HOST") == 0
    assert ledger.pending_action_count("PREFETCH_GPU") == 0
