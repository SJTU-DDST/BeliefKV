#!/usr/bin/env python3
"""Compare optional decay against the pinned author's actual scheduled check."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import random
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet, ThunderProgram

AUTHOR_COMMIT = "7ddc8610270e56d3b109eed8796b3a4360fc67c9"
AUTHOR_FILES = (
    "ThunderAgent/backend/state.py",
    "ThunderAgent/scheduler/router.py",
    "ThunderAgent/program/state.py",
)


def author_sources(checkout):
    commit = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
    ).strip()
    if commit != AUTHOR_COMMIT:
        raise ValueError("author checkout differs from the declared public commit")
    sources = {}
    for name in AUTHOR_FILES:
        original = subprocess.check_output(
            ["git", "-C", str(checkout), "show", AUTHOR_COMMIT + ":" + name],
        )
        expected = hashlib.sha256(original).hexdigest()
        if sha256_file(checkout / name) != expected:
            raise ValueError("author source has local modifications: " + name)
        sources[name] = expected
    return sources


async def compare(seed, cases):
    from ThunderAgent.backend.state import BackendState
    from ThunderAgent.program.state import Program, ProgramState, ProgramStatus
    from ThunderAgent.scheduler.router import MultiBackendRouter, PausedInfo

    rng = random.Random(seed)
    transitions = {"resumed": 0, "paused_acting": 0, "marked_reasoning": 0}
    fixture_digest = hashlib.sha256()
    now = 1000.
    for sample in range(cases):
        capacity, weight = rng.randint(500, 30000), rng.choice((.25, 1., 2.))
        entries = [
            {
                "identity": str(index), "tokens": rng.randint(50, 6000),
                "active": rng.random() < .6, "acting": rng.random() < .5,
                "steps": rng.randint(1, 5),
                "acting_since": now - rng.choice((0., .1, .5, 1., 2., 5., 20., 999.)),
            }
            for index in range(rng.randint(1, 40))
        ]
        fixture_digest.update(json.dumps(
            [capacity, weight, entries], sort_keys=True, separators=(",", ":"),
        ).encode())

        async def fetch_metrics():
            return True

        metrics = NS(
            cache_config=NS(total_tokens_capacity=capacity),
            healthy=True, fetch_metrics=fetch_metrics,
        )
        backend = BackendState(
            "cpu-fixture", tool_coefficient=weight, metrics_client=metrics,
            use_acting_token_decay=True,
        )
        router = MultiBackendRouter.__new__(MultiBackendRouter)
        router.backends, router.programs, router.global_waiting_queue = {"cpu-fixture": backend}, {}, {}
        router.pause_resume_lock = asyncio.Lock()
        candidate = ThunderActingDecayWorkingSet(capacity, acting_weight=weight)
        candidate._queue_sequence = len(entries)
        for index, entry in enumerate(entries):
            identity, active, acting = entry["identity"], entry["active"], entry["acting"]
            candidate.programs[identity] = ThunderProgram(
                identity, entry["tokens"], steps=entry["steps"], active=active,
                acting=acting, pending=not acting, queue_sequence=index + 1,
            )
            if acting:
                candidate.acting_since[identity] = entry["acting_since"]
            record = Program(
                identity, backend_url="cpu-fixture" if active else None,
                origin_backend=None if active else "cpu-fixture",
                total_tokens=entry["tokens"], step_count=entry["steps"],
                status=ProgramStatus.ACTING if acting else ProgramStatus.REASONING,
                state=ProgramState.ACTIVE if active else ProgramState.PAUSED,
                acting_since=entry["acting_since"] if acting else None,
                waiting_event=None if active else asyncio.Event(),
            )
            router.programs[identity] = record
            if active:
                backend.register_program(identity, record)
            else:
                router.global_waiting_queue[identity] = PausedInfo(
                    identity, entry["tokens"], now, "cpu-fixture", entry["steps"],
                )
        author_resumed = []
        resume = router._resume_program

        def record_resume(state, target_backend=None):
            author_resumed.append(state.program_id)
            resume(state, target_backend)

        router._resume_program = record_resume
        with patch("ThunderAgent.backend.state.time.time", return_value=now):
            expected_capacity = backend.remaining_capacity_with_decay()
            if candidate.remaining_capacity_with_decay(now=now) != expected_capacity:
                raise ValueError(f"author decayed capacity differs in fixture {sample}")
            if candidate.remaining_capacity() != backend.remaining_capacity():
                raise ValueError(f"author undiscounted capacity differs in fixture {sample}")
            resumed = candidate.tick(now=now)
            await router._scheduled_check()
        if tuple(author_resumed) != resumed:
            raise ValueError(f"author resume selection differs in fixture {sample}")
        for identity, record in router.programs.items():
            actual = candidate.programs[identity]
            expected = (
                record.state is ProgramState.ACTIVE, record.status is ProgramStatus.ACTING,
                record.marked_for_pause, record.total_tokens,
            )
            observed = (actual.active, actual.acting, actual.marked_for_pause, actual.tokens)
            if observed != expected:
                raise ValueError(
                    f"author post-control state differs in fixture {sample}/{identity}: "
                    f"{observed!r} != {expected!r}"
                )
            entry = entries[int(identity)]
            transitions["paused_acting"] += int(entry["active"] and entry["acting"] and not actual.active)
            transitions["marked_reasoning"] += int(actual.marked_for_pause)
        if candidate.remaining_capacity() != backend.remaining_capacity():
            raise ValueError(f"author final undiscounted capacity differs in fixture {sample}")
        transitions["resumed"] += len(resumed)
    return {
        "cases": cases, "seed": seed, "fixture_sha256": fixture_digest.hexdigest(),
        "transitions": transitions,
        "actual_author_methods": [
            "BackendState.remaining_capacity_with_decay",
            "MultiBackendRouter._scheduled_check",
            "MultiBackendRouter._greedy_resume",
            "MultiBackendRouter._pause_until_safe",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author-checkout", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.cases <= 10000:
        parser.error("preserve reports and use 1..10000 fixtures")
    checkout = args.author_checkout.resolve()
    sources = author_sources(checkout)
    sys.path.insert(0, str(checkout))
    logging.getLogger("ThunderAgent").setLevel(logging.ERROR)
    result = asyncio.run(compare(args.seed, args.cases))
    if author_sources(checkout) != sources:
        raise ValueError("author sources changed during comparison")
    result.update(
        author_commit=AUTHOR_COMMIT, author_checkout=str(checkout),
        author_source_sha256=sources,
        adaptation_source_sha256={
            name: sha256_file(ROOT / name) for name in (
                "scripts/qualify_thunder_acting_decay_author.py",
                "beliefkv/policy/paper_baselines.py",
                "beliefkv/runtime/paper_baseline.py",
                "beliefkv/experiments/paper_baselines.py",
                "tests/test_thunder_acting_decay.py",
            )
        },
        scope=[
            "Actual pinned author capacity and scheduled-check methods execute without source rewriting.",
            "Shared tokens are zero; one backend, exact observed token counts, synthetic CPU program states and fixed metric replies.",
            "Every fixture compares decayed capacity, resume order, full post-control state and undiscounted pressure capacity.",
            "A monotonic elapsed clock replaces the author's wall clock for the adapter's observed ACTING age.",
            "No HTTP server, GPU, physical KV transfer, whole workload, JCT or throughput qualification.",
        ],
        gpu_used=False, performance_accepted=False,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        name: result[name] for name in ("cases", "seed", "fixture_sha256", "transitions")
    }, indent=2))


if __name__ == "__main__":
    main()
