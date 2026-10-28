"""Bind current components, unforced model outputs and the prepared replay tree."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
REPORTS = Path(__file__).resolve().parent
APPLICATION = ROOT.parent / ".worktrees/beliefkv-sibling-capacity-gpu-100ec17e"
ENGINE = ROOT.parent / ".worktrees/sglang-sibling-capacity-20261028"
PREPARED = ROOT.parent / ".worktrees/sglang-sibling-capacity-comparison-source-20261028"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.native_engine_variant import SIBLING_CAPACITY_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.run_causal_source_capture import engine_evidence


def load(path):
    return json.loads(path.read_bytes())


def main():
    output = REPORTS / "gpu_model_review.json"
    native_sources = REPORTS / "native_registration_sources.json"
    if output.exists() or native_sources.exists():
        raise FileExistsError("preserve existing qualification evidence")
    component_path = REPORTS / "gpu_components_r1.json"
    model_path = REPORTS / "model_r1/report.json"
    cleanup_path = REPORTS / "model_r1/owned_process_cleanup.json"
    shared_path = REPORTS / "qualified_shared_cpu_r2.evidence.json"
    model, component, shared = map(load, (model_path, component_path, shared_path))
    configuration = APPLICATION / "configs/migration/predictive_sibling_service_capacity_20261028.json"
    current_engine = git_source_state(ENGINE)
    if (
        git_source_state(APPLICATION)["dirty"] or current_engine["dirty"]
        or component["status"] != "native_gpu_layer_handoff_components_qualified"
        or component["native_engine"] != current_engine
        or component["direction_groups"] != {"d2h": 8, "h2d": 4}
        or model["status"] != "unforced_layer_handoff_model_qualified"
        or model["native_engine"] != current_engine
        or model["configuration_sha256"] != sha256_file(configuration)
        or model["component_sha256"] != sha256_file(component_path)
        or model["operator_sha256"] != sha256_file(
            APPLICATION / "scripts/qualify_layer_handoff_model.py"
        )
        or component["operator_sha256"] != sha256_file(
            APPLICATION / "scripts/qualify_layer_handoff.py"
        )
    ):
        raise ValueError("frozen application, native engine or actual GPU proof changed")
    scenes = component["scenarios"]
    required = (
        "all_donor_host_and_consumer_device_kv_verified",
        "protected_resident_kv_unchanged",
        "old_donor_reader_completed_before_overwrite",
        "actual_native_pool_layer_counter_and_accessors_used",
        "donor_early_return_blocked_until_backup_ack",
    )
    if (
        len(scenes) != 2
        or {row["delayed_final_donor_layer"] for row in scenes} != {False, True}
        or any(
            any(row.get(key) is not True for key in required)
            or (row["initial_free_tokens"], row["post_submit_free_tokens"],
                row["final_free_tokens"]) != (4, 4, 20)
            or row["delayed_final_donor_layer"]
            and not row["first_consumer_layer_read_before_final_donor_ack"]
            for row in scenes
        )
    ):
        raise ValueError("real GPU byte, writer or reserved-capacity checks incomplete")
    requests = model["model"]["requests"]
    for actual_name, reference_name in (
        ("b_handoff", "b_reference"), ("a_recovered", "a_reference"),
    ):
        actual, reference = requests[actual_name], requests[reference_name]
        if (
            actual["output_token_ids"] != reference["output_token_ids"]
            or not actual["unforced_sampling"] or not reference["unforced_sampling"]
            or actual["native_finish_reason"] != reference["native_finish_reason"]
            or not 0 <= model["model"]["maximum_logprob_delta"][actual_name] <= .05
        ):
            raise ValueError("unforced restored model output failed numerical comparison")
    handoffs = model["model"]["handoffs"]
    if not any(
        row["event"] == "layer_kv_handoff_submitted"
        and row["beneficiary_request_id"] == "layer-model:b_handoff"
        and (row["donor_group_layers"], row["consumer_group_layers"]) == (8, 4)
        and row["capacity_effect"] == "reserved_extent;no_free_page_credit"
        for row in handoffs
    ):
        raise ValueError("model did not consume an actual grouped native handoff")
    cleanup = load(cleanup_path)
    nvml = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        text=True,
    ).splitlines()
    if (
        cleanup["remaining_processes"] or nvml
        or Path(f"/proc/{model['server']['pid']}").exists()
        or Path(f"/proc/{component['owned_gpu_pid']}").exists()
    ):
        raise ValueError("GPU qualification processes have not actually exited")
    manifest_path = Path(shared["source_manifest"]["path"])
    if sha256_file(manifest_path) != shared["source_manifest"]["sha256"]:
        raise ValueError("CPU manifest changed")
    causal.comparison.verify_cpu_snapshot(APPLICATION, load(manifest_path))
    report_path = Path(shared["report"]["path"])
    if sha256_file(report_path) != shared["report"]["sha256"]:
        raise ValueError("CPU test report changed")
    patch = engine_patch(APPLICATION, SIBLING_CAPACITY_TREE)
    prepared = git_source_state(PREPARED, canonical_patch=patch)
    prior = shared["native_engine"]
    for field in ("commit", "dirty", "tracked_change_tree_sha256"):
        if prepared[field] != prior[field]:
            raise ValueError("CPU-qualified native state changed: " + field)
    for field in ("sha256", "expected_tree", "actual_tree", "matches_worktree"):
        if prepared["canonical_patch"][field] != prior["canonical_patch"][field]:
            raise ValueError("native patch or canonical tree changed: " + field)
    if prepared["canonical_patch"]["actual_tree"] != SIBLING_CAPACITY_TREE:
        raise ValueError("prepared replay tree differs from the GPU-qualified engine")
    native_report = REPORTS / "native_targeted_r1.xml"
    causal.reservation.write_json(native_sources, prepared)
    native = engine_evidence(PREPARED, native_report, native_sources)
    paths = (
        component_path, model_path, cleanup_path, shared_path, manifest_path,
        report_path, configuration, native_sources, native_report,
        *(REPORTS / "model_r1").glob("request_*.json"),
        *(REPORTS / "model_r1").glob("response_*.json"),
    )
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Current native GPU bytes and unforced model correctness; performance pending.",
        "frozen_application": git_source_state(APPLICATION),
        "native_gpu_engine": current_engine,
        "native_replay_cpu_evidence": native,
        "maximum_logprob_delta": model["model"]["maximum_logprob_delta"],
        "actual_grouped_handoff_count": len(handoffs),
        "actual_grouped_components_and_model_qualified": True,
        "logical_join_and_sibling_gpu_service_qualified": False,
        "whole_workload_qualified": False,
        "performance_accepted": False,
        "model_server_and_component_exited": True,
        "gpu_compute_processes_absent": True,
        "native_patch_path_rebound_to_identical_frozen_bytes": True,
        "inputs_sha256": {str(path): sha256_file(path) for path in paths},
        "operator_sha256": sha256_file(Path(__file__)),
    })
    print("Verified current GPU bytes, unforced model, owned exit and exact replay engine.")


if __name__ == "__main__":
    main()
