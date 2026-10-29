"""Freeze the free-extent candidate against the current warm-start control."""

from copy import deepcopy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE
from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file


def main():
    output = HERE / "full_warm_start.json"
    if output.exists():
        raise FileExistsError("preserve the prepared candidate")
    base_path = ROOT / "configs/migration/predictive_demand_prefill_pipeline_20261028.json"
    control_path = ROOT / "experiments/reports/warm_start_thunder_20261029/thunderagent.json"
    base, control = (json.loads(path.read_bytes()) for path in (base_path, control_path))
    arrivals = deepcopy(control["workflow_arrivals"])
    manifest = Path(arrivals["path"])
    if sha256_file(manifest) != arrivals["sha256"]:
        raise ValueError("the control's actual arrival table changed")
    arrival = json.loads(manifest.read_bytes())
    validate_arrival_plan(arrival, [row["workflow_id"] for row in arrival["arrival_schedule"]])
    spec = deepcopy(base)
    if any(
        base["tool_timing_overlay"][field] != control["tool_timing_overlay"][field]
        for field in ("path", "sha256")
    ):
        raise ValueError("the base and control use different actual tool timings")
    spec["tool_timing_overlay"] = deepcopy(control["tool_timing_overlay"])
    spec["status"] = "cpu_candidate_requires_current_gpu_components_model_and_position"
    spec["common_environment"].update({
        "SGLANG_PATCH_FLAVOR": "layer_free_extent",
        "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "1",
    })
    spec["comparison_contract"].update({
        "native_engine_tree": FREE_EXTENT_TREE,
        "native_handoff_free_extent": "actually_allocated_after_complete_input_growth_reserve",
        "native_demand_pipeline": "actual_fitting_batch_including_first_unallocated_chunk",
    })
    spec["workflow_arrivals"] = arrivals
    spec["gpu_execution"] = {
        "registered": False,
        "complete_control": str(ROOT.parent / "ops/warm_start_thunder_20261029_r1.registration.json"),
        "requires_fresh_free_extent_components": True,
        "requires_unforced_free_extent_model_restore": True,
        "requires_fresh_position_and_sampling": True,
    }
    spec["candidate_provenance"] = {
        "base": str(base_path), "base_sha256": sha256_file(base_path),
        "control": str(control_path), "control_sha256": sha256_file(control_path),
        "arrival_schedule_sha256": arrival["arrival_schedule_sha256"],
        "same_work_tokens_dependencies_tools_capacity_seeds_and_serving_profile": True,
        "formal_gpu_registered": False,
    }
    with output.open("x") as stream:
        json.dump(spec, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"candidate": str(output), "sha256": sha256_file(output),
                      "GPU_registered": False}, indent=2))


if __name__ == "__main__":
    main()
