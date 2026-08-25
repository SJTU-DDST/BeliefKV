#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.oracle.contracts import FrozenDemandProvenance
from beliefkv.oracle.exporter import OracleTruthExporter


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export FrozenAgentDemand v2 and its schedule-neutral KV sidecar."
    )
    parser.add_argument("--runtime-events", type=Path, required=True)
    parser.add_argument("--runtime-audit", type=Path, required=True)
    parser.add_argument("--request-token-trace", type=Path, required=True)
    parser.add_argument("--workload-manifest", type=Path, required=True)
    parser.add_argument("--truth-id", required=True)
    parser.add_argument("--source-trace-id", required=True)
    parser.add_argument("--workload-manifest-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--tokenizer-revision", required=True)
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--exporter-revision", required=True)
    parser.add_argument(
        "--initial-radix-state",
        choices=("empty_server_boot", "explicit_cache_reset", "unknown"),
        required=True,
    )
    parser.add_argument(
        "--workload-instance-file",
        type=Path,
        help="Optional newline-delimited allowlist of complete workload instances.",
    )
    parser.add_argument(
        "--artifact-role",
        choices=(
            "cpu_counterfactual_oracle_estimate_input",
            "gpu_trace_replay_input",
        ),
        default="cpu_counterfactual_oracle_estimate_input",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _args()
    output = args.output_dir.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    provenance = FrozenDemandProvenance(
        truth_id=args.truth_id,
        source_trace_id=args.source_trace_id,
        workload_manifest_id=args.workload_manifest_id,
        model_revision=args.model_revision,
        tokenizer_revision=args.tokenizer_revision,
        runtime_revision=args.runtime_revision,
        harness_revision=args.harness_revision,
        exporter_revision=args.exporter_revision,
    )
    result = OracleTruthExporter().export(
        runtime_event_path=args.runtime_events,
        runtime_audit_path=args.runtime_audit,
        request_token_trace_path=args.request_token_trace,
        workload_manifest_path=args.workload_manifest,
        provenance=provenance,
        source_trace_id=args.source_trace_id,
        initial_radix_state=args.initial_radix_state,
        workload_instances=(
            frozenset(
                line.strip()
                for line in args.workload_instance_file.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )
            if args.workload_instance_file is not None
            else None
        ),
    )
    truth_path = output / "frozen_agent_demand_v2.json"
    sidecar_path = output / "frozen_physical_sidecar_v1.json.gz"
    manifest_path = output / "export_manifest.json"
    truth_path.write_bytes(result.truth.canonical_bytes())
    with sidecar_path.open("wb") as raw_output:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_output,
            compresslevel=6,
            mtime=0,
        ) as compressed:
            compressed.write(result.physical_sidecar.canonical_bytes())
    summary = {
        **result.summary(),
        "schema_version": 1,
        "artifact_role": args.artifact_role,
        "truth_path": truth_path.name,
        "physical_sidecar_path": sidecar_path.name,
        "historical_release_schedule_only": True,
        "subset_replay_initial_radix_reset": (
            args.workload_instance_file is not None
        ),
        "forbidden_truth_fields": [
            "historical_queue_wait",
            "historical_admission_order",
            "historical_request_finish_wall_clock",
            "historical_join_absolute_timestamp",
            "historical_gpu_service_ms",
            "historical_transfer_timestamp",
        ],
    }
    manifest_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
