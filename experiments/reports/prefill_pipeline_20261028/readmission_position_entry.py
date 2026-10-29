"""Bind the repaired source and model proofs before its fresh position entry."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
APPLICATION = ROOT.parent / ".worktrees/beliefkv-ack-readmission-5cf3bb8b-20261028"
OUTPUT = HERE / "readmission_position_entry.json"
NATIVE_SOURCES = HERE / "native_registration_ack_retry.sources.json"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.native_engine_variant import DEMAND_PREFILL_PIPELINE_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.qualify_free_page_h2d_fence import check_reservation


def main():
    if OUTPUT.exists() or NATIVE_SOURCES.exists():
        raise FileExistsError("preserve earlier repaired-source position entry")
    inputs = {}

    def load(path):
        inputs[str(path)] = sha256_file(path)
        return causal.load(path)

    old_position_path = OPS / "demand_prefill_pipeline_position_20261028_r1.registration.json"
    old_full_path = OPS / "demand_prefill_pipeline_20261028_r1.registration.json"
    repeat_path = OPS / "demand_prefill_pipeline_20261028_r2.registration.json"
    old_position, old_full, repeated = (
        load(path) for path in (old_position_path, old_full_path, repeat_path)
    )
    model = load(HERE / "readmission_gpu_review.json")
    cpu = load(HERE / "admission_retry_shared_r1_cpu.evidence.json")
    manifest_path = Path(cpu["source_manifest"]["path"])
    manifest = load(manifest_path)
    state = git_source_state(APPLICATION)
    if (
        state != model["application"] or state["dirty"]
        or state["commit"] != "5cf3bb8bf40daf48ee001988e60f46e517bdd345"
        or model["eligible_for_position_qualification"] is not True
        or model["deterministic_fixture_only"] is not True
        or model["native_tree"] != DEMAND_PREFILL_PIPELINE_TREE
        or len(manifest) != 1815 or cpu["passing_unique_case_identities"] != 4102
        or sha256_file(manifest_path) != cpu["source_manifest"]["sha256"]
    ):
        raise ValueError("current frozen source lacks the reviewed CPU/model qualification")
    causal.comparison.verify_cpu_snapshot(APPLICATION, manifest)
    for source, digest in model["inputs_sha256"].items():
        if sha256_file(Path(source)) != digest:
            raise ValueError("actual repaired-model proof changed: " + source)
        inputs[source] = digest
    if not causal.predecessor_ready({**repeated, "after_registration": str(repeat_path)}):
        raise ValueError("R2 or an owned child has not actually exited")
    causal.comparison.verify_sources(repeated)
    specification = APPLICATION / "configs/migration/predictive_demand_prefill_pipeline_20261028.json"
    spec = load(specification)
    if (
        sha256_file(specification) != sha256_file(Path(old_full["experiment_configuration"]))
        or spec["order"] != ["full"] or spec["stream_response_metrics"] is not True
        or any(spec["common_environment"].get(name) != value for name, value in {
            "ROOT_COUNT": "18", "MAX_RUNNING_REQUESTS": "24",
            "DEMAND_PREFILL_PIPELINE": "1", "TOOL_PREFETCH": "0",
            "TOOL_PREFETCH_PRIORITY_MS": "0", "EXECUTION_FRONTIER_RESTORE": "0",
            "BELIEFKV_DEMAND_PREFILL_PIPELINE_PROGRESS_PROBE": "0",
            "BELIEFKV_LAYER_HANDOFF_D2H_LAYERS": "8",
            "BELIEFKV_LAYER_HANDOFF_H2D_LAYERS": "4",
        }.items())
    ):
        raise ValueError("formal work, policy, probe or numerical configuration changed")
    prepared = Path(old_position["engine_cpu_checkout"])
    native = git_source_state(
        prepared, canonical_patch=engine_patch(APPLICATION, DEMAND_PREFILL_PIPELINE_TREE),
    )
    clean = Path(cpu["environment"]["SGLANG_SOURCE_CHECKOUT"])
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=clean, text=True,
    ).strip()
    scheduler = "python/sglang/srt/managers/scheduler.py"
    if (
        tree != DEMAND_PREFILL_PIPELINE_TREE
        or native["canonical_patch"]["actual_tree"] != tree
        or native["canonical_patch"]["matches_worktree"] is not True
        or sha256_file(prepared / scheduler) != cpu["native_scheduler_sha256"]
        or sha256_file(clean / scheduler) != cpu["native_scheduler_sha256"]
    ):
        raise ValueError("prepared replay engine is not the exact qualified native tree")
    authorization = check_reservation()
    causal.reservation.write_json(NATIVE_SOURCES, native)
    evidence = capture.engine_evidence(
        prepared, Path(old_position["engine_evidence"]["report"]), NATIVE_SOURCES,
    )
    if evidence["passing_cases"] != 92:
        raise ValueError("unchanged native CPU report population differs")
    inputs[str(NATIVE_SOURCES)] = sha256_file(NATIVE_SOURCES)
    inputs[evidence["report"]] = evidence["report_sha256"]
    inputs[str(engine_patch(APPLICATION, tree))] = sha256_file(engine_patch(APPLICATION, tree))
    inputs[str(Path(__file__))] = sha256_file(Path(__file__))
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Exact repaired source, complete predecessor and current-model entry for fresh position.",
        "application": state, "cpu_cases": 4102, "cpu_manifest_files": len(manifest),
        "native_registration_evidence": evidence,
        "native_sources_rebound_to_frozen_patch_path_only": True,
        "same_formal_configuration_as_R1_R2": True,
        "same_complete_work_and_normal_tails": True,
        "actual_ack_retry_branch_remains_CPU_evidence": True,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "deterministic_model_fixture_not_formal_math": True,
        "R2_complete_and_actual_owned_exit_verified": True,
        "authorization": authorization, "inputs_sha256": inputs,
        "position_gpu_started": False, "performance_accepted": False,
    }
    causal.reservation.write_json(OUTPUT, result)
    print(json.dumps({
        "entry": str(OUTPUT), "native_registration_evidence": evidence,
        "same_formal_configuration_as_R1_R2": True, "position_gpu_started": False,
    }, indent=2))


if __name__ == "__main__":
    main()
