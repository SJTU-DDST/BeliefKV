#!/usr/bin/env python3
"""Reproduce baseline completion-stage trace loss without a GPU or model."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import types
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "beliefkv/runtime/deepagents_adapter.py"
TEST = "tests/test_deepagents_adapter.py"
sys.path.insert(0, str(ROOT))
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter


def run_case(implementation, delivery, helpers):
    test = helpers["test_completion_stage_tool_ack_binds_child_without_report_payload"]
    scope = test.__globals__
    original_adapter, original_sink = scope["DeepAgentsRuntimeAdapter"], scope["CollectingSink"]
    sinks = []

    class ObservedSink(original_sink):
        def __init__(self):
            super().__init__()
            sinks.append(self)

    scope["DeepAgentsRuntimeAdapter"], scope["CollectingSink"] = implementation, ObservedSink
    failure = None
    try:
        test(delivery)
    except AssertionError:
        failure = "AssertionError"
    finally:
        scope["DeepAgentsRuntimeAdapter"], scope["CollectingSink"] = original_adapter, original_sink
    assert len(sinks) == 2
    stages = [
        [event for event in sink.events
         if event.attributes.get("child_completion_signal_kind") == "stage"]
        for sink in sinks
    ]
    return {
        "test_failure": failure,
        "trace_stage_events": len(stages[0]),
        "control_stage_events": len(stages[1]),
        "recorded_estimates": [event.attributes["estimated_final_report_tokens"] for event in stages[0]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="02fd87c764d8f66997e02e5fe517e36874d770ce")
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old_source = subprocess.check_output(["git", "show", f"{args.baseline}:{SOURCE}"], cwd=ROOT)
    old_module = types.ModuleType("beliefkv.runtime._completion_trace_baseline")
    sys.modules[old_module.__name__] = old_module
    exec(compile(old_source, f"{args.baseline}:{SOURCE}", "exec"), old_module.__dict__)
    helpers = runpy.run_path(str(ROOT / TEST))
    cases = {
        delivery: {
            "baseline": run_case(old_module.DeepAgentsRuntimeAdapter, delivery, helpers),
            "current": run_case(DeepAgentsRuntimeAdapter, delivery, helpers),
        }
        for delivery in ("queued", "trace_only", "same_sink", "direct")
    }
    for delivery, arms in cases.items():
        assert arms["baseline"]["trace_stage_events"] == int(delivery == "queued")
        assert arms["baseline"]["test_failure"] == (None if delivery == "queued" else "AssertionError")
        assert arms["current"]["test_failure"] is None
        assert arms["current"]["trace_stage_events"] == 1
        assert arms["current"]["recorded_estimates"] == [256]
        assert arms["current"]["control_stage_events"] == int(delivery == "queued")
    assert cases["queued"]["baseline"] == cases["queued"]["current"]
    suites = ET.parse(args.junit).getroot()
    assert all(int(suite.get("failures", 0)) == int(suite.get("errors", 0)) == 0 for suite in suites)
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "scope": "CPU identity/trace behavior only; no GPU/JCT benefit inferred.",
        "baseline_commit": subprocess.check_output(
            ["git", "rev-parse", args.baseline], cwd=ROOT, text=True,
        ).strip(),
        "baseline_source_sha256": hashlib.sha256(old_source).hexdigest(),
        "source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "test_sha256": hashlib.sha256((ROOT / TEST).read_bytes()).hexdigest(),
        "helper_path": str(Path(__file__).relative_to(ROOT)),
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "junit_sha256": hashlib.sha256(args.junit.read_bytes()).hexdigest(),
        "test_suites": [dict(suite.attrib) for suite in suites],
        "cases": cases,
        "safety_scope": (
            "One local valid-stage trace event for a successful bound child announcement. "
            "Only the existing queued sink receives asynchronous control; direct/trace-only "
            "modes gain no scheduling authorization. No agent prompt, completion guard, "
            "token budget, model invocation or high-frequency semantic telemetry is changed."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
