"""Export causal snapshots and train with declared project or capture holdouts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import (
    FEATURES, SCHEMA, WorkflowProgress, WorkflowWorkModel, ngrams, task_requirements,
)
from beliefkv.runtime.replay_token_capture import decode_tokens


BOUNDARY_PROGRESS = "completed_request_boundaries_v1"
NATIVE_PROGRESS = "observed_native_decode_progress_128_v1"
SNAPSHOT_EVENTS = frozenset((
    Kind.WORKFLOW_START, Kind.LLM_SUBMIT, Kind.LLM_RESULT,
    Kind.JOIN_WAIT, Kind.JOIN_SATISFIED, Kind.TOOL_START, Kind.TOOL_END,
))


def progress_contract(dataset):
    value = dataset.get("progress_observation_contract", BOUNDARY_PROGRESS)
    if value not in (BOUNDARY_PROGRESS, NATIVE_PROGRESS):
        raise ValueError("unsupported workflow progress observation contract")
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def native_decode_progress(events, batches, output_lengths, *, quantum=128):
    """Use observed native output counts; final lengths only exclude terminal rows."""
    if type(quantum) is not int or quantum < 1:
        raise ValueError("native progress requires a positive observation quantum")
    submitted, completed = {}, {}
    for event in events:
        if event.kind not in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
            continue
        rid = event.attributes["request_id"]
        target = submitted if event.kind is Kind.LLM_SUBMIT else completed
        if rid in target:
            raise ValueError("duplicate native-progress source boundary")
        target[rid] = event
    if (
        not submitted or submitted.keys() != completed.keys()
        or not submitted.keys() <= output_lengths.keys()
        or any(type(output_lengths[rid]) is not int or output_lengths[rid] < 0 for rid in submitted)
    ):
        raise ValueError("native progress needs complete source calls and captured outputs")
    observed, published, seen_samples, domains, values = set(), Counter(), set(), set(), []
    previous = -math.inf
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        identity, now = batch.get("sample_id"), batch.get("complete_monotonic_ms")
        domain = batch.get("monotonic_clock_domain")
        if (
            type(identity) is not str or not identity or identity in seen_samples
            or type(now) not in (int, float) or not math.isfinite(now) or now < previous
            or type(domain) is not str or not domain
        ):
            raise ValueError("native progress has an invalid sample identity or clock")
        seen_samples.add(identity)
        domains.add(domain)
        previous = now
        for sample in batch.get("request_samples", ()):
            rid = sample.get("request_id")
            if rid not in submitted:
                continue
            begin, end = submitted[rid], completed[rid]
            if (
                any(sample.get(name) != getattr(begin, name) for name in (
                    "workflow_id", "invocation_id", "context_id", "context_epoch",
                ))
                or now < begin.ts_ms
            ):
                raise ValueError("native progress differs from observed source identity or time")
            observed.add(rid)
            if sample.get("phase", batch.get("phase")) != "decode":
                if now > end.ts_ms:
                    raise ValueError("native progress service follows source completion")
                continue
            before, delta = sample.get("output_tokens_before"), sample.get("token_delta")
            if (
                sample.get("token_delta_semantics") != "observed_output_ids_delta"
                or any(type(value) is not int or value < 0 for value in (before, delta))
            ):
                raise ValueError("native progress requires observed output-count deltas")
            count = before + delta
            if count > output_lengths[rid]:
                raise ValueError("observed native output exceeds the captured generation")
            # Overlapped batches can retain a completed row without new output.
            if now > end.ts_ms:
                if delta == 0:
                    continue
                raise ValueError("native progress service follows source completion")
            if (
                count == output_lengths[rid] or now == end.ts_ms
                or count - published[rid] < quantum
            ):
                continue
            published[rid] = count
            values.append({
                "ts_ms": now, "request_id": rid, "output_tokens": count,
                "workflow_id": begin.workflow_id, "sample_id": identity,
            })
    if len(domains) != 1 or observed != submitted.keys():
        raise ValueError("native progress lacks one clock domain or full request coverage")
    return values


def _native_progress_records(path):
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                raise ValueError("native progress requires a complete service journal")
            if b'"gpu_service_sample"' in line:
                yield json.loads(line)


def export_capture(capture, *, prefill_weight, native_progress=False):
    """Only completed same-context histories contribute past logical KV work."""
    clients = list(capture.glob("client_*/workflows"))
    if len(clients) != 1:
        raise ValueError("capture needs exactly one client workflow population")
    workflows, evidence, rejected = {}, {}, []
    for directory in sorted(clients[0].iterdir()):
        trajectory, events = directory / "trajectory.json", directory / "runtime_events.deepagents.jsonl"
        if not trajectory.exists() or not events.exists():
            continue
        result_path = directory / "result.json"
        result = json.loads(result_path.read_bytes()) if result_path.exists() else {}
        control = result.get("agent_control", {})
        interventions = sum(control.get(name, 0) for name in (
            "guard_intervened_completions", "guard_intervened_natural_language_returns",
            "forced_semantic_completions",
        ))
        evidence[str(result_path.resolve())] = sha256_file(result_path) if result_path.exists() else None
        if (
            result.get("natural_terminal") is not True or result.get("error")
            or interventions or result.get("semantic_gate_controlled_stop")
        ):
            rejected.append({
                "path": str(directory.resolve()), "natural_terminal": result.get("natural_terminal"),
                "error": result.get("error"), "interventions": interventions,
            })
            continue
        messages = json.loads(trajectory.read_bytes())
        first = next((row.get("content", "") for row in messages if row.get("type") == "human"), "")
        if "Project: " not in first or not task_requirements(first):
            raise ValueError("source lacks a user task and original project")
        project = first.split("Project: ", 1)[1].split("\n", 1)[0].strip()
        rows = [RuntimeEvent.from_dict(json.loads(line)) for line in events.open()]
        starts = [row for row in rows if row.kind is Kind.WORKFLOW_START]
        ends = [row for row in rows if row.kind is Kind.WORKFLOW_END]
        if len(starts) != 1 or len(ends) != 1:
            continue
        workflow = starts[0].workflow_id
        workflows[workflow] = (project, task_requirements(first), rows, sha256_file(events))
        evidence[str(events.resolve())] = sha256_file(events)
        evidence[str(trajectory.resolve())] = sha256_file(trajectory)
    # The token capture is ordered and delta-compressed. Only its recorded base
    # is used to decode bytes; the progress/label calculation uses causal events.
    prompts, outputs = {}, {}
    token_path = capture / "server/request_tokens.jsonl"
    for line in token_path.open():
        row = json.loads(line)
        if row["event"] == "request_prompt":
            base = prompts.get(row.get("base_request_id"))
            prompt = decode_tokens(row, base_tokens=base)
            prompts[row["request_id"]] = prompt
        elif row["event"] == "request_output":
            outputs[row["request_id"]] = decode_tokens(row)
    evidence[str(token_path.resolve())] = sha256_file(token_path)
    streamed = defaultdict(list)
    if native_progress:
        service_path = capture / "server/runtime_audit.jsonl"
        for observation in native_decode_progress(
            (event for _project, _text, events, _digest in workflows.values() for event in events),
            _native_progress_records(service_path),
            {rid: len(output) for rid, output in outputs.items()},
        ):
            streamed[observation["workflow_id"]].append(observation)
        evidence[str(service_path.resolve())] = sha256_file(service_path)
    result = []
    for workflow, (project, text, events, execution_digest) in workflows.items():
        progress, snapshots, completed = WorkflowProgress(), [], set()
        progress.text = text
        task_hash = hashlib.sha256(text.encode()).hexdigest()
        observations = [(event.ts_ms, i, event) for i, event in enumerate(events)]
        if native_progress:
            observations.extend(
                (item["ts_ms"], len(events) + i, item)
                for i, item in enumerate(streamed[workflow])
            )
            observations.sort(key=lambda item: item[:2])
        for _when, _order, event in observations:
            if isinstance(event, dict):
                progress.generated(event["request_id"], event["output_tokens"])
                snapshots.append({
                    "features": progress.features(),
                    "attained_work": progress.counts["input_tokens"] * prefill_weight
                                     + progress.counts["output_tokens"],
                    "event": "native_decode_progress", "observed_ts_ms": event["ts_ms"],
                    "observation_request_id": event["request_id"],
                    "observed_output_tokens": event["output_tokens"],
                })
                continue
            if event.kind is Kind.WORKFLOW_END:
                continue
            progress.event(event)
            request = event.attributes.get("request_id")
            if event.kind is Kind.LLM_SUBMIT:
                if request not in prompts:
                    raise ValueError("causal submit has no native token capture")
                progress.started(request, event.invocation_id, event.context_id, prompts[request])
            elif event.kind is Kind.LLM_RESULT:
                if request not in outputs or request in completed:
                    raise ValueError("source has missing or duplicate logical model result")
                completed.add(request)
                progress.completed(
                    request, event.invocation_id, event.context_id,
                    prompts[request], outputs[request],
                )
            if event.kind in SNAPSHOT_EVENTS:
                snapshots.append({
                    "features": progress.features(),
                    "attained_work": progress.counts["input_tokens"] * prefill_weight
                                     + progress.counts["output_tokens"],
                    "event": event.kind.value,
                })
        total = progress.counts["input_tokens"] * prefill_weight + progress.counts["output_tokens"]
        if not completed or any(call not in completed for call in progress.active_calls):
            raise ValueError("cannot label an incomplete logical workflow")
        for index, snapshot in enumerate(snapshots):
            result.append({
                "workflow": str(capture.resolve()) + ":" + workflow,
                "source_capture": str(capture.resolve()), "logical_workflow_id": workflow,
                "source_workflow_sha256": execution_digest,
                "project": project, "task_template": task_hash, "text": text,
                "snapshot": index, **snapshot, "total_work": total,
                "remaining_work": max(0., total - snapshot["attained_work"]),
            })
    return result, evidence, rejected


def fit(
    rows, calibration, *, epochs=300, seed=21, split_mode="project",
    hidden_width=64, vocabulary_size=4096, dropout=0., weight_decay=.03,
):
    import numpy as np
    import torch

    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    if not rows or not calibration:
        raise ValueError("training and calibration workflows are required")
    if split_mode not in ("project", "capture", "workflow"):
        raise ValueError("unknown workflow work split mode")
    if (
        type(hidden_width) is not int or not 4 <= hidden_width <= 256
        or type(vocabulary_size) is not int or not 1 <= vocabulary_size <= 16384
        or not math.isfinite(dropout) or not 0 <= dropout < 1
        or not math.isfinite(weight_decay) or weight_decay < 0
    ):
        raise ValueError("invalid workflow model regularization settings")
    if split_mode == "project" and {row["project"] for row in rows} & {row["project"] for row in calibration}:
        raise ValueError("training/calibration projects overlap")
    if split_mode == "project" and {row["task_template"] for row in rows} & {row["task_template"] for row in calibration}:
        raise ValueError("training/calibration task templates overlap")
    if {row["workflow"] for row in rows} & {row["workflow"] for row in calibration}:
        raise ValueError("training/calibration workflows overlap")
    if split_mode in ("capture", "workflow"):
        capture_split(rows, calibration, (), require_capture_disjoint=split_mode == "capture")
    texts = {row["workflow"]: row["text"] for row in rows}
    df = Counter()
    for text in texts.values():
        df.update(ngrams(text).keys())
    vocabulary = sorted(df, key=lambda word: (-df[word], word))[:vocabulary_size]
    index = {word: i for i, word in enumerate(vocabulary)}
    idf = np.asarray([math.log((1 + len(texts)) / (1 + df[word])) + 1 for word in vocabulary])

    def matrix(values):
        encoded = {}
        for row in values:
            if row["workflow"] in encoded:
                continue
            counts = ngrams(row["text"])
            vector = np.zeros(len(vocabulary), np.float32)
            for word, count in counts.items():
                if word in index:
                    vector[index[word]] = (1 + math.log(count)) * idf[index[word]]
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
            encoded[row["workflow"]] = vector
        return np.asarray([encoded[row["workflow"]] for row in values])

    totals = {row["workflow"]: row["total_work"] for row in rows}
    thresholds = np.quantile(list(totals.values()), (1 / 3, 2 / 3))
    if not 0 < thresholds[0] < thresholds[1]:
        raise ValueError("training population cannot define three distinct work classes")
    numeric = np.log1p(np.asarray([row["features"] for row in rows]))
    workflow_counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1. / workflow_counts[row["workflow"]] for row in rows])
    weights /= weights.sum()
    center = np.average(numeric, axis=0, weights=weights)
    scale = np.maximum(.25, np.sqrt(np.average((numeric - center) ** 2, axis=0, weights=weights)))
    task = torch.tensor(matrix(rows), dtype=torch.float32)
    dynamic = torch.tensor((numeric - center) / scale, dtype=torch.float32)
    target = torch.tensor(np.searchsorted(thresholds, [row["remaining_work"] for row in rows], side="right"))
    # Balance work classes inside each workflow, then restore equal workflow
    # totals. Long trajectories and their dense late/tool snapshots cannot
    # dominate either the text model or the training objective.
    labels = target.numpy()
    class_mass = np.asarray([weights[labels == i].sum() for i in range(3)])
    balanced = weights / np.maximum(class_mass[labels], 1e-6)
    totals_by_workflow = defaultdict(float)
    for row, value in zip(rows, balanced):
        totals_by_workflow[row["workflow"]] += value
    balanced = np.asarray([
        value / totals_by_workflow[row["workflow"]] / len(workflow_counts)
        for row, value in zip(rows, balanced)
    ])
    weight = torch.tensor(balanced, dtype=torch.float32)
    static_layer = torch.nn.Linear(len(vocabulary), hidden_width, bias=False)
    dynamic_layer = torch.nn.Linear(len(FEATURES), hidden_width)
    hidden_layer = torch.nn.Linear(hidden_width, max(4, hidden_width // 2))
    output_layer = torch.nn.Linear(max(4, hidden_width // 2), 3)
    parameters = list(static_layer.parameters()) + list(dynamic_layer.parameters())
    parameters += list(hidden_layer.parameters()) + list(output_layer.parameters())
    optimizer = torch.optim.AdamW(parameters, lr=.003, weight_decay=weight_decay)
    for _epoch in range(epochs):
        optimizer.zero_grad()
        hidden = torch.relu(static_layer(task) + dynamic_layer(dynamic))
        hidden = torch.nn.functional.dropout(hidden, p=dropout, training=True)
        logits = output_layer(torch.relu(hidden_layer(hidden)))
        loss = (torch.nn.functional.cross_entropy(logits, target, reduction="none") * weight).sum()
        loss.backward()
        optimizer.step()

    def array(tensor):
        return tensor.detach().numpy().tolist()

    raw = {
        "schema": SCHEMA, "features": list(FEATURES), "vocabulary": vocabulary,
        "idf": idf.tolist(), "center": center.tolist(), "scale": scale.tolist(),
        "static_weight": array(static_layer.weight.T),
        "dynamic_weight": array(dynamic_layer.weight.T), "bias": array(dynamic_layer.bias),
        "hidden_weight": array(hidden_layer.weight.T), "hidden_bias": array(hidden_layer.bias),
        "output_weight": array(output_layer.weight.T), "output_bias": array(output_layer.bias),
        "work_thresholds": thresholds.tolist(), "temperature": 1.,
        "metadata": {
            "calibration_status": "uncalibrated",
            "training_projects": sorted({row["project"] for row in rows}),
            "calibration_projects": sorted({row["project"] for row in calibration}),
            "training_workflows": len(totals), "seed": seed, "epochs": epochs,
            "training_parameters": {
                "hidden_width": hidden_width, "vocabulary_size": vocabulary_size,
                "dropout": dropout, "weight_decay": weight_decay,
            },
            "split_mode": split_mode,
            "validation_scope": (
                "unseen_projects" if split_mode == "project"
                else "known_projects_independent_captures_development" if split_mode == "capture"
                else "known_projects_independent_executions_development"
            ),
            "training_capture_paths": sorted({row.get("source_capture", "") for row in rows} - {""}),
            "calibration_capture_paths": sorted({row.get("source_capture", "") for row in calibration} - {""}),
            "loss_weighting": "class_balanced_with_equal_workflow_totals",
            "target": "same_context_logical_new_prefill_times_weight_plus_decode_tokens",
            "online_inputs": "task_instructions_and_observed_progress_only",
        },
    }
    model = WorkflowWorkModel(raw)
    # A single held-out temperature, with workflows equally weighted.
    predictions = probabilities(model, calibration)
    labels = np.searchsorted(thresholds, [row["remaining_work"] for row in calibration], side="right")
    logs = np.log(np.maximum(predictions, 1e-12))
    counts = Counter(row["workflow"] for row in calibration)
    weights = np.asarray([1 / counts[row["workflow"]] for row in calibration])
    weights /= weights.sum()
    from scipy.optimize import minimize_scalar

    def nll(log_temperature):
        scores = logs / math.exp(log_temperature)
        scores -= scores.max(axis=1, keepdims=True)
        return float(np.sum(weights * (
            np.log(np.exp(scores).sum(axis=1)) - scores[np.arange(len(labels)), labels]
        )))

    fitted = minimize_scalar(nll, bounds=(-1.6, 3.), method="bounded")
    raw["temperature"] = math.exp(float(fitted.x))
    raw["metadata"]["calibration_status"] = split_mode + "_heldout_temperature"
    raw["metadata"]["calibration_class_workflows"] = [
        len({row["workflow"] for row, label in zip(calibration, labels) if label == i})
        for i in range(3)
    ]
    raw["metadata"]["calibration_class_projects"] = [
        len({row["project"] for row, label in zip(calibration, labels) if label == i})
        for i in range(3)
    ]
    return raw


def probabilities(model, rows):
    import numpy as np

    projections = {}
    results = []
    for row in rows:
        if row["workflow"] not in projections:
            projections[row["workflow"]] = model.task_projection(row["text"])
        projection = projections[row["workflow"]]
        results.append(
            model.predict(projection, row["features"]) if projection is not None else (1 / 3,) * 3,
        )
    return np.asarray(results)


def evaluate(model, rows):
    import numpy as np

    scores = probabilities(model, rows)
    true = np.searchsorted(model.thresholds, [row["remaining_work"] for row in rows], side="right")
    predicted = scores.argmax(axis=1)
    counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1 / counts[row["workflow"]] for row in rows])
    weights /= weights.sum()
    confusion = np.zeros((3, 3))
    for left, right, weight in zip(true, predicted, weights):
        confusion[left, right] += weight
    f1 = [2 * confusion[i, i] / max(1e-12, confusion[i].sum() + confusion[:, i].sum()) for i in range(3)]
    support = model.metadata.get("calibration_class_projects", (0, 0, 0))
    used = (scores.max(axis=1) >= .6) & bool(len(support) == 3 and min(support) > 0)
    effective = np.where(used, predicted, 1)
    return {
        "workflows": len(counts), "projects": len({row["project"] for row in rows}),
        "snapshots": len(rows), "workflow_equal_weight_accuracy": float(np.sum(weights * (predicted == true))),
        "macro_f1": float(np.mean(f1)), "weighted_confusion": confusion.tolist(),
        "model_coverage_at_0_6": float(weights[used].sum()),
        "effective_bucket_distribution": [float(weights[effective == i].sum()) for i in range(3)],
        "long_to_short_rate": float(
            weights[(true == 2) & (effective == 0)].sum() / max(1e-12, weights[true == 2].sum()),
        ),
        "initial_accuracy": float(np.mean([
            predicted[i] == true[i] for i, row in enumerate(rows) if row["snapshot"] == 0
        ])),
        "paused_competition_quality": "requires_live_selection_snapshots_not_inferred_from_capture",
    }


def capture_split(training, calibration, test, *, require_capture_disjoint=True):
    """Permit shared projects/tasks; reject the same execution on either side."""
    groups = (training, calibration, test)
    captures, executions = [], []
    for rows in groups:
        if any(
            not row.get("source_capture") or not row.get("logical_workflow_id")
            or len(row.get("source_workflow_sha256", "")) != 64
            or any(c not in "0123456789abcdef" for c in row["source_workflow_sha256"])
            for row in rows
        ):
            raise ValueError("capture holdout requires source captures and complete execution fingerprints")
        captures.append({row["source_capture"] for row in rows})
        executions.append({row["source_workflow_sha256"] for row in rows})
    for i in range(3):
        for j in range(i):
            if require_capture_disjoint and captures[i] & captures[j]:
                raise ValueError("training/calibration/evaluation captures overlap")
            if executions[i] & executions[j]:
                raise ValueError("training/calibration/evaluation workflow executions overlap")
    return {
        "mode": "capture" if require_capture_disjoint else "workflow",
        "validation_scope": (
            "known_projects_independent_captures_development" if require_capture_disjoint
            else "known_projects_independent_executions_development"
        ),
        "training_capture_paths": sorted(captures[0]),
        "calibration_capture_paths": sorted(captures[1]),
        "evaluation_capture_paths": sorted(captures[2]),
        "training_execution_sha256": sorted(executions[0]),
        "calibration_execution_sha256": sorted(executions[1]),
        "evaluation_execution_sha256": sorted(executions[2]),
        "complete_captures_disjoint": not any(
            captures[i] & captures[j] for i in range(3) for j in range(i)
        ),
        "complete_workflows_disjoint": True,
        "project_disjoint_required": False, "unseen_project_generalization_claimed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    export = sub.add_parser("export")
    export.add_argument("--capture", type=Path, action="append", required=True)
    export.add_argument("--prefill-token-weight", type=float, required=True)
    export.add_argument("--native-progress", action="store_true")
    export.add_argument("--output", type=Path, required=True)
    train = sub.add_parser("fit")
    train.add_argument("--dataset", type=Path, required=True)
    train.add_argument("--evaluation-dataset", type=Path, required=True)
    train.add_argument("--output-dir", type=Path, required=True)
    train.add_argument("--epochs", type=int, default=300)
    train.add_argument("--split-mode", choices=("project", "capture", "workflow"), default="project")
    train.add_argument("--calibration-dataset", type=Path)
    train.add_argument("--hidden-width", type=int, default=64)
    train.add_argument("--vocabulary-size", type=int, default=4096)
    train.add_argument("--dropout", type=float, default=0.)
    train.add_argument("--weight-decay", type=float, default=.03)
    args = parser.parse_args()
    if args.mode == "export":
        if not math.isfinite(args.prefill_token_weight) or args.prefill_token_weight <= 0:
            raise ValueError("a frozen positive model-work conversion is required")
        values, evidence, rejections = [], {}, []
        for capture in args.capture:
            rows, bound, rejected = export_capture(
                capture, prefill_weight=args.prefill_token_weight,
                native_progress=args.native_progress,
            )
            values.extend(rows)
            evidence.update(bound)
            rejections.extend(rejected)
        write(args.output, {
            "schema": SCHEMA, "rows": values, "sources_sha256": evidence,
            "prefill_token_weight": args.prefill_token_weight,
            "work_unit": "explicit_logical_token_work_proxy_not_calibrated_GPU_seconds",
            "rejected_non_natural_workflows": rejections,
            "progress_observation_contract": (
                NATIVE_PROGRESS if args.native_progress else BOUNDARY_PROGRESS
            ),
        })
        print({"workflows": len({row["workflow"] for row in values}), "snapshots": len(values)})
        return
    dataset, evaluation = (
        json.loads(path.read_bytes()) for path in (args.dataset, args.evaluation_dataset)
    )
    if dataset["prefill_token_weight"] != evaluation["prefill_token_weight"]:
        raise ValueError("training and evaluation work units differ")
    observation_contract = progress_contract(dataset)
    if progress_contract(evaluation) != observation_contract:
        raise ValueError("training and evaluation progress observations differ")
    test = evaluation["rows"]
    excluded_projects = {row["project"] for row in test}
    excluded_templates = {row["task_template"] for row in test}
    if args.split_mode in ("capture", "workflow"):
        if args.calibration_dataset is None:
            raise ValueError("execution holdout requires a separate calibration dataset")
        calibrated = json.loads(args.calibration_dataset.read_bytes())
        if calibrated["prefill_token_weight"] != dataset["prefill_token_weight"]:
            raise ValueError("calibration work units differ")
        if progress_contract(calibrated) != observation_contract:
            raise ValueError("training and calibration progress observations differ")
        training, calibration = dataset["rows"], calibrated["rows"]
        split_contract = capture_split(
            training, calibration, test, require_capture_disjoint=args.split_mode == "capture",
        )
    else:
        if args.calibration_dataset is not None:
            raise ValueError("separate capture calibration requires explicit capture mode")
        eligible = [
            row for row in dataset["rows"]
            if row["project"] not in excluded_projects and row["task_template"] not in excluded_templates
        ]
        projects = sorted({row["project"] for row in eligible}, key=lambda value: hashlib.sha256(value.encode()).digest())
        if len(projects) < 4:
            raise ValueError("fewer than four eligible projects after full evaluation exclusion")
        project_work = {
            project: sorted({
                row["workflow"]: row["total_work"] for row in eligible if row["project"] == project
            }.values())
            for project in projects
        }
        ordered_projects = sorted(
            projects, key=lambda project: (project_work[project][len(project_work[project]) // 2], project),
        )
        calibration_projects = {ordered_projects[0], ordered_projects[-1]}
        training = [row for row in eligible if row["project"] not in calibration_projects]
        calibration = [row for row in eligible if row["project"] in calibration_projects]
        split_contract = {
            "mode": "project", "validation_scope": "unseen_projects",
            "project_and_task_template_disjoint": True,
        }
    raw = fit(
        training, calibration, epochs=args.epochs, split_mode=args.split_mode,
        hidden_width=args.hidden_width, vocabulary_size=args.vocabulary_size,
        dropout=args.dropout, weight_decay=args.weight_decay,
    )
    raw["metadata"]["validation_scope"] = split_contract["validation_scope"]
    raw["metadata"].update({
        "prefill_token_weight": dataset["prefill_token_weight"],
        "training_dataset_sha256": sha256_file(args.dataset),
        "progress_observation_contract": observation_contract,
        "evaluation_excluded_projects": sorted(excluded_projects) if args.split_mode == "project" else [],
        "evaluation_excluded_task_templates": sorted(excluded_templates) if args.split_mode == "project" else [],
        "split_contract": split_contract,
        "calibration_dataset_sha256": (
            sha256_file(args.calibration_dataset) if args.calibration_dataset else None
        ),
    })
    # Deployment checks use calibration executions only. Evaluation labels
    # cannot turn ranking on or select a checkpoint.
    calibration_model = WorkflowWorkModel(raw)
    calibration_metrics = evaluate(calibration_model, calibration)
    reasons = []
    if calibration_metrics["macro_f1"] < .5:
        reasons.append("calibration_macro_f1_below_0_50")
    if calibration_metrics["long_to_short_rate"] > .1:
        reasons.append("calibration_long_to_short_above_0_10")
    if min(raw["metadata"]["calibration_class_projects"]) <= 0:
        reasons.append("missing_calibration_class_project_support")
    raw["metadata"]["decision_eligible"] = not reasons
    raw["metadata"]["decision_rejection_reasons"] = reasons
    raw["metadata"]["calibration_decision_metrics"] = calibration_metrics
    model = WorkflowWorkModel(raw)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(args.output_dir / "model.json", raw)
    write(args.output_dir / "evaluation.json", {
        "training_workflows": len({row["workflow"] for row in training}),
        "training_projects": sorted({row["project"] for row in training}),
        "calibration_projects": sorted({row["project"] for row in calibration}),
        "test_projects": sorted(excluded_projects),
        "project_and_task_template_disjoint": args.split_mode == "project",
        "split_contract": split_contract,
        "all_snapshots_of_a_workflow_kept_together": True,
        "train": evaluate(model, training), "calibration": evaluate(model, calibration),
        "test": evaluate(model, test),
        "deployed_bucket_distribution": (
            evaluate(model, test)["effective_bucket_distribution"] if not reasons else [0., 1., 0.]
        ),
        "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "dataset_sha256": sha256_file(args.dataset),
        "evaluation_dataset_sha256": sha256_file(args.evaluation_dataset),
        "performance_accepted": False, "GPU_experiment_started": False,
    })
    print(json.dumps(evaluate(model, test), indent=2))


if __name__ == "__main__":
    main()
