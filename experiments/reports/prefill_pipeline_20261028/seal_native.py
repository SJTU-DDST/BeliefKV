"""Bind CPU-tested scheduler bytes to a clean native commit and cumulative patch."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.demand_prefill_pipeline_engine import project_disabled_scheduler
from beliefkv.experiments.native_engine_variant import SIBLING_CAPACITY_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file


def main():
    output = HERE / "native_seal.json"
    if output.exists():
        raise FileExistsError("preserve previous native binding")
    proof_path = HERE / "shared_r3_cpu.evidence.json"
    proof = json.loads(proof_path.read_bytes())
    manifest_path = Path(proof["source_manifest"]["path"])
    if sha256_file(manifest_path) != proof["source_manifest"]["sha256"]:
        raise ValueError("qualified CPU source manifest changed")
    for relative, digest in json.loads(manifest_path.read_bytes()).items():
        if sha256_file(ROOT / relative) != digest:
            raise ValueError("CPU-tested source changed: " + relative)
    engine = Path(proof["environment"]["SGLANG_SOURCE_CHECKOUT"])
    before, current = proof["native_engine"], git_source_state(engine)
    relative = "python/sglang/srt/managers/scheduler.py"

    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=engine, text=True).strip()

    if (
        current["dirty"] or not before["dirty"] or before["untracked_paths"]
        or before["tracked_change_paths"] != [relative]
        or git("diff", "--name-only", before["commit"], current["commit"]).splitlines() != [relative]
        or git("rev-parse", before["commit"] + "^{tree}") != SIBLING_CAPACITY_TREE
        or sha256_file(engine / relative) != proof["native_scheduler_sha256"]
    ):
        raise ValueError("committed native code differs from the actual CPU-tested scheduler")
    project_disabled_scheduler(
        git("show", before["commit"] + ":" + relative), (engine / relative).read_text(),
    )
    base = "fa5cb52afaeaad1f59f6d3eb2c5cf124f72bb491"
    expected_tree = git("rev-parse", "HEAD^{tree}")
    patch = ROOT / "patches/sglang-demand-prefill-pipeline-runtime-20261028.patch"
    generated = subprocess.check_output(
        ["git", "diff", "--binary", base, current["commit"]], cwd=engine,
    )
    if patch.exists():
        if patch.read_bytes() != generated:
            raise ValueError("existing native patch differs; preserve it")
    else:
        with patch.open("xb") as stream:
            stream.write(generated)
    with tempfile.TemporaryDirectory(prefix="beliefkv-demand-native-patch-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        for arguments in (
            ["git", "read-tree", base],
            ["git", "apply", "--cached", str(patch)],
        ):
            subprocess.run(arguments, cwd=engine, env=env, check=True, capture_output=True)
        actual_tree = subprocess.check_output(
            ["git", "write-tree"], cwd=engine, env=env, text=True,
        ).strip()
    if actual_tree != expected_tree or git_source_state(engine) != current:
        raise ValueError("native patch does not reproduce the clean tested commit")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "CPU content identity and reproducible native packaging only.",
        "native_engine": current,
        "native_scheduler_sha256": proof["native_scheduler_sha256"],
        "cpu_evidence": {"path": str(proof_path), "sha256": sha256_file(proof_path)},
        "canonical_patch": {
            "path": str(patch), "sha256": sha256_file(patch), "base_commit": base,
            "expected_tree": expected_tree, "actual_tree": actual_tree,
            "reproduces_clean_cpu_tested_commit": True,
        },
        "default_off_projects_to_qualified_parent": True,
        "only_commit_metadata_changed_after_cpu_tests": True,
        "operator_sha256": sha256_file(Path(__file__)),
        "gpu_correctness_verified": False, "performance_accepted": False,
        "formal_engine_registration_created": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
