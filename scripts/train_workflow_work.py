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
    CONTEXT_SCHEMA, R5_SCHEMA, SCHEMA, WorkflowProgress, WorkflowWorkModel, feature_names, ngrams,
    task_requirements, work_prediction_features,
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


def export_capture(capture, *, prefill_weight, native_progress=False, feature_schema=SCHEMA):
    """Only completed same-context histories contribute past logical KV work."""
    feature_names(feature_schema)
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
                    "features": progress.features(feature_schema=feature_schema),
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
                    "features": progress.features(feature_schema=feature_schema),
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
    feature_schema=SCHEMA, task_encoder=None, training_weighting=None, text_encoding=None,
    observed_delegations=False, work_thresholds=None, ranking_loss_weight=0.,
    task_only_before_work=False, ranking_pair_mode="phase_matched", ordinal_loss_weight=0.,
    numeric_feature_transform=None, remaining_work_loss_weight=0., decision_observation_mass=0.,
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
    names = feature_names(feature_schema)
    from beliefkv.predictor.workflow_work import OBSERVED_RATIOS, work_numeric_features

    if any(len(row["features"]) != len(names) for row in (*rows, *calibration)):
        raise ValueError("workflow training features differ from the declared schema")
    if split_mode not in ("project", "capture", "workflow"):
        raise ValueError("unknown workflow work split mode")
    if (
        type(hidden_width) is not int or not 4 <= hidden_width <= 256
        or type(vocabulary_size) is not int or not 1 <= vocabulary_size <= 16384
        or not math.isfinite(dropout) or not 0 <= dropout < 1
        or not math.isfinite(weight_decay) or weight_decay < 0
        or type(ranking_loss_weight) not in (int, float)
        or not math.isfinite(ranking_loss_weight) or not 0 <= ranking_loss_weight <= 2.
        or type(task_only_before_work) is not bool
        or task_only_before_work and feature_schema != R5_SCHEMA
        or ranking_pair_mode not in ("phase_matched", "ready_mixed_progress")
        or ranking_pair_mode != "phase_matched" and (
            feature_schema != R5_SCHEMA or not ranking_loss_weight
        )
        or type(ordinal_loss_weight) not in (int, float)
        or not math.isfinite(ordinal_loss_weight) or not 0 <= ordinal_loss_weight <= 2.
        or ordinal_loss_weight and feature_schema != R5_SCHEMA
        or numeric_feature_transform not in (None, OBSERVED_RATIOS)
        or numeric_feature_transform is not None and feature_schema != R5_SCHEMA
        or type(remaining_work_loss_weight) not in (int, float)
        or not math.isfinite(remaining_work_loss_weight) or not 0 <= remaining_work_loss_weight <= 2.
        or remaining_work_loss_weight and feature_schema != R5_SCHEMA
        or type(decision_observation_mass) not in (int, float)
        or not math.isfinite(decision_observation_mass) or not 0 <= decision_observation_mass <= .75
        or decision_observation_mass and feature_schema != R5_SCHEMA
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
    text_key = lambda row: (row["workflow"], row["text"])
    texts = {text_key(row): row["text"] for row in rows}
    from beliefkv.predictor.workflow_work import task_terms

    semantic = None
    if task_encoder is not None:
        from beliefkv.predictor.workflow_task_encoder import WorkflowTaskEncoder

        if feature_schema != R5_SCHEMA:
            raise ValueError("semantic text training requires the R5 schema")
        semantic = WorkflowTaskEncoder(task_encoder)
    df = Counter()
    if semantic is None:
        if observed_delegations:
            terms_by_workflow = defaultdict(set)
            for (workflow, _text), text in texts.items():
                terms_by_workflow[workflow].update(task_terms(text, text_encoding).keys())
            for terms in terms_by_workflow.values():
                df.update(terms)
        else:
            for text in texts.values():
                df.update(task_terms(text, text_encoding).keys())
    vocabulary = sorted(df, key=lambda word: (-df[word], word))[:vocabulary_size]
    index = {word: i for i, word in enumerate(vocabulary)}
    documents = len({row["workflow"] for row in rows}) if observed_delegations else len(texts)
    idf = np.asarray([math.log((1 + documents) / (1 + df[word])) + 1 for word in vocabulary])

    def matrix(values):
        encoded = {}
        for row in values:
            key = text_key(row)
            if key in encoded:
                continue
            if semantic is not None:
                encoded[key] = semantic.encode(row["text"])[0]
                continue
            counts = task_terms(row["text"], text_encoding)
            vector = np.zeros(len(vocabulary), np.float32)
            for word, count in counts.items():
                if word in index:
                    vector[index[word]] = (1 + math.log(count)) * idf[index[word]]
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
            encoded[key] = vector
        return np.asarray([encoded[text_key(row)] for row in values])

    totals = {row["workflow"]: row["total_work"] for row in rows}
    thresholds = (
        np.quantile(list(totals.values()), (1 / 3, 2 / 3))
        if work_thresholds is None else np.asarray(work_thresholds, dtype=float)
    )
    if thresholds.shape != (2,) or not np.isfinite(thresholds).all():
        raise ValueError("fixed work thresholds require two finite boundaries")
    if not 0 < thresholds[0] < thresholds[1]:
        raise ValueError("training population cannot define three distinct work classes")
    numeric = np.log1p(np.asarray([
        work_numeric_features(
            work_prediction_features(row["features"], task_only_before_work=task_only_before_work),
            numeric_feature_transform,
        )
        for row in rows
    ]))
    workflow_counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1. / workflow_counts[row["workflow"]] for row in rows])
    weights /= weights.sum()
    center = np.average(numeric, axis=0, weights=weights)
    scale = np.maximum(.25, np.sqrt(np.average((numeric - center) ** 2, axis=0, weights=weights)))
    task_indices = None
    if feature_schema == R5_SCHEMA:
        unique = {text_key(row): row for row in rows}
        ordered = list(unique)
        positions = {workflow: index for index, workflow in enumerate(ordered)}
        task = torch.tensor(matrix(list(unique.values())), dtype=torch.float32)
        task_indices = torch.tensor([positions[text_key(row)] for row in rows])
    else:
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
    if training_weighting is not None:
        from beliefkv.predictor.workflow_work_training import (
            PHASE_BALANCED, READY_BALANCED, phase_weights, ready_weights,
        )

        if training_weighting == PHASE_BALANCED:
            balanced = phase_weights(rows)
        elif training_weighting == READY_BALANCED and feature_schema == R5_SCHEMA:
            balanced = ready_weights(rows, balanced)
        else:
            raise ValueError("unknown workflow training weighting")
    if decision_observation_mass:
        from beliefkv.predictor.workflow_work_training import decision_observation_weights

        balanced = decision_observation_weights(rows, balanced, decision_observation_mass)
    weight = torch.tensor(balanced, dtype=torch.float32)
    static_layer = torch.nn.Linear(task.shape[1], hidden_width, bias=False)
    dynamic_layer = torch.nn.Linear(numeric.shape[1], hidden_width)
    hidden_layer = torch.nn.Linear(hidden_width, max(4, hidden_width // 2))
    output_layer = torch.nn.Linear(max(4, hidden_width // 2), 3)
    parameters = list(static_layer.parameters()) + list(dynamic_layer.parameters())
    parameters += list(hidden_layer.parameters()) + list(output_layer.parameters())
    work_head, work_target, work_normalization = None, None, None
    if remaining_work_loss_weight:
        from beliefkv.predictor.workflow_work_training import continuous_work_targets

        work_values, work_normalization = continuous_work_targets(rows, balanced)
        work_target = torch.tensor(work_values, dtype=torch.float32)
        # Preserve primary initialization and dropout RNG when adding supervision.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed + 1)
            work_head = torch.nn.Linear(max(4, hidden_width // 2), 1)
        parameters += list(work_head.parameters())
    optimizer = torch.optim.AdamW(parameters, lr=.003, weight_decay=weight_decay)
    ranking = None
    if ranking_loss_weight:
        from beliefkv.predictor.workflow_work_training import work_ranking_pairs

        pairs = np.asarray(work_ranking_pairs(rows, seed=seed, mode=ranking_pair_mode))
        ranking = (
            torch.tensor(pairs[:, 0], dtype=torch.long),
            torch.tensor(pairs[:, 1], dtype=torch.long),
            torch.tensor(pairs[:, 2], dtype=torch.float32),
            torch.tensor(pairs[:, 3] / pairs[:, 3].sum(), dtype=torch.float32),
        )
    for _epoch in range(epochs):
        optimizer.zero_grad()
        static = static_layer(task)
        hidden = torch.relu((static if task_indices is None else static[task_indices]) + dynamic_layer(dynamic))
        hidden = torch.nn.functional.dropout(hidden, p=dropout, training=True)
        representation = torch.relu(hidden_layer(hidden))
        logits = output_layer(representation)
        loss = (torch.nn.functional.cross_entropy(logits, target, reduction="none") * weight).sum()
        if work_head is not None:
            loss = loss + remaining_work_loss_weight * (
                torch.nn.functional.smooth_l1_loss(
                    work_head(representation).squeeze(1), work_target, reduction="none",
                ) * weight
            ).sum()
        if ordinal_loss_weight:
            cumulative = logits.softmax(1).cumsum(1)[:, :2]
            truth = (target[:, None] <= torch.arange(2)[None, :]).float()
            loss = loss + ordinal_loss_weight * (
                (cumulative - truth).square().mean(1) * weight
            ).sum()
        if ranking is not None:
            left, right, direction, pair_weight = ranking
            utility = logits.softmax(1) @ torch.tensor((1., .5, .25))
            margin = 8. * direction * (utility[left] - utility[right])
            loss = loss + ranking_loss_weight * (
                torch.nn.functional.softplus(-margin) * pair_weight
            ).sum()
        loss.backward()
        optimizer.step()

    def array(tensor):
        return tensor.detach().numpy().tolist()

    raw = {
        "schema": feature_schema, "features": list(names), "vocabulary": vocabulary,
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
    if task_encoder is not None:
        raw["task_encoder"] = task_encoder
        raw["metadata"]["task_training_encoding"] = "frozen_cpu_semantics_explicit_instructions_only"
    if observed_delegations:
        raw["observed_delegation_inputs"] = True
    if text_encoding is not None:
        raw["task_text_encoding"] = text_encoding
    if training_weighting is not None:
        raw["metadata"]["loss_weighting"] = training_weighting
    if work_thresholds is not None:
        raw["metadata"]["work_threshold_source"] = "explicit_frozen_reference_thresholds"
    if task_only_before_work:
        raw["task_only_before_observed_work"] = True
        raw["metadata"]["initial_feature_contract"] = (
            "task_only_until_positive_coarse_observed_work_or_observed_child"
        )
    if numeric_feature_transform is not None:
        from beliefkv.predictor.workflow_work import RATIO_FEATURES

        raw["numeric_feature_transform"] = numeric_feature_transform
        raw["metadata"]["derived_numeric_features"] = {
            "names": list(RATIO_FEATURES), "observed_R5_counters_only": True,
            "new_events_or_prompt_inputs": False,
        }
    if ranking is not None:
        raw["metadata"]["training_parameters"]["ranking_loss_weight"] = ranking_loss_weight
        raw["metadata"]["ranking_training"] = {
            "kind": "phase_matched_cross_workflow_probability_utility_pairs",
            "pairs": len(pairs), "offline_labels_only": True,
            "runtime_rank_weight": [1., .5, .25], "new_online_features": False,
        }
        if ranking_pair_mode != "phase_matched":
            raw["metadata"]["ranking_training"]["kind"] = (
                "phase_first_request_and_cross_progress_ready_probability_utility_pairs"
            )
            raw["metadata"]["ranking_training"]["pair_mode"] = ranking_pair_mode
            raw["metadata"]["ranking_training"]["equal_stratum_mass"] = True
    if ordinal_loss_weight:
        raw["metadata"]["training_parameters"]["ordinal_loss_weight"] = ordinal_loss_weight
        raw["metadata"]["ordinal_training"] = {
            "kind": "ranked_probability_score", "offline_labels_only": True,
            "new_online_features": False,
        }
    if work_head is not None:
        raw["metadata"]["training_parameters"]["remaining_work_loss_weight"] = remaining_work_loss_weight
        raw["metadata"]["continuous_work_supervision"] = {
            "kind": "standardized_log_remaining_work_smooth_l1",
            "training_normalization": work_normalization,
            "offline_labels_only": True, "auxiliary_head_exported": False,
            "online_model_dimensions_unchanged": True,
        }
    if decision_observation_mass:
        from beliefkv.predictor.workflow_work_training import DECISION_OBSERVATION

        raw["metadata"]["training_parameters"]["decision_observation_mass"] = decision_observation_mass
        raw["metadata"]["decision_observation_training"] = {
            "kind": DECISION_OBSERVATION,
            "complete_workflow_mass_preserved": True,
            "observed_states": sum(
                row.get("training_observation_kind") == DECISION_OBSERVATION for row in rows
            ),
            "new_online_features": False,
        }
    if feature_schema == R5_SCHEMA:
        from beliefkv.predictor.workflow_work_distribution import training_contract

        raw["distribution_contract"] = training_contract(rows)
        raw["metadata"]["online_inputs"] = "explicit_task_delegation_metadata_and_observed_progress_only"
        raw["metadata"]["task_training_encoding"] = (
            (
                "one_static_projection_per_workflow_per_epoch"
                if len(texts) == len(workflow_counts) else "one_projection_per_observed_text_version_per_epoch"
            ) if semantic is None
            else "frozen_cpu_semantics_one_static_projection_per_workflow_per_epoch"
        )
    model = WorkflowWorkModel(raw)
    # A single held-out temperature, with workflows equally weighted.
    predictions = probabilities(model, calibration)
    labels = np.searchsorted(thresholds, [row["remaining_work"] for row in calibration], side="right")
    logs = np.log(np.maximum(predictions, 1e-12))
    counts = Counter(row["workflow"] for row in calibration)
    weights = np.asarray([1 / counts[row["workflow"]] for row in calibration])
    weights /= weights.sum()
    from beliefkv.predictor.workflow_work_training import (
        PHASE_BALANCED, READY_BALANCED, phase_weights, ready_weights,
    )

    if training_weighting == PHASE_BALANCED:
        weights = phase_weights(calibration)
    elif training_weighting == READY_BALANCED:
        weights = ready_weights(calibration, weights)
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


def model_text(model, row):
    return row["text"] if model.observed_delegations else row.get("task_text", row["text"])


def probabilities(model, rows):
    import numpy as np

    projections = {}
    results = []
    for row in rows:
        text = model_text(model, row)
        key = row["workflow"], text
        if key not in projections:
            projections[key] = model.task_projection(text)
        projection = projections[key]
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
    export.add_argument("--feature-schema", choices=(SCHEMA, CONTEXT_SCHEMA), default=SCHEMA)
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
                feature_schema=args.feature_schema,
            )
            values.extend(rows)
            evidence.update(bound)
            rejections.extend(rejected)
        write(args.output, {
            "schema": args.feature_schema, "rows": values, "sources_sha256": evidence,
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
    feature_schema = dataset.get("schema", SCHEMA)
    feature_names(feature_schema)
    if evaluation.get("schema", SCHEMA) != feature_schema:
        raise ValueError("training and evaluation feature schemas differ")
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
        if calibrated.get("schema", SCHEMA) != feature_schema:
            raise ValueError("training and calibration feature schemas differ")
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
        feature_schema=feature_schema,
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
