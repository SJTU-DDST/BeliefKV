"""Rescore sealed causal competition features without rereading the GPU journals."""

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


def rescore(decisions, texts, models):
    values, cached = deepcopy(decisions), {}
    for decision in values:
        for candidate in decision["candidates"]:
            if not candidate.get("selector_metadata_valid", True) or "features" not in candidate:
                continue
            workflow = candidate["key"]["root_workflow_id"]
            if workflow not in texts:
                raise ValueError("sealed competition lacks its original initial task metadata")
            for name, model in models.items():
                if model.observed_delegations:
                    raise ValueError("a root-only sealed rescore cannot infer delegation text versions")
                key = name, workflow
                if key not in cached:
                    cached[key] = model.task_encoding(texts[workflow])
                projection, coverage = cached[key]
                if projection is None:
                    continue
                probabilities = model.predict(projection, candidate["features"])
                support = support_status(model, candidate["features"], coverage)
                candidate.setdefault("predictions", {})[name] = {
                    "probabilities": probabilities, "support": support,
                    "effective_weight": (
                        probabilities[0] + .5 * probabilities[1] + .25 * probabilities[2]
                        if support == "calibrated_distribution" else .5
                    ),
                }
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sealed-audit", "dataset", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", type=Path, action="append", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier competition rescoring results")
    paths = [args.sealed_audit, args.dataset, args.cohort, *args.model]
    inputs = {str(path.resolve()): sha256_file(path) for path in paths}
    sealed, dataset, cohort = (
        json.loads(path.read_bytes()) for path in (args.sealed_audit, args.dataset, args.cohort)
    )
    if (
        not sealed["scope"]["client_published_events_and_observed_native_progress_only_for_features"]
        or sealed["scope"].get("protected_JOIN_identification")
        != "zero_global_rank_not_reference_category_v2"
    ):
        raise ValueError("require an explicitly causal source audit")
    texts = {
        row["logical_workflow_id"]: row.get("task_text", row["text"])
        for row in dataset["rows"] if row["snapshot"] == 0
    }
    models = {
        str(path.resolve()): WorkflowWorkModel.load(path, inputs[str(path.resolve())])
        for path in args.model
    }
    values = rescore(sealed["decisions"], texts, models)
    measured = set(cohort["measured_workflow_ids"])
    summary = {
        name: {
            "all_workflows": rank_summary(values, name, measured=measured),
            "both_held_out_measured": rank_summary(values, name, measured=measured, held_out_only=True),
        } for name in models
    }
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("an immutable rescoring input changed")
    write(args.output, {
        "models": summary, "decisions": values, "inputs_sha256": inputs,
        "original_audit_inputs_sha256": sealed["inputs_sha256"],
        "scope": sealed["scope"],
        "causal_feature_vectors_reused_exactly": True,
        "only_initial_task_text_used_from_source_dataset": True,
        "new_GPU_or_service_or_JCT_evidence": False,
    })
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
