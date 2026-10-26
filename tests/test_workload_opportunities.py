from collections import Counter

import pytest

from beliefkv.core.events import EventConfidence, RuntimeEvent, RuntimeEventKind as Kind
from scripts.audit_workload_opportunities import analyze


def fixture():
    events = []

    def add(kind, ts, inv=None, **kwargs):
        events.append(RuntimeEvent(
            event_id=f"e{len(events)}", ts_ms=ts, kind=kind,
            workflow_id="w", invocation_id=inv, **kwargs,
        ))

    add(Kind.WORKFLOW_START, 0)
    add(Kind.INVOCATION_CREATE, 0, "root")
    add(Kind.INVOCATION_CREATE, 10, "child", parent_invocation_id="root")
    return events, add


def test_all_descendants_parallel_tool_union_and_other_work_are_distinct():
    events, add = fixture()
    add(Kind.TOOL_START, 20, "child", attributes={
        "tool_run_id": "a", "tool_name": "execute",
    })
    add(Kind.TOOL_START, 30, "child", attributes={
        "tool_run_id": "b", "tool_name": "execute",
    })
    add(Kind.TOOL_END, 120, "child", attributes={
        "tool_run_id": "a", "status": "success",
    })
    add(Kind.TOOL_END, 180, "child", attributes={
        "tool_run_id": "b", "status": "error",
    })
    add(Kind.LLM_SUBMIT, 100, "root", attributes={"request_id": "r"})
    add(Kind.LLM_RESULT, 200, "root", attributes={
        "request_id": "r", "tool_call_count": 2,
    })
    add(Kind.RETURN, 200, "child")
    add(Kind.WORKFLOW_END, 240)
    report = analyze(events)
    child = next(row for row in report["agents"] if row["invocation_id"] == "child")
    assert child["ordinary_tool_blocked_ms"] == 160
    assert child["tool_blocked_fraction_of_lifetime"] == pytest.approx(160 / 190)
    assert child["blocked_ms_with_other_submitted_model_work"] == 80
    assert report["ordinary_tool_count"] == 2
    assert report["ordinary_tool_duration_ms_by_name"]["execute"]["max"] == 150
    assert report["ordinary_tool_status_counts"] == {"success": 1, "error": 1}
    assert report["complete_event_pairing"]
    assert not report["scope"]["blocked_time_proves_exclusive_cold_kv_or_gpu_overlap"]


def test_join_uses_actual_wait_and_excludes_reconstructed_join():
    events, add = fixture()
    add(Kind.JOIN_CREATE, 10, "root", join_id="j", member_invocation_ids=("child",))
    add(Kind.JOIN_WAIT, 50, "root", join_id="j")
    add(Kind.JOIN_SATISFIED, 150, "root", join_id="j")
    add(Kind.JOIN_WAIT, 20, "root", join_id="inferred", confidence=EventConfidence.INFERRED)
    add(Kind.JOIN_SATISFIED, 190, "root", join_id="inferred",
        confidence=EventConfidence.INFERRED)
    add(Kind.LLM_SUBMIT, 70, "child", attributes={"request_id": "child-request"})
    add(Kind.LLM_RESULT, 130, "child", attributes={"request_id": "child-request"})
    add(Kind.RETURN, 150, "child")
    add(Kind.WORKFLOW_END, 200)
    report = analyze(events)
    root = next(row for row in report["agents"] if row["invocation_id"] == "root")
    assert root["observed_join_blocked_ms"] == 100
    assert root["blocked_ms_with_other_submitted_model_work"] == 60
    assert report["observed_join_member_histogram"] == {1: 1}
    assert report["inferred_join_events_excluded"] == 2
    assert report["complete_event_pairing"]


def test_metadata_unfinished_tools_and_same_agent_work_do_not_inflate_opportunity():
    events, add = fixture()
    add(Kind.TOOL_START, 20, "child", attributes={
        "tool_call_id": "meta", "tool_name": "write_todos",
    })
    add(Kind.TOOL_END, 100, "child", attributes={"tool_call_id": "meta"})
    add(Kind.TOOL_START, 30, "child", attributes={
        "tool_call_id": "incomplete", "tool_name": "execute",
    })
    add(Kind.TOOL_START, 40, "root", attributes={
        "tool_call_id": "tool", "tool_name": "execute",
    })
    add(Kind.TOOL_END, 90, "root", attributes={"tool_call_id": "tool"})
    add(Kind.LLM_SUBMIT, 50, "root", attributes={"request_id": "r"})
    add(Kind.LLM_RESULT, 80, "root", attributes={"request_id": "r"})
    add(Kind.RETURN, 110, "child")
    add(Kind.WORKFLOW_END, 120)
    report = analyze(events)
    assert not report["complete_event_pairing"]
    assert report["coverage"]["unfinished_tools"] == 1
    assert report["metadata_tool_count"] == 1
    assert report["ordinary_tool_count"] == 1
    assert report["ordinary_tool_duration_ms"]["mean"] == 50
    assert sum(row["blocked_ms_with_other_submitted_model_work"]
               for row in report["agents"]) == 0


def test_tool_bins_and_child_service_counts_preserve_long_tails():
    events, add = fixture()
    for index, duration in enumerate((1000, 1001, 10000, 10001, 60000, 60001)):
        start = index * 100000 + 100
        add(Kind.TOOL_START, start, "child", attributes={
            "tool_run_id": str(index), "tool_name": "execute",
        })
        add(Kind.TOOL_END, start + duration, "child", attributes={
            "tool_run_id": str(index),
        })
        add(Kind.LLM_SUBMIT, start + duration + 1, "child",
            attributes={"request_id": str(index)})
        add(Kind.LLM_RESULT, start + duration + 2, "child",
            attributes={"request_id": str(index)})
    add(Kind.RETURN, 700000, "child")
    add(Kind.WORKFLOW_END, 700001)
    report = analyze(events)
    assert report["ordinary_tool_duration_bins"] == {
        "le_1s": 1, "1_to_10s": 2, "10_to_60s": 2, "gt_60s": 1,
    }
    assert report["ordinary_tool_duration_ms"]["max"] == 60001
    assert report["child_model_calls"]["mean"] == 6
    assert not any(Counter(report["coverage"]).values())
