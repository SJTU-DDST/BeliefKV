#!/usr/bin/env python3
"""Equal-work illustrative schedules, not a replay or a GPU performance estimate."""

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import ExecutionMode, InvocationState as State
from beliefkv.policy.workflow_completion import WorkflowCompletionOrder


def simulate(durations: dict[str, tuple[float, ...]], *, policy: str) -> dict:
    graph = NS(workflows={}, invocations={})
    pending, keys, services = [], {}, {}
    for workflow, requests in durations.items():
        identities = {f"{workflow}:{index}" for index in range(len(requests))}
        graph.workflows[workflow] = NS(invocation_ids=identities, end_ts_ms=None)
        for index, duration in enumerate(requests):
            identity = f"{workflow}:{index}"
            graph.invocations[identity] = NS(
                child_invocation_ids=set(), execution_mode=ExecutionMode.FOREGROUND,
                state=State.READY,
            )
            slot = len(keys)
            keys[slot] = NS(root_workflow_id=workflow, invocation_id=identity)
            pending.append((slot, identity))
            services[identity] = duration
    completion = WorkflowCompletionOrder(graph)
    now, rows, ends = 0., [], {}
    while pending:
        if policy == "request_shortest":
            pending.sort(key=lambda pair: (services[pair[1]], pair[0]))
        elif policy == "workflow_completion":
            pending = completion.order(pending, keys, attained={}, critical=lambda _: False)
        else:
            raise ValueError("unknown illustrative policy")
        slot, identity = pending.pop(0)
        workflow = keys[slot].root_workflow_id
        completion.admitted(workflow, identity)
        rows.append({"request": identity, "ttft": now, "finish": now + services[identity]})
        now += services[identity]
        completion.completed(identity)
        graph.invocations[identity].state = State.DONE
        completion.invalidate(workflow)
        ends[workflow] = now
    return {
        "model": "one server; all exposed independent calls ready at time zero; arbitrary time units",
        "total_service": now, "request_count": len(rows),
        "mean_request_ttft": sum(row["ttft"] for row in rows) / len(rows),
        "mean_workflow_jct": sum(ends.values()) / len(ends),
        "workflow_jct": ends, "requests": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve the previous analysis")
    result = {
        "scope": "Illustrations and counterexamples only; no workload/JCT speedup or novelty claim.",
        "cases": {
            name: {
                policy: simulate(work, policy=policy)
                for policy in ("request_shortest", "workflow_completion")
            }
            for name, work in {
                "request_count_weights_disagree": {"wide": (1.,) * 8, "narrow": (4.,)},
                "width_is_not_remaining_work": {"wide": (1., 1.), "narrow": (100.,)},
            }.items()
        },
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "scope": result["scope"]}))


if __name__ == "__main__":
    main()
