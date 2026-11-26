#!/usr/bin/env python3
"""Evaluate per-call tool return rates on repository- and source-disjoint traces."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.predictor.contextual_tool_timing import (
    COMMAND_FEATURE_COUNT, INTERVAL_STARTS, DelayedBrierWeights, ToolCallHistory,
    ToolReturnDistribution, call_features, mixture_quantile,
)
from beliefkv.core.events import RuntimeEvent
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.predictor.tool_survival import KaplanMeierCurve
from beliefkv.predictor.tool_timing_prior import tool_signature
from beliefkv.runtime.replay_token_capture import decode_tokens


LANDMARKS = (0., .1, .5, 1., 2., 5.)
HORIZONS = (.4, 1., 2.)
MINIMUM_EXPOSURE_S = .001


@dataclass(frozen=True)
class Call:
    project: str
    workflow: str
    features: tuple
    duration: float
    returned: bool
    identity: tuple = ()
    capture: str = ""
    start_s: float = 0.


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _call_key(event):
    attrs = event["attributes"]
    identity = attrs.get("tool_run_id") or attrs.get("tool_call_id")
    if not identity:
        raise ValueError("tool event missing call identity")
    return event["workflow_id"], event["invocation_id"], identity


def _input_digest(args):
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str, ensure_ascii=True).encode(),
    ).hexdigest()


def recover_commands(source, nodes, starts, manifest, tokenizer_path):
    """Bind actual arguments to both the predecessor request and start input digest."""
    from sglang.srt.function_call.qwen3_coder_detector import Qwen3CoderDetector
    from tokenizers import Tokenizer

    token_path = source / "server/request_tokens.jsonl"
    token_sha256 = _sha256(token_path)
    if token_sha256 != manifest["artifacts_sha256"]["server/request_tokens.jsonl"]:
        raise ValueError("source token artifact changed")
    needed = defaultdict(dict)
    results = {}
    for node in nodes:
        event = node["event"]
        if event and event["kind"] == "llm_result":
            results[node["node_id"]] = event
    for key, entry in starts.items():
        attrs = entry["event"]["attributes"]
        for dependency in entry["dependencies"]:
            event = results.get(dependency)
            if event is not None and (event["workflow_id"], event["invocation_id"]) == key[:2]:
                needed[event["attributes"]["request_id"]][attrs["input_sha256"]] = None
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    detector = Qwen3CoderDetector()
    decoder_file = Path(sys.modules[Qwen3CoderDetector.__module__].__file__)
    with token_path.open("rb") as stream:
        for line in stream:
            row = json.loads(line)
            args = needed.get(row["request_id"])
            if args is None or row["event"] != "request_output":
                continue
            text = tokenizer.decode(list(decode_tokens(row)), skip_special_tokens=False)
            for call in detector.detect_and_parse(text, tools=None).calls:
                if call.name != "execute":
                    continue
                payload = json.loads(call.parameters)
                if type(payload.get("timeout")) is str and payload["timeout"].isdecimal():
                    payload["timeout"] = int(payload["timeout"])
                digest = _input_digest(payload)
                if digest in args and isinstance(payload.get("command"), str):
                    args[digest] = payload["command"]
    commands = {}
    for key, entry in starts.items():
        attrs = entry["event"]["attributes"]
        for dependency in entry["dependencies"]:
            event = results.get(dependency)
            if event is None or (event["workflow_id"], event["invocation_id"]) != key[:2]:
                continue
            command = needed[event["attributes"]["request_id"]].get(attrs["input_sha256"])
            if command is not None:
                commands[key] = command
    return commands, {
        "token_capture_sha256": token_sha256,
        "tokenizer_sha256": _sha256(tokenizer_path),
        "parser_source_sha256": _sha256(decoder_file),
        "exact_command_bindings": len(commands),
    }


def causal_history(starts, ends, outcomes):
    histories = defaultdict(ToolCallHistory)
    timeline = [
        (node["event"]["ts_ms"], kind, key)
        for kind, events in ((0, starts), (1, ends))
        for key, node in events.items()
    ]
    features = {}
    # At equal clocks, starts cannot learn from an end not yet delivered.
    for _, kind, key in sorted(timeline):
        attrs = starts[key]["event"]["attributes"]
        signature = tool_signature(attrs)
        digest = attrs.get("input_sha256")
        history = histories[key[0]]
        if kind == 0:
            features[key] = history.start(key, signature, digest)
        else:
            duration, returned = outcomes[key]
            history.end(key, signature, digest, duration, returned=returned)
    return features


def load_calls(source, tool_name, *, tokenizer_path=None):
    manifest_path = source / "composite_source.json"
    plan_path = source / "plan.json"
    manifest = json.loads(manifest_path.read_bytes())
    plan_sha = _sha256(plan_path)
    if manifest["artifacts_sha256"]["plan.json"] != plan_sha:
        raise ValueError("source plan differs from the bound natural trace")
    selected = {
        row["workflow_id"]: row for row in manifest["selected_workflows"]
    }
    nodes = json.loads(plan_path.read_bytes())["nodes"]
    starts, ends = {}, {}
    for node in nodes:
        event = node["event"]
        if not event or event["kind"] not in {"tool_start", "tool_end"}:
            continue
        if event["attributes"].get("tool_name") != tool_name:
            continue
        key = _call_key(event)
        table = starts if event["kind"] == "tool_start" else ends
        if key in table:
            raise ValueError("duplicate natural tool event")
        table[key] = node

    if starts.keys() != ends.keys():
        raise ValueError("tool timing evaluation requires paired natural events")
    commands, command_evidence = (
        recover_commands(source, nodes, starts, manifest, tokenizer_path)
        if tool_name == "execute" and tokenizer_path is not None else ({}, {})
    )
    outcomes, diagnostics = {}, Counter()
    for key, start in starts.items():
        end = ends[key]
        if end["dependencies"] != [start["node_id"]]:
            raise ValueError("tool timer must depend on its matched start")
        result = end["event"]["attributes"]
        duration = end["delay_ms"] / 1000.
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("invalid natural tool duration")
        # The target is a result reaching the runtime. A returned tool error
        # still wakes the agent. An administrative interruption censors it.
        returned = result.get("censored") is not True and result.get(
            "tool_error_class",
        ) not in {"cancelled", "administrative_cancel"}
        diagnostics["returned" if returned else "censored"] += 1
        if result.get("status") == "error":
            diagnostics["error_events"] += 1
        if result.get("tool_error_class") == "timeout":
            diagnostics["timeout_events"] += 1
        outcomes[key] = duration, returned
    history_features = causal_history(starts, ends, outcomes)
    calls = [
        Call(
            selected[key[0]]["project"], key[0], (
                *call_features(start["event"]["attributes"], command=commands.get(key)),
                *history_features[key],
            ), *outcomes[key], identity=key,
            capture=selected[key[0]]["source_capture"],
            start_s=start["event"]["ts_ms"] / 1000.,
        )
        for key, start in starts.items()
    ]
    online = replay_online_baseline(
        starts, ends, outcomes, {key: row["source_capture"] for key, row in selected.items()},
    )
    return calls, online, {
        **command_evidence,
        "source_manifest_sha256": _sha256(manifest_path),
        "source_plan_sha256": plan_sha,
        "call_outcomes": dict(diagnostics),
    }


def replay_online_baseline(starts, ends, outcomes, captures):
    timelines = defaultdict(list)
    for key, start in starts.items():
        end = ends[key]
        start_event, end_event = start["event"], end["event"]
        timeline = timelines[captures[key[0]]]
        duration, _ = outcomes[key]
        timeline.extend((
            (start_event["ts_ms"], 0, key, start_event),
            (end_event["ts_ms"], 1, key, end_event),
        ))
        timeline.extend(
            (start_event["ts_ms"] + elapsed * 1000., 2, key, elapsed)
            for elapsed in LANDMARKS if elapsed < duration
        )
    online = {}
    for timeline in timelines.values():
        estimator = OnlineWorkloadToolTiming()
        active = set()
        for now, kind, key, payload in sorted(timeline):
            if kind == 2:
                if key in active:
                    estimate = estimator.estimate(*key[:2], now_ms=now)
                    if estimate is not None:
                        online[key, payload] = estimate
                continue
            estimator.observe(RuntimeEvent.from_dict(payload))
            (active.add if kind == 0 else active.remove)(key)
    return online


def split_calls(calls, *, mixed=False):
    def holdout(project):
        digest = hashlib.sha256(f"contextual_tool_timing_v1:{project}".encode()).digest()
        return int.from_bytes(digest, "big") % 5 == 0

    train, internal, external = [], [], []
    for call in calls:
        if not call.project.startswith("locobench/") and (not mixed or holdout(call.project)):
            external.append(call)
        elif call.project.startswith("locobench/") and holdout(call.project):
            internal.append(call)
        else:
            train.append(call)
    projects = [{call.project for call in group} for group in (train, internal, external)]
    if not all(projects) or any(
        projects[i] & projects[j] for i in range(3) for j in range(i)
    ):
        raise ValueError("expected three nonempty repository-disjoint populations")
    return train, internal, external


def exposures(call):
    """Split one observed path into exposure and at most one return event."""
    for index, start in enumerate(INTERVAL_STARTS):
        end = INTERVAL_STARTS[index + 1] if index + 1 < len(INTERVAL_STARTS) else math.inf
        terminal = call.duration <= end
        exposure = min(call.duration, end) - start
        if exposure > 0 or terminal and call.returned:
            yield start, max(exposure, MINIMUM_EXPOSURE_S), int(terminal and call.returned)
        if terminal:
            break


def _feature_row(call, start, vocabulary, contextual):
    features = call.features
    row = [start, *(mapping.get(features[i], -1) for i, mapping in enumerate(vocabulary))]
    feature_stop = {
        "shape": 3, "metadata": 7, "full": 7 + COMMAND_FEATURE_COUNT,
        "history": len(features),
    }[contextual]
    return row + list(features[len(vocabulary):feature_stop])


def fit_rates(calls, *, contextual):
    vocabulary = [
        {value: i for i, value in enumerate(sorted({call.features[col] for call in calls}))}
        for col in range(3)
    ]
    rows, labels, weights = [], [], []
    for call in calls:
        for start, exposure, event in exposures(call):
            rows.append(_feature_row(call, start, vocabulary, contextual))
            # Weighted Poisson loss with label d/t and weight t gives t*r-d*log(r).
            labels.append(event / exposure)
            weights.append(exposure)
    model = lgb.train(
        {
            "objective": "poisson", "learning_rate": .05, "num_leaves": 31,
            "min_data_in_leaf": 30, "lambda_l2": 10., "seed": 21,
            "deterministic": True, "force_col_wise": True, "num_threads": 2,
            "verbosity": -1,
        },
        lgb.Dataset(
            np.asarray(rows), label=labels, weight=weights,
            categorical_feature=[1, 2, 3],
        ),
        num_boost_round=160,
    )
    return model, vocabulary, contextual


def predict_rates(fitted, calls):
    model, vocabulary, contextual = fitted
    rows = np.asarray([
        _feature_row(call, start, vocabulary, contextual)
        for call in calls for start in INTERVAL_STARTS
    ])
    rates = model.predict(rows, num_threads=2).reshape(len(calls), -1)
    return [ToolReturnDistribution(tuple(row)) for row in rates]


def fit_shape_curves(calls):
    groups = defaultdict(list)
    for call in calls:
        sample = call.duration, call.returned
        groups[call.features[:2]].append(sample)
        groups[()].append(sample)
    curves = {
        key: KaplanMeierCurve().fit(samples)
        for key, samples in groups.items() if sum(event for _, event in samples) >= 8
    }
    if () not in curves:
        raise ValueError("insufficient baseline support")
    return curves


def _shape_curve(curves, call, elapsed):
    curve = curves.get(call.features[:2], curves[()])
    return curve if curve.survival(elapsed) > 0 else curves[()]


def _online_survival(estimate, horizon):
    index = bisect_right(estimate.release_cdf, (horizon * 1000., math.inf))
    return 1. - estimate.release_cdf[index - 1][1] if index else 1.


def _within_shape_auc(rows, labels, scores):
    groups = defaultdict(list)
    for call, target, score in zip(rows, labels, scores):
        groups[call.features[:2]].append((target, score))
    correct = pairs = 0.
    for group in groups.values():
        targets, values = np.asarray(group).T
        positive = targets == 1.
        n_pos = int(positive.sum())
        comparison_count = n_pos * (len(group) - n_pos)
        if comparison_count:
            correct += float(rankdata(values)[positive].sum()) - n_pos * (n_pos + 1) / 2
            pairs += comparison_count
    return {"auc": correct / pairs if pairs else None, "comparable_pairs": int(pairs)}


def _matched_coverage(labels, baseline_scores, candidate_scores):
    count = int(np.sum(baseline_scores >= .75))
    if count == 0:
        return {"selected": 0}
    scores = np.sort(candidate_scores)[::-1]
    cutoff = scores[count - 1]
    above, tied = candidate_scores > cutoff, candidate_scores == cutoff
    # Average the boundary ties without using test labels to pick members.
    correct = labels[above].sum() + (count - above.sum()) * labels[tied].mean()
    return {
        "selected": count,
        "baseline_precision": float(labels[baseline_scores >= .75].mean()),
        "candidate_precision": float(correct / count),
    }


def delayed_mixture_weights(rows, elapsed, expert_curves):
    """Use one set of weights for the entire conditional distribution."""
    timeline = defaultdict(list)
    for index, call in enumerate(rows):
        landmark = call.start_s + elapsed
        labels = tuple(
            1. if call.duration > elapsed + horizon else 0. if call.returned else None
            for horizon in HORIZONS
        )
        timeline[call.capture].extend((
            (landmark, 1, index, None),
            (call.start_s + min(call.duration, elapsed + HORIZONS[-1]), 0, index, labels),
        ))
    weights = [None] * len(rows)
    lane_count = 0
    resolved = 0
    losses = np.zeros(1 + len(expert_curves[0]))
    for events in timeline.values():
        aggregate = DelayedBrierWeights(len(expert_curves[0]))
        pending_probabilities = {}
        for _, is_query, index, labels in sorted(events):
            if is_query:
                probabilities = tuple(
                    tuple(curve(horizon) for horizon in HORIZONS)
                    for curve in expert_curves[index]
                )
                weights[index] = aggregate.forecast(index, probabilities)
                pending_probabilities[index] = probabilities
            else:
                aggregate.resolve(index, labels)
                known = [i for i, label in enumerate(labels) if label is not None]
                probabilities = pending_probabilities.pop(index)
                if known:
                    expert_scores = np.asarray(probabilities)[:, known]
                    target = np.asarray(labels)[known].astype(float)
                    weighted = np.asarray(weights[index]) @ expert_scores
                    losses[0] += np.mean((weighted - target) ** 2)
                    losses[1:] += np.mean((expert_scores - target) ** 2, axis=1)
                    resolved += 1
        lane_count += aggregate.lanes
    regret = float(losses[0] - min(losses[1:]))
    bound = 2 * lane_count * math.log(len(expert_curves[0]))
    if regret > bound + 1e-9:
        raise AssertionError("delayed aggregate exceeded its pathwise regret bound")
    return weights, {
        "resolved": resolved, "lanes": lane_count,
        "cumulative_brier_regret": regret, "cumulative_brier_regret_bound": bound,
        "average_brier_regret_bound": bound / resolved if resolved else None,
    }


def evaluate(calls, curves, predictions, online):
    incumbent = "online_km_with_fallback"
    cold_context = "online_km_with_contextual_cold_start"
    adaptive = "delayed_brier_aggregate"
    reports = {}
    brier_by_project = defaultdict(lambda: defaultdict(list))
    for elapsed in LANDMARKS:
        survivors = [i for i, call in enumerate(calls) if call.duration > elapsed]
        if not survivors:
            continue
        rows = [calls[i] for i in survivors]
        expert_curves = []
        for i, call in zip(survivors, rows):
            estimate = online.get((call.identity, elapsed))
            curve = _shape_curve(curves, call, elapsed)
            km = (
                lambda h, estimate=estimate: _online_survival(estimate, h)
            ) if estimate is not None else (
                lambda h, curve=curve: curve.conditional_survival(elapsed, h)
            )
            context = lambda h, distribution=predictions["contextual_history"][i]: (
                distribution.survival(elapsed, h)
            )
            expert_curves.append((km, context, km if estimate is not None else context))
        weights, bound_report = delayed_mixture_weights(rows, elapsed, expert_curves)
        median_errors = defaultdict(list)
        upper_coverage = defaultdict(list)
        for i, experts, blend in zip(survivors, expert_curves, weights):
            call = calls[i]
            if not call.returned:
                continue
            actual = call.duration - elapsed
            medians = {
                "shape_km": _shape_curve(curves, call, elapsed).remaining_quantile(elapsed, .5),
                **{name: forecasts[i].quantile(elapsed, .5)
                   for name, forecasts in predictions.items()},
            }
            estimate = online.get((call.identity, elapsed))
            medians[incumbent] = (
                estimate.p50_ms / 1000. if estimate is not None else medians["shape_km"]
            )
            medians[cold_context] = (
                estimate.p50_ms / 1000. if estimate is not None else medians["contextual_history"]
            )
            medians[adaptive] = mixture_quantile(experts, blend, .5, maximum_s=3600.)
            p90s = {
                incumbent: estimate.p90_ms / 1000. if estimate is not None else
                _shape_curve(curves, call, elapsed).remaining_quantile(elapsed, .9),
                adaptive: mixture_quantile(experts, blend, .9, maximum_s=3600.),
            }
            for name, upper in p90s.items():
                if math.isfinite(upper):
                    upper_coverage[name].append(actual <= upper)
            for name, estimate in medians.items():
                if math.isfinite(estimate):
                    median_errors[name].append(abs(actual - estimate))
        report = {
            "survivors": len(survivors),
            "aggregate_regret": bound_report,
            "calls_with_incumbent_online_estimate": sum(
                (calls[i].identity, elapsed) in online for i in survivors
            ),
            "calls_with_same_input_history": sum(
                any(math.isfinite(x) for x in calls[i].features[5:7]) for i in survivors
            ),
            "calls_with_command_features": sum(
                math.isfinite(calls[i].features[7]) for i in survivors
            ),
            "point_median_errors_ms": {
                name: {
                    "observed": len(values),
                    "mae": float(np.mean(values) * 1000),
                    "p50": float(np.quantile(values, .5) * 1000),
                    "p90": float(np.quantile(values, .9) * 1000),
                } for name, values in median_errors.items()
            },
            "p90_interval_coverage": {
                name: {"observed": len(values), "covered": sum(values)}
                for name, values in upper_coverage.items()
            },
            "horizons": {},
        }
        reports[str(elapsed)] = report
        for horizon in HORIZONS:
            rows = [calls[i] for i in survivors]
            selected = [
                index for index, call in enumerate(rows)
                if call.returned or call.duration > elapsed + horizon
            ]
            if not selected:
                continue
            scores = {name: [] for name in ("shape_km", incumbent, cold_context, *predictions)}
            for i, call in zip(survivors, rows):
                curve = _shape_curve(curves, call, elapsed)
                scores["shape_km"].append(curve.conditional_survival(elapsed, horizon))
                estimate = online.get((call.identity, elapsed))
                scores[incumbent].append(
                    _online_survival(estimate, horizon) if estimate is not None
                    else scores["shape_km"][-1]
                )
                for name, forecasts in predictions.items():
                    scores[name].append(forecasts[i].survival(elapsed, horizon))
                scores[cold_context].append(
                    scores[incumbent][-1] if estimate is not None
                    else scores["contextual_history"][-1]
                )
            scores[adaptive] = [
                sum(weight * curve(horizon) for weight, curve in zip(blend, experts))
                for blend, experts in zip(weights, expert_curves)
            ]
            rows = [rows[index] for index in selected]
            targets = np.asarray([float(call.duration > elapsed + horizon) for call in rows])
            scores = {name: np.asarray(values)[selected] for name, values in scores.items()}
            stats = {}
            for name, values in scores.items():
                losses = (values - targets) ** 2
                for call, loss in zip(rows, losses):
                    brier_by_project[call.project][name].append(float(loss))
                accept = values >= .75
                stats[name] = {
                    "brier": float(losses.mean()),
                    "within_shape_auc": _within_shape_auc(rows, targets, values),
                    "accepted_at_75": int(accept.sum()),
                    "early_returns_at_75": int((targets[accept] == 0.).sum()),
                }
            report["horizons"][str(horizon)] = {
                "scored": len(selected), "unresolved_censored": len(survivors) - len(selected),
                "true_survivals": int(targets.sum()), "models": stats,
                "coverage_matched_to_online_km": {
                    name: _matched_coverage(targets, scores[incumbent], scores[name])
                    for name in (*predictions, cold_context, adaptive)
                },
            }
    project_losses = {
        project: {name: float(np.mean(values)) for name, values in losses.items()}
        for project, losses in sorted(brier_by_project.items())
    }
    comparisons = {}
    for name in (*predictions, cold_context, adaptive):
        comparisons[name] = {}
        for baseline in ("shape_km", incumbent):
            differences = np.asarray([
                values[baseline] - values[name] for values in project_losses.values()
            ])
            rng = np.random.default_rng(21)
            bootstrap = rng.choice(differences, (5000, len(differences)), replace=True).mean(axis=1)
            comparisons[name][baseline] = {
                "project_mean_brier_gain": float(differences.mean()),
                "project_bootstrap_95_interval": list(np.quantile(bootstrap, [.025, .975])),
            }
    return {
        "calls": len(calls), "projects": len(project_losses),
        "landmarks": reports, "project_mean_brier": project_losses,
        "mean_brier": {
            method: float(np.mean([losses[method] for losses in project_losses.values()]))
            for method in next(iter(project_losses.values()))
        },
        "comparisons": comparisons,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tool-name", default="execute")
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--mixed-repository-holdout", action="store_true")
    args = parser.parse_args()
    calls, online, evidence = load_calls(args.source, args.tool_name, tokenizer_path=args.tokenizer)
    train, internal, external = split_calls(calls, mixed=args.mixed_repository_holdout)
    curves = fit_shape_curves(train)
    variants = {
        "shape_rate": "shape", "metadata": "metadata",
        "contextual": "full", "contextual_history": "history",
    }
    fitted = {name: fit_rates(train, contextual=features) for name, features in variants.items()}
    swe_report = (
        "swe_rebench_repo_holdout" if args.mixed_repository_holdout
        else "swe_rebench_source_holdout"
    )
    report = {
        **evidence, "tool_name": args.tool_name,
        "train_calls": len(train), "train_projects": sorted({row.project for row in train}),
        **{
            name: evaluate(rows, curves, {
                method: predict_rates(model, rows) for method, model in fitted.items()
            }, online)
            for name, rows in (("locobench_repo_holdout", internal), (swe_report, external))
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        name: {
            "calls": report[name]["calls"],
            "projects": report[name]["projects"],
            "incumbent_comparison": {
                method: report[name]["comparisons"][method]["online_km_with_fallback"]
                for method in (
                    "contextual_history", "online_km_with_contextual_cold_start", "delayed_brier_aggregate",
                )
            },
        } for name in ("locobench_repo_holdout", swe_report)
    }, indent=2))


if __name__ == "__main__":
    main()
