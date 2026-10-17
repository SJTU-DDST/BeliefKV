from copy import deepcopy

import pytest

from scripts.audit_checkpoint_continuation_compatibility import (
    continuation_profile, historical_write_roles, successor_inputs_before_write,
)


def state(node=1, prefix=12, role="generated_output_tail", created=1):
    return {
        "node_id": node, "creation_time": created, "prefix_tokens": prefix,
        "role": role,
    }


def continuation():
    return {
        "event": "generation_continuation_prefix_probe", "ts_ms": 20,
        "workflow_id": "workflow", "context_id": "context",
        "request_id": "next", "prior_request_id": "prior",
        "prior_input_tokens": 10, "prior_generated_output_tokens": 5,
        "current_input_tokens": 16, "common_prior_input_prefix_tokens": 9,
        "common_generated_output_prefix_tokens": 0,
        "common_prior_transcript_prefix_tokens": 9,
        "checkpoint_path_observed": True,
        "checkpoint_compatibility": [
            {**state(), "prefix_compatibility": "token_prefix_diverges"},
            {**state(2, 8, "input_prefix"), "prefix_compatibility": "exact_prefix_compatible"},
        ],
    }


def completion(timestamp=10, *, node=1, created=1, role="generated_output_tail"):
    return {
        "kind": "llm_result", "ts_ms": timestamp, "attributes": {
            "request_id": f"prior-{timestamp}",
            "checkpoint_path_evidence": {
                "status": "observed", "request_input_tokens": 10,
                "probe_wall_ns": 200000, "probe_thread_cpu_ns": 100000,
                "mamba_states": [state(node, 12 if role == "generated_output_tail" else 8, role, created)],
            },
        },
    }


def transfer(timestamp=15, *, nodes=(1,), units=1):
    return {
        "command_id": f"copy-{timestamp}", "status": "completed", "direction": "d2h",
        "submit_ts_ms": timestamp, "node_ids": list(nodes),
        "num_tokens_by_pool": {"mamba": units},
    }


def test_last_input_token_divergence_prevents_reusing_generated_prefix():
    result = continuation_profile([continuation()], 0, 30)
    assert result["counts"]["prior_input_last_token_diverges"] == 1
    assert result["counts"]["preserved_generated_prefix_tokens"] == 0
    assert result["distinct_checkpoints_ever_observed_as_output_tail"] == 1
    assert result["output_tail_checkpoints_also_observed_compatible"] == 0
    assert result["checkpoint_observations_by_role_and_compatibility"] == {
        "generated_output_tail/token_prefix_diverges": 1,
        "input_prefix/exact_prefix_compatible": 1,
    }


def test_compatible_output_is_retained_instead_of_assuming_all_tails_are_waste():
    row = continuation()
    row.update(
        common_prior_input_prefix_tokens=10, common_generated_output_prefix_tokens=4,
        common_prior_transcript_prefix_tokens=14,
    )
    row["checkpoint_compatibility"][0]["prefix_compatibility"] = "exact_prefix_compatible"
    result = continuation_profile([row], 0, 30)
    assert result["counts"]["preserved_generated_prefix_tokens"] == 4
    assert result["output_tail_checkpoints_also_observed_compatible"] == 1
    selections = result["illustrative_per_path_selections"]
    assert selections["furthest_one"]["retains_furthest_compatible_checkpoint"] == 1
    assert selections["latest_at_or_before_input_boundary"]["retains_furthest_compatible_checkpoint"] == 0


def test_simple_path_restrictions_keep_the_reusable_input_anchor():
    selections = continuation_profile([continuation()], 0, 30)[
        "illustrative_per_path_selections"
    ]
    assert selections["furthest_one"]["retains_some_compatible_checkpoint"] == 0
    assert selections["furthest_two"]["retains_furthest_compatible_checkpoint"] == 1
    assert selections["latest_at_or_before_input_boundary"]["retains_furthest_compatible_checkpoint"] == 1
    assert selections["furthest_two"]["retained_checkpoint_observations"] == 2


def test_unavailable_paths_do_not_become_failed_selection_trials():
    row = continuation()
    row.update(checkpoint_path_observed=False, checkpoint_compatibility=[])
    assert continuation_profile([row], 0, 30)["illustrative_per_path_selections"] == {}


def test_a_changed_final_input_token_can_invalidate_the_input_boundary_itself():
    row = continuation()
    row["checkpoint_compatibility"].append({
        **state(3, 10, "input_boundary"), "prefix_compatibility": "token_prefix_diverges",
    })
    selections = continuation_profile([row], 0, 30)["illustrative_per_path_selections"]
    assert selections["furthest_two"]["retains_some_compatible_checkpoint"] == 0
    assert selections["latest_at_or_before_input_boundary"]["retains_some_compatible_checkpoint"] == 0
    assert selections["latest_strict_input_prefix"]["retains_furthest_compatible_checkpoint"] == 1


def test_repeated_logical_checkpoint_observations_are_not_distinct_copies():
    first, second = continuation(), continuation()
    second.update(request_id="third", prior_request_id="next", ts_ms=25)
    result = continuation_profile([first, second], 0, 30)
    assert result["counts"]["continuations"] == 2
    assert result["distinct_logical_checkpoints"] == 2
    assert result["checkpoint_observations_by_role_and_compatibility"][
        "generated_output_tail/token_prefix_diverges"
    ] == 2


def test_unobserved_paths_and_unobserved_successors_are_not_checkpoint_misses():
    row = continuation()
    row.update(checkpoint_path_observed=False, checkpoint_compatibility=[])
    result = continuation_profile([row, {"event": "different_event"}], 0, 30)
    assert result["counts"]["continuations"] == 1
    assert result["counts"]["checkpoint_path_unavailable"] == 1
    assert result["distinct_logical_checkpoints"] == 0


@pytest.mark.parametrize("bad", ["duplicate", "lengths", "role", "compatibility", "identity"])
def test_inconsistent_evidence_is_rejected(bad):
    row = continuation()
    rows = [row]
    if bad == "duplicate":
        rows.append(deepcopy(row))
    elif bad == "lengths":
        row["common_generated_output_prefix_tokens"] = 1
    elif bad == "role":
        row["checkpoint_compatibility"][0]["role"] = "input_prefix"
    elif bad == "compatibility":
        row["checkpoint_compatibility"][0]["prefix_compatibility"] = "exact_prefix_compatible"
    else:
        row["prior_request_id"] = None
    with pytest.raises(ValueError):
        continuation_profile(rows, 0, 30)


def test_write_classification_uses_only_strictly_earlier_observations():
    events = [completion(10), completion(30, node=2), completion(40, node=3)]
    result = historical_write_roles(
        events, [transfer(10), transfer(15), transfer(25, nodes=(2,))], 0, 35, 64,
    )
    assert result["write_units_by_last_observed_role"] == {
        "last_observed_generated_output_tail": {"copy_units": 1, "mamba_bytes": 64},
        "no_prior_completion_path_observation": {"copy_units": 2, "mamba_bytes": 128},
    }
    assert result["prior_observation_age_ms_by_role"][
        "last_observed_generated_output_tail"
    ]["p50"] == 5


def test_aggregate_writes_are_not_evenly_assigned_to_nodes():
    result = historical_write_roles(
        [completion(), completion(40)], [transfer(nodes=(1, 2), units=2)], 0, 30, 64,
    )
    assert result["write_units_by_last_observed_role"] == {
        "unassignable_aggregate_batch": {"copy_units": 2, "mamba_bytes": 128}
    }


def test_changed_node_identity_keeps_writes_unknown_without_affecting_earlier_copies():
    events = [completion(10), completion(20, created=2), completion(40)]
    result = historical_write_roles(events, [transfer(15), transfer(25)], 0, 30, 64)
    assert result["write_units_by_last_observed_role"] == {
        "last_observed_generated_output_tail": {"copy_units": 1, "mamba_bytes": 64},
        "observed_node_identity_conflict": {"copy_units": 1, "mamba_bytes": 64},
    }


def test_missing_completion_coverage_is_not_a_valid_empty_window():
    with pytest.raises(ValueError, match="do not cover"):
        historical_write_roles([completion()], [], 0, 30, 64)


@pytest.mark.parametrize(
    ("timestamp", "status"),
    [
        (19, "no_comparison_input_in_window_before_write"),
        (20, "no_comparison_input_in_window_before_write"),
        (25, "only_incompatible_successors_observed"),
    ],
)
def test_successor_input_must_strictly_precede_the_write(timestamp, status):
    observations = []
    continuation_profile([continuation()], 0, 30, observation_sink=observations)
    result = successor_inputs_before_write(observations, [transfer(timestamp)], 0, 30, 64)
    assert result["copy_units_by_observed_successor_status"] == {
        status: {"copy_units": 1, "mamba_bytes": 64}
    }


@pytest.mark.parametrize("context", ["context", "other"])
def test_a_compatible_successor_in_any_context_cannot_be_hidden(context):
    first, second = continuation(), continuation()
    second.update(
        context_id=context, request_id="other-next", prior_request_id="other-prior",
        ts_ms=22, common_prior_input_prefix_tokens=10,
        common_generated_output_prefix_tokens=4, common_prior_transcript_prefix_tokens=14,
    )
    second["checkpoint_compatibility"][0]["prefix_compatibility"] = "exact_prefix_compatible"
    observations = []
    continuation_profile([first, second], 0, 30, observation_sink=observations)
    result = successor_inputs_before_write(observations, [transfer(25)], 0, 30, 64)
    assert result["copy_units_by_observed_successor_status"] == {
        "compatible_and_incompatible_successors_observed": {"copy_units": 1, "mamba_bytes": 64}
    }


def test_prior_window_comparisons_and_aggregate_nodes_are_not_assigned():
    observations = []
    continuation_profile([continuation()], 21, 30, observation_sink=observations)
    result = successor_inputs_before_write(
        observations, [transfer(25), transfer(26, nodes=(1, 2), units=2)], 21, 30, 64,
    )
    assert result["copy_units_by_observed_successor_status"] == {
        "no_comparison_input_in_window_before_write": {"copy_units": 1, "mamba_bytes": 64},
        "unassignable_aggregate_batch": {"copy_units": 2, "mamba_bytes": 128},
    }
