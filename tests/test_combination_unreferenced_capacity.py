from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.prepare_calibrated_combination_oracle import calibrated_artifact
from tests.test_calibrated_combination_preparation import calibration, original
from tests.test_workflow_combination_runtime import configured, runtime_with_queue


def leaf(identity, tokens=200):
    data = NS(value=list(range(tokens)), host_value=list(range(tokens)),
              session_ref=0, session_ids=set(), lock_ref=0, host_lock_ref=0)
    return NS(id=identity, creation_time=float(identity), key=list(range(tokens)),
              backuped=True, component_data={0: data}, write_through_pending_id=None,
              load_back_pending_id=None)


def summary(nodes):
    cache = NS(tree_components=(0,), ongoing_write_through={})
    cache.tree_core = NS(
        evictable_device_leaves=nodes,
        node_by_id=lambda identity: next(row for row in nodes if row.id == identity),
    )
    a = NS(runtime=NS(_native_cache=cache), counts=Counter())
    controller = object.__new__(WorkflowCombinationController)
    controller.admission = a
    return controller, cache, a


@pytest.mark.parametrize("unsafe", (
    "none", "protected", "unbacked", "device_lock", "host_lock", "session_ref",
    "session_ids", "host_missing", "partial_host", "pending_write", "pending_load",
    "ongoing_write", "stale_leaf", "hybrid", "unknown_reference", "unknown_lock", "invalid_ids",
))
def test_only_settled_unreferenced_unprotected_full_leaves_supply_capacity(unsafe):
    node = leaf(4)
    controller, cache, _a = summary([node])
    full = node.component_data[0]
    protected = {4} if unsafe == "protected" else set()
    if unsafe == "unbacked":
        node.backuped = False
    elif unsafe == "device_lock":
        full.lock_ref = 1
    elif unsafe == "host_lock":
        full.host_lock_ref = 1
    elif unsafe == "session_ref":
        full.session_ref = 1
    elif unsafe == "session_ids":
        full.session_ids.add("ready-agent")
    elif unsafe == "unknown_reference":
        full.session_ref = None
    elif unsafe == "unknown_lock":
        full.host_lock_ref = None
    elif unsafe == "invalid_ids":
        full.session_ids = []
    elif unsafe == "host_missing":
        full.host_value = None
    elif unsafe == "partial_host":
        full.host_value.pop()
    elif unsafe == "pending_write":
        node.write_through_pending_id = 1
    elif unsafe == "pending_load":
        node.load_back_pending_id = 1
    elif unsafe == "ongoing_write":
        cache.ongoing_write_through[4] = node
    elif unsafe == "stale_leaf":
        cache.tree_core.node_by_id = lambda _identity: leaf(4)
    elif unsafe == "hybrid":
        cache.tree_components = (0, 2)
    assert controller._unreferenced_capacity(protected) == (((4, 4., 200),) if unsafe == "none" else ())
    assert len(full.value) == 200


def test_unreferenced_scan_is_bounded_and_does_not_free_or_backup_nodes():
    nodes = [leaf(index, 10) for index in range(100)]
    controller, _cache, a = summary(nodes)
    assert len(controller._unreferenced_capacity(set())) == 32
    assert a.counts["workflow_combination_unreferenced_leaf_checks"] == 32
    assert all(len(row.component_data[0].value) == 10 for row in nodes)


def test_duplicate_leaf_or_native_root_cannot_inflate_capacity():
    node = leaf(4)
    controller, cache, _a = summary([node, node])
    assert controller._unreferenced_capacity(set()) == ((4, 4., 200),)
    cache.tree_core.root_node = node
    assert controller._unreferenced_capacity(set()) == ()


def test_new_capacity_contract_is_fingerprinted_and_preserves_intrinsic_oracle_work():
    kwargs = {"calibration_path": "/costs", "calibration_sha256": "a" * 64}
    old = calibrated_artifact(original(), calibration(), **kwargs)
    new = calibrated_artifact(original(), calibration(), include_unreferenced_capacity=True, **kwargs)
    assert old["admission_cost_profile"]["kind"] == "r5_frozen_batch_cost_proxy"
    assert new["admission_cost_profile"]["kind"] == "r5_frozen_batch_cost_proxy_capacity_v2"
    assert new["admission_cost_profile"]["identity"] != old["admission_cost_profile"]["identity"]
    assert new["requests"] == old["requests"] and new["workflow_graphs"] == old["workflow_graphs"]


def test_actual_constructor_loads_the_explicit_capacity_revision(monkeypatch, tmp_path):
    config = configured(tmp_path)
    path = Path(config["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = "r5_frozen_batch_cost_proxy_capacity_v2"
    path.write_text(json.dumps(raw))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, value in arm_environment(config, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "capacity.sock"))
    try:
        assert runtime._agent_admission._combination.include_unreferenced_capacity is True
    finally:
        runtime.close()


@pytest.mark.parametrize("protected_prefix", (False, True))
def test_capacity_extension_reaches_selection_but_never_counts_a_candidate_prefix(
    monkeypatch, tmp_path, protected_prefix,
):
    from beliefkv.runtime import workflow_combination as combination

    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    controller = admission._combination
    controller.include_unreferenced_capacity = True
    node = leaf(99, 1000)
    cache = runtime._native_cache
    cache.tree_core = NS(evictable_device_leaves=[node], node_by_id=lambda identity: node)
    monkeypatch.setattr(combination, "observe_static_full_mamba_headroom", lambda _cache: NS(
        observable=True, device_full_free_tokens=0, device_mamba_free_slots=0,
    ))
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: {
        "checkpoint_tokens": 0, "missing_full_tokens": 0, "missing_mamba_slots": 0,
        "component_leaves": ((0, ((99, 99.),)),) if protected_prefix else (),
    })
    try:
        admission.tick()
        row = next(row for row in records if row["event"] == "workflow_combination_selection")
        if protected_prefix:
            assert row["resources"]["cold_device_tokens"] == 0
            assert row["unreferenced_cold_device_tokens"] == 0
            assert row["protected_prefix_anchor_ids"] == [99]
            assert admission.policy.programs["a"].active
        else:
            assert row["resources"]["cold_device_tokens"] == 1000
            assert row["unreferenced_cold_device_tokens"] == 1000
            assert set(row["decision"]["selected"]) == {"b", "c"}
        assert node.component_data[0].value is not None
        assert row["private_blocked_cold_tokens_before_prefix_protection"] == 0
        assert row["physical_capacity_credit"] is False
    finally:
        runtime.close()


def test_legacy_profile_keeps_the_original_capacity_summary(monkeypatch, tmp_path):
    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    try:
        assert admission._combination.include_unreferenced_capacity is False
        monkeypatch.setattr(
            admission._combination, "_unreferenced_capacity",
            lambda _protected: pytest.fail("legacy profile scanned unreferenced leaves"),
        )
        admission.tick()
    finally:
        runtime.close()
