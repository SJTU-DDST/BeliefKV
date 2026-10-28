"""Bind the base-plus-patch replay checkout to the tested clean native tree."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import DEMAND_PREFILL_PIPELINE_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_source_capture import engine_evidence


def main():
    output = HERE / "prepared_native_seal.json"
    sources = HERE / "native_registration_sources.json"
    if output.exists() or sources.exists():
        raise FileExistsError("preserve earlier native packaging evidence")
    clean = ROOT.parent / ".worktrees/sglang-demand-prefill-pipeline-20261028"
    prepared = ROOT.parent / ".worktrees/sglang-demand-prefill-comparison-source-20261028"
    cpu_path = HERE / "deterministic_entry_r1_cpu.evidence.json"
    cpu = json.loads(cpu_path.read_bytes())
    patch = engine_patch(ROOT, DEMAND_PREFILL_PIPELINE_TREE)
    clean_state = git_source_state(clean)
    state = git_source_state(prepared, canonical_patch=patch)
    report = HERE / "native_registration_r1_cpu.xml"
    xml = ET.parse(report).getroot()
    cases = list(xml.iter("testcase"))
    scheduler = "python/sglang/srt/managers/scheduler.py"
    if (
        clean_state != cpu["native_engine"] or clean_state["dirty"]
        or state["commit"] != "fa5cb52afaeaad1f59f6d3eb2c5cf124f72bb491"
        or not state["canonical_patch"]["matches_worktree"]
        or state["canonical_patch"]["actual_tree"] != DEMAND_PREFILL_PIPELINE_TREE
        or state["canonical_patch"]["expected_tree"] != DEMAND_PREFILL_PIPELINE_TREE
        or sha256_file(prepared / scheduler) != cpu["native_scheduler_sha256"]
        or sha256_file(clean / scheduler) != cpu["native_scheduler_sha256"]
        or len(cases) != 92
        or len({(row.get("classname"), row.get("name")) for row in cases}) != 92
        or any(list(xml.iter(tag)) for tag in ("failure", "error", "skipped"))
    ):
        raise ValueError("prepared replay native code or CPU proof differs")
    with sources.open("x") as stream:
        json.dump(state, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence = engine_evidence(prepared, report, sources)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "clean_native": clean_state, "prepared_native": state,
        "native_registration_evidence": evidence,
        "same_native_source_tree": True,
        "scope": "Source packaging and native CPU evidence only; not new GPU/model/performance.",
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (cpu_path, patch, report, sources)
        },
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
