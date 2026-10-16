import json

import pytest

from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_wait_state_recovery import audit, recoveries, summarize


def park(ts=10., node=1, **extra):
    return {
        "workflow_id": "wf", "invocation_id": "root", "context_id": "ctx",
        "context_epoch": 0, "ts_ms": ts, "node_id": node,
        "freed_units": {"0": 5}, "source": "join_wait", **extra,
    }


def request(ts=20., observed=40., **extra):
    return {
        "kind": "llm_submit", "workflow_id": "wf", "invocation_id": "root",
        "context_id": "ctx", "context_epoch": 1, "ts_ms": ts,
        "attributes": {
            "request_id": "req", "cached_tokens_host": 5,
            "native_host_hit_match_path": {
                "observed_ts_ms": observed, "last_host_path": [[1, 7.]],
            },
        }, **extra,
    }


def transfer(command="load", ts=25., nodes=None, pools=None, **extra):
    return {
        "command_id": command, "status": "completed", "direction": "h2d",
        "node_ids": [1] if nodes is None else nodes,
        "num_tokens_by_pool": {"kv": 5} if pools is None else pools,
        "actual_bytes": 50, "submit_ts_ms": ts, "complete_ts_ms": ts + 1.,
        "transfer_stream_elapsed_ms": 0.5, **extra,
    }


def test_later_request_needs_same_workflow_invocation_context_and_new_epoch():
    distractors = [
        request(ts=12., workflow_id="other"),
        request(ts=13., invocation_id="child"),
        request(ts=14., context_id="other"),
        request(ts=15., context_epoch=0),
    ]
    for index, row in enumerate(distractors):
        row["attributes"]["request_id"] = f"wrong-{index}"
    [row] = recoveries([park()], {}, [transfer()], distractors + [request()])
    assert row["following_request_id"] == "req"
    assert row["parked_ids_in_following_match_path"] == [1]
    [link] = row["subsequent_same_node_h2d"]
    assert link["component_forward_reuse"] == "unproven"
    assert link["creation_identity_in_native_receipt"] == "unrecorded"


def test_other_node_or_pool_does_not_become_a_same_node_recovery():
    [row] = recoveries([park()], {}, [
        transfer(command="other-node", nodes=[2]),
        transfer(command="other-pool", pools={"mamba": 1}),
    ], [request()])
    assert row["subsequent_same_node_h2d"] == []


def test_service_missing_or_no_timing_keeps_followup_unknown():
    [pending] = recoveries([park()], {}, [transfer()], [])
    assert pending["following_first_service_observed"] is False
    assert pending["subsequent_same_node_h2d"] is None
    seen = request()
    del seen["attributes"]["native_host_hit_match_path"]
    [without_path] = recoveries([park()], {}, [transfer()], [seen])
    assert without_path["following_first_service_observed"] is True
    assert without_path["subsequent_same_node_h2d"] is None
    assert summarize([pending, without_path])["counts"]["following_first_service_time_unknown"] == 2


def test_restore_must_start_after_demotion_and_before_service_observation():
    [row] = recoveries([park()], {}, [
        transfer(command="before", ts=9.),
        transfer(command="equal", ts=10.),
        transfer(command="valid", ts=39.),
        transfer(command="after", ts=41.),
    ], [request()])
    assert [link["transfer_command_id"] for link in row["subsequent_same_node_h2d"]] == ["valid"]


def test_batch_bytes_are_deduplicated_without_assigning_them_to_each_node():
    [row] = recoveries([park(), park(node=2)], {}, [
        transfer(nodes=[1, 2], pools={"kv": 10}),
    ], [request()])
    [link] = row["subsequent_same_node_h2d"]
    assert link["single_node_part"] is False
    assert link["matched_parked_node_ids"] == [1, 2]
    assert summarize([row])["associated_h2d_whole_batch_bytes"] == 50


def test_ordered_reload_and_repark_is_only_a_node_id_association():
    [row] = recoveries([park(), park(ts=30.)], {}, [transfer()], [request()])
    [cycle] = row["reload_then_repark_before_following_service"]
    assert cycle["identity_scope"] == "node_id_only"
    assert cycle["component_forward_reuse"] == "unproven"


def test_different_component_demotion_does_not_prove_a_reload_repark_cycle():
    [row] = recoveries(
        [park(), park(ts=30., freed_units={"2": 1})], {},
        [transfer()], [request()],
    )
    assert row["reload_then_repark_before_following_service"] == []


def test_request_queued_before_demotion_is_not_skipped_for_a_later_round():
    first = request(ts=9., observed=40.)
    later = request(ts=50., observed=60., context_epoch=2)
    later["attributes"]["request_id"] = "later"
    [row] = recoveries([park()], {}, [transfer()], [later, first])
    assert row["following_request_id"] == "req"
    assert row["following_native_queue_ts_ms"] == 9.


def test_later_epoch_already_served_before_demotion_remains_a_timing_finding():
    [row] = recoveries([park()], {}, [transfer()], [request(ts=5., observed=7.)])
    assert row["following_first_service_precedes_demotion"] is True
    assert row["demotion_to_first_service_observation_ms"] == -3.
    assert row["subsequent_same_node_h2d"] == []


def test_prepare_creation_is_not_promoted_to_demotion_generation():
    prepares = {
        "backup": {
            "context_id": "ctx", "context_epoch": 0,
            "node_id": 1, "node_creation_time": 6.,
        },
    }
    backup = transfer(
        command="write", ts=1., direction="d2h",
        tagged_child_commits=[{
            "command_id": "backup", "anchor_node_id": 1,
            "num_tokens_by_pool": {"kv": 5}, "num_bytes": 50,
        }],
    )
    [row] = recoveries([park()], prepares, [backup, transfer()], [request()])
    [receipt] = row["prior_same_node_prepare_acks"]
    assert receipt["anchor_creation_time"] == 6.
    assert receipt["service_path_creation_times"] == [7.]
    assert receipt["creation_identity_at_demotion"] == "unrecorded"


@pytest.mark.parametrize("kind", ["request", "transfer"])
def test_duplicate_identifiers_fail_instead_of_double_counting(kind):
    with pytest.raises(ValueError, match="duplicate"):
        recoveries(
            [park()], {}, [transfer(), transfer()] if kind == "transfer" else [],
            [request(), request()] if kind == "request" else [],
        )


def test_unordered_first_service_path_is_rejected():
    with pytest.raises(ValueError, match="unordered"):
        recoveries([park()], {}, [], [request(observed=19.)])


def test_live_prefix_ignores_incomplete_tail_and_later_appends(tmp_path):
    path = tmp_path / "live.jsonl"
    path.write_bytes(b'{"value":1}\n{"value":')
    sealed = SealedRecords()
    assert list(sealed(path)) == [{"value": 1}]
    with path.open("ab") as stream:
        stream.write(b'2}\n{"value":3}\n')
    assert list(sealed(path)) == [{"value": 1}]
    assert sealed.prefixes[str(path)]["complete_prefix_bytes"] == len(b'{"value":1}\n')


def test_changed_or_truncated_prefix_is_rejected(tmp_path):
    path = tmp_path / "live.jsonl"
    path.write_text(json.dumps({"value": 1}) + "\n")
    sealed = SealedRecords()
    assert list(sealed(path)) == [{"value": 1}]
    path.write_text(json.dumps({"value": 2}) + "\n")
    with pytest.raises(ValueError, match="changed sealed prefix"):
        list(sealed(path))
    path.write_text("")
    with pytest.raises(ValueError, match="short sealed prefix"):
        list(sealed(path))


def test_collecting_no_prefetch_arm_without_clock_census_is_auditable(tmp_path):
    files = {
        "opportunities/admission_opportunities.jsonl": [park(event="parent_pressure_demoted")],
        "server/transfer_telemetry.jsonl": [transfer()],
        "server/runtime_events.sglang.jsonl": [request()],
        "server/physical_action_ack.jsonl": [],
    }
    for name, rows in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "server/native_capacity_census.json").write_text(json.dumps({
        "capacity": {"pool_layout": "static_separate_full_mamba"},
    }))
    result = audit(tmp_path)
    assert result["summary"]["counts"]["with_subsequent_same_node_h2d"] == 1
    assert result["tool_prediction_timing"]["measured"] is False
    assert result["tool_prediction_timing"]["scheduler_clock_census_records"] == 0
    assert len(result["input_jsonl_prefixes"]) == 4


def test_missing_required_input_is_an_error_instead_of_zero_opportunities(tmp_path):
    with pytest.raises(FileNotFoundError):
        audit(tmp_path)
