from copy import deepcopy
import json
import pytest

from scripts.prepare_composite_comparison import BASE, ROOT, configuration, measurement_protocol


def test_sustained_config_preserves_retained_policy_and_keeps_cohort_private(tmp_path):
    base = json.loads(BASE.read_bytes())
    original = deepcopy(base)
    timing, prior, arrivals = (tmp_path / name for name in ("timing.json", "prior.json", "arrivals.json"))
    timing.write_text('{"timing_domain":"declared-observable-network-classes"}')
    prior.write_text("{}")
    arrivals.write_text("{}")
    result = configuration(
        base, workflow_count=48, workload_manifest=ROOT / "composed/workload_manifest.json",
        source_registration=tmp_path / "composed.registration.json",
        arrivals=arrivals, timing=timing, prior=prior,
    )
    assert base == original
    assert result["order"] == ["full"]
    assert result["common_environment"]["ROOT_COUNT"] == "48"
    assert result["common_environment"]["MAX_RUNNING_REQUESTS"] == "24"
    assert "CLIENT_ROOT_CONCURRENCY" not in result["common_environment"]
    assert result["common_environment"]["TOOL_PREFETCH"] == "0"
    assert result["common_environment"]["LAYER_HANDOFF"] == "0"
    assert result["common_environment"]["AGENT_CONTROL_INTERVAL_MS"] == "2000"
    assert result["common_environment"]["AGENT_ADMISSION"] == base["common_environment"]["AGENT_ADMISSION"]
    assert result["common_environment"]["JOIN_PREFETCH_TRIGGER"] == "observed_eos"
    assert result["comparison_contract"]["measured_cohort_not_delivered_to_scheduler"]
    assert not result["comparison_contract"]["workflow_work_ranking"]
    assert not result["native_reference"]["matched_native_capture_exists"]
    assert "analysis_cohort" not in result


def test_initial_fill_is_separate_from_the_arrival_index_warmup_and_measurement():
    arrivals, cohort = measurement_protocol(
        [f"workflow-{index}" for index in range(46)], rate_per_second=.0125,
        arrival_seed=31, permutation_seed=31, initial_workflows=6, warmup=10, measured=18,
    )
    assert arrivals["initial_workflows"] == 6
    assert len(cohort["warmup_workflow_ids"]) == 10
    assert len(cohort["measured_workflow_ids"]) == 18
    assert len(cohort["background_workflow_ids"]) == 18
    assert cohort["measured_workflow_ids"][0] == arrivals["arrival_schedule"][10]["workflow_id"]
    assert cohort["throughput_window_offset_seconds"][0] > arrivals["arrival_schedule"][6]["offset_seconds"]


def test_oracle_control_dispatch_override_changes_only_the_declared_common_cadence(tmp_path):
    timing, prior, arrivals = (tmp_path / name for name in ("timing.json", "prior.json", "arrivals.json"))
    timing.write_text('{"timing_domain":"declared-observable-network-classes"}')
    prior.write_text("{}")
    arrivals.write_text("{}")
    kwargs = dict(
        workflow_count=52, workload_manifest=ROOT / "composed/workload_manifest.json",
        source_registration=tmp_path / "source.registration.json",
        arrivals=arrivals, timing=timing, prior=prior,
    )
    base = json.loads(BASE.read_bytes())
    original = configuration(base, **kwargs)
    paired = configuration(base, **kwargs, dispatch_interval_ms=500)
    common, old = paired["common_environment"], original["common_environment"]
    assert {name for name in common if common[name] != old[name]} == {
        "AGENT_FREE_DISPATCH_INTERVAL_MS", "BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS",
    }
    assert common["AGENT_CONTROL_INTERVAL_MS"] == "2000"
    assert common["WORKFLOW_SIZE_RESUME"] == "0"
    assert paired["comparison_contract"]["free_dispatch_interval_ms"] == 500
    with pytest.raises(ValueError, match="matched 500ms"):
        configuration(base, **kwargs, dispatch_interval_ms=50)
