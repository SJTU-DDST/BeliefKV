import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES
from scripts.train_workflow_utility_readout import ranking_queries, sampled, selection_score, utility_metrics


def row(owner, work, snapshot=0):
    features = [0.] * len(R5_FEATURES)
    features[R5_FEATURES.index("ready")] = 1.
    return {"workflow": owner, "remaining_work": work, "snapshot": snapshot, "features": features}


def test_sampling_keeps_whole_workflow_mass_bounded_without_using_labels():
    rows = [row(owner, index + 1., index) for owner in ("a", "b") for index in range(10)]
    selected = sampled(rows, 3)
    assert [(item["workflow"], item["snapshot"]) for item in selected] == [
        ("a", 0), ("a", 4), ("a", 9), ("b", 0), ("b", 4), ("b", 9),
    ]
    changed = [{**item, "remaining_work": 99999.} for item in rows]
    assert [(item["workflow"], item["snapshot"]) for item in sampled(changed, 3)] == [
        (item["workflow"], item["snapshot"]) for item in selected
    ]


def test_metrics_distinguish_large_gap_sorting_and_bounded_utility_error():
    rows = [row("a", 10.), row("b", 100.)]
    correct = utility_metrics(rows, [.8, .1], 10.)
    wrong = utility_metrics(rows, [.1, .8], 10.)
    assert correct["ready"]["pair_equal_weight_accuracy"] == 1.
    assert wrong["ready"]["work_ratio_at_least_two_pair_accuracy"] == 0.
    assert selection_score(correct) == 1.
    assert utility_metrics(rows, [.5, .5], 10.)["initial"]["pair_equal_weight_accuracy"] == .5
    with pytest.raises(ValueError):
        utility_metrics(rows, [1.1, .5], 10.)


def test_pairwise_queries_use_six_distinct_training_workflows_with_shorter_relevance_higher():
    rows = [row(str(index), float(index + 1)) for index in range(8)]
    indices, relevance, sizes = ranking_queries(rows, count=3)
    assert sizes == [6, 6, 6]
    for start in range(0, len(indices), 6):
        selected = indices[start:start + 6]
        labels = relevance[start:start + 6]
        assert len(set(selected)) == 6
        assert sorted(labels) == list(range(6))
        assert all(
            (rows[left]["remaining_work"] - rows[right]["remaining_work"]) * (a - b) < 0
            for left, a in zip(selected, labels) for right, b in zip(selected, labels) if left != right
        )
