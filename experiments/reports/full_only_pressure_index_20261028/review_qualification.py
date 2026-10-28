"""Bind the CPU-only implementation fixes and unchanged native model components."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--model", action="store_true")
    args = parser.parse_args()
    app = args.application.resolve()
    sys.path.insert(0, str(app))
    from beliefkv.experiments.native_engine_variant import SIBLING_CAPACITY_TREE, engine_patch
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_comparison as causal
    from scripts import run_causal_source_capture as capture

    cpu_path = HERE / "shared_cpu_r1.evidence.json"
    cpu = causal.load(cpu_path)
    manifest = Path(cpu["source_manifest"]["path"])
    if sha256_file(manifest) != cpu["source_manifest"]["sha256"]:
        raise ValueError("CPU source manifest changed")
    causal.comparison.verify_cpu_snapshot(app, causal.load(manifest))
    application = git_source_state(app)
    if application["dirty"] or cpu["passing_cases_including_subtests"] != 3718:
        raise ValueError("require clean frozen application and complete current CPU proof")
    configuration = app / "configs/migration/predictive_sibling_service_capacity_20261028.json"
    if sha256_file(configuration) != cpu["configuration"]["sha256"]:
        raise ValueError("candidate configuration differs from qualified sources")
    engine = ROOT.parent / ".worktrees/sglang-sibling-capacity-comparison-source-20261028"
    native_report = ROOT / "experiments/reports/sibling_service_capacity_20261028/native_targeted_r1.xml"
    prior_path = ROOT / "experiments/reports/control_plan_diagnostics_20261028/native_registration_sources.json"
    prior = causal.load(prior_path)
    observed = git_source_state(engine, canonical_patch=engine_patch(app, SIBLING_CAPACITY_TREE))
    expected = {
        **prior, "canonical_patch": {
            **prior["canonical_patch"], "path": observed["canonical_patch"]["path"],
        },
    }
    if (
        {key: value for key, value in observed.items() if key != "state_sha256"}
        != {key: value for key, value in expected.items() if key != "state_sha256"}
        or observed["canonical_patch"]["actual_tree"] != SIBLING_CAPACITY_TREE
    ):
        raise ValueError("native state changed beyond the frozen patch pathname")
    component_path = ROOT / "experiments/reports/sibling_service_capacity_20261028/gpu_components_r1.json"
    component = causal.load(component_path)
    clean_engine = ROOT.parent / ".worktrees/sglang-sibling-capacity-20261028"
    if (
        component["status"] != "native_gpu_layer_handoff_components_qualified"
        or component["native_engine"] != git_source_state(clean_engine)
        or component["operator_sha256"] != sha256_file(app / "scripts/qualify_layer_handoff.py")
        or component["direction_groups"] != {"d2h": 8, "h2d": 4}
    ):
        raise ValueError("unchanged native component proof no longer binds")
    sources_path = HERE / "native_registration_sources.json"
    output = HERE / ("model_review.json" if args.model else "cpu_native_binding.json")
    if output.exists() or not args.model and sources_path.exists():
        raise FileExistsError("preserve prior qualification review")
    paths = [cpu_path, manifest, configuration, prior_path, component_path, native_report, Path(__file__)]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "frozen_application": application, "native_tree": SIBLING_CAPACITY_TREE,
        "passing_cpu_cases": 3718, "performance_accepted": False,
        "native_binding_changed_field_only": "canonical_patch.path",
        "native_component_operator_and_groups_verified": True,
        "model_qualified": False,
    }
    if args.model:
        model_path = HERE / "model_r1/report.json"
        cleanup_path = HERE / "model_r1/owned_process_cleanup.json"
        model, cleanup = causal.load(model_path), causal.load(cleanup_path)
        if (
            model["status"] != "unforced_layer_handoff_model_qualified"
            or model["component_sha256"] != sha256_file(component_path)
            or model["native_engine"] != component["native_engine"]
            or model["configuration_sha256"] != sha256_file(configuration)
            or model["operator_sha256"] != sha256_file(app / "scripts/qualify_layer_handoff_model.py")
            or cleanup["remaining_processes"]
            or any(causal.reservation.alive(row) for row in cleanup["identities"])
        ):
            raise ValueError("model, component/configuration or actual cleanup is unverified")
        requests = model["model"]["requests"]
        for actual, reference in (("b_handoff", "b_reference"), ("a_recovered", "a_reference")):
            left, right = requests[actual], requests[reference]
            if (
                not left["unforced_sampling"] or not right["unforced_sampling"]
                or left["output_token_ids"] != right["output_token_ids"]
                or left["native_finish_reason"] != right["native_finish_reason"]
                or not 0 <= model["model"]["maximum_logprob_delta"][actual] <= .05
            ):
                raise ValueError("restored unforced model differs from independent reference")
        state = causal.qualified.last_runtime_state(
            HERE / "model_r1/arm/opportunities/admission_opportunities.jsonl",
        )
        if (
            state["physical_disabled"] or state["tool_prefetch"]
            or state["execution_frontier_restore"] or state["profile_shared_path"]
            or not state["layer_handoff_enabled"]
            or not any(
                row["reused_tokens"] > 0
                and (row["donor_group_layers"], row["consumer_group_layers"]) == (8, 4)
                for row in model["model"]["handoffs"]
            )
        ):
            raise ValueError("actual model runtime differs from declared Full configuration")
        result.update(
            model_qualified=True, maximum_logprob_delta=model["model"]["maximum_logprob_delta"],
            actual_model_owned_exit_verified=True,
        )
        paths += [model_path, cleanup_path, HERE / "cpu_native_binding.json", sources_path]
    else:
        causal.reservation.write_json(sources_path, observed)
        result["native_evidence"] = capture.engine_evidence(engine, native_report, sources_path)
    result["inputs_sha256"] = {str(path): sha256_file(path) for path in paths}
    result["limitations"] = [
        "CPU native trees use copy/event stand-ins; local cost is not whole-run or JCT benefit.",
        "Unchanged native components are reused only with exact tree/operator/group binding.",
        "The model fixture exercises demand handoff/recovery, not every JOIN ancestor topology.",
        "Complete same-work Full performance remains to be established.",
    ]
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "frozen_application", "passing_cpu_cases", "model_qualified",
    )}, indent=2))


if __name__ == "__main__":
    main()
