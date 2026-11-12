"""Inspect periodic grants and native progress before the next observed decision."""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts.audit_free_dispatch_service import identity, validate_grant
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


EVENT = "agent_periodic_admission_decision"
EXTRA_GRANTS = frozenset((
    "agent_continuation_free_capacity_admitted", "agent_dispatch_free_capacity_admitted",
    "agent_join_free_capacity_admitted", "agent_join_siblings_coadmitted",
    "agent_join_blocked_capacity_admitted",
))


def intervention(record, sequence, interventions, *, replacement_consumer_already_granted=False):
    name = record.get("event")
    immediate = name in ("agent_full_offload_paused", "agent_predicted_tool_replacement")
    pressure = name == "workflow_remaining_pressure_pause"
    if pressure:
        if type(record.get("marked_for_pause")) is not bool:
            raise ValueError("pressure intervention requires actual versus planned pause")
        if record["marked_for_pause"]:
            return
    if name not in EXTRA_GRANTS and name != "agent_initial_registration_decision" and not (
        immediate or pressure
    ):
        return
    when = record.get("monotonic_s")
    if not finite_number(when) or when < 0:
        raise ValueError("admission intervention needs an observed monotonic clock")
    if pressure:
        owner = record.get("selected")
        if type(owner) is not str or not owner:
            raise ValueError("actual pressure pause requires an observed agent")
        interventions[owner].append((when * 1000., sequence))
        return
    members = (
        ([{"key": record["donor"]}] if replacement_consumer_already_granted else
         [{"key": record["consumer"]}, {"key": record["donor"]}])
        if name == "agent_predicted_tool_replacement" else
        [{"key": record["key"]}]
        if name in ("agent_initial_registration_decision", "agent_full_offload_paused")
        else [*record["programs"], *record.get("donors", ())]
    )
    for member in members:
        key = identity(member["key"], grant=True)
        interventions[key[2]].append((when * 1000., sequence))


class PublishedGrantInterventions:
    """Link a replacement marker only to its adjacent, validated grant."""

    def __init__(self):
        self.pending = None
        self.choice_seen = False
        self.links = []
        self.unlinked_replacements = 0

    def _choice_matches(self, row):
        if self.pending is None or self.choice_seen:
            return False
        _sequence, grant, key = self.pending
        members = [
            item for item in row.get("candidates", ()) if item.get("selected") is True
        ]
        return (
            row.get("grant_published") is True
            and row.get("grant_event") == grant["event"]
            and row.get("grant_monotonic_s") == grant["monotonic_s"]
            and row.get("remaining_before_tokens") == grant["remaining_before_tokens"]
            and row.get("selected_tokens") == grant["required_tokens"]
            and len(members) == 1 and identity(members[0]["key"], grant=True) == key
        )

    def observe(self, row, sequence, changes):
        event, linked = row.get("event"), False
        pending = self.pending
        if event == "agent_predicted_tool_replacement":
            if pending is not None:
                grant_sequence, grant, key = pending
                linked = (
                    identity(row["consumer"], grant=True) == key
                    and identity(row["donor"], grant=True)[2] != key[2]
                    and row.get("remaining_before_tokens") == grant["remaining_before_tokens"]
                    and row.get("remaining_after_tokens") == grant["remaining_after_tokens"]
                    and finite_number(row.get("monotonic_s"))
                    and row["monotonic_s"] >= grant["monotonic_s"]
                    and row.get("coalescing_window_bypassed") is True
                    and row.get("ordinary_restore_lease") is False
                    and row.get("physical_capacity_authority")
                    == "settled_private_release_then_fresh_native_budget"
                )
                if linked:
                    self.links.append({
                        "grant_journal_sequence": grant_sequence,
                        "replacement_journal_sequence": sequence,
                        "grant_monotonic_s": grant["monotonic_s"],
                        "replacement_monotonic_s": row["monotonic_s"],
                        "consumer_key": key,
                    })
            self.unlinked_replacements += not linked
            self.pending = None
        elif event == "agent_free_capacity_choice" and self._choice_matches(row):
            self.choice_seen = True
        else:
            self.pending = None
            self.choice_seen = False
            if event == "agent_dispatch_free_capacity_admitted":
                try:
                    members = validate_grant(row)
                except (KeyError, TypeError, ValueError):
                    members = []
                if len(members) == 1:
                    self.pending = sequence, row, members[0][0]
        intervention(
            row, sequence, changes, replacement_consumer_already_granted=linked,
        )

    def summary(self):
        return {
            "linked_tool_replacement_markers": len(self.links),
            "unlinked_tool_replacement_markers": self.unlinked_replacements,
            "links": self.links,
            "scope": (
                "One validated single-consumer free grant followed immediately by "
                "its replacement marker, allowing one matching choice observation. "
                "Exact attempt/session identity, budgets and monotonic order required. "
                "The marker still bounds the donor; unlinked markers bound both owners."
            ),
        }


def useful_work(member, phase):
    output, tokens = member.get("output_token_delta"), member.get("token_delta")
    if output is None or tokens is None:
        return None
    if any(type(value) is not int or value < 0 for value in (output, tokens)):
        raise ValueError("native progress needs nonnegative input and output deltas")
    return output > 0 or phase == "prefill" and tokens > 0


def inspect(records, batches, *, include_free_grants=False):
    if type(include_free_grants) is not bool:
        raise ValueError("free-grant progress must be explicitly enabled")
    counts, decisions, grants = Counter(), [], []
    observations = []
    controls, interventions = [], defaultdict(list)
    tracker = PublishedGrantInterventions()
    previous = -1.
    for sequence, record in enumerate(records):
        tracker.observe(record, sequence, interventions)
        if include_free_grants and record.get("event") == "agent_dispatch_free_capacity_admitted":
            for key, grant, _observed, _budget in validate_grant(record):
                # This event follows the logical grant commit. Later pressure
                # or regrant events still bound the usable observation window.
                grants.append({
                    "key": key, "selection_ms": grant["monotonic_s"] * 1000.,
                    "journal_sequence": sequence, "selection_retained": True,
                    "grant_kind": "free_budget",
                })
                counts["free_budget_grant_episodes_in_progress_audit"] += 1
        if record.get("event") != EVENT:
            continue
        now, capacity = record.get("monotonic_s"), record.get("resume_capacity_tokens")
        programs = record.get("programs")
        resumed = record.get("resumed_program_ids")
        if (
            not finite_number(now) or now < previous or type(capacity) is not int
            or not isinstance(programs, list) or not isinstance(resumed, list)
            or len(set(resumed)) != len(resumed)
            or record.get("physical_capacity_credit") is not False
        ):
            raise ValueError("invalid actual periodic boundary")
        previous = now
        controls.append((now * 1000., sequence))
        metadata = {}
        for item in programs:
            owner = item["invocation_id"]
            if owner in metadata or any(type(item.get(name)) is not bool for name in (
                "active_before", "pending_before", "acting_before", "marked_before",
                "application_ready", "active_after_pressure", "marked_after_pressure",
            )) or type(item.get("complete_budget_tokens")) is not int or item["complete_budget_tokens"] <= 0:
                raise ValueError("invalid periodic program state or budget")
            metadata[owner] = item
        if not set(resumed) <= metadata.keys() or sum(
            metadata[owner]["complete_budget_tokens"] for owner in resumed
        ) > max(0, capacity):
            raise ValueError("periodic selection exceeds its recorded resume budget")
        application_ready = [
            item for item in programs
            if item["application_ready"] and item["pending_before"]
            and not item["active_before"] and not item["marked_before"]
        ]
        if any(type(item.get("resume_window_eligible", True)) is not bool for item in programs):
            raise ValueError("invalid periodic admission window eligibility")
        ready = [item for item in application_ready if item.get("resume_window_eligible", True)]
        deferred = [item for item in application_ready if not item.get("resume_window_eligible", True)]
        application_fitting = [
            item for item in application_ready if item["complete_budget_tokens"] <= capacity
        ]
        deferred_fitting = [
            item for item in deferred if item["complete_budget_tokens"] <= capacity
        ]
        if deferred:
            counts["controls_with_window_deferred_ready_paused"] += 1
            counts["window_deferred_ready_paused_observations"] += len(deferred)
        fitting = [item for item in ready if item["complete_budget_tokens"] <= capacity]
        owners = {}
        for item in application_ready:
            key = identity(item["key"], grant=True)
            if key[2] != item["invocation_id"]:
                raise ValueError("periodic request has another invocation owner")
            owners[item["invocation_id"]] = key
        competition = (
            len({owners[item["invocation_id"]][1] for item in fitting}) > 1
            and sum(item["complete_budget_tokens"] for item in fitting) > capacity
        )
        application_competition = (
            len({owners[item["invocation_id"]][1] for item in application_fitting}) > 1
            and sum(item["complete_budget_tokens"] for item in application_fitting) > capacity
        )
        counts["controls"] += 1
        counts["controls_with_ready_paused"] += bool(ready)
        counts["cross_workflow_logical_set_competition_controls"] += competition
        counts["free_budget_without_ready_paused_controls"] += capacity > 100 and not ready
        counts["controls_with_application_ready_paused"] += bool(application_ready)
        counts["controls_with_window_deferred_fitting_ready_paused"] += bool(deferred_fitting)
        counts["window_deferred_fitting_ready_paused_observations"] += len(deferred_fitting)
        counts["cross_workflow_application_ready_set_competition_controls"] += application_competition
        counts["controls_with_available_resume_budget"] += capacity > 100
        counts["free_budget_without_application_ready_paused_controls"] += (
            capacity > 100 and not application_ready
        )
        counts["free_budget_with_application_ready_but_none_fitting_controls"] += (
            capacity > 100 and bool(application_ready) and not application_fitting
        )
        counts["free_budget_with_only_window_deferred_fitting_ready_controls"] += (
            capacity > 100 and bool(application_fitting) and not fitting
        )
        counts["free_budget_with_fitting_window_eligible_ready_controls"] += (
            capacity > 100 and bool(fitting)
        )
        observations.append({
            "monotonic_s": now, "resume_capacity_tokens": capacity,
            "ready_paused_agents": len(ready),
            "fitting_ready_paused_agents": len(fitting),
            "application_ready_paused_agents": len(application_ready),
            "fitting_application_ready_paused_agents": len(application_fitting),
            "window_deferred_ready_paused_agents": len(deferred),
            "window_deferred_fitting_ready_paused_agents": len(deferred_fitting),
            "cross_workflow_application_ready_set_competition": application_competition,
            "fitting_workflows": len({
                owners[item["invocation_id"]][1] for item in fitting
            }),
            "cross_workflow_logical_set_competition": competition,
            "resumed_programs": len(resumed),
            "resumed_retained_after_pressure": sum(
                metadata[owner]["active_after_pressure"] for owner in resumed
            ),
        })
        for owner in resumed:
            item = metadata[owner]
            counts["resumed_program_episodes"] += 1
            counts["resumed_revoked_by_pressure"] += not item["active_after_pressure"]
            if item["key"] is not None:
                interventions[owner].append((now * 1000., sequence))
            if owner in owners and item.get("resume_window_eligible", True):
                grants.append({
                    "key": owners[owner], "selection_ms": now * 1000.,
                    "journal_sequence": sequence,
                    "active_after_pressure": item["active_after_pressure"],
                    "marked_after_pressure": item["marked_after_pressure"],
                })
        if competition:
            decisions.append({
                "monotonic_s": now, "resume_capacity_tokens": capacity,
                "fitting_candidates": [item["key"] for item in fitting],
                "selected": resumed,
                "selected_after_pressure": [
                    owner for owner in resumed if metadata[owner]["active_after_pressure"]
                ],
            })
    progress = associate_progress(grants, controls, interventions, batches, counts)
    return {
        "counts": dict(counts), "competition_decisions": decisions,
        "control_observations": observations, **progress,
        "published_grant_interventions": tracker.summary(),
        "scope": {
            "exact_periodic_resume_budget_and_before_after_state": True,
            "application_readiness_separate_from_timed_resume_eligibility": True,
            "legacy_no_ready_counter_means_no_window_eligible_candidate": True,
            "application_ready_competition_is_diagnostic_not_a_grant_opportunity": True,
            "cross_category_competition_is_diagnostic_not_original_permission": True,
            "logical_fit_does_not_prove_physical_feasibility": True,
            "earlier_and_foreign_attempt_service_excluded": True,
            "intervening_grants_or_exclusive_causal_benefit_proven": False,
            "bounded_progress_ends_at_next_control_or_recorded_same_agent_intervention": True,
            "bounded_progress_requires_new_launch_and_positive_model_progress": True,
            "launch_clock_is_scheduler_run_batch_entry_not_CUDA_submission": True,
            "service_start_clock_is_worker_interval_not_kernel_start": True,
            "completion_clock_has_observed_positive_native_progress": True,
            "open_prefix_windows_are_unknown_not_negative_results": True,
            "unlogged_interventions_and_exclusive_causal_benefit_not_proven": True,
            "other_admission_entries_require_their_separate_audits": True,
            "independently_sealed_prefixes_not_atomic": True,
            "published_free_budget_grants_included": include_free_grants,
        },
    }


def associate_progress(grants, controls, interventions, batches, counts):
    """Associate exact-attempt progress within each observed grant's lifetime."""
    target = {row["key"] for row in grants}
    services, launches = defaultdict(list), defaultdict(list)
    domains = set()
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        when, domain = batch.get("service_start_monotonic_ms"), batch.get("monotonic_clock_domain")
        if not finite_number(when) or when < 0 or not domain:
            raise ValueError("unobserved native service clock")
        domains.add(domain)
        for member in batch.get("request_samples", ()):
            if any(member.get(name) is None for name in ("attempt_id", "session_id", "session_generation")):
                continue
            key = identity(member)
            if key in target:
                services[key].append(when)
                launch = batch.get("launch_monotonic_ms")
                if launch is None:
                    counts["target_service_samples_without_launch_clock"] += 1
                    continue
                if not finite_number(launch) or not 0 <= launch <= when:
                    raise ValueError("native launch must precede observed service in the same clock")
                complete = batch.get("complete_monotonic_ms")
                if complete is None:
                    counts["target_service_samples_without_completion_clock"] += 1
                    continue
                if not finite_number(complete) or complete < when:
                    raise ValueError("native completion precedes observed service")
                progress = useful_work(member, member.get("phase", batch.get("phase")))
                if progress is None:
                    counts["target_service_samples_without_progress_deltas"] += 1
                elif progress:
                    launches[key].append((launch, when, complete))
                else:
                    counts["target_zero_progress_service_samples"] += 1
    if len(domains) > 1:
        raise ValueError("native services cross monotonic clock domains")
    for values in services.values():
        values.sort()
    for values in launches.values():
        values.sort()
    for values in interventions.values():
        values.sort()
    latencies, launch_latencies, bounded_service_latencies, completion_latencies = [], [], [], []
    for row in grants:
        retained = row.get("selection_retained", row.get("active_after_pressure"))
        if type(retained) is not bool:
            raise ValueError("grant progress requires observed eligibility at selection")
        values = services[row["key"]]
        index = bisect_left(values, row["selection_ms"])
        row["following_native_service_ms"] = (
            values[index] - row["selection_ms"]
            if retained and index < len(values) else None
        )
        if row["following_native_service_ms"] is not None:
            latencies.append(row["following_native_service_ms"])
            counts["ready_grants_with_following_exact_service"] += 1
        # A later control or regrant must not validate an earlier grant's window.
        position = row["selection_ms"], row["journal_sequence"]
        next_control = bisect_right(controls, position)
        changes = interventions[row["key"][2]]
        next_change = bisect_right(changes, position)
        boundaries = []
        if next_control < len(controls):
            boundaries.append((controls[next_control][0], "next_control"))
        if next_change < len(changes):
            boundaries.append((changes[next_change][0], "same_agent_intervention"))
        end, reason = min(boundaries) if boundaries else (None, None)
        candidates = launches[row["key"]]
        index = bisect_left(candidates, (row["selection_ms"], -1., -1.))
        selected = (
            candidates[index]
            if retained and index < len(candidates) else None
        )
        launched = selected is not None and end is not None and selected[0] < end
        served = launched and selected[1] < end
        completed = launched and selected[2] < end
        row.update(
            observation_window_end_ms=end, observation_window_end_reason=reason,
            first_new_useful_launch_ms=selected[0] - row["selection_ms"] if selected else None,
            first_new_useful_service_ms=selected[1] - row["selection_ms"] if selected else None,
            first_new_useful_completion_ms=selected[2] - row["selection_ms"] if selected else None,
            new_launch_before_next_control_or_intervention=launched if end is not None else None,
            new_service_before_next_control_or_intervention=served if end is not None else None,
            new_completion_before_next_control_or_intervention=completed if end is not None else None,
        )
        counts["ready_grants_with_observed_new_useful_launch"] += selected is not None
        counts["ready_grants_with_open_observation_window"] += end is None
        counts["ready_grants_launched_within_observation_window"] += launched
        counts["ready_grants_served_within_observation_window"] += served
        counts["ready_grants_completed_useful_batch_within_observation_window"] += completed
        if launched:
            launch_latencies.append(row["first_new_useful_launch_ms"])
        if served:
            bounded_service_latencies.append(row["first_new_useful_service_ms"])
        if completed:
            completion_latencies.append(row["first_new_useful_completion_ms"])
    return {
        "ready_grant_episodes": grants, "following_service_ms": distribution(latencies),
        "within_window_new_launch_ms": distribution(launch_latencies),
        "within_window_new_service_ms": distribution(bounded_service_latencies),
        "within_window_new_completion_ms": distribution(completion_latencies),
    }


def observation_phases(result, arrivals, cohort, events):
    """Keep decision-time diagnostics separate from the fixed throughput window."""
    validate_cohort(cohort, arrivals)
    offsets = {
        row["workflow_id"]: row["offset_seconds"]
        for row in arrivals["arrival_schedule"]
    }
    starts, ends, requests = {}, {}, set()
    model_completions = []
    for row in events:
        workflow, when, kind = row["workflow_id"], row["ts_ms"], row["kind"]
        if workflow not in offsets or not finite_number(when) or when < 0:
            raise ValueError("phase diagnostics require known workflows and finite clocks")
        if kind in ("workflow_start", "workflow_end"):
            mapping = starts if kind == "workflow_start" else ends
            if workflow in mapping:
                raise ValueError("duplicate workflow boundary in phase diagnostics")
            mapping[workflow] = when
        elif kind == "llm_result":
            request = row["attributes"]["request_id"]
            if request in requests:
                raise ValueError("duplicate model completion in phase diagnostics")
            requests.add(request)
            model_completions.append((when, workflow))
    origins = [when - offsets[workflow] * 1000. for workflow, when in starts.items()]
    if (
        not origins or max(origins) - min(origins) > 1e-5
        or not ends.keys() <= starts.keys()
        or any(ends[key] < starts[key] for key in ends)
    ):
        raise ValueError("phase boundaries differ from the frozen arrival clock")
    origin = origins[0] / 1000.
    members = set(cohort["measured_workflow_ids"])
    complete = members <= ends.keys()
    measured_end = max(ends[key] / 1000. - origin for key in members) if complete else None
    start, end = cohort["throughput_window_offset_seconds"]
    controls = result["control_observations"]
    if not controls:
        raise ValueError("phase diagnostics require actual periodic controls")
    observed_through = max(row["monotonic_s"] for row in controls) - origin
    windows = {
        "fixed_common_throughput_window": (start, end),
        "measured_execution_observation_span": (start, measured_end),
        "measured_execution_after_common_window": (end, measured_end),
    }
    phases = {}
    for name, (left, right) in windows.items():
        rows = [
            row for row in controls if row["monotonic_s"] - origin >= left
            and (right is None or row["monotonic_s"] - origin < right)
        ]
        ready = sum(row["ready_paused_agents"] > 0 for row in rows)
        supply_observed = all(
            all(field in row for field in (
                "application_ready_paused_agents", "fitting_application_ready_paused_agents",
                "window_deferred_ready_paused_agents", "window_deferred_fitting_ready_paused_agents",
                "cross_workflow_application_ready_set_competition",
            )) for row in rows
        )
        supply = {
            "controls_with_application_ready_paused": sum(
                row["application_ready_paused_agents"] > 0 for row in rows
            ),
            "controls_with_window_deferred_fitting_ready_paused": sum(
                row["window_deferred_fitting_ready_paused_agents"] > 0 for row in rows
            ),
            "application_ready_cross_workflow_logical_set_competitions": sum(
                row["cross_workflow_application_ready_set_competition"] for row in rows
            ),
            "free_budget_without_application_ready_paused_controls": sum(
                row["resume_capacity_tokens"] > 100 and not row["application_ready_paused_agents"]
                for row in rows
            ),
            "free_budget_with_application_ready_but_none_fitting_controls": sum(
                row["resume_capacity_tokens"] > 100 and bool(row["application_ready_paused_agents"])
                and not row["fitting_application_ready_paused_agents"] for row in rows
            ),
            "free_budget_with_only_window_deferred_fitting_ready_controls": sum(
                row["resume_capacity_tokens"] > 100 and bool(row["fitting_application_ready_paused_agents"])
                and not row["fitting_ready_paused_agents"] for row in rows
            ),
            "free_budget_with_fitting_window_eligible_ready_controls": sum(
                row["resume_capacity_tokens"] > 100 and bool(row["fitting_ready_paused_agents"])
                for row in rows
            ),
        } if supply_observed else {
            name: None for name in (
                "controls_with_application_ready_paused",
                "controls_with_window_deferred_fitting_ready_paused",
                "application_ready_cross_workflow_logical_set_competitions",
                "free_budget_without_application_ready_paused_controls",
                "free_budget_with_application_ready_but_none_fitting_controls",
                "free_budget_with_only_window_deferred_fitting_ready_controls",
                "free_budget_with_fitting_window_eligible_ready_controls",
            )
        }
        completed = [
            workflow for when, workflow in model_completions
            if workflow in members and when / 1000. - origin >= left
            and (right is None or when / 1000. - origin < right)
        ]
        phases[name] = {
            "start_s": left, "end_s": right,
            "observed_through_offset_s": min(observed_through, right) if right is not None else observed_through,
            "phase_started": observed_through >= left,
            "complete_phase_observed": right is not None and observed_through >= right,
            "actual_periodic_controls": len(rows),
            "controls_with_predecision_ready_paused": ready,
            "predecision_ready_fraction": ready / len(rows) if rows else None,
            "application_readiness_diagnostics_observed": supply_observed,
            **supply,
            "actual_cross_workflow_logical_set_competitions": sum(
                row["cross_workflow_logical_set_competition"] for row in rows
            ),
            "free_budget_without_predecision_ready_paused_controls": sum(
                row["resume_capacity_tokens"] > 100 and not row["ready_paused_agents"]
                for row in rows
            ),
            "periodic_granted_programs": sum(row["resumed_programs"] for row in rows),
            "periodic_grants_retained_after_pressure": sum(
                row["resumed_retained_after_pressure"] for row in rows
            ),
            "completed_measured_model_requests": len(completed),
        }
    return {
        "origin_monotonic_ms": origins[0],
        "all_measured_completed": complete,
        "observed_measured_completions": len(members & ends.keys()),
        "last_measured_completion_offset_s": measured_end,
        "phases": phases,
        "scope": {
            "preselection_periodic_state_not_postselection_snapshot": True,
            "application_readiness_separate_from_timed_resume_eligibility": True,
            "actual_periodic_entries_only_other_grants_remain_separate": True,
            "fixed_common_window_preserved_for_throughput": True,
            "execution_span_depends_on_policy_completion_diagnostic_only": True,
            "overlapping_phase_counts_must_not_be_added": True,
            "completed_requests_not_token_work_or_GPU_time": True,
            "logical_fit_not_physical_feasibility": True,
            "independently_sealed_prefixes_not_atomic": True,
            "workload_acceptance_gate_changed": False,
            "GPU_action": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--cohort", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior decision observations")
    registration = json.loads(args.registration.read_bytes())
    measured = None
    if args.complete and args.cohort is not None:
        measured = verify_execution(args.registration, args.cohort)
    elif args.complete and complete_work(args.registration) is None:
        raise ValueError("complete decision audit requires full work, fresh drain and actual owned exit")
    arm = Path(registration["run_root"]) / "full"
    reader = SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    if args.cohort is not None:
        arrivals_path = Path(registration["workflow_arrivals_path"])
        arrivals = json.loads(arrivals_path.read_bytes())
        if arrivals != registration["workflow_arrivals"]:
            raise ValueError("phase arrivals differ from the serving registration")
        result["observation_phases"] = observation_phases(
            result, arrivals, json.loads(args.cohort.read_bytes()),
            reader(arm / "replay/runtime_events.replay.jsonl"),
        )
        result["phase_inputs_sha256"] = {
            str(path.resolve()): sha256_file(path)
            for path in (args.cohort, arrivals_path)
        }
    if args.complete and any(
        Path(path).stat().st_size != prefix["complete_prefix_bytes"]
        or sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
        for path, prefix in reader.prefixes.items()
    ):
        raise ValueError("completed decision evidence changed during analysis")
    result.update(
        registration=str(args.registration.resolve()),
        registration_sha256=sha256_file(args.registration),
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, performance_accepted=False,
        analysis_sha256=sha256_file(Path(__file__)),
        complete_work_and_owned_exit_verified=args.complete and measured is None,
        complete_measured_work_and_owned_exit_verified=measured is not None,
    )
    if measured is not None:
        result["measured_execution_proof"] = {
            name: measured[name] for name in (
                "protocol", "certificate", "certificate_sha256", "cohort_sha256",
                "measured_requests", "measured_output_tokens", "actual_owned_exit_verified",
            )
        }
    write(args.output, result)
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
