import json

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts.merge_native_token_cost import merge


@pytest.mark.parametrize("defect", [None, "overlap", "native", "accounting", "numerical"])
def test_only_disjoint_matched_comparisons_with_same_resources_are_merged(tmp_path, defect):
    def put(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    accounting = tmp_path / "accounting.json"
    put(accounting, {
        "scopes": {"common_window": {"worker_ms": 100.}},
        "phase_costs": {"window:decode": {"worker_ms": 80.}},
        "common_window_decode_by_width": {"6": {"worker_ms": 30.}, "10": {"worker_ms": 20.}},
    })
    inputs = tmp_path / "inputs.json"
    put(inputs, {
        "device_full_tokens": 805198, "engine_tree": "same",
        "capture_buckets": [1, 2], "sparse_buckets": [1, 2],
    })
    paths = []
    for index, width in enumerate(("6", "6" if defect == "overlap" else "10")):
        plan = tmp_path / str(index) / "plan.json"
        put(plan, {
            "native": {"state": "different" if defect == "native" and index else "same"},
            "inputs": str(inputs), "inputs_sha256": sha256_file(inputs),
        })
        path = tmp_path / str(index) / "comparison.json"
        put(path, {
            "candidate_label": "exact_moe",
            "inputs_sha256": {
                str(accounting): "bad" if defect == "accounting" and index else sha256_file(accounting),
                str(plan): sha256_file(plan),
            },
            "numerical": {
                "requests": 1,
                "all_output_sequences_equal": not (defect == "numerical" and index),
                "rows": [{"request": f"r{index}", "tokens": 96}],
            },
            "by_width": {width: {"candidate_worker_cost_reduction_percent": 10.}},
        })
        paths.append(path)
    if defect:
        with pytest.raises(ValueError):
            merge(paths, accounting)
    else:
        result = merge(paths, accounting)
        assert result["numerical"]["requests"] == 2
        assert result["numerical"]["output_tokens"] == 192
        assert result["sample_reweighting"]["covered_common_window_worker_fraction"] == .5
        assert result["sample_reweighting"]["covered_common_window_decode_worker_fraction"] == .625
        assert result["sample_reweighting"]["estimated_total_worker_reduction_percent"] == 5.
