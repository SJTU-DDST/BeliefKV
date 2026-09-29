#!/usr/bin/env python3
"""Compare project-disjoint first RETURN cues on the same native requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_child_server_eos_cue import audit
from scripts.pilot_child_stream_content import evaluate


def summarize(rows: dict[str, dict], first: dict[str, float]) -> dict:
    returns = [rid for rid, row in rows.items() if row["label"] == "return"]
    tools = [rid for rid, row in rows.items() if row["label"] == "tool"]
    join_last = [rid for rid in returns if rows[rid]["join_last"]]
    lead = {
        rid: float(rows[rid]["return_ts"]) - first[rid]
        for rid in returns if rid in first
    }
    return {
        "return_rounds": len(returns),
        "tool_rounds": len(tools),
        "join_last_return_rounds": len(join_last),
        "return_first_triggers": len(lead),
        "tool_false_first_triggers": sum(rid in first for rid in tools),
        "join_last_first_triggers": sum(rid in first for rid in join_last),
        "return_500_to_2000ms": sum(500 <= dt <= 2000 for dt in lead.values()),
        "join_last_500_to_2000ms": sum(
            500 <= lead[rid] <= 2000 for rid in join_last if rid in lead
        ),
        "return_early_over_2000ms": sum(dt > 2000 for dt in lead.values()),
        "return_late_under_500ms": sum(dt < 500 for dt in lead.values()),
    }


def compare(run: Path, split: Path, tokenizer_json: Path) -> dict:
    selection = json.loads(split.read_text(encoding="utf-8"))
    fit = tuple(selection["fit_projects"])
    calibration = tuple(selection["calibration_projects"])
    heldouts = tuple(selection["heldout_projects"])
    if not fit or not calibration or not heldouts:
        raise ValueError("fit, calibration, and held-out projects are required")
    if len(set(fit + calibration + heldouts)) != len(fit + calibration + heldouts):
        raise ValueError("project split is not disjoint")
    selected = selection["instance_ids"]
    if len(set(selected)) != len(selected):
        raise ValueError("selected workflow tasks must be distinct")
    selected_projects = {task.split("__", 1)[0] for task in selected}
    if selected_projects != set(fit + calibration + heldouts):
        raise ValueError("selected tasks do not match the frozen project split")

    eos = audit(run, tokenizer_json, include_request_cues=True)
    cues = eos.pop("request_cues")
    comparison: dict[str, dict] = {}
    for project in heldouts:
        content = evaluate(
            run / "workloads/workflows", project,
            train_projects=fit, calibration_projects=calibration,
            include_first_triggers=True,
        )
        if content["status"] != "frozen_project_disjoint_calibrated_threshold":
            comparison[project] = {
                "status": content["status"],
                "train_rounds": content["train_rounds"],
                "calibration_rounds": content["calibration_rounds"],
                "heldout_rounds": content["heldout_rounds"],
            }
            continue
        rows = {rid: row for rid, row in cues.items() if row["project"] == project}
        eos_first = {
            rid: float(row["first_by_threshold"]["0.05"])
            for rid, row in rows.items()
            if "0.05" in row["first_by_threshold"]
        }
        near: dict[str, dict] = {}
        for name in (
            "size_only", "progress_only", "delivered_tail_plus_size",
            "progress_conditioned_content",
        ):
            result = content["results"][f"near_return_2000ms_{name}"]
            if result.get("status"):
                near[name] = {"status": result["status"]}
                continue
            first = result["first_trigger_by_request"]
            unknown = set(first) - set(cues)
            first = {rid: float(ts) for rid, ts in first.items() if rid in rows}
            joint = {
                rid: max(ts, eos_first[rid])
                for rid, ts in first.items() if rid in eos_first
            }
            near[name] = {
                "calibration_threshold": result["threshold_selected_on_calibration"],
                "first_triggers_excluded_by_measurement_gate": len(unknown),
                "model": summarize(rows, first),
                "model_and_eos": summarize(rows, joint),
            }
        comparison[project] = {
            "status": "project_disjoint_same_request_comparison",
            "qualified_requests": len(rows),
            "eos_0_05": summarize(rows, eos_first),
            "near_return_2000ms": near,
            "content_counts": content["counts"],
        }
    return {
        "scope": (
            "Read-only development evidence. Content thresholds use fit and disjoint "
            "calibration projects; EOS 0.05 was fixed in the prior Django/Pytest "
            "development run. Server EOS cues assume instantaneous delivery to the "
            "runtime and require actual delivered content. AND fires at the later "
            "first cue, not earlier. No physical H2D or online predictor is implied."
        ),
        "project_split": {
            "fit": fit, "calibration": calibration, "heldout": heldouts,
        },
        "clock_bridge": eos["clock_bridge"],
        "eos_excluded": eos["excluded"],
        "eos_invalid_child_requests": eos["invalid_child_requests"],
        "projects": comparison,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--tokenizer-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = compare(args.run, args.split, args.tokenizer_json)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
