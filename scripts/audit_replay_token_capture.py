#!/usr/bin/env python3
"""Check replay token fidelity without reconstructing missing token identities."""

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

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.replay_token_capture import (
    native_request_expectations, verify_capture,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords


def audit(arm: Path) -> dict:
    server = arm / "server"
    capture = server / "request_tokens.jsonl"
    sealed = SealedRecords()
    expected = native_request_expectations(sealed(server / "runtime_events.sglang.jsonl"))
    status_path = server / "native_telemetry_status.json"
    status = json.loads(status_path.read_bytes()) if status_path.exists() else {}
    result = {
        "token_evidence_complete": False,
        "dependency_replay_qualified": False, "gpu_replay_qualified": False,
        "performance_accepted": False,
        "native_requests": len(expected),
        "native_prompt_tokens": sum(row["llm_submit"][1] for row in expected.values()),
        "native_output_tokens": sum(row["llm_result"][1] for row in expected.values()),
        "recording_enabled": status.get("replay_token_capture"),
        "capture_exists": capture.is_file(),
    }
    if not capture.is_file():
        result["reason"] = "exact_prompt_and_output_capture_unavailable"
    elif status.get("replay_token_capture") is not True:
        result["reason"] = "capture_not_enabled_in_native_status"
    elif status.get("writer_error") is not None or status.get("failed_records") != 0:
        result["reason"] = "native_writer_failed_or_unknown"
    else:
        try:
            result.update(verify_capture(sealed(capture), expected))
        except (TypeError, ValueError) as error:
            result.update(reason="invalid_token_capture", error=str(error))
    return {
        **result, "arm": str(arm.resolve()),
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "input_jsonl_prefixes": sealed.prefixes,
        "native_status_sha256": sha256_file(status_path) if status_path.is_file() else None,
        "analysis_source_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in (
                Path(__file__), ROOT / "beliefkv/runtime/replay_token_capture.py",
            )
        },
        "evidence_limit": (
            "Independent complete JSONL prefixes, not an atomic snapshot. "
            "Missing prompts, child transcripts or EOS information are not inferred "
            "from lengths, hashes or a root's final trajectory."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve prior evidence; choose a new output")
    result = audit(args.arm.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "token_evidence_complete", "gpu_replay_qualified", "native_requests",
            "native_prompt_tokens", "native_output_tokens",
        )
    }, indent=2))
    if args.require_complete and not result["token_evidence_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
