"""Independently bind the ACK repair's actual component and model I/O evidence."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CHECKOUT = ROOT.parent / ".worktrees/beliefkv-ack-readmission-5cf3bb8b-20261028"
DIRECTORY = ROOT / "experiments/raw/demand_prefill_ack_retry_model_20261028_r1"
spec = importlib.util.spec_from_file_location(
    "readmission_independent_model_helpers", HERE / "review_current_models.py",
)
REVIEW = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REVIEW)


def main():
    output = HERE / "readmission_gpu_review.json"
    if output.exists():
        raise FileExistsError("preserve earlier independent ACK repair I/O evidence")
    seals = {}
    frozen = REVIEW.load(HERE / "readmission_source_check.json", seals)
    cpu_path = HERE / "admission_retry_shared_r1_cpu.evidence.json"
    cpu = REVIEW.load(cpu_path, seals)
    manifest_path = Path(cpu["source_manifest"]["path"])
    manifest = REVIEW.load(manifest_path, seals)
    if (
        len(manifest) != 1815 or frozen["matched_cpu_manifest_files"] != 1815
        or REVIEW.sha256_file(manifest_path) != cpu["source_manifest"]["sha256"]
        or cpu["passing_unique_case_identities"] != 4102
        or any(REVIEW.sha256_file(CHECKOUT / path) != digest for path, digest in manifest.items())
    ):
        raise ValueError("frozen repair differs from its complete CPU evidence")
    component_path = HERE / "gpu_components_ack_retry_r1.json"
    component = REVIEW.load(component_path, seals)
    REVIEW.validate_scenarios(component["scenarios"])
    if (
        component["status"] != "native_gpu_demand_prefill_components_qualified"
        or component["application"] != REVIEW.git_source_state(CHECKOUT)
        or component["native_engine"] != cpu["native_engine"]
        or component["native_tree"] != REVIEW.DEMAND_PREFILL_PIPELINE_TREE
        or component["cpu_evidence"]["sha256"] != REVIEW.sha256_file(cpu_path)
        or Path(f"/proc/{component['owned_gpu_pid']}").exists()
    ):
        raise ValueError("current component sources, CPU binding or actual process exit differ")
    model = REVIEW.load(DIRECTORY / "report.json", seals)
    if (
        model["status"] != "unforced_demand_prefill_deterministic_model_qualified"
        or model["application"] != component["application"]
        or model["native_engine"] != component["native_engine"]
        or model["component_sha256"] != REVIEW.sha256_file(component_path)
        or model["operator_sha256"]
        != REVIEW.sha256_file(CHECKOUT / "scripts/qualify_demand_prefill_model.py")
        or model["fixture"]["model_outputs_forced"] is not False
        or model["fixture"]["artificial_copy_delay"] is not False
    ):
        raise ValueError("current actual model does not match the repaired component source")
    REVIEW.validate_model_parameters(
        REVIEW.load(DIRECTORY / "server_info.json", seals),
        deterministic=True, capacity=61440,
    )
    _, values = REVIEW.responses(DIRECTORY, seals, warm=False)
    numerical = REVIEW.compare_outputs(values)
    if numerical["maximum_logprob_delta"] != {"a_resident": 0., "b_demand": 0.}:
        raise ValueError("actual independent model outputs or logprobs differ")
    paths = {
        name: DIRECTORY / "arm" / relative for name, relative in (
            ("journals", "opportunities/admission_opportunities.jsonl"),
            ("workers", "server/runtime_audit.jsonl"),
            ("transfers", "server/transfer_telemetry.jsonl"),
        )
    }
    for path in paths.values():
        seals[str(path)] = REVIEW.sha256_file(path)
    records = {name: REVIEW.records(path) for name, path in paths.items()}
    progress = REVIEW.verify_progress(records["journals"], records["workers"])
    staged = REVIEW.inspect(records["journals"], records["transfers"], bytes_per_token=98304)
    service = REVIEW.service_evidence(staged["episodes"], records["journals"], records["workers"])
    if (
        staged["counts"] != {
            "matched_staging_episodes": 1, "native_selected_requests": 1,
            "native_full_tokens": 24583, "native_h2d_bytes": 2416607232,
        }
        or service["counts"] != {"members_with_subsequent_native_service": 1}
        or staged != model["model"]["staged_native_ack"]
        or service != model["model"]["subsequent_native_service"]
        or progress != model["model"]["progress"]
    ):
        raise ValueError("actual model progress, exact native ACK or later service differs")
    ready = [
        row for row in records["journals"]
        if row.get("event") == "native_demand_restore_ready"
    ]
    if len(ready) != 1 or type(ready[0].get("retry_native_capacity_check")) is not bool:
        raise ValueError("repaired model does not expose its actual completed-ACK branch")
    REVIEW.exited(DIRECTORY, model, seals)
    if subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"], text=True,
    ).strip():
        raise ValueError("model component or server still occupies the GPU")
    seals[str(HERE / "review_current_models.py")] = REVIEW.sha256_file(
        HERE / "review_current_models.py",
    )
    if any(REVIEW.sha256_file(Path(path)) != digest for path, digest in seals.items()):
        raise ValueError("independent component/model sources changed during review")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual repaired-source deterministic KV I/O and normal DMA progress; no JCT claim.",
        "application": component["application"], "native_tree": component["native_tree"],
        "cpu_cases": 4102, "component_scenarios": len(component["scenarios"]),
        "independent_exact_outputs": numerical,
        "normal_dma_model_progress": progress, "native_ack": staged,
        "subsequent_native_service": service,
        "actual_ack_native_capacity_retry_observed": ready[0]["retry_native_capacity_check"],
        "all_recorded_model_servers_exited": True, "gpu_compute_processes_absent": True,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "deterministic_fixture_only": True, "eligible_for_position_qualification": True,
        "whole_workload_qualified": False, "performance_accepted": False,
        "inputs_sha256": seals, "operator_sha256": REVIEW.sha256_file(Path(__file__)),
        "limitations": [
            "The model fixture did not exercise the stale-full retry branch; its real-native CPU proof remains separate.",
            "Deterministic fixture math is not the formal non-deterministic FA3/flashinfer benchmark mode.",
            "Prior ordinary-math long-greedy diagnostic failures remain unchanged and unqualified.",
            "Current position and complete workload performance remain unverified for this new repair.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "cpu_cases": result["cpu_cases"], "component_scenarios": result["component_scenarios"],
        "native_ack": staged["counts"], "exact_outputs": numerical,
        "actual_ack_native_capacity_retry_observed": result["actual_ack_native_capacity_retry_observed"],
        "eligible_for_position_qualification": True, "performance_accepted": False,
    }, indent=2))


if __name__ == "__main__":
    main()
