import pytest

from scripts.train_workflow_decision_readout import adapted_weights, decision_rows
from scripts.train_workflow_utility_readout import utility_metrics
from tests.test_workflow_utility_readout import row


def test_decision_states_keep_their_complete_workflow_partition_and_deduplicate():
    partitions = {
        name: [{"workflow": name, "logical_workflow_id": name}]
        for name in ("training", "calibration", "development")
    }
    candidate = {"workflow": "development", "features": [1., 2.], "remaining_model_work": 30.}
    audit = {"decisions": [
        {"planning_capacity_competition": True, "ordinary_workflows": [candidate]},
        {"planning_capacity_competition": True, "ordinary_workflows": [candidate]},
    ]}
    result = decision_rows(partitions, audit)
    assert result["training"] == result["calibration"] == []
    assert len(result["development"]) == 1
    candidate["workflow"] = "unknown"
    with pytest.raises(ValueError, match="split binding"):
        decision_rows(partitions, audit)


def test_adaptation_keeps_workflow_mass_equal_and_balances_the_two_sources():
    rows = [
        {"workflow": "a"}, {"workflow": "a"}, {"workflow": "a", "decision_state": True},
        {"workflow": "b"},
    ]
    weights = adapted_weights(rows)
    assert list(weights) == [.125, .125, .25, .5]
    assert sum(weights) == 1.


def test_actual_ready_paused_population_includes_previously_served_running_features():
    rows = [row("a", 10.), row("b", 100.)]
    for item in rows:
        item["features"] = [0.] * len(item["features"])
        item["decision_state"] = True
    result = utility_metrics(rows, [.8, .1], 10.)
    assert result["ready"]["workflow_pairs"] == 1
    assert result["ready"]["pair_equal_weight_accuracy"] == 1.
