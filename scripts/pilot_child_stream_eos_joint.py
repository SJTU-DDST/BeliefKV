#!/usr/bin/env python3
"""Request-matched, development-only EOS/content first-trigger comparison."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_child_stream_content import (
    _OPEN_ISSUE, collect, evaluate as content_evaluate,
)

THRESHOLDS = ("top20", "0.0001", "0.001", "0.01", "0.05", "0.1", "0.25")
GATES = (
    "eos_only",
    "chars_128",
    "chars_512",
    "elapsed_4000ms",
    "chars_512_elapsed_4000ms",
    "sentence_end_no_open_issue",
)


def first_eos_events(row: dict, events: list[dict], tool_chunk_ts: float) -> dict[str, float]:
    first_content_ts = row["snapshots"][0]["ts_ms"]
    found: dict[str, float] = {}
    for event in events:
        attrs = event.get("attributes") or {}
        if (
            event.get("kind") != "structured_action"
            or event.get("invocation_id") != row["invocation_id"]
            or event.get("context_id") != row["context_id"]
            or event.get("context_epoch") != row["context_epoch"]
            or attrs.get("request_id") != row["rid"]
        ):
            continue
        ts = float(event["ts_ms"])
        if not first_content_ts <= ts < min(row["result_ts"], tool_chunk_ts):
            continue
        if attrs.get("beliefkv_child_eos_first_top_hit_shadow"):
            threshold = "top20"
        elif attrs.get("beliefkv_child_eos_shadow") and type(
            attrs.get("eos_top_probability_threshold")
        ) in (int, float):
            threshold = str(attrs["eos_top_probability_threshold"])
        else:
            continue
        if threshold in THRESHOLDS:
            found[threshold] = min(ts, found.get(threshold, ts))
    return found


def score(rows: list[dict], threshold: str) -> dict:
    positives = [row for row in rows if row["label"] == "return"]
    tools = [row for row in rows if row["label"] == "tool"]
    triggered = [row for row in positives if threshold in row["eos"]]
    lead = [
        row["return_ts"] - row["eos"][threshold]
        for row in triggered
    ]
    return {
        "return_rounds": len(positives),
        "tool_rounds_with_content": len(tools),
        "first_triggered_return": len(triggered),
        "first_triggered_tool": sum(threshold in row["eos"] for row in tools),
        "return_trigger_early_over_2000ms": sum(t > 2000 for t in lead),
        "return_trigger_500_to_2000ms": sum(500 <= t <= 2000 for t in lead),
        "return_trigger_late_under_500ms": sum(t < 500 for t in lead),
        "join_last_rounds": sum(row["join_last"] for row in positives),
        "join_last_500_to_2000ms": sum(
            row["join_last"]
            and threshold in row["eos"]
            and 500 <= row["return_ts"] - row["eos"][threshold] <= 2000
            for row in positives
        ),
        "window_hits_by_workflow": dict(sorted(Counter(
            row["task"] for row in triggered
            if 500 <= row["return_ts"] - row["eos"][threshold] <= 2000
        ).items())),
    }


def snapshot_gate_accepts(row: dict, observed: dict, ts: float, gate: str) -> bool:
    """Inspect only content delivered at or before an EOS observation."""
    snapshots = row["snapshots"]
    chars = observed["content_chars"]
    elapsed = ts - snapshots[0]["ts_ms"]
    if gate == "eos_only":
        return True
    if gate == "chars_128":
        return chars >= 128
    if gate == "chars_512":
        return chars >= 512
    if gate == "elapsed_4000ms":
        return elapsed >= 4000
    if gate == "chars_512_elapsed_4000ms":
        return chars >= 512 and elapsed >= 4000
    if gate == "sentence_end_no_open_issue":
        text = observed["content_tail"].rstrip()
        return (
            text.endswith((".", "!", "?", "。", "！", "？"))
            and not _OPEN_ISSUE.search(text)
        )
    raise ValueError(f"unknown gate: {gate}")


def gate_accepts(row: dict, threshold: str, gate: str) -> bool:
    """Legacy first-crossing trace: never reuse content delivered later."""
    ts = row["eos"].get(threshold)
    if ts is None:
        return False
    observed = next((
        snap for snap in reversed(row["snapshots"])
        if snap["ts_ms"] <= ts
    ), None)
    return (
        observed is not None
        and snapshot_gate_accepts(row, observed, ts, gate)
    )


def snapshot_eos_ts(row: dict, threshold: str, gate: str) -> float | None:
    """First eligible sampled EOS evidence, not an inferred persistent state."""
    floor = (
        float("-inf") if threshold == "top20"
        else math.log(float(threshold))
    )
    for snap in row["snapshots"]:
        value = snap["eos_shadow_max_logprob_since_previous_snapshot"]
        if (
            type(value) in (float, int) and math.isfinite(value)
            and value >= floor
            and snapshot_gate_accepts(row, snap, snap["ts_ms"], gate)
        ):
            return float(snap["ts_ms"])
    return None


def sampled_eos_window_diagnostic(rows: list[dict], threshold: str) -> dict:
    """Re-observation is a diagnostic upper bound, not an online trigger policy."""
    floor = float("-inf") if threshold == "top20" else math.log(float(threshold))
    result: Counter = Counter()
    for row in rows:
        if row["label"] != "return":
            continue
        result["return_rounds"] += 1
        snapshots = row["snapshots"]
        window = [
            snap for snap in snapshots
            if 500 <= row["return_ts"] - snap["ts_ms"] <= 2000
        ]
        if window:
            result["return_with_window_snapshot"] += 1
        observed = [
            snap for snap in snapshots
            if (
                (value := snap["eos_shadow_max_logprob_since_previous_snapshot"])
                is not None and math.isfinite(value) and value >= floor
            )
        ]
        if not observed:
            continue
        first = observed[0]
        lead = row["return_ts"] - first["ts_ms"]
        if lead <= 2000:
            continue
        result["early_first_trigger"] += 1
        if row["join_last"]:
            result["join_last_early_first_trigger"] += 1
        if not window:
            continue
        result["early_with_window_snapshot"] += 1
        later_window_eos = any(snap in observed for snap in window)
        if later_window_eos:
            result["early_with_later_window_eos"] += 1
            if row["join_last"]:
                result["join_last_early_with_later_window_eos"] += 1
    return {
        key: result[key] for key in (
            "return_rounds", "return_with_window_snapshot",
            "early_first_trigger", "early_with_window_snapshot",
            "early_with_later_window_eos",
            "join_last_early_first_trigger",
            "join_last_early_with_later_window_eos",
        )
    }


def gated_score(rows: list[dict], threshold: str, gate: str) -> dict:
    sampled = bool(rows and "eos_shadow_max_logprob_since_previous_snapshot"
                   in rows[0]["snapshots"][0])
    if sampled:
        raw = [
            snapshot_eos_ts(row, threshold, "eos_only") for row in rows
        ]
        accepted = [
            snapshot_eos_ts(row, threshold, gate) for row in rows
        ]
    else:
        # A rejected first crossing cannot be delayed in legacy traces.
        raw = [row["eos"].get(threshold) for row in rows]
        accepted = [
            row["eos"].get(threshold) if gate_accepts(row, threshold, gate)
            else None for row in rows
        ]
    gated = [
        {**row, "eos": (
            {threshold: ts} if ts is not None else {}
        )}
        for row, ts in zip(rows, accepted)
    ]
    result = score(gated, threshold)
    result["gate_rejected_return_crossings"] = sum(
        row["label"] == "return" and crossing is not None and accepted_ts is None
        for row, crossing, accepted_ts in zip(rows, raw, accepted)
    )
    result["gate_rejected_tool_crossings"] = sum(
        row["label"] == "tool" and crossing is not None and accepted_ts is None
        for row, crossing, accepted_ts in zip(rows, raw, accepted)
    )
    return result


def select_policy(train: list[dict]) -> tuple[tuple[str, str] | None, dict]:
    results = {
        f"{threshold}|{gate}": gated_score(train, threshold, gate)
        for threshold in THRESHOLDS for gate in GATES
    }
    eligible = [
        (threshold, gate)
        for threshold in THRESHOLDS for gate in GATES
        if (
            (result := results[f"{threshold}|{gate}"])["return_rounds"] >= 10
            and result["tool_rounds_with_content"] >= 20
            and result["first_triggered_tool"]
            / result["tool_rounds_with_content"] <= 0.05
            and result["return_trigger_early_over_2000ms"]
            / result["return_rounds"] <= 0.1
        )
    ]
    selected = max(
        eligible, key=lambda pair: (
            results["|".join(pair)]["return_trigger_500_to_2000ms"],
            -results["|".join(pair)]["first_triggered_tool"],
            -results["|".join(pair)]["return_trigger_early_over_2000ms"],
            -GATES.index(pair[1]),
            -THRESHOLDS.index(pair[0]),
        ),
    ) if eligible else None
    return selected, results


def load_rows(
    workflows: list[Path],
) -> tuple[list[dict], Counter, list[dict], bool]:
    rows, counts = collect(workflows, min_snapshot_chars=1)
    by_task: dict[str, list[dict]] = {}
    eos_manifests = []
    for root in workflows:
        manifest_path = root.parent / "manifest.json"
        if manifest_path.is_file():
            config = json.loads(manifest_path.read_text()).get("config") or {}
            eos_manifests.append({
                "workflows": str(root),
                "collected": bool(config.get("child_eos_shadow")),
                "top_hit": bool(config.get("child_eos_top_hit_shadow")),
            })
        for path in root.glob("*/runtime_events.deepagents.jsonl"):
            by_task[path.parent.name] = [
                json.loads(line) for line in path.read_text().splitlines()
            ]
    if not eos_manifests or not all(item["collected"] for item in eos_manifests):
        raise ValueError("all workflow roots must have EOS collection enabled")

    content_files = {
        path.parent.name: path
        for root in workflows for path in root.glob("*/child_stream_content.jsonl")
    }
    tool_chunk_by_task: dict[str, dict[str, float]] = {}
    for task, path in content_files.items():
        first_tool: dict[str, float] = {}
        for line in path.read_text().splitlines():
            observation = json.loads(line)
            if observation["event"] == "child_stream_content" and observation["tool_chunk"]:
                rid = observation["request_id"]
                first_tool[rid] = min(
                    float(observation["ts_ms"]),
                    first_tool.get(rid, float("inf")),
                )
        tool_chunk_by_task[task] = first_tool
    for row in rows:
        row["eos"] = first_eos_events(
            row, by_task[row["task"]],
            tool_chunk_by_task[row["task"]].get(row["rid"], float("inf")),
        )
    sampled = [
        "eos_shadow_max_logprob_since_previous_snapshot" in snap
        for row in rows for snap in row["snapshots"]
    ]
    if any(sampled) and not all(sampled):
        raise ValueError("cannot mix sampled and legacy EOS snapshot evidence")
    has_sampled_eos = bool(sampled and sampled[0])
    return rows, counts, eos_manifests, has_sampled_eos


def evaluate(workflows: list[Path], heldout_project: str) -> dict:
    rows, counts, eos_manifests, has_sampled_eos = load_rows(workflows)
    train = [row for row in rows if row["project"] != heldout_project]
    held = [row for row in rows if row["project"] == heldout_project]
    if not train or not held or not (
        has_sampled_eos or any(row["eos"] for row in rows)
    ):
        raise ValueError("not enough content/EOS rounds for project holdout")
    train_scores = {
        threshold: gated_score(train, threshold, "eos_only")
        for threshold in THRESHOLDS
    }
    held_scores = {
        threshold: gated_score(held, threshold, "eos_only")
        for threshold in THRESHOLDS
    }
    # Fixed training-only gate. Fail closed when no threshold meets both risks.
    admissible = [
        threshold for threshold, result in train_scores.items()
        if result["tool_rounds_with_content"] >= 20
        and result["return_rounds"] >= 10
        and result["first_triggered_tool"] / result["tool_rounds_with_content"] <= 0.05
        and result["return_trigger_early_over_2000ms"]
        / result["return_rounds"] <= 0.1
    ]
    selected = max(
        admissible, key=lambda threshold: (
            train_scores[threshold]["return_trigger_500_to_2000ms"],
            -train_scores[threshold]["first_triggered_tool"],
        ),
    ) if admissible else None
    fused_selected, fused_train_scores = select_policy(train)
    fused_held_score = (
        gated_score(held, *fused_selected) if fused_selected else None
    )
    previous_report = (
        workflows[-1].parent.parent
        / f"eos_joint_matched_holdout_{heldout_project.split('-', 1)[0]}.json"
    )
    cached = json.loads(previous_report.read_text()) if previous_report.is_file() else {}
    if (
        cached.get("heldout_project") == heldout_project
        and cached.get("collected_manifests") == eos_manifests
        and cached.get("counts") == dict(counts)
        and "same_rows_length_progress_baselines" in cached
    ):
        baselines_result = cached["same_rows_length_progress_baselines"]
        baselines_source = str(previous_report)
    else:
        baselines = content_evaluate(workflows, heldout_project, min_snapshot_chars=1)
        baselines_result = (
            baselines["results"] if baselines["status"].startswith("diagnostic_")
            else baselines
        )
        baselines_source = "recomputed"
    keys = (
        "near_return_2000ms_size_only",
        "near_return_2000ms_progress_only",
        "near_return_2000ms_progress_conditioned_content",
        "near_return_2000ms_progress_conditioned_phase",
    )
    return {
        "scope": "development only: no physical transfer; top-k absence is unknown, not P(EOS)=0",
        "eos_evidence": (
            "max candidate per content-snapshot interval; trigger at delivered snapshot, "
            "not the exact token crossing" if has_sampled_eos else
            "only first EOS threshold crossing per request; no recheck after content gate"
        ),
        "heldout_project": heldout_project,
        "train_projects": sorted({row["project"] for row in train}),
        "collected_manifests": eos_manifests,
        "counts": dict(counts),
        "return_with_first_content": sum(row["label"] == "return" for row in rows),
        "tool_with_first_content": sum(row["label"] == "tool" for row in rows),
        "train_eos_threshold_scores": train_scores,
        "heldout_eos_threshold_scores": held_scores,
        "selected_threshold_from_train": selected,
        "selected_heldout_score": held_scores[selected] if selected else None,
        "selected_heldout_eos_reobservation": (
            sampled_eos_window_diagnostic(held, selected)
            if selected and has_sampled_eos else None
        ),
        "fused_gate_scope": (
            "Training projects alone select EOS threshold and causal content gate. "
            "An EOS candidate and content must coexist in a delivered snapshot "
            "interval; unseen top-k candidates are unknown."
            if has_sampled_eos else
            "Training projects alone select EOS threshold and same-moment "
            "causal content gate. A rejected first crossing cannot be "
            "reconsidered: subsequent EOS probabilities were not collected."
        ),
        "train_fused_policy_scores": fused_train_scores,
        "selected_fused_policy_from_train": (
            {"threshold": fused_selected[0], "gate": fused_selected[1]}
            if fused_selected else None
        ),
        "selected_fused_heldout_score": fused_held_score,
        "same_rows_length_progress_baselines_source": baselines_source,
        "same_rows_length_progress_baselines": {
            key: baselines_result[key] for key in keys
        } if "near_return_2000ms_size_only" in baselines_result else baselines_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.workflows, args.heldout_project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "heldout_project": args.heldout_project,
        "threshold": report["selected_threshold_from_train"],
        "heldout_score": report["selected_heldout_score"],
    }, indent=2))


if __name__ == "__main__":
    main()
