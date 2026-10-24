"""Explicit generation settings shared by live capture and replay calibration."""

SAMPLING_PROFILES = ("legacy_greedy", "qwen35_coding", "qwen3_coder")


def sampling_parameters(profile: str) -> dict:
    if profile == "legacy_greedy":
        return {"temperature": 0.0}
    if profile not in SAMPLING_PROFILES:
        raise ValueError("unknown sampling profile")
    coder = profile == "qwen3_coder"
    return {
        "temperature": 0.7 if coder else 0.6,
        "top_p": 0.8 if coder else 0.95,
        "presence_penalty": 0.0,
        "extra_body": {
            "top_k": 20, "min_p": 0.0, "repetition_penalty": 1.05 if coder else 1.0,
        },
    }
