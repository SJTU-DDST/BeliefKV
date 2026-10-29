"""Bind the committed native delta and actual CPU report without GPU claims."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file

BASE = "d3da19c1"
TARGET = "c5ad39a1e1285532335139fa3bc33d49c7eabcf6"
CHANGED = (
    "python/sglang/srt/mem_cache/beliefkv_layer_handoff.py",
    "python/sglang/srt/mem_cache/l2_transfer.py",
    "python/sglang/srt/mem_cache/unified_radix_cache.py",
    "test/srt/test_beliefkv_layer_handoff.py",
    "test/srt/test_beliefkv_layer_groups.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    args = parser.parse_args()
    output = HERE / "native_seal.json"
    if output.exists():
        raise FileExistsError("preserve the native seal")
    cpu = json.loads(args.cpu_evidence.read_bytes())
    sources = json.loads(Path(cpu["source_manifest"]["path"]).read_bytes())
    native = git_source_state(args.engine)
    if (
        cpu["returncode"] != 0 or cpu["failed_error_skipped_cases"]
        or not cpu["all_retained_literal_identities_present"]
        or not cpu["sources_unchanged_during_execution"]
        or cpu["native_source"] != native or native["dirty"] or native["commit"] != TARGET
        or any(sha256_file(ROOT / path) != digest for path, digest in sources.items())
        or any(sha256_file(Path(cpu[field]["path"])) != cpu[field]["sha256"]
               for field in ("report", "source_manifest"))
    ):
        raise ValueError("current CPU or native source differs")
    patch = ROOT / "patches/sglang-layer-free-extent-delta-20261029.patch"
    delta = subprocess.check_output(
        ["git", "diff", BASE, TARGET, "--", *CHANGED], cwd=args.engine,
    )
    actual_changed = subprocess.check_output(
        ["git", "diff", "--name-only", BASE, TARGET], cwd=args.engine, text=True,
    ).splitlines()
    if delta != patch.read_bytes() or set(actual_changed) != set(CHANGED):
        raise ValueError("native delta contains other changes or differs from committed code")
    subprocess.run(
        ["git", "apply", "--reverse", "--check", str(patch)], cwd=args.engine, check=True,
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "native": native,
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", BASE], cwd=args.engine, text=True,
        ).strip(),
        "base_tree": subprocess.check_output(
            ["git", "rev-parse", BASE + "^{tree}"], cwd=args.engine, text=True,
        ).strip(),
        "target_tree": subprocess.check_output(
            ["git", "rev-parse", TARGET + "^{tree}"], cwd=args.engine, text=True,
        ).strip(),
        "changed_files": actual_changed,
        "delta_patch": {"path": str(patch), "sha256": hashlib.sha256(delta).hexdigest()},
        "exact_committed_diff_and_reverse_apply_check": True,
        "cpu_evidence": {"path": str(args.cpu_evidence), "sha256": sha256_file(args.cpu_evidence)},
        "operator_sha256": sha256_file(Path(__file__)),
        "GPU_qualification_pending": True, "formal_launch_enabled": False,
        "performance_accepted": False, "running_baseline_sources_changed": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"native_commit": TARGET, "target_tree": result["target_tree"],
                      "GPU_qualification_pending": True}, indent=2))


if __name__ == "__main__":
    main()
