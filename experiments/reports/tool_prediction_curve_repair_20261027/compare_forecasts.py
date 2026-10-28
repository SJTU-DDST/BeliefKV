"""Compare historical empirical forecast interpretation without future tool data."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.action_frontier import ActionTimingCurve, EmpiricalActionTimingCurve
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve the original interpretation comparison")
    arm = ROOT / "experiments/raw/locobench_tool_offload_before_admission_20261027_r1/full"
    reader = SealedRecords()
    counts, sources, episodes, newly_allowed_episodes = Counter(), Counter(), set(), set()
    p10_changes = []
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") != "tool_wait_forecast" or row.get("residual_support_detail") not in (
            "online_offline_prior_command", "online_hybrid_workflow_command", "online_hybrid_run_command",
        ):
            continue
        points = row.get("release_cdf")
        if not points:
            continue
        values = tuple(item[0] for item in points)
        probabilities = tuple(item[1] for item in points)
        old = ActionTimingCurve(values, probabilities, "pooled", 0.)
        corrected = EmpiricalActionTimingCurve(values, probabilities, "pooled", 0.)
        old_probability = old.release_within(1000.)
        corrected_probability = corrected.release_within(1000.)
        identity = (
            row["workflow_id"], row["invocation_id"], row["context_id"],
            row["context_epoch"], row["invocation_revision_ts_ms"],
        )
        episodes.add(identity)
        counts["forecast_records"] += 1
        sources[row["residual_support_detail"]] += 1
        counts["old_one_second_survival_gate_passes"] += old_probability <= .1
        counts["corrected_one_second_survival_gate_passes"] += corrected_probability <= .1
        if old_probability > .1 and corrected_probability <= .1:
            counts["newly_passing_forecast_records"] += 1
            newly_allowed_episodes.add(identity)
        before, after = old.quantile(.1), corrected.quantile(.1)
        if before is not None and after is not None:
            p10_changes.append(after - before)
            counts["p10_changed_records"] += abs(after - before) > 1e-6
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_arm": str(arm), "counts": dict(counts), "forecast_sources": dict(sources),
        "distinct_observed_wait_revisions": len(episodes),
        "wait_revisions_with_a_newly_passing_forecast": len(newly_allowed_episodes),
        "p10_increase_ms": {
            "count": len(p10_changes), "mean": statistics.mean(p10_changes) if p10_changes else None,
            "median": statistics.median(p10_changes) if p10_changes else None,
            "max": max(p10_changes) if p10_changes else None,
        },
        "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "interpretation_sources_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "beliefkv/runtime/sglang_v0520_prediction.py",
                "beliefkv/predictor/action_frontier.py",
            )
        },
        "performance_accepted": False,
        "limitations": [
            "This evaluates the one-second gate at forecast issuance, not every physical dispatch.",
            "Forecast refreshes are correlated; wait revisions need not be independent tool calls.",
            "No reconstructed capacity, safe donor, Host availability or consumer benefit is assumed.",
            "Passing this probability gate alone does not authorize offload or establish performance.",
            "No later tool completion time enters either compared forecast interpretation.",
        ],
    }
    with output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({name: report[name] for name in (
        "counts", "distinct_observed_wait_revisions",
        "wait_revisions_with_a_newly_passing_forecast", "p10_increase_ms",
    )}, indent=2))


if __name__ == "__main__":
    main()
