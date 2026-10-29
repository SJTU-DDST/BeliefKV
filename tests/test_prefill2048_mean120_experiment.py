from copy import deepcopy
from pathlib import Path

import pytest

from experiments.reports.prefill2048_mean120_20261029 import prepare, queue_pair
from scripts import run_causal_comparison as causal


def specifications():
    bases = {label: causal.load(path) for label, path in prepare.BASES.items()}
    candidates = deepcopy(bases)
    for item in candidates.values():
        item["common_environment"].update(CHUNKED_PREFILL_SIZE="2048", ENABLE_MIXED_CHUNK="0")
    return candidates, bases


def test_profile_changes_only_prefill_and_retains_original_full_dispatch_policy():
    candidates, bases = specifications()
    prepare.validate_profiles(candidates, bases)
    for label in prepare.ORDER:
        old = causal.prefill_server_parameters({}, bases[label]["common_environment"])
        new = causal.prefill_server_parameters({}, candidates[label]["common_environment"])
        assert old == {"chunked_prefill_size": 4096, "enable_mixed_chunk": False}
        assert new == {"chunked_prefill_size": 2048, "enable_mixed_chunk": False}
    assert candidates["full"]["workflow_arrivals"] == candidates["thunderagent_acting_decay"]["workflow_arrivals"]


@pytest.mark.parametrize("change", (
    "mixed", "chunk", "dispatch", "tool_prefetch", "frontier", "capacity", "arrivals", "timing", "arm",
))
def test_profile_cannot_silently_change_policy_or_comparison_work(change):
    candidates, bases = specifications()
    common = candidates["full"]["common_environment"]
    if change in ("mixed", "chunk", "dispatch", "tool_prefetch", "frontier", "capacity"):
        field, value = {
            "mixed": ("ENABLE_MIXED_CHUNK", "1"),
            "chunk": ("CHUNKED_PREFILL_SIZE", "1024"),
            "dispatch": ("AGENT_FREE_DISPATCH_INTERVAL_MS", "500"),
            "tool_prefetch": ("TOOL_PREFETCH", "1"),
            "frontier": ("EXECUTION_FRONTIER_RESTORE", "1"),
            "capacity": ("HICACHE_SIZE_GB", "100"),
        }[change]
        common[field] = value
    elif change == "arrivals":
        candidates["full"]["workflow_arrivals"]["sha256"] = "0" * 64
    elif change == "timing":
        candidates["full"]["tool_timing_overlay"] = {"path": "different"}
    else:
        candidates["full"]["order"] = ["native_matched"]
    with pytest.raises(ValueError):
        prepare.validate_profiles(candidates, bases)


@pytest.mark.parametrize("change", (None, "patch_bytes", "actual_tree", "commit", "dirty"))
def test_native_rebinding_accepts_path_only_after_byte_and_metadata_equivalence(change):
    qualified = {
        "commit": "a", "dirty": True, "state_sha256": "old",
        "canonical_patch": {"path": "/old/patch", "sha256": "bytes", "actual_tree": "tree"},
    }
    current = deepcopy(qualified)
    current["state_sha256"] = "new"
    current["canonical_patch"]["path"] = "/frozen/patch"
    if change == "patch_bytes":
        current["canonical_patch"]["sha256"] = "different"
    elif change == "actual_tree":
        current["canonical_patch"]["actual_tree"] = "different"
    elif change == "commit":
        current["commit"] = "different"
    elif change == "dirty":
        current["dirty"] = False
    if change is None:
        queue_pair.validate_native_rebinding(current, qualified)
    else:
        with pytest.raises(ValueError, match="identical native"):
            queue_pair.validate_native_rebinding(current, qualified)


@pytest.mark.parametrize("stage", (
    "position_running", "position_failed", "position_exited_without_comparison",
    "formal_running", "formal_failed", "formal_live_tail", "formal_complete",
))
def test_next_method_waits_for_actual_complete_formal_work_and_owned_exit(tmp_path, monkeypatch, stage):
    position = tmp_path / "position.registration.json"
    comparison = tmp_path / "formal.registration.json"
    position_state = position.with_suffix(".state.json")
    comparison_state = comparison.with_suffix(".state.json")
    position_state.touch()
    entry = {"position_registration": str(position), "comparison_registration": str(comparison)}
    states = {
        position_state: {
            "status": "starting_position_gpu_check" if stage == "position_running" else
            "stopped_for_investigation" if stage == "position_failed" else
            "calibrated_comparison_registered",
            "watcher": {"pid": "position"},
        },
        comparison_state: {
            "status": "collected_performance_review_pending" if stage in ("formal_live_tail", "formal_complete")
            else "running_full",
            "watcher": {"pid": "formal"},
        },
    }
    if stage.startswith("formal"):
        comparison_state.touch()
    monkeypatch.setattr(causal, "load", lambda path: states[Path(path)])
    live = {"position"} if stage == "position_running" else {"formal"} if stage == "formal_running" else set()
    monkeypatch.setattr(causal.reservation, "alive", lambda identity: identity and identity["pid"] in live)
    checked = []

    def complete_work(path):
        checked.append(path)
        return {"all_requests": "exact"} if stage == "formal_complete" else None

    monkeypatch.setattr(queue_pair, "complete_work", complete_work)
    if stage in ("position_failed", "position_exited_without_comparison", "formal_failed"):
        with pytest.raises(ValueError):
            queue_pair.method_complete(entry)
    else:
        result = queue_pair.method_complete(entry)
        assert result == ({"all_requests": "exact"} if stage == "formal_complete" else None)
    assert bool(checked) == (stage in ("formal_live_tail", "formal_complete"))
