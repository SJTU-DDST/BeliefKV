"""Read-only class-prior and complete Host-pressure evidence, with explicit scope."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.tool_timing_prior import ToolTimingPrior
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve prior audit evidence")
    registration = Path("/home/longhao/experiment/ops/tool_prediction_curve_repair_20261027_r1.registration.json")
    plan = causal.load(registration)
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("audit requires complete owned exit")
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    if not states:
        raise ValueError("no complete native runtime states")
    state = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    pools = [
        row for row in reader(arm / "server/host_pool_telemetry.jsonl")
        if row.get("event") == "host_pool_usage"
    ]
    span = covered = at_99 = below_reserve = 0.
    gaps = []
    reserve = state["idle_full_prepare"]["native"]["host_reserved_tokens"]
    for previous, current in zip(pools, pools[1:]):
        dt = current["ts_ms"] - previous["ts_ms"]
        if dt < 0:
            raise ValueError("Host sample timestamps went backward")
        span += dt
        if dt > 5000.:
            gaps.append(dt)
            continue
        covered += dt
        full = previous["pools"]["full"]
        at_99 += dt * (full["used_fraction"] >= .99)
        below_reserve += dt * (full["capacity_units"] - full["used_units"] < reserve)
    prior_path = Path(plan["reference_environment"]["BELIEFKV_TOOL_TIMING_PRIOR"])
    prior_sha = plan["reference_environment"]["BELIEFKV_TOOL_TIMING_PRIOR_SHA256"]
    prior, source = ToolTimingPrior.load(prior_path, prior_sha), causal.load(prior_path)
    quantiles = {
        category: [
            {
                "elapsed_s": age / 1000.,
                "remaining_p10_s": curve.remaining_quantile(age, .1) / 1000.,
                "remaining_p50_s": curve.remaining_quantile(age, .5) / 1000.,
                "remaining_p90_s": curve.remaining_quantile(age, .9) / 1000.,
            } for age in (0., 5000., 10000.)
        ] for (_domain, category), curve in prior.curves.items()
    }
    idle = state["idle_full_prepare"]["native"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "scope": "complete run counters; sampled Host pressure; class-prior quantiles",
        "idle_prepare": {
            "native_checks": idle["checks"],
            "host_budget_pre_scan_skips": idle["host_budget_pre_scan_skips"],
            "host_budget_pre_scan_skip_fraction": idle["host_budget_pre_scan_skips"] / idle["checks"],
            "reserved_tokens": reserve,
            "reserved_bytes": reserve * 98304,
            "not_unique_failed_tools_or_lost_JCT": True,
        },
        "host_usage": {
            "samples": len(pools), "sample_span_ms": span, "covered_sample_hold_ms": covered,
            "uncovered_gaps_over_5s": gaps,
            "covered_sample_hold_fraction_at_or_above_99pct": at_99 / covered,
            "covered_sample_hold_fraction_below_backup_reserve": below_reserve / covered,
            "minimum_observed_free_tokens": min(
                row["pools"]["full"]["capacity_units"] - row["pools"]["full"]["used_units"]
                for row in pools
            ),
            "scope": "Sample-hold approximation; not continuous occupancy or causal policy loss.",
        },
        "prior_sha256": prior_sha, "prior_quantiles": quantiles,
        "held_out_evaluation": source["held_out_evaluation"],
        "current_network_signature": ["timing_domain", "observed_timing_class"],
        "original_execute_semantics_predict_private_within_class_sample": False,
        "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "idle_prepare", "host_usage", "prior_quantiles", "held_out_evaluation",
    )}, indent=2))


if __name__ == "__main__":
    main()
