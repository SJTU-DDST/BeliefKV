from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.continuum_history import ContinuumHistory
from beliefkv.policy.paper_baselines import continuum_public_ttl


def test_history_learns_only_when_the_same_program_reenters():
    model = ContinuumHistory()
    model.completed("a", tool="grep", now=1.)
    assert model.public_ttl("grep") == 2.
    assert model.arrived("other", now=100.) is None
    assert model.public_ttl("grep") == 2.
    assert model.arrived("a", now=4.) == ("grep", 3.)
    assert model.public_ttl("grep") == 0.
    assert model.arrived("a", now=100.) is None
    assert model.tool_gap_counts["grep"] == 1


def test_public_online_sufficient_statistics_match_full_history():
    model = ContinuumHistory()
    gaps = [2., 2., 2.01, .01, 10., 0.]
    for tool in ("shell:grep", "tool:task"):
        observed = []
        for gap in gaps:
            model.completed("same", tool=tool, now=1.)
            model.arrived("same", now=1. + gap)
            observed.append(gap)
            assert model.public_ttl(tool) == continuum_public_ttl(
                observed, tool_identified=True,
            )
    assert model.public_ttl(None) == 0.


def test_parallel_programs_do_not_mix_departures_or_tool_types():
    model = ContinuumHistory()
    model.completed("a", tool="fast", now=1.)
    model.completed("b", tool="slow", now=2.)
    model.arrived("b", now=10.)
    model.arrived("a", now=1.1)
    assert model.public_ttl("fast") == 2.
    assert model.public_ttl("slow") == 0.


def test_normal_return_and_retirement_do_not_create_synthetic_gap_samples():
    model = ContinuumHistory()
    model.completed("normal_return", tool=None, now=1.)
    model.completed("retired", tool="grep", now=1.)
    model.forget("retired")
    assert model.arrived("normal_return", now=100.) is None
    assert model.arrived("retired", now=100.) is None
    assert not model.tool_gap_counts


def test_invalid_reentry_keeps_the_original_observation():
    model = ContinuumHistory()
    model.completed("a", tool="grep", now=10.)
    with pytest.raises(ValueError, match="precedes"):
        model.arrived("a", now=9.)
    with pytest.raises(ValueError, match="twice"):
        model.completed("a", tool="grep", now=11.)
    assert model.arrived("a", now=12.) == ("grep", 2.)


@pytest.mark.parametrize("when", (-1, True, "2", float("nan"), float("inf")))
def test_unobserved_or_invalid_times_do_not_enter_the_estimator(when):
    model = ContinuumHistory()
    with pytest.raises(ValueError):
        model.completed("a", tool="grep", now=when)
    with pytest.raises(ValueError):
        model.arrived("a", now=when)
    assert not model.departures and not model.tool_gap_counts


@pytest.fixture
def tools(monkeypatch):
    import os
    from pathlib import Path

    engine = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not engine:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the qualified native engine")
    monkeypatch.syspath_prepend(str(Path(engine) / "python"))
    from beliefkv.runtime.continuum_tools import ContinuumTools

    return ContinuumTools()


def parsed_request(text):
    def decode(output, *, skip_special_tokens):
        assert output == [1, 2] and skip_special_tokens is False
        return text

    return NS(output_ids=[1, 2], tokenizer=NS(decode=decode), finished=lambda: True)


def test_tools_use_the_native_current_model_parser(tools):
    request = parsed_request(
        "<tool_call>\n<function=shell>\n<parameter=command>\n"
        "grep -n value file.py\n</parameter>\n</function>\n</tool_call>"
    )
    assert tools.identify(request) == "shell:grep"
    request = parsed_request(
        "<tool_call>\n<function=task>\n<parameter=description>\n"
        "Review the change\n</parameter>\n</function>\n</tool_call>"
    )
    assert tools.identify(request) == "tool:task"


def test_unidentified_and_parallel_calls_do_not_receive_an_invented_ttl(tools):
    assert tools.identify(parsed_request("done")) is None
    assert tools.identify(NS(output_ids=[1, 2], tokenizer=None)) is None
    first = (
        "<tool_call><function=shell><parameter=command>grep x"
        "</parameter></function></tool_call>"
    )
    assert tools.identify(parsed_request(first + first)) is None
    assert tools.identify(parsed_request(
        "<tool_call><function=shell><parameter=other>grep x"
        "</parameter></function></tool_call>"
    )) is None


def test_running_output_cannot_supply_a_future_tool_boundary(tools):
    req = parsed_request(
        "<tool_call><function=shell><parameter=command>grep x"
        "</parameter></function></tool_call>"
    )
    req.finished = lambda: False
    assert tools.identify(req) is None
