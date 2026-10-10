#!/usr/bin/env python3
"""Audit token truncation of the last delivered child semantic text windows."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
import time

from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.predictor.child_semantic_work import encoder_snapshot
from scripts.summarize_semantic_h2d_ab import records


def summary(rows: list[dict]) -> dict:
    dropped = [row["dropped_tokens"] for row in rows if row["dropped_tokens"] > 0]
    return {
        "request_count": len(rows),
        "truncated_window_count": len(dropped),
        "truncated_fraction": len(dropped) / len(rows) if rows else None,
        "dropped_tokens_p50": median(dropped) if dropped else None,
        "dropped_tokens_max": max(dropped) if dropped else None,
        "at_least_16_dropped_tokens_count": sum(value >= 16 for value in dropped),
    }


def audit(arm: Path, artifact_path: Path) -> dict:
    started = time.time() * 1000.
    artifact = json.loads(artifact_path.read_text())
    snapshot = encoder_snapshot(artifact, artifact_path)
    max_tokens = artifact["metadata"]["plan"]["encoder"]["max_tokens"]
    tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
    paths = sorted((
        *arm.glob("client_*/workflows/*/child_stream_content.jsonl"),
        *arm.glob("workloads/workflows/*/child_stream_content.jsonl"),
    ))
    source_stats = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in paths
    }
    windows, results = {}, {}
    for path in paths:
        for row in records(path):
            rid = row.get("request_id")
            if row.get("event") == "child_stream_result" and rid:
                results[rid] = row
            if (
                row.get("event") != "child_stream_content"
                or row.get("tool_chunk") or row.get("finish_reason") or not rid
            ):
                continue
            text = row.get("semantic_content_tail")
            if not isinstance(text, str) or not text:
                continue
            previous = windows.get(rid)
            if previous is None or row["ts_ms"] >= previous["ts_ms"]:
                windows[rid] = {
                    "request_id": rid, "task": path.parent.name,
                    "invocation_id": row.get("invocation_id"),
                    "context_id": row.get("context_id"),
                    "context_epoch": row.get("context_epoch"),
                    "ts_ms": row["ts_ms"], "text": text[-1024:],
                }
    rows = []
    values = list(windows.values())
    for offset in range(0, len(values), 128):
        batch = values[offset:offset + 128]
        texts = [row["text"] for row in batch]
        original = tokenizer(
            texts, truncation=False, add_special_tokens=True,
        )["input_ids"]
        retained = tokenizer(
            texts, truncation=True, max_length=max_tokens, add_special_tokens=True,
        )["input_ids"]
        for row, full, kept in zip(batch, original, retained):
            result = results.get(row["request_id"], {})
            no_tool_stop = (
                result.get("finish_reason") == "stop"
                and result.get("tool_call_count") == 0
                and result.get("invalid_tool_call_count", 0) == 0
                and result.get("output_chars", 0) > 0
            )
            rows.append({
                **{key: value for key, value in row.items() if key != "text"},
                "encoder_tokens_before_truncation": len(full),
                "encoder_tokens_retained": len(kept),
                "dropped_tokens": len(full) - len(kept),
                "no_tool_stop_round": no_tool_stop,
                "client_finish_reason": result.get("finish_reason"),
            })
    stable = all(
        (path.stat().st_size, path.stat().st_mtime_ns) == source_stats[str(path)]
        for path in paths
    )
    return {
        "schema_version": 1,
        "scope": (
            "Development input audit; last delivered client text window before "
            "client finish. No-tool stop is an observed output-round property, "
            "not a natural RETURN or native pre-EOS timing label. Encoder tokens "
            "are not Qwen decode/KV tokens. No prediction or throughput claim."
        ),
        "audit_started_ts_ms": started,
        "audit_finished_ts_ms": time.time() * 1000.,
        "artifact_path": str(artifact_path),
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "encoder_snapshot": str(snapshot),
        "max_tokens": max_tokens,
        "tokenizer_truncation_side": tokenizer.truncation_side,
        "dropped_content_location": (
            "newest suffix" if tokenizer.truncation_side == "right" else "oldest prefix"
        ),
        "input_policy": "nonempty semantic_content_tail[-1024:]; no tool/finish chunks",
        "source_file_count": len(paths),
        "source_files_stable_during_audit": stable,
        "source_signature_sha256": hashlib.sha256(
            json.dumps(source_stats, sort_keys=True).encode()
        ).hexdigest(),
        "all_last_windows": summary(rows),
        "no_tool_stop_last_windows": summary([
            row for row in rows if row["no_tool_stop_round"]
        ]),
        "finish_round_counts": dict(Counter(
            row["client_finish_reason"] for row in rows
        )),
        "truncated_rows": [row for row in rows if row["dropped_tokens"] > 0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.arm.resolve(), args.artifact.resolve())
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        key: report[key] for key in (
            "source_files_stable_during_audit", "tokenizer_truncation_side",
            "all_last_windows", "no_tool_stop_last_windows",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
