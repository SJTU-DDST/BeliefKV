from array import array
from collections import OrderedDict
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


def observer(nodes, *, diagnostics=False):
    audit = NativeReactiveTelemetry.__new__(NativeReactiveTelemetry)
    audit._cache = NS(tree_core=NS(node_by_id=nodes.__getitem__))
    audit._prefetch_prefix_diagnostics = diagnostics
    audit._pending_prefetch_use = OrderedDict()
    audit._unacked_prefetch_use = {}
    audit._prefetch_use_before_ack = {}
    audit._prefetch_ack_times = OrderedDict()
    records = []
    audit._emit = lambda kind, record: records.append((kind, record))
    return audit, records


def action(node_id, *, command=None):
    return NS(
        command_id=command or f"restore-{node_id}", action="PREFETCH_GPU",
        source="join_ticket", context_id="ctx", context_epoch=2,
        node_ids=(node_id,), pool_bytes=(("kv", 80),), num_bytes=80,
    )


def chain(depth):
    nodes = {0: NS(id=0, parent=None)}
    for index in range(1, depth + 1):
        nodes[index] = NS(
            id=index, parent=nodes[index - 1], key=array("q", range(8)),
            creation_time=float(index),
            component_data=(NS(value=object()),),
        )
    return nodes


@pytest.mark.parametrize("diagnostics", (False, True))
@pytest.mark.parametrize("before_ack", (False, True))
@pytest.mark.parametrize("mutation", (None, "allocation", "generation", "short_prefix"))
def test_scoped_issuance_preserves_complete_reuse_proof_and_ack_order(
    monkeypatch, diagnostics, before_ack, mutation,
):
    monkeypatch.setattr("beliefkv.runtime.v0520_native_telemetry.time.time", lambda: 100.)
    nodes = chain(16)
    baseline, expected = observer(nodes, diagnostics=diagnostics)
    candidate, actual = observer(nodes, diagnostics=diagnostics)
    actions = [action(index) for index in range(1, 17)]
    for audit, scoped in ((baseline, False), (candidate, True)):
        with audit._prefetch_issuance_scope() if scoped else nullcontext():
            for item in actions:
                audit.on_prefetch_issued(item)
        assert getattr(audit, "_prefetch_prefix_lengths", None) is None
    assert baseline._pending_prefetch_use == candidate._pending_prefetch_use
    assert baseline._unacked_prefetch_use == candidate._unacked_prefetch_use

    if mutation == "allocation":
        nodes[16].component_data[0].value = object()
    elif mutation == "generation":
        nodes[16].creation_time += 1.
    request = NS(
        rid="parent-next", last_node=16, best_match_node=16,
        origin_input_ids=list(range(129)),
        prefix_indices=range(120 if mutation == "short_prefix" else 128),
        cached_tokens_device=128, cached_tokens_host=0,
    )
    for audit, records in ((baseline, expected), (candidate, actual)):
        if not before_ack:
            for item in actions:
                audit.on_verified_action_ack(item)
        audit._record_prefetch_first_service(request, {"context_id": "ctx", "context_epoch": 3})
        if before_ack:
            assert not records
            for item in actions:
                audit.on_verified_action_ack(item)
        assert not audit._pending_prefetch_use
        assert not audit._unacked_prefetch_use
        assert not audit._prefetch_use_before_ack
    assert actual == expected
    uses = [record for kind, record in actual if kind == "action_use"]
    assert len(uses) == 16
    assert all(record["full_node_reused"] is True for record in uses[:-1])
    assert uses[-1]["full_node_reused"] is (mutation is None)


@pytest.mark.parametrize("order", ((1, 2, 3, 4), (4, 3, 2, 1), (2, 4, 1, 3)))
def test_scoped_prefix_lengths_handle_shared_branches_in_any_order(order):
    nodes = chain(3)
    nodes[4] = NS(
        id=4, parent=nodes[1], key=array("q", range(3)), creation_time=4.,
        component_data=(NS(value=object()),),
    )
    audit, _ = observer(nodes)
    with audit._prefetch_issuance_scope():
        for node_id in order:
            audit.on_prefetch_issued(action(node_id))
            assert audit._pending_prefetch_use[f"restore-{node_id}"][1][0][3] == {
                1: 8, 2: 16, 3: 24, 4: 11,
            }[node_id]
    assert audit._prefetch_prefix_lengths is None


@pytest.mark.parametrize("mutation,expected_length", (
    ("reparent", 9), ("key_growth", 13), ("missing_key", None), ("root", None),
))
def test_issuance_after_scope_exit_recaptures_changed_native_topology(mutation, expected_length):
    nodes = chain(2)
    audit, _ = observer(nodes)
    with audit._prefetch_issuance_scope():
        audit.on_prefetch_issued(action(2, command="first"))
    assert audit._pending_prefetch_use["first"][1][0][3] == 16
    if mutation == "reparent":
        nodes[3] = NS(parent=nodes[0], key=array("q", (1,)))
        nodes[2].parent = nodes[3]
    elif mutation == "key_growth":
        nodes[1].key = array("q", range(5))
    elif mutation == "missing_key":
        nodes[1].key = None
    else:
        nodes[2].parent = None
    with audit._prefetch_issuance_scope():
        audit.on_prefetch_issued(action(2, command="second"))
    assert audit._pending_prefetch_use["second"][1][0][3] == expected_length
    assert audit._pending_prefetch_use["first"][1][0][3] == 16


@pytest.mark.parametrize("depth", (4095, 4096))
@pytest.mark.parametrize("prime_ancestor", (False, True))
def test_scoped_prefix_length_preserves_the_native_ancestry_bound(depth, prime_ancestor):
    nodes = chain(depth)
    audit, _ = observer(nodes)
    with audit._prefetch_issuance_scope():
        if prime_ancestor:
            audit.on_prefetch_issued(action(depth - 1))
        audit.on_prefetch_issued(action(depth))
    expected = depth * 8 if depth == 4095 else None
    assert audit._pending_prefetch_use[f"restore-{depth}"][1][0][3] == expected


def test_scoped_issuance_keeps_cyclic_ancestry_unverifiable():
    nodes = chain(2)
    nodes[1].parent = nodes[2]
    audit, _ = observer(nodes)
    with audit._prefetch_issuance_scope():
        audit.on_prefetch_issued(action(2))
    assert audit._pending_prefetch_use["restore-2"][1][0][3] is None
    assert audit._prefetch_prefix_lengths is None


def test_runtime_scope_clears_observer_cache_on_recording_failure():
    audit, _ = observer(chain(2))
    runtime = NativeAdmissionRuntime()
    runtime.prefetch_issued_callback = audit.on_prefetch_issued
    try:
        with pytest.raises(RuntimeError, match="writer failure"):
            with runtime._prefetch_issue_observation_scope():
                audit.on_prefetch_issued(action(2))
                assert len(audit._prefetch_prefix_lengths) == 2
                raise RuntimeError("writer failure")
        assert audit._prefetch_prefix_lengths is None
    finally:
        runtime.close()


def test_runtime_scope_preserves_callbacks_without_a_native_observer():
    runtime = NativeAdmissionRuntime()
    records = []
    runtime.prefetch_issued_callback = records.append
    try:
        with runtime._prefetch_issue_observation_scope():
            runtime.prefetch_issued_callback("record")
        assert records == ["record"]
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("issued", (0, 1, 16, 17, 32))
def test_partially_issued_burst_uses_actual_size_and_preserves_its_reservations(source, issued):
    runtime, _, cache, _, actions, _, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=32, native_budget=True,
        workflows=36, running=8, capture_issuance=True,
    )
    native = cache.prefetch_gpu_session_nodes
    cache.prefetch_gpu_session_nodes = lambda **kwargs: native(**{
        **kwargs, "nodes": kwargs["nodes"][:issued],
    })
    audit = runtime._cpu_issuance_probe
    try:
        with patch("time.monotonic", lambda: now), patch.object(
            audit, "_prefetch_issuance_scope", wraps=audit._prefetch_issuance_scope,
        ) as scope:
            dispatch()
        assert scope.call_count == int(issued > 16)
        assert len(actions) == runtime.physical_ledger.pending_count == issued
        assert len(audit._pending_prefetch_use) == len(audit._unacked_prefetch_use) == issued
        assert getattr(audit, "_prefetch_prefix_lengths", None) is None
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
    finally:
        runtime.close()
