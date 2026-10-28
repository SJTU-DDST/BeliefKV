"""Bind the tested JOIN/group candidate to its exact application and native sources."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import GROUPED_LAYER_TREE, LAYER_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_comparison import reservation

REPORTS = Path(__file__).parent
PREPARED = ROOT.parent / ".worktrees/sglang-layer-groups-comparison-source-20261028"
CLEAN = ROOT.parent / ".worktrees/sglang-layer-groups-20261028"


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    evidence_path = REPORTS / "qualified_shared_cpu.evidence.json"
    shared = json.loads(evidence_path.read_bytes())
    manifest = json.loads(Path(shared["source_manifest"]["path"]).read_bytes())
    if any(sha256_file(ROOT / name) != digest for name, digest in manifest.items()):
        raise ValueError("qualified application sources changed")
    patch = ROOT / "patches/sglang-layer-handoff-groups-runtime-20261028.patch"
    prepared = git_source_state(PREPARED, canonical_patch=patch)
    clean = git_source_state(CLEAN)
    tree = subprocess.check_output(
        ["git", "-C", str(CLEAN), "rev-parse", "HEAD^{tree}"], text=True,
    ).strip()
    if (
        clean["dirty"] or tree != GROUPED_LAYER_TREE
        or prepared["canonical_patch"]["actual_tree"] != GROUPED_LAYER_TREE
        or not prepared["canonical_patch"]["matches_worktree"]
    ):
        raise ValueError("clean and prepared native sources differ")
    expected = {
        "python/sglang/srt/mem_cache/l2_transfer.py",
        "python/sglang/srt/mem_cache/pool_host/mha.py",
        "python/sglang/srt/mem_cache/beliefkv_layer_handoff.py",
        "python/sglang/srt/mem_cache/unified_radix_cache.py",
        "test/srt/test_beliefkv_layer_groups.py",
    }
    changed = set(subprocess.check_output(
        ["git", "-C", str(CLEAN), "diff", "--name-only", LAYER_TREE, GROUPED_LAYER_TREE],
        text=True,
    ).splitlines())
    if changed != expected:
        raise ValueError("native grouping changed outside its reviewed scope")
    native_path = REPORTS / "native_final_cpu.xml"
    native_cases = ET.parse(native_path).getroot().findall(".//testcase")
    if not native_cases or any(
        row.find(tag) is not None for row in native_cases for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("native CPU checks did not all pass")
    full = shared["constructors"]["full"]["verified_configuration"]
    if not (
        full["agent_join_blocked_handoff"] and full["enable_predicted_tool_offload"]
        and not full["enable_tool_prefetch"]
        and (full["layer_handoff_d2h_layers"], full["layer_handoff_h2d_layers"]) == (8, 4)
    ):
        raise ValueError("actual Full constructor differs from the declared candidate")
    for label in ("thunderagent", "thunderagent_acting_decay"):
        control = shared["constructors"][label]["verified_configuration"]
        if control["agent_join_blocked_handoff"] or control["layer_handoff_enabled"]:
            raise ValueError("new candidate leaked into a paper baseline")
    cost_path = REPORTS / "control_cost_cpu.json"
    cost = json.loads(cost_path.read_bytes())
    if any(sha256_file(ROOT / name) != digest for name, digest in cost["sources"].items()):
        raise ValueError("callback-cost sources changed")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Source-bound CPU candidate, not new GPU or performance qualification.",
        "shared_passed": shared["passing_cases_including_subtests"],
        "shared_new_cases": shared["new_cases"],
        "native_cpu_passed": len(native_cases),
        "native_clean": clean, "native_prepared": prepared,
        "native_group_tree": tree, "native_group_delta_paths": sorted(changed),
        "full_constructor": full,
        "callback_cost": cost,
        "48_layer_groups": {
            "d2h_layers": 8, "h2d_layers": 4,
            "d2h_jit_calls": 6, "h2d_jit_calls": 12,
            "layer_completion_events": 18,
            "previous_copy_calls_and_layer_completion_events_each": 96,
            "call_count_reduction_percent": 81.25,
            "gpu_submission_time_gain_measured": False,
        },
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                evidence_path, Path(shared["source_manifest"]["path"]),
                Path(shared["report"]["path"]), native_path, cost_path, patch,
                REPORTS / "shared_cpu.xml",
                REPORTS / "nested_order_and_resume_filter_cpu.xml",
            )
        },
        "operator_sha256": sha256_file(Path(__file__)),
        "preserved_failures": [
            "initial_targeted_cpu.xml: two test-fixture defects; corrected without weakening production guards.",
            "shared_cpu.xml: one nested-harness event-time order failure; exact retry and final full execution pass.",
            "control_cost_initial_failure.json: observer interpreted event-count dictionary as an integer.",
        ],
        "actual_gpu_component_qualified": False,
        "actual_model_qualified": False,
        "performance_accepted": False,
    }
    reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "shared_passed", "shared_new_cases", "native_cpu_passed", "native_group_tree",
    )}, indent=2))


if __name__ == "__main__":
    main()
