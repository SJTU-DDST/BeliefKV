"""Admission order overlay; native PrefillAdder retains physical authority."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_admission import (
    NativePrefillPlan,
    PrefillCandidateKey,
    select_native_prefill_candidates,
)


def _req(request_id: str, *, epoch: int = 0, attempt: int = 0, tagged: bool = True):
    return NS(
        rid=request_id,
        cache_request_handle=NS(attempt_id=attempt),
        beliefkv_metadata=(
            {
                "root_workflow_id": "workflow",
                "invocation_id": request_id,
                "context_id": f"context-{request_id}",
                "context_epoch": epoch,
            }
            if tagged
            else None
        ),
    )


def _key(req) -> PrefillCandidateKey:
    return PrefillCandidateKey(
        request_id=req.rid,
        root_workflow_id=req.beliefkv_metadata["root_workflow_id"],
        invocation_id=req.beliefkv_metadata["invocation_id"],
        context_id=req.beliefkv_metadata["context_id"],
        context_epoch=req.beliefkv_metadata["context_epoch"],
        attempt_id=req.cache_request_handle.attempt_id,
    )


def test_prioritizes_tagged_without_changing_untagged_positions_or_native_queue() -> None:
    first, untagged, last = _req("first"), _req("native", tagged=False), _req("last")
    native = [first, untagged, last]
    selection = select_native_prefill_candidates(
        native,
        plan=NativePrefillPlan(7, (_key(last), _key(first))),
        current_semantic_revision=7,
    )
    assert selection.candidates == (last, untagged, first)
    assert selection.rejected == ()
    assert native == [first, untagged, last]


def test_skips_unauthorized_tagged_but_keeps_following_candidates() -> None:
    denied, untagged, allowed = _req("denied"), _req("native", tagged=False), _req("allowed")
    selection = select_native_prefill_candidates(
        (denied, untagged, allowed),
        plan=NativePrefillPlan(2, (_key(allowed),)),
        current_semantic_revision=2,
    )
    assert selection.candidates == (untagged, allowed)
    assert selection.rejected == (("denied", "no_authorization"),)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda req: setattr(req.cache_request_handle, "attempt_id", 1), "identity_changed"),
        (lambda req: req.beliefkv_metadata.update(context_epoch=1), "identity_changed"),
        (lambda req: req.beliefkv_metadata.update(context_id="new"), "identity_changed"),
        (lambda req: req.beliefkv_metadata.update(context_epoch=True), "invalid_identity"),
    ],
)
def test_rejects_changed_live_identity(change, reason) -> None:
    tagged, untagged = _req("tagged"), _req("native", tagged=False)
    plan = NativePrefillPlan(4, (_key(tagged),))
    change(tagged)
    result = select_native_prefill_candidates(
        (tagged, untagged), plan=plan, current_semantic_revision=4
    )
    assert result.candidates == (untagged,)
    assert result.rejected == (("tagged", reason),)


def test_stale_revision_and_bound_fail_closed_only_for_tagged() -> None:
    tagged, untagged = _req("tagged"), _req("native", tagged=False)
    plan = NativePrefillPlan(4, (_key(tagged),))
    stale = select_native_prefill_candidates(
        (tagged, untagged), plan=plan, current_semantic_revision=5
    )
    assert stale.candidates == (untagged,)
    assert stale.rejected == (("tagged", "stale_revision"),)
    bounded = select_native_prefill_candidates(
        (tagged, untagged), plan=plan, current_semantic_revision=4, max_candidates=1
    )
    assert bounded.candidates == (untagged,)
    assert bounded.rejected == (("tagged", "candidate_bound"),)


def test_rejects_duplicate_authorizations_and_native_request_ids() -> None:
    req = _req("tagged")
    with pytest.raises(ValueError, match="duplicate request ID"):
        select_native_prefill_candidates(
            (req,), plan=NativePrefillPlan(1, (_key(req), _key(req))), current_semantic_revision=1
        )
    with pytest.raises(ValueError, match="duplicate tagged"):
        select_native_prefill_candidates(
            (req, req), plan=NativePrefillPlan(1, (_key(req),)), current_semantic_revision=1
        )
