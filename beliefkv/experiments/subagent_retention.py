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
        "MODEL_CONTEXT_TOKENS": "262144",
        "FANOUT_PROFILE": REUSABLE_NESTED_IN_GRAPH_PROFILE,
        "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0",
    }
    if any(common.get(name) != value for name, value in required.items()):
        raise ValueError("reusable workload differs from the prepared comparison")
    result = arm_environment(common, "full")
    result.update(
        BELIEFKV_SUBAGENT_RETENTION=policy,
        BELIEFKV_INTERNAL_COMPARISON_MODE="full",
    )
    return result
