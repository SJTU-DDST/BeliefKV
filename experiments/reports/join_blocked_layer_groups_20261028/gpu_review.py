"""Verify the actual grouped component, latest CPU binding and owned exit."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_comparison import reservation

REPORTS = Path(__file__).parent
APPLICATION = ROOT.parent / ".worktrees/beliefkv-join-groups-component-retry-20261028"
ENGINE = ROOT.parent / ".worktrees/sglang-layer-groups-20261028"


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    component_path = REPORTS / "gpu_components_r2.json"
    component = json.loads(component_path.read_bytes())
    if (
        component["status"] != "native_gpu_layer_handoff_components_qualified"
        or component["direction_groups"] != {"d2h": 8, "h2d": 4}
        or [row["delayed_final_donor_layer"] for row in component["scenarios"]] != [False, True]
        or not component["scenarios"][1]["first_consumer_layer_read_before_final_donor_ack"]
    ):
        raise ValueError("actual grouped GPU scenarios are incomplete")
    for row in component["scenarios"]:
        if (
            any(row.get(name) is not True for name in (
                "all_donor_host_and_consumer_device_kv_verified",
                "protected_resident_kv_unchanged", "old_donor_reader_completed_before_overwrite",
                "actual_native_pool_layer_counter_and_accessors_used",
                "donor_early_return_blocked_until_backup_ack",
            ))
            or (row["initial_free_tokens"], row["post_submit_free_tokens"], row["final_free_tokens"])
            != (4, 4, 20)
        ):
            raise ValueError("actual native bytes, ordering or capacity did not verify")
    application = git_source_state(APPLICATION)
    native = git_source_state(ENGINE)
    if application["dirty"] or native != component["native_engine"]:
        raise ValueError("frozen application or native sources changed")
    sources = {
        str(APPLICATION / relative): sha256_file(APPLICATION / relative)
        for relative in (
            "scripts/qualify_layer_handoff.py", "scripts/qualify_free_page_h2d_fence.py",
            "configs/migration/predictive_join_blocked_layer_groups_20261028.json",
        )
    }
    if sources[str(APPLICATION / "scripts/qualify_layer_handoff.py")] != component["operator_sha256"]:
        raise ValueError("component operator differs from the frozen application")
    shared_path = REPORTS / "fixture_qualified_shared_cpu.evidence.json"
    shared = json.loads(shared_path.read_bytes())
    manifest = json.loads(Path(shared["source_manifest"]["path"]).read_bytes())
    if any(sha256_file(ROOT / name) != digest for name, digest in manifest.items()):
        raise ValueError("latest CPU-qualified application changed")
    pid = component["owned_gpu_pid"]
    nvml = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"], text=True,
    ).splitlines()
    if Path(f"/proc/{pid}").exists() or str(pid) in [row.strip() for row in nvml]:
        raise ValueError("owned GPU component has not actually exited")
    reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual native grouped copies and lifecycle, not a model or performance result.",
        "frozen_application": application, "native_engine": native,
        "component": str(component_path), "component_sha256": sha256_file(component_path),
        "frozen_component_sources": sources,
        "shared_cpu": {"passed": shared["passing_cases_including_subtests"], "new_cases": shared["new_cases"]},
        "shared_evidence": str(shared_path), "shared_evidence_sha256": sha256_file(shared_path),
        "owned_pid": pid, "owned_process_exited": True, "owned_nvml_context_absent": True,
        "remaining_gpu_pids": nvml,
        "actual_grouped_gpu_component_qualified": True,
        "unforced_model_qualified": False, "whole_workload_qualified": False,
        "performance_accepted": False, "operator_sha256": sha256_file(Path(__file__)),
    })
    print("Verified grouped GPU components, current CPU sources and actual owned exit.")


if __name__ == "__main__":
    main()
