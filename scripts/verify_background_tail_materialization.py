"""Verify unchanged original work and measured traffic in a background extension."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import extend_poisson_arrival_plan
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import apply_overlay
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def verify_graph_prefix(
    base, extended, base_expected, extended_expected, *, external_workflow_arrivals=False,
):
    identities = {node.workflow_id for node in base.nodes}
    original = {node.node_id: node for node in base.nodes}
    retained = {node.node_id: node for node in extended.nodes if node.workflow_id in identities}
    root_offsets = []
    if original.keys() != retained.keys() or any(
        extended.request_usage.get(key) != value for key, value in base.request_usage.items()
    ) or {key: value for key, value in extended_expected.items() if key in base_expected} != base_expected:
        raise ValueError("background extension changed original requests, events, delays or dependencies")
    for key, before in original.items():
        after = retained[key]
        if before == after:
            continue
        # External release maps override only independent workflow-start deadlines.
        if not (
            external_workflow_arrivals and before.event is not None
            and before.event.kind is Kind.WORKFLOW_START
            and not before.dependencies and not after.dependencies
            and replace(after, delay_ms=before.delay_ms) == before
        ):
            raise ValueError("background extension changed original requests, events, delays or dependencies")
        root_offsets.append({
            "workflow_id": before.workflow_id,
            "original_stored_offset_ms": before.delay_ms,
            "extended_stored_offset_ms": after.delay_ms,
        })
    old_requests = {node.request_id for node in retained.values() if node.request_id is not None}
    if old_requests != set(base_expected) or set(base.request_usage) != set(base_expected):
        raise ValueError("original request ownership or expected work is incomplete")
    return {
        "original_workflows": len(identities),
        "original_requests": len(base_expected), "original_causal_nodes": len(original),
        "original_expected_native_work_and_causal_nodes_equal": not root_offsets,
        "original_expected_native_work_and_internal_causal_nodes_equal": True,
        "external_workflow_arrivals_checked": external_workflow_arrivals,
        "workflow_start_offsets_overridden_by_external_arrivals": root_offsets,
    }


def verify_traffic(base_arrivals, arrivals, base_cohort, cohort, additional):
    validate_cohort(base_cohort, base_arrivals)
    validate_cohort(cohort, arrivals)
    if arrivals != extend_poisson_arrival_plan(base_arrivals, additional):
        raise ValueError("background extension changed the original arrival process")
    changed_fields = {"arrival_plan_sha256", "background_workflow_ids"}
    if any(base_cohort.get(key) != cohort.get(key)
           for key in base_cohort.keys() | cohort.keys() if key not in changed_fields) or (
        cohort["background_workflow_ids"] != base_cohort["background_workflow_ids"] + additional
    ):
        raise ValueError("background extension changed measured members or complete JCT semantics")
    return {
        "measured_workflows": len(cohort["measured_workflow_ids"]),
        "warmup_workflows": len(cohort["warmup_workflow_ids"]),
        "background_workflows": len(cohort["background_workflow_ids"]),
        "original_arrivals_measured_members_and_common_window_equal": True,
        "first_additional_arrival_seconds": arrivals["arrival_schedule"][
            len(base_arrivals["arrival_schedule"])
        ]["offset_seconds"],
        "last_arrival_seconds": arrivals["arrival_schedule"][-1]["offset_seconds"],
    }


def verify_overlay_prefix(base, extended, base_overlay, overlay, additional):
    apply_overlay(base, base_overlay)
    apply_overlay(extended, overlay)
    fields = (
        "schema_version", "kind", "sampling", "seed", "public_source", "sample_audit",
        "target_tool_names", "timing_domain", "category_population", "evaluation_groups",
        "online_metadata", "semantics", "configuration",
    )
    if any(base_overlay.get(key) != overlay.get(key) for key in fields) or (
        base_overlay.get("offline_prior", {}).get("sha256")
        != overlay.get("offline_prior", {}).get("sha256")
    ):
        raise ValueError("background extension changed the public timing pool or predictor prior")
    before = {tuple(row["tool_identity"]): row for row in base_overlay["entries"]}
    after = {tuple(row["tool_identity"]): row for row in overlay["entries"]}
    if any(after.get(key) != value for key, value in before.items()) or any(
        key[0] not in additional for key in after.keys() - before.keys()
    ):
        raise ValueError("background extension changed an original tool sample or added a foreign call")
    return {
        "original_tool_samples": len(before),
        "additional_tool_samples": len(after) - len(before),
        "original_tool_samples_and_public_prior_equal": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    names = (
        "base-source-arm", "source-arm", "population-plan", "base-arrivals", "arrivals",
        "base-cohort", "cohort", "base-overlay", "overlay", "output",
    )
    for name in names:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve previous evidence and use nice19 hidden-CUDA verification")
    paths = {
        name: getattr(args, name.replace("-", "_"))
        for name in names if name not in ("base-source-arm", "source-arm", "output")
    }
    paths.update({
        "base_source": args.base_source_arm / "composite_source.json",
        "source": args.source_arm / "composite_source.json",
        "operator": Path(__file__),
    })
    pins = {str(path.resolve()): sha256_file(path) for path in paths.values()}
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "inputs_sha256": pins,
        "GPU_action": False, "GPU_successor_registered": False,
        "formal_workload_accepted": False, "performance_accepted": False,
    }
    try:
        documents = {
            name: json.loads(path.read_bytes()) for name, path in paths.items()
            if name != "operator"
        }
        base, base_expected, base_evidence = source_plan(args.base_source_arm)
        extended, extended_expected, extended_evidence = source_plan(args.source_arm)
        before = documents["base_source"]["selected_workflows"]
        after = documents["source"]["selected_workflows"]
        population = documents["population-plan"]
        if after != population["selected_workflows"] or after[:len(before)] != before or (
            after[len(before):] != population["additional_workflows"]
        ):
            raise ValueError("materialization changed the predeclared original population")
        additional = [row["workflow_id"] for row in population["additional_workflows"]]
        if {node.workflow_id for node in extended.nodes} != {row["workflow_id"] for row in after}:
            raise ValueError("materialized graph differs from the declared original population")
        traffic = verify_traffic(
            documents["base-arrivals"], documents["arrivals"],
            documents["base-cohort"], documents["cohort"], additional,
        )
        external_arrivals = all(
            evidence.get("external_workflow_arrivals_required") is True
            for evidence in (base_evidence, extended_evidence)
        )
        result.update(
            graph=verify_graph_prefix(
                base, extended, base_expected, extended_expected,
                external_workflow_arrivals=external_arrivals,
            ),
            traffic=traffic,
            tools=verify_overlay_prefix(
                base, extended, documents["base-overlay"], documents["overlay"], additional,
            ),
            extended_work=extended.summary(),
            model_work_units=documents["source"]["population"]["model_work_units"],
            exact_raw_token_artifact_and_original_source_bindings_verified=True,
            actual_ready_supply_Host_loss_and_complete_JCT_still_require_GPU_execution=True,
            status="CPU_materialization_verified",
        )
        if any(sha256_file(Path(name)) != value for name, value in pins.items()):
            raise ValueError("background verification inputs changed during analysis")
        for document in (documents["base-overlay"], documents["overlay"]):
            prior = document.get("offline_prior")
            if prior is None or sha256_file(Path(prior["path"])) != prior["sha256"]:
                raise ValueError("public predictor prior binding changed or is missing")
    except Exception as error:
        result.update(status="failed", error=repr(error))
        write(args.output, result)
        raise
    write(args.output, result)
    print(json.dumps({
        "status": result["status"], "graph": result["graph"], "traffic": result["traffic"],
        "tools": result["tools"], "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
