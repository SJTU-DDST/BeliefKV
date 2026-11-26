#!/usr/bin/env python3
"""Register one completed external harness capture for exact causal replay."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beliefkv.experiments.harness_source import HARNESS_NAMES, register_harness_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--harness", choices=HARNESS_NAMES, required=True)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-revision", required=True)
    parser.add_argument("--events", action="append", required=True, help="client event JSONL relative to arm")
    parser.add_argument("--workflow-task-ids", type=Path, required=True, help="JSON mapping workflow ID to task ID")
    args = parser.parse_args()
    result = register_harness_source(
        args.arm, harness=args.harness, harness_revision=args.harness_revision,
        dataset=args.dataset, dataset_revision=args.dataset_revision,
        client_event_files=args.events, workflow_task_ids=json.loads(args.workflow_task_ids.read_bytes()),
    )
    print(json.dumps(result["lifecycle"], indent=2))


if __name__ == "__main__":
    main()
