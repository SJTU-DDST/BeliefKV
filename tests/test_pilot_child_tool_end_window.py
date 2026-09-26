import json

import numpy as np

from scripts.pilot_child_tool_end_window import report, samples, select_rule


def test_tool_end_waits_for_all_parallel_tools_and_censors_cancel(tmp_path):
    root = tmp_path / "workflows" / "django__task"
    root.mkdir(parents=True)
    events = []

    def add(kind, ts, child, **attributes):
        events.append({
            "kind": kind, "ts_ms": ts, "sequence": len(events),
            "invocation_id": child, "attributes": attributes,
        })

    add("spawn", 100., "root", target_invocation_id="ignored")
    events[-1]["target_invocation_id"] = "deepagents-invocation:final"
    for child in ("other", "cancelled", "outoforder"):
        add("spawn", 110., "root")
        events[-1]["target_invocation_id"] = f"deepagents-invocation:{child}"
    final = "deepagents-invocation:final"
    add("tool_start", 200., final, tool_call_id="call-1",
        observed_command_shape="python_inline")
    add("tool_start", 220., final, tool_call_id="call-2",
        observed_command_shape="test_suite")
    add("tool_end", 300., final, tool_call_id="call-1",
        tool_name="execute", status="success")
    add("tool_end", 350., final, tool_call_id="call-2",
        tool_name="execute", status="success", output_chars=100)
    add("llm_result", 1400., final, request_id="final-request",
        runtime_internal=False, tool_call_count=0, finish_reason="stop",
        output_chars=32)
    add("return", 1500., final)

    other = "deepagents-invocation:other"
    add("tool_start", 400., other, tool_call_id="call-3",
        observed_command_shape="python_inline")
    add("tool_end", 450., other, tool_call_id="call-3",
        tool_name="execute", status="success")
    add("llm_result", 1000., other, request_id="continuing",
        runtime_internal=False, tool_call_count=1, finish_reason="tool_calls",
        output_chars=0)

    cancelled = "deepagents-invocation:cancelled"
    add("tool_start", 480., cancelled, tool_call_id="call-4",
        observed_command_shape="python_inline")
    add("tool_end", 510., cancelled, tool_call_id="call-4",
        tool_name="execute", status="success")
    add("invocation_cancel", 600., cancelled)

    outoforder = "deepagents-invocation:outoforder"
    add("tool_start", 480., outoforder, tool_call_id="call-5",
        observed_command_shape="python_inline")
    add("tool_end", 530., outoforder, tool_call_id="call-5",
        tool_name="execute", status="success")
    add("tool_start", 540., outoforder, tool_call_id="call-6",
        observed_command_shape="python_inline")
    add("llm_result", 1100., outoforder, request_id="unpaired",
        runtime_internal=False, tool_call_count=1, finish_reason="tool_calls",
        output_chars=0)

    (root / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    rows = samples(tmp_path / "workflows")
    assert len(rows) == 2
    positive = next(row for row in rows if row["true_return"])
    assert positive["shape"] == "test_suite"
    assert positive["lead_ms"] == 1150.
    assert positive["ordinal"] == 2
    assert rows[1]["true_return"] is False
    result = report(rows, np.asarray([0.8, 0.2]), 0.5)
    assert (result["selected"], result["correct"], result["not_next_return"]) == (
        1, 1, 0,
    )
    assert select_rule(rows, np.asarray([0.8, 0.2])) is None
