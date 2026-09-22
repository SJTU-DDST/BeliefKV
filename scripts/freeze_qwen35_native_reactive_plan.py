#!/usr/bin/env python3
"""Freeze train-only Qwen3.5 native-reactive batches from an existing split."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.p6_collection import load_collection_batch


def freeze_native_reactive_train_plan(source: Path, output: Path) -> dict:
    source = source.resolve()
    original = source.read_bytes()
    plan = json.loads(original)
    if (
        not isinstance(plan, dict) or plan.get("frozen") is not True
        or plan.get("runtime_policy") != "frozen_p5_observed"
        or plan.get("predictor_enabled") is not False
        or plan.get("predictive_actions_enabled") is not False
    ):
        raise ValueError("source must be a frozen, predictor-off P5 plan")
    train = [
        item for item in plan.get("batches", ())
        if isinstance(item, dict) and item.get("split") == "train"
    ]
    if not train or len({item.get("batch_id") for item in train}) != len(train):
        raise ValueError("source must have uniquely identified train batches")
    for batch in train:
        load_collection_batch(source, batch["batch_id"])
    instance_ids = {
        str(instance_id)
        for batch in train
        for instance_id in batch.get("instance_ids", ())
    }
    projects = {
        str(project) for batch in train
        for project in batch.get("projects", ())
    }
    native = {
        **plan,
        "plan_id": "qwen35-native-reactive-v0520-v1",
        "runtime_policy": "frozen_native_reactive_v0520",
        "source_plan_sha256": hashlib.sha256(original).hexdigest(),
        "source_plan": str(source),
        "batches": [
            {**item, "policy": "frozen_native_reactive_v0520"}
            for item in train
        ],
        "batch_count": len(train),
        "unique_task_count": len(instance_ids),
        "repository_count": len(projects),
        "workflow_count": sum(int(item["workflow_count"]) for item in train),
        "unique_task_counts_by_split": {"train": len(instance_ids)},
        "workflow_counts_by_split": {
            "train": sum(int(item["workflow_count"]) for item in train)
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(native, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return native


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = freeze_native_reactive_train_plan(args.source, args.output)
    print(json.dumps({
        "plan_id": plan["plan_id"],
        "batch_count": plan["batch_count"],
        "workflow_count": plan["workflow_count"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
