#!/usr/bin/env python3
"""Join sampled short-prefill phases to exact native request Host counters."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_prefill_turnover import read_sealed, summarize
from scripts.audit_sealed_wait_prefetch import SealedRecords


def restore_phase_groups(samples, events):
    counters = {}
    for event in events:
        if event.get("kind") != "llm_submit":
            continue
        attrs = event.get("attributes") or {}
        request_id = attrs.get("request_id")
        if type(request_id) is not str or not request_id or request_id in counters:
            raise ValueError("missing or duplicate native request identity")
        full, state = attrs.get("cached_tokens_host"), attrs.get("mamba_host_hit_slots")
        for value in (full, state):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("invalid Host counter")
        counters[request_id] = full, state
    groups, joined = defaultdict(list), []
    for sample in samples:
        if sample["batch_size"] != 1 or not 0 <= sample["extend_tokens"] < 512:
            continue
        request_ids = sample.get("request_ids")
        if not isinstance(request_ids, (list, tuple)) or len(request_ids) != 1:
            raise ValueError("short-prefill identity disagrees with batch size")
        identity = request_ids[0]
        full, state = counters.get(identity, (None, None))
        if full is not None and full > 0:
            status = "positive_full_host"
        elif state is not None and state > 0:
            status = "positive_mamba_host_without_positive_full"
        elif full == 0 and state == 0:
            status = "known_zero_host_counters"
        elif full == 0:
            status = "zero_full_host_mamba_unknown"
        else:
            status = "host_counters_unobserved"
        groups[status].append(sample)
        joined.append({
            "request_id": identity,
            "forward_iter": sample["forward_iter"],
            "status": status,
            "reported_host_full_tokens": full,
            "reported_host_mamba_slots": state,
            "extend_tokens": sample["extend_tokens"],
            "prefix_tokens": sample["prefix_tokens"],
            "selection": sample["phases"].get("selection"),
        })
    return {
        "short_prefill_samples": len(joined),
        "by_host_counter": {
            status: summarize(rows) for status, rows in sorted(groups.items())
        },
        "joined_rows": joined,
        "semantics": (
            "Exact request-ID join, not time-nearest association. Positive FULL/Mamba "
            "counters describe request accounting, not unique physical transfer bytes. "
            "Zero FULL with unknown Mamba is not classified as no restoration. "
            "Phase means across different requests are associations, not causal costs."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pressure-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; choose a fresh report ID")
    raw = args.pressure_report.read_bytes()
    basis = json.loads(raw)
    arms = {}
    for name, arm in basis["arms"].items():
        begin, end = arm["window_ts_ms"]
        phases = read_sealed({
            **arm["geometry"],
            "monitor_window": {"begin_ts_ms": begin, "end_ts_ms": end},
        })
        sealed = SealedRecords()
        groups = restore_phase_groups(
            phases["samples"],
            sealed(Path(arm["run"]) / "server/runtime_events.sglang.jsonl"),
        )
        arms[name] = {
            "phases": phases, "request_restore_association": groups,
            "request_event_prefixes": sealed.prefixes,
        }
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "pressure_source": {
            "path": str(args.pressure_report), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "arms": arms,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/analyze_prefill_turnover.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "semantics": [
            "One-in-16 sampled prefills, paired with exact native forward/request geometry and the recorded service-completion window.",
            "Geometry source bytes are rehashed against the pressure receipt. Native events use a stat-bounded complete-line prefix; cross-file reads are not atomic.",
            "Thread CPU, wall, CUDA-stream spans and processed worker intervals overlap and are not additive or removable JCT.",
            "Missing and ambiguous identities remain explicit. Samples are not extrapolated to unsampled forwards.",
            "Request counter associations and historical realized trajectories do not prove why cache restoration increased, mechanism causality or paper novelty.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {
            name: {
                status: {
                    "sample_count": group["sample_count"],
                    "selection_thread_cpu_ms": group["phases"].get(
                        "selection", {},
                    ).get("thread_cpu_ms"),
                    "run_batch_thread_cpu_ms": group["phases"].get(
                        "run_batch", {},
                    ).get("thread_cpu_ms"),
                }
                for status, group in result["request_restore_association"]["by_host_counter"].items()
            }
            for name, result in arms.items()
        },
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
