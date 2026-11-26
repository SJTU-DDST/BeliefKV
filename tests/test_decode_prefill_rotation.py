from collections import Counter
from dataclasses import replace

from scripts.screen_decode_prefill_rotation import (
    Batch,
    Member,
    Receipt,
    phase_rotations,
    stable_decode_launch_gap,
    terminal_members,
)


def decoder(request, before, *, launch_output=None):
    launch_output = before if launch_output is None else launch_output
    owner = (request, "invocation", "context", 0)
    return Member(
        request=request,
        workflow=request,
        owner=owner,
        attempt=0,
        session=request,
        generation=1,
        sequence=100 + launch_output,
        launch_output=launch_output,
        before=before,
        after=before + 1,
        delta=1,
    )


def receipt(member, when):
    return Receipt(member.owner, member.session, member.generation, when, member.after, "clock")


def test_split_requires_new_member_to_be_supplied_by_prefill():
    prior = decoder("old", 3, launch_output=2)
    retained = decoder("old", 4, launch_output=4)
    finishing = decoder("old", 5, launch_output=4)
    prefilled = replace(decoder("new", 0), sequence=3, delta=97)
    new_first = decoder("new", 1)
    new_second = decoder("new", 2)
    batches = [
        Batch(10, "decode", 0., 0., 2., (prior,)),
        Batch(11, "prefill", 1., 2., 12., (prefilled,)),
        Batch(12, "decode", 11., 12., 14., (retained, new_first)),
        Batch(13, "decode", 13., 14., 16., (finishing, new_second)),
    ]
    receipts = {"old": receipt(finishing, 15.)}
    counts = Counter()
    [rotation] = phase_rotations(batches, receipts, counts)
    assert rotation["terminal_requests"] == ["old"]
    assert rotation["new_decode_requests"] == ["new"]
    assert rotation["prefill_requests"] == ["new"]
    assert rotation["prefill_worker_ms"] == 10.
    assert rotation["decode_worker_ms"] == 4.
    assert rotation["additional_early_decode_token_slots"] == 2
    assert not rotation["prior_result_before_prefill_launch"]
    assert counts["same_exact_ordered_roster"] == 0

    # A later generation needs a different prefill, even when its request ID matches.
    batches[1] = replace(batches[1], members=(replace(prefilled, generation=2),))
    assert list(phase_rotations(batches, receipts, Counter())) == []


def test_unchanged_roster_allows_exact_rotation_with_partial_prefill():
    prior = decoder("old", 3)
    finishing = decoder("old", 4)
    partial = replace(decoder("new", 0), after=0, delta=2048)
    batches = [
        Batch(10, "decode", 0., 0., 2., (prior,)),
        Batch(11, "prefill", 2., 2., 12., (partial,)),
        Batch(12, "decode", 11., 12., 14., (finishing,)),
    ]
    counts = Counter()
    [rotation] = phase_rotations(batches, {"old": receipt(finishing, 13.)}, counts)
    assert rotation["new_decode_member_count"] == 0
    assert rotation["prior_result_before_prefill_launch"]
    assert counts["exact_cohort_prefix_with_terminal"] == 1


def test_terminal_matching_rejects_inflight_residue_and_stale_receipt():
    finishing = decoder("old", 4)
    finished = Batch(10, "decode", 0., 0., 2., (finishing,))
    stale = replace(finishing, before=5, delta=0)
    overlap_residue = Batch(11, "decode", 1., 2., 4., (stale,))
    exact = receipt(finishing, 2.)
    assert terminal_members(finished, {"old": exact}) == ["old"]
    assert terminal_members(overlap_residue, {"old": exact}) == []
    assert terminal_members(finished, {"old": replace(exact, generation=0)}) == []


def test_decode_cadence_uses_real_launch_and_requires_contiguous_progress():
    prior = Batch(10, "decode", 0., 0., 5., (decoder("old", 3),))
    batch = Batch(11, "decode", 6., 6., 10., (decoder("old", 4, launch_output=3),))
    assert stable_decode_launch_gap(prior, batch) == 1.
    assert stable_decode_launch_gap(prior, replace(batch, launch=4., start=5.)) == 0.
    assert stable_decode_launch_gap(prior, replace(batch, iteration=12)) is None
    assert stable_decode_launch_gap(prior, replace(batch, members=(decoder("old", 5),))) is None
