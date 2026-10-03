import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1] / "third_party/sglang-v0.5.20/python/sglang/srt"


def native(file, cls, methods, namespace):
    tree = ast.parse((ROOT / file).read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls)
    body = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="Native", bases=[], keywords=[], body=body, decorator_list=[]),
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / file), "exec"), namespace)
    return namespace["Native"]()


def test_latest_session_snapshot_does_not_reject_long_request_history():
    tracker = native(
        "mem_cache/unified_cache/session_ref_tracker.py", "UnifiedSessionRefTracker",
        {"snapshot_latest_session_leaf_anchors", "snapshot_session_leaf_anchors"}, {},
    )
    leaves = [NS(id=i, creation_time=float(i), component_data=[
        NS(session_ids={"s"}), None, NS(session_ids={"s"}),
    ]) for i in range(20)]
    tracker.enable_session_radix_cache = True
    tracker._session_generations, tracker._closed_session_ids = {"s": 1}, {}
    tracker.components = tuple(NS(component_type=ct, _session_leaves={"s": leaves}) for ct in (0, 2))
    tracker.tree_core = NS(node_by_id=lambda node: leaves[node])
    tracker._latest_session_leaves = {"s": {0: leaves[19], 2: leaves[18]}}
    assert tracker.snapshot_session_leaf_anchors("s", 1) is None
    assert tracker.snapshot_latest_session_leaf_anchors("s", 1) == (
        (0, ((19, 19.0),)), (2, ((18, 18.0),)),
    )
    assert tracker.snapshot_latest_session_leaf_anchors("s", 2) is None
    tracker.components[1]._session_leaves["s"] = []
    assert tracker.snapshot_latest_session_leaf_anchors("s", 1) is None


@pytest.mark.parametrize("backed,locked,shared,live", [
    (True, False, False, True), (False, False, False, True),
    (True, True, False, True), (True, False, True, True),
    (True, False, False, False),
])
def test_pressure_parks_only_live_exclusive_unlocked_backed_parent(backed, locked, shared, live):
    cache = native(
        "mem_cache/unified_radix_cache.py", "UnifiedRadixCache",
        {"_evict_backed_join_parent"}, {"BASE_COMPONENT_TYPE": 0, "ComponentType": NS(MAMBA=2)},
    )
    node = NS(id=7, creation_time=2.0, component_data=[
        NS(session_ref=1), None, NS(
            value=[10], host_value=[20] if backed else None,
            lock_ref=int(locked), session_ref=2 if shared else 1,
        ),
    ], write_through_pending_id=None, load_back_pending_id=None)
    cache.is_write_back = True
    cache.ongoing_write_through = {}
    cache.beliefkv_join_pressure_validator = lambda *_: live
    cache.beliefkv_join_pressure_candidates = ((7, 2.0),)
    demote = Mock(return_value=NS(device_frees={}, host_frees={}, tracker={2: 1}))
    cache.tree_core = NS(
        lru_lists={2: NS(get_lru_no_lock=lambda: node)},
        node_by_id=lambda _: node, demote_backed_mamba_state=demote,
    )
    cache._free_values = Mock()
    cache._accumulate_tracker = lambda target, delta: target.update(delta)
    cache.beliefkv_join_pressure_parked = Mock()
    tracker = {0: 0, 2: 0}
    cache._evict_backed_join_parent(2, tracker)
    assert demote.call_count == int(backed and not locked and not shared and live)
    assert tracker[2] == int(backed and not locked and not shared and live)


def test_mamba_pressure_park_leaves_full_and_host_copy_untouched():
    tree = native(
        "mem_cache/unified_cache/unified_tree_core.py", "UnifiedTreeCore",
        {"demote_backed_mamba_state"},
        {"DemoteResult": lambda: NS(tracker={}, device_frees={}, host_frees={}),
         "ComponentType": NS(MAMBA=2), "EvictLayer": NS(DEVICE="device")},
    )
    full, host = object(), object()
    node = NS(component_data=[NS(value=full), None, NS(value=[1], host_value=host, lock_ref=0)])
    tree.node_by_id = lambda _: node
    tree.components_by_type = {2: NS()}
    tree._update_evictable_leaf_sets = Mock()

    def evict(current, comp, **kwargs):
        assert kwargs["target"] == "device"
        current.component_data[2].value = None
        kwargs["tracker"][2] = 1

    tree._evict_component_and_detach_lru = evict
    result = tree.demote_backed_mamba_state(7)
    assert result.tracker == {2: 1}
    assert node.component_data[0].value is full
    assert node.component_data[2].host_value is host
