"""Bind actual unforced generation, grouped components and the replay engine."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import GROUPED_LAYER_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_comparison import reservation
from scripts.run_causal_source_capture import engine_evidence

REPORTS = Path(__file__).parent
APPLICATION = ROOT.parent / ".worktrees/beliefkv-join-groups-component-retry-20261028"
ENGINE = ROOT.parent / ".worktrees/sglang-layer-groups-20261028"
PREPARED = ROOT.parent / ".worktrees/sglang-layer-groups-comparison-source-20261028"


def main():
    output = Path(__file__).with_suffix(".json")
    native_sources = REPORTS / "native_sources.json"
    if output.exists() or native_sources.exists():
        raise FileExistsError("preserve the existing model and native-source evidence")
    model_path = REPORTS / "model_r1/report.json"
    model = json.loads(model_path.read_bytes())
    component_path = REPORTS / "gpu_components_r2.json"
    component = json.loads(component_path.read_bytes())
    config = ROOT / "configs/migration/predictive_join_blocked_layer_groups_20261028.json"
    if (
        model["status"] != "unforced_layer_handoff_model_qualified"
        or model["configuration_sha256"] != sha256_file(config)
        or model["component_sha256"] != sha256_file(component_path)
        or model["native_engine"] != git_source_state(ENGINE)
        or component["direction_groups"] != {"d2h": 8, "h2d": 4}
        or git_source_state(APPLICATION)["dirty"]
        or model["operator_sha256"] != sha256_file(APPLICATION / "scripts/qualify_layer_handoff_model.py")
    ):
        raise ValueError("model, components, configuration or frozen sources changed")
    requests = model["model"]["requests"]
    for actual_name, reference_name in (
        ("b_handoff", "b_reference"), ("a_recovered", "a_reference"),
    ):
        actual, reference = requests[actual_name], requests[reference_name]
        if (
            actual["output_token_ids"] != reference["output_token_ids"]
            or not actual["unforced_sampling"] or not reference["unforced_sampling"]
            or actual["native_finish_reason"] != reference["native_finish_reason"]
            or model["model"]["maximum_logprob_delta"][actual_name] != 0
        ):
            raise ValueError("unforced restored output differs from its reference")
    handoffs = model["model"]["handoffs"]
    if not any(
        row["event"] == "layer_kv_handoff_submitted"
        and row["beneficiary_request_id"] == "layer-model:b_handoff"
        and (row["donor_group_layers"], row["consumer_group_layers"]) == (8, 4)
        and row["capacity_effect"] == "reserved_extent;no_free_page_credit"
        for row in handoffs
    ):
        raise ValueError("model did not exercise the actual grouped handoff")
    cleanup_path = REPORTS / "model_r1/owned_process_cleanup.json"
    cleanup = json.loads(cleanup_path.read_bytes())
    nvml = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"], text=True,
    ).splitlines()
    if cleanup["remaining_processes"] or Path(f"/proc/{model['server']['pid']}").exists() or nvml:
        raise ValueError("model process cleanup or GPU availability is incomplete")
    shared_path = REPORTS / "fixture_qualified_shared_cpu.evidence.json"
    shared = json.loads(shared_path.read_bytes())
    manifest = json.loads(Path(shared["source_manifest"]["path"]).read_bytes())
    if any(sha256_file(ROOT / name) != digest for name, digest in manifest.items()):
        raise ValueError("CPU-qualified application changed")
    patch = ROOT / "patches/sglang-layer-handoff-groups-runtime-20261028.patch"
    prepared = git_source_state(PREPARED, canonical_patch=patch)
    if prepared["canonical_patch"]["actual_tree"] != GROUPED_LAYER_TREE:
        raise ValueError("prepared replay engine differs from the qualified grouped engine")
    reservation.write_json(native_sources, prepared)
    native = engine_evidence(PREPARED, REPORTS / "native_final_cpu.xml", native_sources)
    reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual grouped transfer and unforced output correctness, not workload performance.",
        "frozen_application": git_source_state(APPLICATION),
        "native_engine": git_source_state(ENGINE),
        "native_replay_evidence": native,
        "maximum_logprob_delta": model["model"]["maximum_logprob_delta"],
        "actual_grouped_handoff_count": len(handoffs),
        "actual_grouped_model_qualified": True,
        "join_budget_handoff_gpu_service_qualified": False,
        "whole_workload_qualified": False,
        "performance_accepted": False,
        "model_server_exited": True,
        "gpu_compute_processes_absent": True,
        "inputs_sha256": {
            str(path): sha256_file(path)
            for path in (
                model_path, component_path, cleanup_path, config, shared_path,
                Path(shared["source_manifest"]["path"]), native_sources,
                *(REPORTS / "model_r1").glob("request_*.json"),
                *(REPORTS / "model_r1").glob("response_*.json"),
            )
        },
        "operator_sha256": sha256_file(Path(__file__)),
    })
    print("Verified unforced grouped model outputs, actual exit and prepared replay engine.")


if __name__ == "__main__":
    main()
