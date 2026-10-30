from copy import deepcopy

import pytest

from scripts.register_sustained_oracle import matching_conditions


def configurations():
    original = {
        "order": ["full"], "source_registration": "source",
        "stream_response_metrics": True,
        "workflow_arrivals": {"sha256": "fixed"}, "tool_timing_overlay": {"sha256": "fixed"},
        "common_environment": {
            "WORKFLOW_SIZE_RESUME": "0", "BELIEFKV_WORKFLOW_SIZE_RESUME": "0",
            "WORKFLOW_WORK_SOURCE": "learned", "BELIEFKV_WORKFLOW_WORK_SOURCE": "learned",
            "WORKFLOW_WORK_MODEL": "", "BELIEFKV_WORKFLOW_WORK_MODEL": "",
            "WORKFLOW_WORK_MODEL_SHA256": "", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "",
            "AGENT_CONTROL_INTERVAL_MS": "2000", "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
            "MAX_RUNNING_REQUESTS": "24", "CHUNKED_PREFILL_SIZE": "2048", "TOOL_PREFETCH": "0",
        },
    }
    candidate = deepcopy(original)
    for name, value in (
        ("WORKFLOW_SIZE_RESUME", "1"), ("WORKFLOW_WORK_SOURCE", "oracle"),
        ("WORKFLOW_WORK_MODEL", "oracle.json"), ("WORKFLOW_WORK_MODEL_SHA256", "bound"),
        ("WORKFLOW_COMBINATION_ADMISSION", "0"),
    ):
        candidate["common_environment"][name] = value
        candidate["common_environment"]["BELIEFKV_" + name] = value
    return original, candidate


def test_oracle_entry_preserves_all_nonselection_conditions_and_aliases():
    matching_conditions(*configurations())


@pytest.mark.parametrize("change", (
    "arrivals", "tools", "source", "chunk", "batch", "prefetch", "cadence",
    "alias", "extra", "missing", "not_oracle", "invalid_selection_flag", "stream",
))
def test_preparation_cannot_hide_an_unmatched_oracle_comparison(change):
    original, candidate = configurations()
    common = candidate["common_environment"]
    if change in ("arrivals", "tools", "source"):
        name = {"arrivals": "workflow_arrivals", "tools": "tool_timing_overlay", "source": "source_registration"}[change]
        candidate[name] = "different"
    elif change == "alias":
        common["BELIEFKV_WORKFLOW_WORK_MODEL"] = "different"
    elif change == "extra":
        common["UNDECLARED_SERVING_CHANGE"] = "1"
    elif change == "missing":
        common.pop("CHUNKED_PREFILL_SIZE")
    elif change == "not_oracle":
        common["WORKFLOW_WORK_SOURCE"] = "learned"
    elif change == "stream":
        candidate["stream_response_metrics"] = False
    elif change == "invalid_selection_flag":
        common["WORKFLOW_COMBINATION_ADMISSION"] = "unknown"
        common["BELIEFKV_WORKFLOW_COMBINATION_ADMISSION"] = "unknown"
    else:
        name = {
            "chunk": "CHUNKED_PREFILL_SIZE", "batch": "MAX_RUNNING_REQUESTS",
            "prefetch": "TOOL_PREFETCH", "cadence": "AGENT_FREE_DISPATCH_INTERVAL_MS",
        }[change]
        common[name] = "different"
    with pytest.raises(ValueError):
        matching_conditions(original, candidate)
