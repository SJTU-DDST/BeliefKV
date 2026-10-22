#!/usr/bin/env python3
"""Qualify historical causal dependencies without claiming a GPU replay."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.causal_replay import (
    CausalReplayError, compile_causal_replay, evaluate_causal_plan,
)
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_replay_token_capture import audit as audit_tokens
from scripts.audit_sealed_wait_prefetch import SealedRecords


def audit(arm: Path) -> dict:
    sealed = SealedRecords()
    summaries = list(arm.glob("client_*/summary.json"))
    if len(summaries) != 1:
        raise CausalReplayError("one completed client summary is required")
    summary = json.loads(summaries[0].read_bytes())
    paths = sorted(summaries[0].parent.glob("workflows/*/runtime_events.deepagents.jsonl"))
    if len(paths) != summary.get("completed_workflows") or summary.get("workflow_count") != len(paths):
        raise CausalReplayError("client trace population differs from the completed summary")
    result = {
        "arm": str(arm.resolve()),
        "causal_graph_compiled": False, "gpu_replay_qualified": False,
        "performance_accepted": False,
    }
    events = [
        RuntimeEvent.from_dict(row) for path in paths for row in sealed(path)
    ]
    native = tuple(sealed(arm / "server/runtime_events.sglang.jsonl"))
    try:
        plan = compile_causal_replay(events, native)
        result.update(plan.summary())
        # Removing all recorded model time must leave tools and delivery delays.
        fixture = evaluate_causal_plan(plan, dict.fromkeys(plan.request_usage, 0.))
        result["zero_model_service_fixture"] = {
            "completed_workflows": len(fixture["workflow_elapsed_ms"]),
            "scope": (
                "CPU reachability fixture, not an oracle or JCT bound; virtual "
                "requests consume no service or capacity"
            ),
        }
    except CausalReplayError as error:
        result.update(reason="invalid_causal_dependencies", error=str(error))
    token_evidence = audit_tokens(arm)
    result["token_evidence_complete"] = token_evidence["token_evidence_complete"]
    result["token_evidence_reason"] = token_evidence.get("reason")
    result["token_input_jsonl_prefixes"] = token_evidence["input_jsonl_prefixes"]
    result["input_jsonl_prefixes"] = sealed.prefixes
    result["client_summary_sha256"] = sha256_file(summaries[0])
    result["analysis_source_sha256"] = {
        str(path.relative_to(ROOT)): sha256_file(path) for path in (
            Path(__file__), ROOT / "beliefkv/experiments/causal_replay.py",
            ROOT / "beliefkv/runtime/replay_token_capture.py",
        )
    }
    result["observed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    result["evidence_limit"] = (
        "Complete independently sealed prefixes, not an atomic cross-file snapshot. "
        "Graph and usage validation do not qualify native EOS, exact token "
        "execution, session lifecycle, cache hits, or real replay scheduling."
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-graph", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous evidence; choose a new report")
    try:
        result = audit(args.arm.resolve())
    except (CausalReplayError, KeyError, TypeError, ValueError) as error:
        result = {
            "arm": str(args.arm.resolve()), "causal_graph_compiled": False,
            "token_evidence_complete": False, "gpu_replay_qualified": False,
            "performance_accepted": False, "reason": "invalid_causal_source",
            "error": str(error),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result.get(key) for key in (
            "causal_graph_compiled", "requests", "workflows", "event_counts",
            "token_evidence_complete", "reason", "error", "gpu_replay_qualified",
        )
    }, indent=2))
    if args.require_graph and not result["causal_graph_compiled"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
