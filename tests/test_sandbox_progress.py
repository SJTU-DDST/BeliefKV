from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from beliefkv.experiments.sandbox_progress import _ProgressFrames, observe_output
from scripts.audit_sandbox_output_timing import _first_silence_lead_ms, audit
from scripts.audit_sandbox_test_progress import summarize as summarize_test_progress


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
                    "is_child": True,
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
    assert report["by_origin_long_commands_matched"]["child"][
        "command_count"
    ] == 1


def test_stdout_timing_audit_rejects_mixed_output_modes(tmp_path):
    path = tmp_path / "astropy__one"
    path.mkdir()
    (path / "sandbox_audit.jsonl").write_text(
        "".join(json.dumps({
            "event": "sandbox_execute", "output_timing_shadow": True,
            "unbuffered_output_shadow": flag,
            "ts_ms": index * 1000, "duration_ms": 100,
            "execute_elapsed_ms": 100,
            "first_output_after_execute_ms": 50,
            "exit_code": 0,
        }) + "\n" for index, flag in enumerate((True, False))),
    )
    report = audit(tmp_path)
    assert report["output_modes"] == {
        "buffered": 1, "unbuffered": 1, "unknown": 0,
    }
    with pytest.raises(ValueError, match="expected all sandbox commands"):
        audit(tmp_path, expected_unbuffered=True)
    with pytest.raises(ValueError, match="expected all sandbox commands"):
        audit(tmp_path, expected_unbuffered=False)


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


def test_progress_frames_preserve_invalid_frames_and_split_output():
    frames = _ProgressFrames()
    first, events = frames.feed(b"before\x1eBKVP:{\"phase\":\"test_done\",")
    assert first == b"before"
    assert events == []
    second, events = frames.feed(b"\"completed\":1,\"total\":2}\x1fafter")
    assert second == b"after"[:max(0, len(b"after") - len(frames.PREFIX) + 1)]
    assert events == [("test_done", 1, 2)]
    tail, events = frames.feed(b"", final=True)
    assert first + second + tail == b"beforeafter"
    assert events == []
    bad, events = frames.feed(
        b"\x1eBKVP:{\"phase\":\"test_done\",\"completed\":5,\"total\":2}\x1f",
        final=True,
    )
    assert b"completed" in bad
    assert events == []


def test_opt_in_pytest_progress_arrives_before_command_end_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_file = tmp_path / "test_progress_example.py"
    test_file.write_text(
        "import time\n"
        "def test_one():\n    time.sleep(.03)\n"
        "def test_two():\n    time.sleep(.18)\n"
    )
    support = Path(__file__).resolve().parents[1] / (
        "beliefkv/experiments/sandbox_support"
    )
    monkeypatch.setenv("PYTHONPATH", str(support))
    monkeypatch.setenv("PYTEST_PLUGINS", "beliefkv_pytest_progress")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = observe_output(
        [sys.executable, "-m", "pytest", "-q", "-c", os.devnull, str(test_file)],
        timeout_s=20, test_progress_shadow=True,
    )
    assert result.exit_code == 0, result.output
    assert result.total_progress_events == 4
    assert [row[1:] for row in result.recent_progress_events] == [
        ("collection", 0, 2),
        ("test_done", 1, 2),
        ("test_done", 2, 2),
        ("session_finish", 2, 2),
    ]
    assert [row[1] for row in result.first_progress_stages] == [
        "collection", "all_tests_done",
    ]
    first_test_ms = result.recent_progress_events[1][0]
    assert result.elapsed_ms - first_test_ms >= 100
    assert "2 passed" in result.output
    assert "BKVP" not in result.output
    assert b"\x1e" not in result.output.encode()


def test_pytest_progress_audit_requires_observed_collection_and_nonterminal_lead():
    report = summarize_test_progress([{
        "execute_elapsed_ms": 3000,
        "exit_code": 0,
        "total_test_progress_events": 4,
        "recent_test_progress_events": (
            (200, "collection", 0, 10),
            (1000, "test_done", 9, 10),
            (2880, "test_done", 10, 10),
            (2900, "session_finish", 10, 10),
        ),
    }])
    assert report["stages"]["ninety_percent_before_last"][
        "lead_500_to_3000ms"
    ] == 1
    assert report["stages"]["all_tests_done"]["lead_at_least_500ms"] == 0
    assert report["truncated_progress_count"] == 0


def test_pytest_progress_audit_allows_bounded_tail_but_not_missing_first_crossing():
    report = summarize_test_progress([{
        "execute_elapsed_ms": 3000,
        "exit_code": 0,
        "total_test_progress_events": 350,
        "recent_test_progress_events": (
            (1000, "test_done", 300, 320),
            (2000, "test_done", 320, 320),
        ),
    }])
    assert report["truncated_progress_count"] == 1
    assert report["stages"]["ninety_percent_before_last"]["trigger_count"] == 0
    assert report["stages"]["all_tests_done"]["trigger_count"] == 1
    with_stage = summarize_test_progress([{
        "execute_elapsed_ms": 3000,
        "exit_code": 0,
        "total_test_progress_events": 350,
        "recent_test_progress_events": (
            (1000, "test_done", 300, 320),
            (2000, "test_done", 320, 320),
        ),
        "first_test_progress_stages": (
            (100, "collection", 0, 320),
            (800, "ninety_percent_before_last", 288, 320),
            (2000, "all_tests_done", 320, 320),
        ),
    }])
    assert with_stage["stages"]["ninety_percent_before_last"][
        "lead_500_to_3000ms"
    ] == 1
