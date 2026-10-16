import hashlib
import json

import pytest

from scripts.audit_terminal_cache_lifetime import analyze, audit_arm, summarize_releases


def release(**changes):
    return {
        "event": "terminal_child_cache_reclaimed", "ts_ms": 1002.,
        "workflow_id": "wf", "invocation_id": "child", "context_id": "ctx",
        "context_epoch": 1,
        "session_id": "session", "session_generation": 1,
        "invocation_scope": "dynamic_subagent", "join_id": "join",
        "parent_context_id": "parent", "retry": False,
        "child_return_observed_wall_ts_ms": 1000.,
        "child_return_event_ts_ms": -100000.,
        "freed_device_units": {"0": 10}, "freed_host_units": {"2": 1},
        "bytes_per_unit": {"0": 20, "2": 100},
        "discarded_nodes": [[7, 20.]], "pending_anchors": 0, "reason": None,
        **changes,
    }


def sample(ts_ms, **changes):
    node = {
        "node_id": 7, "creation_time": 20.,
        "full_device_tokens": 10, "full_host_tokens": 20,
        "mamba_device_present": True, "mamba_host_present": False,
        "full_session_refs": 2, "mamba_session_refs": 0,
        "full_device_locks": 1, "full_host_locks": 0,
        "mamba_device_locks": 0, "mamba_host_locks": 0,
        "pending_write_id": 0, "pending_load_id": None,
    }
    return {
        "event": "terminal_context_cache_sample",
        "ts_ms": ts_ms, "workflow_id": "wf", "invocation_id": "child",
        "context_id": "ctx", "context_epoch": 1, "session_id": "session",
        "elapsed_since_terminal_ms": ts_ms - 1000.,
        "nodes": [node, dict(node)], **changes,
    }


def test_internal_requests_and_conflicting_evidence_do_not_count_as_dynamic_children():
    events = [
        release(),
        release(invocation_id="summary", context_id="internal", session_id="summary",
                invocation_scope="runtime_internal"),
        release(invocation_id="ambiguous", context_id="ambiguous", session_id="ambiguous"),
        release(invocation_id="unknown", context_id="unknown", session_id="unknown",
                invocation_scope=None),
    ]
    result = analyze(events, {("wf", "ambiguous"): "runtime_internal"})
    groups = result["release_by_invocation_scope"]
    assert set(groups) == {"dynamic_subagent", "runtime_internal", "conflicting", "unknown"}
    assert groups["dynamic_subagent"]["freed_device_bytes_known"] == {"0": 200}
    assert result["parent_handoff_observations"]["terminal_children"] == 1


def test_retry_bytes_accumulate_but_first_release_uses_only_scheduler_clock():
    initial = release(freed_device_units={}, freed_host_units={}, discarded_nodes=[],
                      pending_anchors=1)
    retry = release(ts_ms=1010., retry=True, pending_anchors=1)
    result = summarize_releases([retry, initial])
    assert result["contexts_with_observed_reclaim_record"] == 1
    assert result["reclaim_records"] == 2 and result["retry_records"] == 1
    assert result["first_positive_release_after_scheduler_terminal_ms"]["p50"] == 10.
    assert result["contexts_last_recorded_with_pending_anchors"] == 1
    assert result["freed_host_bytes_known"] == {"2": 100}


@pytest.mark.parametrize("observed", (None, 1003., float("nan")))
def test_missing_or_unordered_scheduler_clock_remains_unknown(observed):
    result = summarize_releases([release(child_return_observed_wall_ts_ms=observed)])
    assert result["first_positive_release_after_scheduler_terminal_ms"]["count"] == 0
    assert result["contexts_with_positive_release_but_unknown_delay"] == 1


def test_node_number_reuse_is_distinct_and_exact_duplicate_release_is_reported():
    result = summarize_releases([
        release(),
        release(ts_ms=1004., retry=True, discarded_nodes=[[7, 21.]]),
        release(ts_ms=1005., retry=True),
    ])
    assert result["discarded_node_identities"] == 2
    assert result["duplicate_discarded_node_identity_records"] == 1


def test_shared_ancestry_is_deduplicated_per_snapshot_and_kept_separate_from_release():
    result = analyze([sample(1020.), sample(1001., nodes=[])],
                     {("wf", "child"): "dynamic_subagent"})
    scope = result["retired_checkpoint_ancestry_by_invocation_scope"]["dynamic_subagent"]
    assert scope["first_observation"]["full_device_tokens"]["p50"] == 0
    last = scope["last_observation"]
    assert last["full_device_tokens"]["p50"] == 10
    assert last["nodes_with_multiple_session_refs"]["p50"] == 1
    assert last["nodes_with_locks"]["p50"] == 1
    assert last["nodes_with_pending_transfer"]["p50"] == 1
    assert "sum" not in last["full_device_tokens"]
    assert result["release_by_invocation_scope"] == {}


def test_epoch_retirement_does_not_imply_agent_return_and_checkpoint_matching_is_exact():
    result = analyze([
        release(child_return_observed_wall_ts_ms=1005., ts_ms=1006.),
        sample(1001.),
        sample(1010.),
        sample(1020., context_epoch=0),
        sample(1021., session_id="different-session"),
    ], {("wf", "child"): "dynamic_subagent"})
    all_snapshots = result["retired_checkpoint_ancestry_by_invocation_scope"]["dynamic_subagent"]
    matched = result[
        "observed_terminal_release_checkpoint_ancestry_by_invocation_scope"
    ]["dynamic_subagent"]
    assert all_snapshots["sampled_checkpoint_keys"] == 3
    assert all_snapshots["sampled_invocations"] == 1
    assert matched["sampled_checkpoint_keys"] == 1
    assert matched["sample_records"] == 2
    assert matched["records_after_scheduler_terminal_observation"] == 1
    assert matched["records_without_matching_terminal_release_checkpoint"] == 2


def test_active_file_uses_a_hashed_complete_line_prefix_and_never_changes_input(tmp_path):
    opportunities = tmp_path / "opportunities/admission_opportunities.jsonl"
    opportunities.parent.mkdir()
    complete = (json.dumps(release()) + "\n").encode()
    data = complete + b'{"event":'
    opportunities.write_bytes(data)
    result = audit_arm(tmp_path)
    binding = result["inputs"][0]
    assert binding["analyzed_bytes"] == len(complete)
    assert binding["sha256"] == hashlib.sha256(complete).hexdigest()
    assert binding["discarded_tail_bytes"] == len(data) - len(complete)
    assert opportunities.read_bytes() == data
    assert result["summary"]["release_by_invocation_scope"]["dynamic_subagent"]["reclaim_records"] == 1


def test_absent_runtime_instrumentation_is_unavailable_and_invalid_identity_is_rejected(tmp_path):
    assert audit_arm(tmp_path)["summary"] is None
    with pytest.raises(ValueError, match="complete context/session identity"):
        summarize_releases([release(session_generation=None)])
