"""Bounded donor selection at actual tool boundaries, with native revalidation."""

from dataclasses import asdict
import time

from beliefkv.policy.tool_boundary_preemption import BoundaryDonor, choose_boundary_plan
from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


def replace_at_tool_boundary(reference, boundary):
    controller = reference.offload
    runtime, admission = controller.runtime, boundary.admission
    if not boundary.switch_due():
        admission.counts["tool_boundary_global_interval_skips"] += 1
        return None
    if boundary.migration_allowance() <= 0:
        admission.counts["tool_boundary_migration_budget_skips"] += 1
        return None
    donors = boundary.donor_keys()
    if not donors:
        return None
    started = time.thread_time_ns()
    deadline = time.monotonic() + boundary.config["native_probe_budget_ms"] / 1000.
    work_cache = {
        donor: admission._work.prediction(donor.root_workflow_id) for donor in donors
    }
    try:
        for consumer in reference.order:
            if time.monotonic() > deadline:
                admission.counts["tool_boundary_native_probe_deadlines"] += 1
                break
            request = admission._live_requests.get(consumer.request_id)
            prediction = admission._work.prediction(consumer.root_workflow_id)
            if request is None or prediction is None:
                continue
            matching = [
                key for key in donors if work_cache[key] is not None
                and key.root_workflow_id != consumer.root_workflow_id
                and boundary.priority_work(work_cache[key])
                > boundary.priority_work(prediction) * boundary.config["min_work_ratio"]
            ]
            target = admission.policy.programs[consumer.invocation_id]
            logical = target.tokens + admission.policy.buffer_tokens - admission.policy.remaining_capacity()
            credits = sorted((
                item.tokens + admission.policy.buffer_tokens if item.active else 0
                for key in matching for item in (admission.policy.programs[key.invocation_id],)
            ), reverse=True)
            if not matching or logical > sum(credits[:2]):
                admission.counts["tool_boundary_metadata_prefilter_skips"] += 1
                continue
            reference.consumer = controller.consumer = request, consumer
            demand = reference.demand(request, consumer, refresh=True)
            if demand is None or demand[0] <= 0 and demand[1] <= 0:
                continue
            unit = runtime._residency_pool_bytes
            if unit is None:
                continue
            proposals, payloads, needs_backup = {}, {}, []

            def propose(donor, order, available, selection=None, backup_tokens=0):
                physical = available + backup_tokens
                if physical <= 0:
                    proposals.pop(donor, None)
                    payloads.pop(donor, None)
                    return
                item = admission.policy.programs[donor.invocation_id]
                proposals[donor] = BoundaryDonor(
                    donor, donor.root_workflow_id, boundary.priority_work(work_cache[donor]),
                    item.tokens + admission.policy.buffer_tokens if item.active else 0,
                    physical, controller._service_ms(backup_tokens * unit[0], "d2h"),
                    controller._service_ms(physical * unit[0], "h2d"),
                    2 * physical + backup_tokens,
                )
                payloads[donor] = order, selection, backup_tokens

            for donor in matching:
                if time.monotonic() > deadline:
                    admission.counts["tool_boundary_native_probe_deadlines"] += 1
                    break
                debt = boundary.preempted.get(donor.invocation_id)
                if debt is not None and not debt["grant_complete"] and debt["consumer"] != consumer:
                    continue
                reference.donor = donor
                if boundary.returned_candidate(donor) and not runtime._pressure_candidate_groups().get(donor):
                    boundary.promote_boundary_nodes(donor)
                    runtime._register_backed_pressure_nodes(donor)
                order, available = reference._release_plan(donor, advance=False, max_nodes=64)
                if available < max(1, demand[0]):
                    needs_backup.append(donor)
                controller.node_cursor += 64
                propose(donor, order, available)
            common = dict(
                consumer_workflow=consumer.root_workflow_id,
                consumer_work=boundary.priority_work(prediction),
                logical_deficit=max(0, demand[1]), physical_deficit=max(0, demand[0]),
                consumer_restore_ms=controller._service_ms(demand[2], "h2d"),
                consumer_migration_tokens=(demand[2] + unit[0] - 1) // unit[0],
                model_seconds_per_work_token=boundary.seconds_per_work_token,
                margin_ms=boundary.config["margin_ms"], min_work_ratio=boundary.config["min_work_ratio"],
                migration_allowance=boundary.migration_allowance(),
                committed_donors=tuple(
                    debt["donor"] for debt in boundary.preempted.values()
                    if debt["consumer"] == consumer and not debt["grant_complete"]
                ),
            )
            plan = choose_boundary_plan(tuple(proposals.values()), max_donors=1, **common)
            if plan is not None:
                admission.counts["tool_boundary_settled_plan_selections"] += 1
            else:
                # A slow cold snapshot must not hide a complete settled donor
                # later in the same bounded window.
                for donor in needs_backup:
                    if time.monotonic() > deadline:
                        admission.counts["tool_boundary_native_probe_deadlines"] += 1
                        break
                    reference.donor = donor
                    headroom = observe_static_full_mamba_headroom(runtime._native_cache)
                    admission.counts["tool_boundary_shadow_probes"] += 1
                    selection = runtime._refreshed_shadow_backup_selection(
                        context_id=donor.context_id, host_full_free_tokens=headroom.host_full_free_tokens,
                        source="tool_boundary",
                    )
                    order, available = reference._release_plan(donor, advance=False, max_nodes=64)
                    backup_tokens = 0
                    if selection is not None:
                        step, _candidate = selection
                        full = runtime._native_cache.tree_core.node_by_id(step.node_id).component_data[0]
                        if full.value is not None and full.host_value is None:
                            backup_tokens = max(step.missing_full_prefix_tokens, len(full.value))
                    propose(donor, order, available, selection, backup_tokens)
                plan = choose_boundary_plan(tuple(proposals.values()), max_donors=1, **common)
            if plan is None:
                # Multi-owner handovers must already be fully Host-backed.
                # Do not start two speculative backups for an unadmitted B.
                plan = choose_boundary_plan(
                    [item for item in proposals.values() if payloads[item.key][2] == 0],
                    max_donors=2, **common,
                )
            admission.counts["tool_boundary_plan_searches"] += 1
            if plan is None:
                continue
            boundary.active_plan, boundary.active_consumer = plan, consumer
            boundary.active_details = {
                "tool_boundary_preemption": True, "tool_boundary_policy": boundary.config["kind"],
                "tool_boundary_donors": [asdict(key) for key in plan.donors],
                "tool_boundary_surrogate_gain_ms": plan.gain_ms,
                "tool_boundary_migration_tokens_budgeted": plan.migration_tokens,
                "tool_boundary_current_tool_residual_assumed_ms": (
                    None if boundary.critical_path else 0.
                ),
                "tool_boundary_priority_basis": boundary.status()["priority_basis"],
                "tool_boundary_score_is_not_counterfactual_JCT": True,
            }
            signature = consumer, plan.donors
            if signature not in boundary._records:
                boundary._records[signature] = True
                while len(boundary._records) > 512:
                    boundary._records.popitem(last=False)
                writer = getattr(runtime, "_opportunity_writer", None)
                if writer is not None:
                    writer.record({
                        "event": "tool_boundary_preemption_plan", "ts_ms": time.time() * 1000.,
                        "monotonic_s": time.monotonic(), "consumer": asdict(consumer),
                        **boundary.active_details, "logical_deficit_tokens": max(0, demand[1]),
                        "physical_deficit_tokens": max(0, demand[0]),
                        "physical_capacity_credit": False,
                    })
            try:
                completed = False
                release_count = admission.counts["tool_boundary_physical_release_notifications"]
                if not all(boundary.donor_candidate(key) for key in plan.donors):
                    continue
                for donor in plan.donors:
                    reference.donor = donor
                    order, selection, backup_tokens = payloads[donor]
                    if backup_tokens:
                        if (
                            runtime.physical_ledger.pending_action_count("PREPARE_HOST")
                            or selection is None or not controller._eligible(donor)
                        ):
                            return False
                        step, candidate = selection
                        headroom = observe_static_full_mamba_headroom(runtime._native_cache)
                        if not controller._prepare_allowed(step, headroom):
                            return False
                        issued = runtime.issue_shadow_backup_steps(
                            step, source="tool_boundary", _selection_candidate=candidate,
                            _authorization=lambda: controller._authorize_backup(donor, step),
                        )
                        for backed, _command in issued:
                            runtime._remember_parent_pressure_node(
                                backed.node_id, backed.key, backed.creation_time,
                            )
                        runtime.counts["predicted_tool_offload_backup_issued"] += len(issued)
                        admission.counts["tool_event_replacement_backup_attempts"] += bool(issued)
                        if issued:
                            boundary.migration_history.append((
                                time.monotonic() * 1000., backup_tokens,
                            ))
                            boundary.backup_issued(donor, consumer)
                        return False
                    if not reference.permits(request, consumer, donor):
                        return False
                    if not controller._evict_backed(donor, node_ids=order):
                        return False
                if not reference._grant():
                    admission.counts["tool_boundary_released_without_complete_grant"] += 1
                    return False
                boundary.migration_history.append((
                    time.monotonic() * 1000., common["consumer_migration_tokens"],
                ))
                admission.counts["tool_boundary_plan_donors_granted"] += len(plan.donors)
                completed = True
                return True
            finally:
                if not completed and any(
                    boundary.has_release(key, consumer) for key in plan.donors
                ):
                    admission.counts["tool_boundary_ungranted_partial_attempts"] += 1
                    if admission.counts["tool_boundary_physical_release_notifications"] > release_count:
                        controller.rearm()
                boundary.active_plan = boundary.active_consumer = boundary.active_details = None
        return None
    finally:
        reference.consumer = controller.consumer = reference.donor = None
        admission.counts["tool_boundary_plan_cpu_ns"] += time.thread_time_ns() - started
