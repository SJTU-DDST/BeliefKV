import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).with_name("v3448_source_health_audit.py")
spec = importlib.util.spec_from_file_location("v3448_source_health_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def call(index, *, invocation="child", workflow="wf", tool="grep", start=None):
    return {
        "workflow": workflow, "invocation": invocation, "call_id": str(index),
        "tool": tool, "input_sha256": "a" * 64,
        "output_sha256": f"volatile-message-id-and-feedback-{index}",
        "status": "success", "start_ms": index * 10 if start is None else start,
        "end_ms": index * 10 + 1 if start is None else start + 1,
    }


def proof(index, **changes):
    return {
        "event": "agent_tool_repeated_result_feedback", "tool_call_id": str(index),
        "tool_name": "grep", "physical_execution": True, "suppression_enabled": False,
        "source_result_sha256": "b" * 64, "signature": "a" * 64,
        "identical_result_count": index + 1, **changes,
    }


def test_volatile_callback_hashes_do_not_hide_370_equal_physical_results():
    calls = [call(index) for index in range(370)]
    linked, unknown = audit.link_raw_results(calls, [proof(index) for index in range(1, 370)])
    assert not unknown
    assert len(max(audit.serial_runs(calls), key=len)) == 370
    assert len(max(audit.serial_runs(calls, linked), key=len)) == 369
    assert max(row["reported_identical_result_count"] for row in linked.values()) == 370
    assert ("wf", "child", "0") not in linked


@pytest.mark.parametrize("changes", (
    {"physical_execution": False}, {"suppression_enabled": True},
    {"tool_name": "execute"}, {"source_result_sha256": "bad"},
    {"signature": None}, {"identical_result_count": True},
))
def test_invalid_or_suppressed_raw_proofs_remain_unknown(changes):
    linked, unknown = audit.link_raw_results([call(1)], [proof(1, **changes)])
    assert linked == {}
    assert unknown == {"invalid_raw_result_evidence": 1}


def test_ambiguous_call_id_across_invocations_never_receives_a_raw_proof():
    linked, unknown = audit.link_raw_results(
        [call(1), call(1, invocation="other")], [proof(1)],
    )
    assert linked == {}
    assert unknown == {"missing_or_ambiguous_completed_call": 1}


def test_duplicate_raw_result_receipts_are_not_silently_overwritten():
    with pytest.raises(ValueError, match="duplicate"):
        audit.link_raw_results([call(1)], [proof(1), proof(1)])


@pytest.mark.parametrize("change", ("overlap", "other_input", "other_agent", "other_workflow", "other_tool", "raw_result", "status"))
def test_changed_observation_or_execution_scope_breaks_the_consecutive_run(change):
    calls = [call(index) for index in range(1, 4)]
    feedback = [proof(index) for index in range(1, 4)]
    if change == "overlap":
        calls[1]["start_ms"] = calls[0]["end_ms"] - .5
    elif change == "other_input":
        calls[1]["input_sha256"] = "c" * 64
    elif change == "other_agent":
        calls[1]["invocation"] = "different"
    elif change == "other_workflow":
        calls[1]["workflow"] = "different"
    elif change == "other_tool":
        calls[1]["tool"] = feedback[1]["tool_name"] = "execute"
    elif change == "raw_result":
        feedback[1]["source_result_sha256"] = "c" * 64
    else:
        calls[1]["status"] = "error"
    linked, _ = audit.link_raw_results(calls, feedback)
    assert max(map(len, audit.serial_runs(calls, linked)), default=0) < 3


def test_matching_call_id_in_another_workflow_file_does_not_leak_evidence():
    first, _ = audit.link_raw_results([call(1)], [proof(1)])
    other = [call(1, workflow="different")]
    assert audit.serial_runs(other, first) == []
