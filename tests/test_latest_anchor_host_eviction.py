"""Native Host eviction must preserve discovery of a surviving input prefix."""

from tests.test_full_only_native_lifecycle import anchors, full_native


def test_latest_full_anchor_follows_surviving_prefix_after_host_eviction(full_native):
    from beliefkv.runtime.sglang_v0520_physical import inspect_session_h2d_opportunity
    from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, _, _ = full_native
    first, generation = insert_session(cache, [1, 2, 3, 4], "parent")
    assert cache.prepare_beliefkv_spawn_full(
        session_id="parent", session_generation=generation,
    )["issued"]
    cache.writing_check(finish_count=1)
    tail, _ = insert_session(cache, list(range(1, 9)), "parent")
    assert cache.prepare_beliefkv_spawn_full(
        session_id="parent", session_generation=generation,
    )["issued"]
    cache.writing_check(finish_count=1)
    cache.beliefkv_cold_full_owner_validator = lambda owners: True
    free = allocator.available_size()
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=free + 8, mamba_slots=0,
    ) == {0: 8}
    before = anchors(cache, generation, input_tokens=8)
    assert before is not None
    assert before.component_leaves == ((0, ((tail.id, tail.creation_time),)),)

    freed = cache.tree_core.drive_host_eviction(ComponentType.FULL, 4)
    try:
        assert freed.tracker[ComponentType.FULL] == 4
        assert first.component_data[0].host_value is not None
        assert first.component_data[0].value is None
        expected = ((0, ((first.id, first.creation_time),)),)
        assert cache.session_refs.snapshot_session_leaf_anchors("parent", generation) == expected
        assert cache.session_refs.snapshot_latest_session_leaf_anchors("parent", generation) == expected
        restored = anchors(cache, generation, input_tokens=8)
        assert restored is not None
        plan = inspect_session_h2d_opportunity(
            cache, restored, max_steps=8, include_checkpoint_plan=True,
        )
        assert plan.required_full_tokens == 4 and plan.required_mamba_slots == 0
        assert [step.node_id for step in plan.steps] == [first.id]
        assert cache.session_refs.snapshot_latest_session_leaf_anchors(
            "parent", generation + 1,
        ) is None
    finally:
        freed.device_frees.clear()
        freed.host_frees.clear()
