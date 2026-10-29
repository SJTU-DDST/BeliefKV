from copy import deepcopy
from pathlib import Path

import pytest

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from experiments.reports.arrival_mean120_20261029 import queue_pair
from experiments.reports.arrival_mean120_20261029.review_pair import validate_methods
from beliefkv.experiments.native_engine_variant import COMMON_TREE, FREE_EXTENT_TREE
from scripts import run_causal_comparison as causal


def test_same_qualified_revision_can_freeze_two_independent_registrations(tmp_path, monkeypatch):
    monkeypatch.setattr(causal.comparison, "EXPERIMENTS", tmp_path)
    first = tmp_path / "thunder.registration.json"
    second = tmp_path / "full.registration.json"
    a = causal.frozen_execution_paths("a" * 40, "b" * 40, first)
    b = causal.frozen_execution_paths("a" * 40, "b" * 40, second)
    assert len(set((*a, *b))) == 4
    assert causal.frozen_execution_paths("a" * 40, "b" * 40, first.parent / "." / first.name) == a
    assert all(path.parent == tmp_path / ".worktrees" for path in (*a, *b))


def test_requested_arrivals_preserve_workflow_order_and_only_double_release_offsets():
    specifications = {
        label: causal.load(queue_pair.MAIN / "experiments/reports/arrival_mean120_20261029" / (label + ".json"))
        for label in queue_pair.ORDER
    }
    old = causal.load(queue_pair.MAIN / "experiments/reports/warm_start_thunder_20261029/arrivals.json")
    identities = [row["workflow_id"] for row in old["arrival_schedule"]]
    actual = queue_pair.validate_pair(specifications, identities)
    assert actual == poisson_arrival_plan(
        identities, rate_per_second=1 / 120, seed=21, initial_workflows=10,
    )
    assert [row["offset_seconds"] for row in actual["arrival_schedule"]] == pytest.approx(
        [row["offset_seconds"] * 2 for row in old["arrival_schedule"]],
    )
    assert actual["arrival_schedule"][-1]["offset_seconds"] == pytest.approx(863.4309955)


@pytest.mark.parametrize("change", ("arrival_digest", "different_arrivals", "policy", "arm", "population"))
def test_mean120_pair_rejects_changed_work_or_policy(change):
    specifications = {
        label: deepcopy(causal.load(
            queue_pair.MAIN / "experiments/reports/arrival_mean120_20261029" / (label + ".json"),
        ))
        for label in queue_pair.ORDER
    }
    arrivals = causal.load(Path(specifications["full"]["workflow_arrivals"]["path"]))
    identities = [row["workflow_id"] for row in arrivals["arrival_schedule"]]
    if change == "arrival_digest":
        for item in specifications.values():
            item["workflow_arrivals"]["sha256"] = "0" * 64
    elif change == "different_arrivals":
        specifications["full"]["workflow_arrivals"]["sha256"] = "0" * 64
    elif change == "policy":
        specifications["full"]["common_environment"]["TOOL_PREFETCH"] = "1"
    elif change == "arm":
        specifications["full"]["order"] = ["native_matched"]
    else:
        identities = identities[:-1]
    with pytest.raises(ValueError):
        queue_pair.validate_pair(specifications, identities)


def test_pending_predecessor_cannot_release_gpu_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(causal, "load", lambda _: {})
    monkeypatch.setattr(causal, "predecessor_ready", lambda _: False)

    def forbidden(_):
        raise AssertionError("must not validate work or allocate before actual predecessor exit")

    monkeypatch.setattr(causal, "predecessor_work", forbidden)
    assert queue_pair.complete_work(tmp_path / "previous.json") is None


@pytest.mark.parametrize("change", (None, "tool_timing", "work", "capacity"))
def test_reference_contract_ignores_only_overlay_path_not_its_contents(change):
    plans = {label: deepcopy(causal.load(path)) for label, path in queue_pair.PREVIOUS.items()}
    if change == "tool_timing":
        plans["full"]["tool_timing_overlay"]["sha256"] = "0" * 64
    elif change == "work":
        plans["full"]["token_source_sha256"] = "0" * 64
    elif change == "capacity":
        plans["full"]["source_capacity"] = {}
    if change is None:
        queue_pair.validate_references(plans)
    else:
        with pytest.raises(ValueError):
            queue_pair.validate_references(plans)


@pytest.mark.parametrize("change", (None, "engine", "arrival", "extra_field"))
def test_review_allows_only_the_declared_method_specific_native_difference(change):
    rows = {
        "ThunderAgent decay": {"match_contract": {"engine_tree": COMMON_TREE, "workflow_arrivals": {"seed": 21}}},
        "FULL": {"match_contract": {"engine_tree": FREE_EXTENT_TREE, "workflow_arrivals": {"seed": 21}}},
    }
    if change == "engine":
        rows["FULL"]["match_contract"]["engine_tree"] = "unqualified"
    elif change == "arrival":
        rows["FULL"]["match_contract"]["workflow_arrivals"]["seed"] = 22
    elif change == "extra_field":
        rows["FULL"]["match_contract"]["changed"] = True
    if change is None:
        validate_methods(rows)
    else:
        with pytest.raises(ValueError):
            validate_methods(rows)


@pytest.mark.parametrize("change", (None, "surviving_child", "cleanup_incomplete", "stale_idle", "partial_work"))
def test_terminal_state_also_requires_exact_work_fresh_drain_and_owned_exit(monkeypatch, tmp_path, change):
    work = {str(i): (0, 0) for i in range(3674)}
    work["0"] = (145565966, 549671)
    plan = {"order": ["full"], "run_root": str(tmp_path)}
    cleanup = {"remaining_processes": [], "finished_at": "now", "identities": [{"pid": 1234}]}
    drain = {"previous_zero_snapshot_ts_ms": 1, "drained_status": {"snapshot_ts_ms": 2}}
    if change == "cleanup_incomplete":
        cleanup["remaining_processes"] = [{"pid": 1234}]
    elif change == "stale_idle":
        drain["drained_status"]["snapshot_ts_ms"] = 1
    elif change == "partial_work":
        work.pop("1")

    def load(path):
        return {"owned_process_cleanup.json": cleanup, "telemetry_drain.json": drain}.get(path.name, plan)

    monkeypatch.setattr(causal, "load", load)
    monkeypatch.setattr(causal, "predecessor_ready", lambda _: True)
    monkeypatch.setattr(causal, "predecessor_work", lambda _: work)
    monkeypatch.setattr(causal.reservation, "alive", lambda _: change == "surviving_child")
    if change is None:
        assert queue_pair.complete_work(tmp_path / "previous.json") == work
    else:
        with pytest.raises(ValueError):
            queue_pair.complete_work(tmp_path / "previous.json")
