from copy import deepcopy

import pytest

from scripts.resume_v78_after_complete_v77_20261016 import collection_evidence


def fixtures():
    previous = {
        "status": "stopped_for_diagnosis",
        "error": "ValueError('native-close collection/startup failed: exit=1')",
        "startup": {"path": "verified-startup.json", "sha256": "frozen"},
    }
    trace = {
        "workflow_lifecycle_valid": True, "llm_pairing_valid": True, "tool_pairing_valid": True,
    }
    summary = {
        "workflow_count": 2, "completed_workflows": 1,
        "native_agent_jct_eligible_workflows": 1,
        "workflows": [
            {"instance_id": "a", "outcome": "completed", "native_agent_jct_eligible": True,
             "error": None, "trace": deepcopy(trace)},
            {"instance_id": "b", "outcome": "incomplete", "native_agent_jct_eligible": False,
             "error": None, "trace": deepcopy(trace)},
        ],
    }
    telemetry = {
        "writer_error": None, "dropped_records": 0, "failed_records": 0,
        "writer_queue_depth": 0, "pending_batch_count": 0, "pending_request_count": 0,
    }
    return previous, summary, telemetry, ["a", "b"], ["b", "a"]


def test_complete_collection_does_not_pass_or_relabel_the_native_gate():
    args = fixtures()
    before = deepcopy(args)
    result = collection_evidence(*args)
    assert result["status"] == "complete_collection_with_failed_native_gate"
    assert result["native_gate_passed"] is False
    assert result["original_driver_exit_code"] == 1
    assert result["outcomes"] == {"completed": 1, "incomplete": 1}
    assert args == before


def test_all_completed_still_preserves_ineligible_guard_outcomes():
    args = fixtures()
    args[1]["workflows"][1]["outcome"] = "completed"
    args[1]["completed_workflows"] = 2
    result = collection_evidence(*args)
    assert result["outcomes"] == {"completed": 2}
    assert result["native_agent_jct_eligible_workflows"] == 1
    assert result["native_gate_passed"] is False


@pytest.mark.parametrize("kind", [
    "wrong_failure", "startup_missing", "missing_summary_task", "duplicate_finish",
    "missing_finish", "wrong_task", "task_error", "unpaired_llm", "unknown_outcome",
    "wrong_completed_count", "wrong_gate_count", "unexplained_exit",
    "writer_error", "dropped", "pending", "missing_telemetry",
])
def test_incomplete_collection_or_unexplained_failure_cannot_be_recovered(kind):
    previous, summary, telemetry, expected, finished = fixtures()
    if kind == "wrong_failure":
        previous["error"] = "server failed"
    elif kind == "startup_missing":
        previous.pop("startup")
    elif kind == "missing_summary_task":
        summary["workflows"].pop()
    elif kind == "duplicate_finish":
        finished[:] = ["a", "a"]
    elif kind == "missing_finish":
        finished.pop()
    elif kind == "wrong_task":
        summary["workflows"][1]["instance_id"] = "c"
    elif kind == "task_error":
        summary["workflows"][1]["error"] = "server lost"
    elif kind == "unpaired_llm":
        summary["workflows"][1]["trace"]["llm_pairing_valid"] = False
    elif kind == "unknown_outcome":
        summary["workflows"][1]["outcome"] = "error"
    elif kind == "wrong_completed_count":
        summary["completed_workflows"] = 2
    elif kind == "wrong_gate_count":
        summary["native_agent_jct_eligible_workflows"] = 0
    elif kind == "unexplained_exit":
        summary["workflows"][1]["native_agent_jct_eligible"] = True
        summary["native_agent_jct_eligible_workflows"] = 2
    elif kind == "writer_error":
        telemetry["writer_error"] = "disk full"
    elif kind == "dropped":
        telemetry["dropped_records"] = 1
    elif kind == "pending":
        telemetry["pending_batch_count"] = 1
    else:
        telemetry.pop("writer_error")
    with pytest.raises(ValueError):
        collection_evidence(previous, summary, telemetry, expected, finished)
