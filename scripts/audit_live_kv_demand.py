"""Compare live logical token-prefix unions with the combined physical KV ceiling."""

import argparse
from collections import defaultdict
from graphlib import TopologicalSorter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def prefix_union(rows):
    groups = defaultdict(list)
    for namespace, unit, data in rows:
        if unit <= 0 or len(data) % unit:
            raise ValueError("token-prefix storage must contain complete fixed-width IDs")
        groups[namespace, unit].append(data)
    total = 0
    for (_namespace, unit), values in groups.items():
        previous = b""
        for current in sorted(values):
            common, limit = 0, min(len(previous), len(current))
            while common < limit and previous[common] == current[common]:
                common += 1
            total += len(current) // unit - common // unit
            previous = current
    return total


def reusable_checkpoint(checkpoint, following_input):
    """Future-only diagnostic: retain the old prefix needed by the next call."""
    namespace, unit, data = checkpoint
    if following_input is None:
        return namespace, unit, b""
    other_namespace, other_unit, following = following_input
    if unit != other_unit:
        raise ValueError("source token representation changed within one context")
    if namespace != other_namespace:
        return namespace, unit, b""
    common, limit = 0, min(len(data), len(following))
    while common < limit and data[common] == following[common]:
        common += 1
    return namespace, unit, data[:common // unit * unit]


def next_context_requests(graph):
    nodes = {node.node_id: node for node in graph.nodes}
    previous, following = {}, {}
    for identity in TopologicalSorter(graph.dependency_graph()).static_order():
        event = nodes[identity].event
        if event is None or event.kind.value != "llm_result":
            continue
        context = event.workflow_id, event.invocation_id, event.context_id
        request = event.attributes["request_id"]
        if context in previous:
            following[previous[context]] = request
        previous[context] = request
    return following


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier live-state demand observations")
    registration = json.loads(args.registration.read_bytes())
    source = Path(registration["source_arm"])
    graph, expected, evidence = source_plan(source)
    following = next_context_requests(graph)
    arm, reader = Path(registration["run_root"]) / "full", SealedRecords()
    events = sorted(reader(arm / "replay/runtime_events.replay.jsonl"), key=lambda row: row["ts_ms"])
    states = [row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
              if row.get("event") == "admission_runtime_state"]
    if not events or not states:
        raise ValueError("demand audit requires observed events and clock calibration")
    offset = states[0]["ts_ms"] - states[0]["monotonic_ms"]
    probes = [row for row in reader(arm / "server/eviction_attribution.jsonl")
              if row.get("event") == "context_prefix_reuse_probe"
              and row.get("previously_served_prefix_recompute_proxy_tokens", 0) > 0]
    points = [(row["ts_ms"] - offset, row["request_id"]) for row in probes]
    points.append((events[-1]["ts_ms"], None))
    capacity = json.loads((source / "server/native_capacity_census.json").read_bytes())["capacity"]
    ceiling = capacity["device_full_tokens"] + capacity["host_full_tokens"]
    live, ended, served, pending = {}, set(), {}, {}
    cursor, observations = 0, []
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        encoded = {}

        def sequence(request, include_output):
            cache_key = request, include_output
            if cache_key not in encoded:
                value = corpus.get(request)
                tokens = value.prompt_tokens + value.output_tokens if include_output else value.prompt_tokens
                encoded[cache_key] = (
                    json.dumps([value.extra_key, value.cache_salt], sort_keys=True),
                    tokens.itemsize, tokens.tobytes(),
                )
            return encoded[cache_key]

        for when, request in sorted(points):
            if when > events[-1]["ts_ms"]:
                continue
            while cursor < len(events) and events[cursor]["ts_ms"] <= when:
                event = events[cursor]
                cursor += 1
                kind, invocation = event["kind"], event.get("invocation_id")
                if kind == "invocation_create":
                    live[invocation] = event["workflow_id"]
                elif kind in ("return", "invocation_cancel"):
                    live.pop(invocation, None)
                elif kind == "workflow_end":
                    ended.add(event["workflow_id"])
                elif kind in ("llm_submit", "llm_result"):
                    context = event["workflow_id"], invocation, event["context_id"]
                    rid = event["attributes"]["request_id"]
                    if kind == "llm_submit":
                        pending[context] = rid
                    else:
                        served[context] = rid
                        pending.pop(context, None)
            contexts = {
                key for key in served.keys() | pending.keys()
                if key[1] in live and key[0] not in ended
            }
            checkpoints = [sequence(served[key], True) for key in contexts if key in served]
            reusable = [
                reusable_checkpoint(
                    sequence(served[key], True),
                    sequence(following[served[key]], False) if served[key] in following else None,
                )
                for key in contexts if key in served
            ]
            demands = [
                sequence(pending[key], False) if key in pending else sequence(served[key], True)
                for key in contexts
            ]
            checkpoint_tokens, demand_tokens = prefix_union(checkpoints), prefix_union(demands)
            reusable_tokens = prefix_union(reusable)
            observations.append({
                "monotonic_ms": when, "recompute_request_id": request,
                "live_workflows": len({key[0] for key in contexts}),
                "live_contexts": len(contexts), "contexts_with_completed_model_state": len(checkpoints),
                "pending_first_model_contexts": sum(key not in served for key in contexts),
                "latest_served_prefix_union_tokens": checkpoint_tokens,
                "pending_input_or_latest_served_union_tokens": demand_tokens,
                "latest_served_union_fraction_of_combined_ceiling": checkpoint_tokens / ceiling,
                "pending_demand_union_fraction_of_combined_ceiling": demand_tokens / ceiling,
                "next_request_reusable_prior_prefix_union_tokens": reusable_tokens,
                "next_request_reusable_prior_union_fraction_of_combined_ceiling": reusable_tokens / ceiling,
            })
            # Only a small set of checkpoints is needed at each observation.
            if len(encoded) > 1024:
                encoded.clear()
    write(args.output, {
        "combined_FULL_capacity_tokens": ceiling, "observations": observations,
        "source_evidence": evidence, "input_jsonl_prefixes": reader.prefixes,
        "scope": {
            "latest_live_invocation_contexts_only": True,
            "temporary_returned_children_and_ended_workflows_excluded": True,
            "shared_identical_token_prefixes_counted_once_per_native_namespace": True,
            "EOS_and_small_growth_differences_not_physical_page_accounting": True,
            "completed_state_can_include_obsolete_suffix_after_compaction": True,
            "next_request_reusable_prior_prefix_uses_private_future_for_CPU_diagnosis_only": True,
            "reusable_prior_prefix_excludes_no_future_model_call_and_compacted_suffix": True,
            "reuse_cap_is_not_a_deployable_retention_rule": True,
            "live_uncompleted_input_growth_is_not_in_reusable_prior_prefix": True,
            "pending_input_can_include_uncomputed_first_prefill": True,
            "does_not_prove_native_allocation_or_copy_overlap_feasibility": True,
            "occupancy_or_Host_duplicate_cause_not_identified_by_this_bound_alone": True,
            "GPU_action": False, "performance_accepted": False,
        },
    })
    print(json.dumps({"combined_tokens": ceiling, "points": len(observations),
                      "latest": observations[-1] if observations else None}))


if __name__ == "__main__":
    main()
