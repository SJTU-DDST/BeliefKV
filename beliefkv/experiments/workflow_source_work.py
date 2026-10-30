"""Conserved intrinsic request work from complete, independently compiled traces."""

from collections import Counter
from graphlib import TopologicalSorter

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import WorkflowProgress


def reconstruct_work(plan, corpus):
    """Use causal completion order, never compare clocks of separate captures."""
    nodes = {node.node_id: node for node in plan.nodes}
    states, previous, requests, totals = {}, {}, {}, Counter()
    for identity in TopologicalSorter(plan.dependency_graph()).static_order():
        event = nodes[identity].event
        if event is None or event.kind is not Kind.LLM_RESULT:
            continue
        request = event.attributes["request_id"]
        if request in requests or request not in plan.request_usage:
            raise ValueError("duplicate or foreign source model completion")
        value = corpus.get(request)
        if plan.request_usage[request] != (len(value.prompt_tokens), len(value.output_tokens)):
            raise ValueError("source request usage differs from exact tokens")
        progress = states.setdefault(event.workflow_id, WorkflowProgress())
        increment = progress.completed(
            request, event.invocation_id, event.context_id, value.prompt_tokens, value.output_tokens,
        )
        context = event.workflow_id, event.context_id
        requests[request] = {
            "workflow_id": event.workflow_id, "invocation_id": event.invocation_id,
            "context_id": event.context_id, "prompt_tokens": len(value.prompt_tokens),
            "output_tokens": len(value.output_tokens), "input_increment": increment,
            "predecessor_request_id": previous.get(context),
        }
        previous[context] = request
        totals[event.workflow_id] += increment + 100 * len(value.output_tokens)
    if requests.keys() != plan.request_usage.keys():
        raise ValueError("source work omits a logical request")
    return requests, dict(totals)
