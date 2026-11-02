"""Check actual background publication and bounded cached prediction CPU cost."""

import argparse
from collections import defaultdict
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import support_status
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from scripts.train_workflow_work import write
from scripts.train_workflow_work_distribution import split_rows


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "evaluation", "dataset", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--check-unchanged-validity", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier actual-worker observations")
    paths = (args.model, args.evaluation, args.dataset, args.cohort)
    inputs = {str(path.resolve()): sha256_file(path) for path in paths}
    evaluation, dataset, cohort = (
        json.loads(path.read_bytes()) for path in (args.evaluation, args.dataset, args.cohort)
    )
    if evaluation["model_sha256"] != inputs[str(args.model.resolve())]:
        raise ValueError("validate the exact selected predictor")
    _train, _calibration, test, _split = split_rows(dataset["rows"], cohort["measured_workflow_ids"])
    initial = [row for row in test if row["snapshot"] == 0]
    model = WorkflowWorkModel.load(args.model, inputs[str(args.model.resolve())])
    worker = WorkflowWorkWorker("", "", model=model)
    observed = {}
    validity_cost, revalidated = [], 0
    try:
        for row in initial:
            workflow, invocation = row["logical_workflow_id"], row["logical_workflow_id"] + "-root"
            metadata = WorkflowRankInput.from_instructions(task_instructions=row["text"])
            worker.submit(workflow, "event", RuntimeEvent(
                workflow + "-start", 0., Kind.WORKFLOW_START, workflow,
                attributes={"workflow_ranking_input": metadata.to_dict()},
            ))
            worker.submit(workflow, "event", RuntimeEvent(
                workflow + "-root-create", 1., Kind.INVOCATION_CREATE, workflow,
                invocation_id=invocation, context_id=invocation + "-context",
                relation_type=RelationType.ROOT,
            ))
        deadline = time.monotonic() + 15.
        while time.monotonic() < deadline:
            if worker.error:
                raise ValueError("actual prediction worker failed: " + worker.error)
            observed = {row["logical_workflow_id"]: worker.prediction(row["logical_workflow_id"])
                        for row in initial}
            if all(value is not None for value in observed.values()):
                break
            time.sleep(.01)
        if len(observed) != len(initial) or any(value is None for value in observed.values()):
            raise ValueError("actual background worker did not publish the complete initial cohort")
        for row in initial:
            projection = model.task_projection(row["text"])
            expected = model.predict(projection, row["features"])
            if not np.allclose(expected, observed[row["logical_workflow_id"]].probabilities, atol=1e-12, rtol=0):
                raise ValueError("actual worker and offline initial probabilities differ")
        if args.check_unchanged_validity:
            before = worker.counts["inferences"]
            identities = list(observed)
            for index in range(1024):
                workflow = identities[index % len(identities)]
                previous = worker.prediction(workflow)
                if previous is None:
                    raise ValueError("the unchanged real-worker input unexpectedly became unavailable")
                worker._predictions[workflow] = replace(previous, expires_at=0.)
                started = time.thread_time_ns()
                current = worker.prediction(workflow)
                validity_cost.append((time.thread_time_ns() - started) / 1000.)
                if current is None:
                    continue
                if (
                    current.probabilities != previous.probabilities
                    or current.updated_at != previous.updated_at or current.revision != previous.revision
                    or current.expires_at <= time.monotonic()
                ):
                    raise ValueError("validity revalidation changed inference semantics")
                revalidated += 1
            if worker.counts["inferences"] != before:
                raise ValueError("unchanged-input validation introduced extra inference")
        worker_counts = dict(worker.counts)
    finally:
        worker.close()
    if worker._thread.is_alive():
        raise ValueError("actual prediction worker failed to exit")
    cached, samples = {}, defaultdict(list)
    for index in np.linspace(0, len(test) - 1, min(1024, len(test)), dtype=int):
        row = test[index]
        workflow = row["workflow"]
        if workflow not in cached:
            cached[workflow] = model.task_encoding(row["text"])
        projection, coverage = cached[workflow]
        started = time.thread_time_ns()
        model.predict(projection, row["features"])
        support_status(model, row["features"], coverage)
        samples["cached_inference_and_support_us"].append((time.thread_time_ns() - started) / 1000.)
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("an actual-worker check input changed")
    write(args.output, {
        "initial_workflows_published": len(observed),
        "initial_probabilities_match_offline": True,
        "actual_background_thread_joined": True,
        "unchanged_input_validity": {
            "checked": args.check_unchanged_validity,
            "sampled_expiries": len(validity_cost), "revalidated_without_new_inference": revalidated,
            "inference_timestamp_and_probabilities_preserved": bool(revalidated),
            "thread_CPU_us_p50": float(np.quantile(validity_cost, .5)) if validity_cost else None,
            "thread_CPU_us_p99": float(np.quantile(validity_cost, .99)) if validity_cost else None,
        },
        "worker_counts": worker_counts,
        "component_cost": {key: {
            "count": len(values), "p50": float(np.quantile(values, .5)),
            "p99": float(np.quantile(values, .99)),
        } for key, values in samples.items()},
        "inputs_sha256": inputs, "GPU_action": False,
        "online_scheduler_ingest_lag_or_complete_control_cost_or_JCT_proven": False,
    })
    print(json.dumps({"published": len(observed), "counts": worker_counts,
                      "component_cost_p50_us": float(np.quantile(
                          samples["cached_inference_and_support_us"], .5))}), flush=True)


if __name__ == "__main__":
    main()
