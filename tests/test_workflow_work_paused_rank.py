from experiments.reports.workflow_work_training_20261029.audit_paused_rank import (
    continuous_pair_metrics, pair_metrics,
)


def candidate(workflow, truth, prediction, size, category=0):
    return {
        "workflow": workflow, "true_bucket": truth, "bucket": prediction,
        "context_tokens": size, "category": category,
    }


def test_paused_rank_counts_helpful_and_harmful_context_order_reversals():
    rows = [
        [candidate("a", 0, 0, 10), candidate("b", 2, 2, 5)],
        [candidate("a", 0, 2, 5), candidate("b", 2, 0, 10)],
    ]
    metrics = pair_metrics(rows)
    assert metrics["beneficial_class_reversal"] == metrics["harmful_class_reversal"] == 1
    assert metrics["disambiguated_pair_accuracy"] == .5
    assert metrics["context_order_correct"] == metrics["combined_order_correct"] == 1


def test_sibling_fanout_and_different_categories_do_not_multiply_comparisons():
    rows = [[
        candidate("a", 0, 0, 10), candidate("a", 0, 0, 11),
        candidate("b", 2, 2, 5), candidate("other-category", 2, 2, 1, category=1),
    ]]
    assert pair_metrics(rows)["different_true_class_pairs"] == 1


def test_uncertain_medium_is_not_counted_as_a_correct_sort():
    metrics = pair_metrics([[candidate("a", 0, 1, 5), candidate("b", 2, 1, 10)]])
    assert metrics["predicted_ties"] == 1
    assert metrics["disambiguated_pair_accuracy"] is None


def test_continuous_order_quality_does_not_depend_on_training_class_thresholds():
    rows = [[
        {**candidate("a", 1, 0, 10), "remaining_logical_work": 5.},
        {**candidate("b", 1, 1, 5), "remaining_logical_work": 9.},
    ]]
    result = continuous_pair_metrics(rows)
    assert result["comparable_pairs"] == result["combined_order_correct"] == 1
    assert result["context_order_correct"] == 0
