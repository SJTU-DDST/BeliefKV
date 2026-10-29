"""CPU microbenchmarks of cached resume selection and background prediction."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from beliefkv.predictor.workflow_work import FEATURES, WorkflowProgress, WorkflowWorkModel


def measure(operation, repeats=200):
    import numpy as np

    cpu, wall = [], []
    for _ in range(repeats):
        a, b = time.thread_time(), time.perf_counter()
        operation()
        cpu.append((time.thread_time() - a) * 1e6)
        wall.append((time.perf_counter() - b) * 1e6)
    return {
        name: {"p50_us": float(np.median(values)), "p99_us": float(np.quantile(values, .99)),
               "mean_us": float(np.mean(values))}
        for name, values in (("thread_cpu", cpu), ("wall", wall))
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--configuration", type=Path)
    args = parser.parse_args()
    result = {
        "scope": "exclusive CPU microbenchmarks; no end-to-end CPU, GPU stall or JCT claim",
        "resume": {},
        "model_sha256": sha256_file(args.model),
    }
    for n in (8, 64, 256):
        original = ThunderActingDecayWorkingSet(n * 2400)
        candidate = WorkflowSizeWorkingSet(
            n * 2400, rank=lambda identity, now: int(identity) % 3,
            sibling_group=lambda identity: str(int(identity) // 2),
        )
        templates = {
            str(i): ThunderProgram(str(i), 1000 + (i * 7919) % 7000, steps=2, pending=True,
                                   queue_sequence=i)
            for i in range(n)
        }
        pair = {}
        for name, live in (("reference", original), ("candidate", candidate)):
            def run():
                live.programs = {identity: replace(item) for identity, item in templates.items()}
                live.tick(now=10.)
            pair[name] = measure(run)
        result["resume"][str(n)] = pair
    model = WorkflowWorkModel.load(args.model, sha256_file(args.model))
    environment = (
        json.loads(args.configuration.read_bytes())["common_environment"]
        if args.configuration else None
    )
    task = model.task_input_prefix(environment) + (
        "Implement and validate a concurrent API endpoint with focused tests."
    )
    result["initial_task_projection"] = measure(lambda: model.task_projection(task), 40)
    projection = model.task_projection(task)
    result["cached_task_dynamic_inference"] = measure(
        lambda: model.predict(projection, [1.] * len(FEATURES)),
    )
    prefix = tuple(range(100000))
    warm = WorkflowProgress()
    warm.completed("warm", "agent", "context", prefix, ())
    def completed_prefix():
        progress = WorkflowProgress()
        progress.histories["context"] = warm.histories["context"]
        progress.completed("request", "agent", "context", prefix + (100000,), (2, 3))
    result["background_100k_same_context_prefix_accounting"] = measure(completed_prefix, 40)
    result["history_representation"] = "owned_contiguous_numpy_int64"
    result["task_input_kind"] = model.task_input_kind
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
