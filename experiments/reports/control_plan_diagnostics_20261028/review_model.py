"""Bind unchanged native components to the current profiled unforced model."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]


def load(path):
    return json.loads(Path(path).read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", type=Path, required=True)
    args = parser.parse_args()
    output = HERE / "model_review.json"
    if output.exists():
        raise FileExistsError("preserve earlier model review")
    application = args.application.resolve()
    sys.path.insert(0, str(application))
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_comparison as causal

    component_path = ROOT / "experiments/reports/sibling_service_capacity_20261028/gpu_components_r1.json"
    component = load(component_path)
    evidence_path = HERE / "shared_cpu_r2.evidence.json"
    evidence = load(evidence_path)
    manifest = Path(evidence["source_manifest"]["path"])
    if sha256_file(manifest) != evidence["source_manifest"]["sha256"]:
        raise ValueError("CPU manifest changed")
    causal.comparison.verify_cpu_snapshot(application, load(manifest))
    app_state = git_source_state(application)
    if app_state["dirty"]:
        raise ValueError("application is not a clean frozen checkout")
    model_path, cleanup_path = HERE / "model_r1/report.json", HERE / "model_r1/owned_process_cleanup.json"
    model, cleanup = load(model_path), load(cleanup_path)
    configuration = application / "configs/migration/predictive_control_plan_diagnostics_20261028.json"
    if (
        component["status"] != "native_gpu_layer_handoff_components_qualified"
        or component["direction_groups"] != {"d2h": 8, "h2d": 4}
        or component["operator_sha256"] != sha256_file(application / "scripts/qualify_layer_handoff.py")
        or model["status"] != "unforced_layer_handoff_model_qualified"
        or model["component_sha256"] != sha256_file(component_path)
        or model["native_engine"] != component["native_engine"]
        or model["operator_sha256"] != sha256_file(application / "scripts/qualify_layer_handoff_model.py")
        or model["configuration_sha256"] != sha256_file(configuration)
    ):
        raise ValueError("current model/component/native/configuration binding failed")
    requests = model["model"]["requests"]
    for actual_name, reference_name in (
        ("b_handoff", "b_reference"), ("a_recovered", "a_reference"),
    ):
        actual, reference = requests[actual_name], requests[reference_name]
        if (
            not actual["unforced_sampling"] or not reference["unforced_sampling"]
            or actual["output_token_ids"] != reference["output_token_ids"]
            or actual["native_finish_reason"] != reference["native_finish_reason"]
            or not 0 <= model["model"]["maximum_logprob_delta"][actual_name] <= .05
        ):
            raise ValueError("unforced restored-model numerical comparison failed")
    if not any(
        row.get("event") == "layer_kv_handoff_submitted"
        and row["beneficiary_request_id"] == "layer-model:b_handoff"
        and (row["donor_group_layers"], row["consumer_group_layers"]) == (8, 4)
        for row in model["model"]["handoffs"]
    ):
        raise ValueError("current model did not actually consume a layer handoff")
    if cleanup["remaining_processes"] or any(
        causal.reservation.alive(row) for row in cleanup["identities"]
    ):
        raise ValueError("model qualification still owns processes")
    state = causal.qualified.last_runtime_state(
        HERE / "model_r1/arm/opportunities/admission_opportunities.jsonl",
    )
    timing = state["shared_path_timing"]
    if (
        state["profile_shared_path"] is not True
        or timing["schema_version"] != 2 or timing["sample_every"] != 64
        or timing["cpu_clock"] != "thread_time_ns" or not timing["phases"]
        or any("thread_cpu_self_ms" not in phase for phase in timing["phases"].values())
        or state["tool_prefetch"] or state["execution_frontier_restore"]
    ):
        raise ValueError("real runtime did not enable the declared sparse CPU diagnostics")
    paths = (
        component_path, evidence_path, manifest, model_path, cleanup_path,
        configuration, Path(__file__),
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Unchanged native component proof plus current unforced profiled model.",
        "frozen_application": app_state,
        "native_engine": model["native_engine"],
        "maximum_logprob_delta": model["model"]["maximum_logprob_delta"],
        "profile_observed_on_gpu_runtime": True,
        "profile_snapshot": timing,
        "model_owned_processes_exited": True,
        "inputs_sha256": {str(path): sha256_file(path) for path in paths},
        "performance_accepted": False,
        "limitations": [
            "Small unforced model fixture is not full workload, causal JCT or throughput evidence.",
            "Previously verified components are reused only with identical native state, operator and groups.",
            "Profiling samples are measured observations, without whole-run expansion.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({name: result[name] for name in (
        "frozen_application", "maximum_logprob_delta", "profile_observed_on_gpu_runtime",
    )}, indent=2))


if __name__ == "__main__":
    main()
