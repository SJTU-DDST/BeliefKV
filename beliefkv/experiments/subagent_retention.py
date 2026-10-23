"""Common logical workload and explicit physical-retention comparison arms."""

from __future__ import annotations

from beliefkv.experiments.delegation_policy import REUSABLE_NESTED_IN_GRAPH_PROFILE
from beliefkv.experiments.internal_comparison import arm_environment


ORDER = ("all", "drop", "lru", "selective")


def environment(common: dict[str, str], policy: str) -> dict[str, str]:
    if policy not in ORDER:
        raise ValueError("unknown subagent retention comparison arm")
    required = {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21",
        "PYTHONHASHSEED": "21",
        "MODEL_CONTEXT_TOKENS": "262144",
        "CONTEXT_WINDOW_TOKENS": "262144",
        "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.95",
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "FANOUT_PROFILE": REUSABLE_NESTED_IN_GRAPH_PROFILE,
        "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0",
        "PREFILL_SCHEDULING_POLICY": "workflow",
        "TOOL_RESTORE_MODE": "speculative", "RESTORE_TIERS": "complete",
        "JOIN_PREFETCH_TRIGGER": "observed_eos", "TOOL_CONTINUITY": "0",
    }
    if any(common.get(name) != value for name, value in required.items()):
        raise ValueError("reusable workload differs from the prepared comparison")
    result = arm_environment(common, "full", tool_prefetch=common.get("TOOL_PREFETCH") != "0")
    result.update(
        BELIEFKV_SUBAGENT_RETENTION=policy,
        BELIEFKV_INTERNAL_COMPARISON_MODE="full",
    )
    return result
