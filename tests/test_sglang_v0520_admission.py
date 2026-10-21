"""Admission order overlay; native PrefillAdder retains physical authority."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, asdict, fields, replace
import json
import pickle
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_admission import (
    NativePrefillPlan,
    PrefillCandidateKey,
    _request_key,
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


@pytest.mark.parametrize("field", [field.name for field in fields(PrefillCandidateKey)])
def test_key_hash_lookup_preserves_all_identity_fields(field):
    request = _req("tagged", epoch=2, attempt=3)
    request.session_id, request.session_generation = "session", 4
    key, rebuilt = _key(request), _key(request)
    assert rebuilt is not key
    assert key == rebuilt
    assert hash(key) == hash(rebuilt)
    values = {key: "authorized"}
    assert values[rebuilt] == "authorized"
    value = getattr(key, field)
    changed = replace(
        key, **{field: value + 1 if type(value) is int else f"{value}-changed"},
    )
    values[changed] = "different"
    assert len(values) == 2
    assert values[rebuilt] == "authorized"
    assert changed != key
    assert key != changed


def test_key_subclass_and_foreign_values_do_not_share_authorization():
    class OtherKey(PrefillCandidateKey):
        pass

    key = _key(_req("tagged"))
    subtype = OtherKey(**{field.name: getattr(key, field.name) for field in fields(key)})
    assert key != subtype
    assert subtype != key
    assert key != tuple(getattr(key, field.name) for field in fields(key))
    assert len({key, subtype}) == 2


@pytest.mark.parametrize("roundtrip", (
    copy, deepcopy,
    lambda key: pickle.loads(pickle.dumps(key, protocol=4)),
    lambda key: pickle.loads(pickle.dumps(key, protocol=pickle.HIGHEST_PROTOCOL)),
    lambda key: PrefillCandidateKey(**json.loads(json.dumps(asdict(key)))),
), ids=("copy", "deepcopy", "pickle4", "pickle_latest", "json"))
def test_identity_roundtrip_keeps_live_selection_and_rejects_changed_attempt(roundtrip):
    request = _req("tagged", epoch=2, attempt=3)
    request.session_id, request.session_generation = "session", 4
    key = _key(request)
    restored = roundtrip(key)
    assert type(restored) is type(key)
    assert asdict(restored) == asdict(key)
    assert {key: "authorized"}[restored] == "authorized"
    plan = compile_native_prefill_plan((restored,), semantic_revision=7)
    selection = select_native_prefill_candidates(
        (request,), plan=plan, current_semantic_revision=7,
    )
    assert selection.candidates == (request,)
    assert not selection.rejected
    request.cache_request_handle.attempt_id += 1
    selection = select_native_prefill_candidates(
        (request,), plan=plan, current_semantic_revision=7,
    )
    assert not selection.candidates
    assert selection.rejected == ((request.rid, "identity_changed"),)


@pytest.mark.parametrize("field", [field.name for field in fields(PrefillCandidateKey)])
def test_identity_fields_remain_immutable_after_roundtrip(field):
    key = pickle.loads(pickle.dumps(_key(_req("tagged"))))
    bound = {key: "authorized"}
    original = getattr(key, field)
    with pytest.raises(FrozenInstanceError):
        setattr(key, field, None)
    assert getattr(key, field) == original
    assert bound[key] == "authorized"


@pytest.mark.parametrize("streaming_session", (False, True))
def test_live_identity_reuses_the_bound_key_after_session_normalization(streaming_session):
    request = _req("tagged")
    request.session_generation = 3
    if streaming_session:
        request.session = NS(session_id="session")
    else:
        request.session_id = "session"
    bound = _request_key(request)
    known = {request.rid: bound}
    assert _request_key(request, known_keys=known) is bound
    assert _request_key(request, known_keys={}) == bound
    request.beliefkv_metadata["unrelated"] = "changed"
    assert _request_key(request, known_keys=known) is bound


@pytest.mark.parametrize("change", (
    lambda req: setattr(req, "rid", "new-request"),
    lambda req: req.beliefkv_metadata.update(root_workflow_id="new-workflow"),
    lambda req: req.beliefkv_metadata.update(invocation_id="new-invocation"),
    lambda req: req.beliefkv_metadata.update(context_id="new-context"),
    lambda req: req.beliefkv_metadata.update(context_epoch=1),
    lambda req: setattr(req.cache_request_handle, "attempt_id", 1),
    lambda req: setattr(req, "session_id", "new-session"),
    lambda req: setattr(req, "session_generation", 4),
    lambda req: req.beliefkv_metadata.update(context_epoch=True),
    lambda req: setattr(req.cache_request_handle, "attempt_id", -1),
    lambda req: setattr(req, "session_generation", True),
    lambda req: setattr(req, "session", NS(session_id="conflicting-session")),
    lambda req: setattr(req, "rid", []),
    lambda req: setattr(req, "beliefkv_metadata", None),
))
def test_bound_identity_still_observes_every_changed_or_invalid_live_field(change):
    request = _req("tagged")
    request.session_id, request.session_generation = "session", 3
    bound = _request_key(request)
    known = {request.rid: bound}
    change(request)
    current = _request_key(request, known_keys=known)
    assert current == _request_key(request)
    assert current is not bound
    assert known["tagged"] is bound


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


@pytest.mark.parametrize("field", ("root_workflow_id", "invocation_id", "context_id"))
@pytest.mark.parametrize("value", (None, "", 7, False))
def test_compiler_rejects_invalid_required_identity_fields(field, value) -> None:
    req = _req("tagged")
    req.beliefkv_metadata[field] = value
    with pytest.raises(ValueError, match="invalid tagged identity"):
        compile_native_prefill_plan((req,), semantic_revision=1)
    assert select_native_prefill_candidates(
        (req,), plan=NativePrefillPlan(1, ()), current_semantic_revision=1,
    ).rejected == (("tagged", "invalid_identity"),)


@pytest.mark.parametrize("changed", ("epoch", "attempt", "generation"))
def test_compiler_reuses_bound_keys_but_selection_rechecks_live_identity(changed) -> None:
    first, last, native = _req("first"), _req("last"), _req("native", tagged=False)
    first.session_id, first.session_generation = "agent-1", 4
    bound = (_key(last), _key(first))
    plan = compile_native_prefill_plan(bound, semantic_revision=7)
    assert plan.prioritized == bound
    assert select_native_prefill_candidates(
        (first, native, last), plan=plan, current_semantic_revision=7,
    ).candidates == (last, native, first)
    if changed == "epoch":
        first.beliefkv_metadata["context_epoch"] += 1
    elif changed == "attempt":
        first.cache_request_handle.attempt_id += 1
    else:
        first.session_generation += 1
    selection = select_native_prefill_candidates(
        (first, native, last), plan=plan, current_semantic_revision=7,
    )
    assert selection.candidates == (native, last)
    assert selection.rejected == (("first", "identity_changed"),)
    with pytest.raises(ValueError, match="duplicate request ID"):
        compile_native_prefill_plan((bound[0], bound[0]), semantic_revision=7)
    with pytest.raises(ValueError, match="exceeds bound"):
        compile_native_prefill_plan(bound, semantic_revision=7, max_candidates=1)


def test_mixed_rejections_preserve_every_surviving_native_position_and_request_identity():
    denied, first, changed, second = (_req(name) for name in ("denied", "first", "changed", "second"))
    native_first, native_last = _req("native", tagged=False), _req("native", tagged=False)
    native = [denied, None, first, native_first, changed, second, None, native_last]
    original = tuple(id(request) for request in native)
    plan = NativePrefillPlan(3, (_key(second), _key(changed), _key(first)))
    changed.beliefkv_metadata["context_epoch"] += 1
    selection = select_native_prefill_candidates(
        native, plan=plan, current_semantic_revision=3,
    )
    assert tuple(id(request) for request in selection.candidates) == tuple(
        id(request) for request in (None, second, native_first, first, None, native_last)
    )
    assert selection.rejected == (
        ("denied", "no_authorization"), ("changed", "identity_changed"),
    )
    assert tuple(id(request) for request in native) == original


@pytest.mark.parametrize("stale", (False, True))
def test_empty_or_all_native_queue_retains_objects_even_with_absent_authorizations(stale):
    native = [None, _req("same-native-id", tagged=False), _req("same-native-id", tagged=False)]
    plan = NativePrefillPlan(1, (_key(_req("not-enqueued")),))
    selection = select_native_prefill_candidates(
        native, plan=plan, current_semantic_revision=1 + int(stale),
    )
    assert tuple(id(request) for request in selection.candidates) == tuple(map(id, native))
    assert not selection.rejected
    empty = select_native_prefill_candidates(
        (), plan=plan, current_semantic_revision=1 + int(stale),
    )
    assert not empty.candidates and not empty.rejected
