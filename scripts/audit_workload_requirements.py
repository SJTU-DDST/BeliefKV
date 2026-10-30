"""Refresh complete source work attribution and screen declared mechanism needs."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import inventory_capture
from beliefkv.experiments.workload_requirements import assess
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--shape", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve all prior workload assessments")
    previous = json.loads(args.inventory.read_bytes())
    shape = json.loads(args.shape.read_bytes())
    for path, digest in shape["native_service_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("cached shape no longer binds the native service journal")
    current = inventory_capture(
        Path(previous["capture"]), seconds_per_model_unit=previous["model_seconds_per_unit"],
    )
    if (
        current["token_evidence"]["token_source_sha256"] != previous["token_evidence"]["token_source_sha256"]
        or {row["workflow_id"] for row in current["workflows"]} !=
        {row["workflow_id"] for row in previous["workflows"]}
    ):
        raise ValueError("refreshed inventory differs from the retained original source")
    result = assess(current, shape)
    result["inputs_sha256"] = {
        str(path.resolve()): sha256_file(path)
        for path in (
            args.inventory, args.shape, Path(__file__),
            ROOT / "beliefkv/experiments/workload_requirements.py",
            ROOT / "beliefkv/experiments/workload_mix.py",
        )
    }
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(args.output_dir / "inventory.json", current)
    write(args.output_dir / "assessment.json", result)
    print({
        "shape_status": result["shape_status"], "shortfalls": result["shape_shortfalls"],
        "summary": result["summary"], "formal_comparison_ready": False, "GPU_action": False,
    })


if __name__ == "__main__":
    main()
