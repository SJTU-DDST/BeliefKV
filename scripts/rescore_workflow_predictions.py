"""Score new predictors against sealed, previously reconstructed competition inputs."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import support_status
from scripts.audit_live_work_prediction import rank_summary
from scripts.train_workflow_work import write


def rescore(decisions, models, initial_texts):
    """Reuse recorded inputs; no final task state or future text can enter a prediction."""
    result, projections = deepcopy(decisions), {}
    for model in models.values():
        if model.observed_delegations:
            raise ValueError("sealed numeric inputs lack per-boundary delegation text")
    for decision in result:
        for candidate in decision["candidates"]:
            if "features" not in candidate:
                continue
            workflow = candidate["key"]["root_workflow_id"]
            text = initial_texts[workflow]
            for name, model in models.items():
                key = name, workflow
                if key not in projections:
                    projections[key] = model.task_encoding(text)
                projection, coverage = projections[key]
                candidate.setdefault("predictions", {})[name] = None
                if projection is None:
                    continue
                probabilities = model.predict(projection, candidate["features"])
                support = support_status(model, candidate["features"], coverage)
                candidate["predictions"][name] = {
                    "probabilities": probabilities, "support": support,
                    "effective_weight": (
                        probabilities[0] + .5 * probabilities[1] + .25 * probabilities[2]
                        if support == "calibrated_distribution" else .5
                    ),
                }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("frozen-audit", "dataset", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", type=Path, action="append", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier sealed competition evaluations")
    paths = (args.frozen_audit, args.dataset, args.cohort, *args.model)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    audit, data, cohort = (json.loads(path.read_bytes()) for path in paths[:3])
    if (
        audit["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or audit["inputs_sha256"].get(str(args.cohort.resolve())) != pins[str(args.cohort.resolve())]
        or not audit["scope"]["protected_JOIN_pairs_excluded_from_predictor_rank_quality"]
        or not audit["scope"]["client_published_events_and_observed_native_progress_only_for_features"]
    ):
        raise ValueError("require source-bound sealed causal inputs and the same evaluation population")
    texts = {}
    for row in data["rows"]:
        if row["snapshot"] == 0:
            workflow = row["logical_workflow_id"]
            if workflow in texts:
                raise ValueError("ambiguous initial task identity")
            texts[workflow] = row.get("task_text", row["text"])
    models = {
        str(path.resolve()): WorkflowWorkModel.load(path, pins[str(path.resolve())])
        for path in args.model
    }
    decisions = rescore(audit["decisions"], models, texts)
    measured = set(cohort["measured_workflow_ids"])
    summaries = {name: {
        "all_workflows": rank_summary(decisions, name, measured=measured),
        "both_held_out_measured": rank_summary(decisions, name, measured=measured, held_out_only=True),
    } for name in models}
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a sealed competition or model changed during evaluation")
    write(args.output, {
        "models": summaries, "decisions": decisions, "inputs_sha256": pins,
        "original_audit_inputs_sha256": audit["inputs_sha256"], "scope": audit["scope"],
        "causal_feature_vectors_and_labels_reused_exactly": True,
        "only_initial_task_text_used_from_source_dataset": True,
        "new_GPU_or_service_or_JCT_evidence": False,
    })
    print(json.dumps(summaries), flush=True)


if __name__ == "__main__":
    main()
