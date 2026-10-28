"""Rebind only the canonical patch pathname after proving all other fields equal."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", required=True, type=Path)
    args = parser.parse_args()
    application = args.application.resolve()
    sys.path.insert(0, str(application))
    from beliefkv.experiments.native_engine_variant import SIBLING_CAPACITY_TREE, engine_patch
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_source_capture as capture
    from scripts import run_causal_comparison as causal

    prior_dir = ROOT / "experiments/reports/sibling_service_capacity_20261028"
    prior_path, report = prior_dir / "native_registration_sources.json", prior_dir / "native_targeted_r1.xml"
    output, review = HERE / "native_registration_sources.json", HERE / "native_binding_review.json"
    if output.exists() or review.exists():
        raise FileExistsError("preserve previous native binding")
    engine = ROOT.parent / ".worktrees/sglang-sibling-capacity-comparison-source-20261028"
    prior = causal.load(prior_path)
    try:
        capture.engine_evidence(engine, report, prior_path)
    except ValueError as error:
        rejection = repr(error)
    else:
        raise ValueError("old proof did not reproduce the recorded pathname rejection")
    observed = git_source_state(engine, canonical_patch=engine_patch(application, SIBLING_CAPACITY_TREE))
    expected = {
        **prior, "canonical_patch": {
            **prior["canonical_patch"], "path": observed["canonical_patch"]["path"],
        },
    }
    if (
        {key: value for key, value in expected.items() if key != "state_sha256"}
        != {key: value for key, value in observed.items() if key != "state_sha256"}
        or observed["canonical_patch"]["actual_tree"] != SIBLING_CAPACITY_TREE
    ):
        raise ValueError("native rebinding changed more than the canonical patch pathname")
    causal.reservation.write_json(output, observed)
    checked = capture.engine_evidence(engine, report, output)
    causal.reservation.write_json(review, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "pre_gpu_rejection": rejection,
        "original_registration_created": False,
        "only_changed_provenance_field": "canonical_patch.path",
        "state_digest_recomputed": True,
        "old_patch_path": prior["canonical_patch"]["path"],
        "new_patch_path": observed["canonical_patch"]["path"],
        "native_tree_unchanged": SIBLING_CAPACITY_TREE,
        "engine_evidence": checked,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            prior_path, report, output, Path(__file__),
        )},
        "gpu_work_started": False, "performance_accepted": False,
    })
    print(json.dumps(checked, indent=2))


if __name__ == "__main__":
    main()
