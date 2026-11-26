"""Audit archived D2 forecasts, settled backups, releases and native service."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_staged_native_restore import transfer_key
from scripts.audit_tool_reentry_service import tool_identity, tool_reentries


REPLAY_EVENTS = {
    "workflow_start", "workflow_end", "llm_submit", "llm_result", "tool_start",
    "tool_end", "return", "invocation_cancel", "context_compact", "join_wait",
}
RELEASE_EVENTS = {"predicted_tool_kv_evicted", "parent_pressure_demoted"}
SHARED_TOOL_RELEASE = "tool_start_shared_kv_evicted"
FREE_GRANTS = {
    "agent_continuation_free_capacity_admitted", "agent_dispatch_free_capacity_admitted",
    "agent_join_free_capacity_admitted", "agent_join_siblings_coadmitted",
}
PERIODIC_GRANT = "agent_periodic_admission_decision"
PAUSE_EVENT = "agent_full_offload_paused"
PRESSURE_PAUSE = "workflow_remaining_pressure_pause"
TERMINAL_RELEASES = {
    "terminal_child_cache_reclaimed", "terminal_workflow_root_cache_reclaimed",
}
ADMISSION_TRACE = FREE_GRANTS | RELEASE_EVENTS | TERMINAL_RELEASES | {
    PERIODIC_GRANT, PAUSE_EVENT, PRESSURE_PAUSE, "agent_initial_registration_decision",
    "agent_join_blocked_capacity_admitted", SHARED_TOOL_RELEASE,
}
JOURNAL_EVENTS = RELEASE_EVENTS | {
    "admission_runtime_state", "tool_wait_forecast", "prepare_native_issued",
    "agent_predicted_tool_replacement", PERIODIC_GRANT,
    PRESSURE_PAUSE, "native_demand_restore_staged",
    "native_demand_restore_ready", SHARED_TOOL_RELEASE,
}
KIND_PREFIX = re.compile(rb'^\s*\{\s*"(event|kind|action)"\s*:\s*"([^"]+)"')
# Wall-only journal records near a client boundary are kept as ambiguous.
CLOCK_MARGIN_MS = 1.


class Archive:
    def __init__(self, arm):
        self.arm = arm
        self.inputs = {}

    def records(self, name, kinds=None, *, field="event", required=False):
        path = self.arm / name
        if not path.is_file():
            if required:
                raise FileNotFoundError(path)
            self.inputs[name] = {"status": "missing"}
            return
        allowed = {kind.encode() for kind in kinds} if kinds is not None else None
        discriminator = field.encode()
        digest, consumed, count = hashlib.sha256(), 0, 0
        before = path.stat()
        with path.open("rb") as stream:
            while consumed < before.st_size:
                line = stream.readline(before.st_size - consumed)
                if not line.endswith(b"\n"):
                    break
                consumed += len(line)
                digest.update(line)
                if not line.strip():
                    continue
                count += 1
                match = KIND_PREFIX.match(line) if allowed is not None else None
                if match is not None and match[1] == discriminator and match[2] not in allowed:
                    continue
                row = json.loads(line)
                if kinds is None or row.get(field) in kinds:
                    yield row
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino,
        ):
            raise ValueError(f"archive log changed during read: {path}")
        self.inputs[name] = {
            "status": "complete_lines" if consumed == before.st_size else "partial_tail",
            "complete_prefix_bytes": consumed, "records": count,
            "complete_prefix_sha256": digest.hexdigest(),
        }

    def metadata(self, name):
        path = self.arm / name
        if not path.is_file():
            self.inputs[name] = {"status": "missing"}
            return {}
        body = path.read_bytes()
        self.inputs[name] = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        return json.loads(body)


def context(row):
    return row.get("context_id"), row.get("context_epoch")


def owner(row):
    return row.get("workflow_id") or row.get("root_workflow_id"), row.get("invocation_id"), *context(row)


def attempt(row):
    key = (*owner(row), row.get("request_id"), row.get("attempt_id"),
           row.get("session_id"), row.get("session_generation"))
    for index, value in enumerate(key):
        if index in (3, 5, 7):
            if type(value) is not int or value < 0:
                return None
        elif type(value) is not str or not value:
            return None
    return key


@dataclass(eq=False)
class ToolCall:
    origin: dict | None
    start: float
    identity: tuple | None = None
    end: float | None = None
    first_forecast: float | None = None
    first_gate_pass: float | None = None
    first_gate_horizon: float | None = None
    logical_host_hit: bool = False
    prior_staged_ready: float | None = None
    prior_backup_ack: bool = False

    @property
    def has_host_evidence(self):
        return self.logical_host_hit or self.prior_staged_ready is not None or self.prior_backup_ack


def replay_tools(events):
    streams, submits = defaultdict(list), {}
    for row in events:
        if row["kind"] in ("llm_submit", "tool_start", "tool_end"):
            streams[row["workflow_id"], row["invocation_id"]].append(row)
        if row["kind"] == "llm_submit":
            rid = row["attributes"]["request_id"]
            if rid in submits:
                raise ValueError("duplicate replay request ID")
            submits[rid] = row

    calls, by_context, counts = [], defaultdict(list), Counter()
    for stream in streams.values():
        origin, tool_pairs = None, defaultdict(lambda: ([], []))
        for row in sorted(stream, key=lambda row: (row["ts_ms"], row.get("sequence", 0))):
            kind = row["kind"]
            if kind == "llm_submit":
                origin = row
                continue
            key = tool_identity(row)
            call_id = row["attributes"].get("tool_call_id")
            if kind == "tool_start":
                call = ToolCall(origin, row["ts_ms"], key)
                calls.append(call)
                if origin is not None:
                    by_context[context(origin)].append(call)
                if key is not None:
                    tool_pairs[key][0].append((call_id, call))
            elif key is not None:
                tool_pairs[key][1].append((call_id, row["ts_ms"]))
        for starts, ends in tool_pairs.values():
            if len(starts) == len(ends) == 1:
                (start_id, call), (end_id, ended) = starts[0], ends[0]
                if start_id == end_id and call.start <= ended:
                    call.end = ended
            elif len(starts) > 1 or len(ends) > 1:
                counts["ambiguous_tool_identity"] += len(starts)
            if not starts:
                counts["tool_ends_without_start"] += len(ends)
    return calls, by_context, submits, counts


def matching_call(by_context, row, when):
    if when is None:
        return None, "clock_unobserved"
    candidates = [
        call for call in by_context.get(context(row), ())
        if call.start <= when + CLOCK_MARGIN_MS
        and (call.end is None or when < call.end + CLOCK_MARGIN_MS)
        and (not row.get("workflow_id") or owner(row) == owner(call.origin))
    ]
    if len(candidates) != 1:
        return None, "no_open_call" if not candidates else "multiple_possible_calls"
    call = candidates[0]
    if call.end is None:
        return None, "tool_end_unobserved"
    if not call.start + CLOCK_MARGIN_MS <= when < call.end - CLOCK_MARGIN_MS:
        return None, "near_client_boundary"
    return call, "matched_open_call"


def forecast_gate(row, when, survival):
    """Evaluate the recorded hint at publication against its fixed reference horizon."""
    hint = NativeToolWaitHint(
        None, row["p10_ms"], row["p50_ms"], row["p90_ms"],
        row["observed_monotonic_ms"], row["expires_monotonic_ms"], "archived",
        release_cdf=tuple(map(tuple, row.get("release_cdf", ()))),
        residual_duration_mass=tuple(map(tuple, row.get("residual_duration_mass", ()))),
        residual_support_detail=row.get("residual_support_detail"),
    )
    if not hint.live(None, now_ms=when):
        return "not_live"
    horizon = row["tool_offload_wait_ms"]
    probability = hint.release_probability_within(horizon, now_ms=when)
    if hint.release_cdf:
        if probability is None:
            return "unsupported_residual"
        return "passed" if probability <= 1. - survival + 1e-12 else "below_survival"
    quantile = max((q for q in (.1, .5, .9) if q <= round(1. - survival, 12)), default=None)
    remaining = hint.remaining_quantile(quantile, now_ms=when) if quantile is not None else None
    if remaining is None:
        return "unsupported_residual"
    return "passed" if remaining >= horizon else "below_survival"


def selected_journal(archive):
    journal, offsets, state = defaultdict(list), [], {}
    timeline = []
    for sequence, row in enumerate(archive.records(
        "opportunities/admission_opportunities.jsonl", JOURNAL_EVENTS | ADMISSION_TRACE,
    )):
        kind = row["event"]
        if kind in ADMISSION_TRACE:
            if kind == PERIODIC_GRANT:
                resumed = set(row.get("resumed_program_ids", ()))
                timeline.append((sequence, {
                    "event": kind, "ts_ms": row.get("ts_ms"), "monotonic_s": row["monotonic_s"],
                    "programs": [p for p in row.get("programs", ()) if p["invocation_id"] in resumed],
                }))
            else:
                timeline.append((sequence, row))
        if kind == "admission_runtime_state":
            pair = row.get("ts_ms"), row.get("monotonic_ms")
            if all(map(finite_number, pair)):
                offsets.append(pair[0] - pair[1])
            state = row
        elif kind == PERIODIC_GRANT:
            journal[kind].append(row["monotonic_s"] * 1000.)
        elif kind in JOURNAL_EVENTS:
            journal[kind].append(row)
    offset = (
        statistics.median(offsets)
        if offsets and max(offsets) - min(offsets) <= CLOCK_MARGIN_MS else None
    )
    return journal, state, offset, timeline, {
        "paired_samples": len(offsets), "wall_offset_ms": distribution(offsets),
        "boundary_margin_ms": CLOCK_MARGIN_MS, "usable_offset": offset is not None,
    }


def matching_prepare(issued, ack):
    return (
        ack is not None and context(issued) == context(ack)
        and issued["node_id"] in ack["node_ids"]
        and ack["pool_bytes"].get("kv", 0) > 0 and issued["ts_ms"] <= ack["ts_ms"]
    )


def stage_acks(archive, journal):
    def transaction(row):
        return transfer_key({
            "submit_ts_ms": row["native_submit_ts_ms"],
            "node_ids": row["native_node_ids"], "actual_bytes": row["native_bytes"],
        })

    staged, ready, acks = {}, {}, {}
    counts = Counter()
    for kind, target in (("staged", staged), ("ready", ready)):
        for row in journal[f"native_demand_restore_{kind}"]:
            key = transaction(row)
            if key in target:
                raise ValueError(f"duplicate native {kind} restore transaction")
            target[key] = row
    for row in archive.records("server/transfer_telemetry.jsonl"):
        if (
            row.get("direction") == "h2d" and row.get("status") == "completed"
            and row.get("telemetry_origin") == "native_hicache_ack_v0520"
            and row.get("submit_ts_ms") is not None and row.get("node_ids")
        ):
            key = transfer_key(row)
            if key in staged:
                if key in acks:
                    raise ValueError("duplicate native H2D ACK")
                acks[key] = row
    verified = defaultdict(list)
    for key, row in staged.items():
        ack, done = acks.get(key), ready.get(key)
        members = tuple(map(attempt, row["requests"]))
        counts["staged_batches"] += 1
        if (
            not ack or ack.get("tagged_child_commits")
            or type(row.get("native_full_tokens")) is not int or row["native_full_tokens"] <= 0
            or ack.get("num_tokens_by_pool") != {"kv": row["native_full_tokens"]}
            or not finite_number(ack.get("complete_ts_ms"))
            or row["ts_ms"] < key[0] or ack["complete_ts_ms"] < key[0]
            or len(members) != row["native_batch_size"] or None in members
            or len(set(members)) != len(members)
        ):
            counts["staged_batches_without_exact_native_ack"] += 1
            continue
        counts["staged_batches_with_exact_native_ack"] += 1
        if (
            done and tuple(map(attempt, done["requests"])) == members
            and done["monotonic_clock_domain"] == row["monotonic_clock_domain"]
            and done["monotonic_ms"] >= row["monotonic_ms"] and done["ts_ms"] >= row["ts_ms"]
        ):
            counts["staged_batches_with_exact_native_ack_and_ready"] += 1
        else:
            done = None
        for member in members:
            verified[member[4]].append((member, row, ack, done))
    return verified, counts


def staged_restore_phases(transfers, *, submitted, launch, to_monotonic):
    """Use the first and last staged batch before the same request's launch."""
    result = {}
    for label, (stage, ack, ready) in (("first", transfers[0]), ("last", transfers[-1])):
        ack_at = to_monotonic(ack["complete_ts_ms"])
        if ack_at is not None and submitted <= ack_at <= launch:
            result[f"{label}_stage_ACK_to_launch"] = launch - ack_at
        if ready is not None and stage["monotonic_ms"] <= ready["monotonic_ms"] <= launch:
            result[f"{label}_stage_READY_to_launch"] = launch - ready["monotonic_ms"]
    prior_ready = transfers[0][2]
    repeat_wait, end = 0., None
    for stage, _, ready in transfers[1:]:
        if (
            prior_ready is not None and ready is not None
            and ready.get("waiting_checks", 0) > 0
            and prior_ready["monotonic_ms"] <= stage["monotonic_ms"] < ready["monotonic_ms"] <= launch
        ):
            start = stage["monotonic_ms"] if end is None else max(end, stage["monotonic_ms"])
            right = ready["monotonic_ms"]
            repeat_wait += max(0., right - start)
            end = right if end is None else max(end, right)
        prior_ready = ready
    if repeat_wait:
        result["repeat_staged_wait_with_recorded_deferral"] = repeat_wait
    return result


def forward_identity(row):
    domain, forward = row.get("monotonic_clock_domain"), row.get("forward_iter")
    if type(domain) is str and domain and type(forward) is int and forward > 0:
        return domain, forward
    return None


@dataclass(frozen=True)
class ServiceBatch:
    launch: float
    complete: float
    domain: str
    worker: float | None
    prefill_identity: tuple[str, int] | None


def request_evidence(archive):
    submissions, receipts, first, domains, counts = {}, {}, defaultdict(dict), set(), Counter()
    turnover, batch_counts = defaultdict(list), Counter()
    unidentified_progress = {}
    for row in archive.records("server/runtime_events.sglang.jsonl", {"llm_submit"}, field="kind"):
        rid = row["attributes"]["request_id"]
        if rid in submissions:
            raise ValueError("duplicate native queue registration")
        submissions[rid] = row
    for row in archive.records("replay/native_requests.jsonl"):
        rid = row["request_id"]
        if rid in receipts:
            raise ValueError("duplicate native completion receipt")
        if row.get("exact_tokens_verified") is True:
            receipts[rid] = row["native_receipt"]
    for row in archive.records("server/runtime_audit.jsonl", {"gpu_service_sample", "prefill_turnover_sample"}):
        identity = forward_identity(row)
        if row["event"] == "prefill_turnover_sample":
            turnover[identity].append(row)
            continue
        if identity is not None:
            batch_counts[identity] += 1
        launch, complete, domain = (
            row.get("launch_monotonic_ms"), row.get("complete_monotonic_ms"),
            row.get("monotonic_clock_domain"),
        )
        if not all(map(finite_number, (launch, complete))) or complete < launch or not domain:
            counts["invalid_service_clock_batches"] += 1
            continue
        worker = row.get("service_start_monotonic_ms")
        if not finite_number(worker) or not launch <= worker <= complete:
            worker = None
        prefill = (
            row.get("phase") in (None, "prefill")
            and row.get("timing_semantics_version") in (None, "gpu_service_interval_v1")
        )
        batch = ServiceBatch(launch, complete, domain, worker, identity if prefill else None)
        domains.add(domain)
        for member in row.get("request_samples", ()):
            if not any(
                type(member.get(field)) is int and member[field] > 0
                for field in ("token_delta", "output_token_delta")
            ):
                continue
            key = attempt(member)
            if key is None:
                counts["progress_samples_missing_attempt_identity"] += 1
                rid = member.get("request_id")
                if type(rid) is str and rid:
                    unidentified_progress[rid] = min(launch, unidentified_progress.get(rid, launch))
                continue
            existing = first[key[4]].get(key)
            if existing is None or launch < existing[0].launch:
                first[key[4]][key] = batch, False
            elif launch == existing[0].launch:
                first[key[4]][key] = existing[0], True
    # Keep the legacy first-service result, but withhold the selection join
    # when an earlier or simultaneous positive sample lacks an attempt key.
    for rid, launch in unidentified_progress.items():
        for key, (batch, _) in first.get(rid, {}).items():
            if launch <= batch.launch:
                first[rid][key] = batch, True
    counts["requests_with_positive_exact_attempt_samples"] = len(first)
    return submissions, receipts, first, domains, counts, turnover, batch_counts


def first_service(first, receipts, rid, expected_owner, domains, *, selection=None, require_prefill=False):
    if len(domains) != 1 or len(first.get(rid, ())) != 1:
        return None
    key, (batch, ambiguous_first) = next(iter(first[rid].items()))
    if require_prefill and (ambiguous_first or batch.prefill_identity is None):
        return None
    if selection is not None and (ambiguous_first or batch.prefill_identity != selection):
        return None
    if key[:4] != expected_owner:
        return None
    receipt = receipts.get(rid)
    if receipt is not None and (
        tuple(receipt["identity"]) != key[:4] or receipt["clock_domain"] != batch.domain
        or (receipt["session_id"], receipt["session_generation"]) != key[-2:]
        or receipt["complete_monotonic_ms"] < batch.complete - CLOCK_MARGIN_MS
    ):
        return None
    return key, batch.launch, batch.complete, batch.worker


def positive_full_release(row):
    field = "freed_device_units" if row["event"] in TERMINAL_RELEASES else "freed_units"
    units = row.get(field, {}).get("0", 0)
    return units if type(units) is int and units > 0 else 0


def exact_tool_release(call, row):
    return (
        call.identity is not None and row.get("tool_call_id") == call.identity[1]
        and finite_number(started := row.get("tool_start_monotonic_ms"))
        and abs(started - call.start) <= CLOCK_MARGIN_MS
    )


def executed_release_quotes(releases, *, fixed18, fixed38):
    """Compare each tool episode's first physical release quote with its return."""
    groups = defaultdict(list)
    for call, releases_in_wait in releases.items():
        first = {}
        for row, at, _ in sorted(releases_in_wait, key=lambda item: item[1]):
            if row["event"] != "predicted_tool_kv_evicted":
                continue
            stage = row.get("dispatch_stage") or "unspecified"
            if stage == "tool_start" and not exact_tool_release(call, row):
                continue
            first.setdefault(stage, (at, row))
        if not first:
            continue
        first["all_stages"] = min(first.values(), key=lambda item: item[0])
        workflow = call.origin["workflow_id"]
        for stage, (at, row) in first.items():
            for name, members in (("all", None), ("fixed18", fixed18), ("fixed38", fixed38)):
                if members is None or workflow in members:
                    groups[name, stage].append((call.end - at, row))

    result = {"all": {}, "fixed18": {}, "fixed38": {}}
    for (group, stage), samples in sorted(groups.items()):
        counts = Counter(episodes=len(samples))
        excess, predicted = [], []
        observed_early = 0
        for remaining, row in samples:
            threshold = row.get("remaining_threshold_ms")
            if not finite_number(threshold) or threshold < 0:
                counts["without_valid_quote"] += 1
                continue
            slack = remaining - threshold
            excess.append(slack)
            if abs(slack) <= CLOCK_MARGIN_MS:
                counts["within_clock_margin"] += 1
                continue
            early = slack < 0
            counts["returned_before_quote" if early else "outlasted_quote"] += 1
            probability = row.get("predicted_completion_probability_within_threshold")
            if finite_number(probability) and 0. <= probability <= 1.:
                predicted.append(probability)
                observed_early += early
        result[group][stage] = {
            "counts": dict(counts),
            "observed_remaining_minus_quote_ms": distribution(excess),
            "probability_samples_with_determinate_outcome": len(predicted),
            "mean_predicted_early_return_probability": statistics.mean(predicted) if predicted else None,
            "observed_early_returns_with_probability": observed_early,
            "observed_early_return_fraction_with_probability": (
                observed_early / len(predicted) if predicted else None
            ),
        }
    return result


def shared_tool_releases(rows, by_context, *, offset):
    releases, counts = [], Counter()
    for row in rows:
        if not (units := positive_full_release(row)):
            continue
        counts["positive_release_records"] += 1
        counts["released_FULL_tokens"] += units
        if offset is None or not finite_number(row.get("ts_ms")):
            counts["clock_unobserved"] += 1
            continue
        at = row["ts_ms"] - offset
        donors = row.get("donors")
        if not isinstance(donors, list) or len(donors) < 2:
            counts["invalid_donor_group"] += 1
            continue
        calls, keys = [], []
        for donor in donors:
            if not isinstance(donor, dict):
                break
            key = attempt(donor)
            call, _ = matching_call(by_context, donor, at)
            if (
                key is None or call is None or not exact_tool_release(call, donor)
                or owner(call.origin) != key[:4]
                or call.origin["attributes"]["request_id"] != key[4]
            ):
                break
            keys.append(key)
            calls.append(call)
        if (
            len(calls) != len(donors) or len({key[6] for key in keys}) != len(calls)
            or len(set(calls)) != len(calls)
            or row.get("primary_request_id") not in {key[4] for key in keys}
        ):
            counts["donor_group_unverified"] += 1
            continue
        counts["all_donors_verified_in_tool_wait"] += 1
        calls.sort(key=lambda call: call.origin["attributes"]["request_id"])
        releases.append((tuple(calls), row, at, units))
    return releases, counts


def proactive_service_paths(timeline, releases, native, service_for, *, offset, window_ms, domains):
    """Find bounded grants after a physical release, honoring any recorded consumer."""
    if not releases:
        return []
    positions, previous, following, releases_in_order = {}, {}, {}, []
    controls, control_sequences, grants, grant_sequences = [], [], [], []
    interventions = defaultdict(list)
    prior = None
    prior_sequence = None
    for sequence, row in timeline:
        if prior_sequence is not None and prior_sequence in previous:
            following[prior_sequence] = row
        prior_sequence = sequence
        kind = row["event"]
        if kind in RELEASE_EVENTS or kind in TERMINAL_RELEASES or kind == SHARED_TOOL_RELEASE:
            if positive_full_release(row):
                positions[id(row)] = sequence
                releases_in_order.append(sequence)
                previous[sequence] = prior
            prior = row
            continue
        prior = row
        start = row.get("monotonic_s")
        wall = row.get("ts_ms")
        if not all(map(finite_number, (start, wall))):
            continue
        start *= 1000.
        published = wall - offset
        if published < start - CLOCK_MARGIN_MS:
            continue
        if kind == PERIODIC_GRANT:
            controls.append(start)
            control_sequences.append(sequence)
        if kind == PRESSURE_PAUSE:
            invocation = row.get("selected")
            if row.get("marked_for_pause") is False and type(invocation) is str:
                interventions[invocation].append((sequence, start))
            continue
        if kind in (PAUSE_EVENT, "agent_initial_registration_decision"):
            if (key := attempt(row["key"])) is not None:
                interventions[key[1]].append((sequence, start))
            continue
        for item in row.get("donors", ()):
            if (key := attempt(item["key"])) is not None:
                interventions[key[1]].append((sequence, start))
        for item in row["programs"]:
            if (key := attempt(item["key"] or {})) is None:
                continue
            interventions[key[1]].append((sequence, start))
            if kind == PERIODIC_GRANT and (
                item.get("active_after_pressure") is not True
                or item.get("marked_after_pressure") is True
                or item.get("application_ready") is not True
                or item.get("pending_before") is not True
                or item.get("active_before") is not False
            ):
                continue
            if kind == PERIODIC_GRANT or kind in FREE_GRANTS:
                grants.append((sequence, row, key, start, max(start, published)))
                grant_sequences.append(sequence)

    links = []
    for call, rows in releases.items():
        ordered = sorted(rows, key=lambda item: positions[id(item[0])])
        first, first_at, _ = ordered[0]
        last, last_at, _ = ordered[-1]
        recorded_consumer = None
        if any("tool_start_consumer_key" in row for row, _, _ in ordered):
            keys = {attempt(row.get("tool_start_consumer_key") or {}) for row, _, _ in ordered}
            times = [row.get("tool_start_release_monotonic_ms") for row, _, _ in ordered]
            if (
                len(keys) != 1 or None in keys or len(domains) != 1
                or any(row.get("monotonic_clock_domain") not in domains for row, _, _ in ordered)
                or any(not finite_number(at) for at in times)
                or times != sorted(times) or not call.start <= times[0] <= times[-1] < call.end
            ):
                continue
            recorded_consumer = keys.pop()
            first_at, last_at = times[0], times[-1]
        first_seq, last_seq = positions[id(first)], positions[id(last)]
        donor_owner = owner(call.origin)
        donor_request = call.origin["attributes"]["request_id"]
        released = sum(units for _, _, units in ordered)
        after = following.get(last_seq) or {}
        aggregate = after.get("event") == PAUSE_EVENT and after.get("source") == "tool_start_offload"
        pause = after if aggregate else (previous[first_seq] or {})
        pause_key = attempt(pause.get("key") or {}) if pause.get("event") == PAUSE_EVENT else None
        logical_delta = pause.get("released_logical_tokens")
        matched_pause = (
            pause_key is not None and pause_key[:5] == (*donor_owner, donor_request)
            and pause_key[-2:] == (first.get("session_id"), first.get("session_generation"))
            and pause.get("source") == ("tool_start_offload" if aggregate else "predicted_tool_offload")
            and pause.get("observed_missing_full_tokens") == (
                released if aggregate else positive_full_release(first)
            )
            and type(logical_delta) is int and logical_delta > 0
        )
        deadline = min(call.end - CLOCK_MARGIN_MS, last_at + window_ms)
        next_control = bisect_right(control_sequences, last_seq)
        limit = control_sequences[next_control] if next_control < len(controls) else float("inf")
        for idx in range(bisect_right(grant_sequences, last_seq), bisect_right(grant_sequences, limit)):
            seq, grant, key, start, published = grants[idx]
            rid = key[4]
            queued = native.get(rid)
            if (
                not last_at <= published < deadline or start < last_at - CLOCK_MARGIN_MS
                or recorded_consumer is not None and key != recorded_consumer
                or key[:2] == donor_owner[:2]
                or queued is None or owner(queued) != key[:4]
                or queued["ts_ms"] >= first["ts_ms"]
            ):
                continue
            gap = bisect_right(releases_in_order, seq) - bisect_right(releases_in_order, first_seq - 1)
            other_releases = gap - len(ordered)
            free, required = grant.get("remaining_before_tokens"), grant.get("required_tokens")
            budget_margin = (
                matched_pause and grant["event"] in FREE_GRANTS
                and type(free) is int and type(required) is int
                and 0 < required <= free < required + logical_delta
                and grant.get("remaining_after_tokens") == free - required
                and grant.get("native_capacity_authority_preserved") is True
            )
            observed = service_for(rid, require_prefill=recorded_consumer is not None)
            launch, worker = (
                (observed[1], observed[3]) if observed and observed[0] == key
                and observed[1] >= published else (None, None)
            )
            next_control = bisect_right(control_sequences, seq)
            changes = interventions[key[1]]
            next_change = bisect_right(changes, (seq, float("inf")))
            service_deadline = min(
                deadline,
                controls[next_control] if next_control < len(controls) else deadline,
                changes[next_change][1] if next_change < len(changes) else deadline,
            )
            links.append({
                "donor_request_id": donor_request, "donor_tool_id": call.identity[1],
                "consumer_key": key, "grant_event": grant["event"], "grant_journal_sequence": seq,
                **({"consumer_binding": "recorded"} if recorded_consumer is not None else {}),
                "tool_start_to_first_release_ms": first_at - call.start,
                "released_FULL_tokens": released,
                "release_to_grant_ms": published - last_at,
                "matched_logical_pause": matched_pause,
                "grant_budget_would_not_fit_without_recorded_pause_delta": budget_margin,
                "other_positive_release_records_through_grant": other_releases,
                "grant_to_first_launch_ms": launch - published if launch is not None else None,
                "grant_to_first_worker_service_ms": worker - published if worker is not None else None,
                "first_worker_service_before_return": worker < call.end - CLOCK_MARGIN_MS
                if worker is not None else None,
                "first_worker_service_in_bounded_window": worker < service_deadline
                if worker is not None else None,
            })
    donor_links = Counter((row["donor_request_id"], row["donor_tool_id"]) for row in links)
    consumer_links = Counter(row["consumer_key"] for row in links)
    for row in links:
        row["ambiguous_association"] = bool(
            donor_links[row["donor_request_id"], row["donor_tool_id"]] > 1
            or consumer_links[row["consumer_key"]] > 1
            or row["other_positive_release_records_through_grant"] > 0
        )
    return links


def proactive_service_summary(links):
    unique = [row for row in links if not row["ambiguous_association"]]
    served = [row for row in unique if row["first_worker_service_in_bounded_window"] is True]
    return {
        "candidate_associations": len(links),
        "unique_donor_tool_calls_with_candidates": len({
            (row["donor_request_id"], row["donor_tool_id"]) for row in links
        }),
        "ambiguous_associations": len(links) - len(unique),
        "one_to_one_associations": len(unique),
        "one_to_one_with_adjacent_logical_pause": sum(row["matched_logical_pause"] for row in unique),
        "one_to_one_with_free_grant_budget_margin": sum(
            row["grant_budget_would_not_fit_without_recorded_pause_delta"] for row in unique
        ),
        "one_to_one_with_first_worker_service_before_return": sum(
            row["first_worker_service_before_return"] is True for row in unique
        ),
        "one_to_one_with_bounded_first_worker_service": len(served),
        "one_to_one_with_unverified_first_worker_service": sum(
            row["grant_to_first_worker_service_ms"] is None for row in unique
        ),
        "one_to_one_release_to_grant_ms": distribution(row["release_to_grant_ms"] for row in unique),
        "bounded_grant_to_first_worker_service_ms": distribution(
            row["grant_to_first_worker_service_ms"] for row in served
        ),
        "bounded_release_to_first_worker_service_ms": distribution(
            row["release_to_grant_ms"] + row["grant_to_first_worker_service_ms"] for row in served
        ),
    }


def selection_span(row):
    phases = row.get("phases")
    selection = phases.get("selection") if isinstance(phases, dict) else None
    if isinstance(selection, dict):
        start, end = selection.get("start_monotonic_ms"), selection.get("end_monotonic_ms")
        if all(map(finite_number, (start, end))) and start <= end:
            return start, end
    return None


def selection_consumers(sample, identity, selection_end, calls, release, native, service_for):
    consumers, unmatched = [], Counter()
    request_ids = sample.get("request_ids")
    if not isinstance(request_ids, list) or not request_ids or not all(
        type(rid) is str and rid for rid in request_ids
    ) or len(set(request_ids)) != len(request_ids):
        return consumers, {"selection_request_ids_unverified": 1}
    first_release_wall, last_release = release
    sample_launch = sample.get("launch_monotonic_ms")
    donor_owners = {owner(call.origin)[:2] for call in calls}
    first_return = min(call.end for call in calls)
    for rid in request_ids:
        observed = service_for(rid, selection=identity)
        if observed is None:
            unmatched["first_service_not_joined"] += 1
            continue
        key, launch, _, worker = observed
        if key[:2] in donor_owners:
            unmatched["donor_invocation"] += 1
            continue
        if launch < selection_end or (sample_launch is not None and sample_launch != launch):
            unmatched["selection_launch_disagreement"] += 1
            continue
        ingress = native.get(rid)
        if (
            ingress is None or owner(ingress) != key[:4]
            or not finite_number(ingress.get("ts_ms")) or ingress["ts_ms"] >= first_release_wall
        ):
            unmatched["native_ingress_before_release_unverified"] += 1
            continue
        if worker is None or not last_release < worker < first_return - CLOCK_MARGIN_MS:
            unmatched["worker_start_before_return_unverified"] += 1
            continue
        consumers.append({"key": key, "first_worker_start_monotonic_ms": worker})
    return consumers, unmatched


def prewrite_shortfall_service(
    timeline, releases, native, service_for, turnover, batch_counts, *, offset, shared=(),
):
    """Group temporal observations by donor calls and sampled selection."""
    shortfalls = {
        id(row): ((call,), row, at, units)
        for call, rows in releases.items() for row, at, units in rows
        if row.get("tool_start_release_trigger") == "native_prewrite_shortfall"
        and owner(row) == owner(call.origin)
    }
    shortfalls.update({id(item[1]): item for item in shared})
    donor_nodes = defaultdict(list)
    for row_id, (calls, _, _, _) in shortfalls.items():
        donor_nodes[calls].append(row_id)

    def observation(calls, nodes, identity=None):
        primary = nodes[0][1].get("primary_request_id")
        call = next((call for call in calls if call.origin["attributes"]["request_id"] == primary), calls[0])
        return {
            "donor_request_id": call.origin["attributes"]["request_id"],
            "donor_tool_id": call.identity[1],
            "selection_identity": identity,
            "release_node_records": len(nodes),
            "released_FULL_tokens": sum(item[3] for item in nodes),
            "first_release_monotonic_ms": min(item[2] for item in nodes),
            "last_release_monotonic_ms": max(item[2] for item in nodes),
            "shared_donor_request_ids": [
                call.origin["attributes"]["request_id"] for call in calls
            ] if len(calls) > 1 else [],
            "consumers": [],
        }

    grouped_observations, bracketed_shortfalls = [], set()
    if shortfalls:
        positive = sorted((
            (row["ts_ms"] - offset, id(row))
            for _, row in timeline
            if row["event"] in RELEASE_EVENTS | TERMINAL_RELEASES | {SHARED_TOOL_RELEASE}
            and positive_full_release(row) and finite_number(row.get("ts_ms"))
        ), key=lambda item: item[0])
        times = [at for at, _ in positive]
        for identity, samples in turnover.items():
            if identity is None:
                continue
            spans = [span for sample in samples if (span := selection_span(sample)) is not None]
            # The selection brackets projected journal timestamps. Forward identity
            # joins the two native streams independently of that wall-clock bridge.
            bracketed = dict.fromkeys(
                row_id for start, end in spans
                for _, row_id in positive[bisect_left(times, start):bisect_right(times, end)]
            )
            donors = defaultdict(list)
            for row_id in bracketed:
                if row_id in shortfalls:
                    bracketed_shortfalls.add(row_id)
                    item = shortfalls[row_id]
                    donors[item[0]].append(item)
            for calls, nodes in donors.items():
                row = observation(calls, nodes, identity)
                reasons = []
                if len(samples) > 1:
                    reasons.append("multiple_selection_samples")
                service_batches = batch_counts[identity]
                if service_batches > 1:
                    reasons.append("multiple_service_batches")
                if len(bracketed) > len(nodes):
                    reasons.append("competing_positive_releases")
                if len(samples) == 1:
                    request_ids = samples[0].get("request_ids")
                    if isinstance(request_ids, list) and len(request_ids) > 1:
                        reasons.append("multiple_selected_requests")
                    if service_batches == 1:
                        release = min(item[1]["ts_ms"] for item in nodes), row["last_release_monotonic_ms"]
                        row["consumers"], row["unmatched_selection_members"] = selection_consumers(
                            samples[0], identity, spans[0][1], calls, release, native, service_for,
                        )
                        if len(row["consumers"]) > 1:
                            reasons.append("multiple_first_service_consumers")
                if not service_batches:
                    row["reason"] = "service_batch_unobserved"
                row["selection_spans_monotonic_ms"] = spans
                row["ambiguity_reasons"] = reasons
                grouped_observations.append((calls, row))

    observations, statuses = [], Counter()
    donor_selections = Counter(calls for calls, _ in grouped_observations)
    for calls, row in grouped_observations:
        if donor_selections[calls] > 1:
            row["ambiguity_reasons"].append("multiple_selections_for_donor_call")
        if row["ambiguity_reasons"]:
            row["status"] = "ambiguous"
        elif row["consumers"]:
            row["status"] = "single_observed"
        else:
            row["status"] = "unmatched"
        statuses[row["status"]] += 1
        observations.append(row)
    for calls, row_ids in donor_nodes.items():
        nodes = [shortfalls[row_id] for row_id in row_ids if row_id not in bracketed_shortfalls]
        if nodes:
            observations.append({
                **observation(calls, nodes), "status": "unmatched", "reason": "selection_unobserved",
            })
            statuses["unmatched"] += 1
    return {
        "verified_release_calls": len({call for calls in donor_nodes for call in calls}),
        "verified_release_donor_groups": len(donor_nodes),
        "verified_release_node_records": len(shortfalls),
        "sampled_selection_records": sum(map(len, turnover.values())),
        "release_calls_without_sampled_selection": len(
            {call for calls in donor_nodes for call in calls}
            - {call for calls in donor_selections for call in calls}
        ),
        "release_node_records_without_sampled_selection": len(shortfalls) - len(bracketed_shortfalls),
        "single_observed": statuses["single_observed"],
        "ambiguous": statuses["ambiguous"],
        "unmatched": statuses["unmatched"],
        "observations": observations,
    }


def measure(archive, *, cohort, survival):
    events = list(archive.records(
        "replay/runtime_events.replay.jsonl", REPLAY_EVENTS, field="kind", required=True,
    ))
    calls, by_context, submits, client_counts = replay_tools(events)
    rounds, _, round_counts = tool_reentries(events)
    by_origin = {item["origin_request_id"]: item for item in rounds}
    journal, state, offset, timeline, clock = selected_journal(archive)
    survival = survival if survival is not None else state.get("tool_offload_min_survival")
    if survival is not None and not 0 < survival <= 1:
        raise ValueError("invalid minimum tool survival")
    stages, stage_counts = stage_acks(archive, journal)
    native, receipts, service, domains, service_counts, turnover, batch_counts = request_evidence(archive)
    status = archive.metadata("server/native_telemetry_status.json")
    shared, shared_counts = shared_tool_releases(journal[SHARED_TOOL_RELEASE], by_context, offset=offset)
    shared_donors = defaultdict(list)
    for donor_calls, row, at, units in shared:
        for call in donor_calls:
            shared_donors[call].append((row, at, units))

    def mono(ts):
        return ts - offset if offset is not None and finite_number(ts) else None

    def locate(row):
        at = mono(row.get("ts_ms"))
        call, reason = matching_call(by_context, row, at)
        return call, reason, at

    def service_for(rid, *, selection=None, require_prefill=False):
        return (
            first_service(
                service, receipts, rid, owner(submits[rid]), domains, selection=selection,
                require_prefill=require_prefill,
            )
            if rid in submits else None
        )

    forecast_counts = Counter()
    for row in journal["tool_wait_forecast"]:
        call, reason, at = locate(row)
        forecast_counts[reason] += 1
        if call is None:
            continue
        # A refresh issued before this call cannot serve as its prediction.
        if not call.start <= row["observed_monotonic_ms"] <= at + CLOCK_MARGIN_MS:
            forecast_counts["issue_outside_matched_window"] += 1
            continue
        if call.first_forecast is None or at < call.first_forecast:
            call.first_forecast = at
        if survival is not None:
            gate = forecast_gate(row, max(at, row["observed_monotonic_ms"]), survival)
            forecast_counts[gate] += 1
            if gate == "passed" and (call.first_gate_pass is None or at < call.first_gate_pass):
                call.first_gate_pass = at
                call.first_gate_horizon = row["tool_offload_wait_ms"]

    acks = {}
    for row in archive.records("server/physical_action_ack.jsonl"):
        if row.get("action") == "PREPARE_HOST":
            if row["command_id"] in acks:
                raise ValueError("duplicate prepare ACK")
            acks[row["command_id"]] = row
    backups, prior_backups, node_backups = defaultdict(list), defaultdict(list), defaultdict(list)
    backup_counts = {"tool_wait": Counter(), "tool_start": Counter()}
    for row in journal["prepare_native_issued"]:
        ack = acks.get(row["command_id"])
        ack = ack if matching_prepare(row, ack) else None
        if ack:
            ack_ms = mono(ack["ts_ms"])
            prior_backups[context(ack)].append(ack_ms)
            if ack_ms is not None and row.get("node_creation_time") is not None:
                node = (*context(ack), row["node_id"], row["node_creation_time"])
                node_backups[node].append(ack_ms)
        source = row.get("source")
        if source not in backup_counts:
            continue
        call, reason, at = locate(row)
        counts = backup_counts[source]
        counts["issued"] += 1
        counts[reason] += 1
        counts["matched_ack"] += ack is not None
        if call is not None:
            ack_ms = mono(ack["ts_ms"]) if ack else None
            backups[call].append((row, at, ack_ms))
            counts["acked_before_tool_end"] += (
                ack_ms is not None and ack_ms < call.end - CLOCK_MARGIN_MS
            )

    for call in calls:
        if not call.origin:
            continue
        rid = call.origin["attributes"]["request_id"]
        ingress = native.get(rid)
        queued = mono(ingress["ts_ms"]) if ingress else None
        call.logical_host_hit = bool(
            queued is not None and queued < call.start - CLOCK_MARGIN_MS
            and owner(ingress) == owner(call.origin)
            and ingress["attributes"].get("cached_tokens_host", 0) > 0
        )
        own_service = service_for(rid)
        call.prior_staged_ready = max((
            done["monotonic_ms"]
            for key, _stage, _ack, done in stages.get(rid, ())
            if done and key[:4] == owner(call.origin)
            and done["monotonic_clock_domain"] in domains
            and (own_service is None or own_service[0] == key)
            and done["monotonic_ms"] < call.start
        ), default=None)
        call.prior_backup_ack = any(
            at is not None and at < call.start - CLOCK_MARGIN_MS
            for at in prior_backups.get(context(call.origin), ())
        )

    releases, proactive_releases, release_counts = defaultdict(list), defaultdict(list), defaultdict(Counter)
    by_beneficiary = defaultdict(list)
    for event in sorted(RELEASE_EVENTS):
        for row in journal[event]:
            if event == "parent_pressure_demoted" and row.get("source") != "tool_wait":
                continue
            units = positive_full_release(row)
            if not units:
                continue
            source = row.get("dispatch_stage") or event
            call, reason, at = locate(row)
            counts = release_counts[source]
            counts["positive_node_records"] += 1
            counts["released_tokens_in_all_records"] += units
            if row.get("tool_start_release_trigger") == "native_prewrite_shortfall":
                counts["native_prewrite_shortfall_node_records"] += 1
                counts["native_prewrite_shortfall_released_tokens"] += units
                if row.get("tool_start_smaller_shortfall_retry") is True:
                    counts["native_prewrite_smaller_shortfall_retry_nodes"] += 1
                    counts["native_prewrite_smaller_shortfall_retry_tokens"] += units
                if row.get("tool_start_reoffered_after_prepare_ack") is True:
                    counts["native_prewrite_reoffered_after_prepare_ack_nodes"] += 1
                    counts["native_prewrite_reoffered_after_prepare_ack_tokens"] += units
            counts[reason] += 1
            if call is None:
                continue
            releases[call].append((row, at, units))
            if source == "tool_start":
                if exact_tool_release(call, row):
                    proactive_releases[call].append((row, at, units))
                else:
                    counts["unverified_tool_start_identity"] += 1
            counts["released_tokens_in_matched_open_calls"] += units
            if event == "predicted_tool_kv_evicted":
                node = (*context(row), row["node_id"], row.get("creation_time"))
                counts["same_epoch_node_prepare_ACK_before_release"] += any(
                    ack_ms <= at for ack_ms in node_backups.get(node, ())
                )
                threshold = row.get("remaining_threshold_ms")
                if finite_number(threshold):
                    counts["recorded_cost_threshold"] += 1
                    counts["remaining_tool_time_exceeds_cost_threshold"] += (
                        call.end - at > threshold + CLOCK_MARGIN_MS
                    )
            rid = row.get("beneficiary_request_id")
            if rid:
                by_beneficiary[rid].append((call, row, at))

    consumers, seen = [], set()
    for rid, rows in by_beneficiary.items():
        observed = service_for(rid)
        key, launch = observed[:2] if observed is not None else (None, None)
        for call, row, at in sorted(rows, key=lambda item: item[2]):
            if (call, rid) in seen:
                continue
            seen.add((call, rid))
            after = bool(
                key and key[0:2] != owner(call.origin)[0:2]
                and row["beneficiary_context_id"] == key[2]
                and submits[rid]["ts_ms"] < at and launch > at + CLOCK_MARGIN_MS
            )
            consumers.append((call, rid, at, launch if after else None))
    handoffs = []
    for row in journal["agent_predicted_tool_replacement"]:
        if row.get("physical_capacity_authority") != "settled_private_release_then_fresh_native_budget":
            continue
        consumer, donor = attempt(row["consumer"]), attempt(row["donor"])
        if consumer is None or donor is None:
            continue
        when = row["monotonic_s"] * 1000.
        matches = [
            (call, at) for call, release, at in by_beneficiary[consumer[4]]
            if owner(release) == donor[:4] and call.origin["attributes"]["request_id"] == donor[4]
            and (release.get("session_id"), release.get("session_generation")) == donor[-2:]
            and release.get("beneficiary_context_id") == consumer[2]
            and release.get("dispatch_stage") == "event_tool_replacement"
            and 0 <= when - at <= 50.
        ]
        if len({call for call, _ in matches}) != 1:
            continue
        call, at = max(matches, key=lambda item: item[1])
        observed = service_for(consumer[4])
        launch = observed[1] if observed and observed[0] == consumer and observed[1] >= when else None
        handoffs.append((call, consumer[4], at, launch))

    def consumer_stats(rows):
        served = [(call, at, launch) for call, _rid, at, launch in rows if launch is not None]
        return {
            "observations": len(rows), "unique_consumer_requests": len({row[1] for row in rows}),
            "first_positive_service_after_release": len(served),
            "first_positive_service_before_donor_tool_end": sum(
                launch < call.end - CLOCK_MARGIN_MS for call, _, launch in served
            ),
            "served_consumers_submitted_before_donor_tool_start": sum(
                launch is not None and submits[rid]["ts_ms"] < call.start
                for call, rid, _, launch in rows
            ),
            "release_to_first_service_ms": distribution(launch - at for _, at, launch in served),
        }

    def restore_stats(selected):
        grouped = defaultdict(list)
        for call, rows in selected.items():
            grouped[call.origin["attributes"]["request_id"]].extend(rows)
        delays, counts = defaultdict(list), Counter()
        phases = {"all": defaultdict(list), "fixed18": defaultdict(list), "fixed38": defaultdict(list)}
        for origin, rows in grouped.items():
            episode = by_origin.get(origin, {})
            rid, end = episode.get("request_id"), episode.get("client_ready_ts_ms")
            counts["released_origin_rounds"] += 1
            if rid is None:
                continue
            counts["resolved_pure_tool_successors"] += 1
            observed = service_for(rid)
            if observed is None or observed[1] < episode["client_submit_ts_ms"]:
                continue
            key, launch = observed[:2]
            counts["successors_with_first_positive_service"] += 1
            delays["tool_round_end_to_first_service_ms"].append(launch - end)
            delays["tool_round_end_to_submit_ms"].append(episode["client_submit_ts_ms"] - end)
            delays["submit_to_first_service_ms"].append(launch - episode["client_submit_ts_ms"])
            transfers = [
                (stage, ack, ready) for member, stage, ack, ready in stages.get(rid, ())
                if member == key and stage["monotonic_clock_domain"] in domains
                and episode["client_submit_ts_ms"] <= stage["monotonic_ms"] <= launch
            ]
            if not transfers:
                continue
            transfers.sort(key=lambda item: item[0]["monotonic_ms"])
            counts["successors_with_exact_staged_H2D_ACK"] += 1
            counts["successors_with_repeated_restore_stages"] += len(transfers) > 1
            nodes = {row["node_id"] for row, _, _ in rows}
            counts["successors_with_matching_released_and_restored_node_id"] += any(
                nodes.intersection(ack["node_ids"]) for _, ack, _ in transfers
            )
            staged, ack, ready = transfers[0]
            at = mono(ack["submit_ts_ms"])
            ack_at = mono(ack["complete_ts_ms"])
            if at is not None and episode["client_submit_ts_ms"] <= at <= launch:
                delays["submit_to_native_H2D_submit_ms"].append(at - episode["client_submit_ts_ms"])
                delays["native_H2D_submit_to_first_service_ms"].append(launch - at)
                if at <= ack_at <= launch:
                    delays["native_H2D_submit_to_ACK_ms"].append(ack_at - at)
                    delays["native_H2D_ACK_to_first_service_ms"].append(launch - ack_at)
            if ready is not None and ready["monotonic_ms"] <= launch:
                counts["successors_with_staged_ready_before_service"] += 1
            latency = staged_restore_phases(
                transfers, submitted=episode["client_submit_ts_ms"], launch=launch, to_monotonic=mono,
            )
            for name, workflows in (("all", None), ("fixed18", fixed18), ("fixed38", fixed38)):
                if workflows is None or episode["workflow_id"] in workflows:
                    for metric, delay in latency.items():
                        phases[name][metric].append(delay)
        return {
            "counts": dict(counts),
            "latencies": {key: distribution(values) for key, values in sorted(delays.items())},
            "staged_restore_phases_ms": {
                group: {key: distribution(values) for key, values in sorted(metrics.items())}
                for group, metrics in phases.items()
            },
        }

    def summarize_calls(selected):
        selected = set(selected)
        closed = [call for call in selected if call.end is not None]
        backed = [call for call in selected if call.has_host_evidence]
        observed_fit = [
            call for call in closed if call.first_gate_pass is not None
            and call.end - call.first_gate_pass > call.first_gate_horizon + CLOCK_MARGIN_MS
        ]
        release_paths = {}
        for label, kinds in (
            ("any_observed", RELEASE_EVENTS), ("predicted", {"predicted_tool_kv_evicted"}),
            ("tool_start", {"predicted_tool_kv_evicted"}),
            ("allocation_pressure", {"parent_pressure_demoted"}),
        ):
            group = {}
            for call in selected:
                group_rows = [
                    item for item in releases.get(call, ())
                    if item[0]["event"] in kinds
                    and (label != "tool_start" or item[0].get("dispatch_stage") == "tool_start")
                ]
                if group_rows:
                    group[call] = group_rows
            release_paths[label] = {
                "calls": len(group),
                "node_records": sum(map(len, group.values())),
                "FULL_tokens": sum(units for rows in group.values() for _, _, units in rows),
                "start_to_first_release_ms": distribution(
                    min(at for _, at, _ in rows) - call.start for call, rows in group.items()
                ),
                "released_FULL_tokens_per_call": distribution(
                    sum(units for _, _, units in rows) for rows in group.values()
                ),
                "last_release_to_tool_end_ms": distribution(
                    call.end - max(at for _, at, _ in rows) for call, rows in group.items()
                ),
            }
        return {
            "tool_starts": len(selected), "tool_ends_matched": len(closed),
            "starts_with_origin_request": sum(call.origin is not None for call in selected),
            "tool_duration_ms": distribution(call.end - call.start for call in closed),
            "tool_duration_ge_1000ms": sum(call.end - call.start >= 1000. for call in closed),
            "tool_duration_ge_2000ms": sum(call.end - call.start >= 2000. for call in closed),
            "prior_Host_evidence": {
                "any": len(backed), "logical_Host_hit_at_origin_ingress": sum(
                    call.logical_host_hit for call in backed
                ),
                "verified_staged_origin_H2D_ACK_and_ready": sum(
                    call.prior_staged_ready is not None for call in backed
                ),
                "verified_staged_origin_ready_within_2s_before_start": sum(
                    call.prior_staged_ready is not None and call.start - call.prior_staged_ready <= 2000.
                    for call in backed
                ),
                "same_epoch_explicit_prepare_ACK": sum(call.prior_backup_ack for call in backed),
            },
            "call_forecast_coverage": {
                label: {
                    "before_return": sum(getattr(call, field) is not None for call in selected),
                    "within_400ms": sum(
                        getattr(call, field) is not None and getattr(call, field) - call.start <= 400.
                        for call in selected
                    ),
                    "within_100ms": sum(
                        getattr(call, field) is not None and getattr(call, field) - call.start <= 100.
                        for call in selected
                    ),
                    "start_to_first_ms": distribution(
                        getattr(call, field) - call.start for call in selected
                        if getattr(call, field) is not None
                    ),
                }
                for label, field in (
                    ("accepted_hint", "first_forecast"), ("fixed_reference_survival_pass", "first_gate_pass"),
                )
            },
            "fixed_gate_label_screen": {
                "observed_residual_exceeds_horizon": len(observed_fit),
                "within_100ms_and_observed_residual_exceeds_horizon": sum(
                    call.first_gate_pass - call.start <= 100. for call in observed_fit
                ),
                "within_100ms_over_horizon_with_prior_Host_evidence": sum(
                    call.first_gate_pass - call.start <= 100. and call.has_host_evidence
                    for call in observed_fit
                ),
                "within_100ms_over_horizon_with_staged_ready_in_preceding_2s": sum(
                    call.first_gate_pass - call.start <= 100. and call.prior_staged_ready is not None
                    and call.start - call.prior_staged_ready <= 2000. for call in observed_fit
                ),
            },
            "calls_with_prepare_issue": sum(call in backups for call in selected),
            "calls_with_prepare_ACK_before_end": sum(
                any(ack is not None and ack < call.end - CLOCK_MARGIN_MS for _, _, ack in backups.get(call, ()))
                for call in selected
            ),
            "release_paths": release_paths,
        }

    fixed18 = set(cohort.get("measured_workflow_ids", ()))
    fixed38 = fixed18 | set(cohort.get("warmup_workflow_ids", ()))
    groups = {"all": calls}
    for name, workflows in (("fixed18", fixed18), ("fixed38", fixed38)):
        if workflows:
            groups[name] = [call for call in calls if call.origin and call.origin["workflow_id"] in workflows]
    control_ms = state.get("agent_control_interval_ms", 2000)
    if not finite_number(control_ms) or control_ms <= 0:
        raise ValueError("invalid control interval for tool-start service attribution")
    links = proactive_service_paths(
        timeline, proactive_releases, native, service_for, offset=offset, window_ms=control_ms,
        domains=domains,
    )
    link_groups = {
        "all": proactive_service_summary(links),
        **{
            name: proactive_service_summary([row for row in links if row["consumer_key"][0] in workflows])
            for name, workflows in (("fixed18_consumers", fixed18), ("fixed38_consumers", fixed38))
            if workflows
        },
    }
    arrivals, finishes = {}, {}
    for row in events:
        if row["kind"] == "workflow_start":
            arrivals[row["workflow_id"]] = row["ts_ms"]
        elif row["kind"] == "workflow_end" and row["attributes"].get("outcome") == "completed":
            finishes[row["workflow_id"]] = row["ts_ms"]
    jct = {}
    for name, workflows in (("fixed18", fixed18), ("fixed38", fixed38)):
        if workflows:
            closed = workflows & arrivals.keys() & finishes.keys()
            jct[name] = {
                "completed": len(closed), "members": len(workflows),
                "mean_JCT_s": statistics.mean((finishes[w] - arrivals[w]) / 1000. for w in closed)
                if closed == workflows else None,
            }

    native_streams = {
        "audit": "server/runtime_audit.jsonl", "events": "server/runtime_events.sglang.jsonl",
        "action_ack": "server/physical_action_ack.jsonl", "transfer": "server/transfer_telemetry.jsonl",
    }
    return {
        "arm": str(archive.arm), "input_prefixes": archive.inputs,
        "wall_to_monotonic": clock, "observed_jct": jct,
        "policy": {
            "tool_offload_min_survival": survival,
            "tool_offload_interval_ms": state.get("tool_offload_interval_ms"),
            "agent_control_interval_ms": state.get("agent_control_interval_ms"),
            "tool_start_offload_enabled": state.get("tool_start_offload"),
            "tool_start_offload_counters_at_last_snapshot": {
                key: value for key, value in state.get("counts", {}).items()
                if key.startswith("tool_start_offload_")
            },
            "tool_start_nonblocking_allocations_at_last_snapshot": state.get(
                "tool_start_nonblocking_allocations"
            ),
            "tool_prefetch_enabled": state.get("tool_prefetch"),
            "last_application_snapshot_marked_final": state.get("final"),
            "tool_replacement_thread_cpu_s_at_last_snapshot": (
                state["agent_admission"]["counts"]["tool_event_replacement_cpu_ns"] / 1e9
                if "tool_event_replacement_cpu_ns" in (state.get("agent_admission") or {}).get("counts", {})
                else None
            ),
        },
        "native_writer_observation": {
            name: status.get(name) for name in (
                "dropped_records", "failed_records", "writer_error",
                "writer_queue_depth", "pending_batch_count", "pending_request_count",
            )
        },
        "native_stream_record_counts": {
            label: {
                "writer_snapshot": status.get("record_counts", {}).get(label),
                "records_read": archive.inputs[filename].get("records"),
            }
            for label, filename in native_streams.items()
        },
        "client_tool_diagnostics": client_counts, "pure_round_diagnostics": round_counts,
        "forecast_records_by_status": forecast_counts,
        "tool_wait_prepare_commands": backup_counts["tool_wait"],
        "tool_start_prepare_commands": backup_counts["tool_start"],
        "positive_node_release_records_by_stage": release_counts,
        "first_executed_release_quotes": executed_release_quotes(
            releases, fixed18=fixed18, fixed38=fixed38,
        ),
        "logical_pressure_pause_marks_by_state_source": Counter(
            row["tool_state"]["source"] for row in journal["workflow_remaining_pressure_pause"]
            if row.get("marked_for_pause") is True
        ),
        "periodic_logical_boundaries": len(journal["agent_periodic_admission_decision"]),
        "named_release_consumer_service": consumer_stats(consumers),
        "matched_event_replacement_grant_service": consumer_stats(handoffs),
        "tool_start_release_grant_service": {
            "verified_tool_start_release_calls": len(proactive_releases),
            "observation_limit_ms": control_ms,
            "summaries": link_groups,
            "candidate_links": links,
        },
        "tool_start_prewrite_shortfall_service": prewrite_shortfall_service(
            timeline, proactive_releases, native, service_for, turnover, batch_counts, offset=offset,
            shared=shared,
        ),
        "tool_start_shared_release_counts": shared_counts,
        "event_replacement_markers": len(journal["agent_predicted_tool_replacement"]),
        "native_service_coverage": service_counts, "native_staged_restore_coverage": stage_counts,
        "released_round_restore": {
            "predicted": restore_stats({
                call: [item for item in rows if item[0]["event"] == "predicted_tool_kv_evicted"]
                for call, rows in releases.items()
                if any(item[0]["event"] == "predicted_tool_kv_evicted" for item in rows)
            }),
            "tool_start": restore_stats(proactive_releases),
            "tool_start_shared": restore_stats(shared_donors),
            "any_observed": restore_stats(releases),
        },
        "call_counts_and_timing": {name: summarize_calls(rows) for name, rows in groups.items()},
    }


SCOPE = [
    "All counts use sealed archive prefixes. Missing streams and unmatched boundaries leave coverage unknown.",
    "Accepted hints are publications within a single observed tool interval. The fixed reference gate "
    "uses the archived forecast horizon. Per-candidate cost, support, allocator and demand checks are separate.",
    "Origin Host hits and completed staged restores establish earlier Host evidence. Private settled "
    "residency and native release eligibility at TOOL_START require a contemporaneous allocator observation.",
    "Prepare ACKs are Host copies. Positive native node releases are cumulative and can repeat "
    "physical pages. Allocation-pressure releases have no general periodic-control or beneficiary link.",
    "Executed release quotes use the first physical release per verified closed tool episode, "
    "globally or within each dispatch stage. The outcome tests the logged horizon on that action. "
    "Probability summaries use only releases with a logged probability and determinate tool return. "
    "Shared-owner releases and episodes without an observed physical release are outside this metric.",
    "Named consumers have exact request and unambiguous attempt/session progress after the release. "
    "Event replacement additionally requires a close explicit donor/consumer grant. Exclusive page "
    "reuse and a causal improvement in first-service time remain unknown.",
    "TOOL_START temporal links use native-registered requests and grants published no later than "
    "the first periodic decision, return or one control interval. First worker service is bounded "
    "by the next periodic decision, same-agent intervention, return and the same interval. "
    "Recorded beneficiaries require the same attempt and clock plus a positive native prefill. "
    "Competing observed releases or multiple candidate links are ambiguous. Requests that were "
    "already logically active and did not receive a new grant are outside this link metric.",
    "Native prewrite shortfall observations group validated tool-start release nodes by donor call "
    "and sampled prefill selection. They bracket wall-projected release records with native selection "
    "endpoints and join the first observed positive service by exact forward iteration and clock. "
    "They use native ingress before release and worker start before tool return, independent of grants. "
    "These are temporal observations only. Sampling is incomplete and allocation identity is unavailable.",
    "Shared prewrite releases are counted once. First consumer service must precede the earliest "
    "verified donor return, and recovery is tracked independently for each donor.",
    "Restore coverage is limited to demand staging with exact native ACKs. Matching node IDs is "
    "corroboration only. Untagged direct H2D, per-member bytes in shared batches and exposed GPU stall "
    "require additional telemetry. Restore phase durations use the scheduler launch of the first "
    "positive native batch. Repeat-stage wait includes intervals with a recorded deferral after "
    "a verified earlier READY.",
    "JCT uses completed workflows and replay start/end. This audit contains no counterfactual replay.",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, metavar="NAME=ARM")
    parser.add_argument("--cohort", action="append", default=[], metavar="NAME=COHORT_JSON")
    parser.add_argument("--minimum-survival", type=float, help="Override only the offline fixed-gate diagnostic")
    parser.add_argument("--output", type=Path, help="Fresh JSON path; defaults to stdout")
    args = parser.parse_args()

    def named_paths(items):
        result = {}
        for item in items:
            name, sep, path = item.partition("=")
            if not (name and sep and path) or name in result:
                parser.error("expected unique NAME=PATH arguments")
            result[name] = Path(path).resolve()
        return result

    arms, cohorts = named_paths(args.arm), named_paths(args.cohort)
    if set(cohorts) - set(arms) or args.output is not None and args.output.exists():
        parser.error("cohorts must match arm names and the output path must be fresh")
    result = {"schema_version": 1, "scope": SCOPE, "arms": {}}
    for name, path in arms.items():
        cohort_path = cohorts.get(name)
        body = cohort_path.read_bytes() if cohort_path is not None else None
        report = measure(Archive(path), cohort=json.loads(body) if body else {}, survival=args.minimum_survival)
        if body is not None:
            report["cohort"] = {"path": str(cohort_path), "sha256": hashlib.sha256(body).hexdigest()}
        result["arms"][name] = report
    text = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output is None:
        sys.stdout.write(text)
    else:
        with args.output.open("x") as stream:
            stream.write(text)


if __name__ == "__main__":
    main()
