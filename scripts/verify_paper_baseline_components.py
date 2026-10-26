#!/usr/bin/env python3
"""Compare policy components with pinned author code without importing engines."""

from __future__ import annotations

import argparse
import ast
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import logging
from pathlib import Path
import random
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.policy.paper_baselines import (
    ThunderProgram, ThunderWorkingSet, continuum_public_priority, continuum_public_ttl,
)
from beliefkv.runtime.continuum_pins import ContinuumPins

PINS = {
    "thunderagent": "7ddc8610270e56d3b109eed8796b3a4360fc67c9",
    "continuum": "316a58794a6ff86b216e579b74fd56ed0c5a911f",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_source(directory, relative, repository, evidence):
    commit = subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True,
    ).strip()
    if commit != PINS[repository]:
        raise ValueError("author checkout differs from the reviewed commit")
    data = subprocess.check_output(
        ["git", "-C", str(directory), "show", "HEAD:" + relative],
    )
    path = directory / relative
    if path.read_bytes() != data:
        raise ValueError("author source has local changes")
    evidence.append({
        "repository": repository, "commit": commit, "path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
    })
    return ast.parse(data)


def compile_nodes(module, nodes):
    document = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *deepcopy(nodes),
    ], type_ignores=[])
    ast.fix_missing_locations(document)
    exec(compile(document, "pinned-author-policy", "exec"), module.__dict__)


def native_thunder(source_root, evidence):
    root = source_root / "thunderagent"
    sources = {
        name: checked_source(root, name, "thunderagent", evidence)
        for name in (
            "ThunderAgent/program/state.py", "ThunderAgent/backend/state.py",
            "ThunderAgent/scheduler/router.py",
        )
    }
    module = types.ModuleType("_beliefkv_author_policy_reference")
    sys.modules[module.__name__] = module
    module.__dict__.update(
        asyncio=asyncio, dataclass=dataclass, field=field,
        Enum=__import__("enum").Enum, time=types.SimpleNamespace(time=lambda: 0.),
        logger=logging.getLogger("author-policy-reference"), BUFFER_PER_PROGRAM=100,
    )
    for relative, names in (
        ("ThunderAgent/program/state.py", ("ProgramStatus", "ProgramState", "Program")),
        ("ThunderAgent/backend/state.py", ("BackendState",)),
        ("ThunderAgent/scheduler/router.py", ("PausedInfo", "MultiBackendRouter")),
    ):
        nodes = [
            node for node in sources[relative].body
            if isinstance(node, ast.ClassDef) and node.name in names
            or isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in (
                    "DEFAULT_TOOL_COEFFICIENT", "BUFFER_PER_PROGRAM", "DECODE_BUFFER",
                ) for target in node.targets
            )
        ]
        for node in nodes:
            if isinstance(node, ast.ClassDef) and node.name == "MultiBackendRouter":
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method.body = [
                            child for child in method.body
                            if not isinstance(child, ast.ImportFrom)
                            or child.module != "backend.state"
                        ]
        compile_nodes(module, nodes)
    return module


async def compare_thunder(module, *, cases):
    rng = random.Random(21)
    results = []
    for case in range(cases):
        capacity = rng.randrange(50, 5001)
        weight = rng.choice((.5, 1., 1.5))
        policy = ThunderWorkingSet(capacity, acting_weight=weight)
        router = module.MultiBackendRouter.__new__(module.MultiBackendRouter)
        backend = module.BackendState.__new__(module.BackendState)
        backend.url, backend.tool_coefficient = "one", weight
        backend.metrics_client = types.SimpleNamespace(
            healthy=True, cache_config=types.SimpleNamespace(total_tokens_capacity=capacity),
        )
        backend._programs, backend.shared_tokens, backend.future_paused_tokens = {}, 0, 0
        backend.use_acting_token_decay = False
        router.backends, router.programs, router.global_waiting_queue = {"one": backend}, {}, {}
        router.pause_resume_lock = asyncio.Lock()
        router.acting_token_weight = weight
        for number in range(rng.randrange(1, 13)):
            identity = str(number)
            tokens = rng.randrange(1, 21) * 100
            active, acting = rng.choice((False, True)), rng.choice((False, True))
            steps = rng.randrange(1, 5)
            candidate = ThunderProgram(
                identity, tokens, steps=steps, acting=acting, active=active,
                pending=not acting,
            )
            policy.programs[identity] = candidate
            program = module.Program(
                identity, backend_url="one" if active else None,
                status=module.ProgramStatus.ACTING if acting else module.ProgramStatus.REASONING,
                state=module.ProgramState.ACTIVE if active else module.ProgramState.PAUSED,
                total_tokens=tokens, step_count=steps,
            )
            router.programs[identity] = program
            if active:
                backend.register_program(identity, program)
            else:
                router._add_to_global_waiting_queue_sync(identity, program, backend=None)
                policy._queue_sequence += 1
                candidate.queue_sequence = policy._queue_sequence
        resumed = []
        original_resume = router._resume_program

        def record_resume(state, target_backend=None):
            resumed.append(state.program_id)
            return original_resume(state, target_backend)

        router._resume_program = record_resume
        await router._greedy_resume()
        if backend.remaining_capacity() < 0:
            await router._pause_until_safe(backend)
        actual = policy.tick(now=0.)
        native_state = {
            identity: (
                item.backend_url is not None, item.marked_for_pause,
                item.status is module.ProgramStatus.ACTING,
            ) for identity, item in router.programs.items()
        }
        candidate_state = {
            identity: (item.active, item.marked_for_pause, item.acting)
            for identity, item in policy.programs.items()
        }
        if tuple(resumed) != actual or native_state != candidate_state:
            raise ValueError(f"ThunderAgent policy differs from author code at case {case}")
        if backend.remaining_capacity() != policy.remaining_capacity():
            raise ValueError("ThunderAgent capacity accounting differs from author code")
        results.append({"case": case, "programs": len(policy.programs), "equal": True})
    return results


async def compare_thunder_streaming(module, *, cases, events_per_case=40):
    rng, progress, completed = random.Random(21), 0, 0
    for case in range(cases):
        capacity = rng.randrange(500, 5001)
        policy = ThunderWorkingSet(capacity)
        router = module.MultiBackendRouter.__new__(module.MultiBackendRouter)
        backend = module.BackendState.__new__(module.BackendState)
        backend.url, backend.tool_coefficient = "one", 1.
        backend.metrics_client = types.SimpleNamespace(
            healthy=True, cache_config=types.SimpleNamespace(total_tokens_capacity=capacity),
        )
        backend._programs, backend.shared_tokens, backend.future_paused_tokens = {}, 0, 0
        backend.use_acting_token_decay = False
        router.backends, router.programs, router.global_waiting_queue = {"one": backend}, {}, {}
        router.pause_resume_lock = asyncio.Lock()
        router.acting_token_weight = 1.
        for number in range(rng.randrange(2, 10)):
            identity = str(number)
            tokens, active = rng.randrange(50, 501), number < 2
            policy.programs[identity] = ThunderProgram(
                identity, tokens, steps=2, active=active, pending=True,
            )
            program = module.Program(
                identity, backend_url="one" if active else None, total_tokens=tokens,
                step_count=2, status=module.ProgramStatus.REASONING,
                state=module.ProgramState.ACTIVE if active else module.ProgramState.PAUSED,
            )
            router.programs[identity] = program
            if active:
                backend.register_program(identity, program)
            else:
                router._add_to_global_waiting_queue_sync(identity, program, backend=None)
                policy._queue_sequence += 1
                policy.programs[identity].queue_sequence = policy._queue_sequence
        for event in range(events_per_case):
            running = [
                identity for identity, item in policy.programs.items()
                if item.active and item.pending and not item.acting
            ]
            if running:
                identity = rng.choice(running)
                item, original = policy.programs[identity], router.programs[identity]
                delta = rng.randrange(1, 501)
                policy.generated(identity, item.tokens + delta)
                router.update_program_tokens_streaming(original, delta)
                progress += 1
                if rng.random() < .4:
                    policy.completed(identity, item.tokens, now=event * 5.)
                    router.update_program_after_request(identity, original, original.total_tokens)
                    completed += 1
            elif active_acting := [
                identity for identity, item in policy.programs.items() if item.active and item.acting
            ]:
                identity = rng.choice(active_acting)
                item, original = policy.programs[identity], router.programs[identity]
                tokens = item.tokens + rng.randrange(1, 101)
                policy.arrive(identity, tokens, now=event * 5.)
                # Exact pretokenized request ingress, as declared for the port.
                original.total_tokens = tokens
                original.step_count += 1
                original.status = module.ProgramStatus.REASONING
            await router._greedy_resume()
            if backend.remaining_capacity() < 0:
                await router._pause_until_safe(backend)
            policy.tick(now=event * 5.)
            actual = {
                identity: (item.active, item.marked_for_pause, item.acting, item.tokens)
                for identity, item in policy.programs.items()
            }
            expected = {
                identity: (
                    item.backend_url is not None, item.marked_for_pause,
                    item.status is module.ProgramStatus.ACTING, item.total_tokens,
                )
                for identity, item in router.programs.items()
            }
            if actual != expected or policy.remaining_capacity() != backend.remaining_capacity():
                raise ValueError(
                    f"ThunderAgent streaming/completion control differs at case {case}, event {event}"
                )
    return {
        "cases": cases, "events_per_case": events_per_case,
        "control_state_comparisons": cases * events_per_case,
        "streaming_token_updates": progress, "completed_call_updates": completed,
        "all_equal": True,
        "scope": "author_streaming_and_completion_methods_interleaved_with_actual_capacity_control",
    }


def compare_continuum(source_root, evidence):
    tree = checked_source(
        source_root / "continuum", "vllm/v1/core/estimate_with_func.py", "continuum", evidence,
    )
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "ToolCallEstimator")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "set_up_pin")
    module = types.ModuleType("_beliefkv_continuum_reference")
    module.FIXED_THRESHOLD_CONTINUUM = 2.
    compile_nodes(module, [method])
    cases = []
    for identified in (False, True):
        for samples in ((), (.01,), (2.,), (2.01,), (1., 3.), (.01, 10.)):
            mean = sum(samples) / len(samples) if samples else None
            instance = types.SimpleNamespace(get_func_call_exec_time=lambda _tool: mean)
            request = types.SimpleNamespace(this_func_call="tool" if identified else None)
            expected = module.set_up_pin(instance, request)
            actual = continuum_public_ttl(samples, tool_identified=identified)
            if expected != actual:
                raise ValueError("public Continuum TTL differs from author code")
            cases.append({"identified": identified, "samples": samples, "equal": True})
    return cases


def compare_continuum_queue_and_pressure(source_root, evidence, *, cases):
    queue_tree = checked_source(
        source_root / "continuum", "vllm/v1/core/sched/request_queue.py",
        "continuum", evidence,
    )
    scheduler_tree = checked_source(
        source_root / "continuum", "vllm/v1/core/sched/scheduler.py",
        "continuum", evidence,
    )
    queue = next(
        node for node in ast.walk(queue_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "peek_request"
        and any(arg.arg == "pinned_requests" for arg in node.args.args)
    )
    victim = next(
        node for node in ast.walk(scheduler_tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "pop_running_request_based_on_last_step"
    )
    module = types.ModuleType("_beliefkv_continuum_queue_reference")
    compile_nodes(module, (queue, victim))

    class Queue(list):
        pass

    rng = random.Random(21)
    for case in range(cases):
        requests = Queue(
            types.SimpleNamespace(
                job_id=str(index), arrival_time=float(rng.randrange(10)),
                is_last_step=False,
            )
            for index in range(rng.randrange(1, 20))
        )
        requests.job_id_first_entry_time = {
            req.job_id: float(rng.randrange(10)) for req in requests
        }
        pinned = [
            (req, float(rng.randrange(10)))
            for req in requests if rng.choice((True, False))
        ]
        retained = {req.job_id for req, _deadline in pinned}
        expected = module.peek_request(requests, pinned, None, None)
        actual = min(enumerate(requests), key=lambda item: continuum_public_priority(
            ttl_retained=item[1].job_id in retained,
            program_arrival=requests.job_id_first_entry_time[item[1].job_id],
            native_position=item[0],
        ))[1]
        if actual is not expected:
            raise ValueError(f"public Continuum queue differs at case {case}")
        if not pinned:
            continue
        # The pinned victim branch is independently verifiable without running
        # preemption, which is not yet integrated into the shared engine.
        controller = types.SimpleNamespace(
            running=[requests[0]], pinned_requests=list(pinned),
        )
        expected, unpin = module.pop_running_request_based_on_last_step(
            controller, requests[0],
        )
        physical = ContinuumPins()
        physical.pins = {
            req.job_id: types.SimpleNamespace(expires_at=deadline)
            for req, deadline in pinned
        }
        physical.release = lambda identity, reason: identity
        actual = physical.relieve_pressure(running_requests=1)
        if not unpin or actual != expected.job_id:
            raise ValueError(f"public Continuum pin victim differs at case {case}")
    return {"queue_cases": cases, "all_equal": True, "running_preemption_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve the previous fidelity report")
    if not 1 <= args.cases <= 10000:
        raise ValueError("bounded fidelity corpus required")
    evidence = []
    module = native_thunder(args.source_root, evidence)
    thunder = asyncio.run(compare_thunder(module, cases=args.cases))
    thunder_streaming = asyncio.run(compare_thunder_streaming(module, cases=args.cases))
    continuum = compare_continuum(args.source_root, evidence)
    continuum_queue = compare_continuum_queue_and_pressure(
        args.source_root, evidence, cases=args.cases,
    )
    report = {
        "schema_version": 1, "source_evidence": evidence,
        "candidate_sha256": sha(ROOT / "beliefkv/policy/paper_baselines.py"),
        "verifier_sha256": sha(Path(__file__)),
        "thunderagent": {"cases": len(thunder), "all_equal": True},
        "thunderagent_streaming_and_completion": thunder_streaming,
        "runtime_adapter_sha256": sha(ROOT / "beliefkv/runtime/paper_baseline.py"),
        "continuum_public": {"cases": len(continuum), "all_equal": True},
        "continuum_public_queue_and_pin_victim": continuum_queue,
        "continuum_physical_sha256": sha(ROOT / "beliefkv/runtime/continuum_pins.py"),
        "author_imports_removed": (
            "Only in-method backend.state imports are omitted; the same public "
            "BUFFER_PER_PROGRAM=100 is supplied. No scheduling expressions change."
        ),
        "scope": "Decision-component fidelity only; runtime/GPU/JCT are unverified.",
        "gpu_used": False, "performance_accepted": False,
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
