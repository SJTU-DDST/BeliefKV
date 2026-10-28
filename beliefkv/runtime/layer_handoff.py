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
    try:
        from sglang.srt.mem_cache.radix_cache import RadixKey

        load, extras = cache.tree_core.build_load_back_spec(consumer.id)
        restore_tokens = len(load.host_indices)
        tail = restore_tokens - missing
        if extras or restore_tokens <= 0:
            return False
        if tail:
            # Reentry inspection stops at whole extents before the final input
            # token. Include a cached decode tail only if this request contains
            # that complete private extent; price every transferred token.
            target = RadixKey(
                request.origin_input_ids, extra_key=getattr(request, "extra_key", None),
                cache_salt=getattr(request, "cache_salt", None),
            )
            if (
                not 0 < tail == len(consumer.key) == len(b.host_value) <= cost[1]
                or not consumer.key.is_prefix_at(
                    target, observation["checkpoint_tokens"], page_size=runtime._native_page_size,
                )
            ):
                runtime.counts["layer_handoff_unmatched_decode_tail"] += 1
                return False
    except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
        runtime.counts["layer_handoff_restore_extent_unavailable"] += 1
        return False
    budget = runtime._current_residency_budget()
    minimum_growth = runtime._native_page_size * (
        len(getattr(runtime._native_running_batch, "reqs", ())) + 1
    )
    growth = max(budget.reserve_full_tokens, minimum_growth)
    adder = getattr(offloader, "_admission_adder", None)
    selected = getattr(adder, "can_run_list", None)
    input_bound = getattr(runtime, "_native_input_reserve", None)
    if (
        getattr(budget, "source", None) == "native_next_prefill"
        and type(selected) in (list, tuple) and not selected
        and getattr(adder, "dllm_config", None) is None
        and type(input_bound) is int
        and cost[1] <= input_bound <= budget.reserve_full_tokens
    ):
        # This demand consumer replaces the unselected prefill input bound.
        # Keep every growth slot; an already selected chunk keeps the old bound.
        growth = max(minimum_growth, budget.reserve_full_tokens - input_bound)
        runtime.counts["layer_handoff_selected_input_reserve"] += 1
    reserve = cost[1] + growth
    def authorize(backup_tokens, consumer_tokens):
        return (
            consumer_tokens == restore_tokens
            and runtime.visible.get(getattr(request, "rid", None)) == consumer_key
            and runtime._agent_admission.allows(consumer_key)
            and offloader._eligible(key)
            and offloader._worth_offloading(
                key, backup_tokens=backup_tokens, release_tokens=backup_tokens,
                consumer_restore_bytes=restore_tokens * runtime._residency_pool_bytes[0],
            )
        )

    manager = getattr(runtime, "_host_demand_capacity", None)
    if manager is not None:
        node, donors, donor_tokens = donor, [], 0
        for _ in range(64):
            if node is cache.tree_core.root_node:
                break
            full = node.component_data[0]
            if full.value is None or full.host_value is not None:
                break
            donors.append(node.id)
            donor_tokens += len(full.value)
            if donor_tokens >= restore_tokens:
                break
            node = node.parent
        if (
            donor_tokens < restore_tokens
            or not authorize(donor_tokens, restore_tokens)
            or not manager.ensure(donor_tokens, protected=(*donors, consumer.id))
        ):
            return False
    result = begin(
        donor_node_id=donor.id, donor_creation_time=donor.creation_time,
        donor_session_id=key.session_id, donor_session_generation=key.session_generation,
        consumer_node_id=consumer.id, consumer_creation_time=consumer.creation_time,
        consumer_session_id=consumer_key.session_id,
        consumer_session_generation=consumer_key.session_generation,
        reserve_tokens=reserve, authorize=authorize,
        **({
            "donor_group_layers": runtime.layer_handoff_d2h_layers,
            "consumer_group_layers": runtime.layer_handoff_h2d_layers,
        } if (runtime.layer_handoff_d2h_layers, runtime.layer_handoff_h2d_layers) != (1, 1) else {}),
    )
    if result is None:
        runtime.counts["layer_handoff_native_refused"] += 1
        if runtime._opportunity_writer is not None:
            from sglang.srt.mem_cache.beliefkv_layer_handoff import supported

            cc, component = cache.cache_controller, cache.components[0]
            runtime._opportunity_writer.record({
                "event": "layer_kv_handoff_refused", "ts_ms": time.time() * 1000.,
                "beneficiary_request_id": consumer_key.request_id,
                "donor_node_id": donor.id, "consumer_node_id": consumer.id,
                "supported_cache": supported(cache),
                "device_free_tokens": cc.mem_pool_device_allocator.available_size(),
                "host_free_tokens": cc.mem_pool_host.available_size(),
                "required_reserve_tokens": reserve,
                "planned_restore_tokens": restore_tokens,
                "checkpoint_missing_tokens": missing,
                "donor_tokens": len(a.value) if a.value is not None else 0,
                "donor_device_locks": a.lock_ref, "donor_host_locks": a.host_lock_ref,
                "consumer_device_locks": b.lock_ref, "consumer_host_locks": b.host_lock_ref,
                "donor_is_session_leaf": donor in component._session_leaves.get(key.session_id, ()),
                "consumer_is_session_leaf": consumer in component._session_leaves.get(
                    consumer_key.session_id, (),
                ),
                "donor_is_evictable_leaf": donor in cache.tree_core.evictable_device_leaves,
                "queued_writes": len(cc.write_queue), "queued_loads": len(cc.load_queue),
                "pending_write_acks": len(cc.ack_write_queue),
                "pending_load_acks": len(cc.ack_load_queue),
            })
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
            "reserved_input_tokens": cost[1], "reserved_growth_tokens": growth,
            "checkpoint_missing_tokens": missing, "restored_decode_tail_tokens": tail,
            "speculative_reserve_tokens": budget.reserve_full_tokens,
            "logical_admission_changed": False, **result, **offloader._action_detail(),
        })
    return True
