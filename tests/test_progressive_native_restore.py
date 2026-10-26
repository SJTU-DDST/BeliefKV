from array import array

import pytest

from beliefkv.policy.tokencake import UploadCandidate, paper_upload_reservations
from tests.test_full_only_native_lifecycle import full_native
from tests.test_paper_prefix_transfers import OWNER, insert, native, settle_backup


def key(tokens, namespace="workflow"):
    from sglang.srt.mem_cache.radix_cache import RadixKey

    return RadixKey(array("q", tokens), namespace, cache_salt=namespace)


def cold(native, tokens=tuple(range(8))):
    actor, cache, *_ = native
    leaf = insert(cache, tokens)
    snapshot = actor.capture(leaf.id, owner=OWNER)
    settle_backup(actor, cache, snapshot)
    assert actor.demote(snapshot, owner=OWNER, max_tokens=len(tokens)) == len(tokens)
    return snapshot, key(tokens)


def test_paper_half_deficit_grants_bind_to_real_native_prefix_allocations(native):
    actor, cache, allocator, _, loads, commits, _ = native
    snapshot, observed = cold(native)
    free, remaining, copied = allocator.available_size(), 8, []
    while remaining:
        grants = paper_upload_reservations(
            (UploadCandidate("agent", remaining, remaining, 1., 1., 10.),),
            gpu_free=allocator.available_size(), critical_waiting_demand=0, shared_free=0,
        )
        issued = actor.restore_prefix(
            snapshot, owner=OWNER, observed_key=observed, available_tokens=grants["agent"],
        )
        count = sum(item[2] for item in issued)
        assert count == grants["agent"] and count > 0
        copied.append(count)
        remaining -= count
        assert allocator.available_size() == free - sum(copied)
        assert actor.status(snapshot, owner=OWNER) == "loading"
        # A second control round may not charge or copy the pending prefix twice.
        assert not actor.restore_prefix(
            snapshot, owner=OWNER, observed_key=observed, available_tokens=8,
        )
        cache.loading_check(finish_count=1)
    assert copied == [4, 2, 1, 1]
    assert sum(len(op.host_indices) for op in loads) == 8
    assert actor.status(snapshot, owner=OWNER) == "gpu"
    assert sum(
        dict(commit.num_tokens_by_pool)["kv"]
        for commit in commits if commit.direction == "h2d"
    ) == 8


def test_partial_transfer_split_preserves_the_remaining_full_host_lease(native):
    actor, cache, _, _, _, _, _ = native
    snapshot, observed = cold(native)
    actor.clock = lambda: 10.
    lease = actor.retain_host(snapshot, owner=OWNER, deadline_s=20.)
    assert lease is not None
    assert actor.restore_prefix(
        snapshot, owner=OWNER, observed_key=observed, available_tokens=3,
    )
    path = actor.resolve(snapshot, owner=OWNER)
    assert len(path) == 2
    assert path[0].load_back_pending_id is not None
    assert path[1].component_data[0].value is None
    assert all(node.component_data[0].host_lock_ref >= 1 for node in path)
    cache.loading_check(finish_count=1)
    assert actor.restore_prefix(
        snapshot, owner=OWNER, observed_key=observed, available_tokens=5,
    )
    cache.loading_check(finish_count=1)
    assert actor.release_host(lease)
    assert all(node.component_data[0].host_lock_ref == 0 for node in path)


def test_zero_grant_or_real_free_capacity_never_splits_or_allocates(native):
    actor, cache, allocator, _, loads, _, _ = native
    snapshot, observed = cold(native)
    before = len(actor.resolve(snapshot, owner=OWNER))
    assert not actor.restore_prefix(
        snapshot, owner=OWNER, observed_key=observed, available_tokens=0,
    )
    occupied = allocator.alloc(allocator.available_size())
    assert not actor.restore_prefix(
        snapshot, owner=OWNER, observed_key=observed, available_tokens=8,
    )
    assert not loads and len(actor.resolve(snapshot, owner=OWNER)) == before
    allocator.free(occupied)


@pytest.mark.parametrize("change", ("namespace", "different_tail", "short_key", "owner"))
def test_foreign_or_unobserved_key_cannot_choose_an_arbitrary_host_boundary(native, change):
    actor, _, _, _, loads, _, _ = native
    snapshot, observed = cold(native)
    owner = OWNER
    if change == "namespace":
        observed = key(range(8), "other")
    elif change == "different_tail":
        observed = key([*range(7), 999])
    elif change == "short_key":
        observed = key(range(4))
    else:
        owner = ("workflow", "other_context", 0)
    if change == "owner":
        assert not actor.restore_prefix(
            snapshot, owner=owner, observed_key=observed, available_tokens=4,
        )
    else:
        with pytest.raises(ValueError, match="observed"):
            actor.restore_prefix(
                snapshot, owner=owner, observed_key=observed, available_tokens=4,
            )
    assert not loads


def test_d2h_pending_is_not_used_as_a_ready_source_for_partial_copy(native):
    actor, cache, allocator, _, loads, _, _ = native
    leaf = insert(cache, range(8))
    snapshot = actor.capture(leaf.id, owner=OWNER)
    assert actor.backup(snapshot, owner=OWNER)
    free = allocator.available_size()
    assert not actor.restore_prefix(
        snapshot, owner=OWNER, observed_key=key(range(8)), available_tokens=4,
    )
    assert actor.counts["progressive_restore_waiting_for_d2h_ack"] == 1
    assert not loads and allocator.available_size() == free
