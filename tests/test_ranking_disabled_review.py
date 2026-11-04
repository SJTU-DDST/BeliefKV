from copy import deepcopy

import pytest

from scripts.review_workflow_ranking_disabled import matching


def test_matched_ranking_review_rejects_changed_traffic_or_capacity():
    plan = {"order": ["full"], "source_arm": "original", "expected_replay_capacity": {"Host": 400}}
    matching([plan, deepcopy(plan)])
    for change in (
        {"order": ["thunderagent_acting_decay"]},
        {"source_arm": "different"},
        {"expected_replay_capacity": {"Host": 200}},
    ):
        with pytest.raises(ValueError):
            matching([plan, {**plan, **change}])
