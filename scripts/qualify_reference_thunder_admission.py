#!/usr/bin/env python3
"""Qualify reference-core Full and its explicit free-capacity JOIN extension."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from scripts.qualify_fitting_agent_lending import passed_cases, write
from scripts.run_causal_comparison import environment as serving_environment, preflight_runtime


REPORTS = ROOT / "experiments/reports"
PRIOR = REPORTS / "v377b_fitting_lending_cpu_evidence_20261027.json"
CONFIG = ROOT / "configs/migration/predictive_reference_thunder_20261027.json"
REPORT = REPORTS / "v394_reference_thunder_shared_cpu_20261027.xml"
MANIFEST = REPORTS / "v395_reference_thunder_cpu_evidence_20261027.sources.json"
OUTPUT = REPORTS / "v395_reference_thunder_cpu_evidence_20261027.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", type=Path, default=PRIOR)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--sources", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--configuration", type=Path, default=CONFIG)
    parser.add_argument("--engine", type=Path)
    args = parser.parse_args()
    if any(path.exists() for path in (args.report, args.sources, args.output)):
        raise FileExistsError("preserve previous qualification outputs")
    prior = json.loads(args.prior.read_bytes())
    configuration = json.loads(args.configuration.read_bytes())
    join_free = configuration["common_environment"].get("AGENT_JOIN_FREE_ADMISSION", "0") == "1"
    siblings = configuration["common_environment"].get("AGENT_COADMIT_JOIN_CHILDREN", "0") == "1"
    continuation_free = configuration["common_environment"].get(
        "AGENT_FREE_CONTINUATION_ADMISSION", "0",
    ) == "1"
    early_tool_offload = configuration["common_environment"].get(
        "TOOL_OFFLOAD_BEFORE_ADMISSION", "0",
    ) == "1"
    host_mirror_recycle = configuration["common_environment"].get(
        "IDLE_HOST_MIRROR_RECYCLE", "0",
    ) == "1"
    layer_handoff = configuration["common_environment"].get("LAYER_HANDOFF", "0") == "1"
    join_handoff = configuration["common_environment"].get("AGENT_JOIN_BLOCKED_HANDOFF", "0") == "1"
    host_demand = int(configuration["common_environment"].get(
        "BELIEFKV_HOST_DEMAND_RESERVE_BYTES", "0",
    )) > 0
    paths = set(json.loads(Path(prior["source_manifest"]["path"]).read_bytes()))
    paths.update((
        str(args.configuration.resolve().relative_to(ROOT)), str(Path(__file__).resolve().relative_to(ROOT)),
        "beliefkv/runtime/reference_thunder_admission.py",
        "tests/test_reference_thunder_admission.py",
        "beliefkv/runtime/paper_baseline.py", "beliefkv/policy/paper_baselines.py",
    ))
    extra_tests = (
        "tests/test_reference_join_free_admission.py",
        "tests/test_request_agent_eligibility_audit.py",
        "tests/test_collecting_agent_pipeline_audit.py",
        "tests/test_audit_tool_reentry_service.py",
    ) if join_free or siblings else ()
    extra_tests += ("tests/test_eos_join_prefetch.py",)
    if join_handoff:
        extra_tests += ("tests/test_join_blocked_handoff.py", "tests/test_join_physical_recovery.py")
        paths.update((
            "beliefkv/runtime/join_blocked_handoff.py",
            "beliefkv/runtime/join_physical_recovery.py",
        ))
    if early_tool_offload:
        extra_tests += ("tests/test_tool_offload_before_admission.py", "tests/test_consumer_input_reserve.py")
        paths.update((
            "beliefkv/runtime/predicted_tool_offload.py",
            "beliefkv/runtime/native_transfer_policy.py",
        ))
    if host_mirror_recycle:
        extra_tests += ("tests/test_idle_host_mirror_recycle.py",)
        paths.add("beliefkv/runtime/idle_host_mirror_recycle.py")
    if layer_handoff:
        extra_tests += ("tests/test_layer_handoff.py", "tests/test_native_engine_variant.py")
        paths.update((
            "beliefkv/runtime/layer_handoff.py", "scripts/qualify_layer_handoff.py",
            "scripts/qualify_layer_handoff_model.py",
            "beliefkv/experiments/native_engine_variant.py",
        ))
        declared_tree = configuration.get("comparison_contract", {}).get("native_engine_tree")
        if declared_tree:
            from beliefkv.experiments.native_engine_variant import engine_patch

            paths.add(str(engine_patch(ROOT, declared_tree).relative_to(ROOT)))
        if any(configuration["common_environment"].get(
            "BELIEFKV_LAYER_HANDOFF_" + direction + "_LAYERS", "1",
        ) != "1" for direction in ("D2H", "H2D")):
            extra_tests += ("tests/test_layer_handoff_groups.py",)
    if host_demand:
        extra_tests += ("tests/test_host_demand_capacity.py",)
        paths.add("beliefkv/runtime/host_demand_capacity.py")
    if configuration["common_environment"].get(
        "BELIEFKV_ENABLE_TOOL_PREFETCH",
        configuration["common_environment"].get("TOOL_PREFETCH"),
    ) == "0":
        extra_tests += ("tests/test_tool_prefetch_disabled.py",)
    if continuation_free:
        extra_tests += (
            "tests/test_reference_free_continuation.py",
            "tests/test_reference_capacity_wait_audit.py",
        )
        paths.update((
            "beliefkv/runtime/reference_free_continuation.py",
            "scripts/audit_reference_capacity_waits.py",
        ))
    if siblings:
        extra_tests += (
            "tests/test_reference_sibling_coadmission.py",
            "tests/test_reference_sibling_service.py",
            "tests/test_reference_join_grant_audit.py",
        )
        paths.update((
            "scripts/audit_reference_join_grants.py",
            "beliefkv/runtime/join_sibling_service.py",
        ))
    paths.update(extra_tests)
    if join_free or siblings:
        paths.update((
            "scripts/audit_request_agent_eligibility.py",
            "scripts/audit_collecting_agent_pipeline.py",
        ))
    sources = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    write(args.sources, sources)
    command = [part for part in prior["command"] if not part.startswith("--junitxml=")]
    if "tests/test_reference_thunder_admission.py" not in command:
        command.append("tests/test_reference_thunder_admission.py")
    command.extend(name for name in extra_tests if name not in command)
    command.append(f"--junitxml={args.report.resolve().relative_to(ROOT)}")
    environment = {
        **os.environ, **prior["environment"],
        "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
        "BELIEFKV_CONTEXT_TOKENIZER_TEST": configuration["common_environment"].get(
            "CONTEXT_TOKENIZER_PATH", configuration["common_environment"]["MODEL_PATH"],
        ),
    }
    if args.engine is not None:
        environment["SGLANG_SOURCE_CHECKOUT"] = str(args.engine.resolve())
    native = None
    declared_tree = configuration.get("comparison_contract", {}).get("native_engine_tree")
    if declared_tree:
        from beliefkv.experiments.native_engine_variant import engine_patch
        from beliefkv.experiments.source_provenance import git_source_state

        native = git_source_state(
            Path(environment["SGLANG_SOURCE_CHECKOUT"]),
            canonical_patch=engine_patch(ROOT, declared_tree),
        )
        if native["canonical_patch"]["actual_tree"] != declared_tree:
            raise ValueError("CPU engine differs from the declared exact native tree")
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    previous, current = passed_cases(Path(prior["report"]["path"])), passed_cases(args.report)
    if not previous <= current:
        raise ValueError("shared CPU coverage lost previous case identities")
    inherited = ("attach", "tick", "completed", "aborted", "_refresh_streaming_progress")
    if any(
        getattr(ReferenceThunderAdmission, name) is not getattr(ThunderAgentDecayAdapter, name)
        for name in inherited
    ):
        raise ValueError("Full changed a reference scheduling or native lifecycle method")
    constructors = {}
    for label in ("full", "thunderagent", "thunderagent_acting_decay"):
        settings, _, _ = serving_environment(
            {"arms": {"full": configuration["common_environment"]}},
            label, ROOT, Path(environment["SGLANG_SOURCE_CHECKOUT"]),
            ROOT / "experiments/raw/cpu_reference_thunder_unallocated" / label,
            18658, ROOT / "experiments/raw/cpu_reference_thunder_unallocated/plugin",
            sampling_mode="position", max_running_requests=24,
        )
        expected = "thunderagent_reference" if label == "full" else "off"
        if settings["BELIEFKV_AGENT_ADMISSION"] != expected:
            raise ValueError("Full composition leaked into a paper control")
        if settings["BELIEFKV_AGENT_JOIN_FREE_ADMISSION"] != str(int(join_free and label == "full")):
            raise ValueError("free-capacity JOIN admission leaked into a paper control")
        if settings["BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF"] != str(int(join_handoff and label == "full")):
            raise ValueError("JOIN blocked handoff leaked into a paper control")
        if settings["BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN"] != str(int(siblings and label == "full")):
            raise ValueError("sibling admission leaked into a paper control")
        if settings["BELIEFKV_AGENT_FREE_CONTINUATION_ADMISSION"] != str(
            int(continuation_free and label == "full")
        ):
            raise ValueError("free continuation admission leaked into a paper control")
        if settings["BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION"] != str(
            int(early_tool_offload and label == "full")
        ):
            raise ValueError("tool offload before admission leaked into a paper control")
        if settings["BELIEFKV_IDLE_HOST_MIRROR_RECYCLE"] != str(
            int(host_mirror_recycle and label == "full")
        ):
            raise ValueError("idle Host mirror recycling leaked into a paper control")
        if settings["BELIEFKV_LAYER_HANDOFF"] != str(int(layer_handoff and label == "full")):
            raise ValueError("layer handoff leaked into a paper control")
        constructors[label] = preflight_runtime(
            {**settings, "SGLANG_SOURCE_CHECKOUT": environment["SGLANG_SOURCE_CHECKOUT"]},
            configuration["common_environment"]["MODEL_PATH"],
        )
    if any(sha256_file(ROOT / name) != expected for name, expected in sources.items()):
        raise ValueError("qualified CPU sources changed while executing")
    if native is not None and git_source_state(
        Path(environment["SGLANG_SOURCE_CHECKOUT"]),
        canonical_patch=engine_patch(ROOT, declared_tree),
    ) != native:
        raise ValueError("CPU native engine changed while executing")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "source_bound_cpu_full_with_reference_thunderagent_decay",
        "command": command,
        "environment": {
            key: environment[key]
            for key in (*prior["environment"], "BELIEFKV_CONTEXT_TOKENIZER_TEST")
        },
        "context_tokenizer": {
            "path": environment["BELIEFKV_CONTEXT_TOKENIZER_TEST"],
            "files_sha256": {
                name: sha256_file(Path(environment["BELIEFKV_CONTEXT_TOKENIZER_TEST"]) / name)
                for name in ("tokenizer_config.json", "tokenizer.json")
            },
        },
        "report": {"path": str(args.report.resolve()), "sha256": sha256_file(args.report)},
        "source_manifest": {"path": str(args.sources.resolve()), "sha256": sha256_file(args.sources)},
        "native_engine": native,
        "previous_report": prior["report"],
        "passing_cases_including_subtests": len(current),
        "retained_previous_cases_including_subtests": len(previous),
        "new_cases": len(current - previous),
        "reference_methods_inherited_without_override": list(inherited),
        "configuration": {
            "path": str(args.configuration.resolve()), "sha256": sha256_file(args.configuration),
        },
        "join_free_admission": join_free,
        "coadmit_join_children": siblings,
        "free_continuation_admission": continuation_free,
        "tool_offload_before_admission": early_tool_offload,
        "idle_host_mirror_recycle": host_mirror_recycle,
        "layer_handoff": layer_handoff,
        "join_blocked_handoff": join_handoff,
        "constructors": constructors,
        "preserved_development_reports": [
            str(REPORTS / name) for name in (
                "v393_reference_thunder_admission_targeted_cpu_20261027.xml",
                "v393b_reference_thunder_admission_targeted_cpu_20261027.xml",
                "v398_reference_thunder_offload_targeted_cpu_20261027.xml",
                "v398b_reference_thunder_offload_targeted_cpu_20261027.xml",
            )
        ],
        "development_failures": (
            "New test fixtures initially omitted the required event socket and one "
            "JOIN member's invocation_create; the native offload fixture initially "
            "retained legacy execution recovery and was aligned with the declared "
            "reference profile. Preserve the separate actual V396b integration failure."
        ),
        "gpu_development_failure": {
            "path": str(REPORTS / "v397_reference_thunder_offload_integration_failure_20261027.json"),
            "sha256": sha256_file(
                REPORTS / "v397_reference_thunder_offload_integration_failure_20261027.json",
            ),
        } if (REPORTS / "v397_reference_thunder_offload_integration_failure_20261027.json").exists() else None,
        "native_cpu_copy_event_and_host_index_standins": True,
        "gpu_execution_registered": False,
        "gpu_correctness_verified": False,
        "performance_accepted": False,
        "research_contribution_claimed": False,
    }
    write(args.output, result)
    print(json.dumps({
        key: result[key] for key in (
            "passing_cases_including_subtests", "new_cases",
            "reference_methods_inherited_without_override",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
