"""Prepare the archived workload with its original tool timers and audit the old prior."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.composite_workload import load_composed_source
from beliefkv.experiments.replay_arrivals import load_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import (
    duration_summary, load_overlay, plan_fingerprint, tracelab_samples,
)
from beliefkv.experiments.workflow_cohort import validate_cohort
from beliefkv.predictor.tool_timing_prior import ToolTimingPrior, split_public_sessions
from scripts.train_workflow_work import write


def read(path):
    return json.loads(path.read_bytes())


def bound_path(binding):
    path = Path(binding["path"]).resolve()
    if sha256_file(path) != binding["sha256"]:
        raise ValueError(f"archived input changed: {path}")
    return path


def tool_inventory(plan):
    starts = {
        node.node_id: node.event.attributes["tool_name"]
        for node in plan.nodes
        if node.event is not None and node.event.kind is Kind.TOOL_START
    }
    durations = defaultdict(list)
    for node in plan.nodes:
        if node.event is not None and node.event.kind is Kind.TOOL_END:
            (start_id,) = node.dependencies
            durations[starts.pop(start_id)].append(node.delay_ms)
    if starts:
        raise ValueError("natural tool inventory has unmatched starts")
    return {
        name: {**duration_summary(values), "distinct_durations": len(set(values))}
        for name, values in sorted(durations.items())
    }


def evaluate_prior(prior, heldout, *, domain, min_survival):
    """Evaluate fixed horizons on unique held-out calls that are still running."""
    labels = defaultdict(list)
    for sample in heldout:
        labels[sample["tool_name"]].append(sample["duration_ms"])
    report = []
    for category, durations in sorted(labels.items()):
        curve = prior.curves[domain, category]
        for elapsed_ms in (0, 1_000, 5_000):
            if curve.sample_count * curve.survival(elapsed_ms) < 8:
                continue
            survivors = [duration for duration in durations if duration > elapsed_ms]
            if not survivors:
                continue
            for horizon_ms in (400, 1_000, 2_000, 4_000):
                predicted = curve.conditional_survival(elapsed_ms, horizon_ms)
                labels_at_horizon = [
                    duration > elapsed_ms + horizon_ms for duration in survivors
                ]
                report.append({
                    "category": category, "elapsed_ms": elapsed_ms,
                    "horizon_ms": horizon_ms, "calls": len(survivors),
                    "predicted_survival": predicted,
                    "observed_survivals": sum(labels_at_horizon),
                    "observed_survival": sum(labels_at_horizon) / len(survivors),
                    "brier_score": sum(
                        (predicted - label) ** 2 for label in labels_at_horizon
                    ) / len(survivors),
                    "passes_probability_gate": predicted + 1e-12 >= min_survival,
                })
    return report


def prepare(registration_path, public_trace, output_dir):
    if output_dir.exists():
        raise FileExistsError("preserve the existing natural timing preparation")
    registration = read(registration_path)
    template_path = Path(registration["experiment_configuration"])
    template = read(template_path)
    overlay_path = bound_path(registration["tool_timing_overlay"])
    if bound_path(template["tool_timing_overlay"]) != overlay_path:
        raise ValueError("the configuration and registration use different timing overlays")
    plan, _, evidence = load_composed_source(Path(registration["source_arm"]))
    _, overlay = load_overlay(plan, overlay_path)
    if overlay != registration["tool_timing_overlay"]:
        raise ValueError("registered overlay evidence differs from the loaded replay")
    if plan.summary() != registration["source_plan"]:
        raise ValueError("registered source work differs from the natural replay")
    arrival_path = bound_path(template["workflow_arrivals"])
    cohort_path = bound_path(template["private_analysis_cohort"])
    ids = sorted({node.workflow_id for node in plan.nodes})
    arrivals = load_arrival_plan(arrival_path, ids)
    cohort = read(cohort_path)
    validate_cohort(cohort, arrivals)

    prior_binding = overlay["offline_prior"]
    prior_path = bound_path(prior_binding)
    prior_document = read(prior_path)
    prior = ToolTimingPrior.load(prior_path, prior_binding["sha256"])
    domain = prior_document["domain"]
    if sha256_file(public_trace) != overlay["public_source"]["sha256"]:
        raise ValueError("TraceLab source differs from the archived timing population")
    with gzip.open(public_trace, "rt") as stream:
        public_samples, _ = tracelab_samples(
            map(json.loads, stream), provider="claude",
            tool_names=[category for curve_domain, category in prior.curves if curve_domain == domain],
            include_session=True,
        )
    train, heldout = split_public_sessions(public_samples, seed=overlay["seed"])
    if (
        sorted({row["source_group"] for row in train}) != prior_document["training_groups"]
        or sorted({row["source_group"] for row in heldout})
        != prior_document["evaluation_groups"]
        or prior_document["evaluation_groups"] != overlay["evaluation_groups"]
    ):
        raise ValueError("independent prior evaluation requires the archived session split")
    entries = read(overlay_path)["entries"]
    sample_values = Counter(entry["duration_ms"] for entry in entries)
    inventory = tool_inventory(plan)

    config = deepcopy(template)
    config.update(
        purpose="Replay the registered workflow graph using its recorded tool timers",
        status="cpu_prepared_natural_tool_timing",
        tool_timing_overlay=None,
        performance_accepted=False,
    )
    config["common_environment"].update(
        BELIEFKV_TOOL_TIMING_PRIOR="", BELIEFKV_TOOL_TIMING_PRIOR_SHA256="",
        TOOL_TIMING_ARTIFACT="", ONLINE_TOOL_TIMING="1",
    )
    config["comparison_contract"].update(
        workload_change="same_registered_workflows_and_arrivals_with_recorded_tool_timers",
        historical_GPU_results_are_not_matched_controls=True,
    )
    config["gpu_execution"].update(registered=False, automatic_GPU_followup=False)
    report = {
        "source": evidence,
        "natural_plan_sha256": plan_fingerprint(plan),
        "source_work": plan.summary(),
        "natural_tools": inventory,
        "natural_tool_calls": sum(row["count"] for row in inventory.values()),
        "measured_count": len(cohort["measured_workflow_ids"]),
        "warmup_count": len(cohort["warmup_workflow_ids"]),
        "legacy_overlay": {
            "calls": len(entries),
            "unique_source_calls": len({row["sample_id"] for row in entries}),
            "unique_durations": len(sample_values),
            "largest_duration_ms": max(sample_values),
            "largest_duration_reuses": sample_values[max(sample_values)],
            "sampled_by_tool": {
                name: duration_summary([
                    row["duration_ms"] for row in entries if row["tool_name"] == name
                ]) for name in overlay["target_tool_names"]
            },
        },
        "prior_evaluation": {
            "train_calls": len(train), "test_calls": len(heldout),
            "train_sessions": len(prior_document["training_groups"]),
            "test_sessions": len(prior_document["evaluation_groups"]),
            "fixed_horizons_same_source_offline_prior": evaluate_prior(
                prior, heldout, domain=domain, min_survival=float(
                    template["common_environment"]["BELIEFKV_TOOL_OFFLOAD_MIN_SURVIVAL"],
                ),
            ),
        },
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                registration_path, template_path, overlay_path, prior_path, public_trace,
                arrival_path, cohort_path, Path(__file__),
            )
        },
        "gpu_replay_qualified": False,
        "performance_accepted": False,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    config_path = output_dir / "full_d3.json"
    write(config_path, config)
    report["configuration"] = {"path": str(config_path), "sha256": sha256_file(config_path)}
    write(output_dir / "preparation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--public-trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(*(path.resolve() for path in (
        args.registration, args.public_trace, args.output_dir,
    )))
    print(json.dumps({
        "workflows": report["source_work"]["workflows"],
        "natural_tool_calls": report["natural_tool_calls"],
        "configuration": report["configuration"],
    }))


if __name__ == "__main__":
    main()
