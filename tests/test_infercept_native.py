"""InferCept move semantics over real native backup/reclaim/restore ACK paths."""

from unittest.mock import Mock

import pytest

from tests.test_full_only_native_lifecycle import full_native


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_infercept_host_move_earns_capacity_only_after_native_restore_ack(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    if not hasattr(cache, "release_session_full_host_duplicates"):
        pytest.skip("requires the separately qualified InferCept Host-move engine")
    node, generation = insert_session(cache, list(range(16)), "move-session")
    before_device = allocator.available_size()
    assert cache.prepare_beliefkv_spawn_full(
        session_id="move-session", session_generation=generation,
    )["issued"]
    cache.writing_check(finish_count=1)
    host_slots = set(writes[0].host_indices.tolist())
    full = node.component_data[0]
    cache.components[0]._full_kv_pool_host = cache.host_pool_group.anchor_entry.host_pool

    def release_host(indices, *, pool):
        assert str(getattr(pool, "value", pool)) == "kv"
        for slot in indices.tolist():
            assert slot in host_slots
            host_slots.remove(slot)

    cache.host_pool_group.free = Mock(side_effect=release_host)
    cache.beliefkv_cold_full_owner_validator = lambda _owners: True
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=before_device + 16, mamba_slots=0,
    ) == {0: 16}
    arguments = {
        "session_id": "move-session", "session_generation": generation,
        "leaf_node_id": node.id, "leaf_creation_time": float(node.creation_time),
        "nodes": ((node.id, float(node.creation_time)),),
    }
    assert not cache.release_session_full_host_duplicates(**arguments)["freed_host_units"]
    assert len(host_slots) == 16 and full.value is None
    assert cache.prefetch_gpu_session_node(
        **{key: value for key, value in arguments.items() if key != "nodes"},
        node_id=node.id, node_creation_time=float(node.creation_time),
        beliefkv_command_id="infercept-move",
        beliefkv_before_enqueue=lambda _operation: True, beliefkv_include_mamba=False,
    ).issued
    assert len(loads) == 1 and full.value is not None
    device = full.value
    assert full.host_lock_ref > 0 and node.load_back_pending_id is not None
    assert not cache.release_session_full_host_duplicates(**arguments)["freed_host_units"]
    assert len(host_slots) == 16
    cache.loading_check(finish_count=1)
    assert full.host_lock_ref == 0 and node.load_back_pending_id is None
    result = cache.release_session_full_host_duplicates(**arguments)
    assert result["freed_host_units"] == {0: 16}
    assert not host_slots and full.host_value is None
    assert full.value is device and allocator.available_size() == before_device
    assert not cache.release_session_full_host_duplicates(**arguments)["freed_host_units"]
    cache.host_pool_group.free.assert_called_once()
