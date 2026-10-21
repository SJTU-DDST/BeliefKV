"""The next controls retain matching work configuration and the one-time GPU hold."""

from datetime import datetime, timedelta
import json
from pathlib import Path

from beliefkv.experiments.internal_comparison import arm_environment
from scripts import launch_qualified_after_hold as launcher


PLAN = Path(__file__).resolve().parents[1] / "configs/migration/next_predictive_baselines_20261022.json"


def test_next_plan_uses_fresh_batch8_controls_instead_of_reusing_batch12_full():
    plan = json.loads(PLAN.read_text())
    common = plan["common_environment"]
    assert plan["order"] == ["full", "join_priority_only", "native_matched"]
    assert not plan["performance_accepted"]
    assert common["MAX_RUNNING_REQUESTS"] == "8"
    assert common["SERVER_RANDOM_SEED"] == common["SAMPLING_SEED"] == "21"
    assert common["ROOT_COUNT"] == "36"
    assert common["MODEL_CONTEXT_TOKENS"] == common["CONTEXT_WINDOW_TOKENS"] == "262144"
    assert common["CONTEXT_COMPRESSION_TRIGGER_RATIO"] == "0.95"
    assert common["HOST_SPLIT"] == "80:20"
    assert common["PREFILL_SCHEDULING_POLICY"] == "join_priority"
    assert common["PREFETCH_RESIDENCY_PROTECTION"] == "0"
    for mode in plan["order"]:
        arm = arm_environment(common, mode)
        for name in (
            "MAX_RUNNING_REQUESTS", "SERVER_RANDOM_SEED", "SAMPLING_SEED",
            "ROOT_COUNT", "MODEL_CONTEXT_TOKENS", "CONTEXT_WINDOW_TOKENS",
            "HOST_SPLIT", "HICACHE_SIZE_GB", "FANOUT_PROFILE", "WORKLOAD_MANIFEST",
        ):
            assert arm[name] == common[name]
        assert arm["NATIVE_CHAT_TOKEN_BLOCK_CACHE"] == arm["PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE"] == "1"
        assert arm["EXECUTION_HANDOFF"] == "0"


def test_october22_deadline_cannot_open_at_the_old_october21_deadline(tmp_path, monkeypatch):
    plan = json.loads(PLAN.read_text())
    deadline = datetime.fromisoformat(plan["gpu_not_before"])
    assert deadline.isoformat() == "2026-10-22T06:00:00+08:00"
    marker = tmp_path / "pause"
    marker.write_text("The user reserves the GPU until October 22 06:00 Beijing.")
    monkeypatch.setattr(launcher, "PAUSE", marker)
    stamp = marker.stat()
    identity = [stamp.st_dev, stamp.st_ino, stamp.st_uid]
    hold = {"gpu_not_before": plan["gpu_not_before"], "automatic_resume_allowed": True}
    digest = launcher.sha256_file(marker)
    for offset in (-86400, -1, 0, 1):
        assert launcher.require_hold(
            hold, digest, deadline + timedelta(seconds=offset), identity,
        ) is (offset >= 0)
    assert marker.exists()


def test_protection_and_ordering_controls_change_only_the_selected_common_setting():
    plan = json.loads(PLAN.read_text())
    common = plan["common_environment"]
    baseline = arm_environment(common, "full")
    for name, field in (
        ("full_post_ack_protection", "PREFETCH_RESIDENCY_PROTECTION"),
        ("full_workflow_ordering", "PREFILL_SCHEDULING_POLICY"),
    ):
        control = plan["conditional_controls"][name]
        environment = arm_environment({**common, **control["overrides"]}, control["mode"])
        changed = {key for key in baseline if baseline[key] != environment[key]}
        expected = {field, "BELIEFKV_" + field}
        if field == "PREFILL_SCHEDULING_POLICY":
            expected |= {"BELIEFKV_ENABLE_RESIDENT_FIRST", "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY"}
        assert changed == expected
