import pytest

from scripts.audit_child_report_prefix_length import (
    evaluate, fit, predict_length, prefix_features, score,
)


def _row(project, index, *, length=1800, lead=1200):
    return {
        "project": project,
        "task_id": f"{project}__{index}",
        "features": [index % 3, index % 4, 0, 10, 2, 1, 1, 1, 25],
        "final_output_chars_oracle": length,
        "ms_per_char": 1.,
        "lead_ms": lead,
        "join_last": index == 0,
    }


def test_prefix_features_only_accepts_observed_characters():
    text = "## Summary\n- test\n```" + "x" * 1100
    features = prefix_features(text[:1024])
    assert features == prefix_features((text + "future information")[:1024])
    assert features[0] == 1
    assert features[1] == 1
    with pytest.raises(ValueError, match="already streamed"):
        prefix_features(text)


def test_prefix_model_fits_train_only_and_scores_join_separately():
    train = [_row(project, index, length=1700 + index * 40)
             for project in ("alpha", "beta", "gamma")
             for index in range(8)]
    test = [_row("delta", 0), _row("delta", 1, length=2300, lead=2000)]
    model = fit(train)
    estimate = predict_length(test[0], model)
    result = score(train, test)
    assert result["train_count"] == 24
    assert result["count"] == 2
    assert result["join_last_child"] == 1
    assert result["prefix_structure"]["join_last_child"]["count"] == 1
    assert result["future_length_oracle"]["return"]["count"] == 2
    test[0]["final_output_chars_oracle"] = 9000
    assert predict_length(test[0], fit(train)) == estimate


def test_evaluate_rejects_overlapping_projects(monkeypatch, tmp_path):
    train = [_row(project, index) for project in ("alpha", "beta", "gamma")
             for index in range(8)]
    heldout = [_row("delta", 0)]
    roots = [tmp_path / "train", tmp_path / "heldout"]
    monkeypatch.setattr(
        "scripts.audit_child_report_prefix_length.load",
        lambda root: (train if root == roots[0] else heldout, {}),
    )
    assert evaluate([roots[0]], roots[1])["train_rows"] == 24
    heldout[0]["project"] = "alpha"
    with pytest.raises(ValueError, match="disjoint"):
        evaluate([roots[0]], roots[1])
