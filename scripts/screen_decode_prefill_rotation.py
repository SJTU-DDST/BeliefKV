"""Screen work-neutral rotations of an existing decode prefix and prefill block."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from statistics import mean

import orjson

from scripts.audit_cohort_jct_paths import measured_plan
from scripts.d2_wait_dag import replay_request_shifts
from scripts.run_causal_gpu_replay import source_plan


def sealed_rows(path, evidence, kind=None):
    seal = evidence[str(path)]
    left = seal["complete_prefix_bytes"]
    digest = sha256()
    with path.open("rb") as stream:
        while left:
            line = stream.readline(left)
            if not line.endswith(b"\n"):
                raise ValueError(f"incomplete sealed record in {path}")
            left -= len(line)
            digest.update(line)
            if kind is None or line.startswith(kind):
                yield orjson.loads(line)
    if digest.hexdigest() != seal["complete_prefix_sha256"]:
        raise ValueError(f"changed sealed prefix in {path}")


@dataclass(frozen=True)
class Receipt:
    owner: tuple
    session: str
    generation: int
    completion: float
    output: int
    clock: str


@dataclass(frozen=True)
class Member:
    request: str
    workflow: str
    owner: tuple
    attempt: int
    session: str
    generation: int
    sequence: int
    launch_output: int
    before: int
    after: int
    delta: int

    @classmethod
    def parse(cls, row):
        owner = tuple(row[key] for key in ("workflow_id", "invocation_id", "context_id", "context_epoch"))
        return cls(
            row["request_id"], owner[0], owner, row["attempt_id"], row["session_id"],
            row["session_generation"], row["sequence_tokens_before"],
            row["output_tokens_before"],
            row["output_tokens_reported_before"], row["output_tokens_after"], row["token_delta"],
        )

    @property
    def identity(self):
        return self.request, self.owner, self.attempt, self.session, self.generation

    def continues(self, previous):
        return (
            self.identity == previous.identity and self.session and self.generation is not None
            and self.attempt is not None
            # The launch cursor may trail reported progress by an in-flight step.
            and self.sequence - self.launch_output == previous.sequence - previous.launch_output
            and self.before == previous.after and self.after == self.before + 1
            and self.delta == 1
        )


@dataclass(frozen=True)
class Batch:
    iteration: int
    phase: str
    launch: float
    start: float
    end: float
    members: tuple[Member, ...]

    @classmethod
    def parse(cls, row):
        members = tuple(Member.parse(item) for item in row["request_samples"])
        if (
            len(members) != row["batch_size"]
            or len({item.request for item in members}) != len(members)
            or any(item["phase"] != row["phase"] for item in row["request_samples"])
        ):
            raise ValueError("expected a native batch with distinct same-phase requests")
        return cls(
            row["forward_iter"], row["phase"], row["launch_monotonic_ms"],
            row["service_start_monotonic_ms"], row["complete_monotonic_ms"], members,
        )

    @property
    def cost(self):
        return self.end - self.start


def terminal_members(batch, receipts):
    result = []
    for member in batch.members:
        receipt = receipts.get(member.request)
        if receipt is None or not batch.start <= receipt.completion <= batch.end:
            continue
        if (
            (member.owner, member.session, member.generation)
            != (receipt.owner, receipt.session, receipt.generation)
            or member.after != receipt.output or member.after <= member.before
        ):
            continue
        result.append(member.request)
    return result


def stable_decode_launch_gap(prior, batch):
    """Uncovered host launch gap in adjacent decode turns with verified progress."""
    if (
        prior.phase != "decode" or batch.phase != "decode"
        or batch.iteration != prior.iteration + 1 or len(prior.members) != len(batch.members)
        or not all(member.continues(old) for member, old in zip(batch.members, prior.members))
    ):
        return None
    return max(0., batch.launch - prior.end)


def phase_rotations(batches, receipts, counts, cadence=None):
    """Yield at most one decode prefix ending at the first terminal per prefill episode."""
    prior, prefill, eligible = None, [], False
    for batch in batches:
        if prior is not None and not (prior.iteration < batch.iteration and prior.end <= batch.start):
            raise ValueError("native service intervals are not ordered")
        if prior is not None and cadence is not None:
            cadence["first_service_start_ms"] = cadence.get("first_service_start_ms", prior.start)
            cadence["last_service_end_ms"] = batch.end
            gap = stable_decode_launch_gap(prior, batch)
            if gap is not None:
                cadence["stable_decode_transitions"] += 1
                cadence["positive_gap_transitions"] += gap > 0.
                cadence["total_positive_gap_ms"] += gap
                cadence["max_gap_ms"] = max(cadence["max_gap_ms"], gap)
                cadence["stable_decode_worker_ms"] += batch.cost
        if batch.phase == "prefill":
            if prior is not None and prior.phase != "prefill":
                prefill = []
            if prior is not None and (prior.phase == "decode" or prefill):
                prefill.append((prior, batch))
        elif batch.phase == "decode":
            if prefill and prior.phase == "prefill":
                counts["decode_prefill_decode_episodes"] += 1
                left, first = prefill[0]
                victims = {
                    member.request: member
                    for _, item in prefill for member in item.members
                }
                start = {
                    member.identity: member for member in left.members
                    if member.request not in receipts or receipts[member.request].completion > first.launch
                }
                retained = tuple(member for member in batch.members if member.identity in start)
                incoming = tuple(member for member in batch.members if member.identity not in start)
                exact_roster = bool(start) and tuple(start) == tuple(member.identity for member in batch.members)
                continuous = bool(start) and len(retained) == len(start) and all(
                    member.continues(start[member.identity]) for member in retained
                )
                ready_from_prefill = all(
                    (prefill_member := victims.get(member.request)) is not None
                    and prefill_member.identity == member.identity
                    and prefill_member.after > prefill_member.before
                    and member.before == prefill_member.after
                    for member in incoming
                )
                counts["same_exact_ordered_roster"] += exact_roster
                counts["all_prior_live_decode_members_continue"] += continuous
                counts["post_prefill_has_a_new_decode_member"] += bool(incoming)
                counts["post_prefill_additions_all_released_by_this_prefill"] += bool(incoming) and ready_from_prefill
                eligible = (
                    continuous and ready_from_prefill and (exact_roster or bool(incoming))
                    and {member.request for member in retained}.isdisjoint(victims)
                    and {member.session for member in retained}.isdisjoint(member.session for member in victims.values())
                    and all(
                        member.session and member.generation is not None
                        and member.attempt is not None
                        and member.delta is not None and member.delta > 0
                        for _, item in prefill for member in item.members
                    )
                )
                counts["prior_cohort_disjoint_from_positive_prefill"] += eligible
                if eligible:
                    steps, cost, growth = 0, 0., 0
                    head = sum(item.cost for _, item in prefill)
                    # A worker interval starts after previous CPU result processing.
                    # In-flight launches are kept as a separate, weaker observation.
                    synchronous_start = first.launch >= left.end
                    prefill_terminals = {
                        rid for _, item in prefill for rid in terminal_members(item, receipts)
                    }
                    retained_ids = {member.request for member in retained}
            elif eligible:
                eligible = len(prior.members) == len(batch.members) and all(
                    member.continues(old) for member, old in zip(batch.members, prior.members)
                )
                if not eligible:
                    counts["cohort_changed_before_a_terminal"] += 1
            if eligible:
                steps += 1
                cost += batch.cost
                growth += len(retained_ids)
                terminals = terminal_members(batch, receipts)
                if terminals:
                    targets = [rid for rid in terminals if rid in retained_ids]
                    eligible = False
                    if not targets:
                        counts["only_new_members_finish_at_first_terminal"] += 1
                        prior = batch
                        continue
                    counts["prior_cohort_prefix_with_terminal"] += 1
                    counts["exact_cohort_prefix_with_terminal"] += exact_roster
                    counts["with_prior_result_before_prefill_launch"] += synchronous_start
                    counts["within_prefill_worker_budget"] += cost <= head
                    counts["within_budget_and_prior_result_visible"] += cost <= head and synchronous_start
                    yield {
                        "left": left.iteration,
                        "first_prefill": first.iteration,
                        "last_prefill": prefill[-1][1].iteration,
                        "terminal_decode": batch.iteration,
                        "wall_start": first.start,
                        "wall_end": batch.end,
                        "prefill_forwards": len(prefill),
                        "decode_forwards": steps,
                        "decode_member_count": len(batch.members),
                        "new_decode_member_count": len(incoming),
                        "additional_early_decode_token_slots": growth,
                        "prefill_worker_ms": head,
                        "decode_worker_ms": cost,
                        "prior_result_before_prefill_launch": synchronous_start,
                        "terminal_requests": targets,
                        "new_decode_requests": [member.request for member in incoming],
                        "new_decode_terminals": [rid for rid in terminals if rid not in retained_ids],
                        "prefill_requests": list(victims),
                        "prefill_terminals": sorted(prefill_terminals),
                        "running_requests": [member.request for member in batch.members],
                    }
        else:
            prefill, eligible = [], False
        prior = batch


def summarize_jct(savings, members, observed_ms):
    values = [savings[key] for key in members]
    reference = mean(observed_ms[key] for key in members)
    candidate = mean(observed_ms[key] - savings[key] for key in members)
    longest = sorted(members, key=observed_ms.__getitem__)[-1]
    remaining = [key for key in members if key != longest]
    return {
        "mean_baseline_s": reference / 1000.,
        "mean_conditional_s": candidate / 1000.,
        "mean_savings_s": mean(values) / 1000.,
        "percent_savings": 100. * mean(values) / reference,
        "improved": sum(value > 0. for value in values),
        "delayed": sum(value < 0. for value in values),
        "max_delay_s": -min(0., min(values)) / 1000.,
        "percent_savings_excluding_longest_baseline": (
            100. * sum(savings[key] for key in remaining) / sum(observed_ms[key] for key in remaining)
        ),
    }


def project(rotations, plan, times, receipts, groups, observed_ms, *, charge_all_prefill):
    shifts = Counter()
    unknown = set()
    for row in rotations:
        for request in row["terminal_requests"]:
            shifts[request] += row["prefill_worker_ms"]
        victims = row["prefill_requests"] if charge_all_prefill else row["prefill_terminals"]
        for request in victims:
            shifts[request] -= row["decode_worker_ms"]
            if request not in receipts:
                unknown.add(request)
    selected = {key: shift for key, shift in shifts.items() if key in plan.request_usage}
    savings = replay_request_shifts(plan, times, selected)
    return {
        "rotations": len(rotations),
        "promoted_unique_requests": len({key for row in rotations for key in row["terminal_requests"]}),
        "charged_unique_requests": len({key for row in rotations for key in (
            row["prefill_requests"] if charge_all_prefill else row["prefill_terminals"]
        )}),
        "affected_workflows_in_complete_dag": sum(value != 0. for value in savings.values()),
        "requests_charged_without_terminal_receipt": len(unknown),
        "changed_requests_outside_complete_dag": len(shifts.keys() - selected.keys()),
        "promoted_total_request_s": sum(row["prefill_worker_ms"] * len(row["terminal_requests"])
                                        for row in rotations) / 1000.,
        "charged_total_request_s": sum(row["decode_worker_ms"] * len(
            row["prefill_requests"] if charge_all_prefill else row["prefill_terminals"]
        ) for row in rotations) / 1000.,
        "cohorts": {
            name: summarize_jct(savings, group, observed_ms) for name, group in groups.items()
        },
        "workflow_savings_s": {key: value / 1000. for key, value in savings.items() if value},
    }


def run(paths, source):
    arm = Path(paths["arm"])
    seals = paths["input_jsonl_prefixes"]
    receipts = {}
    for row in sealed_rows(arm / "replay/native_requests.jsonl", seals):
        receipt = row["native_receipt"]
        key = row["request_id"]
        if key in receipts or receipt["request_id"] != key:
            raise ValueError("native receipt identity is not unique")
        receipts[key] = Receipt(
            tuple(receipt["identity"]), receipt["session_id"], receipt["session_generation"],
            row["native_complete_monotonic_ms"], row["output_tokens"], receipt["clock_domain"],
        )
    clocks = {row.clock for row in receipts.values()}
    if len(clocks) != 1:
        raise ValueError("expected one native receipt clock")
    counts = Counter()
    cadence = Counter()

    def service():
        for row in sealed_rows(arm / "server/runtime_audit.jsonl", seals, b'{"event":"gpu_service_sample"'):
            counts["native_batches"] += 1
            if row["monotonic_clock_domain"] not in clocks:
                raise ValueError("native service and request receipts use different clocks")
            yield Batch.parse(row)

    rotations = list(phase_rotations(service(), receipts, counts, cadence))
    if counts["native_batches"] != paths["native_launch_samples"]:
        raise ValueError("sealed service count differs from archived path audit")

    events, starts, ends = {}, {}, {}
    for row in sealed_rows(arm / "replay/runtime_events.replay.jsonl", seals):
        events[row["event_id"]] = row["ts_ms"]
        if row["kind"] == "workflow_start":
            starts[row["workflow_id"]] = row["ts_ms"]
        elif row["kind"] == "workflow_end":
            ends[row["workflow_id"]] = row["ts_ms"]
    fixed18 = paths["cohort"]["measured_workflow_ids"]
    fixed38 = fixed18 + paths["cohort"]["warmup_workflow_ids"]
    incomplete = {
        node.workflow_id for node in source.nodes
        if (node.event is not None and node.event.event_id not in events)
        or (node.request_id is not None and node.request_id not in receipts)
    }
    closed = sorted((starts.keys() & ends.keys()) - incomplete)
    plan = measured_plan(source, closed)
    times = {
        node.node_id: events[node.event.event_id] if node.event is not None
        else receipts[node.request_id].completion
        for node in plan.nodes
    }
    observed_ms = {key: ends[key] - starts[key] for key in closed}
    for workflow in fixed38:
        if abs(observed_ms[workflow] - paths["workflows"][workflow]["jct_ms"]) > 1e-3:
            raise ValueError("zero-shift evaluation must bind the original fixed-cohort JCT")
    if any(replay_request_shifts(plan, times, {}).values()):
        raise ValueError("zero-shift control changed workflow timing")
    groups = {"fixed18": fixed18, "fixed38": fixed38, "all_complete": closed}

    previous_end = float("-inf")
    for row in rotations:
        if previous_end > row["wall_start"]:
            raise ValueError("phase rotation intervals overlap")
        previous_end = row["wall_end"]
    exact = [row for row in rotations if not row["new_decode_member_count"]]
    split = [row for row in rotations if row["new_decode_member_count"]]
    bounded = [row for row in split if row["decode_worker_ms"] <= row["prefill_worker_ms"]]
    synchronous = [row for row in bounded if row["prior_result_before_prefill_launch"]]
    return {
        "arm": str(arm),
        "input_jsonl_prefixes": seals,
        "counts": dict(counts),
        "stable_decode_cadence": dict(cadence),
        "native_receipts": len(receipts),
        "arrived_workflows": len(starts),
        "closed_workflows_with_full_dag": len(closed),
        "fixed18": fixed18,
        "fixed38": fixed38,
        "projections": {
            "exact_roster_full_prefill_delay": project(
                exact, plan, times, receipts, groups, observed_ms, charge_all_prefill=True,
            ),
            "split_free_catchup_only_charge_prefill_terminals": project(
                split, plan, times, receipts, groups, observed_ms, charge_all_prefill=False,
            ),
            "split_free_catchup_full_prefill_delay": project(
                split, plan, times, receipts, groups, observed_ms, charge_all_prefill=True,
            ),
            "split_free_catchup_duration_bounded_full_prefill_delay": project(
                bounded, plan, times, receipts, groups, observed_ms, charge_all_prefill=True,
            ),
            "split_free_catchup_duration_bounded_prior_result_full_prefill_delay": project(
                synchronous, plan, times, receipts, groups, observed_ms, charge_all_prefill=True,
            ),
        },
        "episodes": rotations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", nargs=2, metavar=("NAME", "PATH_AUDIT"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be a new path")
    sources, arms = {}, {}
    for name, path in args.arm:
        body = Path(path).read_bytes()
        data = orjson.loads(body)
        if name in arms or data["group"] != "measured_plus_warmup":
            parser.error("each name must be distinct and bind a complete fixed-38 audit")
        evidence = data["source_evidence"]
        source = Path(evidence["source_arm"])
        if source not in sources:
            plan, _, verified = source_plan(source)
            sources[source] = plan, verified["composite_source_sha256"]
        plan, digest = sources[source]
        if digest != evidence["composite_source_sha256"]:
            raise ValueError("source plan differs from archived path audit")
        print(f"{name} {data['arm']}", flush=True)
        arms[name] = {**run(data, plan), "path_audit": path, "path_audit_sha256": sha256(body).hexdigest()}
    with args.output.open("xb") as stream:
        stream.write(orjson.dumps(arms, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE))


if __name__ == "__main__":
    main()
