#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.predictor.action_targets import (  # noqa: E402
    OperationalActionTargetContract,
    build_action_target_rows,
    build_native_reactive_observations,
)


def _read_jsonl(path: Path, *, optional: bool = False) -> list[dict[str, object]]:
    if optional and not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export schema-v4 operational action targets from frozen P6 data."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path)
    parser.add_argument(
        "--native-reactive-only", action="store_true",
        help="Export observed JOIN/child/admission timing without BF16 anchors.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    if args.native_reactive_only == (args.contract is not None):
        parser.error("choose exactly one of --contract or --native-reactive-only")
    contract = None
    if args.contract is not None:
        contract = OperationalActionTargetContract.load(args.contract)
        manifest_path = args.dataset_dir / "dataset_manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            server = (
                (manifest.get("source") or {})
                .get("runtime_environment_contract", {})
                .get("server_identity", {})
            )
            if (
                str(server.get("served_model_name") or "").startswith("Qwen3.5")
                and contract.deployment_profile_id.startswith("h200_bf16")
            ):
                parser.error(
                    "Qwen3.5 FULL/Mamba cannot use the legacy BF16 KV/transfer contract"
                )
    decisions = _read_jsonl(args.dataset_dir / "frontier_decision_points.jsonl")
    reentries = _read_jsonl(args.dataset_dir / "reentries.jsonl", optional=True)
    transfers = _read_jsonl(args.dataset_dir / "pcie_operations.jsonl", optional=True)
    if args.native_reactive_only:
        rows, report = build_native_reactive_observations(
            decisions, reentries, transfers
        )
    else:
        assert contract is not None
        rows, report = build_action_target_rows(
            decisions,
            _read_jsonl(args.dataset_dir / "external_waits.jsonl"),
            contract, reentries, transfers,
        )
    for path, payload, jsonl in (
        (args.output, rows, True),
        (args.report, report, False),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        if jsonl:
            temporary.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in payload),
                encoding="utf-8",
            )
        else:
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        temporary.replace(path)
    print(json.dumps({"output": str(args.output), "report": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
