#!/usr/bin/env python3
"""Prepare a public empirical tool-time map on CPU; never launch inference."""

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.tool_timing_overlay import (
    apply_overlay, build_overlay, build_categorized_overlay, fingerprint,
    plan_fingerprint, tracelab_samples,
)
from beliefkv.predictor.tool_timing_prior import build_public_prior, split_public_sessions
from beliefkv.experiments.causal_replay import evaluate_causal_plan
from beliefkv.experiments.source_provenance import sha256_file
from scripts.run_causal_gpu_replay import source_plan


def prepare(args):
    if args.output.exists():
        raise FileExistsError("preserve existing timing preparation")
    config = json.loads(args.config.read_bytes())
    source = config["public_source"]
    if (
        config.get("schema_version") != 1
        or config.get("synthetic_tool_timing") is not True
        or config.get("seed") != 21
        or source.get("name") != "TraceLab"
        or sha256_file(args.input) != source["sha256"]
    ):
        raise ValueError("public trace or declared seed differs from the pinned timing source")
    selection = config["sample_selection"]
    categorized = config.get("observable_categories") is True
    prior_output = args.output.with_suffix(".prior.json")
    if categorized and prior_output.exists():
        raise FileExistsError("preserve existing offline timing prior")
    if selection.get("latency_field") != "tool_internal_latency_ms" or selection.get("wall_time_fallback") is not False:
        raise ValueError("use explicit internal tool times without inter-record wall-time fallback")
    with gzip.open(args.input, "rt") as stream:
        samples, audit = tracelab_samples(
            (json.loads(line) for line in stream if line.strip()),
            provider=selection["provider"], tool_names=selection["tool_names"],
            include_session=categorized,
        )
    prior = None
    if categorized:
        train, samples = split_public_sessions(samples, seed=config["seed"])
        prior = build_public_prior(
            train, samples, domain=config["timing_domain"], public_source=source,
        )
        audit = {
            **audit, "complete_population_sample_sha256": audit["sample_sha256"],
            "sample_sha256": fingerprint(samples),
            "split": "provider_session_sha256_mod5_holdout_v1",
            "training_calls": len(train), "evaluation_calls": len(samples),
        }
    plan, _, evidence = source_plan(args.source_arm)
    document = (build_categorized_overlay if categorized else build_overlay)(
        plan, samples, public_source=source, sample_audit=audit,
        target_tool_names=config["target_tool_names"], seed=config["seed"],
        **({"domain": config["timing_domain"]} if categorized else {}),
    )
    changed = apply_overlay(plan, document)
    if changed.summary() != plan.summary() or changed.dependency_graph() != plan.dependency_graph():
        raise ValueError("tool timing changed source work or causal dependencies")
    fixture_service = dict.fromkeys(plan.request_usage, 0.)
    natural_fixture = evaluate_causal_plan(plan, fixture_service)
    changed_fixture = evaluate_causal_plan(changed, fixture_service)
    document.update(
        prepared_at=datetime.now(timezone.utc).isoformat(),
        configuration={"path": str(args.config.resolve()), "sha256": sha256_file(args.config)},
        source_arm=str(args.source_arm.resolve()),
        source_evidence=evidence, source_work=plan.summary(),
        transformed_plan_sha256=plan_fingerprint(changed),
        cpu_dependency_preparation_only=True, gpu_experiment_registered=False,
        cpu_dependency_evidence={
            "unchanged_request_population_and_work_totals": True,
            "unchanged_dependency_graph": True,
            "complete_original_and_transformed_nodes": (
                len(natural_fixture["node_completed_ms"]) == len(plan.nodes)
                and len(changed_fixture["node_completed_ms"]) == len(plan.nodes)
            ),
            "original_zero_service_fixture_workflow_ms": natural_fixture["workflow_elapsed_ms"],
            "transformed_zero_service_fixture_workflow_ms": changed_fixture["workflow_elapsed_ms"],
            "scope": "Complete CPU DAG consistency with zero model-service fixtures. These times are not GPU performance, migration opportunity or application JCT predictions.",
        },
        analysis_source_sha256={
            name: sha256_file(ROOT / name) for name in (
                "scripts/prepare_empirical_tool_timing.py",
                "beliefkv/experiments/tool_timing_overlay.py",
                "beliefkv/experiments/causal_replay.py",
                "scripts/run_causal_gpu_replay.py",
            )
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if prior is not None:
        with prior_output.open("x") as stream:
            json.dump(prior, stream, indent=2, allow_nan=False)
            stream.write("\n")
        document["offline_prior"] = {
            "path": str(prior_output.resolve()), "sha256": sha256_file(prior_output),
            "training_groups_disjoint_from_evaluation": True,
        }
    with args.output.open("x") as stream:
        json.dump(document, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return {
        "output": str(args.output), "source_work": document["source_work"],
        "sample_audit": audit, "original_target_durations": document["original_target_durations"],
        "sampled_target_durations": document["sampled_target_durations"],
        "gpu_experiment_registered": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args), indent=2))


if __name__ == "__main__":
    main()
