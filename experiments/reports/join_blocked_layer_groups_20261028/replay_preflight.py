"""Bind the clean registration checkout without changing earlier native evidence."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
APPLICATION = ROOT.parent / ".worktrees/beliefkv-join-groups-comparison-source-20261028"
ENGINE = ROOT.parent / ".worktrees/sglang-layer-groups-comparison-source-20261028"
REPORTS = Path(__file__).parent
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.run_causal_source_capture import engine_evidence


def main():
    sources_path = REPORTS / "native_registration_sources.json"
    output = Path(__file__).with_suffix(".json")
    if sources_path.exists() or output.exists():
        raise FileExistsError("preserve previous replay preflight evidence")
    model = causal.load(REPORTS / "model_review.json")
    manifest_path = REPORTS / "fixture_qualified_shared_cpu.sources.json"
    manifest = causal.load(manifest_path)
    causal.comparison.verify_cpu_snapshot(APPLICATION, manifest)
    app = git_source_state(APPLICATION)
    if app["dirty"] or not model["actual_grouped_model_qualified"]:
        raise ValueError("registration requires qualified immutable sources")
    patch = APPLICATION / "patches/sglang-layer-handoff-groups-runtime-20261028.patch"
    prepared = git_source_state(ENGINE, canonical_patch=patch)
    previous = causal.load(REPORTS / "native_sources.json")
    for field in ("commit", "dirty", "tracked_change_tree_sha256"):
        if prepared[field] != previous[field]:
            raise ValueError("native prepared checkout changed: " + field)
    for field in ("sha256", "expected_tree", "actual_tree", "matches_worktree"):
        if prepared["canonical_patch"][field] != previous["canonical_patch"][field]:
            raise ValueError("canonical native patch changed: " + field)
    predecessor = ROOT.parent / "ops/host_demand_layer_handoff_20261028_r1.registration.json"
    prior = causal.load(predecessor)
    if not causal.predecessor_ready({**prior, "after_registration": str(predecessor)}):
        raise ValueError("previous complete Full has not actually exited")
    causal.reservation.write_json(sources_path, prepared)
    proof = engine_evidence(ENGINE, REPORTS / "native_final_cpu.xml", sources_path)
    causal.reservation.write_json(output, {
        "scope": "Registration source binding; no new GPU work or performance result.",
        "clean_application": app,
        "native_evidence": proof,
        "same_native_tree_and_patch_as_model": True,
        "cpu_manifest": str(manifest_path),
        "cpu_manifest_sha256": sha256_file(manifest_path),
        "model_review_sha256": sha256_file(REPORTS / "model_review.json"),
        "predecessor": str(predecessor),
        "predecessor_complete_and_exited": True,
        "operator_sha256": sha256_file(Path(__file__)),
    })
    print("Verified clean registration checkout, exact grouped engine and prior actual exit.")


if __name__ == "__main__":
    main()
