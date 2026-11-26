#!/usr/bin/env python3
"""Bind a completed OpenCode capture to drained SGLang token telemetry."""

import argparse
import json
from pathlib import Path

from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.runtime.opencode_capture import compile_opencode_replay
from beliefkv.runtime.replay_token_capture import native_request_expectations


def read_closed_jsonl(path):
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                raise ValueError(f"incomplete JSONL record in {path}")
            yield json.loads(line)


def audit(capture_dir, native_dir):
    summary = json.loads((capture_dir / "opencode-summary.json").read_bytes())
    if summary["scenario"] is not None:
        raise ValueError("native audit requires a real provider capture")
    status = json.loads((native_dir / "native_telemetry_status.json").read_bytes())
    if (
        status.get("replay_token_capture") is not True or status.get("writer_error") is not None
        or any(status.get(key) != 0 for key in (
            "failed_records", "dropped_records", "writer_queue_depth", "pending_batch_count",
        ))
    ):
        raise ValueError("native token capture must be enabled and drained")
    events = tuple(
        RuntimeEvent.from_dict(row) for row in read_closed_jsonl(capture_dir / "opencode-events.jsonl")
    )
    workflow_ids = {event.workflow_id for event in events}
    native = tuple(
        row for row in read_closed_jsonl(native_dir / "runtime_events.sglang.jsonl")
        if row.get("workflow_id") in workflow_ids
    )
    if any(
        (row["attributes"].get("monotonic_clock_domain"), row["attributes"].get("completion_source"))
        != (summary["monotonic_clock_domain"], None)
        for row in native if row["kind"] == "llm_result"
    ):
        raise ValueError("native completion requires SGLang telemetry in the collector clock domain")
    plan = compile_opencode_replay(events, native)
    expected = native_request_expectations(native)
    with CapturedTokenCorpus(native_dir / "request_tokens.jsonl", expected) as corpus:
        return {"graph": plan.summary(), "tokens": corpus.evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--native", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.capture, args.native), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
