import pytest

from scripts.audit_checkpoint_write_roles import write_roles


def event(timestamp, node=None, *, context="a", kind="llm_result", prefix=100, status="observed"):
    return {
        "kind": kind, "ts_ms": timestamp, "workflow_id": "workflow", "context_id": context,
        "attributes": {
            "request_id": f"{kind}:{context}:{timestamp}",
            "reentry_checkpoint": {
                "status": status,
                "mamba_states": [] if node is None else [{
                    "node_id": node, "prefix_tokens": prefix, "within_safe_input": True,
                }],
            },
        },
    }


def write(timestamp, node, *, units=1):
    return {
        "command_id": f"write:{timestamp}:{node}", "direction": "d2h", "status": "completed",
        "submit_ts_ms": timestamp, "node_ids": [node], "num_tokens_by_pool": {"mamba": units},
    }


def test_only_past_observations_classify_a_write_and_new_request_means_unknown():
    result = write_roles([
        event(1, 10), event(4, kind="llm_submit"), event(6, 20), event(10, 30),
    ], [write(2, 10), write(5, 10), write(7, 10), write(8, 20), write(9, 30)], 0, 20, 64)
    assert {key: value["copy_units"] for key, value in result["roles"].items()} == {
        "last_observed_current_safe_anchor": 2,
        "prior_safe_anchor_current_state_unknown": 1,
        "prior_safe_anchor_superseded_in_observed_contexts": 1,
        "not_previously_observed_as_safe_anchor": 1,
    }
    assert result["counts"]["single_node_ever_observed_safe_including_future"] == 5


def test_shared_anchor_stays_current_when_one_context_advances():
    result = write_roles([
        event(1, 10), event(2, 10, context="b"), event(3, 20),
    ], [write(4, 10)], 0, 5, 64)
    assert result["roles"]["last_observed_current_safe_anchor"]["copy_units"] == 1


@pytest.mark.parametrize("status,node", [("missing", None), ("observed", None), ("snapshot_unavailable", None)])
def test_empty_or_unavailable_snapshot_cannot_prove_retirement(status, node):
    result = write_roles([
        event(1, 10), event(2, node, status=status),
    ], [write(3, 10)], 0, 5, 64)
    assert result["roles"]["prior_safe_anchor_current_state_unknown"]["copy_units"] == 1


def test_equal_time_is_not_past_and_multi_node_pool_identity_is_unknown():
    combined = write(3, 10, units=2)
    combined["node_ids"] = [10, 20]
    result = write_roles([event(2, 10)], [write(2, 10), combined], 0, 5, 64)
    assert result["roles"]["not_previously_observed_as_safe_anchor"]["copy_units"] == 1
    assert result["counts"]["unassignable_mamba_copy_units"] == 2


def test_conflicting_prefix_observations_are_not_assumed_same_checkpoint():
    result = write_roles([
        event(1, 10, prefix=100), event(2, 10, prefix=200),
    ], [write(3, 10)], 0, 5, 64)
    assert result["roles"]["observed_prefix_identity_conflict"]["copy_units"] == 1


def test_uncompleted_transfer_with_unknown_timestamp_is_not_a_write():
    pending = write(None, 10)
    pending["status"] = "pending"
    result = write_roles([event(1, 10)], [pending, write(3, 10)], 0, 5, 64)
    assert result["counts"]["cohort_mamba_copy_units"] == 1


@pytest.mark.parametrize("kind", ["duplicate", "invalid_prefix", "duplicate_node"])
def test_invalid_request_or_checkpoint_evidence_is_rejected(kind):
    row = event(1, 10)
    rows = [row]
    if kind == "duplicate":
        rows.append(row)
    elif kind == "invalid_prefix":
        row["attributes"]["reentry_checkpoint"]["mamba_states"][0]["prefix_tokens"] = True
    else:
        states = row["attributes"]["reentry_checkpoint"]["mamba_states"]
        states.append(states[0])
    with pytest.raises(ValueError):
        write_roles(rows, [write(3, 10)], 0, 5, 64)
