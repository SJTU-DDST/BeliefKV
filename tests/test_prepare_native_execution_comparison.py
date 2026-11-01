from copy import deepcopy
import json

import pytest

from beliefkv.experiments.native_execution import CONFIG_KEY, HASH_KEY
from scripts.prepare_mixed_chunk_comparison import SHARED_KEYS
from scripts.prepare_native_execution_comparison import ORDER, prepare
from tests.test_native_execution_configuration import profile


@pytest.mark.parametrize("change", (None, "arrivals", "tools", "capacity", "prefetch", "handoff"))
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
