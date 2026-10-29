"""Bind the actual CPU run to an equal clean native tree without rerunning it."""

import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import DEMAND_PREFILL_PIPELINE_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file

HERE = Path(__file__).resolve().parent


def main():
    output = HERE / "complete_cpu.gpu_binding.json"
    if output.exists():
        raise FileExistsError(output)
    path = HERE / "complete_cpu.evidence.json"
    cpu = json.loads(path.read_bytes())
    manifest_path, report_path = (
        Path(cpu["source_manifest"]["path"]), Path(cpu["report"]["path"]),
    )
    if (
        sha256_file(manifest_path) != cpu["source_manifest"]["sha256"]
        or sha256_file(report_path) != cpu["report"]["sha256"]
    ):
        raise ValueError("actual CPU proof changed")
    for relative, digest in json.loads(manifest_path.read_bytes()).items():
        if sha256_file(ROOT / relative) != digest:
            raise ValueError("CPU-tested application source changed: " + relative)
    cases = ET.parse(report_path).findall(".//testcase")
    identities = {(row.attrib["classname"], row.attrib["name"]) for row in cases}
    if len(identities) != cpu["passing_cases"] or any(
        any(row.find(tag) is not None for tag in ("failure", "error", "skipped"))
        for row in cases
    ):
        raise ValueError("CPU case population changed")
    tested = Path(cpu["environment"]["SGLANG_SOURCE_CHECKOUT"])
    if git_source_state(tested) != cpu["native_source"]:
        raise ValueError("CPU-executed native source state changed")
    canonical = git_source_state(
        tested, canonical_patch=engine_patch(ROOT, DEMAND_PREFILL_PIPELINE_TREE),
    )["canonical_patch"]
    clean_path = ROOT.parent / ".worktrees/sglang-demand-prefill-pipeline-20261028"
    clean = git_source_state(clean_path)
    clean_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=clean_path, text=True,
    ).strip()
    if (
        clean["dirty"] or clean_tree != DEMAND_PREFILL_PIPELINE_TREE
        or canonical["actual_tree"] != clean_tree or not canonical["matches_worktree"]
    ):
        raise ValueError("clean native consumer is not the exact CPU-executed source tree")
    result = {
        **cpu, "passing_unique_case_identities": len(identities),
        "native_engine": clean, "clean_native_checkout": str(clean_path),
        "actual_cpu_evidence": {"path": str(path), "sha256": sha256_file(path)},
        "tested_prepared_native_canonical_projection": canonical,
        "actual_cpu_environment_retained": True, "new_cpu_execution": False,
        "binding_operator_sha256": sha256_file(Path(__file__)),
        "scope": "Original CPU execution retained; exact tree equality permits a clean native GPU entry. GPU remains unqualified.",
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"cpu_cases": len(identities), "native_tree": clean_tree,
                      "new_cpu_execution": False, "gpu_validation_pending": True}))


if __name__ == "__main__":
    main()
