"""Bind predicted cold-tool donors to a selected native consumer's layer handoff."""

import time


def try_handoff(offloader, key, step):
    runtime = offloader.runtime
    if (
        not getattr(runtime, "layer_handoff_enabled", False)
        or offloader._dispatch_stage != "before_native_admission"
        or offloader.consumer is None
    ):
        return False
    cache = runtime._native_cache
    begin = getattr(cache, "begin_beliefkv_layer_handoff", None)
    if not callable(begin):
        raise RuntimeError("layer handoff requires its separately qualified native engine")
    if getattr(cache, "_beliefkv_layer_handoff", None) is not None:
        runtime.counts["layer_handoff_busy"] += 1
        return False
    request, consumer_key = offloader.consumer
    if not runtime._agent_admission.allows(consumer_key):
        return False
    observation = runtime._read_request_reentry(request, key=consumer_key, refresh=True)
    cost = runtime._request_restore_cost(request, observation)
    leaves = dict(observation.get("component_leaves", ())).get(0, ()) if observation else ()
    if cost is None or len(leaves) != 1 or not offloader._eligible(key):
        return False
    try:
        consumer = cache.tree_core.node_by_id(leaves[0][0])
        anchors = runtime._snapshot_key_session_anchors(cache, key, reusable_input_tokens=None)
        donor_leaves = dict(anchors.component_leaves).get(0, ()) if anchors else ()
        if len(donor_leaves) != 1:
            return False
        donor = cache.tree_core.node_by_id(donor_leaves[0][0])
    except (KeyError, RuntimeError, ValueError):
        return False
    a, b = donor.component_data[0], consumer.component_data[0]
    missing = observation.get("missing_full_tokens", 0)
    if (
        donor.creation_time != donor_leaves[0][1]
        or consumer.creation_time != leaves[0][1]
        or a.value is None or a.host_value is not None
        or b.value is not None or b.host_value is None
        or not missing
    ):
        runtime.counts["layer_handoff_geometry_refused"] += 1
        return False
    reserve = cost[1] + max(
        runtime._current_residency_budget().reserve_full_tokens,
        runtime._native_page_size * (
            len(getattr(runtime._native_running_batch, "reqs", ())) + 1
        ),
    )
    def authorize(backup_tokens, consumer_tokens):
        return (
            consumer_tokens == missing
            and runtime.visible.get(getattr(request, "rid", None)) == consumer_key
            and runtime._agent_admission.allows(consumer_key)
            and offloader._eligible(key)
            and offloader._worth_offloading(
                key, backup_tokens=backup_tokens, release_tokens=backup_tokens,
            )
        )

    result = begin(
        donor_node_id=donor.id, donor_creation_time=donor.creation_time,
        donor_session_id=key.session_id, donor_session_generation=key.session_generation,
        consumer_node_id=consumer.id, consumer_creation_time=consumer.creation_time,
        consumer_session_id=consumer_key.session_id,
        consumer_session_generation=consumer_key.session_generation,
        reserve_tokens=reserve, authorize=authorize,
    )
    if result is None:
        runtime.counts["layer_handoff_native_refused"] += 1
        return False
    offloader._after_eviction(key, {0: result["donor_tokens"]})
    runtime._invalidate_wait_transfer_observation(key)
    runtime._invalidate_wait_transfer_observation(consumer_key)
    runtime.counts["layer_handoff_submitted"] += 1
    runtime.counts["layer_handoff_reused_tokens"] += result["reused_tokens"]
    runtime.counts["layer_handoff_reserved_tail_tokens"] += result["reserved_tail_tokens"]
    if runtime._opportunity_writer is not None:
        runtime._opportunity_writer.record({
            "event": "layer_kv_handoff_submitted", "ts_ms": time.time() * 1000.,
            "workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
            "context_id": key.context_id, "context_epoch": key.context_epoch,
            "session_id": key.session_id, "session_generation": key.session_generation,
            "beneficiary_request_id": consumer_key.request_id,
            "beneficiary_invocation_id": consumer_key.invocation_id,
            "beneficiary_context_id": consumer_key.context_id,
            "beneficiary_context_epoch": consumer_key.context_epoch,
            "beneficiary_session_id": consumer_key.session_id,
            "beneficiary_session_generation": consumer_key.session_generation,
            "capacity_effect": "reserved_extent;no_free_page_credit",
            "logical_admission_changed": False, **result, **offloader._action_detail(),
        })
    return True
