"""Check prepared traffic, policy preflights and the real cohort-stop contract."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.host_numa import configuration, expected_full_bytes
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts.run_causal_comparison import environment, preflight_runtime
from scripts.stop_after_measured_cohort import native_baseline_declared, replay_arm
from scripts.train_workflow_work import write


def qualify(prepared, inherited, native, output):
    if output.exists():
        raise FileExistsError("preserve earlier protocol qualifications")
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("require hidden CUDA, CPU0 and nice19")
    previous = json.loads(inherited.read_bytes())
    manifest_path = Path(previous["source_manifest"]["path"])
    if (
        previous.get("qualification_passed") is not True
        or sha256_file(manifest_path) != previous["source_manifest"]["sha256"]
    ):
        raise ValueError("require the complete prior service CPU evidence")
    previous_sources = json.loads(manifest_path.read_bytes())
    unchanged_runtime = {
        name: digest for name, digest in previous_sources.items()
        if name.startswith(("beliefkv/runtime/", "beliefkv/policy/", "beliefkv/control/"))
    }
    if not unchanged_runtime or any(
        sha256_file(ROOT / name) != digest for name, digest in unchanged_runtime.items()
    ):
        raise ValueError("prepared protocol changed the previously qualified runtime")
    pins = {
        str(path.resolve()): sha256_file(path)
        for path in (inherited, manifest_path, Path(__file__), prepared / "preparation.json")
    }
    preparation = json.loads((prepared / "preparation.json").read_bytes())
    if set(preparation["artifacts"]) != {
        "control", "pressure", "thunderagent_acting_decay", "native_matched",
    }:
        raise ValueError("require the four explicitly prepared matching arms")
    results, shared = {}, None
    with tempfile.TemporaryDirectory(prefix="bkv-materialization-protocol-") as temporary:
        for index, (label, binding) in enumerate(preparation["artifacts"].items()):
            path = Path(binding["path"])
            if sha256_file(path) != binding["sha256"]:
                raise ValueError("a prepared arm changed")
            pins[str(path.resolve())] = binding["sha256"]
            spec = json.loads(path.read_bytes())
            common = spec["common_environment"]
            source_registration = Path(spec["source_registration"]).resolve()
            source_digest = sha256_file(source_registration)
            prior_source_digest = previous.get("external_inputs_sha256", {}).get(
                str(source_registration),
            )
            if prior_source_digest is not None and source_digest != prior_source_digest:
                raise ValueError("original workload registration changed")
            pins[str(source_registration)] = source_digest
            for item in (
                spec["workflow_arrivals"], spec["private_analysis_cohort"],
                spec["tool_timing_overlay"],
                {"path": common["WORKFLOW_WORK_MODEL"],
                 "sha256": common["WORKFLOW_WORK_MODEL_SHA256"]},
            ):
                target = Path(item["path"])
                if sha256_file(target) != item["sha256"]:
                    raise ValueError("prepared traffic or prediction artifact changed")
                pins[str(target.resolve())] = item["sha256"]
            arrivals = json.loads(Path(spec["workflow_arrivals"]["path"]).read_bytes())
            cohort = json.loads(Path(spec["private_analysis_cohort"]["path"]).read_bytes())
            validate_cohort(cohort, arrivals)
            identity = {
                name: spec[name] for name in (
                    "source_registration", "workflow_arrivals", "private_analysis_cohort",
                    "tool_timing_overlay",
                )
            }
            identity["Host_bytes"] = expected_full_bytes(common)
            identity["HBM_tokens"] = common.get("DEVICE_FULL_TOKENS")
            identity["running"] = common["MAX_RUNNING_REQUESTS"]
            identity["native_execution"] = common["NATIVE_EXECUTION_CONFIG_SHA256"]
            identity["NUMA"] = configuration(common)
            if shared is not None and identity != shared:
                raise ValueError("prepared arms changed shared traffic or resources")
            shared = identity
            mode, = spec["order"]
            settings, _, _ = environment(
                {"arms": {"full": common}}, mode, ROOT, native, Path(temporary),
                19990 + index, ROOT / "plugins",
                max_running_requests=int(common["MAX_RUNNING_REQUESTS"]),
                tool_restore_mode=common["TOOL_RESTORE_MODE"],
                execution_frontier_restore=common.get("EXECUTION_FRONTIER_RESTORE") == "1",
                frontier_cold_reclaim=common.get("FRONTIER_COLD_RECLAIM") == "1",
                sampling_mode="position",
            )
            settings["CUDA_VISIBLE_DEVICES"] = "99999"
            preflight = preflight_runtime(settings, common["MODEL_PATH"])
            registration = {
                "order": spec["order"], "run_root": temporary,
                "experiment_configuration": str(path),
                "external_inputs": [{"path": str(path), "sha256": binding["sha256"]}],
            }
            if replay_arm(registration) != Path(temporary) / mode:
                raise ValueError("production cohort stopper selected a different arm")
            if native_baseline_declared(registration) != (label == "native_matched"):
                raise ValueError("native baseline stopping contract is ambiguous")
            expected_gate = "pressure_v1" if label == "pressure" else "off"
            if settings["BELIEFKV_SUBAGENT_MATERIALIZATION"] != expected_gate:
                raise ValueError("materialization gate leaked into another arm")
            if label == "native_matched" and preflight != {"native_runtime_not_enabled": True}:
                raise ValueError("native preflight unexpectedly constructed a Full runtime")
            results[label] = {
                "mode": mode, "preflight": preflight,
                "production_fixed_cohort_stopper_accepts": True,
                "materialization_policy": expected_gate,
                "native_runtime_not_constructed": label == "native_matched",
            }
    if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
        raise ValueError("protocol inputs changed during qualification")
    if any(sha256_file(ROOT / name) != digest for name, digest in unchanged_runtime.items()):
        raise ValueError("runtime changed during protocol qualification")
    report = {
        "protocol_qualification_passed": True,
        "prepared": str(prepared.resolve()), "arms": results,
        "same_traffic_resources_and_fixed_measurement_contract": shared,
        "qualified_runtime_sources_unchanged": len(unchanged_runtime),
        "inputs_sha256": pins, "prior_runtime_CPU_evidence": str(inherited.resolve()),
        "not_an_actual_GPU_allocator_or_model_qualification": True,
        "GPU_action": False, "GPU_followup_registered": False, "performance_accepted": False,
    }
    write(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared", "inherited", "native", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = qualify(args.prepared, args.inherited, args.native, args.output)
    print(json.dumps({
        "protocol_qualification_passed": report["protocol_qualification_passed"],
        "arms": list(report["arms"]), "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
