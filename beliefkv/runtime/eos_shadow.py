"""De-identified, optional EOS probability cues from streamed token logprobs."""

from __future__ import annotations

import math
from typing import Any, Mapping

EOS_TOKENS = frozenset({"<|im_end|>", "<|endoftext|>"})
EOS_PROB_THRESHOLDS = (0.01, 0.05, 0.1, 0.25, 0.5)


def eos_top_logprob(logprobs: Any) -> tuple[float | None, int]:
    """Return the best unsampled EOS logprob and number of scored tokens."""
    if not isinstance(logprobs, Mapping):
        return None, 0
    content = logprobs.get("content")
    if not isinstance(content, list):
        return None, 0
    best = None
    scored = 0
    for item in content:
        if not isinstance(item, Mapping) or not isinstance(item.get("token"), str):
            continue
        scored += 1
        if item["token"] in EOS_TOKENS:
            continue
        top = item.get("top_logprobs")
        if not isinstance(top, list):
            continue
        for candidate in top:
            if (
                not isinstance(candidate, Mapping)
                or candidate.get("token") not in EOS_TOKENS
                or type(candidate.get("logprob")) not in {int, float}
            ):
                continue
            value = float(candidate["logprob"])
            if math.isfinite(value) and (best is None or value > best):
                best = value
    return best, scored
