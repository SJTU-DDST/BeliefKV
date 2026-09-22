"""Admission order overlay; native PrefillAdder retains physical authority."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_admission import (
    NativePrefillPlan,
    PrefillCandidateKey,
    compile_native_prefill_plan,
    select_native_prefill_candidates,
)


def _req(request_id: str, *, epoch: int = 0, attempt: int = 0, tagged: bool = True):
    return NS(
        rid=request_id,
        cache_request_handle=NS(attempt_id=attempt),
        session_id=None,
        session_generation=None,
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
        session_id=req.session_id,
        session_generation=req.session_generation,
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
    assert bounded.candidates == (tagged, untagged)
    assert bounded.rejected == ()


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


def test_native_session_generation_change_invalidates_prefill_authorization() -> None:
    req = _req("tagged")
    req.session_id = "context-1"
    req.session_generation = 7
    plan = NativePrefillPlan(3, (_key(req),))
    req.session_generation = 8
    result = select_native_prefill_candidates(
        (req,), plan=plan, current_semantic_revision=3
    )
    assert result.candidates == ()
    assert result.rejected == (("tagged", "identity_changed"),)


def test_session_controller_identity_and_invalid_session_fail_closed() -> None:
    req = _req("tagged")
    req.session = NS(session_id="stream-1")
    plan = NativePrefillPlan(
        3, (PrefillCandidateKey("tagged", "workflow", "tagged", "context-tagged", 0, 0, "stream-1"),)
    )
    assert select_native_prefill_candidates(
        (req,), plan=plan, current_semantic_revision=3
    ).candidates == (req,)
    req.session_id = "different-session"
    assert select_native_prefill_candidates(
        (req,), plan=plan, current_semantic_revision=3
    ).rejected == (("tagged", "invalid_identity"),)
    req.session = None
    req.session_id = "stream-1"
    req.session_generation = True
    assert select_native_prefill_candidates(
        (req,), plan=plan, current_semantic_revision=3
    ).rejected == (("tagged", "invalid_identity"),)


def test_compiler_binds_session_and_only_explicitly_prioritized_requests() -> None:
    first, last, native = _req("first"), _req("last"), _req("native", tagged=False)
    first.session_id = "agent-1"
    first.session_generation = 4
    plan = compile_native_prefill_plan((last, first), semantic_revision=7)
    assert plan.prioritized == (_key(last), _key(first))
    selection = select_native_prefill_candidates(
        (first, native, last), plan=plan, current_semantic_revision=7
    )
    assert selection.candidates == (last, native, first)
    first.session_generation = 5
    selection = select_native_prefill_candidates(
        (first, native, last), plan=plan, current_semantic_revision=7
    )
    assert selection.candidates == (native, last)
    assert selection.rejected == (("first", "identity_changed"),)


def test_compiler_rejects_missing_identity_duplicate_and_oversized_decision() -> None:
    req = _req("first")
    with pytest.raises(ValueError, match="invalid tagged identity"):
        compile_native_prefill_plan((_req("plain", tagged=False),), semantic_revision=1)
    with pytest.raises(ValueError, match="duplicate request ID"):
        compile_native_prefill_plan((req, req), semantic_revision=1)
    with pytest.raises(ValueError, match="exceeds bound"):
        compile_native_prefill_plan((req, req), semantic_revision=1, max_candidates=1)
    with pytest.raises(ValueError, match="invalid semantic revision"):
        compile_native_prefill_plan((req,), semantic_revision=True)
