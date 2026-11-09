from pathlib import Path

import pytest

from scripts import run_arrival_work_ranking_comparison as runner


def setup(monkeypatch):
    preparation, cpu = Path("preparation.json"), Path("cpu.json")
    plan = {
        "inputs_sha256": {}, "mean_interval_seconds": 60.,
        "workflows": 83, "initial_workflows": 20,
        "static_completion_coverage_screen": {"full": {"candidate_schedule_covers_static_tail": True}},
    }
    evidence = {
        "returncode": 0, "failed_error_skipped_cases": 0,
        "all_retained_literal_identities_present": True,
        "sources_unchanged_during_execution": True,
    }
    monkeypatch.setattr(runner.causal, "load", lambda path: {
        preparation: plan, cpu: evidence,
    }[path])
    monkeypatch.setattr(runner.causal.fresh, "_require_clean", lambda path: None)
    monkeypatch.setattr(runner.finish, "verify_pins", lambda pins: None)
    monkeypatch.setattr(
        runner.causal.qualified, "resources", lambda *_: pytest.fail("GPU checked before preparation gates"),
    )
    monkeypatch.setattr(runner, "released", lambda: pytest.fail("GPU launch reached before CPU gates"))
    return preparation, cpu, plan, evidence


def test_registration_name_cannot_silently_describe_another_arrival_rate(tmp_path, monkeypatch):
    preparation, cpu, plan, _ = setup(monkeypatch)
    plan["mean_interval_seconds"] = 50.
    with pytest.raises(ValueError, match="identity requires"):
        runner.start(preparation, cpu, tmp_path / "execution")
    assert not (tmp_path / "execution").exists()


def test_shortened_background_must_cover_the_static_natural_tails(tmp_path, monkeypatch):
    preparation, cpu, plan, _ = setup(monkeypatch)
    plan["static_completion_coverage_screen"]["full"]["candidate_schedule_covers_static_tail"] = False
    with pytest.raises(ValueError, match="static measured tails"):
        runner.start(preparation, cpu, tmp_path / "execution")
    assert not (tmp_path / "execution").exists()


def test_failed_or_stale_cpu_evidence_does_not_reach_gpu_launch(tmp_path, monkeypatch):
    preparation, cpu, _, evidence = setup(monkeypatch)
    evidence["sources_unchanged_during_execution"] = False
    with pytest.raises(ValueError, match="source-bound CPU"):
        runner.start(preparation, cpu, tmp_path / "execution")
    assert not (tmp_path / "execution").exists()
