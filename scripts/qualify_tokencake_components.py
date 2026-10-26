#!/usr/bin/env python3
"""Compare a TokenCake policy port with pure definitions from pinned author files."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.policy import tokencake as port
from beliefkv.policy.tokencake_capacity import TokenCakeCapacityWindow
from beliefkv.policy.tokencake_lifecycle import (
    LifecycleEvent, LifecycleResult, LifecycleSettings, TokenCakeLifecycleRegistry,
)


COMMIT = "1901ebf31fdf63590148e61d5c2f65e9fb7ec010"


class _MetricNamespace:
    def __getattr__(self, name):
        return getattr(port.DecisionReason, name, name.lower())

    def __call__(self, value):
        try:
            return port.DecisionReason(value)
        except ValueError:
            return value


class _Metrics:
    def __init__(self):
        self.values = Counter()

    def count(self, key, count=1):
        self.values[key] += count

    def snapshot(self, live):
        return {"live": live, "counts": dict(self.values)}


def reference(provenance):
    evidence = json.loads(provenance.read_bytes())
    if evidence["commit"] != COMMIT or evidence["repository"] != "pku-lemonade/TokenCake":
        raise ValueError("TokenCake reference differs from the pinned public artifact")
    module = ModuleType("tokencake_pure_reference")
    module.__dict__.update(
        dataclass=dataclass, field=field, math=math, time=time, OrderedDict=OrderedDict,
        TokenCakeMetadata=port.TokenCakeMetadata, OffloadConfig=port.OffloadSettings,
        Metric=_MetricNamespace(), TokenCakeMetrics=_Metrics, _HISTORY_LIMIT=4096,
        _ATTACH_SECONDS=60., _TOMBSTONE_SECONDS=60.,
        LifecycleEventResult=LifecycleResult,
    )
    sys.modules[module.__name__] = module
    wanted = {
        "scheduling.py": {
            "request_score", "AgentHistory", "CapacityPlan", "partition_capacity",
            "SchedulingController",
        },
        "offload_policy.py": {"Pressure", "Benefit", "evaluate_benefit"},
        "lifecycle.py": {"DuplicateLifecycleError", "Lifecycle", "LifecycleRegistry"},
    }
    digests = {}
    for item in evidence["files"]:
        filename = Path(item["repository_path"]).name
        if filename not in wanted:
            continue
        payload = Path(item["path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError("pinned TokenCake source changed: " + filename)
        tree = ast.parse(payload)
        selected = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in wanted[filename]
        ]
        for node in selected:
            if node.name == "SchedulingController":
                node.body = [
                    method for method in node.body if isinstance(method, ast.FunctionDef)
                    and method.name in ("_consumption", "_update_available", "uncommitted_blocks")
                ]
        if {node.name for node in selected} != wanted[filename]:
            raise ValueError("pure author reference definitions are incomplete")
        tree.body = [
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *selected,
        ]
        exec(compile(ast.fix_missing_locations(tree), item["path"], "exec"), module.__dict__)
        digests[item["repository_path"]] = item["sha256"]
    if len(digests) != 3:
        raise ValueError("all three pinned author policy sources are required")
    return module, digests


def qualify(author, count):
    rng = random.Random(21)
    settings = port.OffloadSettings()
    history = port.DurationHistory()
    registry = author.LifecycleRegistry.__new__(author.LifecycleRegistry)
    registry.settings = SimpleNamespace(default_stall_s=1., ewma_alpha=.5)
    registry._history = OrderedDict()
    for _ in range(count):
        maximum = rng.randint(0, 20)
        metadata = port.TokenCakeMetadata(
            agent_type="kind" + str(rng.randrange(8)), agent_name="worker",
            importance=rng.uniform(0, 10), depth=rng.randint(0, maximum),
            in_degree=rng.randrange(6), out_degree=rng.randrange(6),
            similarity=rng.choice((0., 1., .25, .5, rng.uniform(.01, 1.))),
            application_started_at_s=rng.choice((0., 100.)), application_elapsed_s=rng.uniform(0, 1000),
            application_start_offset_s=rng.uniform(0, 120),
            application_max_depth=maximum, remaining_depth=rng.randint(0, maximum),
            critical_path=bool(rng.randrange(2)), near_completion=bool(rng.randrange(2)),
            join_group=rng.choice(("", "known_join")), dependency_depth=rng.randrange(10),
            fanout_width=rng.randrange(1, 8), memory_weight=rng.uniform(0, 5),
            offload_eligible=bool(rng.randrange(2)), reusable_prefix=bool(rng.randrange(2)),
        )
        arrival, now = rng.uniform(0, 1000), 1000.
        if port.request_score(metadata, arrival, now) != author.request_score(metadata, arrival, now):
            raise ValueError("request score differs from author source")
        observed = port.AgentHistory(
            samples=rng.randrange(100), input_tokens=rng.randrange(100000),
            output_tokens=rng.randrange(10000), completed=rng.randrange(100),
            preemptions=rng.randrange(10), deferrals=rng.randrange(10),
            duration=rng.uniform(0, 1000), depth=rng.randrange(100),
            in_degree=rng.randrange(100), out_degree=rng.randrange(100),
            elapsed=rng.uniform(0, 1000), remaining=rng.randrange(100),
        )
        counterpart = author.AgentHistory(**asdict(observed))
        arguments = metadata.importance, rng.randrange(100), rng.uniform(0, 1000)
        if observed.score(*arguments) != counterpart.score(*arguments):
            raise ValueError("agent type history differs from author source")
        scores = {str(index): rng.uniform(1, 100) for index in range(rng.randrange(12))}
        importance = {key: rng.uniform(0, 10) for key in scores}
        used = {key: rng.randrange(10000) for key in scores}
        args = scores, importance, used, rng.randrange(10000), rng.uniform(.05, .3), .75
        if asdict(port.partition_capacity(*args)) != asdict(author.partition_capacity(*args)):
            raise ValueError("integer capacity partition differs from author source")
        pressure = port.Pressure(
            rng.random(), *(rng.randrange(100) for _ in range(6)),
        )
        arguments = {
            "blocks": rng.randrange(100), "cpu_available": rng.randrange(100),
            "cpu_needed": rng.randrange(100), "duration": rng.uniform(0, 20),
            "transfer_time": rng.uniform(0, 10), "agent_score": rng.uniform(0, 30),
            "churn_penalty": rng.uniform(0, 5),
        }
        actual = port.public_offload_benefit(settings, metadata, pressure, **arguments)
        expected = author.evaluate_benefit(settings, metadata, pressure, **arguments)
        if actual.reason != expected.reason or actual.score != expected.score:
            raise ValueError("offload gate differs from author source")
        record = SimpleNamespace(
            metadata=metadata, start=SimpleNamespace(kind=rng.choice(("tool", "external"))),
            started_at=10.,
        )
        key = registry._history_key(record, record.start.kind)
        estimate = rng.choice((None, rng.uniform(0, 20)))
        predicted = registry._predict(record, SimpleNamespace(
            kind=record.start.kind, estimated_duration_s=estimate,
        ))
        if not math.isclose(
            history.predict(key, user_estimate=estimate), predicted, rel_tol=1e-12, abs_tol=1e-12,
        ):
            raise ValueError("duration forecast differs from author source")
        elapsed = rng.uniform(0, 20)
        registry._learn(record, record.started_at + elapsed)
        history.observe(key, elapsed_seconds=elapsed)
        if not math.isclose(history.history[key], registry._history[key], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("duration EWMA differs from author source")
        qualify_capacity_state(author, rng, scores, importance)
    return {
        "sampled_states": count, "request_score_comparisons": count,
        "agent_type_score_comparisons": count, "partition_comparisons": count,
        "offload_gate_comparisons": count, "duration_prediction_comparisons": count,
        "duration_update_comparisons": count,
        "capacity_availability_comparisons": count,
        "capacity_consumption_comparisons": 2 * count,
        "capacity_admission_bound_comparisons": 2 * count,
    }


def qualify_capacity_state(author, rng, scores, importance):
    total = rng.randrange(1, 10001)
    plan = port.partition_capacity(
        scores, importance, {key: rng.randrange(total) for key in scores},
        total, rng.uniform(.05, .3), .75,
    )
    used, remaining = Counter(), total
    for owner in (None, *scores, "retired_type"):
        count = rng.randrange(remaining + 1)
        used[owner] = count
        remaining -= count
    free = rng.randrange(remaining + 1)
    growth = {
        str(index): rng.randrange(total + 1) for index in range(rng.randrange(5))
    }
    waiting = {key: rng.randrange(10001) for key in scores if rng.randrange(2)}
    kind = rng.choice((*scores, "ordinary"))
    request = SimpleNamespace(request_id="request")
    request_priority, margin = rng.randrange(10001), rng.choice((0, 500, 1000))
    controller = author.SchedulingController.__new__(author.SchedulingController)
    controller.plan, controller._used = plan, used
    controller._growth_commitments = growth
    controller._progress_reservation = controller._reclaim_reservation = False
    controller.manager = SimpleNamespace(
        block_pool=SimpleNamespace(get_num_free_blocks=lambda: free),
    )
    controller.metadata = {"request": SimpleNamespace(agent_type=kind)}
    controller.settings = SimpleNamespace(priority_borrow_score_margin=margin)
    controller.waiting_critical = plan.critical.intersection(waiting)
    controller._waiting_scores = waiting
    controller.scores = {"request": request_priority}
    controller._update_available()
    actual = TokenCakeCapacityWindow(total, priority_borrow_score_margin=margin)
    actual.used.update(used)
    actual.growth.update(growth)
    actual.set_plan(plan)
    actual.set_waiting(waiting)
    actual.refresh(free)
    if (
        actual.uncommitted != controller.uncommitted_blocks
        or actual.shared_available != controller.shared_available
        or actual.reserved_available != controller.reserved_available
    ):
        raise ValueError("native-free and generation-commitment budgets differ from author source")
    demand = rng.randrange(total + 1)
    for borrow in (False, True):
        expected = controller._consumption(request, demand, borrow_reserved=borrow)
        expected = tuple(expected) if expected is not None else None
        observed = actual.consumption(kind, demand, score=request_priority, borrow_reserved=borrow)
        if observed != expected or actual.can_admit(
            kind, demand, score=request_priority, borrow_reserved=borrow,
        ) != (demand <= controller.uncommitted_blocks and expected is not None):
            raise ValueError("shared/reserved/borrow admission differs from author source")


class _Retention:
    def __init__(self):
        self.owned = Counter()
        self.operations = []

    def retain(self, keys):
        self.owned.update(keys)
        self.operations.append(("retain", tuple(sorted(keys))))
        return set(keys)

    def release(self, keys):
        self.owned.subtract(keys)
        if any(value < 0 for value in self.owned.values()):
            raise ValueError("lifecycle released unowned native retention")
        self.owned += Counter()
        self.operations.append(("release", tuple(sorted(keys))))

    def reset(self):
        self.owned.clear()
        self.operations.append(("reset",))


def _same_lifecycle_state(expected, actual, expected_manager, actual_manager):
    if expected.records.keys() != actual.records.keys() or set(expected._generations) != actual.generations:
        raise ValueError("lifecycle records or generation guards differ from author")
    for identifier, reference_record in expected.records.items():
        port_record = actual.records[identifier]
        first = {key: value for key, value in vars(reference_record).items() if key != "metadata"}
        second = {key: value for key, value in vars(port_record).items() if key != "metadata"}
        if first != second or reference_record.state != port_record.state:
            raise ValueError("lifecycle state or deadline differs from author")
    if list(expected._history.items()) != list(actual.history.history.items()):
        raise ValueError("lifecycle forecast history differs from author")
    if (
        expected_manager.owned != actual_manager.owned
        or expected_manager.operations != actual_manager.operations
    ):
        raise ValueError("native lifecycle ownership actions differ from author")


def qualify_lifecycle(author, count):
    rng, clock = random.Random(21), [10.]
    settings = LifecycleSettings()
    expected_manager, actual_manager = _Retention(), _Retention()
    expected = author.LifecycleRegistry(settings, expected_manager, clock=lambda: clock[0])
    actual = TokenCakeLifecycleRegistry(settings, actual_manager, clock=lambda: clock[0])
    # Ensure ownership transitions are exercised before randomized invalid,
    # duplicate and expired operations, rather than only comparing empty pools.
    for ordinal in range(32):
        identity, metadata = "retained-" + str(ordinal), port.TokenCakeMetadata(agent_type="worker")
        expected.associate(identity, SimpleNamespace(lifecycle_id=identity, **asdict(metadata)))
        actual.associate(identity, metadata)
        expected.generation_finished(identity, "completed", "prefix")
        actual.generation_finished(identity, "completed", "prefix")
        event = LifecycleEvent("stall_started", identity, "external", 10.)
        expected.apply(event)
        actual.apply(event)
        left, right = expected.records[identity], actual.records[identity]
        expected.retain_ready(left, {"first", "second"})
        actual.retain_ready(right, {"first", "second"})
        _same_lifecycle_state(expected, actual, expected_manager, actual_manager)
        if ordinal % 3 == 1:
            clock[0] = left.release_deadline
            expected.expire()
            actual.expire()
        elif ordinal % 3 == 2:
            expected.set_h2d_estimate(left, left.predicted_duration)
            actual.set_h2d_estimate(right, right.predicted_duration)
        _same_lifecycle_state(expected, actual, expected_manager, actual_manager)
        clock[0] += .25
        event = LifecycleEvent("stall_finished", identity)
        expected.apply(event)
        actual.apply(event)
        _same_lifecycle_state(expected, actual, expected_manager, actual_manager)
    actions = Counter()
    for _ in range(count):
        clock[0] += rng.choice((0., .001, .1, 1., 60., 61., 3601.))
        identity, action = "generation-" + str(rng.randrange(32)), rng.randrange(11)
        actions[action] += 1
        left = expected.records.get(identity)
        right = actual.records.get(identity)
        if action == 0:
            metadata = port.TokenCakeMetadata(
                agent_type=rng.choice(("", "worker", "reviewer")),
                agent_name=rng.choice(("", "anonymous")),
            )
            counterpart = SimpleNamespace(lifecycle_id=identity, **asdict(metadata))
            errors = []
            for invoke in (
                lambda: expected.associate(identity, counterpart),
                lambda: actual.associate(identity, metadata),
            ):
                try:
                    invoke()
                    errors.append(False)
                except ValueError:
                    errors.append(True)
            if errors[0] != errors[1]:
                raise ValueError("generation association differs from author")
        elif action == 1:
            cause = rng.choice(("completed", "aborted", "error"))
            snapshot = ("prefix", rng.randrange(100))
            expected.generation_finished(identity, cause, snapshot)
            actual.generation_finished(identity, cause, snapshot)
        elif action in (2, 3):
            event = LifecycleEvent(
                "stall_started" if action == 2 else "stall_finished",
                identity, rng.choice(("tool", "unknown_external")),
                rng.choice((None, .001, .1, 1., 10., 1000.)),
            )
            first, second = expected.apply(event), actual.apply(event)
            if (
                first.lifecycle_id, first.event, first.state, first.disposition, first.status_code,
            ) != (
                second.lifecycle_id, second.event, second.state, second.disposition, second.status_code,
            ):
                raise ValueError("lifecycle event disposition differs from author")
        elif action == 4 and left is not None and left.started_at is not None:
            seconds = rng.choice((0., .1, 1., 100.))
            expected.set_h2d_estimate(left, seconds)
            actual.set_h2d_estimate(right, seconds)
        elif action == 5 and left is not None:
            keys = {str(rng.randrange(8)) for _ in range(3)}
            if expected.retain_ready(left, keys) != actual.retain_ready(right, keys):
                raise ValueError("lifecycle retention differs from author")
        elif action == 6:
            expected.expire()
            actual.expire()
        elif action == 7:
            expected.invalidate_snapshots()
            actual.invalidate_snapshots()
        elif action == 8:
            expected_manager.reset()
            actual_manager.reset()
            expected.external_reset_succeeded()
            actual.external_reset_succeeded()
        elif action == 9 and left is not None:
            cause = rng.choice(("completed", "aborted", "reset"))
            expected._terminalize(left, cause, clock[0])
            actual.terminate(identity, cause)
        elif action == 10 and left is not None:
            expected.release(left)
            actual.release(right)
        _same_lifecycle_state(expected, actual, expected_manager, actual_manager)
    return {
        "lifecycle_transition_comparisons": count,
        "lifecycle_owned_release_scenarios": 32,
        "lifecycle_nonempty_retention_operations": sum(
            row[0] == "retain" and bool(row[1]) for row in expected_manager.operations
        ),
        "lifecycle_action_counts": dict(sorted(actions.items())),
        "lifecycle_scope": "association_events_expiry_retention_reset_and_generation_guards",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--states", type=int, default=1000)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.states <= 100000:
        raise ValueError("use a fresh output and a positive bounded state count")
    author, digests = reference(args.provenance)
    result = {
        "status": "pure_components_equal_to_pinned_author_source",
        "repository": "pku-lemonade/TokenCake", "commit": COMMIT,
        "source_sha256": digests, "seed": 21,
        **qualify(author, args.states), **qualify_lifecycle(author, args.states),
        "source_extraction": "only_declared_pure_definitions_no_serving_import_or_gpu_execution",
        "paper_predictive_upload_physical_path_qualified": False,
        "native_runtime_adapter_qualified": False,
        "performance_accepted": False,
        "port_sha256": hashlib.sha256((ROOT / "beliefkv/policy/tokencake.py").read_bytes()).hexdigest(),
        "capacity_port_sha256": hashlib.sha256(
            (ROOT / "beliefkv/policy/tokencake_capacity.py").read_bytes(),
        ).hexdigest(),
        "lifecycle_port_sha256": hashlib.sha256(
            (ROOT / "beliefkv/policy/tokencake_lifecycle.py").read_bytes(),
        ).hexdigest(),
    }
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
