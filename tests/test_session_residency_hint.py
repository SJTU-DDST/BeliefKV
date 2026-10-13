from pathlib import Path

import pytest

from beliefkv.runtime.sglang_v0520_physical import inspect_session_residency_hint
from scripts.benchmark_native_reentry_cpu import fixture, variant


@pytest.fixture(scope="module")
def native_reentry():
    return variant(Path(__file__).resolve().parents[1] / "third_party/sglang-v0.5.20")


def set_layout(cache, *, layout):
    tree = cache.tree_core
    leaves = cache.session_refs.snapshot_latest_session_leaf_anchors()[0][1]
    visited = set()
    for leaf, _ in leaves:
        node = tree.node_by_id(leaf)
        while node is not tree.root_node and node.id not in visited:
            visited.add(node.id)
            for component in (0, 2):
                data = node.component_data[component]
                data.value = data.host_value if layout == "resident" else None
            node = node.parent


@pytest.mark.parametrize("depth,leaves", [(4, 1), (8, 8), (64, 8)])
@pytest.mark.parametrize("layout", ["mixed", "resident", "host"])
def test_hint_matches_native_residency_for_identical_input(native_reentry, depth, leaves, layout):
    cache, request = fixture(native_reentry, {
        "tokens": 32768, "depth": depth, "leaves": leaves, "page_size": 1,
    })
    if layout != "mixed":
        set_layout(cache, layout=layout)
    actual = native_reentry.inspect(cache, request)
    hint = inspect_session_residency_hint(cache, request)
    assert hint is not None and hint["input_prefix_verified"] is False
    assert {
        key: hint[key] for key in (
            "checkpoint_tokens", "device_checkpoint_tokens",
            "missing_full_tokens", "missing_mamba_slots",
        )
    } == {key: actual[key] for key in hint if key != "input_prefix_verified"}


def test_hint_does_not_verify_changed_input_contents(native_reentry):
    cache, request = fixture(native_reentry, {
        "tokens": 32768, "depth": 8, "leaves": 1, "page_size": 1,
    })
    set_layout(cache, layout="resident")
    request.origin_input_ids[0] = -1
    assert native_reentry.inspect(cache, request) is None
    hint = inspect_session_residency_hint(cache, request)
    assert hint["missing_full_tokens"] == hint["missing_mamba_slots"] == 0
    assert hint["input_prefix_verified"] is False


@pytest.mark.parametrize("pending", ["write_through_pending_id", "load_back_pending_id"])
def test_hint_preserves_pending_transfer_readiness(native_reentry, pending):
    cache, request = fixture(native_reentry, {
        "tokens": 32768, "depth": 8, "leaves": 1, "page_size": 1,
    })
    set_layout(cache, layout="resident")
    leaf = cache.session_refs.snapshot_latest_session_leaf_anchors()[0][1][0][0]
    setattr(cache.tree_core.node_by_id(leaf), pending, 42)
    actual = native_reentry.inspect(cache, request)
    hint = inspect_session_residency_hint(cache, request)
    assert hint["checkpoint_tokens"] == actual["checkpoint_tokens"]
    assert hint["device_checkpoint_tokens"] == actual["device_checkpoint_tokens"]


def test_hint_reads_changed_residency_without_cross_call_cache(native_reentry):
    cache, request = fixture(native_reentry, {
        "tokens": 32768, "depth": 8, "leaves": 1, "page_size": 1,
    })
    set_layout(cache, layout="resident")
    assert inspect_session_residency_hint(cache, request)["missing_full_tokens"] == 0
    set_layout(cache, layout="host")
    assert inspect_session_residency_hint(cache, request)["missing_full_tokens"] == 32768


def test_hint_rejects_replaced_anchor_and_foreign_namespace(native_reentry):
    cache, request = fixture(native_reentry, {
        "tokens": 32768, "depth": 8, "leaves": 1, "page_size": 1,
    })
    request.cache_salt = "foreign"
    assert inspect_session_residency_hint(cache, request) is None
    request.cache_salt = None
    leaf = cache.session_refs.snapshot_latest_session_leaf_anchors()[0][1][0][0]
    cache.tree_core.node_by_id(leaf).creation_time += 1.
    assert inspect_session_residency_hint(cache, request) is None
