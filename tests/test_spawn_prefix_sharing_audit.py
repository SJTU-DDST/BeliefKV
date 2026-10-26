from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from scripts import audit_spawn_prefix_sharing as audit


def source_fixture(monkeypatch, *, complete_before_spawn=True, different_salt=False):
    def event(kind, time, invocation, request=None, parent=None):
        return NS(
            kind=kind, ts_ms=time, workflow_id="workflow", invocation_id=invocation,
            attributes={"request_id": request}, parent_invocation_id=parent,
        )

    events = [
        event(Kind.LLM_SUBMIT, 1., "parent", "before"),
        event(Kind.LLM_RESULT, 2. if complete_before_spawn else 4., "parent", "before"),
        event(Kind.INVOCATION_CREATE, 3., "child", parent="parent"),
        event(Kind.LLM_SUBMIT, 5., "parent", "later"),
        event(Kind.LLM_SUBMIT, 6., "child", "child-first"),
        event(Kind.LLM_SUBMIT, 7., "child", "child-later"),
    ]
    plan = NS(nodes=[NS(event=value) for value in events])
    monkeypatch.setattr(audit, "source_plan", lambda _arm: (plan, {}, {}))
    reads = []

    class Corpus:
        evidence = {"fixture_only": True}

        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, identity):
            reads.append(identity)
            assert identity in ("before", "child-first")
            return NS(
                identity=("workflow", "parent" if identity == "before" else "child"),
                prompt_tokens=array("I", (1, 2) if identity == "before" else (1, 2, 3, 9)),
                output_tokens=array("I", (3,)),
                extra_key=None,
                cache_salt="other" if identity != "before" and different_salt else None,
                prompt_sha256="fixture",
            )

    monkeypatch.setattr(audit, "CapturedTokenCorpus", Corpus)
    return reads


def test_parent_history_is_frozen_at_creation_not_later_parent_or_child_calls(monkeypatch, tmp_path):
    reads = source_fixture(monkeypatch)
    result = audit.audit(tmp_path)
    assert reads == ["before", "child-first"]
    assert result["summary"]["verified_pairs"] == 1
    assert result["rows"][0]["common_prefix_tokens"] == 3
    assert result["rows"][0]["common_fraction_of_child_prompt"] == .75


def test_unfinished_parent_output_cannot_supply_future_shared_tokens(monkeypatch, tmp_path):
    reads = source_fixture(monkeypatch, complete_before_spawn=False)
    result = audit.audit(tmp_path)
    assert reads == []
    assert result["summary"]["verified_pairs"] == 0
    assert result["summary"]["unknown_pairs"] == {
        "parent_output_not_observed_before_child_creation": 1,
    }


def test_equal_tokens_in_different_namespaces_do_not_establish_cache_sharing(monkeypatch, tmp_path):
    source_fixture(monkeypatch, different_salt=True)
    result = audit.audit(tmp_path)
    assert result["rows"][0]["common_prefix_tokens"] == 3
    assert result["rows"][0]["namespace_compatible_common_prefix_tokens"] == 0


@pytest.mark.parametrize(("left", "right", "expected"), [
    ((), (1,), 0), ((1, 2), (1, 2, 3), 2), ((1, 2, 3), (1, 2, 4), 2),
    ((1,), (2,), 0),
])
def test_prefix_boundaries(left, right, expected):
    assert audit.common_prefix(array("I", left), array("I", right)) == expected
