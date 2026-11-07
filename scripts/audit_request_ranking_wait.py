"""Inspect one completed request's recorded ranking choices with frozen readers."""

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import importlib
import json
import math
import os
from pathlib import Path
import sys


def choice_details(rows, observations, request_id, costs, profile, helpers):
    policy, reader, work = helpers
    if len(rows) != len(observations):
        raise ValueError("each request choice needs its exact observed-state proxy")
    counts, details = Counter(), []
    for row, observed in zip(rows, observations):
        if row["monotonic_s"] != observed.get("monotonic_s"):
            raise ValueError("the observed request proxy belongs to another decision boundary")
        replayed = reader.replay_choice(
            row, costs, profile["identity"], allow_feasible_resource_alternatives=True,
            plan_device_capacity=True,
        )
        owners = [
            owner for owner, key in row.get("keys", {}).items()
            if key["request_id"] == request_id
        ]
        if len(owners) != 1:
            raise ValueError("a request choice must have exactly one invocation owner")
        owner = owners[0]
        candidates = work.combination_candidates(row)
        target = next((candidate for candidate in candidates if candidate.identity == owner), None)
        if (
            target is None or not replayed["search_replayed"]
            or "selected_evaluation" not in row["decision"]
        ):
            raise ValueError("request diagnosis requires reproducible candidate metadata")
        required = set(row["decision"]["required_reference_members"])
        by_id = {candidate.identity: candidate for candidate in candidates}
        required_rows = [by_id[name] for name in required if name != owner]
        resources = policy.CombinationResources(**row["resources"])
        alone = policy._evaluate(required_rows + [target], resources, costs)
        selected = row["decision"]["selected"]
        extension = (
            policy._evaluate([by_id[name] for name in selected] + [target], resources, costs)
            if owner not in selected else None
        )
        proxy = next(
            (item for item in observed["candidates"] if item["key"] == row["keys"][owner]), None,
        )
        if proxy is None or "features" not in proxy or "synchronous" not in proxy.get("predictions", {}):
            raise ValueError("request diagnosis lacks the causal observed-state forecast proxy")
        forecast = proxy["predictions"]["synchronous"]
        optimistic = None
        if row["decision"]["search_complete"]:
            optimistic = policy.choose_combination(
                [replace(candidate, weight=1.) if candidate.workflow == target.workflow else candidate
                 for candidate in candidates],
                row["decision"]["reference"], resources, costs, required=required,
                clock=lambda: 0, allow_feasible_resource_alternatives=True,
            )
        state = (
            "selected" if owner in selected
            else "does_not_fit_with_required_JOIN" if alone is None
            else "planning_fit_not_selected"
        )
        counts[state] += 1
        counts["complete_searches"] += optimistic is not None
        counts["cached_prediction_absent"] += row.get("predictions", {}).get(target.workflow) is None
        counts["proxy_" + forecast["support"]] += 1
        counts["max_weight_one_would_select_target"] += (
            optimistic is not None and owner in optimistic["selected"]
        )
        details.append({
            "monotonic_s": row["monotonic_s"], "source": row["source"],
            "key": row["keys"][owner], "state": state,
            "reference_contains_target": owner in row["decision"]["reference"],
            "search_complete": row["decision"]["search_complete"],
            "decision_reason": row["decision"]["reason"],
            "target_logical_tokens": target.logical_tokens, "cached_weight": target.weight,
            "cached_prediction": row.get("predictions", {}).get(target.workflow),
            "observed_state_proxy": proxy,
            "logical_capacity_tokens": resources.logical_tokens,
            "required_JOIN_members": sorted(required),
            "planning_target_with_required_JOIN": alone,
            "planning_selected_plus_target": extension,
            "selected_evaluation": row["decision"]["selected_evaluation"],
            "selected_candidates": [{
                "identity": by_id[name].identity, "workflow": by_id[name].workflow,
                "logical_tokens": by_id[name].logical_tokens, "weight": by_id[name].weight,
            } for name in selected],
            "max_target_weight_one_selection": optimistic["selected"] if optimistic else None,
        })
    return {"counts": dict(counts), "decisions": details}


def support_excesses(features, model):
    contract = model.distribution_contract
    return [{
        "feature": name, "observed_value": value,
        "training_min": math.expm1(low), "training_max": math.expm1(high),
        "allowed_log_margin": contract["feature_log_margin"],
    } for name, value, low, high in zip(
        model.feature_names, features, contract["feature_log_min"], contract["feature_log_max"],
    ) if not low - contract["feature_log_margin"] <= math.log1p(value) <= high + contract["feature_log_margin"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reader-root", "registration", "control", "comparison", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or args.output.exists()
    ):
        raise ValueError("use CPU-only nice19 and preserve prior request diagnostics")
    sys.path.insert(0, str(args.reader_root.resolve()))
    modules = [importlib.import_module(name) for name in (
        "beliefkv.policy.workflow_combination",
        "scripts.audit_workflow_combination_choices",
        "scripts.audit_learned_combination_prediction",
        "scripts.audit_live_work_prediction",
        "scripts.audit_causal_jct_paths",
    )]
    policy, choices, work, observed, paths = modules
    for module in modules:
        if not Path(module.__file__).resolve().is_relative_to(args.reader_root.resolve()):
            raise ValueError("use the explicitly supplied frozen reader modules")
    from beliefkv.predictor.workflow_work import WorkflowWorkModel
    from scripts.audit_sealed_wait_prefetch import SealedRecords
    from scripts.train_workflow_work import write

    load, sha256 = work.causal.load, work.sha256_file
    plan, control, comparison = map(load, (args.registration, args.control, args.comparison))
    if comparison.get("complete_measured_work_fresh_drain_and_actual_exit") is not True:
        raise ValueError("require the existing complete comparison and normal measured tails")
    for path in (args.registration, args.control):
        if comparison["inputs_sha256"].get(str(path.resolve())) != sha256(path):
            raise ValueError("the complete comparison binds different request executions")
    if sha256(Path(policy.__file__)) != plan["source_sha256"]["beliefkv/policy/workflow_combination.py"]:
        raise ValueError("request diagnosis must replay the actual frozen serving policy")
    spec, control_spec = (
        load(Path(value["experiment_configuration"])) for value in (plan, control)
    )
    work.verify_learned_combination_pair(control_spec, spec)
    model_path = Path(spec["common_environment"]["WORKFLOW_WORK_MODEL"])
    label_path = Path(control["reference_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"])
    pins = {str(path.resolve()): sha256(path) for path in (
        args.registration, args.control, args.comparison, model_path, label_path,
        Path(__file__), *(Path(module.__file__) for module in modules),
    )}
    model = WorkflowWorkModel.load(
        model_path, spec["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"],
    )
    labels, sealed = load(label_path), SealedRecords()
    arm = Path(plan["run_root"]) / "full"
    events = list(sealed(arm / "replay/runtime_events.replay.jsonl"))
    submit = [row for row in events if row["kind"] == "llm_submit"
              and row["attributes"]["request_id"] == args.request_id]
    receipts = {row["request_id"]: row for row in sealed(arm / "replay/native_requests.jsonl")
                if row["request_id"] == args.request_id}
    if len(submit) != 1 or len(receipts) != 1:
        raise ValueError("use one actually submitted and completely served request")
    native_path = arm / "server/runtime_audit.jsonl"
    status_path = arm / "server/native_telemetry_status.json"
    pins[str(status_path.resolve())] = sha256(status_path)
    status = load(status_path)
    if any(status.get(name) != 0 for name in (
        "dropped_records", "failed_records", "writer_queue_depth", "pending_batch_count",
    )) or status.get("writer_error") is not None:
        raise ValueError("require settled complete native telemetry")
    launches = paths.first_launches(
        sealed(native_path), receipts,
        expected_records=status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"],
    )
    start_ms, launch_ms = submit[0]["ts_ms"], launches[args.request_id]
    rows = [row for row in sealed(arm / "opportunities/admission_opportunities.jsonl")
            if row.get("event") == choices.EVENT and start_ms <= row["monotonic_s"] * 1000. <= launch_ms
            and any(key["request_id"] == args.request_id for key in row.get("keys", {}).values())
            and "evaluated_subsets" in row["decision"]]
    observations = [{"monotonic_s": row["monotonic_s"], "candidates": [
        {"key": key} for key in row["keys"].values() if key["request_id"] == args.request_id
    ]} for row in rows]
    timeline = observed.observation_timeline(
        events, (row for row in sealed(native_path) if row.get("event") == "gpu_service_sample"),
        labels["requests"],
    )
    observed.audit(
        observations, timeline, labels["requests"],
        {name: value["total_work_units"] / 100. for name, value in labels["workflows"].items()},
        {"synchronous": model},
    )
    profile = model.raw["admission_cost_profile"]
    result = choice_details(rows, observations, args.request_id, work.planning_costs(profile),
                            profile, (policy, choices, work))
    for detail in result["decisions"]:
        detail["proxy_features_outside_training_support"] = support_excesses(
            detail["observed_state_proxy"]["features"], model,
        )
    if any(sha256(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("an immutable request diagnostic input changed")
    write(args.output, {
        **result, "request_id": args.request_id, "submit_monotonic_ms": start_ms,
        "first_launch_monotonic_ms": launch_ms, "submit_to_first_launch_s": (launch_ms - start_ms) / 1000.,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "inputs_sha256": pins, "input_jsonl_prefixes": sealed.prefixes, "GPU_action": False,
        "scope": {
            "only_recorded_candidate_searches_for_this_exact_request": True,
            "absence_from_shortlist_or_active_state_not_reconstructed": True,
            "snapshot_proxy_uses_client_publication_not_exact_runtime_ingest": True,
            "proxy_support_failure_does_not_prove_reason_for_cached_absence": True,
            "planning_fit_is_not_immediate_native_admission": True,
            "max_weight_one_keeps_other_candidates_weights_and_resource_constraints": True,
            "counterfactual_excludes_incomplete_searches": True,
            "whole_run_JCT_counterfactual_or_prediction_fix_gain_proven": False,
        },
    })
    print(json.dumps({"counts": result["counts"], "wait_s": (launch_ms - start_ms) / 1000.}), flush=True)


if __name__ == "__main__":
    main()
