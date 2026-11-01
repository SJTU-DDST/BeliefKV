import json

import pytest

from scripts.tune_moe_token_cost import (
    BASE_LARGE, BASE_SMALL, SIZES, compact_table, guarded_lookup_table, select_candidate, serve,
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


def test_combined_probe_passes_exact_mode_and_restores_environment_on_failure(tmp_path, monkeypatch):
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
        serve(tmp_path / "inputs.json", tmp_path, tmp_path / "probe", graph_mode="exact")
    import os

    assert calls == [(["exact"], True, str(tmp_path.resolve()))]
    assert os.environ["SGLANG_MOE_CONFIG_DIR"] == "previous"
    record = json.loads((tmp_path / "probe.configuration.json").read_text())
    assert record["graph_mode"] == "exact"
