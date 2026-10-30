"""Compose independently verified whole workflows without joining raw clocks."""

from collections import Counter
from graphlib import TopologicalSorter
import json
import math
from pathlib import Path

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import CausalReplayPlan, ReplayNode
from beliefkv.experiments.source_provenance import sha256_file


KIND = "composed_complete_original_workflows"
SERVING_FIELDS = (
    "model_path", "dtype", "kv_cache_dtype", "mem_fraction_static", "context_length",
    "max_running_requests", "chunked_prefill_size", "page_size", "mamba_ssm_dtype",
    "mamba_full_memory_ratio", "mamba_radix_cache_strategy", "mamba_track_interval",
    "mamba_max_states_per_path", "hicache_size", "hicache_mem_layout",
    "hicache_io_backend", "hicache_host_memory_mode", "hicache_write_policy",
    "disable_overlap_schedule", "enable_mixed_chunk", "radix_cache_backend",
    "radix_eviction_policy", "schedule_policy", "enable_beliefkv_admission",
)


def registered_source_arm(source):
    if source.get("source_kind") == KIND:
        return Path(source["source_arm"])
    return Path(source["run_root"]) / "native_capture"


def composed_quality(arm):
    declaration = json.loads((Path(arm) / "composite_source.json").read_bytes())
    rows = declaration["selected_workflows"]
    return {
        "source_kind": KIND, "workflow_count": len(rows), "completed_workflows": len(rows),
        "native_agent_jct_eligible_workflows": len(rows),
        "workflows": [{
            "workflow_id": row["workflow_id"], "instance_id": row["task_id"],
            "native_agent_jct_eligible": row["eligible"],
        } for row in rows],
        "delegation_round_count": sum(row["joins"] for row in rows),
        "matched_natural_native_baseline_available": False,
    }


def verify_composed_registration(source):
    arm = registered_source_arm(source)
    if (
        source.get("source_kind") != KIND
        or source.get("composition_sha256") != sha256_file(arm / "composite_source.json")
        or source.get("matched_natural_native_baseline_available") is not False
    ):
        raise ValueError("composed registration changed or claims an unavailable native result")
    plan, expected, evidence = load_composed_source(arm)
    environment = source["environment"]
    if (
        environment["ROOT_COUNT"] != str(plan.summary()["workflows"])
        or sha256_file(Path(environment["WORKLOAD_MANIFEST"]))
        != sha256_file(arm / "workload_manifest.json")
        or source["engine_tree"] != json.loads((arm / "serving_source.json").read_bytes())["engine_tree"]
    ):
        raise ValueError("composed registration changes the model, workload or native source")
    return plan, expected, evidence


def merge_plans(plans):
    """Each input was independently compiled in its original clock domain."""
    nodes, usage, workflows = {}, {}, set()
    omitted = 0
    for plan in plans:
        if not plan.native_completion_boundaries_verified:
            raise ValueError("composition requires original native completion boundaries")
        selected = {node.workflow_id for node in plan.nodes}
        if not selected or selected & workflows:
            raise ValueError("workflow identities must be distinct across sources")
        workflows.update(selected)
        if set(plan.request_usage) & usage.keys():
            raise ValueError("request identity collision across sources")
        usage.update(plan.request_usage)
        for node in plan.nodes:
            if node.node_id in nodes:
                raise ValueError("causal node identity collision across sources")
            nodes[node.node_id] = node
        omitted += plan.omitted_diagnostic_events
    result = CausalReplayPlan(tuple(nodes.values()), usage, True, omitted)
    validate_plan(result)
    return result


def validate_plan(plan):
    nodes = {node.node_id: node for node in plan.nodes}
    workflows = {node.workflow_id for node in plan.nodes}
    if not nodes or len(nodes) != len(plan.nodes):
        raise ValueError("composite plan requires unique causal nodes")
    boundaries = Counter()
    requests = set()
    for node in plan.nodes:
        if not math.isfinite(node.delay_ms) or node.delay_ms < 0:
            raise ValueError("invalid composed causal delay")
        if any(dep not in nodes or nodes[dep].workflow_id != node.workflow_id
               for dep in node.dependencies):
            raise ValueError("whole workflows cannot have foreign or missing dependencies")
        if node.event is not None:
            if node.request_id is not None or node.event.workflow_id != node.workflow_id:
                raise ValueError("composed event changes workflow ownership")
            if node.event.kind in (Kind.WORKFLOW_START, Kind.WORKFLOW_END):
                boundaries[node.workflow_id, node.event.kind] += 1
        elif node.request_id is None or node.request_id in requests:
            raise ValueError("composed request identities must be complete and unique")
        else:
            requests.add(node.request_id)
    if (
        requests != set(plan.request_usage)
        or any(boundaries[workflow, kind] != 1 for workflow in workflows
               for kind in (Kind.WORKFLOW_START, Kind.WORKFLOW_END))
    ):
        raise ValueError("composition must retain each complete original workflow")
    tuple(TopologicalSorter(plan.dependency_graph()).static_order())


def plan_dict(plan):
    validate_plan(plan)
    return {
        "nodes": [{
            "node_id": node.node_id, "workflow_id": node.workflow_id,
            "dependencies": list(node.dependencies), "delay_ms": node.delay_ms,
            "event": node.event.to_dict() if node.event is not None else None,
            "request_id": node.request_id,
        } for node in plan.nodes],
        "request_usage": dict(plan.request_usage),
        "native_completion_boundaries_verified": plan.native_completion_boundaries_verified,
        "omitted_diagnostic_events": plan.omitted_diagnostic_events,
    }


def load_composed_source(arm):
    arm = Path(arm)
    receipt = arm / "composite_source.json"
    declaration = json.loads(receipt.read_bytes())
    if (
        declaration.get("schema_version") != 1 or declaration.get("kind") != KIND
        or declaration.get("external_workflow_arrivals_required") is not True
        or declaration.get("matched_natural_native_baseline_available") is not False
        or not {
            "plan.json", "expected.json", "server/request_tokens.jsonl", "token_evidence.json",
        } <= declaration.get("artifacts_sha256", {}).keys()
    ):
        raise ValueError("unsupported composed source contract")
    for name, digest in declaration["artifacts_sha256"].items():
        path = (arm / name).resolve()
        if not path.is_relative_to(arm.resolve()) or sha256_file(path) != digest:
            raise ValueError("composed source artifact changed: " + name)
    for name, digest in declaration["original_inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("original composed source input changed: " + name)
    data = json.loads((arm / "plan.json").read_bytes())
    plan = CausalReplayPlan(
        tuple(ReplayNode(
            row["node_id"], row["workflow_id"], tuple(row["dependencies"]), row["delay_ms"],
            RuntimeEvent.from_dict(row["event"]) if row["event"] is not None else None,
            row["request_id"],
        ) for row in data["nodes"]),
        {request: tuple(counts) for request, counts in data["request_usage"].items()},
        data["native_completion_boundaries_verified"], data["omitted_diagnostic_events"],
    )
    validate_plan(plan)
    if plan.native_completion_boundaries_verified is not True:
        raise ValueError("composed plan lacks original native completion boundaries")
    expected = {
        request: {kind: (tuple(value[0]), value[1]) for kind, value in boundaries.items()}
        for request, boundaries in json.loads((arm / "expected.json").read_bytes()).items()
    }
    selected = declaration["selected_workflows"]
    if (
        len({row["project"] for row in selected}) != len(selected)
        or len({row["workflow_id"] for row in selected}) != len(selected)
        or {node.workflow_id for node in plan.nodes} != {row["workflow_id"] for row in selected}
        or set(expected) != set(plan.request_usage)
        or plan.summary() != declaration["plan_summary"]
        or any(
            set(expected[request]) != {"llm_submit", "llm_result"}
            or expected[request]["llm_submit"][0] != expected[request]["llm_result"][0]
            or tuple(expected[request][kind][1] for kind in ("llm_submit", "llm_result")) != counts
            for request, counts in plan.request_usage.items()
        )
    ):
        raise ValueError("composed source changes the selected complete population")
    return plan, expected, {
        "source_kind": KIND, "source_arm": str(arm.resolve()),
        "composite_source_sha256": sha256_file(receipt),
        "original_inputs_sha256": declaration["original_inputs_sha256"],
        "selection": selected,
        "independent_clock_domains_compiled_separately": True,
        "external_workflow_arrivals_required": True,
        "matched_natural_native_baseline_available": False,
        "independent_task_correctness_verified": False,
    }
