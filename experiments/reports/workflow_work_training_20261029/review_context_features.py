"""Review fixed-execution context features without registering a GPU candidate."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import CONTEXT_SCHEMA, SCHEMA, WorkflowWorkModel
from experiments.reports.workflow_work_training_20261029.refresh_native_progress import (
    verify_context_projection,
)
from scripts.train_workflow_work import probabilities, write


def first_submit_metrics(model, rows):
    import numpy as np

    first = {}
    for row in rows:
        if row["event"] == "llm_submit":
            first.setdefault(row["workflow"], row)
    values = list(first.values())
    true = np.searchsorted(model.thresholds, [row["remaining_work"] for row in values], side="right")
    return {
        "workflows": len(first),
        "accuracy": float((probabilities(model, values).argmax(axis=1) == true).mean()),
        "distinct_numeric_vectors": len({tuple(row["features"]) for row in values}),
        "boundary": "first_actual_submitted_call_not_pre_request_workflow_start",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--paused", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [
        args.original / "input_binding_r2/model.json",
        args.context / "input_binding/model.json",
        args.original / "input_binding_r2/evaluation.json",
        args.context / "input_binding/evaluation.json",
        args.context / "input_binding/parity.json",
        args.original / "paused_stream_repaired_r1.json",
        args.paused,
        args.original / "fit/model.json",
        Path(__file__),
    ]
    data = {}
    for name in ("training", "calibration", "evaluation"):
        pair = [folder / (name + ".json") for folder in (args.original, args.context)]
        paths.extend(pair)
        data[name] = [json.loads(path.read_bytes()) for path in pair]
    bound = {str(path.resolve()): sha256_file(path) for path in paths}
    old, new, old_metric, new_metric, parity, old_rank, new_rank, old_unbound = (
        json.loads(path.read_bytes()) for path in paths[:8]
    )
    if old["schema"] != SCHEMA or new["schema"] != CONTEXT_SCHEMA:
        raise ValueError("expected exact legacy and observed-context feature schemas")
    if {k: v for k, v in old.items() if k != "metadata"} != {
        k: v for k, v in old_unbound.items() if k != "metadata"
    } or old_rank["model_sha256"] != sha256_file(paths[7]):
        raise ValueError("old paused report does not use the original numeric model")
    for key in ("work_thresholds",):
        if old[key] != new[key]:
            raise ValueError("context comparison changed work classes")
    for key in ("split_contract", "training_parameters", "epochs", "seed", "task_input_contract"):
        if old["metadata"][key] != new["metadata"][key]:
            raise ValueError("context comparison changed fixed training or execution controls")
    for original, current in data.values():
        verify_context_projection(original, current)
    if (
        parity["all_probability_and_input_checks_exact"] is not True
        or parity["streaming_progress_parity"]["features_projection_probabilities_exact"] is not True
        or new_metric["model_sha256"] != sha256_file(paths[1])
        or new_rank["model_sha256"] != sha256_file(paths[1])
        or new_rank["dataset_sha256"] != sha256_file(args.context / "evaluation.json")
        or old_rank["counts"] != new_rank["counts"]
    ):
        raise ValueError("context model lacks exact input binding or matching paused evidence")
    if len(old_rank["candidates"]) != len(new_rank["candidates"]) or any(
        {k: v for k, v in left.items() if k != "bucket"}
        != {k: v for k, v in right.items() if k != "bucket"}
        for left, right in zip(old_rank["candidates"], new_rank["candidates"])
    ):
        raise ValueError("context model changed the paused candidate population or labels")
    models = [WorkflowWorkModel(raw) for raw in (old, new)]
    metrics = {}
    for index, (label, metric, ranking) in enumerate((
        ("legacy", old_metric, old_rank), ("context", new_metric, new_rank),
    )):
        metrics[label] = {
            "calibration": metric["calibration"], "development": metric["test"],
            "continuous_paused_pairs": ranking["continuous_pair_metrics"],
            "class_paused_pairs": ranking["pair_metrics"],
            "first_submit": first_submit_metrics(models[index], data["evaluation"][index]["rows"]),
        }
    result = {
        "status": "complete_cpu_context_feature_review_not_promoted",
        "metrics": metrics,
        "fixed_complete_execution_populations_and_old_features_preserved": True,
        "project_task_overlap_allowed_across_independent_executions": True,
        "blind_or_unseen_project_generalization_claimed": False,
        "full_progress_input_parity": parity["streaming_progress_parity"],
        "decision": (
            "Do not launch GPU from classification-score improvement alone. "
            "Continuous paused ordering regressed on the same observed candidate population."
        ),
        "scope": (
            "Repeated post-control observations; no capacity, asynchronous-cache timing, "
            "native selection, JCT or performance counterfactual."
        ),
        "inputs_sha256": bound,
        "GPU_experiment_registered": False, "performance_accepted": False,
    }
    if any(sha256_file(Path(path)) != digest for path, digest in bound.items()):
        raise ValueError("context review inputs changed")
    write(args.output, result)
    print(json.dumps({
        label: {
            "macro_f1": metric["development"]["macro_f1"],
            "continuous_pairs": metric["continuous_paused_pairs"],
            "first_submit": metric["first_submit"],
        } for label, metric in metrics.items()
    }, indent=2))


if __name__ == "__main__":
    main()
