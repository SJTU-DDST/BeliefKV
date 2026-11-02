"""Report held-out progress slices and CPU component cost without tuning the model."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import support_status
from scripts.train_workflow_work import write
from scripts.train_workflow_work_distribution import distribution_metrics


def describe(samples):
    import numpy as np

    return {
        "count": len(samples), "p50_ms": float(np.quantile(samples, .5)),
        "p99_ms": float(np.quantile(samples, .99)), "total_ms": sum(samples),
    }


def audit(dataset, model, evaluation):
    rows = dataset["rows"]
    held_out = set(evaluation["split_contract"]["evaluation_execution_sha256"])
    testing = [row for row in rows if row["source_workflow_sha256"] in held_out]
    slices = {
        "initial": [row for row in testing if row["snapshot"] == 0],
        "first_third_logical_work": [
            row for row in testing if row["attained_work"] / row["total_work"] < 1 / 3
        ],
        "middle_third_logical_work": [
            row for row in testing if 1 / 3 <= row["attained_work"] / row["total_work"] < 2 / 3
        ],
        "last_third_logical_work": [
            row for row in testing if row["attained_work"] / row["total_work"] >= 2 / 3
        ],
    }
    times, projections = defaultdict(list), {}
    for row in rows:
        if row["workflow"] not in projections:
            wall, cpu = time.perf_counter(), time.thread_time()
            projections[row["workflow"]] = model.task_encoding(row["text"])
            times["task_encoding_wall"].append((time.perf_counter() - wall) * 1000)
            times["task_encoding_thread_cpu"].append((time.thread_time() - cpu) * 1000)
    # Fixed stride over all original snapshots; no selection by predicted quality.
    stride = max(1, len(rows) // 4096)
    for row in rows[::stride]:
        projection, coverage = projections[row["workflow"]]
        wall, cpu = time.perf_counter(), time.thread_time()
        if projection is not None:
            model.predict(projection, row["features"])
        support_status(model, row["features"], coverage)
        times["cached_inference_and_support_wall"].append((time.perf_counter() - wall) * 1000)
        times["cached_inference_and_support_thread_cpu"].append((time.thread_time() - cpu) * 1000)
    return {
        "held_out": {name: distribution_metrics(model, values) for name, values in slices.items()},
        "CPU_component_microbenchmark": {name: describe(values) for name, values in times.items()},
        "cost_scope": "task_encoding_once; cached_model_and_support; not_whole_worker_native_or_GPU_control",
        "progress_slices_are_posthoc_diagnostics_not_model_selection": True,
        "actual_ready_paused_competition_quality": "requires_matched_live_selection_observations",
        "model_parameters_thresholds_and_calibration_unchanged": True,
        "GPU_action": False, "JCT_gain_proven": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "model", "evaluation", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    evaluation = json.loads(args.evaluation.read_bytes())
    digest = sha256_file(args.model)
    if evaluation["model_sha256"] != digest:
        raise ValueError("audit the exact held-out model without retraining")
    pins = {str(path.resolve()): sha256_file(path) for path in (
        args.dataset, args.model, args.evaluation, Path(__file__),
    )}
    result = audit(
        json.loads(args.dataset.read_bytes()), WorkflowWorkModel.load(args.model, digest), evaluation,
    )
    if any(sha256_file(Path(path)) != value for path, value in pins.items()):
        raise ValueError("a prediction audit input changed")
    write(args.output, {**result, "inputs_sha256": pins})
    print(json.dumps({
        "initial_accuracy": result["held_out"]["initial"]["initial_accuracy"],
        "cached_inference_and_support_wall": result["CPU_component_microbenchmark"]["cached_inference_and_support_wall"],
        "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
