"""Bound CPU inference and sibling selection cost on sealed, unchanged R5 inputs."""

import argparse
from collections import Counter
from itertools import cycle
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts, CombinationResources
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.audit_r5_shortest_contract import candidate
from scripts.benchmark_workflow_size_cpu import measure
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("require fresh CPU-only nice19 benchmark")
    data = json.loads(args.dataset.read_bytes())
    model = WorkflowWorkModel.load(args.model, sha256_file(args.model))
    base = WorkflowWorkModel.load(Path(data["base_model"]), data["base_sha256"])
    if model.completion_path.raw["training_source"]["sha256"] != sha256_file(args.dataset):
        raise ValueError("benchmark requires the head's actual source dataset")
    rows = data["rows"]
    samples = [rows[index] for index in range(0, len(rows), max(1, len(rows) // 32))][:32]
    projections = {
        wf: model.task_projection(data["task_text"][wf]) for wf in {row["workflow"] for row in samples}
    }
    operations = {}
    for name, serving, with_path in (("model_work", base, False), ("model_tool_path", model, True)):
        inputs = cycle(samples)

        def run(serving=serving, with_path=with_path, inputs=inputs):
            row = next(inputs)
            return serving.predict(
                projections[row["workflow"]], row["features"], with_work=True,
                **({"path_timing": row["timing"]} if with_path else {}),
            )

        operations[name] = run
        for _ in range(len(samples)):
            run()
    inference = {name: measure(operation, 256) for name, operation in operations.items()}
    costs = CombinationCosts(**model.raw["admission_cost_profile"]["costs"])
    selection = {}
    for width in (6, 8):
        cases = [
            row for row in data["decisions"]
            if len(row["candidates"]) == width and row["resources"] is not None
        ][:32]
        parsed = [
            ([candidate(value) for value in row["candidates"]], row["decision"].get("reference", ()),
             CombinationResources(**row["resources"]), row["decision"].get("required_reference_members", ()))
            for row in cases
        ]
        if not parsed:
            selection[str(width)] = {"observed_cases": 0}
            continue
        timings = {}
        for name, grouped in (("legacy_category_groups", False), ("ready_sibling_groups", True)):
            inputs, reasons = cycle(parsed), Counter()

            def run(grouped=grouped, inputs=inputs, reasons=reasons):
                rows, reference, resources, required = next(inputs)
                result = choose_shortest_work_first(
                    rows, reference, resources, costs, required=required,
                    strict_remaining_work=True, group_ready_siblings=grouped,
                )
                reasons[result["reason"]] += 1

            for _ in range(len(parsed)):
                run()
            reasons.clear()
            timings[name] = {**measure(run, 256), "reasons": dict(reasons)}
        selection[str(width)] = {"observed_cases": len(parsed), **timings}
    result = {
        "inference": inference, "selection": selection,
        "actual_causal_snapshots_used": len(samples),
        "CPU_affinity": sorted(os.sched_getaffinity(0)),
        "observed_at": time.time(),
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (
                args.dataset, args.model, Path(__file__),
                ROOT / "beliefkv/predictor/workflow_work.py",
                ROOT / "beliefkv/predictor/workflow_completion_path.py",
                ROOT / "beliefkv/policy/workflow_shortest_frontier.py",
            )
        },
        "scope": {
            "cached_task_projection_and_existing_progress_only": True,
            "inference_on_background_worker_not_synchronous_scheduler": True,
            "selection_excludes_native_prefix_metadata_and_logging": True,
            "local_CPU_cost_not_GPU_stall_or_JCT_gain": True,
        }, "GPU_action": False,
    }
    write(args.output, result)
    print(json.dumps({"inference": inference, "selection": selection}), flush=True)


if __name__ == "__main__":
    main()
