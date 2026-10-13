from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import time
import types
import xml.etree.ElementTree as ET


REPO = Path(__file__).resolve().parents[1]
SOURCE = "beliefkv/runtime/sglang_v0520_runtime.py"
sys.path.insert(0, str(REPO))

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def run_case(implementation, name, helpers):
    scope = helpers["final_stage_runtime"].__globals__
    original = scope["NativeAdmissionRuntime"]
    scope["NativeAdmissionRuntime"] = implementation
    runtime = None
    try:
        if name == "coalesced_notice_and_submit":
            runtime = helpers["final_stage_runtime"]()
            runtime._clear_final_stage("join")
            runtime._child_report_notices.clear()
            runtime.on_events((
                helpers["event"](
                    7, helpers["RuntimeEventKind"].STRUCTURED_ACTION,
                    invocation_id="child", context_id="ctx-child", context_epoch=0,
                    join_id="join",
                    attributes={"beliefkv_child_completion_intent": True,
                                "child_completion_signal_kind": "stage",
                                "estimated_final_report_tokens": 256},
                ),
                helpers["event"](
                    8, helpers["RuntimeEventKind"].LLM_SUBMIT,
                    invocation_id="child", context_id="ctx-child", context_epoch=1,
                    attributes={"request_id": "child"},
                ),
            ))
            stage = runtime._final_stages.get("join")
            result = {
                "bound_live_final_stage": bool(stage and runtime._live_final_stage(stage)),
                "bound_request_id": stage.request_id if stage else None,
            }
        else:
            runtime, first, second, ordinary = helpers["two_final_children"](
                sessions=name in ("best_already_first", "unknown_work"),
            )
            queue = [ordinary, first, second]
            if name == "best_already_first":
                queue = [second, first, ordinary]
            elif name == "unknown_work":
                first.output_ids = list(range(512))
                second.output_ids = list(range(64))
                queue = [second, first, ordinary]
            elif name == "live_semantic_work":
                runtime._final_stages["join2"].expected_tokens = 800
                runtime._semantic_forecasts[second.rid] = helpers["final_work_reply"](runtime, second)
            elif name == "aged_ordinary":
                runtime._visible_since[ordinary.rid] = time.monotonic() - 11.
            selection = helpers["select"](runtime, queue)
            result = {
                "first_selected": selection.candidates[0].rid,
                "priority_promoted": runtime._final_priority_promoted,
                "semantic_selection_count": runtime.counts["final_priority_selected_semantic_work"],
            }
        result["physical_actions_pending"] = runtime.physical_ledger.pending_count
        return result
    finally:
        if runtime is not None:
            runtime.close()
        scope["NativeAdmissionRuntime"] = original


def main():
    parser = argparse.ArgumentParser(
        description="Reproduce final-report admission against the recorded baseline; CPU only.",
    )
    parser.add_argument("--baseline", default="030b7dfe385a681f31b0e6b1b6e2ce36b49f2e73")
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old_source = subprocess.check_output(["git", "show", f"{args.baseline}:{SOURCE}"], cwd=REPO)
    old_module = types.ModuleType("beliefkv.runtime._final_admission_baseline")
    sys.modules[old_module.__name__] = old_module
    exec(compile(old_source, f"{args.baseline}:{SOURCE}", "exec"), old_module.__dict__)
    helpers = runpy.run_path(str(REPO / "tests/test_sglang_v0520_runtime.py"))
    names = (
        "coalesced_notice_and_submit", "shorter_notice", "best_already_first",
        "unknown_work", "live_semantic_work", "aged_ordinary",
    )
    results = {
        name: {
            "baseline": run_case(old_module.NativeAdmissionRuntime, name, helpers),
            "current": run_case(NativeAdmissionRuntime, name, helpers),
        } for name in names
    }
    assert not results["coalesced_notice_and_submit"]["baseline"]["bound_live_final_stage"]
    assert results["coalesced_notice_and_submit"]["current"]["bound_live_final_stage"]
    for name in ("shorter_notice", "best_already_first", "unknown_work", "live_semantic_work"):
        assert results[name]["baseline"]["first_selected"] == "child"
        assert results[name]["current"]["first_selected"] == "child2"
    assert results["live_semantic_work"]["current"]["semantic_selection_count"] == 1
    for arm in ("baseline", "current"):
        assert results["aged_ordinary"][arm]["first_selected"] == "other"
        assert all(value[arm]["physical_actions_pending"] == 0 for value in results.values())

    suites = ET.parse(args.junit).getroot()
    assert all(int(suite.get("failures", 0)) == int(suite.get("errors", 0)) == 0 for suite in suites)
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "scope": "Bounded CPU admission/identity reproduction; not measured GPU or JCT improvement.",
        "baseline_commit": subprocess.check_output(
            ["git", "rev-parse", args.baseline], cwd=REPO, text=True,
        ).strip(),
        "runtime_source_sha256": hashlib.sha256((REPO / SOURCE).read_bytes()).hexdigest(),
        "baseline_runtime_source_sha256": hashlib.sha256(old_source).hexdigest(),
        "qualification_helper_path": str(Path(__file__).relative_to(REPO)),
        "qualification_helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "test_source_sha256": hashlib.sha256(
            (REPO / "tests/test_sglang_v0520_runtime.py").read_bytes(),
        ).hexdigest(),
        "junit_sha256": hashlib.sha256(args.junit.read_bytes()).hexdigest(),
        "test_suites": [dict(suite.attrib) for suite in suites],
        "cases": results,
        "safety_scope": (
            "Soft admission within the existing four-normal-admission quotas and aging; "
            "notice binding remains specific to the exact last-child/request/epoch; "
            "coalesced later tool/RETURN cases are covered by the recorded regression suite. "
            "No capacity reservation, transfer authorization, incomplete-prefix execution or new agent guard."
        ),
        "deployment": (
            "Followup candidate only. Main V49 and queued V50 checkout are unchanged. "
            "Production prevalence of lost coalesced notices is not known from old counters; "
            "new per-second cumulative counters enable cold-run validation."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "suites": report["test_suites"], "cases": results}, indent=2))


if __name__ == "__main__":
    main()
