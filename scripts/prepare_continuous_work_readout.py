"""Bind a CPU-only continuous R5 artifact and measure its single-pass inference cost."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import ContinuousWorkReadout, serving_contract
from beliefkv.predictor.workflow_work import WorkflowWorkModel, before_observed_work
from scripts.evaluate_workflow_continuous_projects import combine_datasets
from scripts.train_workflow_utility_readout import sampled
from scripts.train_workflow_work import write


def training_rows(raw, rows):
    split = raw["metadata"]["split_contract"]
    keys = ("training_execution_sha256", "calibration_execution_sha256", "evaluation_execution_sha256")
    groups = [set(split[key]) for key in keys]
    if any(left & right for index, left in enumerate(groups) for right in groups[index + 1:]):
        raise ValueError("the retained model has overlapping complete execution labels")
    selected = [row for row in rows if row["source_workflow_sha256"] in groups[0]]
    if {row["source_workflow_sha256"] for row in selected} != groups[0]:
        raise ValueError("the reference must use every exact declared training execution and no others")
    return selected


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output_dir.exists():
        raise ValueError("hide CUDA and preserve previous continuous-work evidence")
    sources = [
        ROOT / "beliefkv/predictor/workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_continuous_work.py",
        ROOT / "beliefkv/predictor/workflow_conditional_tree.py",
        ROOT / "beliefkv/predictor/workflow_progress_tree.py",
        ROOT / "beliefkv/runtime/workflow_work_worker.py",
        ROOT / "beliefkv/policy/workflow_combination.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "scripts/evaluate_workflow_continuous_projects.py",
        Path(__file__),
    ]
    pins = {str(path.resolve()): sha256_file(path) for path in [args.model, *args.dataset, *sources]}
    raw = json.loads(args.model.read_bytes())
    if "continuous_work_readout" in raw:
        raise ValueError("preserve the original binned predictor as the exact comparison")
    original = WorkflowWorkModel(raw)
    rows = combine_datasets([json.loads(path.read_bytes()) for path in args.dataset])
    selected = training_rows(raw, rows)
    contract = serving_contract(original, selected)
    candidate_raw = deepcopy(raw)
    candidate_raw["continuous_work_readout"] = contract
    candidate_raw["metadata"].update(
        decision_eligible=False,
        decision_rejection_reasons=["continuous_work_readout_not_yet_performance_qualified"],
        continuous_work_source_model_sha256=pins[str(args.model.resolve())],
        continuous_work_validation_scope="CPU_only_representation_and_cost_not_new_generalization",
    )
    candidate = WorkflowWorkModel(candidate_raw)
    readout = ContinuousWorkReadout(original, contract)
    rows = sampled(rows, 64)
    projections, samples = {}, []
    maximum_error, count = 0., 0
    for row in rows:
        text = row.get("task_text", row["text"])
        if text not in projections:
            projections[text] = original.task_projection(text), candidate.task_projection(text)
        old_projection, projection = projections[text]
        if old_projection is None:
            continue
        old = original.predict(old_projection, row["features"])
        probabilities, forecast = candidate.predict(projection, row["features"], with_work=True)
        maximum_error = max(maximum_error, max(abs(a - b) for a, b in zip(old, probabilities)))
        if old != probabilities:
            raise ValueError("continuous readout changed the original class probabilities")
        reference = readout.predict(
            old_projection, row["features"], probabilities=old, support="calibrated_distribution",
        )
        if forecast != reference:
            raise ValueError("single-pass continuous work differs from the offline reference")
        count += 1
        if not before_observed_work(row["features"]):
            samples.append((old_projection, projection, row["features"]))
    if len(samples) < 64:
        raise ValueError("use at least 64 actual observed-progress states for bounded CPU timing")
    samples = [samples[index] for index in np.linspace(0, len(samples) - 1, 64, dtype=int)]
    functions = {
        "original_probability_only": lambda item: original.predict(item[0], item[2]),
        "continuous_single_pass": lambda item: candidate.predict(item[1], item[2], with_work=True),
        "offline_duplicate_forward": lambda item: readout.predict(
            item[0], item[2], probabilities=original.predict(item[0], item[2]),
            support="calibrated_distribution",
        ),
    }
    for function in functions.values():
        for item in samples[:16]:
            function(item)
    measurements = {name: [] for name in functions}
    names = list(functions)
    for repeat in range(10):
        for index, item in enumerate(samples):
            for name in names[(repeat + index) % 3:] + names[:(repeat + index) % 3]:
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                functions[name](item)
                measurements[name].append((
                    time.thread_time_ns() - cpu, time.perf_counter_ns() - wall,
                ))
    summary = {}
    for name, values in measurements.items():
        values = np.asarray(values) / 1000.
        summary[name] = {
            "samples": len(values),
            **{
                kind + "_us": {
                    "p50": float(np.percentile(values[:, axis], 50)),
                    "p90": float(np.percentile(values[:, axis], 90)),
                    "p99": float(np.percentile(values[:, axis], 99)),
                } for axis, kind in enumerate(("thread_CPU", "wall"))
            },
        }
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a source-bound continuous-work input changed")
    write(args.output_dir / "model.json", candidate_raw)
    write(args.output_dir / "evaluation.json", {
        "source_model_sha256": pins[str(args.model.resolve())], "inputs_sha256": pins,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "reference": contract, "exact_probability_and_readout_parity_snapshots": count,
        "maximum_probability_error": maximum_error, "timing": summary,
        "native_scheduling_path_runs_inference": False,
        "readout_reuses_one_MLP_and_tree_forward": True,
        "trained_weights_features_and_tool_target_changed": False,
        "initial_estimate_still_uses_training_class_prior": True,
        "CPU_timing_is_not_control_cost_or_JCT_gain": True,
        "fresh_generalization_or_accuracy_improvement_claimed": False,
        "GPU_action": False, "serving_eligibility_granted": False,
    })
    print(json.dumps({
        "exact_snapshots": count, "reference_work": contract["reference_work"],
        "timing": summary, "serving_eligibility_granted": False,
    }), flush=True)


if __name__ == "__main__":
    main()
