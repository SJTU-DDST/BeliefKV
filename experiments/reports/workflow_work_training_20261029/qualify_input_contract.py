"""Bind existing weights to their actual inputs and replay native root ingress on CPU."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "experiments/reports/workflow_work_training_20261029"
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import (
    EXECUTION_INPUT_KIND, WorkflowWorkModel, execution_control_text,
    execution_controls_from_environment,
)
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from scripts.prepare_workflow_size_candidate import prepare
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import (
    NATIVE_PROGRESS, SNAPSHOT_EVENTS, _native_progress_records, evaluate,
    native_decode_progress, progress_contract, write,
)


def qualify_progress_observations(
    worker, events, observations, corpus, offline, model, *, tokenizer=None,
):
    """Check real Worker operations at each observation, without cache timing claims."""
    checks, position, predictions, task_submitted = Counter(), 0, 0, False
    sequence = [(event.ts_ms, i, event) for i, event in enumerate(events)]
    sequence.extend(
        (row["ts_ms"], len(events) + i, row) for i, row in enumerate(observations)
    )
    sequence.sort(key=lambda item: item[:2])
    workflow = events[0].workflow_id
    for _when, _order, item in sequence:
        if isinstance(item, dict):
            worker.submit(workflow, "generated", item["request_id"], item["output_tokens"])
            kind = "native_decode_progress"
        else:
            worker.submit(workflow, "event", item)
            request = item.attributes.get("request_id")
            if item.kind is Kind.LLM_SUBMIT:
                value = corpus.get(request)
                if not task_submitted:
                    worker.submit(workflow, "task", tuple(value.prompt_tokens[-8192:]), tokenizer)
                    task_submitted = True
                worker.submit(
                    workflow, "started", request, item.invocation_id, item.context_id,
                    value.prompt_tokens,
                )
            elif item.kind is Kind.LLM_RESULT:
                value = corpus.get(request)
                worker.submit(
                    workflow, "completed", request, item.invocation_id, item.context_id,
                    value.prompt_tokens, value.output_tokens,
                )
            kind = item.kind.value
        worker.process_pending()
        if not isinstance(item, dict) and item.kind not in SNAPSHOT_EVENTS:
            continue
        if position >= len(offline):
            raise ValueError("progress qualification lacks an offline observation")
        expected = offline[position]
        position += 1
        if kind != expected["event"]:
            raise ValueError("native and offline progress observation order differs")
        if isinstance(item, dict) and (
            item["request_id"] != expected["observation_request_id"]
            or item["output_tokens"] != expected["observed_output_tokens"]
            or item["ts_ms"] != expected["observed_ts_ms"]
        ):
            raise ValueError("offline progress differs from the native observation")
        progress = worker._progress[workflow]
        np.testing.assert_array_equal(
            progress.features(feature_schema=model.schema), expected["features"],
        )
        if progress.text:
            if worker.task_prefix + progress.text != expected["text"]:
                raise ValueError("streaming Worker task differs from training input")
            projection = model.task_projection(expected["text"])
            np.testing.assert_array_equal(worker._projections[workflow], projection)
            np.testing.assert_array_equal(
                worker.prediction(workflow).probabilities,
                model.predict(projection, expected["features"]),
            )
            predictions += 1
        elif task_submitted:
            raise ValueError("streaming Worker task unavailable after real ingress")
        checks[kind] += 1
    if position != len(offline):
        raise ValueError("progress qualification left offline observations unchecked")
    return {"events": dict(checks), "prediction_checks": predictions}


def main():
    from transformers import AutoTokenizer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prototype-dir", type=Path, default=HERE / "profile_text_r1")
    parser.add_argument(
        "--base-configuration", type=Path,
        default=ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    base = args.base_configuration
    prototype = args.prototype_dir
    model_path, report_path = prototype / "fit/model.json", prototype / "fit/evaluation.json"
    raw, old_report = (json.loads(path.read_bytes()) for path in (model_path, report_path))
    if old_report["model_sha256"] != sha256_file(model_path):
        raise ValueError("original model and report fingerprints differ")
    data_paths = {name: prototype / (name + ".json") for name in ("training", "calibration", "evaluation")}
    data = {name: json.loads(path.read_bytes()) for name, path in data_paths.items()}
    expected = {
        "training": raw["metadata"]["training_dataset_sha256"],
        "calibration": raw["metadata"]["calibration_dataset_sha256"],
        "evaluation": old_report["evaluation_dataset_sha256"],
    }
    if any(sha256_file(data_paths[name]) != digest for name, digest in expected.items()):
        raise ValueError("fitted model input dataset changed")
    for dataset in data.values():
        profiles = dataset["model_input_contract"]["profiles"]
        for profile in profiles.values():
            if profile["text_prefix"] != execution_control_text(profile["known_execution_controls"]):
                raise ValueError("training prefix differs from the runtime formatter")
        if any(not row["text"].startswith(profiles[row["source_capture"]]["text_prefix"])
               for row in dataset["rows"]):
            raise ValueError("recorded model input lacks its declared prefix")
    env = json.loads(base.read_bytes())["common_environment"]
    controls = execution_controls_from_environment(env)
    profiles = data["evaluation"]["model_input_contract"]["profiles"]
    if len(profiles) != 1 or next(iter(profiles.values()))["known_execution_controls"] != controls:
        raise ValueError("deployment controls differ from the declared evaluation workload")
    bound = {
        str(path.resolve()): sha256_file(path)
        for path in (model_path, report_path, base, *data_paths.values())
    }
    original_arrays = {key: value for key, value in raw.items() if key != "metadata"}
    raw["metadata"]["task_input_contract"] = {
        "kind": EXECUTION_INPUT_KIND, "deployment_controls": controls,
    }
    raw["metadata"]["input_binding_provenance"] = {
        "original_model_sha256": sha256_file(model_path),
        "numeric_weights_and_temperature_changed": False,
        "inputs_sha256": bound,
    }
    target = args.output_dir / "model.json"
    write(target, raw)
    model = WorkflowWorkModel.load(target, sha256_file(target))
    assert original_arrays == {key: value for key, value in model.raw.items() if key != "metadata"}
    for name in ("calibration", "evaluation"):
        metric = evaluate(model, data[name]["rows"])
        if metric != old_report["calibration" if name == "calibration" else "test"]:
            raise ValueError("metadata binding changed numeric predictions")
    source = Path(next(iter(profiles)))
    plan, expected_tokens, source_evidence = source_plan(source)
    tokenizer = AutoTokenizer.from_pretrained(env["MODEL_PATH"], local_files_only=True)
    grouped = {}
    for row in data["evaluation"]["rows"]:
        if row["event"] == "llm_submit":
            grouped.setdefault(row["logical_workflow_id"], row)
    checks = []
    worker = WorkflowWorkWorker(
        str(target), sha256_file(target), autostart=False, execution_environment=env,
    )
    try:
        with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected_tokens) as corpus:
            for path in sorted(source.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
                events = [RuntimeEvent.from_dict(json.loads(line)) for line in path.open()]
                first = next(event for event in events if event.kind is Kind.LLM_SUBMIT)
                value = corpus.get(first.attributes["request_id"])
                for event in events[:events.index(first) + 1]:
                    worker.submit(event.workflow_id, "event", event)
                worker.submit(first.workflow_id, "task", tuple(value.prompt_tokens[-8192:]), tokenizer)
                worker.submit(
                    first.workflow_id, "started", first.attributes["request_id"],
                    first.invocation_id, first.context_id, value.prompt_tokens,
                )
                worker.process_pending()
                observed = worker._progress[first.workflow_id]
                offline = grouped[first.workflow_id]
                if worker.task_prefix + observed.text != offline["text"]:
                    raise ValueError("real ingress task text differs from training input")
                np.testing.assert_array_equal(
                    observed.features(feature_schema=model.schema), offline["features"],
                )
                projection = model.task_projection(offline["text"])
                np.testing.assert_array_equal(worker._projections[first.workflow_id], projection)
                expected_probabilities = model.predict(projection, offline["features"])
                np.testing.assert_array_equal(
                    worker.prediction(first.workflow_id).probabilities, expected_probabilities,
                )
                checks.append({
                    "workflow_id": first.workflow_id,
                    "request_id": first.attributes["request_id"], "prompt_tokens": len(value.prompt_tokens),
                    "task_input_sha256": hashlib.sha256(offline["text"].encode()).hexdigest(),
                    "text_features_projection_probabilities_exact": True,
                })
            source_evidence["tokens"] = corpus.evidence
        if len(checks) != old_report["test"]["workflows"]:
            raise ValueError("root ingress checks omit a workflow")
        status = worker.status()
    finally:
        worker.close()
    streaming_parity = None
    if progress_contract(data["evaluation"]) == NATIVE_PROGRESS:
        grouped_rows = defaultdict(list)
        for row in data["evaluation"]["rows"]:
            grouped_rows[row["logical_workflow_id"]].append(row)
        workflows = []
        for path in sorted(source.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
            events = [RuntimeEvent.from_dict(json.loads(line)) for line in path.open()]
            if events[0].workflow_id in grouped_rows:
                workflows.append(events)
        native_path = source / "server/runtime_audit.jsonl"
        bound[str(native_path.resolve())] = sha256_file(native_path)
        output_lengths = {rid: usage[1] for rid, usage in plan.request_usage.items()}
        observations = defaultdict(list)
        for row in native_decode_progress(
            (event for events in workflows for event in events),
            _native_progress_records(native_path), output_lengths,
        ):
            observations[row["workflow_id"]].append(row)
        worker = WorkflowWorkWorker(
            str(target), sha256_file(target), autostart=False, execution_environment=env,
        )
        try:
            snapshot_checks, prediction_checks = Counter(), 0
            with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected_tokens) as corpus:
                for events in workflows:
                    workflow = events[0].workflow_id
                    checked = qualify_progress_observations(
                        worker, events, observations[workflow], corpus,
                        grouped_rows[workflow], model, tokenizer=tokenizer,
                    )
                    snapshot_checks.update(checked["events"])
                    prediction_checks += checked["prediction_checks"]
            if sum(snapshot_checks.values()) != len(data["evaluation"]["rows"]):
                raise ValueError("streaming parity omits recorded snapshots")
            streaming_parity = {
                "workflow_checks": len(workflows), "snapshot_checks": dict(snapshot_checks),
                "prediction_checks": prediction_checks,
                "worker_status": worker.status(),
                "features_projection_probabilities_exact": True,
                "actual_native_counts_used": True,
                "background_200ms_delivery_timing_emulated": False,
                "completion_callback_timing": "client_result_boundary",
                "scope": "CPU observation delivery semantics; not exact online cache timing",
            }
        finally:
            worker.close()
    if any(sha256_file(Path(path)) != digest for path, digest in bound.items()):
        raise ValueError("qualification inputs changed")
    parity_path = args.output_dir / "parity.json"
    write(parity_path, {
        "checks": checks, "worker_status": status, "source_evidence": source_evidence,
        "inputs_sha256": bound, "model_sha256": sha256_file(target),
        "numeric_model_unchanged": True, "all_probability_and_input_checks_exact": True,
        "streaming_progress_parity": streaming_parity,
        "scope": "CPU real-root-token ingress; not background-cache timing or GPU performance",
        "GPU_experiment_started": False,
    })
    report = deepcopy(old_report)
    report.update(
        model_sha256=sha256_file(target),
        task_input_contract=raw["metadata"]["task_input_contract"],
        input_contract_qualification={"path": str(parity_path.resolve()), "sha256": sha256_file(parity_path)},
    )
    evaluation = args.output_dir / "evaluation.json"
    write(evaluation, report)
    candidate = prepare(base, target, evaluation)
    write(args.output_dir / "candidate.json", candidate)
    print(json.dumps({
        "root_checks": len(checks), "exact": True, "model": str(target),
        "task_input_kind": model.task_input_kind,
        "worker_thread_cpu_us": status["counts"]["worker_thread_cpu_us"],
        "GPU_experiment_started": False,
    }, indent=2))


if __name__ == "__main__":
    main()
