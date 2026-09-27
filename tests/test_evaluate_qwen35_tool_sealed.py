import pytest

from scripts import evaluate_qwen35_tool_sealed as sealed


def test_tool_sealed_uses_training_only_clock_and_identity(monkeypatch, tmp_path):
    identity = {"test_workflows": 16}
    monkeypatch.setattr(sealed, "validate_sealed_run", lambda *args: identity)
    calls = []
    monkeypatch.setattr(sealed, "evaluate", lambda train, heldout: (
        calls.append((train, heldout))
        or {"heldout_workflows": 16, "heldout": {
            "distinct_tasks": 8,
            "real_windows": 5,
            "modes": {"calls": {"selected": 4}},
        }}
    ))
    frozen = tmp_path / "test.json"
    provenance = tmp_path / "provenance.json"
    train = [tmp_path / "high", tmp_path / "low"]
    heldout = tmp_path / "test" / "workflows"
    report = sealed.score_sealed_tool(frozen, provenance, train, heldout)
    assert calls == [(train, heldout)]
    assert report["status"] == (
        "project_disjoint_sealed_tool_shadow_not_action_eligible"
    )
    assert report["heldout"]["modes"]["calls"]["selected"] == 4


def test_tool_sealed_rejects_foreign_workflow_count(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sealed, "validate_sealed_run",
        lambda *args: {"test_workflows": 16},
    )
    monkeypatch.setattr(
        sealed, "evaluate",
        lambda *args: {
            "heldout_workflows": 8,
            "heldout": {"distinct_tasks": 8},
        },
    )
    with pytest.raises(ValueError, match="frozen test identities"):
        sealed.score_sealed_tool(
            tmp_path / "test.json", tmp_path / "provenance.json",
            [tmp_path / "train"], tmp_path / "test" / "workflows",
        )
