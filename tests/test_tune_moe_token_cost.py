import json

import pytest

from scripts.tune_moe_token_cost import (
    BASE_LARGE, BASE_SMALL, PREFILL_SIZES, SIZES, compact_table, guarded_lookup_table,
    guarded_prefill_lookup_table, prefill_candidate_configs, select_candidate, serve, tune,
)
from beliefkv.experiments.source_provenance import sha256_file


def test_native_nearest_lookup_keeps_every_untuned_prefill_size_unchanged():
    table = {int(key): val for key, val in guarded_lookup_table({
        size: {**BASE_SMALL, "BLOCK_SIZE_N": 128} for size in SIZES
    }).items()}
    for size in range(25, 4097):
        nearest = min(table, key=lambda key: abs(key - size))
        assert table[nearest] == (BASE_SMALL if size <= 128 else BASE_LARGE)


def row(index, uniform, skewed, close=True):
    return {
        "candidate_index": index, "numerically_close": close,
        "scenario_median_ms": {"uniform": uniform, "skewed": skewed},
    }


@pytest.mark.parametrize("candidate", [
    row(1, .5, .5, close=False),
    row(1, .5, 1.03),
    row(1, .99, .99),
])
def test_numerical_failure_route_regression_and_weak_improvement_keep_default(candidate):
    assert select_candidate([row(0, 1, 1), candidate])["candidate_index"] == 0


def test_improvement_in_both_routing_scenarios_can_be_selected():
    assert select_candidate([row(0, 1, 1), row(1, .8, .9)])["candidate_index"] == 1


def test_compact_defaults_preserve_nearest_lookup_and_boundary_ties():
    original = guarded_lookup_table({
        size: {**BASE_SMALL, "BLOCK_SIZE_N": 128} for size in SIZES
    })
    compacted = compact_table(original)
    assert len(compacted) == 6
    assert compacted["25"] == BASE_SMALL
    assert compacted["128"] == BASE_SMALL
    assert compacted["129"] == BASE_LARGE
    assert compacted["2048"] == BASE_LARGE


def test_prefill_candidates_start_with_the_actual_large_M_default():
    assert prefill_candidate_configs()[0] == BASE_LARGE
    assert len(prefill_candidate_configs()) == 8


def test_prefill_table_preserves_existing_decode_and_all_untuned_sizes():
    baseline = compact_table(guarded_lookup_table({
        size: {**BASE_SMALL, "BLOCK_SIZE_N": 128, "num_warps": 4} for size in SIZES
    }))
    selected = {size: {**BASE_LARGE, "BLOCK_SIZE_M": size} for size in PREFILL_SIZES}
    tuned = guarded_prefill_lookup_table(selected, baseline)
    original = {int(key): value for key, value in baseline.items()}
    actual = {int(key): value for key, value in tuned.items()}
    for size in range(1, 8193):
        found = actual[min(actual, key=lambda key: abs(key - size))]
        expected = (
            selected[min(selected, key=lambda key: abs(key - size))]
            if 256 <= size <= 2048
            else original[min(original, key=lambda key: abs(key - size))]
        )
        assert found == expected
    assert len(tuned) < 20


def test_prefill_table_keeps_the_original_when_every_candidate_is_rejected():
    baseline = compact_table(guarded_lookup_table({size: BASE_SMALL for size in SIZES}))
    assert guarded_prefill_lookup_table(
        {size: BASE_LARGE for size in PREFILL_SIZES}, baseline,
    ) == baseline


def test_incomplete_prefill_measurements_cannot_create_a_configuration():
    with pytest.raises(ValueError, match="all bounded prefill sizes"):
        guarded_prefill_lookup_table({256: BASE_LARGE}, {"1": BASE_SMALL})


@pytest.mark.parametrize("profile,baseline", [("prefill", None), ("decode", "base.json"), ("other", None)])
def test_invalid_tuning_scope_is_rejected_before_GPU_resource_access(tmp_path, profile, baseline):
    with pytest.raises(ValueError, match="profile|configuration"):
        tune(
            tmp_path / "absent.json", tmp_path / "output",
            tuning_profile=profile, base_configuration=baseline,
        )
    assert not (tmp_path / "output").exists()


def test_prefill_source_changes_are_rejected_before_GPU_access(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"1": BASE_SMALL, "129": BASE_LARGE}))
    source = tmp_path / "source.json"
    source.write_text("{}")
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"source_sha256": {str(source): "changed"}}))

    def unexpected_GPU_check(*_args, **_kwargs):
        raise AssertionError("GPU guard reached before source validation")

    monkeypatch.setattr("scripts.tune_moe_token_cost.fresh.check_hold", unexpected_GPU_check)
    with pytest.raises(ValueError, match="prepared component source changed"):
        tune(
            inputs, tmp_path / "output", tuning_profile="prefill",
            base_configuration=baseline,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("profile", [False, True])
def test_combined_probe_passes_exact_mode_and_restores_environment_on_failure(tmp_path, monkeypatch, profile):
    config = tmp_path / "launch.json"
    config.write_text("{}")
    (tmp_path / "result.json").write_text(json.dumps({
        "configuration_path": str(config), "configuration_sha256": sha256_file(config),
    }))
    monkeypatch.setenv("SGLANG_MOE_CONFIG_DIR", "previous")
    calls = []

    def fail(inputs, output, *, modes, profile):
        import os

        calls.append((modes, profile, os.environ["SGLANG_MOE_CONFIG_DIR"]))
        raise RuntimeError("component failed")

    monkeypatch.setattr("scripts.profile_native_token_cost.run", fail)
    with pytest.raises(RuntimeError, match="component failed"):
        serve(tmp_path / "inputs.json", tmp_path, tmp_path / "probe", graph_mode="exact", profile=profile)
    import os

    assert calls == [(["exact"], profile, str(tmp_path.resolve()))]
    assert os.environ["SGLANG_MOE_CONFIG_DIR"] == "previous"
    record = json.loads((tmp_path / "probe.configuration.json").read_text())
    assert record["graph_mode"] == "exact"
    assert record["profile"] is profile
