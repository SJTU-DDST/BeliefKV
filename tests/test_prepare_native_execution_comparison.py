from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from beliefkv.experiments.native_execution import CONFIG_KEY, HASH_KEY
from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_mixed_chunk_comparison import SHARED_KEYS
from scripts.prepare_native_execution_comparison import ORDER, prepare
from tests.test_native_execution_configuration import profile


@pytest.mark.parametrize("change", (None, "arrivals", "tools", "capacity", "prefetch", "handoff", "numa"))
def test_preparation_preserves_method_policies_and_all_traffic(profile, tmp_path, change):
    checkout, profile_path, common = profile
    common = {**{key: "same" for key in SHARED_KEYS}, **common}
    common.pop(CONFIG_KEY)
    common.pop(HASH_KEY)
    common.update(ENABLE_MIXED_CHUNK="0", TOOL_PREFETCH="0", LAYER_HANDOFF="0")
    base = {
        "source_registration": "same-source", "workflow_arrivals": {"path": "same-arrivals"},
        "tool_timing_overlay": {"path": "same-tools"}, "common_environment": common,
    }
    bases = {name: {**deepcopy(base), "order": [name]} for name in ORDER}
    if change == "arrivals":
        bases[ORDER[1]]["workflow_arrivals"] = {"changed": True}
    elif change == "tools":
        bases[ORDER[1]]["tool_timing_overlay"] = {"changed": True}
    elif change == "capacity":
        bases[ORDER[1]]["common_environment"]["HICACHE_SIZE_GB"] = "256"
    elif change == "numa":
        bases[ORDER[1]]["common_environment"]["HOST_NUMA_MEMORY_POLICY"] = "bind"
    elif change in ("prefetch", "handoff"):
        key = "TOOL_PREFETCH" if change == "prefetch" else "LAYER_HANDOFF"
        bases[ORDER[0]]["common_environment"][key] = "1"
    paths = []
    for name in ORDER:
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(bases[name]))
        paths.append(path)
    cohort = tmp_path / "private-cohort.json"
    cohort.write_text('{"measured_workflow_ids":["private"]}')
    if change:
        with pytest.raises(ValueError):
            prepare(*paths, profile_path, cohort, checkout=checkout)
        return
    prepared = prepare(*paths, profile_path, cohort, checkout=checkout)
    for name in ORDER:
        before, after = bases[name]["common_environment"], prepared[name]["common_environment"]
        assert {key: value for key, value in after.items() if key not in (CONFIG_KEY, HASH_KEY)} == before
        assert prepared[name]["order"] == [name]
        assert prepared[name]["private_analysis_cohort"]["delivered_to_scheduler"] is False
        assert "private" not in json.dumps(after)


@pytest.mark.parametrize("defect", (None, "not_explicit", "one_missing", "different", "stale"))
def test_replacing_common_execution_requires_matching_validated_previous_profiles(
    profile, tmp_path, defect,
):
    checkout, profile_path, common = profile
    relative = "configs/execution/qwen3_coder_h200_exact_prefill_moe_20261101.json"
    candidate_path = checkout / relative
    source = Path(__file__).resolve().parents[1]
    shutil.copyfile(source / relative, candidate_path)
    moe = "configs/execution/h200_moe_prefill_decode"
    shutil.copytree(source / moe, checkout / moe)
    shared = {**{key: "same" for key in SHARED_KEYS}, **common}
    shared.update(ENABLE_MIXED_CHUNK="0", TOOL_PREFETCH="0", LAYER_HANDOFF="0")
    bases = {
        name: {
            "source_registration": "source", "workflow_arrivals": {"path": "arrivals"},
            "tool_timing_overlay": {"path": "tools"}, "order": [name],
            "common_environment": deepcopy(shared),
        }
        for name in ORDER
    }
    other = bases[ORDER[1]]["common_environment"]
    if defect == "one_missing":
        other.pop(CONFIG_KEY)
        other.pop(HASH_KEY)
    elif defect == "different":
        other.update({CONFIG_KEY: relative, HASH_KEY: sha256_file(candidate_path)})
    elif defect == "stale":
        other[HASH_KEY] = "stale"
    paths = []
    for name in ORDER:
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(bases[name]))
        paths.append(path)
    cohort = tmp_path / "cohort.json"
    cohort.write_text("{}")
    if defect:
        with pytest.raises(ValueError):
            prepare(
                *paths, candidate_path, cohort, checkout=checkout,
                replace_existing=defect != "not_explicit",
            )
        return
    result = prepare(*paths, candidate_path, cohort, checkout=checkout, replace_existing=True)
    for name in ORDER:
        current = result[name]
        assert current["serving_profile_experiment"]["previous_native_execution_configuration"][
            "configuration"
        ] == str(profile_path.relative_to(checkout))
        assert current["common_environment"][CONFIG_KEY] == relative
        assert {
            key: value for key, value in current["common_environment"].items()
            if key not in (CONFIG_KEY, HASH_KEY)
        } == {
            key: value for key, value in shared.items() if key not in (CONFIG_KEY, HASH_KEY)
        }
