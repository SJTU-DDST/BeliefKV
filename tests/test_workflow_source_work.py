from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.workflow_source_work import reconstruct_work


def source():
    def node(identity, workflow, timestamp):
        return NS(node_id=identity, event=NS(
            kind=Kind.LLM_RESULT, attributes={"request_id": identity},
            workflow_id=workflow, invocation_id=workflow, context_id="same-local-context",
            ts_ms=timestamp,
        ))
    # Separate capture clocks, and a causal order that differs from timestamp order.
    plan = NS(
        nodes=[node("a2", "a", 1.), node("b1", "b", 2.), node("a1", "a", 100.)],
        request_usage={"a1": (2, 1), "a2": (4, 1), "b1": (2, 1)},
        dependency_graph=lambda: {"a2": ("a1",), "b1": (), "a1": ()},
    )
    data = {
        "a1": NS(prompt_tokens=[1, 2], output_tokens=[3]),
        "a2": NS(prompt_tokens=[1, 2, 3, 4], output_tokens=[5]),
        "b1": NS(prompt_tokens=[1, 2], output_tokens=[3]),
    }
    return plan, NS(get=data.__getitem__)


def test_causal_prefix_work_is_conserved_across_independent_capture_clocks():
    requests, totals = reconstruct_work(*source())
    assert requests["a2"]["input_increment"] == 1
    assert requests["a2"]["predecessor_request_id"] == "a1"
    assert requests["b1"]["input_increment"] == 2
    assert totals == {"a": 203, "b": 102}


@pytest.mark.parametrize("mutation", ("usage", "omitted", "foreign"))
def test_incomplete_or_changed_source_work_is_not_accepted(mutation):
    plan, corpus = source()
    if mutation == "usage":
        plan.request_usage["a2"] = (3, 1)
    elif mutation == "omitted":
        plan.nodes = plan.nodes[:2]
        plan.dependency_graph = lambda: {"a2": (), "b1": ()}
    else:
        plan.request_usage.pop("a1")
    with pytest.raises(ValueError):
        reconstruct_work(plan, corpus)
