"""Read checkpoint roles after generation without reading tensor contents."""

from __future__ import annotations

import time
from typing import Any

from beliefkv.runtime.sglang_v0520_observer import normalize_native_creation_time


def generation_prefix_compatibility(
    prior_input: Any, prior_output: Any, current_input: Any,
    input_common: int, common_prefix: Any, path: dict[str, Any],
) -> dict[str, Any]:
    """Compare an observed transcript with the next actual same-scope input."""
    generated_common = 0
    if input_common == len(prior_input):
        start = len(prior_input)
        generated_common = common_prefix(
            prior_output, current_input[start:start + len(prior_output)],
        )
    common = input_common + generated_common
    prior_length = len(prior_input) + len(prior_output)
    checkpoints = []
    observed = path.get("status") == "observed"
    if observed:
        for state in path.get("mamba_states", ()):
            prefix = state["prefix_tokens"]
            compatibility = (
                "outside_observed_transcript" if prefix > prior_length else
                "exact_prefix_compatible" if prefix <= common else
                "new_input_ends_before_checkpoint" if prefix > len(current_input) else
                "token_prefix_diverges"
            )
            checkpoints.append({
                key: state[key] for key in (
                    "node_id", "creation_time", "prefix_tokens", "role",
                )
            } | {"prefix_compatibility": compatibility})
    return {
        "prior_input_tokens": len(prior_input),
        "prior_generated_output_tokens": len(prior_output),
        "current_input_tokens": len(current_input),
        "common_prior_input_prefix_tokens": input_common,
        "common_generated_output_prefix_tokens": generated_common,
        "common_prior_transcript_prefix_tokens": common,
        "checkpoint_path_observed": observed,
        "checkpoint_compatibility": checkpoints,
        "semantics": (
            "Exact token-prefix compatibility with the next observed same-session input. "
            "Not cache residency, forward consumption, physical-copy reuse or safe global "
            "discard permission; token mismatch does not identify its formatting cause."
        ),
    }


def checkpoint_path_evidence(req: Any, cache: Any, *, max_nodes: int = 256) -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter_ns(), time.thread_time_ns()
    result: dict[str, Any]
    try:
        tree = cache.tree_core
        root, node = tree.root_node, tree.node_by_id(req.last_node)
        path, visited = [], set()
        while node is not root:
            if node is None:
                raise ValueError("detached_path")
            if len(path) >= max_nodes:
                raise ValueError("path_limit")
            if type(node.id) is not int or node.id < 0 or node.id in visited:
                raise ValueError("invalid_or_cyclic_path")
            if tree.node_by_id(node.id) is not node:
                raise ValueError("stale_node_identity")
            visited.add(node.id)
            path.append(node)
            node = node.parent
        input_tokens, output_tokens = len(req.origin_input_ids), len(req.output_ids)
        prefix, states = 0, []
        for node in reversed(path):
            prefix += len(node.key)
            state = node.component_data[2]
            if state.value is None and state.host_value is None:
                continue
            references = {
                "session_ref": state.session_ref, "device_lock_ref": state.lock_ref,
                "host_lock_ref": state.host_lock_ref,
            }
            if any(type(value) is not int or value < 0 for value in references.values()):
                raise ValueError("invalid_reference_counts")
            states.append({
                "node_id": node.id,
                "creation_time": normalize_native_creation_time(node.creation_time),
                "prefix_tokens": prefix,
                "role": (
                    "input_prefix" if prefix < input_tokens else
                    "input_boundary" if prefix == input_tokens else "generated_output_tail"
                ),
                "device_present": state.value is not None,
                "host_present": state.host_value is not None,
                "radix_child_count": len(node.children),
                **references,
            })
        result = {
            "status": "observed", "request_input_tokens": input_tokens,
            "request_output_tokens": output_tokens, "path_node_count": len(path),
            "cached_path_prefix_tokens": prefix, "mamba_states": states,
            "observation_boundary": "finished_request_last_node_path_after_cache_insertion",
            "semantics": (
                "Logical checkpoint identities and references at request completion; "
                "not transfer-source generations, write-time state or future reuse."
            ),
        }
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        result = {
            "status": "unavailable", "reason": str(error),
            "mamba_states": [],
        }
    result["probe_wall_ns"] = time.perf_counter_ns() - start_wall
    result["probe_thread_cpu_ns"] = time.thread_time_ns() - start_cpu
    return result
