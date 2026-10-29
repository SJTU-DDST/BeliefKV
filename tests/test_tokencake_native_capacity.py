from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.tokencake import CapacityPlan
from beliefkv.runtime.tokencake_capacity import TokenCakeNativeCapacity
from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import request
from tests.test_paper_prefix_transfers import insert, native


@pytest.fixture
def accounting(native):
    transfers, cache, allocator, *_ = native
    result = TokenCakeNativeCapacity(transfers, generation_budget=8, context_limit=64)
    yield result, native
    result.close()


def req(identity, tokens, *, context=None):
    result = request(identity, tokens, context=context or identity)
    result.kv = NS(kv_allocated_len=0)
    result.last_node = None
    result.private_indices = None
    result.lock = None
    return result


def activate(accounting, item, allocated=None, *, agent_type="a", score=1000):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    ledger, (_, cache, allocator, *_) = accounting
    probe = ledger.probe(item)
    assert ledger.allows(probe, agent_type=agent_type, score=score)
    matched = cache.match_prefix(MatchPrefixParams(key=RadixKey(
        item.origin_input_ids, item.extra_key, cache_salt=item.cache_salt,
        limit=item._compute_max_prefix_len(len(item.origin_input_ids)),
    )))
    item.last_node = matched.last_device_node
    item.lock = cache.inc_lock_ref(item.last_node).to_dec_params()
    item.kv.kv_allocated_len = len(item.origin_input_ids) if allocated is None else allocated
    count = item.kv.kv_allocated_len - len(matched.device_indices)
    item.private_indices = allocator.alloc(count) if count else None
    ledger.commit(item, agent_type=agent_type, score=score, probe=probe)
    return probe


def finish(accounting, item):
    ledger, (_, cache, allocator, *_) = accounting
    cache.dec_lock_ref(item.last_node, item.lock)
    if item.private_indices is not None:
        allocator.free(item.private_indices)
    assert ledger.release(item.rid)


def test_upload_capacity_hands_shared_prefix_to_its_actual_native_consumer_once(accounting):
    ledger, (actor, cache, _, *_rest) = accounting
    owner = ("workflow", "agent_context", 0)
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=owner)
    assert ledger.can_reserve_prefix(snapshot, owner=owner, agent_type="a", score=1000)
    ledger.reserve_prefix("generation", snapshot, owner=owner, agent_type="a", score=1000)
    assert not ledger.requests and ledger.window.uncommitted == 60
    assert sum(ledger.window.used.values()) == 4
    item = req("next", [1, 2, 3, 4, 5, 6], context="agent_context")
    activate(accounting, item)
    assert sum(ledger.window.used.values()) == 6
    assert ledger.consume_reservation("generation", item)
    assert leaf.component_data[0].lock_ref == 1
    assert sum(ledger.window.used.values()) == 6 and ledger.window.uncommitted == 50
    assert not ledger.consume_reservation("generation", item)
    finish(accounting, item)
    assert not ledger.extents and not ledger.window.used and ledger.window.uncommitted == 64


@pytest.mark.parametrize("invalid", ("empty", "foreign_node", "full_skipped"))
def test_upload_handoff_rejects_default_or_wrong_native_lock_receipts(accounting, invalid):
    from dataclasses import replace

    from sglang.srt.mem_cache.base_prefix_cache import DecLockRefParams
    from sglang.srt.mem_cache.unified_cache.component_type import ComponentType

    ledger, (actor, cache, *_rest) = accounting
    owner = ("workflow", "agent_context", 0)
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=owner)
    ledger.reserve_prefix("generation", snapshot, owner=owner, agent_type="a", score=1000)
    item = req("next", [1, 2, 3, 4, 5], context="agent_context")
    activate(accounting, item)
    receipt = item.lock
    item.lock_receipt = {
        "empty": DecLockRefParams(),
        "foreign_node": replace(receipt, node_id=cache.tree_core.root_node.id),
        "full_skipped": replace(receipt, skipped_lock_components=(ComponentType.FULL,)),
    }[invalid]
    used, locked = ledger.window.used.copy(), leaf.component_data[0].lock_ref
    with pytest.raises(ValueError, match="real consumer's native lock"):
        ledger.consume_reservation("generation", item)
    assert "generation" in ledger.reservations
    assert ledger.window.used == used and leaf.component_data[0].lock_ref == locked
    item.lock_receipt = receipt
    assert ledger.consume_reservation("generation", item)
    finish(accounting, item)


def test_upload_split_keeps_charge_owner_and_cannot_be_consumed_by_a_shorter_prefix(accounting):
    ledger, (actor, cache, *_rest) = accounting
    owner = ("workflow", "agent_context", 0)
    leaf = insert(cache, [1, 2, 3, 4, 5, 6])
    snapshot = actor.capture(leaf.id, owner=owner)
    ledger.reserve_prefix("generation", snapshot, owner=owner, agent_type="a", score=1000)
    short = req("short", [1, 2, 9], context="agent_context")
    activate(accounting, short, agent_type="b")
    assert len(ledger.reservations["generation"].prefix) == 2
    assert not ledger.consume_reservation("generation", short)
    consumer = req("next", [1, 2, 3, 4, 5, 6, 7], context="agent_context")
    activate(accounting, consumer)
    assert ledger.consume_reservation("generation", consumer)
    assert sum(ledger.window.used.values()) == 8
    finish(accounting, consumer)
    assert sum(ledger.window.used.values()) == 3
    finish(accounting, short)
    assert not ledger.extents and not ledger.reservations


def test_progressive_upload_reserves_only_real_new_extents_through_each_native_ack(accounting):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    ledger, (actor, cache, allocator, *_rest) = accounting
    owner = ("workflow", "agent_context", 0)
    leaf = insert(cache, list(range(8)))
    snapshot = actor.capture(leaf.id, owner=owner)
    assert actor.backup(snapshot, owner=owner)
    cache.writing_check(finish_count=1)
    assert actor.demote(snapshot, owner=owner, max_tokens=8) == 8
    observed = RadixKey(array("q", range(8)), "workflow", cache_salt="workflow")
    for count, end in ((4, 4), (2, 6), (1, 7), (1, 8)):
        assert ledger.can_reserve_prefix(
            snapshot, owner=owner, agent_type="a", score=1000, new_tokens=count,
        )
        before = allocator.available_size()
        issued = actor.restore_prefix(snapshot, owner=owner, observed_key=observed, available_tokens=count)
        assert sum(item[2] for item in issued) == count
        assert before - allocator.available_size() == count
        matched = cache.match_prefix(MatchPrefixParams(key=RadixKey(
            array("q", range(end)), "workflow", cache_salt="workflow",
        )))
        partial = actor.capture(matched.last_device_node, owner=owner)
        ledger.reserve_prefix("generation", partial, owner=owner, agent_type="a", score=1000)
        cache.loading_check(finish_count=1)
        assert sum(ledger.window.used.values()) == end
        assert ledger.window.uncommitted == 64 - end
        assert all(
            cache.tree_core.node_by_id(identity[0]).component_data[0].lock_ref > 0
            for identity in ledger.reservations["generation"].prefix
        )
    assert ledger.counts["upload_reservation_new_tokens"] == 8
    assert ledger.release_reservation("generation")
    assert ledger.window.uncommitted == 64


def test_upload_reservation_preserves_a_live_request_charge_after_its_lease_expires(accounting):
    ledger, (actor, cache, *_rest) = accounting
    owner = ("workflow", "agent_context", 0)
    leaf = insert(cache, [1, 2, 3, 4])
    item = req("running", [1, 2, 3, 4, 5], context="agent_context")
    activate(accounting, item)
    used = ledger.window.used.copy()
    snapshot = actor.capture(leaf.id, owner=owner)
    ledger.reserve_prefix("generation", snapshot, owner=owner, agent_type="b", score=1000)
    assert ledger.window.used == used
    assert ledger.release_reservation("generation")
    assert ledger.window.used == used and leaf.component_data[0].lock_ref == 1
    finish(accounting, item)


def test_upload_cannot_consume_critical_waiting_capacity_or_another_context(accounting):
    ledger, (actor, cache, *_rest) = accounting
    owner = ("workflow", "agent_context", 0)
    leaf = insert(cache, [1, 2, 3, 4])
    snapshot = actor.capture(leaf.id, owner=owner)
    ledger.set_plan(CapacityPlan({"critical"}, {"critical": 10.}, {"critical": 64}, 0))
    ledger.set_waiting({"critical": 5000})
    assert not ledger.can_reserve_prefix(snapshot, owner=owner, agent_type="a", score=1000)
    with pytest.raises(ValueError, match="approved spatial capacity"):
        ledger.reserve_prefix("generation", snapshot, owner=owner, agent_type="a", score=1000)
    assert not ledger.reservations and not ledger.window.used
    ledger.set_plan(CapacityPlan(shared=64))
    ledger.reserve_prefix("generation", snapshot, owner=owner, agent_type="a", score=1000)
    peer = req("peer", [1, 2, 3, 4, 5], context="another_context")
    activate(accounting, peer)
    assert not ledger.consume_reservation("generation", peer)
    finish(accounting, peer)
    assert sum(ledger.window.used.values()) == 4
    ledger.release_reservation("generation")


def test_real_native_shared_prefix_is_charged_once_and_growth_is_not_dropped(accounting):
    ledger, (_, cache, _, writes, loads, *_) = accounting
    insert(cache, [1, 2, 3, 4])
    a, b = req("a", [1, 2, 3, 4, 5, 6]), req("b", [1, 2, 3, 4, 9, 10])
    activate(accounting, a)
    assert sum(ledger.window.used.values()) == 6
    assert ledger.window.growth == {"a": 8}
    assert ledger.window.uncommitted == 50
    activate(accounting, b, agent_type="b")
    assert sum(ledger.window.used.values()) == 8
    assert ledger.window.growth == {"a": 8, "b": 8}
    assert ledger.window.uncommitted == 40
    assert next(iter(ledger.extents.values())).users == {"a", "b"}
    assert not writes and not loads
    finish(accounting, a)
    assert sum(ledger.window.used.values()) == 6
    assert ledger.window.uncommitted == 50
    finish(accounting, b)
    assert not ledger.window.used and not ledger.extents and not ledger.requests
    assert ledger.window.uncommitted == 64


def test_native_split_preserves_original_pool_owner_and_shared_membership(accounting):
    ledger, (_, cache, *_) = accounting
    ledger.set_plan(CapacityPlan({"a"}, {"a": 1.}, {"a": 62}, 2))
    insert(cache, [1, 2, 3, 4])
    a, b = req("a", [1, 2, 3, 4, 5, 6]), req("b", [1, 2, 9, 10])
    activate(accounting, a)
    original_used = ledger.window.used.copy()
    probe = ledger.probe(b)
    assert ledger.counts["split_extents"] == 1
    assert ledger.window.used == original_used
    prefix = ledger.extents[probe.extents[0][0]]
    assert prefix.tokens == 2 and prefix.charges == ((None, 2),)
    activate(accounting, b, agent_type="b")
    assert prefix.users == {"a", "b"} and prefix.charges == ((None, 2),)
    finish(accounting, a)
    assert prefix.users == {"b"} and prefix.charges == ((None, 2),)
    finish(accounting, b)
    assert not ledger.window.used


def test_native_decode_growth_consumes_observed_allocation_without_recorded_length(accounting):
    ledger, (_, _, allocator, *_) = accounting
    item = req("a", [1, 2, 3, 4])
    item.sampling_params = NS(max_new_tokens=1)
    first = ledger.probe(item)
    item.sampling_params.max_new_tokens = 1_000_000
    second = ledger.probe(item)
    assert first.target_tokens == second.target_tokens == 12
    activate(accounting, item)
    initial_uncommitted = ledger.window.uncommitted
    extra = allocator.alloc(3)
    item.output_ids.extend([5, 6, 7])
    item.kv.kv_allocated_len += 3
    ledger.commit(item, agent_type="a", score=1000)
    assert ledger.window.growth["a"] == 5
    assert ledger.window.uncommitted == initial_uncommitted
    assert ledger.requests["a"].private == ((None, 7),)
    allocator.free(extra)
    finish(accounting, item)


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_fragmented_native_prefix_is_accounted_beyond_optional_transfer_scan_limit(accounting):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    ledger, (actor, cache, allocator, *_) = accounting
    ledger.context_limit = 512
    tokens = array("q", range(actor.max_nodes + 1))
    leaf = insert(cache, tokens)
    for end in range(1, len(tokens)):
        cache.match_prefix(MatchPrefixParams(key=RadixKey(
            tokens, "workflow", cache_salt="workflow", limit=end,
        )))
    owner = ("workflow", "fragmented", 0)
    assert actor.capture(leaf.id, owner=owner) is None
    assert actor.counts["path_depth_declined"]
    item = req("fragmented", [*tokens, 999], context=owner[1])
    before = allocator.available_size()
    probe = ledger.probe(item)
    assert len(probe.extents) == len(tokens)
    assert sum(units for _, units in probe.extents) == len(tokens)
    assert allocator.available_size() == before
    activate(accounting, item)
    assert len(ledger.requests[item.rid].prefix) == len(tokens)
    finish(accounting, item)
    assert not ledger.extents and not ledger.window.used


@pytest.mark.parametrize("fault", ("foreign_namespace", "cycle", "stale_node", "context_limit"))
def test_spatial_prefix_walk_rejects_invalid_native_paths(accounting, monkeypatch, fault):
    from beliefkv.runtime.sglang_v0520_admission import _request_key

    ledger, (actor, cache, *_rest) = accounting
    leaf = insert(cache, [1, 2, 3, 4])
    item = req("invalid", [1, 2, 3, 4, 5])
    if fault == "foreign_namespace":
        monkeypatch.setattr(leaf.key, "cache_salt", "foreign")
    elif fault == "cycle":
        monkeypatch.setattr(leaf, "parent", leaf)
    elif fault == "stale_node":
        monkeypatch.setattr(actor, "_node", lambda _identity: None)
    else:
        ledger.context_limit = 3
    with pytest.raises(ValueError, match="prefix|path"):
        ledger._path_extents(leaf.id, _request_key(item), ("workflow", "workflow"))
    assert not ledger.requests and not ledger.extents


def test_new_cached_checkpoint_reclassifies_private_allocation_without_double_charging(accounting):
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    ledger, (_, cache, _, *_) = accounting
    item = req("a", [1, 2, 3, 4, 5, 6])
    activate(accounting, item)
    before = ledger.window.used.copy()
    result = cache.insert(InsertParams(
        key=RadixKey(array("q", [1, 2, 3, 4]), "workflow", cache_salt="workflow"),
        value=item.private_indices[:4],
    ))
    cache.dec_lock_ref(item.last_node, item.lock)
    item.last_node = result.last_device_node
    item.lock = cache.inc_lock_ref(item.last_node).to_dec_params()
    item.private_indices = item.private_indices[4:]
    ledger.commit(item, agent_type="a", score=1000)
    assert ledger.window.used == before
    assert sum(extent.tokens for extent in ledger.extents.values()) == 4
    assert ledger.requests["a"].private == ((None, 2),)
    finish(accounting, item)
    assert ledger.window.uncommitted == 64


def test_idle_cached_content_is_not_charged_as_live_capacity(accounting):
    ledger, (_, cache, *_) = accounting
    insert(cache, list(range(40)))
    ledger.refresh()
    assert ledger.window.free == 64 and not ledger.window.used
    assert ledger.window.uncommitted == 64


def test_unrelated_native_dma_capacity_is_not_mistaken_for_free_shared_space(accounting):
    ledger, (transfers, cache, _, _, _, _, _) = accounting
    leaf = insert(cache, list(range(40)))
    owner = ("workflow", "idle", 0)
    snapshot = transfers.capture(leaf.id, owner=owner)
    assert transfers.backup(snapshot, owner=owner)
    item = req("a", list(range(100, 132)))
    probe = ledger.probe(item)
    assert not ledger.allows(probe, agent_type="a", score=1000)
    assert ledger.window.free == 24
    cache.writing_check(finish_count=1)
    assert ledger.allows(probe, agent_type="a", score=1000)
    assert ledger.window.free == 64


def test_matching_d2h_locked_prefix_is_adopted_without_charging_its_capacity_twice(accounting):
    ledger, (transfers, cache, allocator, *_rest) = accounting
    leaf = insert(cache, list(range(20)))
    peer = req("peer", list(range(100, 124)))
    activate(accounting, peer)
    owner = ("workflow", "copy_source", 0)
    snapshot = transfers.capture(leaf.id, owner=owner)
    assert transfers.backup(snapshot, owner=owner)
    item = req("return", list(range(21)))
    probe = ledger.probe(item)
    ledger.refresh()
    assert ledger.window.uncommitted == 12
    assert probe.external_prefix_tokens == 20
    assert ledger.allows(probe, agent_type="a", score=1000)
    # Candidate-local credit never becomes free capacity for an unrelated request.
    assert ledger.window.uncommitted == 12
    unrelated = req("unrelated", list(range(300, 321)))
    assert not ledger.allows(ledger.probe(unrelated), agent_type="a", score=1000)
    activate(accounting, item)
    assert ledger.counts["external_prefix_tokens_adopted"] == 20
    assert sum(ledger.window.used.values()) == 45
    assert ledger.window.uncommitted == 3
    cache.writing_check(finish_count=1)
    ledger.refresh()
    assert ledger.window.uncommitted == 3
    finish(accounting, item)
    finish(accounting, peer)
    assert ledger.window.uncommitted == 64


def test_explicit_reserved_borrow_permission_survives_the_native_physical_commit(accounting):
    ledger, (_, cache, allocator, *_) = accounting
    ledger.set_plan(CapacityPlan({"a", "b"}, {"a": 2., "b": 1.}, {"a": 32, "b": 32}, 0))
    ledger.set_waiting({"a": 1000, "b": 1000})
    item = req("ordinary", [1, 2, 3, 4])
    probe = ledger.probe(item)
    assert not ledger.allows(probe, agent_type="ordinary", score=1000)
    assert ledger.allows(probe, agent_type="ordinary", score=1000, borrow_reserved=True)
    item.last_node = cache.tree_core.root_node.id
    item.lock = cache.inc_lock_ref(item.last_node).to_dec_params()
    item.private_indices = allocator.alloc(4)
    item.kv.kv_allocated_len = 4
    ledger.commit(item, agent_type="ordinary", score=1000, probe=probe)
    assert sum(ledger.window.used.values()) == 4
    assert ledger.window.used[None] == 0
    finish(accounting, item)


def test_prefix_identity_or_attempt_change_invalidates_native_admission_probe(accounting):
    ledger, (_, cache, *_) = accounting
    insert(cache, [1, 2, 3, 4])
    item = req("a", [1, 2, 3, 4, 5])
    probe = ledger.probe(item)
    item.cache_request_handle.attempt_id = 1
    with pytest.raises(ValueError, match="identity or budget"):
        ledger.allows(probe, agent_type="a", score=1000)
    assert not ledger.requests and not ledger.window.used


def test_unapproved_or_stale_first_commit_cannot_create_a_growth_commitment(accounting):
    ledger, *_ = accounting
    item = req("a", [1, 2, 3, 4])
    probe = ledger.probe(item)
    with pytest.raises(ValueError, match="admission probe"):
        ledger.commit(item, agent_type="a", score=1000, probe=probe)
    assert ledger.allows(probe, agent_type="a", score=1000)
    ledger.set_waiting({"a": 1000})
    with pytest.raises(ValueError, match="admission probe"):
        ledger.commit(item, agent_type="a", score=1000, probe=probe)
    assert not ledger.window.growth and not ledger.requests


def stage(accounting, item, *, private=2):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    ledger, (_, cache, *_) = accounting
    probe = ledger.probe(item)
    assert ledger.allows(probe, agent_type="a", score=1000)
    match = cache.match_prefix(MatchPrefixParams(key=RadixKey(
        item.origin_input_ids, item.extra_key, cache_salt=item.cache_salt,
        limit=item._compute_max_prefix_len(len(item.origin_input_ids)),
    )))
    item.last_node = match.last_device_node
    item.lock = cache.inc_lock_ref(item.last_node).to_dec_params()
    ledger.stage(
        item, agent_type="a", score=1000, probe=probe, planned_private_tokens=private,
    )


def test_native_pending_admission_slots_are_reserved_before_batch_allocation(accounting):
    ledger, (_, cache, allocator, *_) = accounting
    insert(cache, [1, 2, 3, 4])
    a, b = req("a", [1, 2, 3, 4, 5, 6]), req("b", [1, 2, 3, 4, 9, 10])
    stage(accounting, a)
    assert a.kv.kv_allocated_len == 0 and ledger.requests["a"].pending_private == 2
    assert sum(ledger.window.used.values()) == 6 and ledger.window.uncommitted == 50
    stage(accounting, b)
    assert sum(ledger.window.used.values()) == 8 and ledger.window.uncommitted == 40
    assert sum(claim.pending_private for claim in ledger.requests.values()) == 4
    for item in (a, b):
        item.private_indices = allocator.alloc(2)
        item.kv.kv_allocated_len = 6
    # Settle all selected requests before reading the now-smaller native free pool.
    ledger.mark_allocated((a, b))
    assert not any(claim.pending_private for claim in ledger.requests.values())
    assert ledger.window.uncommitted == 40
    for item in (a, b):
        ledger.commit(item, agent_type="a", score=1000)
        finish(accounting, item)
    assert ledger.window.uncommitted == 64


def test_cancelled_admission_releases_reservation_without_fabricating_native_free_slots(accounting):
    ledger, (_, cache, allocator, *_) = accounting
    insert(cache, [1, 2, 3, 4])
    item = req("a", [1, 2, 3, 4, 5, 6])
    raw_free = allocator.available_size()
    stage(accounting, item)
    assert allocator.available_size() == raw_free
    finish(accounting, item)
    assert allocator.available_size() == raw_free
    assert not ledger.window.growth and not ledger.window.used
    assert ledger.window.uncommitted == 64


def test_pending_private_slots_cannot_be_settled_before_native_allocation(accounting):
    ledger, (_, cache, *_) = accounting
    insert(cache, [1, 2, 3, 4])
    item = req("a", [1, 2, 3, 4, 5, 6])
    stage(accounting, item)
    with pytest.raises(ValueError, match="materialize"):
        ledger.mark_allocated((item,))
    assert ledger.requests["a"].pending_private == 2
    with pytest.raises(ValueError, match="settle pending"):
        ledger.commit(item, agent_type="a", score=1000)
    finish(accounting, item)
