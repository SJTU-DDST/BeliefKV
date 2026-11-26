#!/usr/bin/env python3
"""Evaluate observable TOOL_START forecasts in the matched 55/65/80 s archives."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from evaluate_contextual_tool_timing import (
    Call, _call_key, _matched_coverage, _online_survival, _sha256, _within_shape_auc,
    fit_rates, fit_shape_curves, load_calls, predict_rates,
)
from beliefkv.core.events import RuntimeEvent
from beliefkv.predictor.contextual_tool_timing import ToolCallHistory, call_features
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.predictor.tool_timing_prior import ToolTimingPrior, tool_signature


HORIZONS_S = (.1, .25, .4, .75, 1., 1.5, 2., 4., 8., 16.)
FOLDS = 5
REPORTS = Path("experiments/reports/d3_reclaim_r5_20261118")
SWEEP = REPORTS / "host200_arrival_rate_80_65_55_r1"


def read_json(path):
    return json.loads(path.read_bytes())


def fold(project):
    return int(hashlib.sha256(f"d2_tool_start_v1:{project}".encode()).hexdigest(), 16) % FOLDS


def load_run(path, projects, overlay, prior):
    km = OnlineWorkloadToolTiming(prior=prior)
    histories = defaultdict(ToolCallHistory)
    started, rows, baseline, timer_deltas = {}, [], {}, []
    counts = Counter()
    other_fits = defaultdict(Counter)
    trace_digest = hashlib.sha256()
    tool_kinds = {"tool_start", "tool_end"}
    lifecycle = {"workflow_end", "return", "invocation_cancel"}
    with path.open("rb") as source:
        for line in source:
            trace_digest.update(line)
            row = json.loads(line)
            kind, attrs = row["kind"], row["attributes"]
            if kind not in tool_kinds | lifecycle:
                continue
            km.observe(RuntimeEvent.from_dict(row))
            if kind in lifecycle:
                continue
            counts[f"{kind}:{attrs['tool_name']}"] += 1
            if attrs["tool_name"] != "execute":
                if kind == "tool_end":
                    for horizon in HORIZONS_S:
                        if attrs["duration_ms"] > horizon * 1000.:
                            other_fits[str(horizon)][attrs["tool_name"]] += 1
                continue
            key = _call_key(row)
            if kind == "tool_start":
                entry = overlay[key]
                if tool_signature(attrs) != (entry["timing_domain"], entry["observed_timing_class"]):
                    raise ValueError("captured tool category differs from the bound overlay")
                signature = tool_signature(attrs)
                digest = attrs.get("input_sha256")
                history = histories[key[0]].start(key, signature, digest)
                estimate = km.estimate(*key[:2], now_ms=row["ts_ms"])
                scores = None
                if estimate is not None and km.prefetch_supported(
                    *key[:2], now_ms=row["ts_ms"], scope=estimate.scope,
                ):
                    scores = [_online_survival(estimate, horizon) for horizon in HORIZONS_S]
                    counts[f"scope:{estimate.scope}"] += 1
                counts["multiple_open_at_start"] += km.estimate_revision(*key[:2]) is None
                counts["source_previous_input_duration_present"] += (
                    attrs.get("previous_same_input_duration_ms") is not None
                )
                # Captured prior-input timings may originate from the unmodified source run.
                features = call_features({**attrs, "previous_same_input_duration_ms": None})
                started[key] = (features + history, row["ts_ms"], digest, scores)
                continue
            features, start, digest, scores = started.pop(key)
            duration = attrs["duration_ms"]
            if not math.isclose(duration, row["ts_ms"] - start, abs_tol=1e-6):
                raise ValueError("tool return has an inconsistent replay clock")
            returned = attrs.get("censored") is not True and attrs.get("tool_error_class") not in {
                "cancelled", "administrative_cancel",
            }
            histories[key[0]].end(key, features[:2], digest, duration / 1000., returned=returned)
            timer_deltas.append(duration - overlay[key]["duration_ms"])
            rows.append(Call(
                projects[key[0]], key[0], features, duration / 1000., returned,
                identity=key, start_s=start / 1000.,
            ))
            if scores is not None:
                baseline[key] = scores
    counts["unpaired_execute_starts"] = len(started)
    counts["paired_execute_supported"] = len(baseline)
    counts["paired_distinct_sample_ids"] = len({overlay[call.identity]["sample_id"] for call in rows})
    counts["paired_distinct_repositories"] = len({call.project for call in rows})
    return rows, baseline, {
        "counts": dict(counts),
        "trace_sha256": trace_digest.hexdigest(),
        "non_execute_elapsed_over_horizon": dict(other_fits),
        "elapsed_minus_overlay_ms_p10_p50_p90_p99": np.quantile(timer_deltas, [.1, .5, .9, .99]).tolist(),
    }


def out_of_fold(rows, natural, natural_models, prior, baseline):
    rows = [call for call in rows if call.identity in baseline]
    scores = {
        "online_km": np.asarray([baseline[call.identity] for call in rows]),
        "pinned_km": np.asarray([
            [prior.curves[call.features[:2]].survival(horizon * 1000.) for horizon in HORIZONS_S]
            for call in rows
        ]),
        **{name: np.full((len(rows), len(HORIZONS_S)), np.nan) for name in (
            "natural_context", "overlay_context", "overlay_category_km",
        )},
    }
    for partition in range(FOLDS):
        indexes = [index for index, call in enumerate(rows) if fold(call.project) == partition]
        test = [rows[index] for index in indexes]
        train = [call for call in natural if fold(call.project) != partition]
        if partition not in natural_models:
            natural_models[partition] = fit_rates(train, contextual="history")
        matched = [call for call in rows if fold(call.project) != partition]
        for name, model in (
            ("natural_context", natural_models[partition]),
            ("overlay_context", fit_rates(matched, contextual="history")),
        ):
            scores[name][indexes] = [
                [distribution.survival(0., horizon) for horizon in HORIZONS_S]
                for distribution in predict_rates(model, test)
            ]
        curves = fit_shape_curves(matched)
        scores["overlay_category_km"][indexes] = [
            [curves[call.features[:2]].survival(horizon) for horizon in HORIZONS_S]
            for call in test
        ]
    if any(not np.isfinite(values).all() for values in scores.values()):
        raise ValueError("missing fold forecasts")
    return rows, scores


def evaluate(rows, scores, members=None):
    horizons = {}
    for col, horizon in enumerate(HORIZONS_S):
        mask = [
            index for index, call in enumerate(rows)
            if (members is None or call.workflow in members)
            and (call.returned or call.duration > horizon)
        ]
        selected = [rows[index] for index in mask]
        durations = np.asarray([call.duration for call in selected])
        labels = (durations > horizon).astype(float)
        values = {name: matrix[mask, col] for name, matrix in scores.items()}
        km = values["online_km"]
        groups = defaultdict(list)
        for index, call in enumerate(selected):
            groups[call.project].append(index)
        rng = np.random.default_rng(21)
        boot = rng.integers(len(groups), size=(5000, len(groups)))
        metrics = {}
        for name, probability in values.items():
            accepted = probability >= .75 - 1e-12
            loss = (probability - labels) ** 2
            diff = (km - labels) ** 2 - loss
            grouped = np.asarray([diff[indexes].mean() for indexes in groups.values()])
            metrics[name] = {
                "brier": float(loss.mean()),
                "project_mean_brier_gain_vs_online_km": float(grouped.mean()),
                "project_bootstrap_gain_95": np.quantile(grouped[boot].mean(axis=1), [.025, .975]).tolist(),
                "within_category_auc": _within_shape_auc(selected, labels, probability)["auc"],
                "accepted": int(accepted.sum()),
                "true_accepts": int(labels[accepted].sum()),
                "early_returns": int((labels[accepted] == 0).sum()),
                "matched_coverage": _matched_coverage(labels, km, probability),
            }
        horizons[str(horizon)] = {
            "scored": len(selected), "groups": len(groups),
            "oracle_duration_fits": int(labels.sum()), "models": metrics,
        }
    return horizons


def cost_quotes(arm):
    costs = []
    path = arm / "startup_attempts/0001/opportunities/admission_opportunities.jsonl"
    with path.open("rb") as source:
        for line in source:
            if b'"predicted_tool_kv_evicted"' not in line or b'"remaining_threshold_ms"' not in line:
                continue
            row = json.loads(line)
            if row["event"] == "predicted_tool_kv_evicted":
                costs.append(row["remaining_threshold_ms"])
    return {
        "source": str(path), "successful_release_node_quotes": len(costs),
        "p10_p50_p90_p99_ms": np.quantile(costs, [.1, .5, .9, .99]).tolist(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sweep = read_json(SWEEP / "plan.json")
    overlay_binding = sweep["fixed"]["TraceLab_tool_overlay"]
    overlay_path = Path(overlay_binding["path"])
    if _sha256(overlay_path) != overlay_binding["sha256"]:
        raise ValueError("matched tool overlay SHA256 mismatch")
    overlay_doc = read_json(overlay_path)
    overlay = {tuple(entry["tool_identity"]): entry for entry in overlay_doc["entries"]}
    source = Path(overlay_doc["source_arm"])
    prior_binding = overlay_doc["offline_prior"]
    prior = ToolTimingPrior.load(prior_binding["path"], prior_binding["sha256"])
    projects = {
        row["workflow_id"]: row["project"]
        for row in read_json(source / "composite_source.json")["selected_workflows"]
    }
    natural, _, evidence = load_calls(source, "execute")
    natural = [
        replace(call, features=(*call.features[:5], math.nan, math.nan, *call.features[7:]))
        for call in natural
    ]
    report = {
        "natural_evidence": evidence, "overlay_sha256": overlay_binding["sha256"],
        "prior_sha256": prior.sha256,
        "fold_projects": [sorted({p for p in projects.values() if fold(p) == i}) for i in range(FOLDS)],
        "scope": (
            "Forecast at observed TOOL_START with a single supported invocation-level KM. "
            "Context models use start metadata and observed execute history without raw arguments. "
            "Each archive uses repository-disjoint folds and the same provider sample pool and IDs. "
            "Cost probes are fixed elapsed seconds. Successful node quotes are contextual anchors. "
            "The reported fit measures duration eligibility."
        ),
        "runs": {},
    }
    natural_models = {}
    for point in sorted(sweep["points"], key=lambda item: item["mean_interval_seconds"]):
        interval = point["mean_interval_seconds"]
        run = (
            REPORTS / "host200_mean65_integrated_full_a7b698e2_r2" if interval == 65
            else SWEEP / f"mean{interval}_full_d3_a7b698e2"
        )
        stage = read_json(run / "plan.json")["stages"]["pressure"]
        arm = Path(stage["run_root"]) / stage["arm"]
        env = read_json(Path(stage["configuration"]))["common_environment"]
        if env["BELIEFKV_TOOL_TIMING_PRIOR_SHA256"] != prior.sha256 or (
            float(env["BELIEFKV_TOOL_OFFLOAD_MIN_SURVIVAL"]) != .75
        ):
            raise ValueError("archive uses a different prior or probability threshold")
        trace = arm / "replay/runtime_events.replay.jsonl"
        calls, baseline, observations = load_run(trace, projects, overlay, prior)
        rows, scores = out_of_fold(calls, natural, natural_models, prior, baseline)
        cohort_path = Path(point["cohort"]["path"])
        if _sha256(cohort_path) != point["cohort"]["sha256"]:
            raise ValueError("archive cohort SHA256 mismatch")
        cohort = read_json(cohort_path)
        measured = set(cohort["measured_workflow_ids"])
        fixed38 = measured | set(cohort["warmup_workflow_ids"])
        report["runs"][interval] = {
            "trace": str(trace), **observations,
            "cost_quotes": cost_quotes(arm),
            "all": evaluate(rows, scores), "fixed18": evaluate(rows, scores, measured),
            "fixed38": evaluate(rows, scores, fixed38),
        }
        print(f"Mean {interval}s has {len(rows)} supported paired execute calls", flush=True)
    with args.output.open("x", encoding="ascii") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")


if __name__ == "__main__":
    main()
