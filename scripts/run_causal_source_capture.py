#!/usr/bin/env python3
"""Record one predeclared native trajectory; never count it as a performance arm."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.delegation_policy import (
    DYNAMIC_NESTED_IN_GRAPH_PROFILES, NESTED_IN_GRAPH_PROFILE, REUSABLE_NESTED_IN_GRAPH_PROFILE,
)
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_eos_pair_after_modules as pair
from scripts.audit_causal_replay import audit as audit_graph
from scripts.audit_replay_token_capture import audit as audit_tokens
from scripts.qualify_locobench_environment import production_execution_evidence

fresh, comparison = pair.fresh, pair.comparison
qualified, reservation, load = pair.qualified, pair.reservation, pair.load


def reference_complete(plan, state, *, alive=reservation.alive):
    if (
        state.get("status") != "collected_performance_review_pending"
        or state.get("completed_arms") != plan["order"]
        or any(alive(state[name]) for name in ("watcher", "driver"))
    ):
        raise ValueError("reference pair must finish and release its verified processes")


def validated_capacity(capacity):
    fields = (
        "device_full_bytes", "device_full_tokens", "device_mamba_bytes", "device_mamba_slots",
        "device_total_bytes", "host_full_bytes", "host_full_tokens", "host_mamba_bytes",
        "host_mamba_slots", "host_total_bytes",
    )
    full_only = isinstance(capacity, dict) and capacity.get("pool_layout") == "static_full_only"
    state_fields = {"device_mamba_bytes", "device_mamba_slots", "host_mamba_bytes", "host_mamba_slots"}
    if (
        not isinstance(capacity, dict)
        or capacity.get("pool_layout") not in ("static_separate_full_mamba", "static_full_only")
        or any(
            type(capacity.get(name)) is not int
            or (capacity[name] != 0 if full_only and name in state_fields else capacity[name] <= 0)
            for name in fields
        )
        or capacity["device_total_bytes"] != (
            capacity["device_full_bytes"] + capacity["device_mamba_bytes"]
        )
        or capacity["host_total_bytes"] != (
            capacity["host_full_bytes"] + capacity["host_mamba_bytes"]
        )
    ):
        raise ValueError("capture needs a complete positive reference capacity census")
    return dict(capacity)


def reference_capacity(reference):
    path = Path(reference["run_root"]) / "native_matched/server/native_capacity_census.json"
    return path, validated_capacity(load(path).get("capacity"))


def validated_workload(path, *, require_disjoint=True):
    manifest = load(path)
    rows = manifest.get("workloads")
    count = manifest.get("requested_workflows")
    if (
        type(count) is not int or not 1 <= count <= 156
        or not isinstance(rows, list) or len(rows) != count
        or type(manifest.get("max_tasks_per_project")) is not int
        or not 1 <= manifest["max_tasks_per_project"] <= 5
        or (require_disjoint and manifest["max_tasks_per_project"] != 1)
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise ValueError("source workload must declare its complete bounded project population")
    projects = [row.get("repo") for row in rows]
    instances = [row.get("instance_id") for row in rows]
    if (
        any(type(value) is not str or not value for value in (*projects, *instances))
        or (require_disjoint and len(set(projects)) != count)
        or max(Counter(projects).values()) > manifest["max_tasks_per_project"]
        or len(set(instances)) != count
        or any(row.get("task_kind") != "locobench_agent" for row in rows)
    ):
        raise ValueError("source workload needs distinct original tasks and bounded projects")
    return manifest


def project_disjoint_workload(path, *, expected_workflows=None):
    manifest = validated_workload(path)
    if (
        expected_workflows is not None
        and manifest["requested_workflows"] != expected_workflows
    ):
        raise ValueError("source workload differs from the declared workflow population")
    return manifest


def declared_workload_inputs(
    path, *, require_disjoint=True, require_test_execution=False,
    require_production_execution=False, require_tool_execution=False,
):
    manifest = validated_workload(path, require_disjoint=require_disjoint)
    inputs = [path]
    tools = manifest.get("tool_environment_qualification")
    if require_tool_execution and tools is None:
        raise ValueError("declared source lacks live root/child tool qualification")
    if tools is not None:
        tool_report, tool_base = Path(tools["path"]), Path(tools["base_manifest"])
        if (
            sha256_file(tool_report) != tools["sha256"]
            or sha256_file(tool_base) != tools["base_manifest_sha256"]
        ):
            raise ValueError("declared source tool evidence changed")
        proof = load(tool_report)
        if (
            proof.get("tool_execution_passed") is not True
            or proof.get("qualification_scope") != "live_root_child_tools"
            or proof.get("manifest_sha256") != tools["base_manifest_sha256"]
        ):
            raise ValueError("declared source tools were not qualified")
        for row in manifest["workloads"]:
            observed = proof["projects"][row["source_repo"]]
            image = row["docker_image"]
            if (
                observed.get("tool_execution_passed") is not True
                or observed["source_commit"] != row["base_commit"]
                or observed["docker_image_id"] != image
                or observed["sandbox_environment"] != row.get("sandbox_environment", {})
                or qualified.command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]) != image
            ):
                raise ValueError("declared source tool configuration changed")
        inputs.extend((tool_report, tool_base))
    environment = manifest.get("environment_qualification")
    if require_test_execution and environment is None:
        raise ValueError("declared source environment lacks test execution qualification")
    if require_production_execution and (
        environment is None or environment.get("production_execution_required") is not True
    ):
        raise ValueError("declared source environment lacks project execution qualification")
    if environment is not None:
        report_path, base_path = Path(environment["path"]), Path(environment["base_manifest"])
        if (
            sha256_file(report_path) != environment["sha256"]
            or sha256_file(base_path) != environment["base_manifest_sha256"]
        ):
            raise ValueError("declared source environment evidence changed")
        report = load(report_path)
        if (
            report.get("dependency_preflight_passed") is not True
            or report.get("test_execution_passed") is not True
            or environment.get("test_execution_required") is not True
            or report.get("manifest_sha256") != environment["base_manifest_sha256"]
            or require_production_execution and (
                report.get("production_execution_required") is not True
                or report.get("production_execution_passed") is not True
            )
        ):
            raise ValueError("declared source environment was not qualified")
        inputs.extend((report_path, base_path))
    for row in manifest["workloads"]:
        if environment is not None:
            image = row["docker_image"]
            evidence = report["projects"][row["source_repo"]]
            if (
                evidence["docker_image_id"] != image
                or evidence["source_commit"] != row["base_commit"]
                or evidence.get("test_execution", {}).get("qualified") is not True
                or evidence.get("original_test_command") != row.get("test_command")
                or evidence.get("sandbox_environment", {}) != row.get("sandbox_environment", {})
                or require_production_execution and (
                    evidence.get("production_execution", {}).get("qualified") is not True
                    or not production_execution_evidence(
                        evidence.get("production_probe_result", {}),
                    )["qualified"]
                )
                or qualified.command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]) != image
            ):
                raise ValueError("declared source environment image changed or is unavailable")
        context = row["frozen_repository_context"]
        provenance = row["dataset_provenance"]
        for name, expected in (
            (context["path"], context["sha256"]),
            (provenance["source_provenance_path"], provenance["source_provenance_sha256"]),
        ):
            source = Path(name)
            if sha256_file(source) != expected:
                raise ValueError("declared source workload artifact changed")
            inputs.append(source)
        if qualified.command([
            "git", "-C", row["source_repo"], "rev-parse", row["base_commit"] + "^{commit}",
        ]) != row["base_commit"]:
            raise ValueError("declared source workload commit changed")
    return inputs


def cuda_toolchain_evidence(home):
    home = home.resolve()
    names = (
        "bin/nvcc", "bin/ptxas", "nvvm/bin/cicc", "nvvm/libdevice/libdevice.10.bc",
        "include/cuda_runtime_api.h", "include/curand.h", "include/curand_kernel.h",
        "include/nv/target", "include/cccl/cub/version.cuh",
        "lib/libcudart.so.13", "lib64/libcudart.so",
    )
    paths = [home / name for name in names]
    if any(not path.is_file() for path in paths):
        raise ValueError("capture CUDA toolchain is incomplete")
    compiler = qualified.command([str(home / "bin/nvcc"), "--version"])
    compiler_version = re.search(r"release\s+(\d+)\.(\d+)", compiler)
    runtime_version = re.search(
        r"^\s*#define\s+CUDART_VERSION\s+(\d+)",
        (home / "include/cuda_runtime_api.h").read_text(), re.MULTILINE,
    )
    if compiler_version is None or runtime_version is None:
        raise ValueError("capture CUDA compiler/runtime version is unavailable")
    compiler_pair = tuple(map(int, compiler_version.groups()))
    runtime_number = int(runtime_version.group(1))
    runtime_pair = runtime_number // 1000, runtime_number % 1000 // 10
    if compiler_pair != runtime_pair or compiler_pair[0] != 13:
        raise ValueError("capture CUDA compiler/runtime versions must match")
    return {
        "home": str(home), "compiler_version": compiler_version.group(0),
        "runtime_version": runtime_number,
        "inputs": [{"path": str(path), "sha256": sha256_file(path)} for path in paths],
    }


def environment(
    reference, checkout: Path, engine: Path, run: Path, port: int, *, source_profile=None,
    capacity=None, source_workload=None, max_running_requests=None,
    sampling_profile=None,
    cuda_home=None, experiment=None,
):
    if source_profile not in (None, "balanced_join", "reusable_nested", "nested_oneshot"):
        raise ValueError("unknown predeclared source profile")
    if max_running_requests is not None and (
        type(max_running_requests) is not int or not 1 <= max_running_requests <= 9999
    ):
        raise ValueError("max running requests must be a positive integer")
    if sampling_profile not in (None, "legacy_greedy", "qwen35_coding", "qwen3_coder"):
        raise ValueError("unknown source sampling profile")
    run = run.resolve()
    base = reference["arms"]["native_matched"]
    required = {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21", "PYTHONHASHSEED": "21",
        "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
        "CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.95",
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "FANOUT_PROFILE": "native_in_graph_nested_2to4",
    }
    if any(base.get(name) != value for name, value in required.items()):
        raise ValueError("capture must preserve the existing seeded 36-root workload")
    if experiment is not None:
        from scripts.run_causal_comparison import experiment_reference

        configured = experiment_reference({"arms": {"full": base}}, experiment)["arms"]["full"]
        if source_profile != experiment["source_profile"] or source_workload is None:
            raise ValueError("source needs the predeclared experiment workload and profile")
        if (ROOT / experiment["common_environment"]["WORKLOAD_MANIFEST"]).resolve() != source_workload.resolve():
            raise ValueError("source workload differs from the predeclared experiment")
        base = configured
    declared = (
        Path(base["WORKLOAD_MANIFEST"]) if source_workload is None
        else source_workload.resolve()
    )
    origin = Path(reference["execution_checkout"]) if source_workload is None else ROOT
    relative = declared.relative_to(origin.resolve())
    target = checkout / relative
    if sha256_file(target) != sha256_file(declared):
        raise ValueError("capture workload manifest changed")
    roots = base["ROOT_COUNT"]
    if source_workload is not None:
        if source_profile not in ("reusable_nested", "nested_oneshot"):
            raise ValueError("a new source workload requires an explicit natural source profile")
        roots = str(project_disjoint_workload(target)["requested_workflows"])
    result = arm_environment(dict(base), "native_matched")
    result.update(
        PYTHONPATH=str(checkout), WORKLOAD_MANIFEST=str(target),
        ROOT_COUNT=roots,
        SGLANG_SOURCE_CHECKOUT=str(engine), RUN_ROOT=str(run / "native_capture"),
        PORT=str(port), CUDA_VISIBLE_DEVICES="0", BELIEFKV_REPLAY_TOKEN_CAPTURE="1",
        BELIEFKV_SUBAGENT_RETENTION="off",
    )
    if sampling_profile is not None:
        result["SAMPLING_PROFILE"] = sampling_profile
    if cuda_home is not None:
        result["CUDA_HOME"] = str(cuda_home.resolve())
    if source_profile == "reusable_nested":
        result["FANOUT_PROFILE"] = REUSABLE_NESTED_IN_GRAPH_PROFILE
    elif source_profile == "nested_oneshot" and experiment is None:
        result["FANOUT_PROFILE"] = NESTED_IN_GRAPH_PROFILE
    if max_running_requests is not None:
        result["MAX_RUNNING_REQUESTS"] = str(max_running_requests)
    if capacity is not None:
        capacity = validated_capacity(capacity)
        result.update(
            DEVICE_FULL_TOKENS=str(capacity["device_full_tokens"]),
            DEVICE_MAMBA_SLOTS=str(capacity["device_mamba_slots"]) if capacity["device_mamba_slots"] else "",
        )
    if result.get("MODEL_CACHE_LAYOUT") == "full_only":
        result.update(
            DEVICE_MAMBA_SLOTS="", HOST_SPLIT="auto",
            BELIEFKV_FULL_MAMBA_HOST_SPLIT="auto", REASONING_PARSER="",
        )
        if capacity is None:
            result["DEVICE_FULL_TOKENS"] = ""
    return result


def engine_evidence(engine: Path, report: Path, sources: Path) -> dict:
    proof = load(sources)
    observed = git_source_state(
        engine, canonical_patch=ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )
    cases = list(ET.parse(report).getroot().iter("testcase"))
    xml = ET.parse(report).getroot()
    if (
        not cases or any(list(xml.iter(tag)) for tag in ("failure", "error", "skipped"))
        or len({(row.get("classname"), row.get("name")) for row in cases}) != len(cases)
        or not proof["canonical_patch"]["matches_worktree"]
        or observed["state_sha256"] != proof["state_sha256"]
    ):
        raise ValueError("engine source tree or CPU evidence changed")
    return {
        "report": str(report.resolve()), "report_sha256": sha256_file(report),
        "sources": str(sources.resolve()), "sources_sha256": sha256_file(sources),
        "tree": observed["canonical_patch"]["actual_tree"],
        "passing_cases": len(cases), "gpu_validation_pending": True,
    }


def observe(arm: Path, *, final=False, expected_capacity=None):
    census = arm / "server/native_capacity_census.json"
    if census.is_file():
        validated_capacity(load(census).get("capacity"))
    if expected_capacity is not None:
        if final and not census.is_file():
            raise ValueError("capture lacks final native capacity census")
        if census.is_file() and load(census).get("capacity") != expected_capacity:
            raise ValueError("capture capacity differs from the reference native")
    path = arm / "server/native_telemetry_status.json"
    if not path.exists():
        if final:
            raise ValueError("capture lacks final native telemetry")
        return
    state = load(path)
    if (
        state.get("replay_token_capture") is not True
        or state.get("writer_error") is not None or state.get("failed_records") != 0
    ):
        raise ValueError("exact token recording is disabled or its writer failed")
    launch = arm / "server/launch_configuration.json"
    if final and not launch.is_file():
        raise ValueError("capture lacks final launch configuration")
    if launch.exists():
        config = load(launch)
        if (
            config.get("native_policy_baseline") is not True
            or config.get("server_random_seed") != 21 or config.get("python_hash_seed") != "21"
        ):
            raise ValueError("capture policy or seed differs from the native source")
    if final and not census.is_file():
        raise ValueError("capture lacks final native capacity census")


def observe_completed_root_delegations(arm: Path, *, profile, checked):
    if profile not in DYNAMIC_NESTED_IN_GRAPH_PROFILES:
        return
    for path in arm.glob("client_*/workflows/*/result.json"):
        if path in checked:
            continue
        try:
            result = load(path)
        except json.JSONDecodeError:
            continue
        rounds = (result.get("trace") or {}).get("root_delegation_round_count")
        if (
            result.get("outcome") != "completed"
            or type(rounds) is not int or rounds < 1
            or (result.get("agent_control") or {}).get("root_delegation_requirement_failures", 0)
        ):
            raise ValueError(f"source root failed its delegation contract: {path.parent.name}")
        checked.add(path)


def balanced_source(summary):
    workflows = summary.get("workflows", [])
    durations = sorted(
        row["duration_seconds"] for row in workflows
        if type(row.get("duration_seconds")) in (int, float)
        and math.isfinite(row["duration_seconds"]) and row["duration_seconds"] > 0
    )
    duration = summary.get("duration_seconds", 0)
    valid_duration = (
        type(duration) in (int, float) and math.isfinite(duration) and duration > 0
    )
    reasons = []
    if (
        len(workflows) != 36 or len(durations) != 36
        or summary.get("completed_workflows") != 36
        or not valid_duration
    ):
        reasons.append("incomplete_workflow_population")
    joins = summary.get("delegation_round_count", 0)
    if type(joins) is not int or joins < 80:
        reasons.append("fewer_than_80_joins")
    if any(row.get("native_agent_jct_eligible") is not True for row in workflows):
        reasons.append("source_guard_or_ineligible_workflow")
    gap = durations[-1] - durations[-2] if len(durations) >= 2 else None
    ratio = durations[-1] / statistics.median(durations) if durations else None
    if gap is None or not valid_duration or gap / duration > .05:
        reasons.append("final_completion_gap_exceeds_five_percent")
    if ratio is None or ratio > 1.8:
        reasons.append("maximum_jct_exceeds_1_8_times_median")
    return {
        "profile": "balanced_join", "accepted": not reasons, "reasons": reasons,
        "workflow_count": len(workflows), "joins": joins,
        "final_completion_gap_seconds": gap, "duration_seconds": duration,
        "maximum_to_median_jct": ratio,
        "criteria": {
            "workflow_count": 36, "minimum_joins": 80, "require_all_source_unguarded": True,
            "maximum_final_gap_fraction": .05, "maximum_to_median_jct": 1.8,
        },
        "scope": "Predeclared source selection only; no Full/native speedup is used.",
    }


def reusable_source(summary, graph, *, expected_workflows=36):
    """Describe real reuse opportunities without selecting for policy speedup."""
    creates = [
        node.event for node in graph.nodes
        if node.event is not None and node.event.kind is Kind.INVOCATION_CREATE
        and node.event.attributes.get("reusable_subagent") is True
    ]
    resumed = {
        (event.workflow_id, event.invocation_id) for event in creates
        if event.attributes.get("resumed") is True
    }
    started = {
        (node.event.workflow_id, node.event.invocation_id) for node in graph.nodes
        if node.event is not None and node.event.kind is Kind.LLM_SUBMIT
        and (node.event.workflow_id, node.event.invocation_id) in resumed
    }
    reasons = []
    workflows = summary.get("workflows", [])
    graph_workflows = {
        node.event.workflow_id for node in graph.nodes
        if node.event is not None and node.event.kind is Kind.WORKFLOW_START
    }
    if (
        type(expected_workflows) is not int or expected_workflows <= 0
        or len(workflows) != expected_workflows
        or summary.get("completed_workflows") != expected_workflows
        or len(graph_workflows) != expected_workflows
        or {row.get("workflow_id") for row in workflows} != graph_workflows
    ):
        reasons.append("incomplete_workflow_population")
    if any(row.get("native_agent_jct_eligible") is not True for row in workflows):
        reasons.append("source_guard_or_ineligible_workflow")
    if summary.get("subagent_fanout_profile") != REUSABLE_NESTED_IN_GRAPH_PROFILE:
        reasons.append("not_the_predeclared_reusable_profile")
    if not started:
        reasons.append("no_real_same_handle_resume")
    return {
        "profile": "reusable_nested", "accepted": not reasons, "reasons": reasons,
        "workflow_count": len(workflows),
        "handles": len({(event.workflow_id, event.attributes["agent_handle"]) for event in creates}),
        "activations": len(creates), "resumes_declared": len(resumed),
        "resumes_started": len(started),
        "joins": graph.summary()["event_counts"].get(Kind.JOIN_CREATE.value, 0),
        "nested_joins": summary.get("nested_delegation_round_count"),
        "root_rounds": summary.get("root_delegation_round_counts"),
        "scope": (
            "First predeclared natural reusable workload. No speedup, fixed JOIN "
            "quota or tail trimming selects this source. Pressure and the amount "
            "of useful reuse require separate capacity and performance evidence."
        ),
    }


def oneshot_source(summary, graph, *, expected_workflows, expected_profile=NESTED_IN_GRAPH_PROFILE):
    """Keep normal tails and report the model's actual delegation decisions."""
    workflows = summary.get("workflows", [])
    started = {
        node.event.workflow_id for node in graph.nodes
        if node.event is not None and node.event.kind is Kind.WORKFLOW_START
    }
    reasons = []
    if (
        len(workflows) != expected_workflows
        or summary.get("completed_workflows") != expected_workflows
        or len(started) != expected_workflows
        or {row.get("workflow_id") for row in workflows} != started
    ):
        reasons.append("incomplete_workflow_population")
    if any(row.get("native_agent_jct_eligible") is not True for row in workflows):
        reasons.append("source_guard_or_ineligible_workflow")
    if summary.get("subagent_fanout_profile") != expected_profile:
        reasons.append("not_the_predeclared_oneshot_profile")
    minimum = (summary.get("delegation_contract") or {}).get(
        "root_minimum_delegations_prompt", 0,
    )
    if type(minimum) is not int or minimum < 0:
        raise ValueError("invalid declared minimum root delegation count")
    minimum_parallel = (summary.get("delegation_contract") or {}).get(
        "minimum_completed_root_join_members", 0,
    )
    if type(minimum_parallel) is not int or minimum_parallel < 0:
        raise ValueError("invalid declared minimum parallel root JOIN")
    root_delegations = None
    root_join_fanout = None
    if minimum or minimum_parallel:
        roots = {
            (node.event.workflow_id, node.event.invocation_id)
            for node in graph.nodes
            if node.event is not None and node.event.kind is Kind.INVOCATION_CREATE
            and node.event.parent_invocation_id is None
            and not node.event.attributes.get("runtime_internal")
        }
        root_delegations = {workflow: 0 for workflow in started}
        for node in graph.nodes:
            event = node.event
            if (
                event is not None and event.kind is Kind.SPAWN
                and (event.workflow_id, event.invocation_id) in roots
                and not event.attributes.get("runtime_internal")
            ):
                root_delegations[event.workflow_id] += 1
        if {workflow for workflow, _invocation in roots} != started:
            reasons.append("root_delegation_evidence_unavailable")
        if any(count < minimum for count in root_delegations.values()):
            reasons.append("root_did_not_delegate")
        if minimum_parallel:
            children, served, returned, satisfied, members = {}, set(), set(), set(), {}
            for node in graph.nodes:
                event = node.event
                if event is None or event.attributes.get("runtime_internal"):
                    continue
                if (
                    event.kind is Kind.INVOCATION_CREATE
                    and event.parent_invocation_id is not None
                ):
                    children[(event.workflow_id, event.invocation_id)] = (
                        event.parent_invocation_id, event.join_id,
                    )
                elif event.kind is Kind.LLM_RESULT:
                    served.add((event.workflow_id, event.invocation_id))
                elif event.kind is Kind.RETURN:
                    returned.add((event.workflow_id, event.invocation_id))
                elif event.kind is Kind.JOIN_SATISFIED:
                    satisfied.add((event.workflow_id, event.join_id))
                elif event.kind is Kind.JOIN_CREATE:
                    members[(event.workflow_id, event.join_id)] = event.member_invocation_ids
            root_join_fanout = {workflow: 0 for workflow in started}
            for (workflow, join), member_ids in members.items():
                if (workflow, join) not in satisfied:
                    continue
                parent_ids = {
                    children.get((workflow, child), (None, None))
                    for child in member_ids
                }
                if len(parent_ids) != 1:
                    continue
                parent, declared_join = next(iter(parent_ids))
                if (workflow, parent) not in roots or declared_join != join:
                    continue
                actual = sum(
                    (workflow, child) in served and (workflow, child) in returned
                    for child in member_ids
                )
                root_join_fanout[workflow] = max(root_join_fanout[workflow], actual)
            if any(count < minimum_parallel for count in root_join_fanout.values()):
                reasons.append("root_parallel_join_requirement_unmet")
    return {
        "profile": "nested_oneshot", "accepted": not reasons, "reasons": reasons,
        "workflow_count": len(workflows),
        "joins": graph.summary()["event_counts"].get(Kind.JOIN_CREATE.value, 0),
        "nested_joins": summary.get("nested_delegation_round_count"),
        "root_rounds": summary.get("root_delegation_round_counts"),
        "minimum_root_delegations": minimum,
        "observed_root_delegations": root_delegations,
        "minimum_completed_root_join_members": minimum_parallel,
        "observed_completed_root_join_max_members": root_join_fanout,
        "root_delegation_return_deferrals": sum(
            (row.get("agent_control") or {}).get("root_delegation_return_deferrals", 0)
            for row in workflows
        ),
        "scope": (
            "Model-directed one-shot delegation with its declared minimum, if present; "
            "return deferrals are reported separately. No reuse quota, additional JOIN-count target, "
            "normal-tail filter or measured policy speedup selects this source."
        ),
    }


def _declared_reservation_wait(plan):
    if not plan.get("hold_file"):
        return False
    hold = load(Path(plan["hold_file"]))
    spec_path = hold.get("reservation_guard_spec")
    if not spec_path or hold.get("automatic_resume_allowed") is not True:
        return False
    spec = load(Path(spec_path))
    begin, end = (datetime.fromisoformat(spec[name]) for name in ("begin", "end"))
    drain = datetime.fromisoformat(spec.get("drain_begin", spec["begin"]))
    if (
        begin.tzinfo is None or end.tzinfo is None or drain.tzinfo is None
        or not drain <= begin < end
        or Path(spec["hold_file"]).resolve() != Path(plan["hold_file"]).resolve()
        or Path(spec["pause_file"]).resolve() != fresh.PAUSE.resolve()
        or hold.get("reservation_begin") != spec["begin"]
        or hold.get("reservation_end") != spec["end"]
        or fresh.deadline(hold) > end
    ):
        raise fresh.HoldChanged("declared GPU reservation metadata changed")
    pause = Path(spec["pause_file"])
    own_pause = pause.is_file() and pause.read_text() == spec["pause_marker"]
    if pause.exists() and not own_pause:
        return False
    at = reservation.now()
    # A declared window also blocks launch before its guard publishes PAUSE.
    # After the deadline, wait for that guard to remove only its own marker.
    return at >= drain and (at < end or own_pause)


def queue_launch_allowed(plan):
    try:
        fresh.check_hold(plan, released=True)
    except fresh.HoldChanged:
        if plan.get("wait_for_gpu") and _declared_reservation_wait(plan):
            return False
        raise
    if _declared_reservation_wait(plan):
        if plan.get("wait_for_gpu"):
            return False
        raise fresh.HoldChanged("GPU launch is inside the declared user reservation")
    return True


def wait_for_resources(plan, state, path, interrupted):
    while True:
        if interrupted():
            raise ValueError("capture cancelled while waiting for the GPU")
        if not queue_launch_allowed(plan):
            state.update(
                status="waiting_user_gpu_reservation",
                resources={"ready": False, "reason": "declared_user_gpu_reservation"},
                checked_at=reservation.now().isoformat(),
            )
            reservation.write_json(path, state)
            time.sleep(10.)
            continue
        previous_alive = False
        if plan.get("after_comparison"):
            previous = load(Path(plan["after_comparison"]).with_suffix(".state.json"))
            identities = [
                *previous.get("owned", []),
                *(previous[name] for name in ("watcher", "server", "driver") if previous.get(name)),
            ]
            if not previous.get("watcher"):
                raise ValueError("previous comparison lacks its supervisor identity")
            previous_alive = any(reservation.alive(row) for row in identities)
        available = (
            {"ready": False, "reason": "previous_comparison_has_live_processes"}
            if previous_alive else qualified.resources(plan["port"])
        )
        if available["ready"]:
            state.update(resources=available, checked_at=reservation.now().isoformat())
            reservation.write_json(path, state)
            return
        if not plan.get("wait_for_gpu"):
            raise ValueError("resources changed before capture")
        state.update(
            status="waiting_gpu_capture", resources=available,
            checked_at=reservation.now().isoformat(),
        )
        reservation.write_json(path, state)
        time.sleep(10.)


def prepare_capture(plan, state, path, interrupted):
    sampling_checked = False
    while True:
        wait_for_resources(plan, state, path, interrupted)
        comparison.verify_sources(plan)
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/preflight_qwen35_host_pool.py"),
             "--memory-policy", "preferred", "--node", "1", "--size-gb", "200"],
            check=True, capture_output=True, timeout=30,
        )
        if interrupted():
            raise ValueError("capture cancelled during preflight")
        if not queue_launch_allowed(plan):
            continue
        if qualified.resources(plan["port"])["ready"]:
            if plan.get("cuda_toolchain") is not None and not sampling_checked:
                output = Path(plan["sampling_preflight_path"])
                try:
                    subprocess.run(
                        [sys.executable, str(ROOT / "scripts/preflight_cuda_sampling.py"),
                         "--output", str(output), "--engine", plan["engine_checkout"]],
                        env={
                            **os.environ, "CUDA_VISIBLE_DEVICES": "0",
                            "CUDA_HOME": plan["cuda_toolchain"]["home"],
                            "FLASHINFER_WORKSPACE_BASE": os.environ.get(
                                "SGLANG_CACHE_DIR", str(Path.home() / ".cache/sglang"),
                            ),
                            "PYTHONPATH": os.pathsep.join((
                                str(Path(plan["engine_checkout"]) / "python"), str(ROOT),
                            )),
                        },
                        check=True, capture_output=True, text=True, timeout=180,
                    )
                except subprocess.CalledProcessError as error:
                    failure = output.with_suffix(".error.json")
                    reservation.write_json(failure, {
                        "returncode": error.returncode,
                        "stdout": error.stdout, "stderr": error.stderr,
                    })
                    raise ValueError(f"sampler preflight failed; see {failure}") from error
                result = load(output)
                if (
                    result.get("gpu_sampling_verified") is not True
                    or result.get("native_kernel_verified") is not True
                    or result.get("cuda_home") != plan["cuda_toolchain"]["home"]
                ):
                    raise ValueError("native sampler preflight did not qualify")
                state["sampling_kernel_preflight"] = {
                    "path": str(output), "sha256": sha256_file(output), **result,
                }
                reservation.write_json(path, state)
                sampling_checked = True
                if interrupted():
                    raise ValueError("capture cancelled during sampler preflight")
                if not queue_launch_allowed(plan) or not qualified.resources(plan["port"])["ready"]:
                    continue
            return
        if not plan.get("wait_for_gpu"):
            raise ValueError("resources changed during capture preflight")


def register(args):
    for path in (
        args.registration, args.registration.with_suffix(".state.json"),
        args.registration.with_suffix(".log"), args.run_root,
    ):
        if path.exists():
            raise FileExistsError("preserve existing capture registration and artifacts")
    if not 1 <= args.port <= 65535:
        raise ValueError("invalid capture port")
    reference = load(args.reference)
    fresh.verify(reference)
    reference_complete(reference, load(args.reference.with_suffix(".state.json")))
    experiment_path = getattr(args, "experiment_config", None)
    experiment = load(experiment_path) if experiment_path else None
    full_only = experiment is not None and experiment["common_environment"].get("MODEL_CACHE_LAYOUT") == "full_only"
    capacity_path, capacity = (None, None) if full_only else reference_capacity(reference)
    cuda_home = getattr(args, "cuda_home", None)
    toolchain = cuda_toolchain_evidence(cuda_home) if cuda_home is not None else None
    if getattr(args, "sampling_profile", None) in ("qwen35_coding", "qwen3_coder") and toolchain is None:
        raise ValueError("coding sampling needs an explicit matched CUDA toolchain")
    queue_launch_allowed({**reference, "wait_for_gpu": args.wait_for_gpu})
    count = comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = load(args.cpu_sources)
    if not {
        "scripts/run_causal_source_capture.py", "tests/test_causal_source_capture.py",
        "beliefkv/runtime/replay_token_capture.py",
        "beliefkv/experiments/causal_replay.py",
    } <= sources.keys():
        raise ValueError("capture execution was not CPU-qualified")
    if toolchain is not None and "scripts/preflight_cuda_sampling.py" not in sources:
        raise ValueError("sampling preflight source was not CPU-qualified")
    fresh._require_clean(ROOT)
    evidence = engine_evidence(args.engine, args.engine_report, args.engine_sources)
    revision = qualified.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    label = args.run_root.name.rsplit("_", 1)[-1]
    checkout = comparison.EXPERIMENTS / ".worktrees" / f"beliefkv-{label}-capture-{revision[:12]}"
    engine = comparison.EXPERIMENTS / ".worktrees" / f"sglang-{label}-capture-{evidence['tree'][:12]}"
    if checkout.exists() or engine.exists():
        raise FileExistsError("preserve existing frozen capture sources")
    if not args.wait_for_gpu and not qualified.resources(args.port)["ready"]:
        raise ValueError("GPU, port or disk is occupied")
    workload_path = (
        args.source_workload.resolve() if args.source_workload
        else Path(reference["arms"]["native_matched"]["WORKLOAD_MANIFEST"])
    )
    workload_inputs = declared_workload_inputs(
        workload_path, require_tool_execution=True,
    )
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(checkout), revision],
                   check=True, capture_output=True)
    comparison.verify_cpu_snapshot(checkout, sources)
    base = qualified.command(["git", "-C", str(args.engine), "rev-parse", "HEAD"])
    subprocess.run(["git", "-C", str(args.engine), "worktree", "add", "--detach", str(engine), base],
                   check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(engine), "apply",
        str(checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
    ], check=True, capture_output=True)
    tree = git_source_state(
        engine, canonical_patch=checkout / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if not tree["matches_worktree"] or tree["actual_tree"] != evidence["tree"]:
        raise ValueError("frozen engine differs from the tested capture engine")
    env = environment(
        reference, checkout, engine, args.run_root, args.port,
        source_profile=args.source_profile, capacity=capacity, source_workload=args.source_workload,
        max_running_requests=args.max_running_requests,
        sampling_profile=getattr(args, "sampling_profile", None),
        cuda_home=cuda_home,
        experiment=experiment,
    )
    extra = [
        args.reference, args.reference.with_suffix(".state.json"),
        args.cpu_report, args.cpu_sources, args.engine_report, args.engine_sources,
        *([capacity_path] if capacity_path else []), *workload_inputs,
    ]
    if experiment_path:
        extra.append(experiment_path)
    if full_only:
        model = Path(env["MODEL_PATH"])
        extra.extend(model / name for name in (
            "config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
            "model.safetensors.index.json",
        ))
    if args.after_comparison:
        extra.append(args.after_comparison)
    plan = {
        "schema_version": 1, "purpose": "predeclared_native_exact_trajectory_capture",
        "registered_at": reservation.now().isoformat(),
        "reference_registration": str(args.reference.resolve()),
        "code_commit": revision, "development_checkout": str(ROOT),
        "execution_checkout": str(checkout), "engine_checkout": str(engine),
        "engine_tree": tree["actual_tree"], "source_sha256": sources,
        "passing_cpu_cases": count, "engine_evidence": evidence,
        "environment": env, "run_root": str(args.run_root.resolve()), "port": args.port,
        "capture_capacity": capacity,
        "cuda_toolchain": toolchain,
        "sampling_preflight_path": (
            str(args.registration.with_suffix(".sampling.json").resolve())
            if toolchain is not None else None
        ),
        "hold_file": reference["hold_file"],
        "external_inputs": [
            *reference["external_inputs"],
            *({"path": str(path.resolve()), "sha256": sha256_file(path)} for path in extra),
            *(toolchain["inputs"] if toolchain is not None else []),
        ],
        "performance_accepted": False, "gpu_replay_qualified": False,
        "source_profile": args.source_profile,
        "source_workload": (
            str(args.source_workload.resolve()) if args.source_workload else None
        ),
        "wait_for_gpu": args.wait_for_gpu,
        "after_comparison": str(args.after_comparison.resolve()) if args.after_comparison else None,
    }
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(checkout / "scripts/run_causal_source_capture.py"),
             "--watch", str(args.registration.resolve())],
            cwd=checkout, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({
        "registration": str(args.registration), "watcher": reservation.process(watcher.pid),
        "purpose": plan["purpose"], "performance_accepted": False,
    }, indent=2))


def watch(registration: Path):
    plan = load(registration)
    path = registration.with_suffix(".state.json")
    if path.exists():
        raise FileExistsError("preserve the previous capture monitor outcome")
    run = Path(plan["run_root"])
    arm = run / "native_capture"
    roots = int(plan["environment"].get("ROOT_COUNT", "36"))
    if not 1 <= roots <= 156:
        raise ValueError("capture workflow population is invalid")
    summary_path = arm / f"client_{roots}/summary.json"
    state = {
        "status": "capture_preflight", "watcher": reservation.process(os.getpid()),
        "current_arm": "native_capture", "performance_accepted": False,
        "gpu_replay_qualified": False, "run_root": str(run),
        "reservation_aware_queue": plan.get("wait_for_gpu") is True,
    }
    driver = None
    interrupted = False
    checked_root_results = set()

    def stop_requested(*_):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    with registration.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            state["checked_at"] = reservation.now().isoformat()
            reservation.write_json(path, state)
            comparison.verify_sources(plan)
            prepare_capture(plan, state, path, lambda: interrupted)
            qualified.machine_helper("start_v88_after_0600_20261020").clear_stale_socket(plan["port"])
            run.mkdir(parents=True, exist_ok=False)
            reservation.write_json(run / "capture_plan.json", plan)
            with (run / "native_capture.launch.log").open("x") as output:
                driver = subprocess.Popen(
                    ["bash", str(ROOT / "scripts/run_qwen35_native_regime_probe.sh")],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True,
                    env=qualified.driver_environment(plan, os.environ),
                )
            state.update(status="recording_native", driver=reservation.process(driver.pid))
            while driver.poll() is None:
                state["checked_at"] = reservation.now().isoformat()
                reservation.write_json(path, state)
                fresh.check_hold(plan, released=True)
                if interrupted:
                    raise ValueError("capture monitor was stopped")
                observe(arm, expected_capacity=plan.get("capture_capacity"))
                observe_completed_root_delegations(
                    arm, profile=plan["environment"].get("FANOUT_PROFILE"),
                    checked=checked_root_results,
                )
                time.sleep(15)
            state["original_exit_code"] = driver.wait()
            observe(arm, final=True, expected_capacity=plan.get("capture_capacity"))
            state["outcome"] = fresh.completed_arm(
                load(summary_path), state["original_exit_code"], roots,
            )
            token_report, graph_report = audit_tokens(arm), audit_graph(arm)
            reservation.write_json(run / "token_evidence.json", token_report)
            reservation.write_json(run / "causal_evidence.json", graph_report)
            if not token_report["token_evidence_complete"] or not graph_report["causal_graph_compiled"]:
                raise ValueError("captured token or dependency evidence is incomplete")
            if (
                plan["environment"].get("SAMPLING_PROFILE") in ("qwen35_coding", "qwen3_coder")
                and token_report.get("original_generation_parameters_complete") is not True
            ):
                raise ValueError("coding source lacks original native sampler parameters")
            state.update(
                status="source_captured_gpu_replay_pending",
                source_requests=token_report["native_requests"],
            )
            if plan.get("source_profile") == "balanced_join":
                selection = balanced_source(load(summary_path))
                reservation.write_json(run / "source_selection.json", selection)
                state["source_selection"] = selection
                if not selection["accepted"]:
                    state["status"] = "source_captured_selection_rejected"
            elif plan.get("source_profile") == "reusable_nested":
                from scripts.run_causal_gpu_replay import source_plan

                graph, _, _ = source_plan(arm)
                selection = reusable_source(
                    load(summary_path), graph, expected_workflows=roots,
                )
                reservation.write_json(run / "source_selection.json", selection)
                state["source_selection"] = selection
                if not selection["accepted"]:
                    state["status"] = "source_captured_selection_rejected"
            elif plan.get("source_profile") == "nested_oneshot":
                from scripts.run_causal_gpu_replay import source_plan

                graph, _, _ = source_plan(arm)
                selection = oneshot_source(
                    load(summary_path), graph, expected_workflows=roots,
                    expected_profile=plan["environment"]["FANOUT_PROFILE"],
                )
                reservation.write_json(run / "source_selection.json", selection)
                state["source_selection"] = selection
                if not selection["accepted"]:
                    state["status"] = "source_captured_selection_rejected"
        except Exception as error:
            administrative = interrupted or isinstance(error, fresh.HoldChanged)
            state["administratively_interrupted"] = administrative
            if driver is not None and driver.poll() is None:
                identities = reservation.owned_runs().get(run, [])
                if not any(item["pid"] == state["driver"]["pid"] for item in identities):
                    identities.append(state["driver"])
                arm.mkdir(parents=True, exist_ok=True)
                reservation.write_json(arm / "server_reservation_cutoff.json", {
                    "reason": "causal_source_capture_interrupted",
                    "stop_started_at": reservation.now().isoformat(), "run": str(arm),
                    "observation_cutoff_monotonic_ms": time.monotonic() * 1000.,
                    "identities": identities, "collection_at_cutoff": {},
                    "administratively_interrupted": administrative, "errors": [],
                })
                reservation.stop_run(arm, identities, state)
                state["stopped_driver_exit_code"] = driver.wait(timeout=60)
            state.update(
                status="capture_interrupted" if administrative else "capture_failed",
                error=repr(error),
            )
        finally:
            state["finished_at"] = reservation.now().isoformat()
            reservation.write_json(path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--cpu-sources", type=Path)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--engine-report", type=Path)
    parser.add_argument("--engine-sources", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--port", type=int, default=18546)
    parser.add_argument(
        "--source-profile", choices=("balanced_join", "reusable_nested", "nested_oneshot"),
    )
    parser.add_argument("--source-workload", type=Path)
    parser.add_argument("--max-running-requests", type=int)
    parser.add_argument("--sampling-profile", choices=("legacy_greedy", "qwen35_coding", "qwen3_coder"))
    parser.add_argument("--experiment-config", type=Path)
    parser.add_argument("--cuda-home", type=Path)
    parser.add_argument("--wait-for-gpu", action="store_true")
    parser.add_argument("--after-comparison", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch.resolve())
    elif all((args.reference, args.cpu_report, args.cpu_sources, args.engine,
              args.engine_report, args.engine_sources, args.registration, args.run_root)):
        register(args)
    else:
        parser.error("register requires the reference, source checks and new output paths")


if __name__ == "__main__":
    main()
