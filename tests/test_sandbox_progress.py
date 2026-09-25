from __future__ import annotations

import json
import sys

import pytest

from beliefkv.experiments.sandbox_progress import observe_output
from scripts.audit_sandbox_output_timing import _first_silence_lead_ms, audit


def test_output_timing_observes_first_and_later_bytes_without_body_in_metadata():
    result = observe_output([
        sys.executable, "-c",
        "import sys,time; sys.stdout.write('first'); sys.stdout.flush(); "
        "time.sleep(.06); sys.stdout.write('last')",
    ], timeout_s=2)
    assert result.output == "firstlast"
    assert result.exit_code == 0
    assert result.observed_bytes == len(b"firstlast")
    assert result.first_output_ms is not None
    assert result.last_output_ms is not None
    assert result.first_output_ms < result.last_output_ms <= result.elapsed_ms
    assert sum(size for _, size in result.recent_output_chunks) == 9
    assert result.total_output_chunks == len(result.recent_output_chunks) == 2
    assert result.timed_out is False


def test_output_timing_handles_empty_output_and_host_timeout():
    empty = observe_output([sys.executable, "-c", "pass"], timeout_s=2)
    assert empty.output == ""
    assert empty.first_output_ms is None
    assert empty.last_output_ms is None
    assert empty.observed_bytes == 0
    assert empty.recent_output_chunks == ()
    assert empty.total_output_chunks == 0
    timed = observe_output([
        sys.executable, "-c",
        "import sys,time; sys.stdout.write('partial'); sys.stdout.flush(); "
        "time.sleep(5)",
    ], timeout_s=.1)
    assert timed.exit_code == 124
    assert timed.timed_out
    assert timed.output.startswith("partial\nCommand exceeded host timeout")
    assert timed.first_output_ms is not None
    assert timed.elapsed_ms < 2000


def test_output_timing_rejects_nonpositive_timeout():
    with pytest.raises(ValueError, match="timeout"):
        observe_output(["true"], timeout_s=0)


def test_stdout_timing_audit_separates_projects_and_long_commands(tmp_path):
    for project, elapsed, first in (
        ("pydata", 4000, 1000),
        ("django", 3000, None),
        ("pydata", 500, 300),
    ):
        path = tmp_path / f"{project}__task" / "sandbox_audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.write(json.dumps({
                "event": "sandbox_execute",
                "output_timing_shadow": True,
                "ts_ms": elapsed + 10,
                "duration_ms": elapsed,
                "execute_elapsed_ms": elapsed,
                "first_output_after_execute_ms": first,
                "exit_code": 0,
            }) + "\n")
    report = audit(tmp_path)
    assert report["long_commands_at_least_2s"]["command_count"] == 2
    assert report["long_commands_at_least_2s"][
        "first_output_at_least_2000ms_before_exit"
    ] == 1
    assert report["by_project_long_commands"]["django"]["no_output_count"] == 1
    assert report["matched_tool_end_count"] == 0


def test_stdout_timing_audit_matches_one_causal_tool_end(tmp_path):
    path = tmp_path / "pytest-dev__one"
    path.mkdir(parents=True)
    (path / "sandbox_audit.jsonl").write_text(
        json.dumps({
            "event": "sandbox_execute", "output_timing_shadow": True,
            "ts_ms": 2990, "duration_ms": 2980, "execute_elapsed_ms": 2980,
            "first_output_after_execute_ms": 1000, "exit_code": 0,
        }) + "\n",
    )
    (path / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in (
            {
                "ts_ms": 0, "kind": "tool_start",
                "attributes": {
                    "tool_call_id": "one", "tool_name": "execute",
                    "observed_command_shape": "test_suite",
                },
            },
            {
                "ts_ms": 3000, "kind": "tool_end",
                "attributes": {
                    "tool_call_id": "one", "tool_name": "execute",
                    "status": "success",
                },
            },
        )),
    )
    report = audit(tmp_path)
    assert report["matched_tool_end_count"] == 1
    assert report["by_shape_long_commands_matched"]["test_suite"][
        "first_output_at_least_500ms_before_exit"
    ] == 1


def test_silence_signal_uses_only_chunks_seen_before_trigger():
    row = {
        "execute_elapsed_ms": 3000,
        "recent_output_chunks": ((100, 1), (300, 2), (1300, 3)),
        "total_output_chunks": 3,
    }
    assert _first_silence_lead_ms(row) == 3000 - 550
    row["recent_output_chunks"] = ((100, 1), (200, 2), (300, 3))
    assert _first_silence_lead_ms(row) == 3000 - 550
    row["total_output_chunks"] = 4
    assert _first_silence_lead_ms(row) is None
    row["total_output_chunks"] = 3
    row["execute_elapsed_ms"] = 450
    assert _first_silence_lead_ms(row) is None
