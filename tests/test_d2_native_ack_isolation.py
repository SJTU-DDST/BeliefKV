"""Exercise ToolStart native hooks with real CPU FULL eviction and load-back."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_full_only_native_lifecycle import full_native
from tests.test_prepare_ack_allocation import native_write_start


TOOL_START_COMMAND = "beliefkv-tool-start-cpu-1"


@pytest.fixture
def ack_isolation(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _writes, loads, commits = full_native
    native_write_start(cache)
    donor, generation = insert_session(cache, list(range(1, 33)), "tool")
    victim, _ = insert_session(cache, list(range(101, 113)), "victim")
    consumer, _ = insert_session(cache, list(range(201, 217)), "consumer")
    for offset, node in enumerate((victim, consumer), start=1):
        full = node.component_data[0]
        full.host_value = full.value + 1000 * offset
        cache.tree_core._update_duplicate_tracking(node)
    cache.release_radix_session("victim")
    cache._demote(consumer.id, {})
    assert allocator.alloc(16) is not None
    assert allocator.available_size() == 4
    cache.host_pool_group.alloc = Mock(side_effect=AssertionError("Host slots owned by stand-in"))
    cache.host_pool_group.free = Mock(side_effect=AssertionError("unexpected Host reclaim"))
    return SimpleNamespace(
        cache=cache, allocator=allocator, donor=donor, generation=generation,
        victim=victim, consumer=consumer, loads=loads, commits=commits,
    )


def _prepare(parts, command=TOOL_START_COMMAND):
    cache, donor = parts.cache, parts.donor
    created = float(donor.creation_time)
    assert cache.prepare_host_shadow(
        session_id="tool", session_generation=parts.generation,
        leaf_node_id=donor.id, leaf_creation_time=created,
        node_id=donor.id, node_creation_time=created,
        beliefkv_command_id=command, beliefkv_include_mamba=False,
    ).issued
    ack, = cache.cache_controller.ack_write_queue
    ack.finish_event.query = Mock(return_value=False)
    return ack


def test_reentry_queues_h2d_while_independent_tool_start_ack_is_pending(ack_isolation):
    parts = ack_isolation
    cache, donor = parts.cache, parts.donor
    full = donor.component_data[0]
    device = full.value
    ack = _prepare(parts)
    assert full.lock_ref == 1

    assert cache.load_back(parts.consumer.id)
    assert len(parts.loads) == 1
    assert parts.allocator.available_size() == 0
    assert parts.victim.component_data[0].value is None
    ack.finish_event.synchronize.assert_not_called()
    assert full.value is device and full.lock_ref == 1
    assert donor.write_through_pending_id == donor.id
    assert tuple(cache.ongoing_write_through) == (donor.id,)
    assert cache.cache_controller.ack_write_queue == [ack]
    assert parts.commits == []
    assert cache._beliefkv_tool_start_nonblocking_allocations == 1

    ack.finish_event.query.return_value = True
    cache.writing_check(finish_count=cache._count_ready_acks(cache.cache_controller.ack_write_queue))
    ack.finish_event.synchronize.assert_called_once_with()
    assert full.value is device and full.lock_ref == 0
    assert donor.write_through_pending_id is None
    assert not cache.ongoing_write_through
    commit, = parts.commits
    assert commit.child_commits[0].command_id == TOOL_START_COMMAND
    assert cache._beliefkv_tool_start_nonblocking_allocations == 1


def test_reentry_still_uses_blocking_ack_when_settled_victim_is_one_token_short(ack_isolation):
    parts = ack_isolation
    cache, donor = parts.cache, parts.donor
    assert cache.enable_beliefkv_incremental_prepare()
    assert parts.allocator.alloc(1) is not None
    ack = _prepare(parts)

    def finish():
        assert parts.allocator.available_size() == 15
        assert parts.victim.component_data[0].value is None
        assert donor.component_data[0].value is not None
        assert not parts.loads

    ack.finish_event.synchronize.side_effect = finish
    assert cache.load_back(parts.consumer.id)
    ack.finish_event.synchronize.assert_called_once_with()
    assert len(parts.loads) == 1
    assert donor.component_data[0].value is None
    assert donor.component_data[0].host_value is not None
    assert not cache.ongoing_write_through
    assert cache._beliefkv_tool_start_nonblocking_allocations == 0


def test_ack_coverage_must_match_all_outstanding_sources(ack_isolation):
    from sglang.srt.mem_cache.unified_radix_cache import BackupKV, ComponentType

    parts = ack_isolation
    cache, donor = parts.cache, parts.donor
    parent, action = cache.tree_core._split_node(donor.key, donor, 16)
    assert action is None
    for sequence, node in enumerate((parent, donor)):
        assert cache._execute_and_commit_kv_backup(
            BackupKV([node.id]), no_host_reclaim=True,
            beliefkv_command_id=f"beliefkv-tool-start-cpu-{sequence}",
        ) == 16

    full = ComponentType.FULL
    targets = {full: (full, parts.allocator.available_size())}
    acks = cache.cache_controller.ack_write_queue
    assert len(acks) == len(cache.ongoing_write_through) == 2
    missing = acks.pop()
    assert not cache._can_defer_tool_start_acks(targets)
    acks.append(acks[0])
    assert not cache._can_defer_tool_start_acks(targets)
    acks[-1] = missing
    assert cache._can_defer_tool_start_acks(targets)
    assert cache._beliefkv_tool_start_nonblocking_allocations == 0

    cache.writing_check(finish_count=2)
    assert not cache.ongoing_write_through
    assert parent.component_data[full].lock_ref == donor.component_data[full].lock_ref == 0


@pytest.mark.parametrize("case", (
    "native", "other_tag", "no_receipt", "no_tag", "wrong_node", "mixed", "unlocked",
    "plain_evict", "tp", "pp", "storage", "buffer", "linker",
))
def test_other_writes_and_eviction_modes_still_drain(ack_isolation, monkeypatch, case):
    from sglang.srt.mem_cache.unified_radix_cache import BackupKV, EvictParams

    parts = ack_isolation
    cache, donor = parts.cache, parts.donor
    controller = cache.cache_controller
    if case in ("native", "unlocked"):
        locked = case == "native"
        assert cache._execute_and_commit_kv_backup(
            BackupKV([donor.id]), write_back=not locked, no_host_reclaim=True,
            beliefkv_command_id=None if locked else TOOL_START_COMMAND,
        ) == 32
        ack, = controller.ack_write_queue
    elif case == "mixed":
        parent, action = cache.tree_core._split_node(donor.key, donor, 16)
        assert action is None
        for node, command in ((parent, TOOL_START_COMMAND), (donor, None)):
            assert cache._execute_and_commit_kv_backup(
                BackupKV([node.id]), no_host_reclaim=True,
                beliefkv_command_id=command, beliefkv_defer_writing=True,
            ) == 16
        controller.start_writing()
        ack, = controller.ack_write_queue
        assert [r.command_id for r in ack.child_receipts] == [TOOL_START_COMMAND, None]
    else:
        ack = _prepare(parts, "beliefkv-shadow-cpu-1" if case == "other_tag" else TOOL_START_COMMAND)
        if case == "no_receipt":
            ack.child_receipts = ()
        elif case == "no_tag":
            del ack.child_receipts[0].command_id
        elif case == "wrong_node":
            ack.child_receipts[0].node_id = parts.victim.id
        elif case == "tp":
            cache.tp_world_size = 2
        elif case == "pp":
            cache.pp_size = 2
        elif case == "storage":
            cache.enable_storage = True
            monkeypatch.setattr(cache, "write_backup_storage", Mock())

    if case in ("buffer", "linker"):
        flag = "buffer_pipeline" if case == "buffer" else "linker"
        drain = cache.writing_check

        def drain_after_recording(**kwargs):
            monkeypatch.setattr(cache, flag, None)
            drain(**kwargs)

        monkeypatch.setattr(cache, flag, object())
        monkeypatch.setattr(cache, "writing_check", Mock(side_effect=drain_after_recording))

    evict = cache.evict if case == "plain_evict" else cache.evict_for_alloc
    assert evict(EvictParams(num_tokens=12)).num_tokens_evicted == 12
    assert parts.allocator.available_size() == 16
    ack.finish_event.synchronize.assert_called_once_with()
    assert not cache.ongoing_write_through
    assert cache._beliefkv_tool_start_nonblocking_allocations == 0


@pytest.mark.parametrize("case", (
    "met", "no_offer", "insufficient", "insufficient_branch", "absent", "plain_evict",
    "already_met", "multi_target", "multi_rank",
))
def test_settled_shortfall_offer_at_actual_full_allocator_write_back(full_native, case):
    from sglang.srt.mem_cache.unified_radix_cache import EvictParams
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, _loads, _commits = full_native
    native_write_start(cache)
    settled, _ = insert_session(cache, list(range(1, 5)), "settled")
    ordinary, _ = insert_session(cache, list(range(101, 113)), "ordinary")
    provider_size = 4 if case == "insufficient" else 10
    provider, _ = insert_session(cache, list(range(201, 201 + provider_size)), "tool")
    backed = (settled, provider)
    parent = None
    if case == "insufficient_branch":
        parent, action = cache.tree_core._split_node(provider.key, provider, 6)
        assert action is None
        backed += (parent,)
    for offset, node in enumerate(backed, start=1):
        full = node.component_data[0]
        full.host_value = full.value + 1000 * offset
        cache.tree_core._update_duplicate_tracking(node)
    cache.release_radix_session("settled")
    cache.release_radix_session("ordinary")
    cache.host_pool_group.free = Mock(side_effect=AssertionError("unexpected Host reclaim"))
    assert allocator.alloc(allocator.available_size() - 4) is not None
    ordinary_full = ordinary.component_data[0]
    ordinary_device = ordinary_full.value
    provider_full = provider.component_data[0]
    provider_host = provider_full.host_value
    releases = case in ("met", "insufficient", "insufficient_branch")
    request_tokens = 4 if case == "already_met" else 24 if parent is not None else 12

    def offer(missing):
        assert allocator.available_size() == 8 and missing == request_tokens - 4
        assert settled.component_data[0].value is None
        assert ordinary_full.value is ordinary_device and ordinary_full.host_value is None
        assert not writes
        if releases:
            assert cache._can_beliefkv_full_demote(provider, full_session_ref=1)
            cache._demote(provider.id, {})
        return 999

    callback = Mock(side_effect=offer)
    if case != "absent":
        cache._beliefkv_tool_start_settled_shortfall = callback
    if case == "multi_rank":
        cache.tp_world_size = 2
    evict = cache.evict if case == "plain_evict" else cache.evict_for_alloc
    result = evict(EvictParams(
        num_tokens=request_tokens,
        mamba_num=1 if case == "multi_target" else 0,
    ))

    if releases or case == "no_offer":
        callback.assert_called_once_with(request_tokens - 4)
    else:
        callback.assert_not_called()
    native_write = case not in ("met", "already_met")
    assert [write.node_id for write in writes] == ([ordinary.id] if native_write else [])
    assert (ordinary_full.value is None) is native_write
    assert (ordinary_full.host_value is not None) is native_write
    assert (provider_full.value is None) is releases
    assert provider_full.host_value is provider_host
    expected_freed = 4 + (12 if native_write else 0)
    if releases:
        expected_freed += provider_size
    if parent is not None:
        assert parent.component_data[0].value is None
    assert result.num_tokens_evicted == expected_freed == allocator.available_size() - 4
    assert allocator.alloc(4 + request_tokens) is not None
