"""Explicit frontend-cache settings for each live experiment arm."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


CHAT_TOKEN_CACHE_ENV_BY_ARM = {
    "native": "NATIVE_CHAT_TOKEN_BLOCK_CACHE",
    "reactive": "REACTIVE_CHAT_TOKEN_BLOCK_CACHE",
    "predictive_h2d": "PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE",
}


def chat_token_cache_configuration(
    arms: Sequence[str], environment: Mapping[str, str],
) -> dict[str, bool]:
    result = {}
    for arm in arms:
        default = "0" if arm == "native" else environment.get(
            "BELIEFKV_CHAT_TOKEN_BLOCK_CACHE", "0",
        )
        value = environment.get(CHAT_TOKEN_CACHE_ENV_BY_ARM[arm], default)
        if value not in ("0", "1"):
            raise ValueError(f"{arm}: chat token block cache must be 0 or 1")
        result[arm] = value == "1"
    return result


def frozen_chat_token_cache_configuration(plan: Mapping) -> dict[str, bool]:
    values = plan.get("chat_token_block_cache_by_arm")
    if values is None:
        values = {
            arm: plan.get("chat_token_block_cache", False) for arm in plan["order"]
        }
    if (
        not isinstance(values, Mapping)
        or set(values) != set(plan["order"])
        or any(type(value) is not bool for value in values.values())
    ):
        raise ValueError("invalid frozen chat token block cache configuration")
    return dict(values)
