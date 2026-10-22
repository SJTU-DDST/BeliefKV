"""Explicit repair and historical-policy fallback for the Full regression."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import mean

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.metrics.workload_compatibility import compare_workload


def environment(reference: dict, checkout: Path, port: int, *, fallback: bool) -> dict:
    common = dict(reference["environment"])
    required = {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21",
        "FANOUT_PROFILE": "native_in_graph_nested_2to4",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
        "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20",
    }
    if any(common.get(name) != value for name, value in required.items()):
        raise ValueError("recovery reference differs from the qualified workload")
    manifest = Path(common["WORKLOAD_MANIFEST"]).relative_to(reference["execution_checkout"])
    common.update(
        PYTHONPATH=str(checkout), WORKLOAD_MANIFEST=str(checkout / manifest),
        PORT=str(port), RUN_ROOT="", RESUME_PENDING="0", CUDA_VISIBLE_DEVICES="0",
        TOOL_CONTINUITY="0", BELIEFKV_ENABLE_TOOL_CONTINUITY="0",
        EXECUTION_FRONTIER_RESTORE="0", BELIEFKV_ENABLE_FRONTIER_RESTORE="0",
        BELIEFKV_SUBAGENT_RETENTION="off",
    )
    if fallback:
        for public, internal, value in (
            ("PREFILL_SCHEDULING_POLICY", "BELIEFKV_PREFILL_SCHEDULING_POLICY", "workflow"),
            ("TOOL_RESTORE_MODE", "BELIEFKV_TOOL_RESTORE_MODE", "speculative"),
            ("IDLE_PREPARE_POLICY", "BELIEFKV_IDLE_PREPARE_POLICY", "all"),
            ("RESTORE_TIERS", "BELIEFKV_RESTORE_TIERS", "complete"),
            ("PREFETCH_RESIDENCY_PROTECTION", "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION", "1"),
        ):
            common[public] = common[internal] = value
    return arm_environment(common, "full")


def handover_ready(plan: dict, state: dict, *, driver_live: bool) -> bool:
    expected = plan["predecessor_watcher"]
    observed = state.get("watcher", {})
    if any(observed.get(name) != expected.get(name) for name in ("pid", "start_ticks")):
        raise ValueError("predecessor supervisor identity changed")
    if state.get("error") or state.get("status", "").startswith(("stopped_", "cancelled_")):
        raise ValueError("predecessor failed; preserve its failure for investigation")
    return (
        set(plan["preserve_arms"]) <= set(state.get("completed_arms", ()))
        and state.get("status") in (
            "waiting_for_idle_resources", "collected_performance_review_pending",
        )
        and not driver_live
    )


def performance_gate(comparison: dict, busy: dict, summaries: dict, candidate: str) -> dict:
    """A development decision to try fallback, never a causal/quality certificate."""
    runs = comparison["runs"]
    controls = ("native_matched", "join_priority_only")
    populations = [
        {row["instance_id"] for row in summary["workflows"]}
        for summary in summaries.values()
    ]
    if any(population != populations[0] for population in populations[1:]):
        raise ValueError("recovery and native task populations differ")
    if any(
        len(summary["workflows"]) != 36
        or len({row["instance_id"] for row in summary["workflows"]}) != 36
        or any(row.get("outcome") != "completed" or row.get("measurement_valid") is not True
               or row.get("system_jct_eligible") is not True for row in summary["workflows"])
        for summary in summaries.values()
    ):
        raise ValueError("performance gate requires all 36 system-valid workflows")
    common = set.intersection(*(
        {row["instance_id"] for row in summary["workflows"]
         if row.get("native_agent_jct_eligible") is True}
        for summary in summaries.values()
    ))
    paired = {
        name: mean(row["duration_seconds"] for row in summary["workflows"]
                   if row["instance_id"] in common) if common else None
        for name, summary in summaries.items()
    }
    for run in runs.values():
        for field in ("jct_mean_seconds", "output_tokens_per_second"):
            if not isinstance(run.get(field), (int, float)) or not math.isfinite(run[field]):
                raise ValueError("performance metrics are missing or nonfinite")
    bands = {name: row["windows"][0] for name, row in busy["runs"].items()}
    if any(
        band.get("service_window_observed_complete") is not True
        or band.get("start_seconds") != 600. or band.get("end_seconds") != 1800.
        or not isinstance(band.get("decode_output_tokens_per_second"), (int, float))
        or not math.isfinite(band["decode_output_tokens_per_second"])
        for band in bands.values()
    ):
        raise ValueError("busy throughput window is incomplete or changed")
    conditions = {
        "realized_work_close_to_controls": all(
            compare_workload(runs[candidate], runs[name])["necessary_workload_condition_passed"]
            for name in controls
        ),
        "raw_mean_jct_no_worse_than_native_and_join": all(
            runs[candidate]["jct_mean_seconds"] <= runs[name]["jct_mean_seconds"]
            for name in controls
        ),
        "common_eligible_mean_jct_no_worse": len(common) >= 35 and all(
            paired[candidate] <= paired[name] for name in controls
        ),
        "output_throughput_no_worse_than_native": (
            runs[candidate]["output_tokens_per_second"]
            >= runs["native_matched"]["output_tokens_per_second"]
        ),
        "busy_decode_no_worse_than_native": (
            bands[candidate]["decode_output_tokens_per_second"]
            >= bands["native_matched"]["decode_output_tokens_per_second"]
        ),
        "natural_eligibility_not_reduced": (
            summaries[candidate]["native_agent_jct_eligible_workflows"] >= 35
        ),
    }
    return {
        "candidate": candidate, "conditions": conditions,
        "development_gate_passed": all(conditions.values()),
        "fallback_required": not all(conditions.values()),
        "common_eligible_workflows": len(common), "common_eligible_mean_jct": paired,
        "metrics": {
            name: {key: row[key] for key in (
                "jct_mean_seconds", "output_tokens", "input_tokens", "llm_requests",
                "tool_calls", "output_tokens_per_second",
            )} for name, row in runs.items()
        },
        "busy_window": bands, "performance_accepted": False,
        "scope": (
            "Development guardrail with complete raw JCT and common natural eligibility. "
            "Dynamic work and revisions differ; correctness is not independently graded. "
            "No ordinary tail trimming or token-normalized substitution for JCT."
        ),
    }
